"""Knowledge-base management endpoints (list / add URL or site / delete).

Doc *uploads* go through the Discord `/olisar learn-doc` command for now;
multipart upload from the dashboard lands with the frontend (Phase 7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select, update

from api.auth.deps import GuildContext, require_guild_admin
from api.schemas import SourceIn
from olisar.audit import record_audit
from olisar.db.engine import session_scope
from olisar.db.models import (
    GuildChannelInfo,
    KBChunk,
    KBSource,
    KBSourceType,
    KBStatus,
    Message,
    SearchMessage,
)
from olisar.memory.vectors import delete_embedding
from olisar.memory.writer import clear_search_index

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("")
async def list_sources(gctx: GuildContext = Depends(require_guild_admin)):
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(KBSource).where(KBSource.guild_id == gctx.guild_id).order_by(KBSource.id)
            )
        ).all()
        out = []
        for r in rows:
            chunk_count = len(
                (await session.scalars(select(KBChunk.id).where(KBChunk.source_id == r.id))).all()
            )
            out.append(
                {
                    "id": r.id,
                    "type": r.type.value,
                    "uri": r.uri,
                    "title": r.title,
                    "status": r.status.value,
                    "error": r.error,
                    "chunks": chunk_count,
                    "crawl_depth": r.crawl_depth,
                    "max_pages": r.max_pages,
                }
            )
    return out


@router.post("")
async def add_source(body: SourceIn, gctx: GuildContext = Depends(require_guild_admin)):
    if body.type not in ("url", "website"):
        raise HTTPException(status_code=400, detail="type must be 'url' or 'website'")
    if not body.uri.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="uri must be an http(s) URL")
    async with session_scope() as session:
        src = KBSource(
            guild_id=gctx.guild_id,
            type=KBSourceType(body.type),
            uri=body.uri,
            title=body.uri,
            status=KBStatus.pending,
            crawl_depth=max(0, min(body.crawl_depth, 3)),
            max_pages=max(1, min(body.max_pages, 100)),
            added_by=gctx.admin.discord_user_id,
        )
        session.add(src)
        await session.flush()
        source_id = src.id
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="add_kb_source",
            target_type="kb_source", target_id=source_id,
            after={"uri": body.uri, "type": body.type},
        )
    return {"id": source_id, "status": "pending"}


@router.post("/reindex")
async def reindex(gctx: GuildContext = Depends(require_guild_admin)):
    """Rebuild the server-wide message search index from each channel's history. Resets
    the backfill cursor on every channel; the background worker re-walks them. The index
    rows update in place (keyed by message id), so this is safe to run anytime."""
    async with session_scope() as session:
        # Only re-arm channels that are actually in the index. Channels set to
        # "not indexed" stay halted (re-enabling one re-arms it via reindex_channel).
        await session.execute(
            update(GuildChannelInfo)
            .where(
                GuildChannelInfo.guild_id == gctx.guild_id,
                GuildChannelInfo.index_enabled.is_(True),
            )
            .values(backfill_done=False, last_indexed_message_id=None)
        )
        # DMs are their own category — re-index them by clearing the DM search rows; the
        # DM backfill pass rebuilds the index from the message table (Discord can't page
        # DM history). Idempotent and safe to run from any server's Re-index button.
        await session.execute(delete(SearchMessage).where(SearchMessage.guild_id == 0))
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="reindex_search",
            target_type="guild", target_id=gctx.guild_id,
        )
    return {"ok": True}


@router.post("/reindex/clear")
async def clear_index(gctx: GuildContext = Depends(require_guild_admin)):
    """Wipe the server-wide message search index and halt backfill. New posts are still
    indexed live; ``/olisar reindex`` (or Re-index all) rebuilds history."""
    async with session_scope() as session:
        removed = await clear_search_index(session, gctx.guild_id)
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="clear_search_index",
            target_type="guild", target_id=gctx.guild_id, after={"removed": removed},
        )
    return {"ok": True, "removed": removed}


@router.get("/reindex/status")
async def reindex_status(gctx: GuildContext = Depends(require_guild_admin)):
    """Per-channel backfill progress for the message search index."""
    async with session_scope() as session:
        # Only channels in the index — ones set to "not indexed" aren't part of it.
        chans = (
            await session.scalars(
                select(GuildChannelInfo)
                .where(
                    GuildChannelInfo.guild_id == gctx.guild_id,
                    GuildChannelInfo.index_enabled.is_(True),
                )
                .order_by(GuildChannelInfo.position)
            )
        ).all()
        counts = dict(
            (
                await session.execute(
                    select(SearchMessage.channel_id, func.count())
                    .where(SearchMessage.guild_id == gctx.guild_id)
                    .group_by(SearchMessage.channel_id)
                )
            ).all()
        )
        # DMs (guild 0) are surfaced as one aggregate category, regardless of the current
        # server, since they're bot-wide.
        dm_indexed = int(
            await session.scalar(
                select(func.count()).select_from(SearchMessage).where(SearchMessage.guild_id == 0)
            )
            or 0
        )
        dm_msgs = int(
            await session.scalar(
                select(func.count()).select_from(Message).where(
                    Message.guild_id == 0, Message.author_is_bot == False  # noqa: E712
                )
            )
            or 0
        )
    channels = []
    done = indexing = queued = 0
    for c in chans:
        if c.backfill_done:
            status = "done"
            done += 1
        elif c.last_indexed_message_id is not None:
            status = "indexing"
            indexing += 1
        else:
            status = "queued"
            queued += 1
        channels.append({
            "channel_id": str(c.channel_id),
            "name": c.name or str(c.channel_id),
            "kind": c.kind,
            "status": status,
            "indexed": int(counts.get(c.channel_id, 0)),
        })
    # One aggregate "Direct messages" row for all DM channels (shown once there's DM
    # activity). Its index is rebuilt from the message table by the DM backfill pass.
    if dm_msgs > 0:
        dm_status = "done" if dm_indexed >= dm_msgs else "indexing"
        channels.append({
            "channel_id": "dm",
            "name": "Direct messages",
            "kind": "dm",
            "status": dm_status,
            "indexed": dm_indexed,
        })
        if dm_status == "done":
            done += 1
        else:
            indexing += 1
    return {
        "total": len(channels),
        "done": done,
        "indexing": indexing,
        "queued": queued,
        "running": (indexing + queued) > 0,
        "indexed_messages": int(sum(counts.values())) + dm_indexed,
        "channels": channels,
    }


@router.delete("/{source_id}")
async def delete_source(source_id: int, gctx: GuildContext = Depends(require_guild_admin)):
    async with session_scope() as session:
        src = await session.get(KBSource, source_id)
        if src is None or src.guild_id != gctx.guild_id:
            raise HTTPException(status_code=404, detail="source not found")
        chunk_ids = (
            await session.scalars(select(KBChunk.id).where(KBChunk.source_id == source_id))
        ).all()
        for cid in chunk_ids:
            await delete_embedding(session, "kb_chunk_embedding", cid)
        await session.delete(src)  # cascades to kb_chunk rows
        await record_audit(
            session, actor=gctx.admin.discord_user_id, action="delete_kb_source",
            target_type="kb_source", target_id=source_id,
        )
    return {"ok": True, "removed_chunks": len(chunk_ids)}
