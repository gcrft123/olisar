"""Which servers the operator has let the bot work in.

A public bot can be added to any server by anyone, and whoever has Manage Server there then
administers Olisar in it: its settings, its console (over remote access), and the operator's
API quota. So a server the operator didn't add waits for them to approve it. Until they do,
the bot doesn't answer there, its commands refuse, and its admins can't sign in to the console.

A server is approved as it's added when it's the configured home server, when its owner is
the operator, or when it's the first server the bot is in (the one setup invites it to).
Servers the bot was in before approval existed stay approved.

``is_pending`` is read on every message and interaction, so the pending set lives in memory:
loaded when the bot connects, and kept current by joins, approvals and departures.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar import discord_app
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import Guild

log = logging.getLogger("olisar.guild_approval")

_pending: set[int] = set()


def is_pending(guild_id: int | None) -> bool:
    """Whether the bot is in ``guild_id`` without the operator's approval."""
    return guild_id is not None and guild_id in _pending


def pending_ids() -> frozenset[int]:
    return frozenset(_pending)


async def load() -> None:
    """Read the pending servers from the database."""
    async with session_scope() as session:
        rows = await session.scalars(
            select(Guild.id).where(Guild.active.is_(True), Guild.approved.is_(False))
        )
        _pending.clear()
        _pending.update(rows)


async def is_operator(user_id: int | None) -> bool:
    if not user_id:
        return False
    return user_id in settings.admin_allowlist or user_id in await discord_app.owner_ids()


async def approved_on_arrival(session: AsyncSession, guild_id: int, owner_id: int | None) -> bool:
    """Whether a server the bot has just been added to, and has no record of, can be worked
    in without asking the operator."""
    if guild_id == settings.target_guild_id:
        return True
    others = await session.scalar(
        select(Guild.id).where(Guild.approved.is_(True), Guild.id != guild_id).limit(1)
    )
    if others is None:
        return True  # the first server: the one setup invited the bot to
    return await is_operator(owner_id)


def set_pending(guild_id: int, pending: bool) -> None:
    if pending:
        _pending.add(guild_id)
    else:
        _pending.discard(guild_id)


async def approve(guild_id: int) -> bool:
    """Approve a server. False when the bot has no record of it."""
    async with session_scope() as session:
        row = await session.get(Guild, guild_id)
        if row is None:
            return False
        row.approved = True
    set_pending(guild_id, False)
    log.info("server %s approved", guild_id)
    return True


__all__ = ["approve", "approved_on_arrival", "is_operator", "is_pending", "load", "pending_ids", "set_pending"]
