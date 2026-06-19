"""Persist observed messages into conversation memory.

Kept deliberately small: the conversation cog calls ``record_message`` for every
message in a channel that's set to ``memory``/``both``. Embedding happens later
on a background queue (Phase 2), so this never blocks a reply.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import (
    ChannelAllowlist,
    ChannelMode,
    GuildChannelInfo,
    Message,
    SearchMessage,
    UserProfile,
    snowflake_time,
    utcnow,
)


async def _indexing_disabled(session: AsyncSession, channel_id: int) -> bool:
    """Whether a channel is excluded from the all-channel search index. Threads
    inherit their parent channel's setting (matching mode inheritance)."""
    ci = await session.get(GuildChannelInfo, channel_id)
    if ci is None:
        return False
    if ci.kind == "thread" and ci.parent_id:
        parent = await session.get(GuildChannelInfo, ci.parent_id)
        return parent is not None and not parent.index_enabled
    return not ci.index_enabled


def _channel_and_threads(guild_id: int, channel_id: int):
    """Select of the channel's id plus its thread children's ids (a forum/text
    channel and everything nested under it)."""
    return select(GuildChannelInfo.channel_id).where(
        GuildChannelInfo.guild_id == guild_id,
        (GuildChannelInfo.channel_id == channel_id)
        | (GuildChannelInfo.parent_id == channel_id),
    )


async def delete_channel_index(session: AsyncSession, guild_id: int, channel_id: int) -> int:
    """Drop a channel's (and its threads') rows from the search index, and halt the
    backfill for them. Returns the number of indexed messages removed. The FTS AFTER
    DELETE trigger drops the corresponding search terms automatically."""
    ids = list((await session.scalars(_channel_and_threads(guild_id, channel_id))).all())
    if channel_id not in ids:
        ids.append(channel_id)
    removed = await session.scalar(
        select(func.count())
        .select_from(SearchMessage)
        .where(SearchMessage.guild_id == guild_id, SearchMessage.channel_id.in_(ids))
    ) or 0
    await session.execute(
        delete(SearchMessage).where(
            SearchMessage.guild_id == guild_id, SearchMessage.channel_id.in_(ids)
        )
    )
    await session.execute(
        update(GuildChannelInfo)
        .where(GuildChannelInfo.channel_id.in_(ids))
        .values(backfill_done=True, last_indexed_message_id=None)
    )
    return removed


async def reindex_channel(session: AsyncSession, guild_id: int, channel_id: int) -> None:
    """Re-arm the backfill for a channel (and its threads) so its history gets
    re-indexed — used when indexing is turned back on."""
    ids = list((await session.scalars(_channel_and_threads(guild_id, channel_id))).all())
    if not ids:
        ids = [channel_id]
    await session.execute(
        update(GuildChannelInfo)
        .where(GuildChannelInfo.channel_id.in_(ids))
        .values(backfill_done=False, last_indexed_message_id=None)
    )


def extract_roles(member: object) -> list[dict]:
    """Turn a discord.Member's roles into JSON-safe records (skipping @everyone).

    Role ids are stored as strings so the dashboard's JSON doesn't lose precision
    on 64-bit snowflakes. Takes ``object`` to avoid importing discord into core.
    """
    roles = getattr(member, "roles", None) or []
    out: list[dict] = []
    for role in roles:
        if getattr(role, "is_default", lambda: False)():
            continue  # skip @everyone
        out.append({"id": str(role.id), "name": role.name})
    return out


def estimate_tokens(text: str) -> int:
    """Cheap, model-agnostic token estimate (~4 chars/token). Good enough for
    budgeting and the summary-threshold counter without a real tokenizer."""
    return max(1, len(text) // 4)


async def get_channel_mode(
    session: AsyncSession, guild_id: int, channel_id: int
) -> ChannelMode:
    """A channel's mode, or ``off`` if it isn't on the allowlist yet."""
    row = await session.scalar(
        select(ChannelAllowlist).where(
            ChannelAllowlist.guild_id == guild_id,
            ChannelAllowlist.channel_id == channel_id,
        )
    )
    return row.mode if row else ChannelMode.off


async def upsert_profile(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    display_name: str,
    roles: list[dict] | None = None,
) -> UserProfile:
    """Insert or refresh a member's profile (display name + roles) and return it."""
    profile = await session.scalar(
        select(UserProfile).where(
            UserProfile.user_id == user_id, UserProfile.guild_id == guild_id
        )
    )
    if profile is None:
        profile = UserProfile(
            user_id=user_id,
            guild_id=guild_id,
            display_name=display_name,
            roles=roles or [],
        )
        session.add(profile)
        return profile
    profile.display_name = display_name or profile.display_name
    if roles is not None:
        profile.roles = roles
    profile.last_seen = utcnow()
    return profile


async def record_message(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    message_id: int,
    author_id: int,
    author_is_bot: bool,
    content: str,
    reply_to: int | None = None,
    display_name: str = "",
    roles: list[dict] | None = None,
) -> Message | None:
    """Store a message and advance the channel's unsummarized-token counter.

    Returns the new ``Message`` (so the caller can enqueue it for embedding), or
    ``None`` if it was skipped (duplicate, or the author opted out).
    """
    # Olisar's own replies are stored for context but don't get a user profile
    # or count toward persona regeneration.
    if author_is_bot:
        profile = None
    else:
        profile = await upsert_profile(session, guild_id, author_id, display_name, roles)
        if profile.memory_opt_out:
            return None  # respect opt-out: don't store their content

    # Guard against re-delivery (gateway reconnects can replay events).
    exists = await session.scalar(
        select(Message.id).where(Message.message_id == message_id)
    )
    if exists is not None:
        return None

    msg = Message(
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        author_id=author_id,
        author_is_bot=author_is_bot,
        content=content,
        reply_to_message_id=reply_to,
    )
    session.add(msg)

    # Count toward this user's next persona regeneration (Phase 2 acts on it).
    if profile is not None:
        profile.messages_since_persona += 1

    await session.execute(
        update(ChannelAllowlist)
        .where(
            ChannelAllowlist.guild_id == guild_id,
            ChannelAllowlist.channel_id == channel_id,
        )
        .values(
            unsummarized_tokens=ChannelAllowlist.unsummarized_tokens
            + estimate_tokens(content)
        )
    )
    return msg


async def record_search_message(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    channel_name: str,
    message_id: int,
    author_id: int,
    author_name: str,
    content: str,
) -> bool:
    """Index one message into the server-wide search corpus (``search_message``).

    Captures messages from EVERY channel (the admin opted into all-channel search),
    deliberately separate from conversational memory. Skips empty content, opt-out
    users (right-to-be-forgotten), and duplicates. Returns True if a row was added.
    """
    content = (content or "").strip()
    if not content:
        return False
    # Respect a per-channel indexing opt-out (threads inherit their parent's setting).
    if await _indexing_disabled(session, channel_id):
        return False
    # Respect opt-out (same gate as conversational recording).
    opted_out = await session.scalar(
        select(UserProfile.memory_opt_out).where(
            UserProfile.user_id == author_id, UserProfile.guild_id == guild_id
        )
    )
    if opted_out:
        return False
    # Dedup on the Discord id (gateway re-delivery, or backfill overlapping live).
    exists = await session.scalar(
        select(SearchMessage.id).where(SearchMessage.message_id == message_id)
    )
    if exists is not None:
        return False
    session.add(
        SearchMessage(
            guild_id=guild_id,
            channel_id=channel_id,
            channel_name=(channel_name or "")[:128],
            message_id=message_id,
            author_id=author_id,
            author_name=(author_name or "")[:128],
            content=content,
            # Real post time from the snowflake — correct even for old history
            # backfilled today (the column default would otherwise be 'now').
            created_at=snowflake_time(message_id),
        )
    )
    return True
