"""Concrete DiscordActions for tools that touch Discord (set_status, react).

`MessageActions` is used when Olisar is replying to a real message (so it can
react to it); `BotActions` is used for /ask interactions, where there's no
message to react to.
"""

from __future__ import annotations

import io

import discord

from bot.replies import chunk_text

_ACTIVITY_VERB = {
    discord.ActivityType.playing: "playing",
    discord.ActivityType.streaming: "streaming",
    discord.ActivityType.listening: "listening to",
    discord.ActivityType.watching: "watching",
    discord.ActivityType.competing: "competing in",
}


def _resolve_member(guild: discord.Guild, query: str) -> discord.Member | None:
    """Find a member by numeric id (or <@id> mention) or by display/user name —
    exact match first, then a unique-ish prefix."""
    query = (query or "").strip()
    if not query:
        return None
    digits = "".join(c for c in query if c.isdigit())
    if digits:
        m = guild.get_member(int(digits))
        if m is not None:
            return m
    low = query.lstrip("@").lower()
    for m in guild.members:
        if m.display_name.lower() == low or m.name.lower() == low:
            return m
    for m in guild.members:
        if m.display_name.lower().startswith(low) or m.name.lower().startswith(low):
            return m
    return None


def _format_activities(member: discord.Member) -> str:
    bits: list[str] = []
    for a in member.activities:
        if isinstance(a, discord.CustomActivity):
            if a.name:
                bits.append(f'custom status "{a.name}"')
        elif isinstance(a, discord.Spotify):
            bits.append(f"listening to {a.title} by {a.artist}")
        else:
            verb = _ACTIVITY_VERB.get(getattr(a, "type", None), "")
            name = getattr(a, "name", None)
            if name:
                bits.append(f"{verb} {name}".strip())
    return "; ".join(bits)


class BotActions:
    """Bot-level actions available everywhere (no triggering message).

    ``channel`` is where generated images are posted; supply the interaction/
    message channel so image generation has somewhere to land (None disables it).
    """

    def __init__(
        self, bot: discord.Client, channel: discord.abc.Messageable | None = None
    ) -> None:
        self.bot = bot
        self.channel = channel

    async def set_status(self, text: str) -> str:
        try:
            await self.bot.change_presence(
                activity=discord.CustomActivity(name=text or "…")
            )
            return f"status set to: {text}"
        except Exception as exc:  # noqa: BLE001 - surfaced back to the model
            return f"couldn't set status: {exc}"

    async def react(self, emoji: str) -> str:
        return "no message to react to here"

    async def send_dm(self, user_id: int, text: str) -> str:
        """Send a private DM to a user by their numeric id. Olisar may do this on
        its own initiative; it degrades gracefully if the user has DMs closed."""
        text = (text or "").strip()
        if not text:
            return "no message to send"
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return f"'{user_id}' isn't a valid user id"
        try:
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
        except discord.NotFound:
            return f"no user with id {uid}"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't find user {uid}: {exc}"
        if user is None:
            return f"no user with id {uid}"
        try:
            for chunk in chunk_text(text):
                await user.send(chunk)
            return f"sent a DM to {user.display_name}"
        except discord.Forbidden:
            return f"can't DM {user.display_name} — their DMs are closed to me"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't DM {user.display_name}: {exc}"

    async def user_status(self, query: str, guild_id: int) -> str:
        """A member's live presence (status + current game/app), for the
        situational-awareness tool. Read on demand, never stored."""
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None:
            return "I can't see that server right now."
        member = _resolve_member(guild, query)
        if member is None:
            return f"I couldn't find anyone matching {query!r} here."
        status = getattr(member.status, "name", None) or "unknown"
        acts = _format_activities(member)
        base = f"{member.display_name} is {status}"
        return f"{base}; {acts}." if acts else f"{base}, with nothing showing as an activity."

    async def who_in_voice(self, guild_id: int) -> str:
        """Who is in each voice channel right now."""
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        if guild is None:
            return "I can't see that server right now."
        lines: list[str] = []
        for vc in guild.voice_channels:
            names = [m.display_name for m in vc.members]
            if names:
                lines.append(f"{vc.name}: " + ", ".join(names))
        if not lines:
            return "Nobody's in a voice channel right now."
        return "In voice right now —\n" + "\n".join(lines)

    async def send_image(
        self, data: bytes, *, filename: str = "olisar.png", caption: str = ""
    ) -> str:
        """Post a generated image to the active channel (set at construction)."""
        if not data:
            return "no image to send"
        if self.channel is None:
            return "can't post an image from here"
        try:
            file = discord.File(io.BytesIO(data), filename=filename)
            await self.channel.send(content=(caption or None), file=file)
            return "image posted"
        except discord.Forbidden:
            return "can't post an image here — missing permission"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't post the image: {exc}"


class MessageActions(BotActions):
    """Adds reacting to the message that triggered the reply."""

    def __init__(self, bot: discord.Client, message: discord.Message) -> None:
        super().__init__(bot, channel=message.channel)
        self.message = message

    async def react(self, emoji: str) -> str:
        emoji = (emoji or "").strip()
        if not emoji:
            return "no emoji given"
        try:
            await self.message.add_reaction(emoji)
            return f"reacted with {emoji}"
        except Exception as exc:  # noqa: BLE001
            return f"couldn't react with {emoji}: {exc}"
