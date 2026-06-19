"""Gemini model ranking and the fallback chain.

Free-tier *chat* models ranked best -> worst. Pro models are excluded (paid as
of 2026) to honor the no-paid-API constraint; specialized models (computer-use,
robotics, embeddings) aren't chat models and are excluded too.

When the preferred model is rate-limited, the client walks DOWN this list to the
next available model (see GeminiClient._raw_generate). Edit the order here (or
set a guild's `default_model` to change the starting point) to retune.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    name: str
    rpm: int  # our conservative per-minute throttle (free-tier ballpark)
    label: str


# Best -> worst. The chain starts at the guild's default_model and continues down.
RANKED: list[ModelInfo] = [
    ModelInfo("gemini-flash-latest", 10, "newest Flash (auto-updates)"),
    ModelInfo("gemini-3.5-flash", 10, "Gemini 3.5 Flash"),
    ModelInfo("gemini-3-flash-preview", 10, "Gemini 3 Flash"),
    ModelInfo("gemini-2.5-flash", 10, "Gemini 2.5 Flash"),
    ModelInfo("gemini-2.0-flash", 15, "Gemini 2.0 Flash"),
    ModelInfo("gemini-flash-lite-latest", 15, "newest Flash-Lite (auto-updates)"),
    ModelInfo("gemini-3.1-flash-lite", 15, "Gemini 3.1 Flash-Lite"),
    ModelInfo("gemini-2.5-flash-lite", 15, "Gemini 2.5 Flash-Lite"),
    ModelInfo("gemini-2.0-flash-lite", 30, "Gemini 2.0 Flash-Lite"),
]

RANKED_NAMES = [m.name for m in RANKED]
_RPM = {m.name: m.rpm for m in RANKED}
_RPM["gemini-embedding-001"] = 100  # embeddings (single model, no fallback)


# Vision (image-understanding) fallback chain, used for image recognition and the
# one-time index descriptions. Every Gemini Flash model is multimodal, so this is
# deliberately drawn from the *lower* end of the chat ranking (Flash-Lite + 2.0
# Flash): captioning is bulk, low-stakes work, and the rate limiter is keyed by
# model name — so steering vision onto the models chat reaches last keeps image
# work from parking the top chat models. Reorder to trade quality for contention.
IMAGE_RANKED: list[ModelInfo] = [
    ModelInfo("gemini-2.0-flash", 15, "Gemini 2.0 Flash (multimodal)"),
    ModelInfo("gemini-2.5-flash-lite", 15, "Gemini 2.5 Flash-Lite (multimodal)"),
    ModelInfo("gemini-flash-lite-latest", 15, "newest Flash-Lite (multimodal)"),
    ModelInfo("gemini-2.0-flash-lite", 30, "Gemini 2.0 Flash-Lite (multimodal)"),
]
IMAGE_RANKED_NAMES = [m.name for m in IMAGE_RANKED]

# Note: image *generation* (text -> image) does NOT run on Gemini — its image
# models are paid-only (free request quota = 0). That lives in olisar/imaging.py
# on Cloudflare Workers AI instead.


def rpm_for(model: str) -> int:
    return _RPM.get(model, 10)


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
