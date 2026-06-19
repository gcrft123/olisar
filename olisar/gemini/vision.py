"""Image understanding via the Gemini vision fallback chain.

Two callers:
* the live conversation cog, which captions images people post so the index can
  find them later ("what was that car someone screenshotted?"), and
* the search backfill, which captions historical images a few at a time.

Both want the same thing: a short, factual, search-oriented description, produced
best-effort. When every vision model is rate-limited this returns '' rather than
raising, so ingestion never blocks on captioning — the filename is already indexed.

A small semaphore serializes captioning so a burst of image posts can't fan out
into a thundering herd of vision calls against the free tier.
"""

from __future__ import annotations

import asyncio
import logging

from olisar.gemini.client import get_gemini
from olisar.gemini.rate_limiter import RateLimitExceeded

log = logging.getLogger("olisar.vision")

_PROMPT = (
    "You are captioning images for a searchable chat index. In 1–2 plain "
    "sentences, describe the image(s) factually: the main subjects, anything "
    "happening, and — verbatim — any visible text, usernames, handles, links, or "
    "logos (these are often what people search for). No preamble, no markdown."
)

# Serialize vision calls (free-tier friendliness); the rate limiter does the rest.
_semaphore = asyncio.Semaphore(2)


async def describe_images(images: list[tuple[bytes, str]]) -> str:
    """Best-effort one-shot description of ``(data, mime)`` images. Returns '' on
    empty input, rate-limit exhaustion, or any failure — never raises."""
    if not images:
        return ""
    async with _semaphore:
        try:
            text = await get_gemini().caption_images(images, instruction=_PROMPT)
        except RateLimitExceeded:
            return ""  # every vision model parked; filename stays searchable
        except Exception:
            log.exception("image description failed")
            return ""
    return " ".join(text.split()).strip()
