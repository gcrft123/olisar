"""Context-channel worker: keeps resource & feed channels in sync with Discord.

Two jobs, both off the reply path:

* **Roster sync** — mirror every text channel into ``guild_channel_info`` so the
  dashboard can offer a real channel picker (the API has no gateway of its own).
* **Snapshot sync** — for each channel an admin set to ``resource`` or ``feed``,
  pull the latest messages from Discord and replace its stored snapshot. Resource
  channels keep a rolling reference window; feed channels keep just the last few.
  Bot/webhook posts are included (announcements are often automated); only
  Olisar's own messages are skipped.

Doing this on a loop (rather than via on_message) means a channel newly flagged
in the dashboard gets backfilled automatically, and edits/deletions in the source
channel propagate — no special-casing in the conversation cog.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks
from sqlalchemy import select

from bot.content import message_text
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import (
    ChannelAllowlist,
    ChannelMode,
    CONTEXT_MODES,
    GuildChannelInfo,
    GuildRole,
    utcnow,
)
from olisar.memory.channels import FEED_KEEP, RESOURCE_KEEP, replace_context_items

log = logging.getLogger("olisar.context_channels")


class ContextChannels(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tick.start()

    def cog_unload(self) -> None:
        self.tick.cancel()

    async def _sync_roster(self, guild: discord.Guild) -> None:
        """Mirror the guild's text + forum channels into guild_channel_info. Forums
        are configurable like text channels (their posts inherit the mode); thread
        rows are added by the search backfill, so they're left untouched here."""
        live = [(ch, "text") for ch in guild.text_channels]
        live += [(ch, "forum") for ch in guild.forums]
        async with session_scope() as session:
            existing = {
                r.channel_id: r
                for r in (
                    await session.scalars(
                        select(GuildChannelInfo).where(GuildChannelInfo.guild_id == guild.id)
                    )
                ).all()
            }
            live_ids = set()
            for ch, kind in live:
                live_ids.add(ch.id)
                category = ch.category.name if ch.category else ""
                topic = (getattr(ch, "topic", "") or "")[:1024]
                row = existing.get(ch.id)
                if row is None:
                    session.add(
                        GuildChannelInfo(
                            channel_id=ch.id,
                            guild_id=guild.id,
                            name=ch.name,
                            category=category,
                            topic=topic,
                            position=ch.position,
                            kind=kind,
                        )
                    )
                else:
                    row.name = ch.name
                    row.category = category
                    row.topic = topic
                    row.position = ch.position
                    row.kind = kind
                    row.updated_at = utcnow()
            # Drop text/forum channels that no longer exist; never drop thread rows.
            for cid, row in existing.items():
                if row.kind != "thread" and cid not in live_ids:
                    await session.delete(row)

    async def _sync_roles(self, guild: discord.Guild) -> None:
        """Mirror the guild's roles into guild_role for the dashboard access picker."""
        async with session_scope() as session:
            existing = {
                r.role_id: r
                for r in (
                    await session.scalars(
                        select(GuildRole).where(GuildRole.guild_id == guild.id)
                    )
                ).all()
            }
            live_ids = set()
            for role in guild.roles:
                if role.is_default():
                    continue  # skip @everyone — it's everyone, never a gate
                live_ids.add(role.id)
                color = f"#{role.color.value:06x}" if role.color.value else ""
                row = existing.get(role.id)
                if row is None:
                    session.add(
                        GuildRole(
                            role_id=role.id,
                            guild_id=guild.id,
                            name=role.name,
                            color=color,
                            position=role.position,
                        )
                    )
                else:
                    row.name = role.name
                    row.color = color
                    row.position = role.position
                    row.updated_at = utcnow()
            for rid, row in existing.items():
                if rid not in live_ids:
                    await session.delete(row)

    async def _sync_snapshots(self, guild: discord.Guild) -> None:
        """Refresh stored snapshots for every resource/feed channel."""
        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(ChannelAllowlist).where(
                        ChannelAllowlist.guild_id == guild.id,
                        ChannelAllowlist.mode.in_(CONTEXT_MODES),
                    )
                )
            ).all()
            targets = [(r.channel_id, r.mode) for r in rows]

        for channel_id, mode in targets:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            keep = FEED_KEEP if mode == ChannelMode.feed else RESOURCE_KEEP
            try:
                fetched = [
                    (m, message_text(m))
                    async for m in channel.history(limit=keep)
                    if m.author.id != self.bot.user.id
                ]
            except discord.Forbidden:
                continue  # no read-history permission; skip quietly
            except Exception:
                log.exception("failed to read history for #%s", channel_id)
                continue
            fetched = [(m, body) for m, body in fetched if body.strip()]
            fetched.reverse()  # oldest-first
            items = [
                {
                    "message_id": m.id,
                    "author_name": m.author.display_name,
                    "content": body,
                }
                for m, body in fetched
            ]
            async with session_scope() as session:
                await replace_context_items(
                    session,
                    guild_id=guild.id,
                    channel_id=channel_id,
                    channel_name=channel.name,
                    items=items,
                )

    @tasks.loop(seconds=90)
    async def tick(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._sync_roster(guild)
                await self._sync_roles(guild)
                await self._sync_snapshots(guild)
            except Exception:
                log.exception("context-channels tick failed for guild %s", guild.id)

    @tick.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ContextChannels(bot))
