"""Discord-side reply helpers: chunk long text, send it, and record it to memory."""

from __future__ import annotations

import discord

from olisar.db.engine import session_scope
from olisar.db.models import GuildConfig
from olisar.memory.writer import record_message

DISCORD_LIMIT = 2000
_ZWSP = "​"


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


async def send_reply(
    channel: discord.abc.Messageable,
    text: str,
    *,
    reply_to: discord.Message | None = None,
) -> list[discord.Message]:
    """Send text (chunked). The first chunk replies to `reply_to` if given. Honours the
    guild's blocked-mention policy so the bot can't @everyone/@here/role when disallowed."""
    blocked = await blocked_mentions_for(channel)
    am = mention_policy(blocked)
    text = sanitize_mentions(text, blocked)
    sent: list[discord.Message] = []
    for i, chunk in enumerate(chunk_text(text)):
        if i == 0 and reply_to is not None:
            sent.append(await reply_to.reply(chunk, allowed_mentions=am))
        else:
            sent.append(await channel.send(chunk, allowed_mentions=am))
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
                display_name="Olisar",
            )
