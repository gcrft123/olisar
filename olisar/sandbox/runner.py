"""Async entry points for running sandboxed extensions.

The extension runs in a sandbox host process (see ``engine``), and each invocation is
driven from a worker thread (a shared pool) that waits on it. Capability requests the
extension makes are bridged back to the *calling* asyncio loop with
``run_coroutine_threadsafe`` so DB sessions and httpx run where they belong; the worker
blocks on the result. The caller awaits the whole thing, so it never blocks the loop on
network-bound extensions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from olisar.message_links import ChannelFilter, channel_filter
from olisar.sandbox import capabilities, engine
from olisar.sandbox.capabilities import DiscordBridge, Invocation
from olisar.sandbox.engine import SandboxError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from olisar.tools import ToolContext

log = logging.getLogger("olisar.sandbox.runner")

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ext-sandbox")


async def _invoke(
    inv: Invocation, compiled_js: str, kind: str, name: str, payload: dict, **limits,
) -> Any:
    loop = asyncio.get_running_loop()
    wall_seconds = limits.get("wall_seconds", engine.TOOL_WALL_SECONDS)

    def job() -> Any:
        deadline = time.monotonic() + wall_seconds

        # A host call counts against the run's wall budget like the extension's own time
        # does: a slow one (a drip-fed fetch) is cancelled when the budget runs out, rather
        # than holding the reply and this thread for the longest budget any run gets.
        def perform(cap: str, method: str, args: list) -> Any:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SandboxError("extension exceeded its time budget")
            fut = asyncio.run_coroutine_threadsafe(
                capabilities.dispatch(inv, cap, method, args), loop
            )
            try:
                return fut.result(timeout=left)
            except TimeoutError:
                if not fut.done():
                    fut.cancel()
                    raise SandboxError("extension exceeded its time budget") from None
                return fut.result()  # it finished after all, or timed out on its own terms

        return engine.invoke(compiled_js, kind, name, payload, perform, **limits)

    return await loop.run_in_executor(_pool, job)


async def extract_manifest(compiled_js: str) -> dict:
    """Compile-check: run the extension once and return its declarative manifest."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, engine.extract_manifest, compiled_js)


def _as_id(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _invoker_can_read(
    discord: DiscordBridge | None, *, guild_id: int, user_id: Any, here: Any,
) -> ChannelFilter:
    """Which channels the member who ran a command or clicked a component can open: the
    check message search uses (``BotActions.readable_channels``), asked through the bot
    that received the interaction. Without one to ask, only the channel it ran in passes,
    so a doubt refuses rather than lets through."""
    client = getattr(getattr(discord, "it", None), "client", None)
    actions = None
    if client is not None:
        from bot.actions import BotActions

        actions = BotActions(client)
    return channel_filter(
        actions, guild_id=guild_id, requester_id=_as_id(user_id), here=_as_id(here),
    )


def _share_blobs(inv: Invocation, discord: DiscordBridge | None) -> None:
    """Point the bridge at the invocation's blob store so FileOut blobId can resolve."""
    if discord is not None:
        discord.blobs = inv.blobs  # type: ignore[attr-defined]


class _ToolBridge:
    """Adapts a ToolContext's DiscordActions to the sandbox DiscordBridge so a tool can post
    to a channel (with components) via host.discord.send. Only ``send`` is available from a
    tool — the interaction-bound methods raise. ``trusted`` decides the mention policy on the
    post (third-party posts can't ping)."""

    def __init__(self, ctx: "ToolContext", ext_key: str, trusted: bool) -> None:
        self._ctx = ctx
        self._ext_key = ext_key
        self._trusted = trusted
        self.blobs: dict = {}

    async def send(self, channel_id: str, payload: Any) -> Any:
        p = {"content": payload} if isinstance(payload, str) else (payload or {})
        ch = channel_id
        if not ch or str(ch) == str(self._ctx.channel_id):
            ch = None  # the current channel — post_components uses its live channel object
        return await self._ctx.actions.post_components(
            channel=ch, content=p.get("content"), embed=p.get("embed"),
            components=p.get("components"), files=p.get("files"),
            blobs=self.blobs, ext_key=self._ext_key,
            home_guild_id=self._ctx.cfg_guild, trusted=self._trusted,
        )

    async def reply(self, payload: Any) -> None:
        raise RuntimeError("a tool posts with host.discord.send(channelId, …), not reply()")

    async def follow_up(self, payload: Any) -> None:
        raise RuntimeError("followUp() isn't available from a tool")

    async def modal(self, spec: Any) -> dict:
        raise RuntimeError("a modal can't open from a tool")

    async def await_component(self, opts: Any) -> dict:
        raise RuntimeError("awaitComponent isn't available from a tool")

    async def update(self, payload: Any) -> None:
        raise RuntimeError("update() isn't available from a tool")

    async def defer_update(self) -> None:
        raise RuntimeError("deferUpdate() isn't available from a tool")

    async def fetch_attachment_bytes(
        self, option_name: str,
    ) -> tuple[bytes, str, str | None]:
        raise RuntimeError(
            "host.files.read/ingest is only available from a slash-command handler"
        )


async def run_tool(
    *, ext_key: str, compiled_js: str, permissions: list[str],
    tool_name: str, args: dict, ctx: "ToolContext", trusted: bool = False,
) -> str:
    """Run a sandboxed LLM tool; always returns a string for the model."""
    # A tool can post to a channel via host.discord.send when Discord actions are available
    # (the live reply path; not the dashboard sandbox, where actions is None).
    bridge = _ToolBridge(ctx, ext_key, trusted) if getattr(ctx, "actions", None) is not None else None
    inv = Invocation(
        ext_key=ext_key, permissions=set(permissions or []),
        guild_id=ctx.cfg_guild, session=ctx.session, discord=bridge, trusted=trusted,
        readable=ctx.readable(), in_dm=bool(getattr(ctx, "is_dm", False)),
    )
    _share_blobs(inv, bridge)
    payload = {
        "args": args or {},
        "ctx": {
            "guildId": str(ctx.cfg_guild), "channelId": str(ctx.channel_id),
            "userId": str(ctx.user_id), "displayName": ctx.display_name,
        },
    }
    result = await _invoke(
        inv, compiled_js, "tool", tool_name, payload,
        cpu_seconds=engine.TOOL_CPU_SECONDS, wall_seconds=engine.TOOL_WALL_SECONDS,
    )
    if result is None:
        return f"the {tool_name} tool ran but returned nothing."
    return result if isinstance(result, str) else json.dumps(result)


async def run_command(
    *, ext_key: str, compiled_js: str, permissions: list[str],
    command_name: str, interaction_data: dict, guild_id: int,
    session: "AsyncSession", discord: DiscordBridge, trusted: bool = False,
) -> None:
    """Run a sandboxed slash command (its flow round-trips through ``discord``)."""
    inv = Invocation(
        ext_key=ext_key, permissions=set(permissions or []),
        guild_id=guild_id, session=session, discord=discord, trusted=trusted,
        readable=_invoker_can_read(
            discord, guild_id=guild_id, user_id=(interaction_data or {}).get("userId"),
            here=(interaction_data or {}).get("channelId"),
        ),
    )
    _share_blobs(inv, discord)
    await _invoke(
        inv, compiled_js, "command", command_name, {"interaction": interaction_data},
        cpu_seconds=engine.COMMAND_CPU_SECONDS, wall_seconds=engine.COMMAND_WALL_SECONDS,
        memory_bytes=engine.COMMAND_MEMORY_BYTES,
    )


async def run_component(
    *, ext_key: str, compiled_js: str, permissions: list[str],
    handler_name: str, component_ctx: dict, guild_id: int,
    session: "AsyncSession", discord: DiscordBridge, trusted: bool = False,
) -> None:
    """Run a persistent component (button/select click) handler. Quick: it updates
    state (host.kv) and edits the source message via ``discord``; bounded by the short
    component wall limit (it never waits on the user)."""
    inv = Invocation(
        ext_key=ext_key, permissions=set(permissions or []),
        guild_id=guild_id, session=session, discord=discord, trusted=trusted,
        readable=_invoker_can_read(
            discord, guild_id=guild_id, user_id=(component_ctx or {}).get("userId"),
            here=(component_ctx or {}).get("channelId"),
        ),
    )
    _share_blobs(inv, discord)
    await _invoke(
        inv, compiled_js, "component", handler_name, {"ctx": component_ctx},
        cpu_seconds=engine.COMMAND_CPU_SECONDS, wall_seconds=engine.COMPONENT_WALL_SECONDS,
    )


async def run_event(
    *, ext_key: str, compiled_js: str, permissions: list[str],
    handler_name: str, event_ctx: dict, guild_id: int,
    session: "AsyncSession", discord: DiscordBridge | None = None, trusted: bool = False,
) -> None:
    """Run a gateway-event handler (e.g. memberJoin). It never waits on a user, but may
    call the model once (host.generate) and post via host.discord.send, so it's bounded by
    the event wall limit rather than the long interactive one."""
    inv = Invocation(
        ext_key=ext_key, permissions=set(permissions or []),
        guild_id=guild_id, session=session, discord=discord, trusted=trusted,
    )
    _share_blobs(inv, discord)
    await _invoke(
        inv, compiled_js, "event", handler_name, {"ctx": event_ctx},
        cpu_seconds=engine.COMMAND_CPU_SECONDS, wall_seconds=engine.EVENT_WALL_SECONDS,
    )


async def run_on_enable(
    *, ext_key: str, compiled_js: str, permissions: list[str],
    session: "AsyncSession", guild_id: int, trusted: bool = False,
) -> None:
    """Run an extension's onEnable hook (idempotent seeding) on OFF->ON."""
    inv = Invocation(
        ext_key=ext_key, permissions=set(permissions or []),
        guild_id=guild_id, session=session, trusted=trusted,
    )
    await _invoke(
        inv, compiled_js, "onEnable", "onEnable", {"ctx": {"guildId": str(guild_id)}},
        cpu_seconds=engine.COMMAND_CPU_SECONDS, wall_seconds=engine.COMMAND_WALL_SECONDS,
    )


__all__ = [
    "extract_manifest", "run_tool", "run_command", "run_component", "run_event",
    "run_on_enable", "SandboxError", "DiscordBridge",
]
