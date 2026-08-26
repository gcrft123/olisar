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
from bot.content import channel_identity
from bot.replies import anchor_for, composing, record_bot_messages, send_paced
from olisar import prompt_overrides
from olisar.context import is_own_message, name_map, speaker_name
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
    FOLLOW_UP_REPLY_NOTE,
    PROACTIVE_NOTE,
    SKIP_SENTINEL,
    classify,
    follow_up_score,
    heuristic_score,
    level_threshold,
    pick_reaction_emoji,
    reaction_score,
    relaxed_threshold,
)

log = logging.getLogger("olisar.proactive")

SCAN_SECONDS = 25
MIN_AGE = 15.0   # let humans answer first
MAX_AGE = 600.0  # don't resurrect stale messages

# Passive reactions run on their own looser cadence: react faster (low stakes) and skip
# the classifier entirely — `reaction_score` and the emoji picker are the gates.
REACT_SCAN_SECONDS = 30
REACT_MIN_AGE = 5.0
REACT_MAX_AGE = 300.0


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
        # Separate state for the passive-reaction path so it never interferes with chiming.
        self._react_cooldown: dict[int, float] = {}      # per-channel
        self._react_recent: dict[int, list[float]] = {}  # per-guild, hourly cap
        self._react_considered: dict[int, int] = {}
        self.scan.start()
        self.react_scan.start()

    def cog_unload(self) -> None:
        self.scan.cancel()
        self.react_scan.cancel()

    @tasks.loop(seconds=SCAN_SECONDS)
    async def scan(self) -> None:
        try:
            await self._scan_once()
        except Exception:
            log.exception("proactive scan failed")

    @scan.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=REACT_SCAN_SECONDS)
    async def react_scan(self) -> None:
        try:
            for guild in list(self.bot.guilds):
                try:
                    if await self._scan_reactions_guild(guild.id):
                        return  # at most one reaction per tick
                except Exception:
                    log.exception("reaction scan failed for guild %s", guild.id)
        except Exception:
            log.exception("reaction scan failed")

    @react_scan.before_loop
    async def _before_react(self) -> None:
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
        follow_up = 0.0
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
                # Two rows, not one: whether Olisar wrote the message directly above is
                # what separates "someone is talking back to me" from "someone is talking".
                recent = (
                    await session.scalars(
                        select(Message)
                        .where(Message.channel_id == cid)
                        .order_by(Message.created_at.desc())
                        .limit(2)
                    )
                ).all()
                latest = recent[0] if recent else None
                if latest is None or latest.author_is_bot:
                    continue  # nothing new, or a bot (incl. Olisar) spoke last
                if latest.message_id <= self._last_considered.get(cid, 0):
                    continue
                age = _age_seconds(latest.created_at)
                if age < MIN_AGE or age > MAX_AGE:
                    continue
                self._last_considered[cid] = latest.message_id  # don't re-evaluate
                answered_olisar = len(recent) > 1 and is_own_message(recent[1])
                reply_signal = follow_up_score(latest.content, after_olisar=answered_olisar)
                # A reply to Olisar is a message that wants an answer, whether or not it
                # parses as a question — which is all `heuristic_score` can see. Whichever
                # signal is stronger opens the gate.
                if max(heuristic_score(latest.content, age), reply_signal) < threshold:
                    continue
                follow_up = reply_signal
                candidate = (cid, latest.message_id, latest.author_id, latest.content)
                break

        if candidate is None:
            return False
        cid, msg_id, author_id, content = candidate

        # Stage 2 — cheap classifier on the last few messages. A message that answers
        # Olisar is judged as "should I carry this on", against a lowered bar: the
        # operator's threshold is set for interrupting other people's conversations, and
        # this isn't one. `relaxed_threshold` keeps a floor, so it's eased, not waived.
        transcript = await self._transcript(cid)
        should, confidence, reason = await classify(transcript, follow_up=follow_up > 0)
        bar = relaxed_threshold(conf_threshold, follow_up)
        if not should or confidence < bar:
            log.info(
                "proactive declined ch=%s conf=%.2f bar=%.2f follow_up=%.2f reason=%s",
                cid, confidence, bar, follow_up, reason,
            )
            return False

        # Stage 3 — full reply (may still self-skip).
        if await self._chime_in(
            guild_id, cid, msg_id, author_id, content, follow_up=follow_up > 0
        ):
            ts = time.monotonic()
            self._last_proactive[guild_id] = ts
            self._channel_cooldown[cid] = ts
            recent.append(ts)
            log.info(
                "proactive chimed in guild=%s ch=%s conf=%.2f bar=%.2f follow_up=%.2f",
                guild_id, cid, confidence, bar, follow_up,
            )
            return True
        return False

    async def _scan_reactions_guild(self, guild_id: int) -> bool:
        """Looser, emoji-only path: find one fresh message worth a reaction, ask the
        model for a single emoji, and add it. No reply, no classifier."""
        now = time.monotonic()
        recent = self._react_recent.setdefault(guild_id, [])
        recent[:] = [t for t in recent if now - t < 3600]

        candidate: tuple[int, int] | None = None
        async with session_scope() as session:
            pconf = await session.get(ProactivityConfig, guild_id)
            if pconf is None or not pconf.reaction_enabled:
                return False
            if self._in_quiet_hours(pconf):
                return False
            if len(recent) >= pconf.reaction_max_per_hour:
                return False
            threshold = pconf.reaction_threshold
            for cid in await self._candidate_channels(session, guild_id, pconf):
                if now - self._react_cooldown.get(cid, 0.0) < pconf.reaction_cooldown_sec:
                    continue
                latest = await session.scalar(
                    select(Message)
                    .where(Message.channel_id == cid)
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                if latest is None or latest.author_is_bot:
                    continue
                if latest.message_id <= self._react_considered.get(cid, 0):
                    continue
                age = _age_seconds(latest.created_at)
                if age < REACT_MIN_AGE or age > REACT_MAX_AGE:
                    continue
                self._react_considered[cid] = latest.message_id  # don't re-evaluate
                # A zero is a hard no whatever the operator's threshold is (the default
                # threshold is 0.0, so without this every question reached the picker).
                score = reaction_score(latest.content)
                if score <= 0 or score < threshold:
                    continue
                candidate = (cid, latest.message_id)
                break

        if candidate is None:
            return False
        cid, msg_id = candidate

        emoji = await pick_reaction_emoji(await self._transcript(cid))
        if not emoji:
            return False
        channel = self.bot.get_channel(cid)
        if channel is None:
            return False
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.add_reaction(emoji)
        except Exception:
            log.info("couldn't add reaction %s in ch=%s", emoji, cid)
            return False
        ts = time.monotonic()
        self._react_cooldown[cid] = ts
        recent.append(ts)
        log.info("reacted %s guild=%s ch=%s", emoji, guild_id, cid)
        return True

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
        return "\n".join(f"{speaker_name(m, names)}: {m.content}" for m in rows)

    async def _chime_in(
        self,
        guild_id: int,
        channel_id: int,
        msg_id: int,
        author_id: int,
        content: str,
        *,
        follow_up: bool = False,
    ) -> bool:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return False
        try:
            trigger = await channel.fetch_message(msg_id)
        except Exception:
            trigger = None
        actions = MessageActions(self.bot, trigger) if trigger else BotActions(self.bot)

        room_name, room_topic = channel_identity(channel)
        async with session_scope() as session:
            display = (await name_map(session, {author_id})).get(author_id, "someone")
            async with composing(channel):
                reply = await generate_reply(
                    session,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    current_message_id=msg_id,
                    bot_user_id=self.bot.user.id,
                    user_id=author_id,
                    display_name=display,
                    user_text=content,
                    actions=actions,
                    # Separately overridable, not one key for both: interrupting a
                    # conversation and continuing your own are different instructions,
                    # and a single override would silently collapse them.
                    runtime_note=(
                        prompt_overrides.follow_up_note(FOLLOW_UP_REPLY_NOTE)
                        if follow_up
                        else prompt_overrides.proactive_note(PROACTIVE_NOTE)
                    ),
                    channel_name=room_name,
                    channel_topic=room_topic,
                )

        # No report button here, unlike the addressed paths: nobody asked Olisar anything,
        # so there is no prompt of theirs to report and no failure they'd recognize. A
        # chime that comes out blank is the bot's problem, and it's in the operator's logs.
        clean = (reply.text or "").strip()
        if not clean or clean.lower() in (SKIP_SENTINEL, "skip"):
            log.info("proactive self-skipped ch=%s", channel_id)
            return False
        # Chiming in unprompted almost always anchors: the message being answered has sat
        # for at least MIN_AGE and the room has usually moved on — which is exactly the case
        # the reply reference exists for. `anchor_for` still decides, so a chime into a
        # channel that went quiet the second before doesn't quote for no reason.
        sent = await send_paced(channel, clean, reply_to=anchor_for(self.bot, trigger))
        await record_bot_messages(
            sent, guild_id=guild_id, channel_id=channel_id, bot_user_id=self.bot.user.id
        )
        return True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Proactive(bot))
