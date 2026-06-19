"""Background memory worker: embeds new content and runs summary/persona passes.

Runs every ~20s, off the reply path, so embedding and Flash-Lite synthesis never
slow down a response. Everything it does is resumable — it works off `embedded`
flags and threshold counters, so a restart just picks up where it left off.
"""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from olisar.knowledge.ingest import process_pending_sources
from olisar.memory.maintenance import embed_pending, run_personas, run_summaries

log = logging.getLogger("olisar.memory_worker")


class MemoryWorker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tick.start()

    def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(seconds=20)
    async def tick(self) -> None:
        try:
            await process_pending_sources()  # ingest one queued KB source, if any
            embedded = await embed_pending()
            if embedded:
                log.info("embedded %d items", embedded)
            await run_summaries()
            await run_personas()
        except Exception:
            log.exception("memory worker tick failed")

    @tick.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemoryWorker(bot))
