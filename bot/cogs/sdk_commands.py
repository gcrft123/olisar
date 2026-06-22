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
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import ExtensionPackage
from olisar.extensions import is_enabled
from olisar.sandbox import SandboxError, run_command

log = logging.getLogger("olisar.cogs.sdk_commands")

_OPT_PY: dict[str, Any] = {
    "string": str, "integer": int, "number": float, "boolean": bool,
    "user": discord.Member, "channel": discord.abc.GuildChannel,
}
_COMPONENT_TIMEOUT = 300.0
_MODAL_TIMEOUT = 600.0


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


def _build_view(components: list, bridge: "_DiscordBridge") -> discord.ui.View | None:
    if not components:
        return None
    view = discord.ui.View(timeout=_COMPONENT_TIMEOUT)
    for comp in components:
        kind = comp.get("kind")
        if kind == "button":
            btn = discord.ui.Button(
                label=comp.get("label", "OK"), custom_id=comp.get("customId", "btn"),
                style=getattr(discord.ButtonStyle, comp.get("style", "secondary"), discord.ButtonStyle.secondary),
            )
            btn.callback = bridge._component_cb(comp.get("customId", "btn"))
            view.add_item(btn)
        elif kind == "select":
            sel = discord.ui.Select(
                custom_id=comp.get("customId", "sel"), placeholder=comp.get("placeholder"),
                options=[discord.SelectOption(label=o.get("label", o["value"]), value=o["value"])
                         for o in comp.get("options", [])],
            )
            sel.callback = bridge._component_cb(comp.get("customId", "sel"))
            view.add_item(sel)
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

    def __init__(self, interaction: discord.Interaction):
        self.it = interaction
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
        view = _build_view(p.get("components") or [], self)
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
        view = _build_view(p.get("components") or [], self)
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
        bridge = _DiscordBridge(interaction)
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
        await self.rebuild()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SdkCommands(bot))
