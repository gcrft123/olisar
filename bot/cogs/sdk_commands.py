"""Dynamic slash commands declared by SDK extensions.

User extensions can declare slash commands in their manifest; this cog builds them at
runtime, registers them on the bot's command tree, gates each on the extension being
enabled for the guild, and routes the invocation into the sandbox. Interaction flows
(reply, modal forms, buttons) round-trip through a bridge so the author's `await
interaction.modal(...)` etc. drive the real Discord UI.

It is deliberately self-contained and defensive: building/registering one command can't
break the others or the bot's built-in commands, and a re-sync is debounced so toggling
extensions doesn't hammer Discord's command-sync rate limit. Live verification needs a
running bot; the sandbox/bridge plumbing is unit-tested without Discord.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import ExtensionPackage
from olisar.extensions import is_enabled
from olisar.sandbox import SandboxError, run_command, run_component

log = logging.getLogger("olisar.cogs.sdk_commands")

_OPT_PY: dict[str, Any] = {
    "string": str, "integer": int, "number": float, "boolean": bool,
    "user": discord.Member, "channel": discord.abc.GuildChannel,
}
_COMPONENT_TIMEOUT = 300.0
_MODAL_TIMEOUT = 600.0
_COMPONENT_COOLDOWN = 1.5  # seconds between one user's clicks on the same message

# Persistent-component custom_id wire format: "<oleb|oles>|<ext_key>|<handlerId>|<arg>".
# The host always mints this (authors only choose handlerId + arg), so the ext_key is
# trustworthy: a click can only ever route to the extension that owns it. Distinct
# prefixes give buttons vs selects their own DynamicItem template (no ambiguity).
_CID_BTN = r"oleb\|(?P<ext>[a-z0-9_]{1,64})\|(?P<h>[a-z0-9_]{1,32})\|(?P<arg>.{0,40})"
_CID_SEL = r"oles\|(?P<ext>[a-z0-9_]{1,64})\|(?P<h>[a-z0-9_]{1,32})\|(?P<arg>.{0,40})"


def _ser(value: Any) -> Any:
    """Make an option value JSON-safe for the sandbox (ids for Discord objects)."""
    if isinstance(value, (discord.Member, discord.User, discord.abc.GuildChannel, discord.Role)):
        return str(value.id)
    return value


def _to_embed(spec: dict | None) -> discord.Embed | None:
    if not spec:
        return None
    inner = spec.get("spec", spec) if isinstance(spec, dict) else {}
    e = discord.Embed(
        title=inner.get("title"), description=inner.get("description"),
        url=inner.get("url"), color=inner.get("color"),
    )
    for f in inner.get("fields", []) or []:
        e.add_field(name=f.get("name", ""), value=f.get("value", ""), inline=bool(f.get("inline")))
    if inner.get("footer"):
        e.set_footer(text=inner["footer"])
    if inner.get("thumbnail"):
        e.set_thumbnail(url=inner["thumbnail"])
    if inner.get("image"):
        e.set_image(url=inner["image"])
    return e


def _style(name: str | None) -> discord.ButtonStyle:
    return getattr(discord.ButtonStyle, name or "secondary", discord.ButtonStyle.secondary)


def _build_view(components: list, *, ext_key: str,
                bridge: "_DiscordBridge | None" = None) -> discord.ui.View | None:
    """Build a View from SDK component specs.

    A component with ``handlerId`` is PERSISTENT — rendered with a `_SdkButton`/`_SdkSelect`
    whose custom_id is routed by a global DynamicItem template, so it keeps working for
    everyone and across restarts. A legacy ``customId`` component is a one-shot bound to
    ``bridge``'s ``awaitComponent`` (300s)."""
    if not components:
        return None
    persistent = any(c.get("handlerId") for c in components)
    view = discord.ui.View(timeout=None if persistent else _COMPONENT_TIMEOUT)
    for comp in components:
        kind = comp.get("kind")
        hid = comp.get("handlerId")
        if hid:  # persistent (routed by ext_key + handler)
            arg = str(comp.get("arg") or "")
            prefix = "oles" if kind == "select" else "oleb"
            if len(f"{prefix}|{ext_key}|{hid}|{arg}") > 100:
                raise ValueError(
                    "component custom_id is too long (>100 chars) — shorten handlerId/arg, "
                    "or store the payload in host.kv and pass a short key as arg"
                )
            if kind == "select":
                view.add_item(_SdkSelect(ext_key, hid, arg, comp.get("placeholder"), comp.get("options") or []))
            else:
                view.add_item(_SdkButton(ext_key, hid, arg, str(comp.get("label", "OK")), _style(comp.get("style"))))
        elif bridge is not None:  # legacy one-shot (awaitComponent)
            cid = str(comp.get("customId", "btn"))
            if kind == "select":
                sel = discord.ui.Select(
                    custom_id=cid, placeholder=comp.get("placeholder"),
                    options=[discord.SelectOption(label=o.get("label", o["value"]), value=o["value"])
                             for o in comp.get("options", [])],
                )
                sel.callback = bridge._component_cb(cid)
                view.add_item(sel)
            else:
                btn = discord.ui.Button(label=comp.get("label", "OK"), custom_id=cid, style=_style(comp.get("style")))
                btn.callback = bridge._component_cb(cid)
                view.add_item(btn)
    return view


class _SdkModal(discord.ui.Modal):
    def __init__(self, spec: dict, future: asyncio.Future):
        super().__init__(title=str(spec.get("title", "Form"))[:45], timeout=_MODAL_TIMEOUT)
        self._future = future
        self._ids: list[str] = []
        for field in (spec.get("fields") or [])[:5]:  # Discord allows up to 5
            fid = str(field.get("id", "field"))
            self._ids.append(fid)
            self.add_item(discord.ui.TextInput(
                label=str(field.get("label", fid))[:45],
                required=bool(field.get("required", False)),
                style=(discord.TextStyle.paragraph if field.get("style") == "paragraph"
                       else discord.TextStyle.short),
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        values = {fid: self.children[i].value for i, fid in enumerate(self._ids)}
        if not self._future.done():
            self._future.set_result(values)


class _DiscordBridge:
    """Implements the sandbox DiscordBridge protocol for one slash interaction.

    No auto-defer: the handler's first action is the initial interaction response, so
    `modal(...)` can open as the first response (Discord requires that). Authors should
    make their first reply/modal within ~3s and do slow work afterwards.
    """

    def __init__(self, interaction: discord.Interaction, ext_key: str):
        self.it = interaction
        self.ext_key = ext_key
        self._responded = False
        self._component_future: asyncio.Future | None = None

    def _component_cb(self, custom_id: str):
        async def cb(interaction: discord.Interaction):
            await interaction.response.defer()
            values = getattr(interaction.data, "get", lambda *_: None)("values") if interaction.data else None
            if self._component_future and not self._component_future.done():
                self._component_future.set_result({"customId": custom_id, "values": values})
        return cb

    def _unpack(self, payload: Any) -> dict:
        if isinstance(payload, str):
            return {"content": payload}
        return payload or {}

    async def reply(self, payload: Any) -> None:
        p = self._unpack(payload)
        view = _build_view(p.get("components") or [], ext_key=self.ext_key, bridge=self)
        kwargs = dict(
            content=p.get("content"), embed=_to_embed(p.get("embed")),
            ephemeral=bool(p.get("ephemeral")),
        )
        if view is not None:
            kwargs["view"] = view
        if not self._responded:
            self._responded = True
            await self.it.response.send_message(**{k: v for k, v in kwargs.items() if v is not None})
        else:
            kwargs.pop("ephemeral", None)
            await self.it.followup.send(**{k: v for k, v in kwargs.items() if v is not None})

    async def follow_up(self, payload: Any) -> None:
        if not self._responded:
            return await self.reply(payload)
        p = self._unpack(payload)
        view = _build_view(p.get("components") or [], ext_key=self.ext_key, bridge=self)
        kwargs = {"content": p.get("content"), "embed": _to_embed(p.get("embed"))}
        if view is not None:
            kwargs["view"] = view
        await self.it.followup.send(**{k: v for k, v in kwargs.items() if v is not None})

    async def modal(self, spec: Any) -> dict:
        if self._responded:
            raise RuntimeError("a modal must be the command's first response")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._responded = True
        await self.it.response.send_modal(_SdkModal(spec or {}, fut))
        return await asyncio.wait_for(fut, timeout=_MODAL_TIMEOUT)

    async def await_component(self, opts: Any) -> dict:
        loop = asyncio.get_running_loop()
        self._component_future = loop.create_future()
        timeout = float((opts or {}).get("timeoutMs", _COMPONENT_TIMEOUT * 1000)) / 1000.0
        return await asyncio.wait_for(self._component_future, timeout=timeout)

    # update()/deferUpdate() are component-only; raise politely if a command tries them.
    async def update(self, payload: Any) -> None:
        raise RuntimeError("update() is only available from a persistent button/select handler")

    async def defer_update(self) -> None:
        raise RuntimeError("deferUpdate() is only available from a persistent button/select handler")

    async def send(self, channel_id: str, payload: Any) -> None:
        raise RuntimeError("host.discord.send is only available from an event handler — use reply()")


# ── Persistent components (buttons/selects that survive restarts) ─────────────
# A click routes via a global DynamicItem template to the owning extension; no
# per-message view is rebuilt on restart. State + lifecycle live in the extension's
# host.kv. Edits to one message are serialized so concurrent clicks don't race the KV.

_locks: dict[tuple[int, int], asyncio.Lock] = {}
_clicks: dict[tuple[int, int], float] = {}


def _message_lock(gid: int, mid: int) -> asyncio.Lock:
    return _locks.setdefault((gid, mid), asyncio.Lock())


async def _safe_ephemeral(it: discord.Interaction, msg: str) -> None:
    try:
        if it.response.is_done():
            await it.followup.send(msg, ephemeral=True)
        else:
            await it.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


class _ComponentBridge:
    """DiscordBridge for one persistent-component click: reply ephemerally to the
    clicker, or edit the source message in place (live tally / attendee list)."""

    def __init__(self, interaction: discord.Interaction, ext_key: str):
        self.it = interaction
        self.ext_key = ext_key
        self._responded = False

    def _unpack(self, payload: Any) -> dict:
        return {"content": payload} if isinstance(payload, str) else (payload or {})

    async def reply(self, payload: Any) -> None:
        p = self._unpack(payload)
        view = _build_view(p.get("components") or [], ext_key=self.ext_key)
        kwargs = {"content": p.get("content"), "embed": _to_embed(p.get("embed"))}
        if view is not None:
            kwargs["view"] = view
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if not self._responded:
            self._responded = True
            await self.it.response.send_message(ephemeral=True, **kwargs)
        else:
            await self.it.followup.send(ephemeral=True, **kwargs)

    async def update(self, payload: Any) -> None:
        p = self._unpack(payload)
        kwargs: dict = {}
        if p.get("content") is not None:
            kwargs["content"] = p["content"]
        if p.get("embed") is not None:
            kwargs["embed"] = _to_embed(p["embed"])
        if "components" in p:  # explicit (even []) replaces the view; [] clears the buttons
            kwargs["view"] = _build_view(p["components"], ext_key=self.ext_key) or discord.ui.View()
        # omitting `components` leaves the existing buttons untouched (live tally edits)
        if not self._responded:
            self._responded = True
            await self.it.response.edit_message(**kwargs)
        else:
            await self.it.message.edit(**kwargs)

    async def defer_update(self) -> None:
        self._responded = True
        await self.it.response.defer()

    async def follow_up(self, payload: Any) -> None:
        await self.reply(payload)

    async def modal(self, spec: Any) -> dict:
        raise RuntimeError("a modal can't open from a button click")

    async def await_component(self, opts: Any) -> dict:
        raise RuntimeError("awaitComponent isn't available in a button handler")

    async def send(self, channel_id: str, payload: Any) -> None:
        raise RuntimeError("host.discord.send is only available from an event handler")


async def _dispatch_component(it: discord.Interaction, ext_key: str, handler_id: str, arg: str) -> None:
    gid = it.guild_id
    mid = it.message.id if it.message else 0
    if gid is None:
        return await _safe_ephemeral(it, "buttons only work inside a server.")
    ck = (it.user.id, mid)
    now = time.monotonic()
    if now - _clicks.get(ck, 0.0) < _COMPONENT_COOLDOWN:
        return await _safe_ephemeral(it, "one sec — you're clicking too fast.")
    _clicks[ck] = now
    async with session_scope() as session:
        if not await is_enabled(session, gid, ext_key):
            return await _safe_ephemeral(it, "that extension is turned off here.")
        pkg = await session.get(ExtensionPackage, ext_key)
        if pkg is None:
            return await _safe_ephemeral(it, "that extension is no longer installed.")
        compiled, perms = pkg.compiled_js, list(pkg.permissions or [])
        trusted = (pkg.origin or "local") == "local"
    ctx = {
        "customId": handler_id, "arg": arg,
        "values": (it.data or {}).get("values"),
        "guildId": str(gid), "channelId": str(it.channel_id), "messageId": str(mid),
        "userId": str(it.user.id), "displayName": it.user.display_name,
    }
    bridge = _ComponentBridge(it, ext_key)
    async with _message_lock(gid, mid):  # serialize edits + KV read-modify-write
        try:
            async with session_scope() as session:
                await run_component(
                    ext_key=ext_key, compiled_js=compiled, permissions=perms,
                    handler_name=handler_id, component_ctx=ctx, guild_id=gid,
                    session=session, discord=bridge, trusted=trusted,
                )
        except Exception:  # noqa: BLE001
            log.exception("sdk component %s/%s failed", ext_key, handler_id)
            await _safe_ephemeral(it, "that action hit an error.")
        finally:
            if not it.response.is_done():  # never leave the click hanging
                try:
                    await it.response.defer()
                except discord.HTTPException:
                    pass


class _SdkButton(discord.ui.DynamicItem[discord.ui.Button], template=_CID_BTN):
    def __init__(self, ext_key: str, handler_id: str, arg: str,
                 label: str = "", style: discord.ButtonStyle = discord.ButtonStyle.secondary):
        self.ext_key, self.handler_id, self.arg = ext_key, handler_id, arg
        super().__init__(discord.ui.Button(
            label=(label or "OK")[:80], style=style,
            custom_id=f"oleb|{ext_key}|{handler_id}|{arg}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["ext"], match["h"], match["arg"], label=item.label or "", style=item.style)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _dispatch_component(interaction, self.ext_key, self.handler_id, self.arg)


class _SdkSelect(discord.ui.DynamicItem[discord.ui.Select], template=_CID_SEL):
    def __init__(self, ext_key: str, handler_id: str, arg: str,
                 placeholder: str | None = None, options: list | None = None):
        self.ext_key, self.handler_id, self.arg = ext_key, handler_id, arg
        opts = [discord.SelectOption(label=o.get("label", o["value"]), value=o["value"])
                for o in (options or [])] or [discord.SelectOption(label="—", value="—")]
        super().__init__(discord.ui.Select(
            custom_id=f"oles|{ext_key}|{handler_id}|{arg}", placeholder=placeholder, options=opts))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["ext"], match["h"], match["arg"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await _dispatch_component(interaction, self.ext_key, self.handler_id, self.arg)


def _make_command(ext_key: str, cmd: dict) -> app_commands.Command:
    name = str(cmd["name"])[:32]
    description = (str(cmd.get("description") or name))[:100]
    options = cmd.get("options") or []

    async def run(interaction: discord.Interaction, opts: dict) -> None:
        gid = interaction.guild_id
        async with session_scope() as session:
            if gid is None or not await is_enabled(session, gid, ext_key):
                await interaction.response.send_message(
                    "that extension is off — an admin can enable it in the dashboard.", ephemeral=True)
                return
            pkg = await session.get(ExtensionPackage, ext_key)
            if pkg is None:
                await interaction.response.send_message("that extension is no longer installed.", ephemeral=True)
                return
            compiled, perms = pkg.compiled_js, list(pkg.permissions or [])
            # First-party extensions may use host secrets; imported/marketplace can't.
            trusted = (pkg.origin or "local") == "local"
        data = {
            "options": {k: _ser(v) for k, v in opts.items()},
            "guildId": str(gid), "channelId": str(interaction.channel_id),
            "userId": str(interaction.user.id), "displayName": interaction.user.display_name,
        }
        bridge = _DiscordBridge(interaction, ext_key)
        try:
            async with session_scope() as session:
                await run_command(
                    ext_key=ext_key, compiled_js=compiled, permissions=perms,
                    command_name=name, interaction_data=data, guild_id=gid,
                    session=session, discord=bridge, trusted=trusted,
                )
        except (SandboxError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            log.exception("sdk command %s/%s failed", ext_key, name)
            msg = "that command hit an error." if isinstance(exc, SandboxError) else "that command timed out or failed."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except discord.HTTPException:
                pass

    # Build a callback whose signature carries the declared options so discord.py
    # generates the slash-command options, then funnels them into run().
    params = [inspect.Parameter("interaction", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                annotation=discord.Interaction)]
    described: dict[str, str] = {}
    for o in options:
        oname = str(o["name"])
        py = _OPT_PY.get(str(o.get("type") or "string").lower(), str)
        required = bool(o.get("required", False))
        ann = py if required else Optional[py]
        kw: dict = {"kind": inspect.Parameter.POSITIONAL_OR_KEYWORD, "annotation": ann}
        if not required:
            kw["default"] = None
        params.append(inspect.Parameter(oname, **kw))
        described[oname] = (str(o.get("description") or oname))[:100]
    sig = inspect.Signature(params)

    async def callback(interaction: discord.Interaction, *args, **kwargs) -> None:
        bound = sig.bind(interaction, *args, **kwargs)
        bound.apply_defaults()
        opts = {k: v for k, v in bound.arguments.items() if k != "interaction"}
        await run(interaction, opts)

    callback.__signature__ = sig  # type: ignore[attr-defined]
    callback.__annotations__ = {p.name: p.annotation for p in params} | {"return": None}

    command = app_commands.Command(name=name, description=description, callback=callback)
    if described:
        try:
            app_commands.describe(**described)(command)
        except Exception:
            log.debug("could not attach descriptions for %s", name)
    if cmd.get("guildOnly", True):
        command.guild_only = True
    return command


class SdkCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._registered: set[str] = set()
        self._resync_task: asyncio.Task | None = None
        self._dynamic_registered = False

    async def _load_command_specs(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        async with session_scope() as session:
            for pkg in (await session.scalars(select(ExtensionPackage))).all():
                for cmd in (pkg.manifest or {}).get("commands", []) or []:
                    if cmd.get("name"):
                        out.append((pkg.key, cmd))
        return out

    async def rebuild(self) -> None:
        """Re-derive all SDK commands and sync them to every guild. Idempotent."""
        for name in list(self._registered):
            try:
                self.bot.tree.remove_command(name)
            except Exception:
                pass
        self._registered.clear()
        try:
            specs = await self._load_command_specs()
        except Exception:
            log.exception("loading SDK command specs failed")
            return
        for ext_key, cmd in specs:
            try:
                command = _make_command(ext_key, cmd)
                self.bot.tree.add_command(command, override=True)
                self._registered.add(command.name)
            except Exception:
                log.exception("building SDK command %s/%s failed", ext_key, cmd.get("name"))
        for guild in self.bot.guilds:
            try:
                self.bot.tree.copy_global_to(guild=guild)
                await self.bot.tree.sync(guild=guild)
            except Exception:
                log.exception("SDK command sync failed for guild %s", guild.id)
        log.info("SDK commands rebuilt: %d command(s)", len(self._registered))

    def request_resync(self) -> None:
        """Debounced rebuild, safe to call from the API after an authoring save."""
        async def _debounced() -> None:
            try:
                await asyncio.sleep(1.5)
                await self.rebuild()
            except asyncio.CancelledError:
                pass
        if self._resync_task and not self._resync_task.done():
            self._resync_task.cancel()
        self._resync_task = self.bot.loop.create_task(_debounced())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._dynamic_registered:  # route persistent button/select clicks
            try:
                self.bot.add_dynamic_items(_SdkButton, _SdkSelect)
                self._dynamic_registered = True
            except Exception:
                log.exception("registering SDK persistent-component templates failed")
        await self.rebuild()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SdkCommands(bot))
