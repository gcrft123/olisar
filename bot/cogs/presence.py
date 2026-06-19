"""Presence / status — part of the 'living creature' feel.

On startup Olisar invents its own Discord custom status, in character, via the
model (so it's a little different each boot). Falls back to a curated line if the
model is unavailable (no key, rate-limited). Olisar can also change it mid-run with
the ``set_status`` tool. Status + avatar are settable at runtime; the profile *bio*
is not (that stays a copy-paste affordance in the dashboard).
"""

from __future__ import annotations

import logging
import random

import discord
from discord.ext import commands
from google.genai import types

from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import Persona
from olisar.gemini.client import get_gemini
from olisar.persona import DEFAULT_PERSONA_NAME, DEFAULT_SYSTEM_PROMPT, DEFAULT_TONE_NOTES

log = logging.getLogger("olisar.presence")

_FALLBACKS = ["hanging out ⛰️", "watching the stars", "lurking", "thinking it over", "online and around"]

_STATUS_PROMPT = (
    "Write your Discord custom status for right now — the short line that shows under your name. "
    "Make it in-character and a little different each time: at most ~6 words, lowercase-ish is fine, "
    "no surrounding quotes. Reply with ONLY the status text."
)


class Presence(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._set = False  # once per process (on_ready re-fires on reconnect)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._set:
            return
        self._set = True
        status = await self._invent_status()
        await self.bot.change_presence(
            status=discord.Status.online,
            activity=discord.CustomActivity(name=status),
        )
        log.info("status set to %r", status)

    async def _invent_status(self) -> str:
        """Ask the model for an in-character status; fall back if it can't."""
        try:
            async with session_scope() as session:
                p = await session.get(Persona, settings.target_guild_id)
            name = p.name if p else DEFAULT_PERSONA_NAME
            system = p.system_prompt if p else DEFAULT_SYSTEM_PROMPT
            tone = p.tone_notes if p else DEFAULT_TONE_NOTES
            si = f"You are {name}, an AI member of a Discord server.\n{system}\n\nVoice: {tone}".strip()
            result = await get_gemini().generate(
                contents=[types.Content(role="user", parts=[types.Part(text=_STATUS_PROMPT)])],
                system_instruction=si,
                temperature=1.0,
                max_output_tokens=30,
            )
            # Collapse any whitespace/newlines into one line, drop wrapping quotes.
            text = " ".join((result.text or "").split()).strip('"').strip()[:128]
            return text or random.choice(_FALLBACKS)
        except Exception:
            log.exception("status generation failed; using a fallback")
            return random.choice(_FALLBACKS)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Presence(bot))
