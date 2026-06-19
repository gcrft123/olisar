"""Proactive participation: Olisar decides, on its own, when to add value.

A background loop scans the latest un-answered message per eligible channel and
runs the cheap→expensive cascade (gates → heuristic → classifier → reply). All
the spam-control knobs live in `proactivity_config` (set via `/olisar proactive`).
Cooldown/rate state is kept in-memory — resetting it on restart is harmless.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from discord.ext import commands, tasks
from sqlalchemy import select

from bot.actions import BotActions, MessageActions
from bot.replies import record_bot_messages, send_reply
from olisar.config import settings
from olisar.context import name_map
from olisar.db.engine import session_scope
from olisar.db.models import (
    ChannelAllowlist,
    ChannelMode,
    Message,
    ProactivityConfig,
    ProactivityLevel,
)
from olisar.pipeline import generate_reply
from olisar.proactivity import (
    PROACTIVE_NOTE,
    SKIP_SENTINEL,
    classify,
    heuristic_score,
    level_threshold,
)

log = logging.getLogger("olisar.proactive")

SCAN_SECONDS = 25
MIN_AGE = 15.0   # let humans answer first
MAX_AGE = 600.0  # don't resurrect stale messages


def _age_seconds(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


class Proactive(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_proactive: dict[int, float] = {}  # per-guild last chime (monotonic)
        self._channel_cooldown: dict[int, float] = {}  # per-channel (ids are unique)
        self._recent: dict[int, list[float]] = {}  # per-guild timestamps for the hourly cap
        self._last_considered: dict[int, int] = {}
        self.scan.start()

    def cog_unload(self) -> None:
        self.scan.cancel()

    @tasks.loop(seconds=SCAN_SECONDS)
    async def scan(self) -> None:
        try:
            await self._scan_once()
        except Exception:
            log.exception("proactive scan failed")

    @scan.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    def _in_quiet_hours(self, pconf: ProactivityConfig) -> bool:
        qh = pconf.quiet_hours or {}
        if "start" not in qh or "end" not in qh or qh["start"] == qh["end"]:
            return False
        start, end = qh["start"], qh["end"]
        hour = datetime.now(timezone.utc).hour
        return (start <= hour < end) if start < end else (hour >= start or hour < end)

    async def _candidate_channels(self, session, guild_id: int, pconf) -> list[int]:
        if pconf.allowed_channels:
            return [int(c) for c in pconf.allowed_channels]
        return list(
            (
                await session.scalars(
                    select(ChannelAllowlist.channel_id).where(
                        ChannelAllowlist.guild_id == guild_id,
                        ChannelAllowlist.mode.in_([ChannelMode.respond, ChannelMode.both]),
                    )
                )
            ).all()
        )

    async def _scan_once(self) -> None:
        # Evaluate each guild the bot is in; chime in at most one per tick.
        for guild in list(self.bot.guilds):
            try:
                if await self._scan_guild(guild.id):
                    return
            except Exception:
                log.exception("proactive scan failed for guild %s", guild.id)

    async def _scan_guild(self, guild_id: int) -> bool:
        now = time.monotonic()
        recent = self._recent.setdefault(guild_id, [])
        recent[:] = [t for t in recent if now - t < 3600]

        candidate: tuple[int, int, int, str] | None = None
        conf_threshold = 0.7
        async with session_scope() as session:
            pconf = await session.get(ProactivityConfig, guild_id)
            if pconf is None or not pconf.enabled or pconf.level == ProactivityLevel.off:
                return False
            if self._in_quiet_hours(pconf):
                return False
            if now - self._last_proactive.get(guild_id, 0.0) < pconf.global_cooldown_sec:
                return False
            if len(recent) >= pconf.max_per_hour:
                return False

            conf_threshold = pconf.confidence_threshold
            threshold = level_threshold(pconf.level)
            for cid in await self._candidate_channels(session, guild_id, pconf):
                if now - self._channel_cooldown.get(cid, 0.0) < pconf.channel_cooldown_sec:
                    continue
                latest = await session.scalar(
                    select(Message)
                    .where(Message.channel_id == cid)
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                if latest is None or latest.author_is_bot:
                    continue  # nothing new, or Olisar already spoke last
                if latest.message_id <= self._last_considered.get(cid, 0):
                    continue
                age = _age_seconds(latest.created_at)
                if age < MIN_AGE or age > MAX_AGE:
                    continue
                self._last_considered[cid] = latest.message_id  # don't re-evaluate
                if heuristic_score(latest.content, age) < threshold:
                    continue
                candidate = (cid, latest.message_id, latest.author_id, latest.content)
                break

        if candidate is None:
            return False
        cid, msg_id, author_id, content = candidate

        # Stage 2 — cheap classifier on the last few messages.
        transcript = await self._transcript(cid)
        should, confidence, reason = await classify(transcript)
        if not should or confidence < conf_threshold:
            log.info("proactive declined ch=%s conf=%.2f reason=%s", cid, confidence, reason)
            return False

        # Stage 3 — full reply (may still self-skip).
        if await self._chime_in(guild_id, cid, msg_id, author_id, content):
            ts = time.monotonic()
            self._last_proactive[guild_id] = ts
            self._channel_cooldown[cid] = ts
            recent.append(ts)
            log.info("proactive chimed in guild=%s ch=%s conf=%.2f", guild_id, cid, confidence)
            return True
        return False

    async def _transcript(self, channel_id: int) -> str:
        async with session_scope() as session:
            rows = list(
                reversed(
                    (
                        await session.scalars(
                            select(Message)
                            .where(Message.channel_id == channel_id)
                            .order_by(Message.created_at.desc())
                            .limit(5)
                        )
                    ).all()
                )
            )
            names = await name_map(session, {m.author_id for m in rows if not m.author_is_bot})
        return "\n".join(
            f"{'Olisar' if m.author_is_bot else names.get(m.author_id, str(m.author_id))}: {m.content}"
            for m in rows
        )

    async def _chime_in(
        self, guild_id: int, channel_id: int, msg_id: int, author_id: int, content: str
    ) -> bool:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return False
        try:
            trigger = await channel.fetch_message(msg_id)
        except Exception:
            trigger = None
        actions = MessageActions(self.bot, trigger) if trigger else BotActions(self.bot)

        async with session_scope() as session:
            display = (await name_map(session, {author_id})).get(author_id, "someone")
            async with channel.typing():
                text = await generate_reply(
                    session,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    current_message_id=msg_id,
                    bot_user_id=self.bot.user.id,
                    user_id=author_id,
                    display_name=display,
                    user_text=content,
                    actions=actions,
                    runtime_note=PROACTIVE_NOTE,
                )

        clean = (text or "").strip()
        if not clean or clean.lower() in (SKIP_SENTINEL, "skip"):
            log.info("proactive self-skipped ch=%s", channel_id)
            return False
        sent = await send_reply(channel, clean, reply_to=trigger)
        await record_bot_messages(
            sent, guild_id=guild_id, channel_id=channel_id, bot_user_id=self.bot.user.id
        )
        return True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Proactive(bot))
