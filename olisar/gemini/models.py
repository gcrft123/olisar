"""Gemini model ranking and the fallback chain.

*Chat* models ranked best -> worst. Every one is on Google's free tier. Pro models are
excluded: they aren't (as of 2026), and a key with billing on replies through the same
chain. Specialized models (computer-use, robotics, embeddings) aren't chat models and are
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
    rpm: int  # our conservative per-minute throttle on a free key (free-tier ballpark)
    label: str
    # Free-tier requests per day. Google's rate-limits page stopped listing these (AI Studio
    # shows a project its own), so these are the last published figures. They stand in until
    # Google names the real one in a daily 429 (see olisar.gemini.quota.read_refusal), and the
    # Usage page counts against that instead. Nothing here stops a request: Olisar only treats
    # a model as spent when Google says so.
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
# The daily self-test (olisar/gemini/canary.py) still sweeps the ``-latest`` aliases.
RANKED: list[ModelInfo] = [
    ModelInfo("gemini-3.5-flash", 10, "Gemini 3.5 Flash", rpd=250),
    ModelInfo("gemini-flash-latest", 10, "newest Flash (auto-updates)", rpd=250),
    ModelInfo("gemini-3-flash-preview", 10, "Gemini 3 Flash", rpd=250),
    ModelInfo("gemini-2.5-flash", 10, "Gemini 2.5 Flash", rpd=250),
    ModelInfo("gemini-3.1-flash-lite", 15, "Gemini 3.1 Flash-Lite", rpd=1000, paid_rpm=4000),
    ModelInfo("gemini-flash-lite-latest", 15, "newest Flash-Lite (auto-updates)", rpd=1000, paid_rpm=4000),
    ModelInfo("gemini-2.5-flash-lite", 15, "Gemini 2.5 Flash-Lite", rpd=1000, paid_rpm=4000),
]

# The head of the chain, and the default for a fresh guild / an unset GEMINI_CHAT_MODEL.
# Imported by olisar.config and olisar.db.models so the default lives in exactly one place.
DEFAULT_CHAT_MODEL = "gemini-3.5-flash"

# The cheap model for off-reply-path synthesis (summaries, personas, glossary). Pinned for
# the same reason, and to the concrete twin of the alias it used to name.
DEFAULT_LITE_MODEL = "gemini-3.1-flash-lite"

# What installs before this change defaulted to. Stored `guild_config.default_model` rows
# still holding it are moved to DEFAULT_CHAT_MODEL on startup (see migrate_model_default):
# the value was seeded, not chosen, so leaving it would pin every existing install to the
# alias forever and make this change a no-op where it matters most.
LEGACY_DEFAULT_CHAT_MODEL = "gemini-flash-latest"

RANKED_NAMES = [m.name for m in RANKED]
_RPM = {m.name: m.rpm for m in RANKED}
_RPM["gemini-embedding-001"] = 100  # embeddings (single model, no fallback)
_PAID_RPM = {m.name: m.paid_rpm for m in RANKED}
_PAID_RPM["gemini-embedding-001"] = 3000
_RPD = {m.name: m.rpd for m in RANKED}
_RPD["gemini-embedding-001"] = 1000

# Google Search grounding has a daily allowance of its own, on top of the model's. On a free
# key it's only the Gemini 2.5 models that have one, shared between the two below; every
# Gemini 3 model answers a grounded request with a bare 429. So that's where a free key's
# web search runs (see GeminiClient.search). Walking the chat chain instead spent three
# refused requests per search and parked the top three chat models for two minutes each.
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
    ModelInfo("gemini-3.1-flash-lite", 15, "Gemini 3.1 Flash-Lite (multimodal)"),
    ModelInfo("gemini-2.5-flash-lite", 15, "Gemini 2.5 Flash-Lite (multimodal)"),
    ModelInfo("gemini-flash-lite-latest", 15, "newest Flash-Lite (multimodal)"),
]
IMAGE_RANKED_NAMES = [m.name for m in IMAGE_RANKED]

# The vision chain's head, and the default for an unset GEMINI_VISION_MODEL.
DEFAULT_VISION_MODEL = IMAGE_RANKED_NAMES[0]


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

    If `preferred` is in the ranking, the chain is everything from it downward.
    Otherwise the chain is `preferred` first, then the whole ranking as fallback.
    """
    if preferred in RANKED_NAMES:
        return RANKED_NAMES[RANKED_NAMES.index(preferred) :]
    return [preferred, *RANKED_NAMES]


def image_model_chain(preferred: str | None = None) -> list[str]:
    """Vision models to try, in order. Like ``model_chain`` but over the
    image-capable ranking; ``preferred=None`` runs the whole chain top-down."""
    if not preferred:
        return list(IMAGE_RANKED_NAMES)
    if preferred in IMAGE_RANKED_NAMES:
        return IMAGE_RANKED_NAMES[IMAGE_RANKED_NAMES.index(preferred) :]
    return [preferred, *IMAGE_RANKED_NAMES]
