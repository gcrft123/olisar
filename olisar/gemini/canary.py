"""A self-test that exercises the shapes the reply path actually depends on.

The chain being *reachable* is not the same as the chain *working*. Every model stayed up
and answered plain prompts on the morning every tool-backed reply started failing with a
400 — the request shape had stopped being valid, and nothing noticed until a user asked a
question and got "…my mind just went blank there". The first report came from Discord.

So this does the one thing an ordinary completion doesn't: a full tool round-trip. The model
emits a function call, we echo its own turn back verbatim (that carries the thought_signature
newer models require), then send the function response as its own turn and expect text back.
That is exactly the sequence in ``pipeline._run_tool_loop``, so a provider-side change to how
any of it is validated fails here — on a schedule, to the operator's log — instead of in a DM.

**Every rung, not just the first.** Measured against the live API, the shape that caused the
incident was rejected *only* by the two ``-latest`` aliases; every pinned model in the chain
accepted it. A self-test that checked the head of the chain would therefore have passed
happily all the way through the outage. What breaks is whichever model the request actually
lands on, and under contention that is not the one at the top — so each model is tested on
its own, pinned to a single-model chain so a healthy neighbour can't answer on its behalf.

Two requests per model per run, once a day. Routed through the normal client, so it respects
the rate limiter and lands in the usage rollup like any other call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from google.genai import types

from olisar.gemini.client import get_gemini
from olisar.gemini.models import IMAGE_RANKED_NAMES, RANKED_NAMES
from olisar.gemini.rate_limiter import RateLimitExceeded

log = logging.getLogger("olisar.canary")

# Distinctive name so it can never collide with a real tool the model knows.
_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="olisar_selftest_echo",
            description="Echo a token back. Used only by Olisar's scheduled self-test.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"token": types.Schema(type="STRING")},
                required=["token"],
            ),
        )
    ]
)

_PROMPT = (
    "Call the olisar_selftest_echo tool with token=\"ping\". Do not reply in text until "
    "you have the tool's result."
)
_SYSTEM = "You are running a self-test. Use the tool you are given, then confirm the result."
_TOKEN = "ping"


@dataclass(frozen=True)
class CanaryResult:
    # "ok"           — the full round-trip worked
    # "failed"       — the API refused a shape the reply path uses; a real alarm
    # "inconclusive" — we couldn't test (model never called the tool, or rate-limited).
    #                  Not an alarm: it says nothing about whether the shape is valid.
    status: str
    detail: str
    at: datetime
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ChainResult:
    """One sweep of the chain. ``status`` is the worst verdict in it: a single broken
    model is a failure even when the rest are fine, because that model still takes
    live traffic whenever the ones above it are busy."""

    status: str
    results: tuple[CanaryResult, ...]
    at: datetime

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def failed_models(self) -> tuple[str, ...]:
        return tuple(r.model for r in self.results if r.status == "failed")


_last: ChainResult | None = None

# A busy or briefly broken model says nothing about whether our request shape is valid, so
# it must not read as a failure. This matters more than it looks: a single-model chain has
# nowhere to fall back to, so the client re-raises the provider's *raw* 429 rather than our
# RateLimitExceeded — and catching only the latter reported a rate-limited model as broken.
# A daily sweep that cries wolf on quota is a daily sweep nobody reads.
_INCONCLUSIVE_CODES = frozenset({429, 500, 502, 503, 504})


def _verdict_for(exc: Exception) -> str:
    """"failed" if this refusal is about our request; "inconclusive" if it's about timing.

    404 stays a failure: a retired model is permanent and needs the chain edited.
    """
    if isinstance(exc, RateLimitExceeded):
        return "inconclusive"
    return "inconclusive" if getattr(exc, "code", None) in _INCONCLUSIVE_CODES else "failed"


def last_result() -> ChainResult | None:
    """The most recent sweep, for /api/health. None until the first one completes."""
    return _last


async def run_canary(model: str) -> CanaryResult:
    """One tool round-trip against exactly ``model``. Never raises — a timer drives this.

    Pinned to a single-model chain on purpose: the point is a verdict about *this* model,
    and the normal fallback would happily let a healthy neighbour answer for a broken one.
    """
    client = get_gemini()
    contents: list = [types.Content(role="user", parts=[types.Part(text=_PROMPT)])]

    def record(status: str, detail: str) -> CanaryResult:
        return CanaryResult(
            status=status, detail=detail, at=datetime.now(timezone.utc), model=model
        )

    try:
        first = await client.generate_with_tools(
            contents=contents,
            system_instruction=_SYSTEM,
            tools=[_TOOL],
            model=model,
            chain=[model],
            source="canary",
        )
    except Exception as exc:  # noqa: BLE001 — a timer must not die on this
        verdict = _verdict_for(exc)
        if verdict == "failed":
            log.error("model self-test FAILED for %s on the first turn: %s", model, exc)
        return record(verdict, f"first turn: {exc}")

    calls = list(first.function_calls or [])
    if not calls:
        # The model chose to answer directly. Says nothing about request validity.
        return record("inconclusive", "model did not call the tool")

    # The model's own turn, verbatim — newer models require the thought_signature it
    # carries, so rebuilding this by hand is not equivalent. pipeline does the same.
    contents.append(first.candidates[0].content)
    contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name=calls[0].name, response={"result": f"echo: {_TOKEN}"}
                )
            ],
        )
    )

    try:
        second = await client.generate_with_tools(
            contents=contents,
            system_instruction=_SYSTEM,
            tools=[_TOOL],
            model=model,
            chain=[model],
            source="canary",
        )
    except Exception as exc:  # noqa: BLE001
        verdict = _verdict_for(exc)
        if verdict == "failed":
            # This is the one that matters: the tool call worked and handing the result
            # back did not — the exact failure that reached users as the blank fallback.
            log.error(
                "model self-test FAILED for %s handing a tool result back — tool-backed "
                "replies on this model come out as the blank fallback: %s",
                model, exc,
            )
        return record(verdict, f"tool result turn: {exc}")

    text = (second.text or "").strip()
    if not text:
        return record("inconclusive", "no text after the tool result")

    return record("ok", text[:120])


def _default_sweep() -> list[str]:
    """Every model any chain can reach, chat and vision, in order and without repeats.

    Vision is included because that is where a retired model actually bit: its chain
    *started* on one, so image understanding was failing outright while chat was fine.
    A sweep that only covered chat would have reported everything healthy.
    """
    seen: dict[str, None] = {}
    for name in (*RANKED_NAMES, *IMAGE_RANKED_NAMES):
        seen.setdefault(name, None)
    return list(seen)


async def run_chain_canary(models: list[str] | None = None) -> ChainResult:
    """Self-test every model the bot can reach, one at a time. Never raises.

    Sequential rather than concurrent: the rate limiter is per model, but the free tier's
    ceilings are low enough that firing the whole chain at once would mostly measure our
    own contention. A daily sweep has no reason to be fast.
    """
    global _last

    names = models if models is not None else _default_sweep()
    results = [await run_canary(name) for name in names]

    failed = [r for r in results if r.status == "failed"]
    unknown = [r for r in results if r.status == "inconclusive"]
    status = "failed" if failed else ("inconclusive" if len(unknown) == len(results) else "ok")

    _last = ChainResult(
        status=status, results=tuple(results), at=datetime.now(timezone.utc)
    )

    if failed:
        log.error(
            "model self-test: %d of %d model(s) cannot complete a tool round-trip — %s. "
            "Replies that land on those models will come out as the blank fallback.",
            len(failed), len(results), ", ".join(r.model for r in failed),
        )
    else:
        log.info(
            "model self-test ok — %d model(s) completed a tool round-trip%s",
            len(results) - len(unknown),
            f", {len(unknown)} untested" if unknown else "",
        )
    return _last
