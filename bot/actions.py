"""Concrete DiscordActions for tools that touch Discord (set_status, react).

`MessageActions` is used when Olisar is replying to a real message (so it can
react to it); `BotActions` is used for /ask interactions, where there's no
message to react to.
"""

from __future__ import annotations

import io

import discord

from bot.replies import chunk_text


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
