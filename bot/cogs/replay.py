"""Memory replays: the bot's half (olisar/replay/ has the rest).

The console starts a run by writing a row; this loop picks it up and carries it through,
one run per server at a time. It lives here rather than in the request because a replay
of a big server takes minutes and has to outlast the console being closed, and because
it needs what only the gateway knows: who's still in the server, who's a bot, their names
and faces, and when Olisar itself joined.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord
from discord.ext import commands, tasks

from olisar.db.engine import session_scope
from olisar.db.models import utcnow
from olisar.peers import is_member_author
from olisar.replay import media, runner, store
from olisar.replay.scope import Person

log = logging.getLogger("olisar.replay")

TICK_SECONDS = 5


class DiscordRoster:
    """The server as discord.py has it, for the replay (olisar.replay.scope.Roster)."""

    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        self.guild = guild
        self.guild_name = guild.name
        self.guild_icon = str(guild.icon.url) if guild.icon else ""
        me = guild.me
        self.me_id = bot.user.id if bot.user else 0
        self.me_joined_at = me.joined_at if me else None
        self.me_avatar = str(bot.user.display_avatar.url) if bot.user else None
        self._members = {m.id: m for m in guild.members}

    def present(self, user_id: int) -> bool:
        return int(user_id) in self._members

    def bot_ids(self) -> set[int]:
        return {m.id for m in self._members.values() if not is_member_author(m)} | {self.me_id}

    def person(self, user_id: int) -> Person | None:
        m = self._members.get(int(user_id))
        if m is None:
            return None
        return Person(name=m.display_name, avatar=str(m.display_avatar.url))

    def join_dates(self) -> list[tuple[int, datetime]]:
        return [(m.id, m.joined_at) for m in self._members.values() if m.joined_at and is_member_author(m)]

    def humans(self) -> int:
        return sum(1 for m in self._members.values() if is_member_author(m))

    async def media(self, channel_id: int, message_id: int) -> list[dict] | None:
        """The GIFs, images and stickers a message holds (olisar/replay/media.py), or None
        if Discord no longer has it or Olisar can't read the channel."""
        channel = self.guild.get_channel_or_thread(int(channel_id))
        if channel is None:
            return None
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.NotFound, discord.Forbidden):
            return None
        return media.from_message(message)

    def names(self) -> set[str]:
        """Every name a member goes by here, lowercased, so a pick is never someone's name."""
        out: set[str] = set()
        for m in self._members.values():
            out.update(n.lower() for n in (m.name, m.global_name, m.nick) if n)
        return out

    def public(self, channel_id: int) -> bool | None:
        channel = self.guild.get_channel_or_thread(int(channel_id))
        if channel is None:
            return None
        perms = channel.permissions_for(self.guild.default_role)
        return bool(perms.view_channel and perms.read_message_history)


class Replay(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.running: dict[int, asyncio.Task] = {}   # guild id → the run being advanced
        self.tick.start()

    def cog_unload(self) -> None:
        self.tick.cancel()
        for task in self.running.values():
            task.cancel()

    def poke(self) -> None:
        """Look for work now rather than at the next tick (the console just started one)."""
        if self.bot.is_ready():
            asyncio.get_running_loop().create_task(self.tick())

    @tasks.loop(seconds=TICK_SECONDS)
    async def tick(self) -> None:
        async with session_scope() as session:
            due = await store.due_runs(session, utcnow())
        for run_id, guild_id in due:
            task = self.running.get(guild_id)
            if task is not None and not task.done():
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue   # not in that server now; the run waits (and shows as stopped)
            self.running[guild_id] = asyncio.create_task(self._advance(run_id, guild), name=f"replay-{run_id}")

    async def _advance(self, run_id: int, guild: discord.Guild) -> None:
        try:
            if not guild.chunked:
                await guild.chunk(cache=True)
            await runner.advance(run_id, DiscordRoster(self.bot, guild))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — runner.advance records its own failures
            log.exception("replay %s: the bot couldn't advance it", run_id)

    @tick.before_loop
    async def _ready(self) -> None:
        await self.bot.wait_until_ready()

    @tick.error
    async def _tick_error(self, exc: BaseException) -> None:
        log.exception("replay tick failed", exc_info=exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Replay(bot))
