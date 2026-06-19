"""Right-to-be-forgotten: purge everything Olisar stores about a user, plus the
``/self-destruct`` full brain-wipe."""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import (
    ChannelContextItem,
    ChannelSummary,
    GeminiUsage,
    GuildChannelInfo,
    GuildFact,
    KBChunk,
    KBSource,
    Message,
    ProactivityState,
    SearchMessage,
    UserMemory,
    UserProfile,
)
from olisar.memory.vectors import delete_embedding

log = logging.getLogger("olisar.purge")

# vec0 virtual tables holding embeddings for the wiped "brain" tables — including
# the knowledge base's kb_chunk_embedding (a self-destruct wipes the KB too).
_BRAIN_EMBEDDING_TABLES = (
    "message_embedding",
    "channel_summary_embedding",
    "user_memory_embedding",
    "kb_chunk_embedding",
)


async def forget_user(
    session: AsyncSession,
    *,
    guild_ids: list[int],
    user_id: int,
    opt_out: bool = False,
) -> dict:
    """Delete a user's messages, remembered facts, and persona across the given
    guild scopes (pass the home guild + the DM sentinel 0). Optionally opt them
    out of future recording. Returns counts for the confirmation message."""
    # Messages (+ their vectors).
    msg_ids = (
        await session.scalars(
            select(Message.id).where(
                Message.guild_id.in_(guild_ids), Message.author_id == user_id
            )
        )
    ).all()
    for mid in msg_ids:
        await delete_embedding(session, "message_embedding", mid)
    await session.execute(
        delete(Message).where(
            Message.guild_id.in_(guild_ids), Message.author_id == user_id
        )
    )

    # Server-wide search index (the FTS AFTER DELETE trigger drops their terms too).
    await session.execute(
        delete(SearchMessage).where(
            SearchMessage.guild_id.in_(guild_ids), SearchMessage.author_id == user_id
        )
    )

    # Remembered facts (+ their vectors).
    fact_ids = (
        await session.scalars(
            select(UserMemory.id).where(
                UserMemory.guild_id.in_(guild_ids), UserMemory.user_id == user_id
            )
        )
    ).all()
    for fid in fact_ids:
        await delete_embedding(session, "user_memory_embedding", fid)
    await session.execute(
        delete(UserMemory).where(
            UserMemory.guild_id.in_(guild_ids), UserMemory.user_id == user_id
        )
    )

    # Clear the synthesized persona on every matching profile; optionally opt out.
    profiles = (
        await session.scalars(
            select(UserProfile).where(
                UserProfile.guild_id.in_(guild_ids), UserProfile.user_id == user_id
            )
        )
    ).all()
    for profile in profiles:
        profile.persona_summary = ""
        profile.persona_updated_at = None
        profile.messages_since_persona = 0
        if opt_out:
            profile.memory_opt_out = True

    log.info(
        "forgot user %s: %d messages, %d facts, opt_out=%s",
        user_id, len(msg_ids), len(fact_ids), opt_out,
    )
    return {"messages": len(msg_ids), "facts": len(fact_ids), "opted_out": opt_out}


async def _count(session: AsyncSession, model, guild_ids: list[int]) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(model).where(model.guild_id.in_(guild_ids))
        )
        or 0
    )


async def wipe_brain(session: AsyncSession, *, guild_ids: list[int]) -> dict:
    """The ``/self-destruct`` brain-wipe: erase everything Olisar has *learned* —
    conversation memory, channel summaries, the server-wide search index,
    remembered facts, the guild glossary, resource/feed snapshots, usage stats, the
    admin-curated knowledge base, and its synthesized read on each person — along
    with the vectors behind them.

    Deliberately KEEPS Olisar's "personality": persona, behaviour/command-reply
    config, proactivity config, channel roles (the allowlist), and dashboard auth.
    Per-user ``memory_opt_out`` is preserved so a wipe never silently re-enrolls
    someone who opted out. Returns counts for the confirmation message.

    Single-guild assumption: the vec0 embedding tables and global usage rows are
    cleared wholesale (they have no guild_id to filter on).
    """
    counts = {
        "messages": await _count(session, Message, guild_ids),
        "summaries": await _count(session, ChannelSummary, guild_ids),
        "facts": await _count(session, UserMemory, guild_ids),
        "glossary": await _count(session, GuildFact, guild_ids),
        "indexed": await _count(session, SearchMessage, guild_ids),
        "snapshots": await _count(session, ChannelContextItem, guild_ids),
        "knowledge": await _count(session, KBSource, guild_ids),
    }

    # Conversation memory, summaries, search index, facts, glossary, snapshots,
    # proactivity runtime state, and the knowledge base (chunks before sources so
    # the wipe doesn't depend on FK cascade being enabled). Deleting search_message
    # fires the FTS AFTER DELETE triggers, clearing the keyword index.
    for model in (
        Message,
        ChannelSummary,
        UserMemory,
        GuildFact,
        SearchMessage,
        ChannelContextItem,
        ProactivityState,
        KBChunk,
        KBSource,
    ):
        await session.execute(delete(model).where(model.guild_id.in_(guild_ids)))

    # Usage stats are global (no guild_id).
    await session.execute(delete(GeminiUsage))

    # Forget people, but keep opt-out promises: drop non-opted-out profiles
    # entirely (they re-register on next activity), and blank the learned fields
    # on opted-out ones while keeping the row + its opt-out flag.
    counts["profiles"] = await _count(session, UserProfile, guild_ids)
    await session.execute(
        delete(UserProfile).where(
            UserProfile.guild_id.in_(guild_ids),
            UserProfile.memory_opt_out == False,  # noqa: E712
        )
    )
    kept = (
        await session.scalars(
            select(UserProfile).where(UserProfile.guild_id.in_(guild_ids))
        )
    ).all()
    for profile in kept:
        profile.persona_summary = ""
        profile.persona_updated_at = None
        profile.messages_since_persona = 0
        profile.notes = {}

    # Halt the search backfill so it doesn't immediately re-index history back into
    # the index we just cleared — keep the channel roster (names) for the dashboard.
    await session.execute(
        update(GuildChannelInfo)
        .where(GuildChannelInfo.guild_id.in_(guild_ids))
        .values(backfill_done=True, last_indexed_message_id=None)
    )

    # Drop the embeddings behind the wiped tables (KB embeddings are left intact).
    for tbl in _BRAIN_EMBEDDING_TABLES:
        await session.execute(text(f"DELETE FROM {tbl}"))

    log.warning("brain-wipe for guilds %s: %s", guild_ids, counts)
    return counts
