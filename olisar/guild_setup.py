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


def _seeded_before_connect(persona: Persona, config: GuildConfig) -> bool:
    """Whether the persona and name trigger are still exactly what the seed writes for a
    guild whose bot name it doesn't know.

    A server-hosted install (Docker, the VM) seeds TARGET_GUILD_ID at startup, before the
    bot has connected and can say what it's called, so its home server started as
    "Olisar" whatever the bot's name. Checked only on the bot's first visit, and only
    all three untouched: anything someone edited stays as they left it."""
    return (
        persona.name == DEFAULT_PERSONA_NAME
        and persona.system_prompt == default_system_prompt(DEFAULT_PERSONA_NAME)
        and list(config.name_triggers or []) == name_trigger_for(DEFAULT_PERSONA_NAME)
    )


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
    name rather than to "Olisar". Rows that already exist keep what they have, with one
    exception: rows the startup seed wrote before the bot ever connected (see
    _seeded_before_connect)."""
    guild = await session.get(Guild, guild_id)
    # The bot names the guild row the first time it provisions it; only the startup seed
    # (scripts/init_db.py, which knows no name) leaves it blank.
    first_visit = bool(bot_name) and (guild is None or not guild.name)
    if guild is None:
        session.add(Guild(id=guild_id, name=name or "", icon=icon or "", active=True))
    else:
        if name:
            guild.name = name
        if icon:
            guild.icon = icon
        guild.active = True
    config = await session.get(GuildConfig, guild_id)
    if config is None:
        config = GuildConfig(guild_id=guild_id)
        if bot_name:
            config.name_triggers = name_trigger_for(bot_name)
        session.add(config)
    persona = await session.get(Persona, guild_id)
    persona_name = (bot_name or DEFAULT_PERSONA_NAME)[:64]
    if persona is None:
        session.add(Persona(
            guild_id=guild_id,
            name=persona_name,
            system_prompt=default_system_prompt(persona_name),
            tone_notes=DEFAULT_TONE_NOTES,
        ))
    else:
        if first_visit and _seeded_before_connect(persona, config):
            persona.name = persona_name
            persona.system_prompt = default_system_prompt(persona_name)
            config.name_triggers = name_trigger_for(bot_name)
        # An exact match with any previous release's seed means nobody ever edited this
        # field, so the guild is running defaults and should get the current ones.
        fresh = refreshed_tone_notes(persona.tone_notes)
        if fresh is not None:
            persona.tone_notes = fresh
    if await session.get(ProactivityConfig, guild_id) is None:
        session.add(ProactivityConfig(guild_id=guild_id))
