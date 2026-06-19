"""/citizen — look up a Star Citizen player's RSI profile and show it as an embed.

Gated on the "star_citizen" extension being enabled (read live from the DB), so the
command only does anything when an admin has turned the extension on. The scraping
lives in olisar/extensions/star_citizen.fetch_citizen (discord-agnostic); this cog
just turns the result into a Discord embed.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.extensions import is_enabled
from olisar.extensions.star_citizen import fetch_citizen

log = logging.getLogger("olisar.cogs.star_citizen")

_RSI_BLUE = 0x1F6FEB


class StarCitizen(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="citizen", description="Look up a Star Citizen player's RSI profile.")
    @app_commands.describe(username="The player's RSI handle, e.g. DadBodNerd")
    async def citizen(self, interaction: discord.Interaction, username: str) -> None:
        # Gate on the extension for this server (ephemeral notice if off — before defer).
        gid = interaction.guild_id or settings.target_guild_id
        async with session_scope() as session:
            if not await is_enabled(session, gid, "star_citizen"):
                await interaction.response.send_message(
                    "the Star Citizen extension is off — an admin can enable it in the dashboard.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer()
        data = await fetch_citizen(username)
        if data is None:
            await interaction.followup.send(
                f"couldn't find a citizen named `{username}`.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=data["handle"],
            url=data.get("url"),
            description=data.get("bio") or None,
            color=_RSI_BLUE,
        )
        if data.get("avatar"):
            embed.set_thumbnail(url=data["avatar"])
        if data.get("record"):
            embed.add_field(name="Citizen record", value=data["record"], inline=True)
        if data.get("enlisted"):
            embed.add_field(name="Enlisted", value=data["enlisted"], inline=True)
        if data.get("fluency"):
            embed.add_field(name="Fluency", value=data["fluency"], inline=True)
        if data.get("location"):
            embed.add_field(name="Location", value=data["location"], inline=True)

        org = data.get("org")
        if org:
            value = org.get("name") or "—"
            if org.get("rank"):
                value += f" — {org['rank']}"
            if org.get("stars"):
                value += f" ({org['stars']} stars)"
            if org.get("sid"):
                value += f"\n[{org['sid']}](https://robertsspaceindustries.com/orgs/{org['sid']})"
            embed.add_field(name="Main organization", value=value, inline=False)

        embed.set_footer(text="Roberts Space Industries")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StarCitizen(bot))
