"""What a replay may read, and who it may name.

The replay reads the search index, so the Channels tab's indexed setting decides which
channels it sees (a thread follows its parent). Within those it leaves out:

* DMs. They're stored under guild 0, which a server's scope never includes.
* Bots, Olisar included. Olisar's own replies come from the ``message`` table instead,
  for the chapter about its arrival.
* Anyone who opted out of search or memory in this server, paused it, or asked Olisar to
  stop remembering them anywhere. Their messages aren't counted or quoted, as if they
  weren't in the index (for anyone who opted out after being indexed, they still are).

People who have left the server are counted but never quoted or named: they show as
"Someone" with a blank face.

The live roster (who's still here, who's a bot, names and avatars, Olisar's own join date)
only exists in the bot process, so it comes in as a ``Roster`` (bot/cogs/replay.py builds
one from discord.py; tests pass their own).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import GuildChannelInfo, MemoryOptOut, SearchMessage, UserProfile


@dataclass
class Person:
    name: str
    avatar: str | None = None


class Roster(Protocol):
    """The server as Discord has it now."""

    guild_name: str
    guild_icon: str
    me_id: int
    me_joined_at: datetime | None

    def present(self, user_id: int) -> bool: ...
    def bot_ids(self) -> set[int]: ...
    def person(self, user_id: int) -> Person | None: ...
    def join_dates(self) -> list[tuple[int, datetime]]: ...
    def humans(self) -> int: ...
    def names(self) -> set[str]: ...
    def public(self, channel_id: int) -> bool | None: ...


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands datetimes back without a zone; every one stored here is UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def hidden_users(session: AsyncSession, guild_id: int, now: datetime) -> set[int]:
    """Everyone a replay of this server must leave out: opted out of search or memory, or
    paused, here; or opted out of memory anywhere."""
    rows = (
        await session.execute(
            select(
                UserProfile.user_id, UserProfile.guild_id, UserProfile.memory_opt_out,
                UserProfile.search_opt_out, UserProfile.pause_until,
            ).where(
                or_(
                    UserProfile.memory_opt_out.is_(True),
                    (UserProfile.guild_id == guild_id)
                    & (UserProfile.search_opt_out.is_(True) | UserProfile.pause_until.is_not(None)),
                )
            )
        )
    ).all()
    hidden: set[int] = set()
    for uid, gid, memory_out, search_out, pause in rows:
        paused = pause is not None and aware(pause) > now
        if memory_out or (gid == guild_id and (search_out or paused)):
            hidden.add(int(uid))
    hidden.update(int(u) for u in (await session.scalars(select(MemoryOptOut.user_id))).all())
    return hidden


@dataclass
class Scope:
    guild_id: int
    # Every indexed channel (threads under their own names) and the category each sits in.
    channels: dict[int, str]
    skip: set[int] = field(default_factory=set)   # authors left out: hidden people and bots
    hidden: set[int] = field(default_factory=set)

    def where(self):
        """The filter every query over ``search_message`` starts from."""
        return (
            SearchMessage.guild_id == self.guild_id,
            SearchMessage.channel_id.in_(list(self.channels)),
            SearchMessage.author_id.not_in(list(self.skip)),
        )

    def name(self, channel_id: int) -> str:
        return self.channels.get(int(channel_id), "")


async def indexed_channels(session: AsyncSession, guild_id: int) -> dict[int, str]:
    """Channel id → name for every channel in the search index's scope: text and forum
    channels with indexing on, and threads whose parent has it on."""
    rows = (
        await session.execute(
            select(
                GuildChannelInfo.channel_id, GuildChannelInfo.name, GuildChannelInfo.index_enabled,
                GuildChannelInfo.kind, GuildChannelInfo.parent_id,
            ).where(GuildChannelInfo.guild_id == guild_id)
        )
    ).all()
    enabled = {int(cid): bool(on) for cid, _, on, kind, _ in rows if kind != "thread"}
    out: dict[int, str] = {}
    for cid, name, on, kind, parent in rows:
        if kind == "thread":
            if parent is not None and enabled.get(int(parent), False):
                out[int(cid)] = name or ""
        elif on:
            out[int(cid)] = name or ""
    return out


async def build_scope(
    session: AsyncSession, guild_id: int, *, now: datetime, bots: set[int] | None = None,
) -> Scope:
    hidden = await hidden_users(session, guild_id, now)
    return Scope(
        guild_id=guild_id,
        channels=await indexed_channels(session, guild_id),
        skip=hidden | set(bots or ()),
        hidden=hidden,
    )


# ── Text as the replay shows it ──
# Discord's markup for mentions and custom emoji means nothing on its own, so it becomes
# what Discord would show. A mention of someone who can't be named reads "@someone".
_USER = re.compile(r"<@!?(\d+)>")
_CHANNEL = re.compile(r"<#(\d+)>")
_ROLE = re.compile(r"<@&(\d+)>")
_EMOJI = re.compile(r"<a?:([A-Za-z0-9_~]+):\d+>")
_TIME = re.compile(r"<t:(\d+)(?::[tTdDfFR])?>")
TEXT_LIMIT = 1000


def clean_text(text: str, *, nameable, channel_of) -> str:
    """A message's text with Discord markup resolved. A mention of someone the report can
    name stays ``<@id>``, for the name to be filled in when it's served (store.present);
    anyone else is "@someone" for good. ``nameable(id)`` says which; ``channel_of(id)``
    gives a channel's name or None."""
    text = text or ""
    text = _USER.sub(lambda m: m.group(0) if nameable(int(m.group(1))) else "@someone", text)
    text = _CHANNEL.sub(lambda m: "#" + (channel_of(int(m.group(1))) or "channel"), text)
    text = _ROLE.sub("@role", text)
    text = _EMOJI.sub(lambda m: f":{m.group(1)}:", text)
    text = _TIME.sub(lambda m: datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc).strftime("%b %-d, %Y"), text)
    text = text.strip()
    if len(text) > TEXT_LIMIT:
        text = text[: TEXT_LIMIT - 1].rsplit(" ", 1)[0] + "…"
    return text
