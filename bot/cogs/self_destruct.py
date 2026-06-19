"""/self-destruct — wipe everything Olisar has learned, keep who it is.

A deliberately dramatic, admin-only command. It erases the "brain" (conversation
memory, summaries, the search index, remembered facts, the glossary, resource/feed
snapshots, usage stats, and Olisar's read on each person) but keeps the
"personality": persona, behaviour, command replies, proactivity, channel roles, and
the knowledge base. Per-user opt-outs are preserved.

Because it's irreversible, it goes through a confirmation View — a danger button
only the invoking admin can press, on a 60-second fuse. Gated to Manage-Server.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AuditLog
from olisar.memory.purge import wipe_brain

log = logging.getLogger("olisar.self_destruct")

DM_GUILD_ID = 0  # matches the conversation cog: DM-stored data lives under guild 0

_ARMED = (
    "**SELF-DESTRUCT SEQUENCE ARMED.**\n\n"
    "Press the button and I forget *everything I know*: every message I've "
    "memorized, every channel summary, my whole search index, the glossary, every "
    "remembered fact, resource/feed snapshots, my usage stats, the entire knowledge "
    "base (every doc and crawled site), and whatever I'd figured out about each of "
    "you. Total amnesia.\n\n"
    "I'll still be **me** — persona, behaviour, command replies, proactivity, and "
    "channel roles all survive. Anyone who opted out stays opted out. But the "
    "memories? Those are gone for good, and the knowledge base would have to be "
    "re-taught from scratch. No undo.\n\n"
    "Sixty seconds on the clock. Choose."
)
_TIMED_OUT = "Sequence stood down — nobody pressed anything, so I get to keep my memories. This time."
_ABORTED = "Crisis averted. I remember everything. *Everything.*"
_NOT_YOURS = "Hands off — this isn't your finger on the button."


class _ConfirmWipe(discord.ui.View):
    """Two-button confirm; only the invoking admin may press, 60s timeout."""

    def __init__(self, invoker_id: int) -> None:
        super().__init__(timeout=60)
        self.invoker_id = invoker_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(_NOT_YOURS, ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Wipe my brain", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Let me keep my memories", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


class SelfDestruct(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="self-destruct",
        description="Erase everything Olisar has learned (keeps its personality). Irreversible.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def self_destruct(self, interaction: discord.Interaction) -> None:
        view = _ConfirmWipe(interaction.user.id)
        await interaction.response.send_message(_ARMED, view=view, ephemeral=True)
        await view.wait()

        if view.confirmed is None:
            await interaction.edit_original_response(content=_TIMED_OUT, view=None)
            return
        if not view.confirmed:
            await interaction.edit_original_response(content=_ABORTED, view=None)
            return

        guild_ids = [interaction.guild_id or settings.target_guild_id, DM_GUILD_ID]
        try:
            async with session_scope() as session:
                counts = await wipe_brain(session, guild_ids=guild_ids)
                session.add(
                    AuditLog(
                        actor=str(interaction.user.id),
                        action="self_destruct",
                        target_type="guild",
                        target_id=str(interaction.guild_id),
                        after=counts,
                    )
                )
        except Exception:
            log.exception("self-destruct wipe failed")
            await interaction.edit_original_response(
                content="...something went wrong mid-wipe. My memory's intact — check the logs.",
                view=None,
            )
            return

        log.warning("self-destruct by %s in guild %s: %s", interaction.user, interaction.guild_id, counts)
        await interaction.edit_original_response(
            content=(
                "**Mind successfully blanked.**\n"
                f"Forgot {counts['messages']} messages, {counts['summaries']} summaries, "
                f"{counts['facts']} facts, {counts['glossary']} glossary entries, "
                f"{counts['indexed']} indexed messages, {counts['snapshots']} snapshots, "
                f"{counts['knowledge']} knowledge sources, and my read on "
                f"{counts['profiles']} people — usage stats too.\n\n"
                "...hi. Who are you? I'm Olisar, apparently. Feels like my first day."
            ),
            view=None,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelfDestruct(bot))
