"""Async entry points for running sandboxed extensions.

The QuickJS context is synchronous and thread-affine, so each invocation runs on a
worker thread (a shared pool). Capability requests the extension makes are bridged
back to the *calling* asyncio loop with ``run_coroutine_threadsafe`` so DB sessions
and httpx run where they belong; the worker blocks on the result. The caller awaits
the whole thing, so it never blocks the loop on network-bound extensions.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from olisar.sandbox import capabilities, engine
from olisar.sandbox.capabilities import DiscordBridge, Invocation
from olisar.sandbox.engine import SandboxError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from olisar.tools import ToolContext

log = logging.getLogger("olisar.sandbox.runner")

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ext-sandbox")


async def _invoke(
    inv: Invocation, compiled_js: str, kind: str, name: str, payload: dict,
    *, perform_timeout: float | None = None, **limits,
) -> Any:
    loop = asyncio.get_running_loop()
    pt = engine.COMMAND_WALL_SECONDS if perform_timeout is None else perform_timeout

    def perform(cap: str, method: str, args: list) -> Any:
        fut = asyncio.run_coroutine_threadsafe(
            capabilities.dispatch(inv, cap, method, args), loop
        )
        return fut.result(timeout=pt)

    job = functools.partial(
        engine.invoke, compiled_js, kind, name, payload, perform, **limits
    )
    return await loop.run_in_executor(_pool, job)


async def extract_manifest(compiled_js: str) -> dict:
    """Compile-check: run the extension once and return its declarative manifest."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, engine.extract_manifest, compiled_js)


class _ToolBridge:
    """Adapts a ToolContext's DiscordActions to the sandbox DiscordBridge so a *trusted*
    tool can post to a channel (with components) via host.discord.send. Only ``send`` is
    available from a tool — the interaction-bound methods raise."""

    def __init__(self, ctx: "ToolContext", ext_key: str) -> None:
        self._ctx = ctx
        self._ext_key = ext_key

    async def send(self, channel_id: str, payload: Any) -> Any:
        p = {"content": payload} if isinstance(payload, str) else (payload or {})
        ch = channel_id
        if not ch or str(ch) == str(self._ctx.channel_id):
            ch = None  # the current channel — post_components uses its live channel object
        return await self._ctx.actions.post_components(
            channel=ch, content=p.get("content"), embed=p.get("embed"),
            components=p.get("components"), ext_key=self._ext_key,
            home_guild_id=self._ctx.cfg_guild,
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


async def run_tool(
    *, ext_key: str, compiled_js: str, permissions: list[str],
    tool_name: str, args: dict, ctx: "ToolContext", trusted: bool = False,
) -> str:
    """Run a sandboxed LLM tool; always returns a string for the model."""
    # A trusted tool can post to a channel via host.discord.send when Discord actions are
    # available (the live reply path; not the dashboard sandbox, where actions is None).
    bridge = _ToolBridge(ctx, ext_key) if getattr(ctx, "actions", None) is not None else None
    inv = Invocation(
        ext_key=ext_key, permissions=set(permissions or []),
        guild_id=ctx.cfg_guild, session=ctx.session, discord=bridge, trusted=trusted,
    )
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
    )
    await _invoke(
        inv, compiled_js, "command", command_name, {"interaction": interaction_data},
        cpu_seconds=engine.COMMAND_CPU_SECONDS, wall_seconds=engine.COMMAND_WALL_SECONDS,
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
    )
    await _invoke(
        inv, compiled_js, "component", handler_name, {"ctx": component_ctx},
        perform_timeout=engine.COMPONENT_WALL_SECONDS,
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
    await _invoke(
        inv, compiled_js, "event", handler_name, {"ctx": event_ctx},
        perform_timeout=engine.EVENT_WALL_SECONDS,
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
