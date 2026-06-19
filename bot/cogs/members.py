"""Roster & role awareness.

Gives Olisar a profile (display name + current roles) for *every* member, not
just people who have spoken — synced on startup and kept fresh on join / role
change. The synthesized per-user persona (built from message history) is a
separate Phase 2 job; this cog only maintains the factual roster.

Requires the privileged ``members`` intent (enabled in client.py + the portal).
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from olisar.db.engine import session_scope
from olisar.memory.writer import extract_roles, upsert_profile

log = logging.getLogger("olisar.members")


class Members(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _sync_member(self, member: discord.Member) -> None:
        if member.bot:
            return
        async with session_scope() as session:
            await upsert_profile(
                session,
                guild_id=member.guild.id,
                user_id=member.id,
                display_name=member.display_name,
                roles=extract_roles(member),
            )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Backfill the roster for every guild the bot is in, once connected."""
        for guild in self.bot.guilds:
            count = 0
            # `members` may be empty until the gateway finishes chunking; fetch_members
            # streams the full list (requires the members intent).
            try:
                async for member in guild.fetch_members(limit=None):
                    await self._sync_member(member)
                    count += 1
            except Exception:
                log.exception("member backfill failed for guild %s", guild.id)
                continue
            log.info("synced %d members for guild %s", count, guild.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._sync_member(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # Only re-sync when the role set actually changed.
        if {r.id for r in before.roles} != {r.id for r in after.roles}:
            await self._sync_member(after)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Members(bot))
