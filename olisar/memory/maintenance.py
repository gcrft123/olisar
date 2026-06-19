"""Background memory maintenance, called on a loop by the memory_worker cog.

Three passes, all off the reply path:
  1. embed_pending  — embed new messages / summaries / user-memory rows
  2. run_summaries  — summarize channels over their token threshold
  3. run_personas   — (re)build personas for users over their message threshold

Each unit of work runs in its own short transaction so one failure can't roll
back the others.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import (
    ChannelAllowlist,
    ChannelSummary,
    GuildConfig,
    KBChunk,
    Message,
    UserMemory,
    UserProfile,
)
from olisar.gemini.embeddings import embed_documents
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.memory.personas import maybe_build_user_persona
from olisar.memory.summarizer import maybe_summarize_channel
from olisar.memory.vectors import upsert_embedding

log = logging.getLogger("olisar.maintenance")

EMBED_BATCH = 50


def _text_of(row) -> str:
    # Message/UserMemory use `content`; ChannelSummary uses `summary`.
    return (getattr(row, "summary", None) or getattr(row, "content", "") or "")


async def _embed_table(session: AsyncSession, model_cls, table: str) -> int:
    rows = (
        await session.scalars(
            select(model_cls)
            .where(model_cls.embedded == False)  # noqa: E712
            .limit(EMBED_BATCH)
        )
    ).all()
    if not rows:
        return 0

    todo = []
    for r in rows:
        if _text_of(r).strip():
            todo.append(r)
        else:
            r.embedded = True  # nothing to embed; don't re-scan it
    if not todo:
        return 0

    vectors = await embed_documents([_text_of(r) for r in todo])
    for r, vec in zip(todo, vectors):
        await upsert_embedding(session, table, r.id, vec)
        r.embedded = True
    return len(todo)


async def embed_pending() -> int:
    """Embed any unembedded messages, summaries, and user-memory rows."""
    total = 0
    targets = [
        (Message, "message_embedding"),
        (ChannelSummary, "channel_summary_embedding"),
        (UserMemory, "user_memory_embedding"),
        (KBChunk, "kb_chunk_embedding"),
    ]
    for model_cls, table in targets:
        try:
            async with session_scope() as session:
                total += await _embed_table(session, model_cls, table)
        except RateLimitExceeded:
            log.info("embedding deferred: embed rate limit reached")
            break
        except Exception:
            log.exception("embedding pass failed for %s", table)
    return total


async def run_summaries() -> None:
    guild_id = settings.target_guild_id
    try:
        async with session_scope() as session:
            config = await session.get(GuildConfig, guild_id)
            threshold = config.summary_token_threshold if config else 4000
            channel_ids = (
                await session.scalars(
                    select(ChannelAllowlist.channel_id).where(
                        ChannelAllowlist.guild_id == guild_id,
                        ChannelAllowlist.unsummarized_tokens >= threshold,
                    )
                )
            ).all()
    except Exception:
        log.exception("failed to scan channels for summarization")
        return

    for channel_id in channel_ids:
        async with session_scope() as session:
            await maybe_summarize_channel(
                session, guild_id=guild_id, channel_id=channel_id, threshold=threshold
            )


async def run_personas() -> None:
    guild_id = settings.target_guild_id
    try:
        async with session_scope() as session:
            config = await session.get(GuildConfig, guild_id)
            threshold = config.user_persona_msg_threshold if config else 30
            user_ids = (
                await session.scalars(
                    select(UserProfile.user_id).where(
                        UserProfile.guild_id == guild_id,
                        UserProfile.memory_opt_out == False,  # noqa: E712
                        UserProfile.messages_since_persona >= threshold,
                    )
                )
            ).all()
    except Exception:
        log.exception("failed to scan users for persona synthesis")
        return

    for user_id in user_ids:
        async with session_scope() as session:
            await maybe_build_user_persona(
                session, guild_id=guild_id, user_id=user_id, threshold=threshold
            )
