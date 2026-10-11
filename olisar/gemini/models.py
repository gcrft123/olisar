"""Gemini model ranking and the fallback chain.

*Chat* models ranked best -> worst. Every one is on Google's free tier. Pro models are
excluded from the ranking: they aren't (as of 2026). A key with billing on reaches Pro only
through model routing's deep tier (olisar/routing), never by walking this chain.
Specialized models (computer-use, robotics, embeddings) aren't chat models and are
excluded too.

When the preferred model is rate-limited, the client walks DOWN this list to the
next available model (see GeminiClient._raw_generate). Edit the order here (or
set a guild's `default_model` to change the starting point) to retune.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    name: str
    rpm: int  # our per-minute throttle on a free key: Google's figure where it named one
    label: str
    # Free-tier requests per day. Google doesn't publish these (AI Studio shows a project its
    # own), so they were measured on 2026-10-10 by asking a free key until Google refused it
    # and named the limit (see the table's comment). They stand in until Google names the
    # project's own in a daily 429 (olisar.gemini.quota.read_refusal), and the Usage page
    # counts against that instead. Nothing here stops a request: Olisar only treats a model as
    # spent when Google says so.
    rpd: int = 0
    # The per-minute throttle on a key with billing on: Google's Tier 1 ballpark. Google
    # turns a billed key away far later than a free one, so the free figure there only
    # pushed a busy server down the chain for no reason.
    paid_rpm: int = 1000


# Best -> worst. The chain starts at the guild's default_model and continues down.
#
# The `-latest` aliases sit *below* their pinned twins, never at the head. An alias is
# whatever Google points it at today and the API won't tell you what: `models.get` reports
# version="Gemini Flash Latest" — a literal string — where every pinned model reports a real
# build ("3.5-flash-05-2026", "001"). Running the default on one meant a provider-side roll
# could change request validation under a live bot with no deploy on our side, which is
# exactly how every tool-backed reply started 400ing on an unsupported role.
#
# It's the same bargain image.yml already refuses for the server image, where `latest`
# tracks releases rather than main so operators don't silently run code nobody shipped. A
# new Flash model is now adopted the same way: deliberately, in a release.
#
# They stay in the chain as a lower rung, because an alias still resolves when a pinned
# model is retired — a self-healing last resort is worth more there than at the front.
# The 2.0 pair used to sit at positions 4 and 8 and are gone: generateContent answers
# `404 ... is no longer available`. Note models.get still returns metadata for a retired
# model, so "does this name resolve?" is not the question — only a real generation is.
# The daily self-test (olisar/gemini/canary.py) sweeps every rung, aliases included.
#
# Matched to Google's deprecations page as of 2026-10-09. Gemini 3.5 Flash (requests now
# routed to 3.6 Flash) and Gemini 3 Flash Preview (superseded by 3.6 Flash) are gone; see
# RETIRED. Gemini 3.1 Flash-Lite is deprecated too but stays a lower rung until it shuts
# down, no earlier than 2027-05-07: every model brings its own free daily allowance, and
# dropping it early would cut a free key's day by about half (500 of a newer project's 1,040).
#
# The 2.5 pair isn't deprecated, but Google only serves it to projects that used it before.
# A newer project gets refused there, which parks each for an hour like a retired model and
# costs the chain nothing else.
RANKED: list[ModelInfo] = [
#
# Daily limits, as Google named them refusing a free key on 2026-10-10: 20 on 3.8 and 3.6
# Flash, 500 on 3.5 and 3.1 Flash-Lite, and 15 a minute on the Flash-Lite models. The
# published figures (250 and 1,000) were far out. A -latest alias draws on the allowance of the model
# it points to (see _ALIAS_OF), so its figure is that model's and adds nothing to the day.
# The 2.5 pair couldn't be measured, since Google doesn't serve them to newer projects: 2.5
# Flash is put at the current Flash models' 20, and 2.5 Flash-Lite keeps its last published
# figure. Google's per-minute limit for Flash never came up before the daily one, so 10
# stays Olisar's own throttle there.
    ModelInfo("gemini-3.8-flash", 10, "Gemini 3.8 Flash", rpd=20),
    ModelInfo("gemini-flash-latest", 10, "newest Flash (auto-updates)", rpd=20),
    ModelInfo("gemini-3.6-flash", 10, "Gemini 3.6 Flash", rpd=20),
    ModelInfo("gemini-2.5-flash", 10, "Gemini 2.5 Flash", rpd=20),
    ModelInfo("gemini-3.5-flash-lite", 15, "Gemini 3.5 Flash-Lite", rpd=500, paid_rpm=4000),
    ModelInfo("gemini-flash-lite-latest", 15, "newest Flash-Lite (auto-updates)", rpd=500, paid_rpm=4000),
    ModelInfo("gemini-3.1-flash-lite", 15, "Gemini 3.1 Flash-Lite", rpd=500, paid_rpm=4000),
    ModelInfo("gemini-2.5-flash-lite", 15, "Gemini 2.5 Flash-Lite", rpd=1000, paid_rpm=4000),
]

# The head of the chain, and the default for a fresh guild / an unset GEMINI_CHAT_MODEL.
# Imported by olisar.config and olisar.db.models so the default lives in exactly one place.
DEFAULT_CHAT_MODEL = "gemini-3.8-flash"

# The cheap model for off-reply-path synthesis (summaries, personas, glossary). Pinned for
# the same reason, and to the concrete twin of the alias it used to name.
DEFAULT_LITE_MODEL = "gemini-3.5-flash-lite"

# Models that left the chain, and what takes their place. A server's stored default_model
# moves to the replacement on startup (see migrate_model_default), and model_chain reads a
# retired name as its replacement, so a GEMINI_CHAT_MODEL left in someone's .env (the old
# .env.example set it to gemini-3.5-flash) doesn't pin the head of every reply to a model
# Google is turning off. Gemini 3.5 Flash goes to the new default rather than 3.6 Flash,
# where Google routes it: it was the seeded default, so almost nobody chose it.
RETIRED: dict[str, str] = {
    "gemini-3.5-flash": DEFAULT_CHAT_MODEL,
    "gemini-3-flash-preview": "gemini-3.6-flash",
}

# What installs before this change defaulted to. Stored `guild_config.default_model` rows
# still holding it are moved to DEFAULT_CHAT_MODEL on startup (see migrate_model_default):
# the value was seeded, not chosen, so leaving it would pin every existing install to the
# alias forever and make this change a no-op where it matters most.
LEGACY_DEFAULT_CHAT_MODEL = "gemini-flash-latest"

RANKED_NAMES = [m.name for m in RANKED]

# The stored ``default_model`` that means "let model routing pick" (olisar/routing). With
# routing off it replies exactly as DEFAULT_CHAT_MODEL would, so a server on it loses
# nothing on an install that never turns routing on.
AUTO = "auto"

# The Pro model routing's deep tier asks first on a key with billing on. Pro has no free
# allowance: on a free key it answers 429 naming a -FreeTier quota (which is how
# olisar.gemini.tier tells the tiers apart), so the free throttle below is 0 and a free key
# never sends it a reply. A preview, so Google may retire it on short notice; the deep chain
# falls back to the Flash rungs on a 404 like any other.
PRO_MODEL = "gemini-3.1-pro-preview"
PRO_LABEL = "Gemini 3.1 Pro"

# Routing's light tier: the Flash-Lite rungs, best first, then the Flash rungs as a last
# resort. Today's chain would answer from those anyway, and a light reply that falls up a
# tier costs more but still answers.
LIGHT_RANKED_NAMES = [
    *(m.name for m in RANKED if "flash-lite" in m.name),
    *(m.name for m in RANKED if "flash-lite" not in m.name),
]

_RPM = {m.name: m.rpm for m in RANKED}
_RPM["gemini-embedding-001"] = 100  # embeddings (single model, no fallback)
_RPM[PRO_MODEL] = 0  # no free allowance at all
_PAID_RPM = {m.name: m.paid_rpm for m in RANKED}
_PAID_RPM["gemini-embedding-001"] = 3000
# Below Google's Tier 1 limit for Pro on purpose: past it, a deep reply goes to Flash. Google
# named that limit refusing a billed key on 2026-10-11: 25 a minute
# (GenerateRequestsPerMinutePerProjectPerModel). This was 50, and a refusal parks Pro for two
# minutes, so a busy minute sent every deep reply for the next two to Flash instead.
_PAID_RPM[PRO_MODEL] = 20
_RPD = {m.name: m.rpd for m in RANKED}
_RPD["gemini-embedding-001"] = 1000

# Thinking levels each model takes, from Google's thinking guide as read on 2026-10-10. A
# level a model doesn't take is sent as the nearest one it does (see thinking_level_for).
# Models not listed (the -latest aliases, 3.1 Flash-Lite) get the level as asked; a 400
# for it is remembered and the request retried without (GeminiClient._raw_generate).
THINKING_LEVELS = ("minimal", "low", "medium", "high")
_LEVELS: dict[str, tuple[str, ...]] = {
    "gemini-3.8-flash": ("low", "medium", "high"),
    "gemini-3.6-flash": ("minimal", "low", "medium", "high"),
    "gemini-3.5-flash-lite": ("minimal", "low", "medium", "high"),
    PRO_MODEL: ("low", "medium", "high"),
}
# The Gemini 2.5 models predate thinking levels and take a token budget instead. High stays
# well under the deep tier's 8,192-token output ceiling, which thinking shares with the
# reply: a budget equal to the ceiling could spend all of it thinking and send nothing.
_BUDGET_FOR_LEVEL = {"minimal": 0, "low": 512, "medium": 1024, "high": 4096}

# Google Search grounding has a daily allowance of its own, on top of the model's. On a free
# key it's only the Gemini 2.5 models that have one, shared between the two below; every
# Gemini 3 model answers a grounded request with a bare 429. So that's where a free key's
# web search runs (see GeminiClient.search). Walking the chat chain instead spent three
# refused requests per search and parked the top three chat models for two minutes each.
# A project Google won't serve the 2.5 models to (one that never used them) has no free
# web search at all.
GROUNDING_RPD = 500
SEARCH_FREE_CHAIN = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

# Text-to-image on a key with billing on (Nano Banana 2 Lite). Gemini's image models have a
# free quota of 0, so a free key makes images on Cloudflare Workers AI instead, if at all
# (see olisar/imaging.py).
GEMINI_IMAGE_MODEL = "gemini-3.1-flash-lite-image"


# Vision (image-understanding) fallback chain, used for image recognition and the
# one-time index descriptions. Every Gemini Flash model is multimodal, so this is
# deliberately drawn from the *lower* end of the chat ranking (the Flash-Lite tier):
# captioning is bulk, low-stakes work, and the rate limiter is keyed by model name —
# so steering vision onto the models chat reaches last keeps image work from parking
# the top chat models. Reorder to trade quality for contention.
#
# This chain used to *start* on gemini-2.0-flash, which is retired. A 404 raised rather
# than falling through, so every image description failed outright — the whole feature
# was down and nothing said so. Both retired entries are gone, a 404 now costs the next
# model instead of the request (see client._MODEL_RETIRED), and the self-test still
# sweeps this chain's head so the next retirement is a log line rather than a silent outage.
IMAGE_RANKED: list[ModelInfo] = [
    ModelInfo("gemini-3.5-flash-lite", 15, "Gemini 3.5 Flash-Lite (multimodal)"),
    ModelInfo("gemini-3.1-flash-lite", 15, "Gemini 3.1 Flash-Lite (multimodal)"),
    ModelInfo("gemini-2.5-flash-lite", 15, "Gemini 2.5 Flash-Lite (multimodal)"),
    ModelInfo("gemini-flash-lite-latest", 15, "newest Flash-Lite (multimodal)"),
]
IMAGE_RANKED_NAMES = [m.name for m in IMAGE_RANKED]

# The vision chain's head, and the default for an unset GEMINI_VISION_MODEL.
DEFAULT_VISION_MODEL = IMAGE_RANKED_NAMES[0]


# The model each -latest alias points to, and so whose daily allowance it draws on. Measured
# on 2026-10-10: a reply from gemini-flash-latest reports model_version gemini-3.8-flash, and
# with 3.8 Flash refused for the day the alias was refused too, Google naming 3.8 Flash's
# quota (20 a day) as the one spent. So the alias is a second way to reach one allowance,
# not a second allowance. Google moves aliases, so each reply updates this (note_alias).
_ALIAS_OF: dict[str, str] = {
    "gemini-flash-latest": "gemini-3.8-flash",
    "gemini-flash-lite-latest": "gemini-3.5-flash-lite",
}


def is_alias(model: str) -> bool:
    return model.endswith("-latest")


def quota_model(model: str) -> str:
    """The model whose daily allowance ``model`` is counted against: itself, or for a
    -latest alias, the model it points to."""
    return _ALIAS_OF.get(model, model)


def aliases_of(model: str) -> list[str]:
    """The aliases that draw on ``model``'s allowance."""
    return [alias for alias, target in _ALIAS_OF.items() if target == model and alias != model]


def note_alias(alias: str, target: str | None) -> None:
    """Record what a -latest alias points to now, from a reply's model_version or the quota
    a refusal named."""
    if isinstance(target, str) and target and is_alias(alias) and target != alias and not is_alias(target):
        _ALIAS_OF[alias] = target


def rpm_for(model: str, paid: bool = False) -> int:
    """Olisar's own per-minute throttle for ``model``: the free-tier figure, or Google's
    Tier 1 ballpark when the key has billing on."""
    if paid:
        return _PAID_RPM.get(model, 1000)
    return _RPM.get(model, 10)


def rpd_for(model: str) -> int:
    """The free-tier daily limit we assume for ``model``, or 0 when we have no figure."""
    return _RPD.get(model, 0)


def model_chain(preferred: str) -> list[str]:
    """Models to try, in order, starting from `preferred`.

    If `preferred` is in the ranking, the chain is everything from it downward. A retired
    model starts where its replacement does (see RETIRED), and AUTO where the default
    does. Otherwise the chain is `preferred` first, then the whole ranking as fallback.
    """
    preferred = DEFAULT_CHAT_MODEL if preferred == AUTO else RETIRED.get(preferred, preferred)
    if preferred in RANKED_NAMES:
        return RANKED_NAMES[RANKED_NAMES.index(preferred) :]
    return [preferred, *RANKED_NAMES]


def light_chain() -> list[str]:
    """Routing's light tier: the Flash-Lite rungs, then the Flash rungs."""
    return list(LIGHT_RANKED_NAMES)


def deep_chain(preferred: str, *, pro: bool) -> list[str]:
    """Routing's deep tier: Pro first when ``pro`` (a billed key with Pro allowed), then the
    same chain a standard reply walks."""
    chain = model_chain(preferred)
    return [PRO_MODEL, *chain] if pro else chain


def thinking_for(model: str, level: str) -> tuple[str, str | int] | None:
    """How to ask ``model`` for thinking ``level``: ``("level", name)``, or
    ``("budget", tokens)`` for a Gemini 2.5 model, which predates levels. A level a model is
    known not to take becomes the nearest one it does, rounding up first: the tier asked for
    more thought than the model's floor allows, not less."""
    if level not in THINKING_LEVELS:
        return None
    if "2.5" in model:
        return ("budget", _BUDGET_FOR_LEVEL[level])
    supported = _LEVELS.get(model)
    if supported is None or level in supported:
        return ("level", level)
    rank = THINKING_LEVELS.index(level)
    higher = [lv for lv in supported if THINKING_LEVELS.index(lv) > rank]
    lower = [lv for lv in supported if THINKING_LEVELS.index(lv) < rank]
    return ("level", higher[0] if higher else lower[-1])


def image_model_chain(preferred: str | None = None) -> list[str]:
    """Vision models to try, in order. Like ``model_chain`` but over the
    image-capable ranking; ``preferred=None`` runs the whole chain top-down."""
    if not preferred:
        return list(IMAGE_RANKED_NAMES)
    preferred = RETIRED.get(preferred, preferred)
    if preferred in IMAGE_RANKED_NAMES:
        return IMAGE_RANKED_NAMES[IMAGE_RANKED_NAMES.index(preferred) :]
    return [preferred, *IMAGE_RANKED_NAMES]
