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

from sqlalchemy import func, select, update
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
    SearchMessage,
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
# Bounds for the operator-triggered (manual) glossary mines, so one console click stays
# a snappy, finite pass rather than churning the whole history at once.
GLOSSARY_MANUAL_MEMORY_CAP = 400   # un-mined conversational messages per "mine from memory"
GLOSSARY_MANUAL_INDEX_CAP = 600    # indexed messages sampled per "deep mine from index"


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


def _memory_guilds() -> list[int]:
    """Guilds the background passes cover: the configured target guild, plus DMs (guild 0),
    which are stored as their own channels. Deduped, target first."""
    return list(dict.fromkeys([settings.target_guild_id, 0]))


async def run_summaries() -> None:
    for guild_id in _memory_guilds():
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
            log.exception("failed to scan channels for summarization (guild %s)", guild_id)
            continue

        for channel_id in channel_ids:
            async with session_scope() as session:
                await maybe_summarize_channel(
                    session, guild_id=guild_id, channel_id=channel_id, threshold=threshold
                )


async def run_glossary() -> None:
    """Mine un-mined messages in memory/both channels for durable guild facts once a
    channel has accumulated enough un-mined text. Independent of summarization and on
    a much lower threshold, so the glossary grows actively."""
    targets: list[tuple[int, int, int]] = []  # (guild_id, channel_id, threshold)
    for guild_id in _memory_guilds():
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
            targets.extend((guild_id, cid, threshold) for cid in channel_ids)
        except Exception:
            log.exception("failed to scan channels for glossary mining (guild %s)", guild_id)

    for guild_id, channel_id, threshold in targets:
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


async def mine_glossary_now(guild_id: int) -> dict:
    """Operator-triggered glossary mine over un-mined conversational memory (the
    ``message`` table) for one guild — the same extraction the background worker runs,
    but on demand and threshold-free. Bounded to ``GLOSSARY_MANUAL_MEMORY_CAP`` messages
    per call; returns how many facts were added, how many messages were mined, and how
    many un-mined remain (so the console can invite another pass)."""
    # 1) Read the batch in one short transaction. Only memory/both channels — the same
    #    scope the auto pass respects, so channels set to "don't remember" stay excluded.
    async with session_scope() as session:
        channel_ids = (
            await session.scalars(
                select(ChannelAllowlist.channel_id).where(
                    ChannelAllowlist.guild_id == guild_id,
                    ChannelAllowlist.mode.in_([ChannelMode.memory, ChannelMode.both]),
                )
            )
        ).all()
        if not channel_ids:
            return {"ok": True, "added": 0, "mined": 0, "remaining": 0}
        rows = [
            m
            for m in (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.channel_id.in_(channel_ids),
                        Message.fact_mined == False,  # noqa: E712
                        Message.author_is_bot == False,  # noqa: E712
                    )
                    .order_by(Message.created_at.asc())
                    .limit(GLOSSARY_MANUAL_MEMORY_CAP)
                )
            ).all()
            if (m.content or "").strip()
        ]
        if not rows:
            return {"ok": True, "added": 0, "mined": 0, "remaining": 0}
        names = await name_map(session, {m.author_id for m in rows})
        items = [(m.id, m.author_id, m.content) for m in rows]

    # 2) Mine in batches, each its own short transaction (model call + writes) — mirrors
    #    the per-channel auto pass so no write lock is held across many network calls.
    added_total = mined_total = 0
    for i in range(0, len(items), GLOSSARY_MINE_BATCH):
        batch = items[i : i + GLOSSARY_MINE_BATCH]
        transcript = "\n".join(
            f"{names.get(aid, str(aid))}: {content}" for (_id, aid, content) in batch
        )
        async with session_scope() as session:
            added_total += await extract_and_store_facts(
                session, guild_id=guild_id, channel_id=None, transcript=transcript
            )
            await session.execute(
                update(Message)
                .where(Message.id.in_([mid for (mid, _a, _c) in batch]))
                .values(fact_mined=True)
            )
        mined_total += len(batch)

    # 3) Report how much un-mined memory is left.
    async with session_scope() as session:
        remaining = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.channel_id.in_(channel_ids),
                Message.fact_mined == False,  # noqa: E712
                Message.author_is_bot == False,  # noqa: E712
            )
        )
    return {
        "ok": True,
        "added": added_total,
        "mined": mined_total,
        "remaining": int(remaining or 0),
    }


async def deep_mine_glossary_now(guild_id: int) -> dict:
    """Operator-triggered DEEP glossary mine over the full message search index (the
    ``search_message`` table, which spans EVERY channel — including ones excluded from
    conversational memory). Samples the most recent ``GLOSSARY_MANUAL_INDEX_CAP`` messages
    and mines them in batches. Nothing is flagged (the index has no mined marker), and
    re-running is safe: upsert dedups and merely reinforces facts already known."""
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(SearchMessage)
                .where(SearchMessage.guild_id == guild_id)
                .order_by(SearchMessage.created_at.desc())
                .limit(GLOSSARY_MANUAL_INDEX_CAP)
            )
        ).all()
        items = [
            (s.author_name or str(s.author_id), s.content)
            for s in rows
            if (s.content or "").strip()
        ]
    if not items:
        return {"ok": True, "added": 0, "sampled": 0}
    items.reverse()  # oldest-first, so the model reads the sample chronologically

    added_total = 0
    for i in range(0, len(items), GLOSSARY_MINE_BATCH):
        batch = items[i : i + GLOSSARY_MINE_BATCH]
        transcript = "\n".join(f"{name}: {content}" for (name, content) in batch)
        async with session_scope() as session:
            added_total += await extract_and_store_facts(
                session, guild_id=guild_id, channel_id=None, transcript=transcript
            )
    return {"ok": True, "added": added_total, "sampled": len(items)}


async def run_personas() -> None:
    targets: list[tuple[int, int, int]] = []  # (guild_id, user_id, threshold)
    for guild_id in _memory_guilds():
        try:
            async with session_scope() as session:
                config = await session.get(GuildConfig, guild_id)
                threshold = config.user_persona_msg_threshold if config else 15
                user_ids = (
                    await session.scalars(
                        select(UserProfile.user_id).where(
                            UserProfile.guild_id == guild_id,
                            UserProfile.memory_opt_out == False,  # noqa: E712
                            UserProfile.dm_opt_out == False,  # noqa: E712
                            UserProfile.messages_since_persona >= threshold,
                        )
                    )
                ).all()
            targets.extend((guild_id, uid, threshold) for uid in user_ids)
        except Exception:
            log.exception("failed to scan users for persona synthesis (guild %s)", guild_id)

    for guild_id, user_id, threshold in targets:
        async with session_scope() as session:
            await maybe_build_user_persona(
                session, guild_id=guild_id, user_id=user_id, threshold=threshold
            )
