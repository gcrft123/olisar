"""Provision a guild's per-server rows (config, persona, proactivity).

Used by both the DB seed script and the bot (on_ready / on_guild_join), so a
server the bot is added to gets the same defaults as the original target guild —
that's what makes every setting server-specific.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import Guild, GuildConfig, Persona, ProactivityConfig
from olisar.persona import DEFAULT_PERSONA_NAME, DEFAULT_SYSTEM_PROMPT, DEFAULT_TONE_NOTES


async def ensure_guild_defaults(
    session: AsyncSession, guild_id: int, *, name: str = "", icon: str = ""
) -> None:
    """Create the per-guild rows for ``guild_id`` if missing (idempotent). Refreshes
    the cached name/icon and marks the guild active. Caller owns the transaction."""
    guild = await session.get(Guild, guild_id)
    if guild is None:
        session.add(Guild(id=guild_id, name=name or "", icon=icon or "", active=True))
    else:
        if name:
            guild.name = name
        if icon:
            guild.icon = icon
        guild.active = True
    if await session.get(GuildConfig, guild_id) is None:
        session.add(GuildConfig(guild_id=guild_id))
    if await session.get(Persona, guild_id) is None:
        session.add(Persona(
            guild_id=guild_id,
            name=DEFAULT_PERSONA_NAME,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tone_notes=DEFAULT_TONE_NOTES,
        ))
    if await session.get(ProactivityConfig, guild_id) is None:
        session.add(ProactivityConfig(guild_id=guild_id))
