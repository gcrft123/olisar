"""Multi-guild bookkeeping.

Records every server Olisar is in (the ``guild`` table — which the dashboard's
server switcher and the auth layer both read), seeds each one's per-server defaults,
and keeps slash commands synced. This is what lets the bot live in more than one
server with independent settings.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands
from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import Guild
from olisar.guild_setup import ensure_guild_defaults

log = logging.getLogger("olisar.guilds")


def _icon_url(guild: discord.Guild) -> str:
    return str(guild.icon.url) if guild.icon else ""


class Guilds(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._initialized = False  # provision + command-sync once per process

    async def _provision(self, guild: discord.Guild) -> None:
        async with session_scope() as session:
            await ensure_guild_defaults(
                session, guild.id, name=guild.name, icon=_icon_url(guild)
            )

    async def _sync_commands(self, guild: discord.Guild) -> None:
        # Per-guild sync propagates instantly (global sync can take ~1h).
        try:
            self.bot.tree.copy_global_to(guild=guild)
            await self.bot.tree.sync(guild=guild)
        except Exception:
            log.exception("command sync failed for guild %s", guild.id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._initialized:
            return  # on_ready can fire again on reconnect; do the heavy work once
        self._initialized = True
        present = {g.id for g in self.bot.guilds}
        for guild in self.bot.guilds:
            await self._provision(guild)
            await self._sync_commands(guild)
        # Mark guilds the bot is no longer in as inactive, so they drop off the switcher.
        async with session_scope() as session:
            for row in (await session.scalars(select(Guild))).all():
                row.active = row.id in present
        log.info("provisioned %d guild(s)", len(present))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._provision(guild)
        await self._sync_commands(guild)
        log.info("added to guild %s (%s)", guild.id, guild.name)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        async with session_scope() as session:
            row = await session.get(Guild, guild.id)
            if row is not None:
                row.active = False
        log.info("removed from guild %s", guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Guilds(bot))
