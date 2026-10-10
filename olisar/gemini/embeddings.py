"""Text embeddings via gemini-embedding-001.

Two things worth knowing:
* We request 768 dims (Matryoshka truncation) to keep the vector DB small. At
  reduced dims the model does NOT return unit vectors, so we L2-normalize here —
  important because sqlite-vec ranks by L2 distance, which only matches cosine
  similarity for normalized vectors.
* A single request embeds a whole batch, so batching is one rate-limited call.
"""

from __future__ import annotations

import math

from google.genai import errors as genai_errors
from google.genai import types

from olisar.config import settings
from olisar.gemini.client import get_gemini
from olisar.gemini.quota import read_refusal
from olisar.gemini import spend
from olisar.gemini.rate_limiter import (
    BudgetSpent,
    RateLimitExceeded,
    get_rate_limiter,
    mark_spent,
    record_usage,
)

BATCH_SIZE = 50  # texts per request


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


async def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []
    model = settings.gemini_embed_model
    client = await get_gemini().aclient()
    # A billed key past a budget that stops there: memory search and indexing wait for the
    # month too. Answering on the cheapest model changes nothing here; there's one model.
    if get_rate_limiter().paid and await spend.over_budget():
        if (await spend.budget()).action == spend.STOP:
            raise BudgetSpent(model)
    out: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        await get_rate_limiter().acquire(model)
        try:
            resp = await client.aio.models.embed_content(
                model=model,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type, output_dimensionality=settings.embed_dim
                ),
            )
        except genai_errors.APIError as exc:
            # Unlike the chat path, embeddings have no fallback chain — one model. On a 429
            # we must PARK it (penalize → cooldown) so acquire() backs off, instead of
            # re-hitting the exhausted quota on every message/index tick (that's what spammed
            # 429s in the log). Raise the typed error callers already defer on. A spent
            # daily quota parks it until Google's reset, and acquire() then refuses at once
            # rather than waiting out the day.
            if getattr(exc, "code", None) == 429:
                refusal = read_refusal(exc)
                if refusal.daily:
                    await mark_spent(model, refusal.limit)
                    raise RateLimitExceeded(model, "daily") from exc
                get_rate_limiter().penalize(model, reason="a rate limit (429)")
                raise RateLimitExceeded(model, "rpm") from exc
            raise
        # Embeddings don't report token counts. About four characters a token is close
        # enough to price them, and they're the cheapest thing Olisar sends.
        await record_usage(
            model, 0, source="embed", input_tokens=sum(len(t) for t in batch) // 4
        )
        out.extend(_normalize(e.values) for e in resp.embeddings)
    return out


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed stored content (messages, summaries, chunks) for retrieval."""
    return await _embed(texts, "RETRIEVAL_DOCUMENT")


async def embed_query(text: str) -> list[float]:
    """Embed a single query string; returns one normalized vector."""
    result = await _embed([text], "RETRIEVAL_QUERY")
    return result[0] if result else []
