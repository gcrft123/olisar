"""The Olisar bot subclass: intents, cog loading, and slash-command sync."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.replies import SAFE_MENTIONS
from olisar import guild_approval
from olisar.config import settings

log = logging.getLogger("olisar.bot")

# Cogs loaded on startup. More are added as later phases land.
INITIAL_COGS = [
    "bot.cogs.guilds",
    "bot.cogs.conversation",
    "bot.cogs.members",
    "bot.cogs.slash",
    "bot.cogs.presence",
    "bot.cogs.memory_worker",
    "bot.cogs.context_channels",
    "bot.cogs.search_index",
    "bot.cogs.events",
    "bot.cogs.proactive",
    "bot.cogs.reminders",
    "bot.cogs.sdk_commands",
    "bot.cogs.sdk_events",  # dispatches gateway events (member join, …) to SDK extensions
    "bot.cogs.killswitch",  # /killswitch — instantly disable an extension in a guild
    "bot.cogs.moderation",  # syncs + enforces the global ban list from the registry
    "bot.cogs.canary",  # daily tool round-trip self-test — see olisar/gemini/canary.py
]


def _build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    # Privileged — must ALSO be toggled on in the Developer Portal. message_content
    # lets Olisar read what people say; members lets it track per-user profiles;
    # presences lets the situational-awareness tools see a member's status/activity
    # (gated per-server by GuildConfig.presence_tools_enabled, default off).
    intents.message_content = True
    intents.members = True
    intents.voice_states = True  # who's in voice (non-privileged)
    # presences is privileged AND requires a Developer-Portal toggle, so it's opt-in:
    # enabling it blindly would stop the bot connecting for anyone who hasn't turned it
    # on in the portal. who_is_in_voice works without it; get_user_status needs it.
    if settings.enable_presence_intent:
        intents.presences = True
    return intents


class _Tree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Every slash command, extension commands included, refuses in a server the
        operator hasn't approved. They aren't synced there, so this is the backstop."""
        if guild_approval.is_pending(interaction.guild_id):
            await interaction.response.send_message(
                "This server is waiting for the bot's operator to approve it.", ephemeral=True,
            )
            return False
        return True


class OlisarBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,  # we lean on slash + triggers, not prefixes
            intents=_build_intents(),
            help_command=None,
            tree_cls=_Tree,
            # Nothing the bot sends pings @everyone, @here or a role unless that send says so.
            allowed_mentions=SAFE_MENTIONS,
        )

    async def setup_hook(self) -> None:
        # Before any cog's on_ready: which servers wait for the operator's approval.
        await guild_approval.load()
        for ext in INITIAL_COGS:
            await self.load_extension(ext)
            log.info("loaded cog %s", ext)
        # Slash commands are synced per guild by the guilds cog on_ready (the guild
        # list isn't known until we've connected), which keeps multi-server in sync.

    async def on_ready(self) -> None:
        log.info("Olisar is online as %s (id=%s)", self.user, self.user and self.user.id)
        await self._sync_profile_bio()

    async def _sync_profile_bio(self) -> None:
        """Push the home/target guild's persona bio — plus the forced attribution line
        (see ``olisar.discord_bio``) — to the bot's profile About Me, its Application
        Description. Once per process — on_ready can fire on every reconnect, and the
        bio rarely changes. The attribution is applied even when the persona bio is
        blank, so a fresh install still carries it."""
        if getattr(self, "_bio_applied", False):
            return
        try:
            from olisar.db.engine import session_scope
            from olisar.db.models import Persona
            from olisar.discord_bio import apply_bot_bio
            from olisar.runtime_config import discord_token

            gid = settings.target_guild_id
            if not gid:
                return
            async with session_scope() as session:
                persona = await session.get(Persona, gid)
            bio = (persona.desired_bio if persona else "") or ""
            if await apply_bot_bio(await discord_token(), bio):
                self._bio_applied = True
        except Exception:
            log.exception("profile bio sync failed")
