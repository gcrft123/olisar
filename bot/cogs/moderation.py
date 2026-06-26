"""Keeps the global ban list fresh.

Pulls the marketplace registry's ban list on a short loop so a ban (set by a platform
developer in the registry console) propagates to this bot within ~a minute. Enforcement
itself lives in ``bot.access.member_allowed``; this cog only does the periodic sync.
"""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from olisar import moderation

log = logging.getLogger("olisar.cogs.moderation")


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sync_bans.start()

    async def cog_unload(self) -> None:
        self.sync_bans.cancel()

    @tasks.loop(seconds=90)
    async def sync_bans(self) -> None:
        await moderation.sync_bans()

    @sync_bans.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
