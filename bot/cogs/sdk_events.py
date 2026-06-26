"""Dispatch gateway events to SDK extensions that declare an event handler.

An extension's manifest can declare ``event_handlers`` (e.g. ``["memberJoin"]``); when the
matching Discord event fires we run that handler in the sandbox, passing a small data
context plus a bridge that can post to a channel (``host.discord.send``).

Event handlers are **trusted-only**: we dispatch to built-in / locally-authored extensions
only — never imported or marketplace code — mirroring how host secrets and host.generate
are gated. This replaces the bespoke Python welcome cog: Welcome is now a pure-SDK
extension that reacts to ``memberJoin`` (see olisar/extensions/sdk_builtins/welcome.js).
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands
from sqlalchemy import select

from bot.cogs.sdk_commands import _to_embed
from bot.replies import chunk_text
from olisar.db.engine import session_scope
from olisar.db.models import ExtensionPackage
from olisar.extensions import is_enabled
from olisar.sandbox import run_event

log = logging.getLogger("olisar.cogs.sdk_events")


class _EventBridge:
    """DiscordBridge for an event handler: post to a channel in the event's guild via
    ``host.discord.send``. Interaction-bound methods (reply/modal/buttons) aren't available."""

    def __init__(self, guild: discord.Guild, ext_key: str) -> None:
        self.guild = guild
        self.ext_key = ext_key

    @staticmethod
    def _unpack(payload: Any) -> dict:
        return {"content": payload} if isinstance(payload, str) else (payload or {})

    async def send(self, channel_id: str, payload: Any) -> None:
        try:
            cid = int(channel_id)
        except (TypeError, ValueError):
            return
        channel = self.guild.get_channel(cid)
        if channel is None:
            try:
                channel = await self.guild.fetch_channel(cid)
            except discord.HTTPException:
                channel = None
        if channel is None or not hasattr(channel, "send"):
            log.info("event send: channel %s not usable in guild %s", channel_id, self.guild.id)
            return
        p = self._unpack(payload)
        embed = _to_embed(p.get("embed"))
        content = p.get("content")
        # Chunk defensively under Discord's 2000-char limit; the embed rides the first chunk.
        chunks = chunk_text(str(content)) if content else [None]
        first = True
        for chunk in chunks:
            kwargs: dict = {}
            if chunk is not None:
                kwargs["content"] = chunk
            if first and embed is not None:
                kwargs["embed"] = embed
            if kwargs:
                await channel.send(**kwargs)
            first = False

    async def reply(self, payload: Any) -> None:
        raise RuntimeError("reply() isn't available in an event handler — use host.discord.send(channelId, …)")

    async def follow_up(self, payload: Any) -> None:
        raise RuntimeError("followUp() isn't available in an event handler")

    async def modal(self, spec: Any) -> dict:
        raise RuntimeError("a modal can't open from an event handler")

    async def await_component(self, opts: Any) -> dict:
        raise RuntimeError("awaitComponent isn't available in an event handler")

    async def update(self, payload: Any) -> None:
        raise RuntimeError("update() isn't available in an event handler")

    async def defer_update(self) -> None:
        raise RuntimeError("deferUpdate() isn't available in an event handler")


async def _targets_for(guild_id: int, event_name: str) -> list[tuple[str, str, list[str]]]:
    """Enabled, trusted (built-in/local) extensions whose manifest declares ``event_name``.
    Returns ``(key, compiled_js, permissions)`` tuples."""
    out: list[tuple[str, str, list[str]]] = []
    async with session_scope() as session:
        for pkg in (await session.scalars(select(ExtensionPackage))).all():
            if event_name not in ((pkg.manifest or {}).get("event_handlers") or []):
                continue
            if (pkg.origin or "local") != "local":
                continue  # event hooks are first-party only
            if not await is_enabled(session, guild_id, pkg.key):
                continue
            out.append((pkg.key, pkg.compiled_js, list(pkg.permissions or [])))
    return out


async def _dispatch(guild: discord.Guild, event_name: str, ctx: dict) -> None:
    """Run every matching extension's handler. One failing extension can't stop the others."""
    for ext_key, compiled, perms in await _targets_for(guild.id, event_name):
        bridge = _EventBridge(guild, ext_key)
        try:
            async with session_scope() as session:
                await run_event(
                    ext_key=ext_key, compiled_js=compiled, permissions=perms,
                    handler_name=event_name, event_ctx=ctx, guild_id=guild.id,
                    session=session, discord=bridge, trusted=True,
                )
        except Exception:  # noqa: BLE001
            log.exception("sdk event %s/%s failed", ext_key, event_name)


class SdkEvents(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or member.guild is None:
            return
        await _dispatch(member.guild, "memberJoin", {
            "event": "memberJoin",
            "guildId": str(member.guild.id),
            "member": {
                "id": str(member.id), "displayName": member.display_name,
                "username": member.name, "mention": member.mention, "bot": member.bot,
            },
        })


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SdkEvents(bot))
