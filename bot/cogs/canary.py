"""Runs the model self-test shortly after startup and once a day after that.

See olisar/gemini/canary.py for what it checks and why. Two requests per run.
"""

from __future__ import annotations

import asyncio
import logging

from discord.ext import commands, tasks

from olisar.gemini.canary import run_chain_canary

log = logging.getLogger("olisar.canary")

# Long enough that a restart doesn't spend its first seconds on a self-test, short enough
# that a broken deploy is reported while the operator is still watching it come up.
_STARTUP_DELAY = 60.0


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
        await asyncio.sleep(_STARTUP_DELAY)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Canary(bot))
