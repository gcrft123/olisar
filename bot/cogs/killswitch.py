"""/killswitch — an operator's panic button to turn off a misbehaving extension now.

Flips an extension off for this server immediately (the pipeline reads enablement live,
so it stops on the next message — no restart). Gated to Manage-Server. `/killswitch all`
disables every enabled extension at once. This is the fast, in-Discord counterpart to the
console's per-extension toggle, for when something installed from the marketplace starts
behaving badly and you want it gone right away.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from olisar.audit import record_audit
from olisar.db.engine import session_scope
from olisar.db.models import ExtensionState
from olisar.extensions import user_registry
from olisar.extensions.base import enabled_keys, get_extension

log = logging.getLogger("olisar.killswitch")

_ALL = "*all*"  # sentinel value for "disable everything"


async def _disable(session, guild_id: int, key: str, actor: int) -> None:
    row = await session.get(ExtensionState, (guild_id, key))
    if row is None:
        row = ExtensionState(guild_id=guild_id, key=key)
        session.add(row)
    row.enabled = False
    await record_audit(
        session, actor=actor, action="killswitch",
        target_type="extension", target_id=key, after={"enabled": False},
    )


def _label(key: str) -> str:
    ext = get_extension(key)
    return ext.name if ext is not None else key


class KillSwitch(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="killswitch",
        description="Instantly turn off an extension in this server (panic button).",
    )
    @app_commands.describe(extension="The extension to disable — or 'all' for every one.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def killswitch(self, interaction: discord.Interaction, extension: str) -> None:
        gid = interaction.guild_id
        if gid is None:
            await interaction.response.send_message("This only works inside a server.", ephemeral=True)
            return
        async with session_scope() as session:
            await user_registry.load(session)  # so SDK extensions resolve too
            enabled = await enabled_keys(session, gid)
            if extension == _ALL:
                targets = sorted(enabled)
            elif extension in enabled:
                targets = [extension]
            else:
                # Tolerate a typed display name; otherwise it's unknown or already off.
                by_name = next((k for k in enabled if _label(k).lower() == extension.lower()), None)
                if by_name is not None:
                    targets = [by_name]
                elif get_extension(extension) is not None or extension in enabled:
                    await interaction.response.send_message(
                        f"**{_label(extension)}** is already off here.", ephemeral=True)
                    return
                else:
                    await interaction.response.send_message(
                        f"I don't know an extension called `{extension}`.", ephemeral=True)
                    return
            if not targets:
                await interaction.response.send_message(
                    "Nothing to switch off — no extensions are enabled here.", ephemeral=True)
                return
            for key in targets:
                await _disable(session, gid, key, interaction.user.id)
        log.warning("killswitch by %s in guild %s: %s", interaction.user, gid, targets)
        if len(targets) == 1:
            msg = f"🔌 **{_label(targets[0])}** is now off in this server."
        else:
            names = ", ".join(_label(k) for k in targets)
            msg = f"🔌 Switched off {len(targets)} extensions: {names}."
        await interaction.response.send_message(
            msg + "\nTurn them back on from the console's Extensions tab.", ephemeral=True)

    @killswitch.autocomplete("extension")
    async def _autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        gid = interaction.guild_id
        if gid is None:
            return []
        cur = (current or "").lower()
        try:
            async with session_scope() as session:
                await user_registry.load(session)
                enabled = sorted(await enabled_keys(session, gid))
        except Exception:  # noqa: BLE001 - autocomplete must never raise
            return []
        choices: list[app_commands.Choice[str]] = []
        if not cur or cur in "all":
            choices.append(app_commands.Choice(name="⚠ All extensions", value=_ALL))
        for key in enabled:
            name = _label(key)
            if cur and cur not in key.lower() and cur not in name.lower():
                continue
            choices.append(app_commands.Choice(name=f"{name} ({key})", value=key))
            if len(choices) >= 25:
                break
        return choices[:25]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(KillSwitch(bot))
