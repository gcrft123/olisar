"""Context-only channels: resources and feeds.

Two kinds of channel that Olisar reads purely for *background context*, never
chatting in them:

* **resource** (e.g. ``#rules``, ``#roles-list``) — a durable reference snapshot,
  always folded into replies and into persona synthesis (so a role named in
  ``#roles-list`` can be linked to the members who hold it).
* **feed** (e.g. ``#announcements``, ``#game-news``) — just the last few messages,
  never summarized, so Olisar knows "what's the latest" without growing memory.

The bot's context-channels worker keeps ``channel_context_item`` in sync with
Discord; the helpers here store snapshots and render them for the reply context.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import (
    ChannelAllowlist,
    ChannelContextItem,
    ChannelMode,
    CONTEXT_MODES,
    GuildChannelInfo,
)
from olisar.memory.writer import opted_out
from olisar.message_links import ChannelFilter

FEED_KEEP = 3              # feed channels: keep only the last N messages
RESOURCE_KEEP = 50        # resource channels: rolling reference window
RESOURCE_CHAR_CAP = 3500  # cap a single resource block injected into context
FEED_RENDER = 3           # feed messages rendered into context


async def replace_context_items(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    channel_name: str,
    items: list[dict],
) -> None:
    """Replace a channel's stored snapshot with ``items`` (oldest-first), each a
    dict of ``{message_id, author_id, author_name, content}``. Wholesale replace keeps the
    snapshot honest about edits and deletions in the source channel.

    A post by someone who asked not to be remembered here is left out, as it would be from
    conversation memory and the search index (olisar.memory.writer.opted_out)."""
    await session.execute(
        delete(ChannelContextItem).where(ChannelContextItem.channel_id == channel_id)
    )
    authors = {it.get("author_id") for it in items if it.get("author_id")}
    unwanted = {a for a in authors if await opted_out(session, a, guild_id)}
    for it in items:
        content = (it.get("content") or "").strip()
        if not content or it.get("author_id") in unwanted:
            continue
        session.add(
            ChannelContextItem(
                guild_id=guild_id,
                channel_id=channel_id,
                channel_name=channel_name,
                author_name=(it.get("author_name") or "")[:128],
                content=content,
                message_id=it.get("message_id"),
            )
        )


async def _items_for(
    session: AsyncSession, guild_id: int, channel_id: int
) -> list[ChannelContextItem]:
    """A channel's stored snapshot. Scoped by guild as well as channel, so an allowlist row
    naming another server's channel can't bring that server's snapshot into this one's
    context."""
    return list(
        (
            await session.scalars(
                select(ChannelContextItem)
                .where(
                    ChannelContextItem.guild_id == guild_id,
                    ChannelContextItem.channel_id == channel_id,
                )
                .order_by(ChannelContextItem.id.asc())
            )
        ).all()
    )


async def _context_channels(
    session: AsyncSession, guild_id: int, modes: tuple[ChannelMode, ...]
) -> list[ChannelAllowlist]:
    return list(
        (
            await session.scalars(
                select(ChannelAllowlist).where(
                    ChannelAllowlist.guild_id == guild_id,
                    ChannelAllowlist.mode.in_(modes),
                )
            )
        ).all()
    )


async def channel_context_blocks(
    session: AsyncSession, guild_id: int, *, readable: ChannelFilter | None = None
) -> list[str]:
    """Rendered background-context blocks for every resource + feed channel, or with
    ``readable`` (the asker's ChannelFilter, olisar.message_links) only the ones it lets
    through. A staff #announcements is a feed channel too, and without the filter its
    latest posts went into every member's reply."""
    context_channels = await _context_channels(session, guild_id, CONTEXT_MODES)
    if readable is not None and context_channels:
        ok = await readable({ch.channel_id for ch in context_channels})
        context_channels = [ch for ch in context_channels if ch.channel_id in ok]
    topics = await _channel_topics(session, guild_id, [ch.channel_id for ch in context_channels])
    blocks: list[str] = []
    for ch in context_channels:
        items = await _items_for(session, guild_id, ch.channel_id)
        if not items:
            continue
        name = items[-1].channel_name or str(ch.channel_id)
        topic = topics.get(ch.channel_id, "")
        if ch.mode == ChannelMode.resource:
            text = "\n".join(it.content for it in items)
            if len(text) > RESOURCE_CHAR_CAP:
                text = text[:RESOURCE_CHAR_CAP].rstrip() + " …"
            # The channel's own topic/description carries its intent alongside content.
            tnote = f' — its topic is "{topic}"' if topic else ""
            blocks.append(f"Reference from #{name} (a server resource channel{tnote}):\n{text}")
        else:  # feed
            recent = items[-FEED_RENDER:]
            lines = "\n".join(f"- {it.content}" for it in recent)
            tnote = f' (topic: "{topic}")' if topic else ""
            blocks.append(f"Latest in #{name}{tnote} (most recent last):\n{lines}")
    return blocks


async def _channel_topics(
    session: AsyncSession, guild_id: int, channel_ids: list[int]
) -> dict[int, str]:
    """Map channel_id -> topic from this guild's synced roster (empty when unset)."""
    if not channel_ids:
        return {}
    rows = (
        await session.execute(
            select(GuildChannelInfo.channel_id, GuildChannelInfo.topic).where(
                GuildChannelInfo.guild_id == guild_id,
                GuildChannelInfo.channel_id.in_(channel_ids),
            )
        )
    ).all()
    return {cid: (topic or "") for cid, topic in rows}


async def resource_reference(session: AsyncSession, guild_id: int) -> str:
    """Concatenated resource-channel content, for persona synthesis (so role
    definitions and the like can be tied to the members who hold them)."""
    parts: list[str] = []
    total = 0
    for ch in await _context_channels(session, guild_id, (ChannelMode.resource,)):
        items = await _items_for(session, guild_id, ch.channel_id)
        if not items:
            continue
        name = items[-1].channel_name or str(ch.channel_id)
        text = "\n".join(it.content for it in items)
        if len(text) > RESOURCE_CHAR_CAP:
            text = text[:RESOURCE_CHAR_CAP].rstrip() + " …"
        parts.append(f"#{name}:\n{text}")
        total += len(text)
        if total > RESOURCE_CHAR_CAP * 2:
            break
    return "\n\n".join(parts)
