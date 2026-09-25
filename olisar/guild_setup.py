"""Provision a guild's per-server rows (config, persona, proactivity).

Used by both the DB seed script and the bot (on_ready / on_guild_join), so a
server the bot is added to gets the same defaults as the original target guild —
that's what makes every setting server-specific.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import Guild, GuildConfig, Persona, ProactivityConfig
from olisar.persona import (
    DEFAULT_PERSONA_NAME,
    DEFAULT_TONE_NOTES,
    default_system_prompt,
    refreshed_tone_notes,
)


def name_trigger_for(bot_name: str) -> list[str]:
    """The name trigger a new server starts with: the bot's own name, as members will type
    it. Punctuation at either end is dropped, since a trigger matches on word boundaries
    and "bot!" would never match; a name with no letters or digits gets none."""
    trigger = re.sub(r"^\W+|\W+$", "", (bot_name or "").lower())
    return [trigger] if trigger else []


async def ensure_guild_defaults(
    session: AsyncSession,
    guild_id: int,
    *,
    name: str = "",
    icon: str = "",
    bot_name: str = "",
) -> None:
    """Create the per-guild rows for ``guild_id`` if missing (idempotent). Refreshes
    the cached name/icon and marks the guild active. Caller owns the transaction.

    ``bot_name`` is what the bot is called in this server. A new server's persona and
    name trigger start from it, so a bot the operator named in Discord answers to that
    name rather than to "Olisar". Rows that already exist keep what they have."""
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
        config = GuildConfig(guild_id=guild_id)
        if bot_name:
            config.name_triggers = name_trigger_for(bot_name)
        session.add(config)
    persona = await session.get(Persona, guild_id)
    if persona is None:
        persona_name = (bot_name or DEFAULT_PERSONA_NAME)[:64]
        session.add(Persona(
            guild_id=guild_id,
            name=persona_name,
            system_prompt=default_system_prompt(persona_name),
            tone_notes=DEFAULT_TONE_NOTES,
        ))
    else:
        # An exact match with any previous release's seed means nobody ever edited this
        # field, so the guild is running defaults and should get the current ones.
        fresh = refreshed_tone_notes(persona.tone_notes)
        if fresh is not None:
            persona.tone_notes = fresh
    if await session.get(ProactivityConfig, guild_id) is None:
        session.add(ProactivityConfig(guild_id=guild_id))
