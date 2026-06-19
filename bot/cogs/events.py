"""Gateway listeners that keep Olisar's stored copies honest when Discord changes.

* **edit** — recompute the message's text (content + embeds + attachments) and
  overwrite it in memory, the search index, and context snapshots.
* **delete / bulk delete** — purge the message(s) from all three, pruning vectors.

These use the *raw* events so they fire even for messages not in the bot's cache
(anything older than the current session), which is most of what gets edited or
deleted. Editing only updates text — a re-added image isn't re-captioned (rare);
a full re-description happens on the next ``/olisar reindex``.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from bot.content import raw_message_text
from olisar.db.engine import session_scope
from olisar.memory.revisions import apply_delete, apply_edit

log = logging.getLogger("olisar.events")


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        data = payload.data or {}
        # Ignore partial updates that carry no visible payload (pins, flags, etc.).
        if not any(k in data for k in ("content", "embeds", "attachments")):
            return
        new_text = raw_message_text(data)
        if not new_text:
            return
        try:
            async with session_scope() as session:
                await apply_edit(session, message_id=payload.message_id, content=new_text)
        except Exception:
            log.exception("failed to apply edit for message %s", payload.message_id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        try:
            async with session_scope() as session:
                await apply_delete(session, message_ids=[payload.message_id])
        except Exception:
            log.exception("failed to apply delete for message %s", payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
        self, payload: discord.RawBulkMessageDeleteEvent
    ) -> None:
        try:
            async with session_scope() as session:
                await apply_delete(session, message_ids=list(payload.message_ids))
        except Exception:
            log.exception("failed to apply bulk delete (%d msgs)", len(payload.message_ids))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
