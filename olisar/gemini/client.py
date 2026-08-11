"""Thin async wrapper around the Gemini SDK.

Adds the things every call should have: rate limiting, fallback across the model
chain on transient/overload errors, usage accounting, and safe text extraction.
Tool/function calling is layered on in Phase 3.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from olisar import runtime_keys
from olisar.config import settings
from olisar.gemini.models import image_model_chain, model_chain
from olisar.gemini.rate_limiter import RateLimitExceeded, get_rate_limiter, record_usage

log = logging.getLogger("olisar.gemini")

# Transient server / overload errors (most often 503 "model overloaded" under high
# demand). Like a 429, these make us fall back to the next model in the chain and
# briefly skip the failing one — rather than hammering an overloaded model.
_TRANSIENT_5XX = {500, 502, 503, 504}
_SERVER_ERROR_COOLDOWN = 15.0  # short skip of a model that just returned a 5xx

# A retired model: `404 ... is no longer available`. Unambiguously about that model and
# nothing else, so it must cost the next model rather than the whole request — a 404 used
# to raise, which is why a retired entry in the vision chain took image understanding down
# completely instead of degrading to the model behind it. Parked for an hour rather than
# seconds: retirement is permanent, and re-asking every call is a wasted round trip on
# every reply until someone ships a new list.
_MODEL_RETIRED = 404
_RETIRED_COOLDOWN = 3600.0

# Optional features whose support genuinely varies across the chain. A 400 naming one of
# these means "this model can't do that" and should cost us the next model; any other 400
# means the request itself is wrong and must fail loudly where it happened.
#
# The two cases arrive as the same status code and near-identical prose — "... is not
# supported" — so the wording can't separate them. What separates them is the *subject* of
# the complaint: an optional feature that varies by model, versus the shape of the
# conversation, which every model validates the same way. Hence a whitelist of features
# rather than a pattern for "unsupported".
#
# Erring permissive is expensive. `role="tool"` was rejected by every model in the chain;
# reading that as a capability gap would have re-sent the same malformed payload nine
# times, spent nine free-tier requests, and still returned the blank fallback — slower and
# harder to diagnose than failing at the first model. Unrecognised 400s keep raising.
#
# Note "tool" alone is deliberately absent: it appears in `Role 'tool' is not supported`,
# the exact bug this must not swallow.
_CAPABILITY_400_FEATURES = (
    "google_search", "google search", "search grounding", "grounding",
    "thinking_config", "thinking_budget", "thinking",
    "code_execution", "code execution",
    "response_schema", "responseschema",
    "response_mime_type", "responsemimetype",
    "tool_config", "toolconfig",
    "url_context", "urlcontext",
    "safety_settings", "safetysettings",
)


def _is_capability_400(exc: Exception) -> bool:
    """True when a 400 blames an optional feature this model lacks, rather than the
    request. See ``_CAPABILITY_400_FEATURES`` for why this is a whitelist."""
    if getattr(exc, "code", None) != 400:
        return False
    detail = _api_error_detail(exc).lower()
    return any(feature in detail for feature in _CAPABILITY_400_FEATURES)


class GroundingUnavailable(Exception):
    """Raised when Google Search grounding is quota-exhausted (free tier is small)."""


# A 429 that carries a retry delay is a *rate* (per-minute) limit — it clears on its own
# in seconds. One with no delay, or a long one, is the daily grounding quota, which won't
# clear until Google's day rolls over. Anything up to this is treated as the former.
_GROUNDING_SHORT_RETRY_MAX = 15 * 60.0
# Matches both shapes Google uses: `"retryDelay": "23s"` and `retry_delay { seconds: 7 }`
# — the first number after the key, whether or not a unit follows it.
_RETRY_DELAY_RE = re.compile(r"retry[_-]?delay\D{0,16}?(\d+(?:\.\d+)?)", re.IGNORECASE)


def _api_error_detail(exc: Exception) -> str:
    """Everything Google told us about a refusal, on one line — the message plus any
    structured details. Without this a 429 is indistinguishable from any other 429, and
    you can't tell a per-minute throttle from a spent daily quota."""
    parts = [str(getattr(exc, "message", None) or exc)]
    details = getattr(exc, "details", None)
    if details:
        parts.append(f"details={details!r}")
    status = getattr(exc, "status", None)
    if status:
        parts.append(f"status={status}")
    return " | ".join(" ".join(str(p).split()) for p in parts if p)


def _retry_after_seconds(exc: Exception) -> float | None:
    """The retryDelay Google attached to a 429, if any (``"retryDelay": "23s"`` or
    ``retry_delay { seconds: 23 }``). None means it didn't say — which is the signal
    that waiting a moment won't help."""
    match = _RETRY_DELAY_RE.search(_api_error_detail(exc))
    return float(match.group(1)) if match else None


def _next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class GenResult:
    text: str
    tokens: int


def safe_text(resp) -> str:
    """Extract text from a response; `.text` raises if blocked/empty."""
    try:
        return (resp.text or "").strip()
    except Exception:
        return ""


def was_truncated(resp) -> bool:
    """True if the model stopped because it ran out of output budget
    (``finish_reason`` MAX_TOKENS) — i.e. the reply was cut off mid-thought and
    the caller should ask it to continue."""
    try:
        fr = resp.candidates[0].finish_reason
    except Exception:
        return False
    if fr is None:
        return False
    name = getattr(fr, "name", None) or str(fr)
    return "MAX_TOKENS" in str(name).upper()


# Models that answered a thinking_config with a 400 — remembered for the process so we
# only pay that discovery once. Name-matching can't be trusted here: the "-latest"
# aliases move between generations (gemini-flash-lite-latest became a Gemini 3 model,
# which rejects thinking_budget=0 outright), so the API's answer is the only reliable
# source. Cleared on restart, which is when an alias could have moved again.
_THINKING_REJECTED: set[str] = set()


def _supports_thinking(model: str) -> bool:
    """Whether a model plausibly accepts ``thinking_config``. Thinking landed with
    Gemini 2.5; the 2.0/1.x fallbacks reject the field with a 400, so we don't set it
    for them. Everything else is a guess — a cheap pre-filter, not the authority. The
    ``-latest`` aliases have no version digit and drift between generations, so a model
    that *takes* the field may still reject a particular budget; ``_THINKING_REJECTED``
    records what the API actually said."""
    m = model.lower()
    return not any(v in m for v in ("2.0", "1.5", "1.0"))


class GeminiClient:
    def __init__(self) -> None:
        self._client: genai.Client | None = None
        self._key: str | None = None
        # When grounded search is refused, stop attempting it until this passes. Without
        # it every reply pays a full round trip (and up to a minute queued behind the
        # rate limiter) to rediscover the same refusal.
        self._grounding_blocked_until: datetime | None = None

    async def aclient(self) -> genai.Client:
        """The underlying SDK client, built lazily and rebuilt when the effective
        API key changes (a dashboard edit overrides .env without a restart). Raises
        if no key is configured anywhere, so callers degrade rather than crash oddly."""
        key = await runtime_keys.gemini_api_key()
        if not key:
            raise RuntimeError("no Gemini API key configured (set GEMINI_API_KEY or add one in the dashboard)")
        if self._client is None or key != self._key:
            self._client = genai.Client(api_key=key)
            self._key = key
        return self._client

    async def _raw_generate(
        self, *, contents, config, model: str, chain: list[str] | None = None,
        thinking_budget: int | None = None, source: str = "other",
        grounding: int = 0, fall_back_on: tuple[int, ...] = (),
    ):
        """Generate, walking the model fallback chain. The first immediately
        available model is used; if it errors transiently — a 429 (rate limit) or a
        5xx (server/overload, e.g. 503 under high demand) — that model is briefly
        parked and we fall back to the next-best model rather than hammering it. A
        non-transient error (e.g. 400/404) is raised. If every model in the chain is
        unavailable, the last error is raised (or RateLimitExceeded if none was hit).

        Pass ``chain`` to override the default chat ranking (e.g. the vision
        chain); otherwise it's derived from ``model`` via ``model_chain``.

        ``fall_back_on`` adds status codes that should also move to the next model
        instead of raising — grounded search passes ``(400,)`` because not every model
        in the chat chain accepts the google_search tool, and "this one can't ground"
        should cost us the next model, not the whole request. ``grounding=1`` marks the
        call in the usage rollup that the per-guild grounding cap reads."""
        limiter = get_rate_limiter()
        chain = chain or model_chain(model)
        last_error: Exception | None = None
        # Which models we walked past, and why — reported with the model that finally
        # answered. This skip used to be silent, so "the reply came from a worse model"
        # and "the reply came from the preferred one" looked identical in the logs.
        skipped: list[str] = []
        for candidate in chain:
            state = limiter.state(candidate)
            if state != "ok":
                skipped.append(f"{candidate} ({state})")
                continue  # busy or cooling down — fall back to the next model
            limiter.reserve(candidate)
            try:
                client = await self.aclient()
                # Per-candidate config: only the thinking-capable models in the chain
                # get thinking_config; the 2.0/1.x fallbacks would 400 on the field.
                cand_config = config
                sent_thinking = (
                    thinking_budget is not None
                    and _supports_thinking(candidate)
                    and candidate not in _THINKING_REJECTED
                )
                if sent_thinking:
                    cand_config = config.model_copy(
                        update={"thinking_config": types.ThinkingConfig(
                            thinking_budget=thinking_budget
                        )}
                    )
                try:
                    resp = await client.aio.models.generate_content(
                        model=candidate, contents=contents, config=cand_config
                    )
                except genai_errors.APIError as exc:
                    # A model that won't take our thinking_config — most often
                    # thinking_budget=0 on a model that can't switch thinking off.
                    # Retry it once with the field dropped, and remember, so this costs
                    # one request per model per process instead of failing forever.
                    if getattr(exc, "code", None) != 400 or not sent_thinking:
                        raise
                    _THINKING_REJECTED.add(candidate)
                    log.warning(
                        "gemini %s rejected thinking_budget=%s (%s); retrying without it "
                        "and omitting it for this model from now on",
                        candidate, thinking_budget, _api_error_detail(exc),
                    )
                    resp = await client.aio.models.generate_content(
                        model=candidate, contents=contents, config=config
                    )
            except genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                last_error = exc
                if code == 429:
                    limiter.penalize(candidate, reason="a rate limit (429)")
                elif code in _TRANSIENT_5XX:
                    log.warning(
                        "gemini %s error code=%s; falling back to next model", candidate, code
                    )
                    limiter.penalize(
                        candidate, seconds=_SERVER_ERROR_COOLDOWN, reason=f"a {code} error"
                    )
                elif code == _MODEL_RETIRED:
                    log.error(
                        "gemini %s is gone: %s | dropping it for now and trying the next "
                        "model — remove it from olisar/gemini/models.py",
                        candidate, _api_error_detail(exc),
                    )
                    limiter.penalize(
                        candidate, seconds=_RETIRED_COOLDOWN, reason="the model is retired"
                    )
                elif code in fall_back_on:
                    # Caller says this code means "this model can't do it" rather than
                    # "the request is bad" — e.g. a model that rejects the search tool.
                    log.warning(
                        "gemini %s rejected the request (code=%s): %s; trying the next model",
                        candidate, code, _api_error_detail(exc),
                    )
                elif _is_capability_400(exc):
                    # A feature this model lacks (see _CAPABILITY_400_FEATURES). Worth the
                    # next model; a 400 about the request itself still raises below.
                    log.warning(
                        "gemini %s lacks a feature this request needs: %s; trying the next model",
                        candidate, _api_error_detail(exc),
                    )
                else:
                    # Not transient, not a capability gap — the request is wrong, and every
                    # model in the chain will say so. Fail here, where the detail is, rather
                    # than nine models later with the last one's error.
                    if code == 400:
                        log.error(
                            "gemini %s rejected the request as malformed: %s | not falling "
                            "back — every model would reject this",
                            candidate, _api_error_detail(exc),
                        )
                    raise  # non-transient error — surface it, don't mask
                continue  # fall back to the next model in the chain

            tokens = (
                resp.usage_metadata.total_token_count
                if resp.usage_metadata is not None
                else 0
            ) or 0
            await record_usage(candidate, tokens, grounding=grounding, source=source)
            # Always say which model answered, not only when it wasn't the preferred one.
            # Diagnosing the blank-fallback incident meant inferring the serving model from
            # the daily gemini_usage rollup, because a reply served by the head of the chain
            # left no trace at all. One line per generation is worth that.
            if candidate == chain[0]:
                log.info("gemini %s served %s", candidate, source)
            else:
                log.info(
                    "gemini %s served %s (fell back from %s%s)",
                    candidate, source, chain[0],
                    f", skipped {', '.join(skipped)}" if skipped else "",
                )
            return resp

        # Every model in the chain was unavailable or erroring.
        log.warning(
            "gemini chain exhausted for %s: %d model(s), skipped %s",
            source, len(chain), ", ".join(skipped) or "none",
        )
        if last_error is not None:
            raise last_error
        raise RateLimitExceeded(chain[0], "all fallback models")

    async def generate(
        self,
        *,
        contents: list,
        system_instruction: str,
        model: str | None = None,
        temperature: float = 0.9,
        max_output_tokens: int = 1024,
        thinking_budget: int | None = 0,
        source: str = "other",
    ) -> GenResult:
        model = model or settings.gemini_chat_model
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        resp = await self._raw_generate(
            contents=contents, config=config, model=model,
            thinking_budget=thinking_budget, source=source,
        )
        tokens = (
            resp.usage_metadata.total_token_count if resp.usage_metadata is not None else 0
        ) or 0
        return GenResult(text=safe_text(resp), tokens=tokens)

    async def generate_with_tools(
        self,
        *,
        contents: list,
        system_instruction: str,
        tools: list,
        model: str | None = None,
        chain: list[str] | None = None,
        temperature: float = 0.9,
        max_output_tokens: int = 2048,
        force_text: bool = False,
        thinking_budget: int | None = 1024,
        source: str = "other",
    ):
        """One tool-enabled turn. Returns the raw response so the caller can read
        `.function_calls`. Automatic function calling is disabled — we run the
        loop ourselves so tools get our DB/Discord context. With ``force_text``
        the model is barred from calling tools (function-calling mode NONE), so it
        must answer in plain text — used to close out the loop without a blank.

        This is the conversational/reasoning path, so thinking is ON but *bounded*:
        ``thinking_budget`` is a cap, not a floor, so the model scales reasoning to
        task difficulty (little for "hi", more for a hard question). Crucially
        ``max_output_tokens`` (a combined ceiling over thinking + visible text) is set
        well above the thinking budget so reasoning can't starve the reply — the bug
        that made replies cut off after ~15 words. Pass ``thinking_budget=0`` to
        disable thinking for a cheap/short call.

        ``chain`` overrides the models to try, as in ``_raw_generate``. The self-test passes
        a single-model chain: it has to report on the model it named, and silently falling
        back would let a broken model pass because a healthy one answered for it."""
        model = model or settings.gemini_chat_model
        tool_config = None
        if force_text:
            tool_config = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE")
            )
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            tools=tools,
            tool_config=tool_config,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        return await self._raw_generate(
            contents=contents, config=config, model=model, chain=chain,
            thinking_budget=thinking_budget, source=source,
        )

    async def caption_images(
        self,
        images: list[tuple[bytes, str]],
        *,
        instruction: str,
        model: str | None = None,
        max_output_tokens: int = 220,
    ) -> str:
        """Describe one or more images in plain text, walking the vision fallback
        chain. ``images`` is a list of ``(data, mime_type)``. Returns the model's
        description (or '' if blocked). Raises RateLimitExceeded if every vision
        model is unavailable, so callers can degrade to filename-only."""
        if not images:
            return ""
        parts = [types.Part(text=instruction)]
        for data, mime in images:
            parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=data)))
        config = types.GenerateContentConfig(
            temperature=0.3, max_output_tokens=max_output_tokens
        )
        chain = image_model_chain(model or settings.gemini_vision_model)
        resp = await self._raw_generate(
            contents=[types.Content(role="user", parts=parts)],
            config=config,
            model=chain[0],
            chain=chain,
            source="vision",
        )
        return safe_text(resp)

    def _suppress_grounding(self, exc: Exception) -> None:
        """Record *why* Google refused, and stop asking for a while.

        A 429 carrying a retryDelay is a per-minute throttle: park grounding for that
        long. One without is the daily quota (or a key whose tier has no grounding at
        all), which no amount of retrying fixes before the day rolls over — that case
        used to retry on every single reply, forever, each attempt a wasted round trip.
        """
        now = datetime.now(timezone.utc)
        detail = _api_error_detail(exc)
        retry = _retry_after_seconds(exc)
        if retry is not None and retry <= _GROUNDING_SHORT_RETRY_MAX:
            until = now + timedelta(seconds=retry)
            why = f"Google asked for {retry:.0f}s"
        else:
            until = _next_utc_midnight(now)
            why = (
                "no retryDelay — treating it as the daily grounding quota"
                if retry is None
                else f"retryDelay {retry:.0f}s is longer than a throttle"
            )
        self._grounding_blocked_until = until
        log.warning(
            "grounded search refused by Google: %s | %s | not asking again until %s",
            detail, why, until.isoformat(timespec="seconds"),
        )

    async def search(self, query: str, *, model: str | None = None) -> tuple[str, list[str]]:
        """Grounded web search via Google Search. Returns (answer, source titles).
        Raises GroundingUnavailable when Google refuses (free-tier grounding is tiny)
        so the caller can degrade rather than crash.

        Walks the same fallback chain as chat: grounding quota is usually per-model, so
        a limit on the preferred model doesn't have to mean no web search at all. This
        used to be pinned to one model with no fallback, which is why a single exhausted
        model made every search fail while ordinary replies carried on."""
        blocked_until = self._grounding_blocked_until
        if blocked_until is not None:
            if datetime.now(timezone.utc) < blocked_until:
                log.info(
                    "grounded search skipped — suppressed until %s",
                    blocked_until.isoformat(timespec="seconds"),
                )
                raise GroundingUnavailable()
            self._grounding_blocked_until = None  # window passed — try again

        model = model or settings.gemini_chat_model
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
        try:
            resp = await self._raw_generate(
                contents=query,
                config=config,
                model=model,
                source="grounding",
                grounding=1,  # counts toward the per-guild grounding cap
                fall_back_on=(400,),  # a model that won't take the search tool
            )
        except RateLimitExceeded as exc:
            # Every model in the chain was already parked — same user-visible outcome.
            log.warning("grounded search: no model in the chain was available (%s)", exc)
            raise GroundingUnavailable() from exc
        except genai_errors.APIError as exc:
            if getattr(exc, "code", None) == 429:
                self._suppress_grounding(exc)
                raise GroundingUnavailable() from exc
            raise

        sources: list[str] = []
        try:
            chunks = resp.candidates[0].grounding_metadata.grounding_chunks or []
            for ch in chunks:
                if ch.web and ch.web.uri:
                    sources.append(ch.web.title or ch.web.uri)
        except Exception:
            pass
        return safe_text(resp), sources


_gemini: GeminiClient | None = None


def get_gemini() -> GeminiClient:
    global _gemini
    if _gemini is None:
        _gemini = GeminiClient()
    return _gemini
