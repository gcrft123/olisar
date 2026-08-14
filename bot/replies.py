"""Discord-side reply helpers: chunk long text, send it, and record it to memory.

Also owns the *cadence* of a reply — how many messages it arrives as, how long the
typing indicator runs, and whether it carries Discord's reply reference. Those are the
difference between a member talking and a service responding, and none of them are
decisions the model can make for itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord

from olisar.db.engine import session_scope
from olisar.db.models import GuildConfig
from olisar.memory.writer import record_message
from olisar.persona import split_messages

DISCORD_LIMIT = 2000
_ZWSP = "​"

# Human pacing. A reply that shows "typing…" for six seconds and then says "yeah" reads
# as a machine: the indicator was tracking how slow the model was, not how much got
# written. So we think in silence, then type for as long as the finished text would
# plausibly take — which means writing it first and typing afterwards.
TYPING_CPS = 18.0             # characters per second — a quick, practised chat typist
TYPING_MIN, TYPING_MAX = 0.6, 6.0
TYPING_JITTER = (0.85, 1.2)   # so the cadence isn't identical every single time
BREAK_PAUSE = (0.4, 1.1)      # between two messages of the same turn
QUIET_THINKING = 4.0          # generate this long in silence before showing "typing…"
# A message older than this has had time to scroll, so a reply to it needs anchoring.
STALE_ANCHOR = 45.0


def sanitize_mentions(text: str, blocked) -> str:
    """Neutralise @everyone/@here in reply text when they're blocked — a zero-width space
    after the @ stops Discord parsing the ping while staying visually identical. Discord's
    allowed_mentions can't separate @everyone from @here (one "everyone" flag covers both),
    so we break the literal text to honour each independently."""
    b = set(blocked or [])
    if "everyone" in b:
        text = text.replace("@everyone", "@" + _ZWSP + "everyone")
    if "here" in b:
        text = text.replace("@here", "@" + _ZWSP + "here")
    return text


def mention_policy(blocked) -> discord.AllowedMentions:
    """AllowedMentions for an Olisar reply: roles are blocked here; @everyone/@here are
    handled by sanitize_mentions; the replied-to author is never pinged."""
    return discord.AllowedMentions(
        everyone=True, users=True, roles=("roles" not in set(blocked or [])), replied_user=False
    )


async def blocked_mentions_for(channel) -> list:
    """The guild's blocked-mention policy for a channel (empty in DMs — you can't
    @everyone/@here/role there anyway)."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return []
    async with session_scope() as session:
        cfg = await session.get(GuildConfig, guild.id)
        return list(cfg.blocked_mentions or []) if cfg else []


def chunk_text(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split text into <=limit pieces, preferring to break on newlines."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single very long line
            chunks.append(line[:limit])
            line = line[limit:]
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = line if not current else f"{current}\n{line}"
    if current:
        chunks.append(current)
    return chunks


def report_view(url: str) -> discord.ui.View | None:
    """A single **Report this** link-button, or None for an empty URL.

    A link button carries no ``custom_id`` and fires no interaction, so this view needs no
    timeout, no handler, and no registration with ``bot.add_view`` — it keeps working
    across restarts because Discord resolves it entirely client-side. The URL is the
    console's, and what it opens there is decided by who signs in (see olisar/failures.py).
    """
    if not url:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Report this", url=url, style=discord.ButtonStyle.link))
    return view


async def send_reply(
    channel: discord.abc.Messageable,
    text: str,
    *,
    reply_to: discord.Message | None = None,
    view: discord.ui.View | None = None,
) -> list[discord.Message]:
    """Send text (chunked). The first chunk replies to `reply_to` if given. Honours the
    guild's blocked-mention policy so the bot can't @everyone/@here/role when disallowed.

    ``view`` rides on the **last** chunk: a view belongs to one message, and a button that
    acts on the whole reply belongs under the end of it, not buried above two more chunks.
    """
    blocked = await blocked_mentions_for(channel)
    am = mention_policy(blocked)
    text = sanitize_mentions(text, blocked)
    chunks = chunk_text(text)
    sent: list[discord.Message] = []
    for i, chunk in enumerate(chunks):
        extra = {"view": view} if (view is not None and i == len(chunks) - 1) else {}
        if i == 0 and reply_to is not None:
            sent.append(await reply_to.reply(chunk, allowed_mentions=am, **extra))
        else:
            sent.append(await channel.send(chunk, allowed_mentions=am, **extra))
    return sent


def anchor_for(bot, message: discord.Message | None) -> discord.Message | None:
    """Discord's reply reference for a response to ``message`` — or ``None`` to just talk.

    The reply affordance exists to disambiguate: in a busy channel it points at which of
    several running strands you mean. Hanging it off *every* answer — including both sides
    of a quiet DM, where there is only one strand — is the machinery showing through. So we
    use it when the room has moved on since the message was posted, or when the message has
    sat long enough to have scrolled away, and otherwise just speak into the channel the way
    someone mid-conversation does.
    """
    if message is None or message.guild is None:  # a DM has nothing to disambiguate
        return None
    me = getattr(bot.user, "id", 0)
    try:
        # discord.py's own message cache — already in memory, so this costs no API call.
        for cached in getattr(bot, "cached_messages", ()):
            if (
                cached.id > message.id
                and cached.channel.id == message.channel.id
                and cached.author.id != me
            ):
                return message
        age = (discord.utils.utcnow() - message.created_at).total_seconds()
    except Exception:  # noqa: BLE001 — an odd cache entry must not cost us the reply
        return message
    return message if age > STALE_ANCHOR else None


def typing_seconds(text: str) -> float:
    """Roughly how long ``text`` would take a person to type, jittered and clamped."""
    secs = len(text or "") / TYPING_CPS
    return min(TYPING_MAX, max(TYPING_MIN, secs)) * random.uniform(*TYPING_JITTER)


async def _pace(channel: discord.abc.Messageable, seconds: float) -> None:
    """Hold the typing indicator for ``seconds`` before the message goes out.

    The wait is the point and the indicator is the garnish, so a channel where we can't
    show typing still gets the pause rather than an instant burst of messages."""
    try:
        async with channel.typing():
            await asyncio.sleep(seconds)
    except Exception:  # noqa: BLE001 — can't raise the indicator here; still don't rush
        await asyncio.sleep(seconds)


async def _type_after(channel: discord.abc.Messageable, delay: float) -> None:
    """Raise the typing indicator once ``delay`` has passed, and hold it until cancelled."""
    await asyncio.sleep(delay)
    with contextlib.suppress(Exception):  # a typing indicator is never worth an exception
        async with channel.typing():
            await asyncio.sleep(3600)  # the caller cancels us; this is just "until then"


@asynccontextmanager
async def composing(channel: discord.abc.Messageable) -> AsyncIterator[None]:
    """Think in silence, and only raise the typing indicator if it's taking a while.

    Wrapping the whole generation in ``channel.typing()`` tied the indicator to model
    latency, so a four-word answer landed after six seconds of typing. Staying quiet first
    is both truer to how someone reads before they answer and leaves the actual typing time
    to :func:`send_paced` — while a slow answer still brings the indicator up, so the
    channel doesn't look dead.
    """
    task = asyncio.create_task(_type_after(channel, QUIET_THINKING))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def send_paced(
    channel: discord.abc.Messageable,
    text: str,
    *,
    reply_to: discord.Message | None = None,
    view: discord.ui.View | None = None,
) -> list[discord.Message]:
    """Send a reply the way a person sends one: as the one to three messages the model
    asked for with ``SPLIT_MARKER``, each preceded by a typing indicator held for about as
    long as that message would take to type.

    Only the first message carries the reply reference and only the last carries the view,
    matching :func:`send_reply` — a turn is one utterance however many bubbles it arrives in.
    """
    sent: list[discord.Message] = []
    pieces = split_messages(text)
    for i, piece in enumerate(pieces):
        if i:
            await asyncio.sleep(random.uniform(*BREAK_PAUSE))
        await _pace(channel, typing_seconds(piece))
        sent += await send_reply(
            channel,
            piece,
            reply_to=reply_to if i == 0 else None,
            view=view if i == len(pieces) - 1 else None,
        )
    return sent


async def record_bot_messages(
    messages: list[discord.Message], *, guild_id: int, channel_id: int, bot_user_id: int
) -> None:
    """Store Olisar's own replies so they appear in future context windows."""
    if not messages:
        return
    async with session_scope() as session:
        for m in messages:
            await record_message(
                session,
                guild_id=guild_id,
                channel_id=channel_id,
                message_id=m.id,
                author_id=bot_user_id,
                author_is_bot=True,
                content=m.content or "",
                # Deliberately nameless: a stored bot message with no author_name is
                # Olisar's own, which is what lets a transcript render it as "me" rather
                # than as one more bot in the channel (olisar/db/models.py, Message).
                display_name="",
            )
