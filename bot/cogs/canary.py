"""Runs the model self-test once a day.

See olisar/gemini/canary.py for what it checks and why. Two requests per model in the
slim default sweep. The first run waits a full day so a restart does not spend another
sweep on free-tier quota.
"""

from __future__ import annotations

import asyncio
import logging

from discord.ext import commands, tasks

from olisar.gemini.canary import run_chain_canary

log = logging.getLogger("olisar.canary")

# Skip the old "60s after ready" first run. Each sweep is several free-tier requests, and
# restarts were doubling the daily bill for no extra signal once the bot was already up.
_FIRST_RUN_DELAY = 24 * 60 * 60


class Canary(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tick.start()

    def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(hours=24)
    async def tick(self) -> None:
        try:
            await run_chain_canary()
        except Exception:  # the canary already swallows; belt and braces for the timer
            log.exception("model self-test raised")

    @tick.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(_FIRST_RUN_DELAY)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Canary(bot))
