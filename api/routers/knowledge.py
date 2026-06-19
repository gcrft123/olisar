"""Knowledge-base management endpoints (list / add URL or site / delete).

Doc *uploads* go through the Discord `/olisar learn-doc` command for now;
multipart upload from the dashboard lands with the frontend (Phase 7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from api.auth.deps import GuildContext, require_guild_admin
from api.schemas import SourceIn
from olisar.audit import record_audit
from olisar.db.engine import session_scope
from olisar.db.models import KBChunk, KBSource, KBSourceType, KBStatus
from olisar.memory.vectors import delete_embedding

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
