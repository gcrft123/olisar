"""Knowledge-base ingestion worker.

Owned by the bot process (single embed-quota owner). One pending source is
processed per call: claim it, gather chunks via network/extraction *outside* any
DB transaction (so crawls don't hold a write lock), then write chunks in a short
transaction. The chunks land with embedded=False; the memory worker's embed pass
vectorizes them. Re-ingest is idempotent (old chunks + vectors are replaced).

Re-reading is content-keyed rather than destructive: a passage whose text is unchanged keeps
its row, and therefore its embedding, so a scheduled re-read of a page that hasn't changed
costs nothing against the free embedding quota. See :func:`plan_chunk_sync`.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import select

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


class ChunkPlan(NamedTuple):
    """What a re-read changes. ``reuse`` is ``(chunk_id, ordinal, url, title)``; ``insert`` is
    ``(ordinal, record)``; ``delete`` is the chunk ids no longer present upstream."""

    reuse: list[tuple[int, int, str | None, str | None]]
    insert: list[tuple[int, dict]]
    delete: list[int]


def plan_chunk_sync(existing: list[tuple[int, str]], records: list[dict]) -> ChunkPlan:
    """Match freshly-read passages against the ones already stored, by exact text.

    Pure, so the matching rules are testable without a schema. The point is the embedding: a
    ``kb_chunk`` row's id *is* its rowid in the ``kb_chunk_embedding`` vector table, so a row
    that survives a re-read keeps its vector and never has to be embedded again. Wiping and
    re-inserting every passage — which is what this used to do — re-embedded a whole site on
    every read, which is affordable once by hand and not at all on a schedule.

    Position is not identity: a page that gains a paragraph shifts every passage after it, so
    matching by ordinal would count the whole tail as new. Text is the key, and duplicate text
    is matched one-for-one so a page carrying the same boilerplate twice stays stable.
    """
    buckets: dict[str, deque[int]] = {}
    for chunk_id, content in existing:
        buckets.setdefault(content, deque()).append(chunk_id)

    reuse: list[tuple[int, int, str | None, str | None]] = []
    insert: list[tuple[int, dict]] = []
    for ordinal, rec in enumerate(records):
        bucket = buckets.get(rec["text"])
        if bucket:
            reuse.append((bucket.popleft(), ordinal, rec.get("url"), rec.get("title")))
        else:
            insert.append((ordinal, rec))

    delete = [chunk_id for bucket in buckets.values() for chunk_id in bucket]
    return ChunkPlan(reuse=reuse, insert=insert, delete=delete)


async def _apply_chunk_plan(session, source_id: int, guild_id: int, plan: ChunkPlan) -> None:
    """Write a :func:`plan_chunk_sync` result. Reused rows keep their id, and with it their
    embedding and ``embedded`` flag; only their position and page metadata move."""
    if plan.reuse:
        rows = {
            row.id: row
            for row in (
                await session.scalars(
                    select(KBChunk).where(KBChunk.id.in_([r[0] for r in plan.reuse]))
                )
            ).all()
        }
        for chunk_id, ordinal, url, title in plan.reuse:
            row = rows.get(chunk_id)
            if row is None:  # deleted underneath us — the insert path covers the text
                continue
            row.ordinal = ordinal
            row.page_url = url
            row.heading_path = title

    for chunk_id in plan.delete:
        await delete_embedding(session, "kb_chunk_embedding", chunk_id)
        row = await session.get(KBChunk, chunk_id)
        if row is not None:
            await session.delete(row)

    for ordinal, rec in plan.insert:
        session.add(
            KBChunk(
                source_id=source_id,
                guild_id=guild_id,
                ordinal=ordinal,
                content=rec["text"],
                token_count=estimate_tokens(rec["text"]),
                page_url=rec.get("url"),
                heading_path=rec.get("title"),
                embedded=False,
            )
        )


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
        # Stamped in the same transaction as the claim, so the two are never observed apart.
        # olisar.knowledge.refresh relies on that to tell an abandoned claim from a live one.
        src.last_checked_at = utcnow()
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
        existing = [
            (row.id, row.content)
            for row in (
                await session.scalars(select(KBChunk).where(KBChunk.source_id == sid))
            ).all()
        ]

        if not records:
            # A read that came back empty is a failed read, not an emptied source. This used
            # to delete every passage first and report the error afterwards, so one temporary
            # 502 — or a page that briefly rendered nothing — erased everything Olisar had
            # learned from that source. Harmless while re-reading was a manual act; on a
            # schedule it is a wipe waiting for a bad afternoon. Keep what we have and say so.
            src.status = KBStatus.error
            src.error = (
                f"couldn’t read it this time — keeping the {len(existing)} passages from the "
                "last successful read"
                if existing
                else "no content could be extracted"
            )
            log.warning("ingest for source %s read nothing (%d kept)", sid, len(existing))
            return True

        plan = plan_chunk_sync(existing, records)
        await _apply_chunk_plan(session, sid, gid, plan)
        src.status = KBStatus.ready
        src.last_ingested_at = utcnow()
        src.error = None

    log.info(
        "ingested source %s: %d passages (%d new, %d unchanged, %d dropped)",
        sid, len(records), len(plan.insert), len(plan.reuse), len(plan.delete),
    )
    return True
