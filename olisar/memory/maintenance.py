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
from olisar.context import name_map
from olisar.db.engine import session_scope
from olisar.db.models import (
    ChannelAllowlist,
    ChannelMode,
    ChannelSummary,
    GuildConfig,
    KBChunk,
    Message,
    UserMemory,
    UserProfile,
)
from olisar.gemini.embeddings import embed_documents
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.memory.facts import extract_and_store_facts
from olisar.memory.personas import maybe_build_user_persona
from olisar.memory.summarizer import maybe_summarize_channel
from olisar.memory.vectors import upsert_embedding
from olisar.memory.writer import estimate_tokens

log = logging.getLogger("olisar.maintenance")

EMBED_BATCH = 50
GLOSSARY_MINE_MIN_MESSAGES = 6
GLOSSARY_MINE_BATCH = 200


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


async def run_glossary() -> None:
    """Mine un-mined messages in memory/both channels for durable guild facts once a
    channel has accumulated enough un-mined text. Independent of summarization and on
    a much lower threshold, so the glossary grows actively."""
    guild_id = settings.target_guild_id
    try:
        async with session_scope() as session:
            config = await session.get(GuildConfig, guild_id)
            threshold = config.glossary_mine_token_threshold if config else 1500
            channel_ids = (
                await session.scalars(
                    select(ChannelAllowlist.channel_id).where(
                        ChannelAllowlist.guild_id == guild_id,
                        ChannelAllowlist.mode.in_([ChannelMode.memory, ChannelMode.both]),
                    )
                )
            ).all()
    except Exception:
        log.exception("failed to scan channels for glossary mining")
        return

    for channel_id in channel_ids:
        try:
            async with session_scope() as session:
                msgs = [
                    m
                    for m in (
                        await session.scalars(
                            select(Message)
                            .where(
                                Message.channel_id == channel_id,
                                Message.fact_mined == False,  # noqa: E712
                                Message.author_is_bot == False,  # noqa: E712
                            )
                            .order_by(Message.created_at.asc())
                            .limit(GLOSSARY_MINE_BATCH)
                        )
                    ).all()
                    if (m.content or "").strip()
                ]
                if len(msgs) < GLOSSARY_MINE_MIN_MESSAGES:
                    continue
                if sum(estimate_tokens(m.content) for m in msgs) < threshold:
                    continue
                names = await name_map(session, {m.author_id for m in msgs})
                transcript = "\n".join(
                    f"{names.get(m.author_id, str(m.author_id))}: {m.content}" for m in msgs
                )
                await extract_and_store_facts(
                    session, guild_id=guild_id, channel_id=channel_id, transcript=transcript
                )
                for m in msgs:
                    m.fact_mined = True
        except Exception:
            log.exception("glossary mining failed for channel %s", channel_id)


async def run_personas() -> None:
    guild_id = settings.target_guild_id
    try:
        async with session_scope() as session:
            config = await session.get(GuildConfig, guild_id)
            threshold = config.user_persona_msg_threshold if config else 15
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
