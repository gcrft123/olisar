"""Knowledge-base ingestion worker.

Owned by the bot process (single embed-quota owner). One pending source is
processed per call: claim it, gather chunks via network/extraction *outside* any
DB transaction (so crawls don't hold a write lock), then write chunks in a short
transaction. The chunks land with embedded=False; the memory worker's embed pass
vectorizes them. Re-ingest is idempotent (old chunks + vectors are replaced).
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, select

from olisar.db.engine import session_scope
from olisar.db.models import KBChunk, KBSource, KBSourceType, KBStatus, utcnow
from olisar.knowledge.chunker import chunk_document
from olisar.knowledge.crawler import Page, crawl, fetch_page
from olisar.knowledge.extract import extract_document
from olisar.memory.vectors import delete_embedding
from olisar.memory.writer import estimate_tokens

log = logging.getLogger("olisar.knowledge.ingest")


async def _gather(stype: KBSourceType, uri: str, depth: int, max_pages: int) -> list[dict]:
    """Network/extraction only — no DB. Returns chunk records."""
    if stype == KBSourceType.doc:
        pages = [Page(url=None, title=Path(uri).name, text=extract_document(uri))]
    elif stype == KBSourceType.url:
        page = await fetch_page(uri)
        pages = [page] if page else []
    elif stype == KBSourceType.website:
        pages = await crawl(uri, max_depth=depth, max_pages=max_pages)
    else:
        pages = []

    records: list[dict] = []
    for page in pages:
        for chunk in chunk_document(page.text):
            records.append({"text": chunk, "url": page.url, "title": page.title})
    return records


async def _replace_chunks(session, source_id: int) -> None:
    old_ids = (
        await session.scalars(select(KBChunk.id).where(KBChunk.source_id == source_id))
    ).all()
    for cid in old_ids:
        await delete_embedding(session, "kb_chunk_embedding", cid)
    await session.execute(delete(KBChunk).where(KBChunk.source_id == source_id))


async def process_pending_sources() -> bool:
    """Process one pending source from any guild. Returns True if a source was handled."""
    # Claim the oldest pending source in a short transaction. Deliberately NOT guild-scoped:
    # the console adds sources under whichever server the operator is viewing, and Discord
    # adds them under the server the command ran in — so filtering to one target guild would
    # strand every other server's sources on "pending" forever.
    async with session_scope() as session:
        src = await session.scalar(
            select(KBSource)
            .where(KBSource.status == KBStatus.pending)
            .order_by(KBSource.id)
            .limit(1)
        )
        if src is None:
            return False
        src.status = KBStatus.crawling
        sid, stype, uri = src.id, src.type, src.uri
        depth, max_pages, gid = src.crawl_depth, src.max_pages, src.guild_id

    # Gather outside any transaction (network-bound).
    try:
        records = await _gather(stype, uri, depth, max_pages)
    except Exception as exc:
        log.exception("ingest failed for source %s", sid)
        async with session_scope() as session:
            src = await session.get(KBSource, sid)
            if src:
                src.status = KBStatus.error
                src.error = str(exc)[:500]
        return True

    # Write chunks in a short transaction.
    async with session_scope() as session:
        src = await session.get(KBSource, sid)
        if src is None:
            return True
        await _replace_chunks(session, sid)
        for i, rec in enumerate(records):
            session.add(
                KBChunk(
                    source_id=sid,
                    guild_id=gid,
                    ordinal=i,
                    content=rec["text"],
                    token_count=estimate_tokens(rec["text"]),
                    page_url=rec.get("url"),
                    heading_path=rec.get("title"),
                    embedded=False,
                )
            )
        if records:
            src.status = KBStatus.ready
            src.last_ingested_at = utcnow()
            src.error = None
        else:
            src.status = KBStatus.error
            src.error = "no content could be extracted"

    log.info("ingested source %s: %d chunks", sid, len(records))
    return True
