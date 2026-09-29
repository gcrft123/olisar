"""Multi-guild bookkeeping.

Records every server Olisar is in (the ``guild`` table — which the dashboard's
server switcher and the auth layer both read), seeds each one's per-server defaults,
and keeps slash commands synced. A server waiting for the operator's approval gets no
slash commands until it's approved. This is what lets the bot live in more than one
server with independent settings.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands
from sqlalchemy import select

from olisar import guild_approval
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

    async def _provision(self, guild: discord.Guild) -> bool:
        """Record the server and seed its defaults. Returns whether it's approved: a server
        the bot has no record of is approved as it's added only when the operator didn't
        need asking (see olisar/guild_approval.py)."""
        # What members see the bot called there: its nickname in that server, else its name.
        me = guild.me or self.bot.user
        async with session_scope() as session:
            known = await session.get(Guild, guild.id) is not None
            arrival = known or await guild_approval.approved_on_arrival(session, guild.id, guild.owner_id)
            await ensure_guild_defaults(
                session,
                guild.id,
                name=guild.name,
                icon=_icon_url(guild),
                bot_name=me.display_name if me else "",
            )
            row = await session.get(Guild, guild.id)
            if not known:
                row.approved = arrival
            approved = bool(row.approved)
        guild_approval.set_pending(guild.id, not approved)
        if not approved:
            log.warning("added to server %s (%s), which waits for the operator's approval", guild.id, guild.name)
        return approved

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
            if await self._provision(guild):
                await self._sync_commands(guild)
        # Mark guilds the bot is no longer in as inactive, so they drop off the switcher.
        async with session_scope() as session:
            for row in (await session.scalars(select(Guild))).all():
                row.active = row.id in present
        log.info("provisioned %d guild(s)", len(present))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if await self._provision(guild):
            await self._sync_commands(guild)
        log.info("added to guild %s (%s)", guild.id, guild.name)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        async with session_scope() as session:
            row = await session.get(Guild, guild.id)
            if row is not None:
                row.active = False
        guild_approval.set_pending(guild.id, False)
        log.info("removed from guild %s", guild.id)

    async def approved(self, guild_id: int) -> None:
        """The operator approved a server: its slash commands go up now."""
        guild = self.bot.get_guild(guild_id)
        if guild is not None:
            await self._sync_commands(guild)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Guilds(bot))
