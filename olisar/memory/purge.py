"""Right-to-be-forgotten: purge everything Olisar stores about a user, plus the
``/self-destruct`` full brain-wipe."""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.engine import after_session_scope
from olisar.db.models import (
    ChannelContextItem,
    ChannelSummary,
    FailureReport,
    Guild,
    GuildChannelInfo,
    GuildFact,
    KBChunk,
    KBSource,
    MemoryOptOut,
    Message,
    ProactivityState,
    Reminder,
    SearchMessage,
    UserMemory,
    UserProfile,
)
from olisar.memory.vectors import delete_embedding

log = logging.getLogger("olisar.purge")

# Rows deleted per transaction by the purges below. SQLite has one writer, and a purge that
# ran as one transaction held the write lock for all of it (40 s for a member with 33k
# messages, longer for a whole server), so every message the bot tried to store meanwhile
# waited out the 5 s busy timeout and was dropped. Between batches the lock is free.
PURGE_BATCH = 500
# Free isn't enough if the next batch takes the lock straight back: a writer waiting on it
# retries only every 100 ms (SQLite's busy handler), so it rarely lands in the moment between
# two batches and can still time out. After this much back-to-back work the purge steps
# aside for longer than that, and whoever is waiting gets in.
_WORK_BEFORE_YIELD = 0.5
_YIELD = 0.15

# Every table holding data that belongs to one member, as (model, user_column). Defined
# once because two features read it from opposite ends: ``forget_user`` deletes it, and the
# member portal's export shows it. If they drift, one of them is lying — an export missing
# a table understates what Olisar holds, and a purge missing one breaks the promise the
# export just made. Each is additionally scoped by ``guild_id``.
MEMBER_DATA_TABLES: tuple[tuple[type, str], ...] = (
    (Message, "author_id"),
    (SearchMessage, "author_id"),
    (UserMemory, "user_id"),
    (Reminder, "user_id"),
    (FailureReport, "user_id"),
)


async def active_memory_guild_ids(session: AsyncSession) -> list[int]:
    """The guild scopes the bot maintains and forgets across: every guild it is currently in
    (``Guild.active``), plus the DM sentinel 0 (DMs are stored as their own channels). Deduped
    with the DM bucket last. This is what makes "same bot, many servers" complete — background
    upkeep and right-to-be-forgotten cover all active guilds, not just the home guild."""
    ids = (await session.scalars(select(Guild.id).where(Guild.active.is_(True)))).all()
    return list(dict.fromkeys([*ids, 0]))

# vec0 virtual tables holding embeddings for the wiped "brain" tables — including
# the knowledge base's kb_chunk_embedding (a self-destruct wipes the KB too).
# The vectors behind what a brain-wipe deletes, and the rows they belong to.
_BRAIN_EMBEDDINGS = (
    ("message_embedding", Message),
    ("channel_summary_embedding", ChannelSummary),
    ("user_memory_embedding", UserMemory),
    ("kb_chunk_embedding", KBChunk),
)


async def _purge(session: AsyncSession, model, *where, vectors: str | None = None) -> int:
    """Delete ``model``'s rows matching ``where``, ``PURGE_BATCH`` at a time, each batch
    together with its vectors (``vectors`` names the vec0 table) and committed on its own.
    A purge cut off partway leaves every row either gone with its vector or still there
    with it, and running it again finishes the job. Returns how many rows went."""
    total = 0
    busy_since = time.monotonic()
    while True:
        ids = list(await session.scalars(select(model.id).where(*where).limit(PURGE_BATCH)))
        if not ids:
            return total
        if vectors:
            await delete_embedding(session, vectors, *ids)
        await session.execute(delete(model).where(model.id.in_(ids)))
        await session.commit()
        total += len(ids)
        if time.monotonic() - busy_since >= _WORK_BEFORE_YIELD:
            await asyncio.sleep(_YIELD)
            busy_since = time.monotonic()


def _truncate_wal_afterwards(session: AsyncSession) -> None:
    """Once the caller's transaction is over, checkpoint the WAL and cut the file back to
    nothing: a big purge writes every page it touches to the WAL first. The checkpoint
    holds off new writers while it waits for readers, so it waits at most a second (then
    checkpoints what it can), well inside the 5 s a message's write waits for the lock."""
    bind = session.bind

    async def truncate() -> None:
        async with bind.connect() as conn:
            await conn.exec_driver_sql("PRAGMA busy_timeout=1000")
            try:
                await conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                await conn.exec_driver_sql("PRAGMA busy_timeout=5000")

    after_session_scope(truncate)


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
    # Messages (+ their vectors). The bulky parts go in committed batches (see _purge).
    messages = await _purge(
        session, Message,
        Message.guild_id.in_(guild_ids), Message.author_id == user_id,
        vectors="message_embedding",
    )

    # Server-wide search index (the FTS AFTER DELETE trigger drops their terms too).
    indexed = await _purge(
        session, SearchMessage,
        SearchMessage.guild_id.in_(guild_ids), SearchMessage.author_id == user_id,
    )

    # Remembered facts (+ their vectors).
    facts = await _purge(
        session, UserMemory,
        UserMemory.guild_id.in_(guild_ids), UserMemory.user_id == user_id,
        vectors="user_memory_embedding",
    )

    # Pending reminders. These are user-authored content keyed to the user, so "delete
    # everything you've stored about me" has to include them — leaving them behind meant a
    # forgotten user could still be DMed months later by a reminder they'd asked to erase.
    # Fired ones go too: the row is the record of what they asked for either way.
    reminder_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Reminder)
            .where(Reminder.guild_id.in_(guild_ids), Reminder.user_id == user_id)
        )
        or 0
    )
    await session.execute(
        delete(Reminder).where(
            Reminder.guild_id.in_(guild_ids), Reminder.user_id == user_id
        )
    )

    # Parked blank-reply reports. The row holds the prompt they typed, so it goes with
    # everything else they wrote — and the log snapshot attached to it goes with it, which
    # is the only route by which that snapshot is ever deleted early.
    await session.execute(
        delete(FailureReport).where(
            FailureReport.guild_id.in_(guild_ids), FailureReport.user_id == user_id
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
        # "Stop recording me from now on" covers everywhere: every profile they have, in
        # any server, and (MemoryOptOut) any server or DM Olisar first sees them in later.
        # Flagging only the profiles in guild_ids let their next DM, or a server the bot
        # joined afterwards, start a fresh profile that recorded them again.
        await session.execute(
            update(UserProfile).where(UserProfile.user_id == user_id).values(memory_opt_out=True)
        )
        if await session.get(MemoryOptOut, user_id) is None:
            session.add(MemoryOptOut(user_id=user_id))

    if messages + indexed + facts >= PURGE_BATCH:
        _truncate_wal_afterwards(session)
    log.info(
        "forgot user %s: %d messages, %d facts, %d reminders, opt_out=%s",
        user_id, messages, facts, reminder_count, opt_out,
    )
    return {
        "messages": messages,
        "facts": facts,
        "reminders": reminder_count,
        "opted_out": opt_out,
    }


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

    Only ``guild_ids`` are touched. Usage stats belong to the whole install (the daily
    web-search cap is counted from them), so they stay, as does every other server's data,
    vectors included.
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

    # Halt the search backfill so it doesn't re-index history back into the index being
    # cleared (it could between the batches below, and would right after) — keep the
    # channel roster (names) for the dashboard.
    await session.execute(
        update(GuildChannelInfo)
        .where(GuildChannelInfo.guild_id.in_(guild_ids))
        .values(backfill_done=True, last_indexed_message_id=None)
    )
    await session.commit()

    # Conversation memory, summaries, search index, facts, glossary, snapshots,
    # proactivity runtime state, and the knowledge base (chunks before sources so
    # the wipe doesn't depend on FK cascade being enabled). Deleting search_message
    # fires the FTS AFTER DELETE triggers, clearing the keyword index. Each goes in
    # committed batches, rows together with their vectors (see _purge).
    vectors = {model: table for table, model in _BRAIN_EMBEDDINGS}
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
        await _purge(session, model, model.guild_id.in_(guild_ids), vectors=vectors.get(model))

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

    _truncate_wal_afterwards(session)
    log.warning("brain-wipe for guilds %s: %s", guild_ids, counts)
    return counts
