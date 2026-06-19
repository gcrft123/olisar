"""Knowledge-base retrieval (KNN over kb_chunk vectors, with citations)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import KBChunk
from olisar.gemini.embeddings import embed_query
from olisar.memory.vectors import knn

log = logging.getLogger("olisar.knowledge")


def _citation(chunk: KBChunk) -> str:
    return chunk.heading_path or chunk.page_url or "knowledge base"


async def kb_block_from_qvec(
    session: AsyncSession, guild_id: int, qvec: list[float], k: int = 4
) -> str:
    """Format the top-k KB chunks for a query vector. Empty string if none."""
    if not qvec:
        return ""
    hits = await knn(session, "kb_chunk_embedding", qvec, k=k)
    if not hits:
        return ""
    by_id = {
        c.id: c
        for c in (
            await session.scalars(
                select(KBChunk).where(
                    KBChunk.id.in_([rid for rid, _ in hits]),
                    KBChunk.guild_id == guild_id,
                )
            )
        ).all()
    }
    lines = []
    used = []
    for rid, _ in hits:
        chunk = by_id.get(rid)
        if chunk and chunk.content.strip():
            # The content goes to the model untagged (no inline citation — the bot
            # only cites web-search results); the citation is kept for the log only.
            lines.append(chunk.content.strip())
            used.append(f"#{rid} {_citation(chunk)}")
    if not lines:
        return ""
    log.info("knowledge base: %d chunk(s) used — %s", len(used), "; ".join(used))
    return "From the community knowledge base:\n- " + "\n- ".join(lines)


async def search_knowledge(
    session: AsyncSession, guild_id: int, query: str, k: int = 5
) -> str:
    """Embed a query and return matching KB chunks with citations (for the tool)."""
    return await kb_block_from_qvec(session, guild_id, await embed_query(query), k=k)
