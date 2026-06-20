"""Posts a generated welcome when a member joins — if the 'welcome' extension is on.

The message is built on TOP of the server's persona (not replacing it): the real
persona system prompt plus the admin's custom instruction (with {user}/{username}
substituted), posted to the configured channel. Config is read live from
ExtensionState.settings, so dashboard edits take effect on the next join.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands
from google.genai import types

from bot.replies import chunk_text
from olisar.db.engine import session_scope
from olisar.db.models import ExtensionState, Persona
from olisar.extensions import is_enabled
from olisar.extensions.welcome import WELCOME_KEY
from olisar.gemini.client import get_gemini
from olisar.persona import (
    DEFAULT_PERSONA_NAME,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TONE_NOTES,
    build_system_prompt,
)

log = logging.getLogger("olisar.welcome")


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild_id = member.guild.id
        async with session_scope() as session:
            if not await is_enabled(session, guild_id, WELCOME_KEY):
                return
            row = await session.get(ExtensionState, (guild_id, WELCOME_KEY))
            cfg = (row.settings if row and row.settings else {}) or {}
            channel_id = cfg.get("channel_id")
            prompt = (cfg.get("prompt") or "").strip()
            if not channel_id or not prompt:
                return  # not fully configured yet
            persona = await session.get(Persona, guild_id)
            if persona is None:
                system = build_system_prompt(
                    persona_name=DEFAULT_PERSONA_NAME,
                    system_prompt=DEFAULT_SYSTEM_PROMPT,
                    tone_notes=DEFAULT_TONE_NOTES,
                )
            else:
                system = build_system_prompt(
                    persona_name=persona.name,
                    system_prompt=persona.system_prompt,
                    tone_notes=persona.tone_notes,
                )

        channel = member.guild.get_channel(int(channel_id))
        if channel is None:
            log.info("welcome channel %s not found in guild %s", channel_id, guild_id)
            return

        instruction = prompt.replace("{user}", member.display_name).replace(
            "{username}", member.name
        )
        task = (
            "A new member just joined the server. Write a short welcome message for "
            "them, staying fully in character. The new member is "
            f"{member.display_name} (username {member.name}). Instruction for this "
            f"welcome: {instruction}\nKeep it to 1-3 sentences. Output only the message."
        )
        try:
            # 600 tokens is generous headroom for a 1-3 sentence message: enough that
            # even a model that overshoots the brevity ask still finishes its thought
            # instead of getting cut off mid-sentence at the ceiling.
            result = await get_gemini().generate(
                contents=[types.Content(role="user", parts=[types.Part(text=task)])],
                system_instruction=system,
                max_output_tokens=600,
            )
        except Exception:
            log.exception("welcome generation failed in guild %s", guild_id)
            return
        text = (result.text or "").strip()
        if not text:
            return
        try:
            # Chunk defensively (mention on the first piece) so a long roast can't be
            # clipped at Discord's 2000-char limit on top of the generation cap.
            chunks = chunk_text(f"{member.mention} {text}")
            for chunk in chunks:
                await channel.send(chunk)
            log.info("welcomed %s in guild %s (%d chars)", member.id, guild_id, len(text))
        except Exception:
            log.exception("couldn't post welcome in channel %s", channel_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
