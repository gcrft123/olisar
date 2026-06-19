"""Discord-side reply helpers: chunk long text, send it, and record it to memory."""

from __future__ import annotations

import discord

from olisar.db.engine import session_scope
from olisar.memory.writer import record_message

DISCORD_LIMIT = 2000


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
    """Send text (chunked). The first chunk replies to `reply_to` if given."""
    sent: list[discord.Message] = []
    for i, chunk in enumerate(chunk_text(text)):
        if i == 0 and reply_to is not None:
            sent.append(await reply_to.reply(chunk, mention_author=False))
        else:
            sent.append(await channel.send(chunk))
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
