"""Which tier a reply goes to, and what that tier asks Gemini for.

Three tiers, each a fallback chain and a thinking level:

* light — the Flash-Lite rungs at minimal thinking, for acknowledgements, greetings and
  banter;
* standard — today's reply, unchanged: the server's chain at the usual thinking budget;
* deep — for code, multi-step reasoning, long or multi-part asks and requests for depth:
  Pro at high thinking on a billed key (on by default, within its caps), and otherwise the
  standard reply.

``choose`` runs once a reply, after recall (so the message's embedding is cached) and before
the tool loop. It never asks Google anything and never raises: anything that goes wrong in
it routes standard, which is what every reply did before routing.

Rules come before the score. A message with images, or one asking for a lookup or an
action, or reading like an injection attempt, never goes light: the router's input is
written by whoever is talking, and the light tier mustn't be a way onto a weaker model's
guardrails. A chime-in nobody asked for, and a DM from someone outside the home server,
never go deep. A follow-up to a recent reply doesn't go below that reply's tier unless it
only acknowledges.

Pro is held to a share of the last day's replies, a per-member daily count and the budget
(olisar.routing.state). A deep reply past one of them is sent as a standard reply.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

from olisar.config import settings
from olisar.gemini import spend
from olisar.gemini.embeddings import cached_query_vector
from olisar.gemini.models import AUTO, PRO_MODEL, deep_chain, light_chain, model_chain
from olisar.gemini.pricing import request_cost
from olisar.gemini.quota import quota_day
from olisar.gemini.rate_limiter import get_rate_limiter
from olisar.gemini.trace import ReplyTrace
from olisar.routing import classifier, features, pacing, state

log = logging.getLogger("olisar.routing")

LIGHT, STANDARD, DEEP = "light", "standard", "deep"

# Thinking per tier. Standard sends nothing new: it keeps the budget every reply has always
# had, so a standard reply is exactly the reply from before routing. Deep thinks high only
# on Pro. Without Pro it replies as standard does: tested in the reply path (2026-10-10),
# 3.8 Flash at high or medium thinking answered a debugging question by running web search
# two or three times, 40 seconds to over two minutes, where Pro answered in 8 to 14 seconds
# and Flash on standard's budget in 4.
_THINKING = {LIGHT: "minimal", STANDARD: None, DEEP: "high"}
# High thinking on Pro can run to thousands of tokens, and max_output_tokens caps thinking
# and the reply together. The usual 2,048 would leave a deep reply cut off or empty.
DEEP_MAX_OUTPUT = 8192


@dataclass(frozen=True)
class Route:
    tier: str
    chain: tuple[str, ...]
    thinking_level: str | None
    max_output_tokens: int | None
    pro: bool  # whether Pro heads the chain
    reason: str  # why, for the log and the test chat
    light: float | None = None  # the classifier's scores, None without a model
    deep: float | None = None
    capped: str = ""  # the cap that kept a deep reply off Pro: "share", "member" or "budget"

    def model_args(self) -> dict:
        """What GeminiClient.generate / generate_with_tools take for this route."""
        args: dict = {"model": self.chain[0], "chain": list(self.chain)}
        if self.thinking_level is not None:
            args["thinking_level"] = self.thinking_level
        if self.max_output_tokens is not None:
            args["max_output_tokens"] = self.max_output_tokens
        return args


@dataclass
class Routing:
    """One reply's route, and whether it was handed up partway through."""

    route: Route
    chosen: Route
    escalated: str = ""  # why a light route was handed to standard, "" if it wasn't

    @classmethod
    def of(cls, route: Route | None) -> "Routing | None":
        return cls(route, route) if route is not None else None

    @property
    def light(self) -> bool:
        return self.route.tier == LIGHT

    def escalate(self, why: str) -> bool:
        """Hand a light reply to the standard tier for the rest of it. False when there's
        nothing to do: only light escalates, once, and never into Pro."""
        if self.route.tier != LIGHT:
            return False
        self.route = _standard(self.route, f"{self.route.reason}; handed up: {why}")
        self.escalated = why
        log.info("routing: light reply handed to standard (%s)", why)
        return True


# A follow-up that carries on from the last answer: "and on windows?", "but why", "what
# about the other one". Only those stay deep after a deep reply; anything else the member
# says next is routed on its own merits.
_CONTINUATION = re.compile(r"^\s*(?:and|but|so|also|then|or|wait|ok so|what about|how about|why|how)\b", re.I)
_CONTINUATION_WORDS = 15


def _continues(text: str, f: features.Features) -> bool:
    if f.ack or f.words > _CONTINUATION_WORDS:
        return False
    return bool(f.questions) or bool(_CONTINUATION.match(text or ""))


# The thinking a standard reply has room for (generate_with_tools' default budget).
_STANDARD_THINKING = 1024


def _standard_estimate(routing: "Routing", served: list) -> float:
    """What the reply would have cost on the standard tier, roughly, for the Usage page.

    Each request is priced on the standard tier's head model, with three adjustments: a
    light reply handed up doesn't count its Flash-Lite requests, which standard would never
    have made; a deep reply's thinking is cut to what standard allows, since that's the extra
    deep pays for; everything else is the same tokens at the standard price."""
    head = model_chain(_base())[0]
    day = quota_day()
    total = 0.0
    for s in served:
        if routing.escalated and "flash-lite" in s.model:
            continue
        output = s.output_tokens
        if routing.chosen.tier == DEEP:
            output = output - s.thinking_tokens + min(s.thinking_tokens, _STANDARD_THINKING)
        total += request_cost(head, day, input_tokens=s.input_tokens, output_tokens=output)
    return total


def _base() -> str:
    return settings.gemini_chat_model


def _standard(prior: Route | None, reason: str) -> Route:
    return Route(
        tier=STANDARD, chain=tuple(model_chain(_base())), thinking_level=None,
        max_output_tokens=None, pro=False, reason=reason,
        light=prior.light if prior else None, deep=prior.deep if prior else None,
    )


async def enabled(default_model: str | None) -> bool:
    """Whether a server with this ``default_model`` is routed: it's on Automatic, and the
    switch for the key's tier is on."""
    if default_model not in (None, "", AUTO):
        return False
    switches = await state.switches()
    return switches.paid if get_rate_limiter().paid else switches.free


async def choose(
    *,
    text: str,
    default_model: str | None,
    names: tuple[str, ...] = (),
    images: bool = False,
    addressed: bool = True,
    in_guild: bool = True,
    user_id: int = 0,
    channel_id: int = 0,
) -> Route | None:
    """The route for a reply to ``text``, or None when this server isn't routed (pinned to
    a model, or routing off for the key's tier), which replies exactly as before."""
    try:
        if not await enabled(default_model):
            return None
        return await _choose(
            text=text, names=names, images=images, addressed=addressed,
            in_guild=in_guild, user_id=user_id, channel_id=channel_id,
        )
    except Exception:
        log.exception("model routing failed; replying on the standard tier")
        return _standard(None, "routing failed")


async def _choose(
    *, text: str, names: tuple[str, ...], images: bool, addressed: bool, in_guild: bool,
    user_id: int, channel_id: int,
) -> Route:
    paid = get_rate_limiter().paid
    f = features.extract(text, names)
    vector = cached_query_vector(text) if text.strip() else None
    scores = classifier.score(f, vector)
    model = classifier.model()

    floors = [why for why, hit in (
        ("images", images), ("lookup", f.lookup), ("action", f.action),
        ("injection", f.injection),
    ) if hit]
    follow = state.follow_up_tier(user_id, channel_id)
    # Only a pure acknowledgement escapes the floor. "do it" or "sure" right after one of
    # Olisar's replies may be a yes to something it offered, which Flash-Lite could answer
    # "done" to without doing it.
    if follow in (STANDARD, DEEP) and (not f.ack or f.assent):
        floors.append("follow-up")
    # An injection attempt isn't worth Pro's money either: standard holds it as well.
    ceiling = [why for why, hit in (
        ("chime-in", not addressed), ("non-member", not in_guild), ("injection", f.injection),
    ) if hit]

    if scores is None or model is None:
        tier, why = STANDARD, "no classifier"
        light = deep = None
    else:
        light, deep = scores.light, scores.deep
        thresholds = model.thresholds_for(scores)
        strict = thresholds["light"]
        if paid:
            light_at = strict
        else:
            pressure = await pacing.flash_pressure(model_chain(_base()))
            light_at = pacing.light_threshold(strict, thresholds["light_loose"], pressure)
        if light >= light_at and not floors:
            tier, why = LIGHT, f"light {light:.2f}"
        elif deep >= thresholds["deep"]:
            tier, why = DEEP, f"deep {deep:.2f}"
        else:
            tier, why = STANDARD, f"standard (light {light:.2f}, deep {deep:.2f})"
        if not scores.embedded:
            why += ", no embedding"
    if follow == DEEP and tier != DEEP and _continues(text, f):
        tier, why = DEEP, f"{why}; follows a deep reply"
    if tier == DEEP and ceiling:
        tier, why = STANDARD, f"{why}; not deep: {', '.join(ceiling)}"
    if tier == STANDARD and floors and "standard (" in why:
        why = f"standard: {', '.join(floors)}"

    if tier == LIGHT:
        return Route(LIGHT, tuple(light_chain()), _THINKING[LIGHT], None, False, why, light, deep)
    if tier == STANDARD:
        route = _standard(None, why)
        return replace(route, light=light, deep=deep)

    capped = ""
    pro = paid and (await state.switches()).pro
    if pro:
        allowed, capped = state.pro_allowed(user_id)
        if allowed and await spend.state() in ("warn", "over"):
            allowed, capped = False, "budget"
        pro = allowed
        if pro:
            state.reserve_pro(user_id)  # released in finish(), however the reply ends
        why += "; Pro" if pro else f"; Pro capped ({capped})"
    if not pro:
        # Still counted as deep (for the Usage page and a follow-up's floor), but sent as a
        # standard reply: see _THINKING.
        return replace(_standard(None, why), tier=DEEP, light=light, deep=deep, capped=capped)
    return Route(
        DEEP, tuple(deep_chain(_base(), pro=True)), _THINKING[DEEP], DEEP_MAX_OUTPUT, True,
        why, light, deep, capped,
    )


async def finish(
    routing: "Routing | None", reply_trace: ReplyTrace, *, user_id: int = 0,
    channel_id: int = 0, answered: bool = True,
) -> dict | None:
    """Count a finished reply and log where it went. Returns a summary for the test chat,
    or None for a reply that wasn't routed. Called however the reply ended; ``answered`` is
    False when it ended on the rate-limit message or the blank fallback, which isn't a reply
    to count."""
    if routing is None:
        return None
    chosen = routing.chosen
    if chosen.pro:
        state.release_pro(user_id)
    served_pro = any(s.model == PRO_MODEL for s in reply_trace.served)
    if not reply_trace.served or not answered:
        log.info("routing: %s reply got no answer (%s)", chosen.tier, routing.route.reason)
        return None
    try:
        state.note_reply(user_id, channel_id, routing.route.tier, served_pro)
        reply_cost = [s for s in reply_trace.served if s.source == "conversation"]
        await state.record(
            chosen.tier, escalated=bool(routing.escalated), capped=bool(chosen.capped),
            cost=sum(s.cost for s in reply_cost),
            standard_cost=_standard_estimate(routing, reply_cost),
        )
    except Exception:
        log.exception("couldn't count a routed reply")
    log.info(
        "routing: %s reply via %s (%s), $%.4f",
        chosen.tier, ", ".join(reply_trace.models()) or "no model", routing.route.reason,
        reply_trace.cost,
    )
    return {
        "tier": chosen.tier,
        "model": reply_trace.last_model,
        "models": reply_trace.models(),
        "reason": routing.route.reason,
        "escalated": routing.escalated,
        "pro": served_pro,
        "capped": chosen.capped,
        "cost_usd": round(reply_trace.cost, 6),
    }
