"""Changes to a knowledge source, shared by the console's API and Olisar's settings tools.

Each caller checks its input its own way (an HTTP error from the API, a sentence the model
reads back from ``olisar.self_settings``). What a change actually does lives here once, so
the two can't drift on the parts that are easy to get wrong: the embeddings a deleted
source leaves behind, and which clock a schedule is measured from.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import KBChunk, KBSource, KBSourceType, KBStatus, utcnow
from olisar.knowledge.refresh import _aware, next_run_at
from olisar.memory.vectors import delete_embedding

# Statuses the ingest worker will still move on its own. A source in one of these is
# already being read, so asking for another read is refused rather than stacked.
BUSY = (KBStatus.pending, KBStatus.crawling, KBStatus.chunking)


def new_source(
    *,
    guild_id: int,
    type: str,
    uri: str,
    crawl_depth: int,
    max_pages: int,
    refresh_hours: int,
    added_by: int | None,
) -> KBSource:
    """A queued source, for the caller to add to its session."""
    return KBSource(
        guild_id=guild_id,
        type=KBSourceType(type),
        uri=uri,
        title=uri,
        status=KBStatus.pending,
        crawl_depth=crawl_depth,
        max_pages=max_pages,
        added_by=added_by,
        refresh_interval_hours=refresh_hours,
        # Measured from the first read, not from now: the source is about to be read
        # anyway, and stamping "now" would make an hourly schedule fire twice up front.
        next_refresh_at=next_run_at(utcnow(), refresh_hours),
    )


def requeue(src: KBSource) -> None:
    """Read ``src`` again now. The row is re-queued in place, so the passages it already
    has stay searchable until the new read replaces them."""
    src.status = KBStatus.pending
    src.error = None
    # A manual read restarts the clock, so "every 6 hours" means six hours from this
    # read rather than six from whenever the last scheduled one happened to land.
    src.next_refresh_at = next_run_at(utcnow(), src.refresh_interval_hours)


def set_schedule(src: KBSource, hours: int) -> None:
    """Re-read ``src`` every ``hours`` hours; 0 turns the schedule off."""
    src.refresh_interval_hours = hours
    # Re-based on the last read rather than on now, so shortening an interval takes
    # effect against the content's real age instead of granting a fresh full period.
    anchor = _aware(src.last_checked_at) or _aware(src.last_ingested_at) or utcnow()
    src.next_refresh_at = next_run_at(anchor, hours)


async def delete_source(session: AsyncSession, src: KBSource) -> int:
    """Delete ``src`` and everything read out of it. Returns how many passages went."""
    chunk_ids = (
        await session.scalars(select(KBChunk.id).where(KBChunk.source_id == src.id))
    ).all()
    # The vectors live in a separate virtual table the cascade can't reach.
    for cid in chunk_ids:
        await delete_embedding(session, "kb_chunk_embedding", cid)
    await session.delete(src)  # cascades to kb_chunk rows
    return len(chunk_ids)
