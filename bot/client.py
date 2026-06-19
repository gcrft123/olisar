"""The Olisar bot subclass: intents, cog loading, and slash-command sync."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from olisar.config import settings

log = logging.getLogger("olisar.bot")

# Cogs loaded on startup. More are added as later phases land.
INITIAL_COGS = [
    "bot.cogs.guilds",
    "bot.cogs.conversation",
    "bot.cogs.members",
    "bot.cogs.slash",
    "bot.cogs.presence",
    "bot.cogs.memory_worker",
    "bot.cogs.context_channels",
    "bot.cogs.search_index",
    "bot.cogs.events",
    "bot.cogs.self_destruct",
    "bot.cogs.star_citizen",
    "bot.cogs.proactive",
]


def _build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    # Privileged — must ALSO be toggled on in the Developer Portal. message_content
    # lets Olisar read what people say; members lets it track per-user profiles.
    intents.message_content = True
    intents.members = True
    return intents


class OlisarBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,  # we lean on slash + triggers, not prefixes
            intents=_build_intents(),
            help_command=None,
        )

    async def setup_hook(self) -> None:
        for ext in INITIAL_COGS:
            await self.load_extension(ext)
            log.info("loaded cog %s", ext)
        # Slash commands are synced per guild by the guilds cog on_ready (the guild
        # list isn't known until we've connected), which keeps multi-server in sync.

    async def on_ready(self) -> None:
        log.info("Olisar is online as %s (id=%s)", self.user, self.user and self.user.id)
