"""Fires due reminders.

Reminders are created by the ``add_reminder`` tool ('remind me in 2h…') or
automatically from a time-bound ``event`` fact. A 30s loop delivers the ones whose
time has come — by DM, or in the channel where it was set if asked. State lives in
the ``reminder`` table, so nothing is lost across restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from discord.ext import commands, tasks
from sqlalchemy import select

from bot.actions import BotActions
from olisar.db.engine import session_scope
from olisar.db.models import Reminder

log = logging.getLogger("olisar.reminders")

TICK_SECONDS = 30


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tick.start()

    def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(seconds=TICK_SECONDS)
    async def tick(self) -> None:
        try:
            await self._dispatch_due()
        except Exception:
            log.exception("reminder dispatch failed")

    @tick.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _dispatch_due(self) -> None:
        now = datetime.now(timezone.utc)
        async with session_scope() as session:
            due = (
                await session.scalars(
                    select(Reminder)
                    .where(Reminder.fired == False, Reminder.scheduled_at <= now)  # noqa: E712
                    .order_by(Reminder.scheduled_at.asc())
                    .limit(20)
                )
            ).all()
            for r in due:
                try:
                    await self._deliver(r)
                except Exception:
                    log.exception("reminder %s delivery failed", r.id)
                # Mark fired either way so a permanently-undeliverable one can't loop.
                r.fired = True

    async def _deliver(self, r: Reminder) -> None:
        body = f"⏰ {r.content}"
        if r.target == "channel" and r.channel_id:
            channel = self.bot.get_channel(r.channel_id)
            if channel is not None:
                await channel.send(f"<@{r.user_id}> {body}")
                log.info("reminder %s posted in channel %s", r.id, r.channel_id)
                return
        result = await BotActions(self.bot).send_dm(r.user_id, body)
        log.info("reminder %s -> %s", r.id, result)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
