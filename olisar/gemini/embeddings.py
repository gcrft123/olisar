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

from google.genai import types

from olisar.config import settings
from olisar.gemini.client import get_gemini
from olisar.gemini.rate_limiter import get_rate_limiter, record_usage

BATCH_SIZE = 50  # texts per request


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


async def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []
    model = settings.gemini_embed_model
    client = await get_gemini().aclient()
    out: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        await get_rate_limiter().acquire(model)
        resp = await client.aio.models.embed_content(
            model=model,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=settings.embed_dim
            ),
        )
        await record_usage(model, 0, source="embed")  # embeddings don't report token counts
        out.extend(_normalize(e.values) for e in resp.embeddings)
    return out


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed stored content (messages, summaries, chunks) for retrieval."""
    return await _embed(texts, "RETRIEVAL_DOCUMENT")


async def embed_query(text: str) -> list[float]:
    """Embed a single query string; returns one normalized vector."""
    result = await _embed([text], "RETRIEVAL_QUERY")
    return result[0] if result else []
