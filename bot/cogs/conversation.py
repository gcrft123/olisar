"""Message listener: record to memory, detect triggers, and reply.

Pipeline (Phase 1):
  record (if channel stores) -> detect trigger -> if addressed & allowed to
  speak: typing -> generate reply -> send (chunked) -> record the reply.

Speaking is gated by the channel's mode (respond/both) or being a DM, so admins
control where Olisar talks. Memory is gated separately by memory/both.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from bot.access import dm_home_guild_id, member_allowed, resolve_member
from bot.actions import MessageActions
from bot.content import download_images, image_attachments, message_text
from bot.replies import record_bot_messages, send_reply
from bot.triggers import detect_trigger
from olisar.db.engine import session_scope
from olisar.db.models import ChannelMode, GuildConfig
from olisar.gemini.vision import describe_images
from olisar.memory.media import store_image_description
from olisar.memory.writer import (
    extract_roles,
    get_channel_mode,
    record_message,
    record_search_message,
)
from olisar.pipeline import generate_reply

log = logging.getLogger("olisar.conversation")

# DMs are recorded under this sentinel guild id (they aren't tied to a guild).
DM_GUILD_ID = 0


class Conversation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Live image-captioning runs detached from the reply path; keep refs so
        # the tasks aren't garbage-collected mid-flight.
        self._media_tasks: set[asyncio.Task] = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        bot_user = self.bot.user
        if bot_user is not None and message.author.id == bot_user.id:
            return  # never act on our own messages

        is_dm = message.guild is None
        guild_id = DM_GUILD_ID if is_dm else message.guild.id

        # Full text of the message: content + embeds + attachment/sticker markers,
        # so announcement embeds and posted files are stored and searchable, not
        # just plain chat text.
        text_body = message_text(message)

        # Server-wide search index: capture EVERY guild message (any channel/mode,
        # including bots/webhooks like #announcements). Kept separate from
        # conversational memory — used only by the search_messages tool — so the
        # channel-mode opt-in still governs what Olisar reads and remembers.
        if not is_dm:
            async with session_scope() as session:
                indexed = await record_search_message(
                    session,
                    guild_id=guild_id,
                    channel_id=message.channel.id,
                    channel_name=getattr(message.channel, "name", "") or "",
                    message_id=message.id,
                    author_id=message.author.id,
                    author_name=message.author.display_name,
                    content=text_body,
                )
            # One-time, best-effort image description for the index (detached so it
            # never delays the reply). Only when we actually indexed the row — so
            # opt-out users and duplicates are skipped — and not for bot posts.
            if indexed and not message.author.bot and image_attachments(message):
                self._spawn_caption(message)

        if message.author.bot:
            return  # conversational memory + replies ignore other bots

        # Load this server's behaviour config (DMs borrow the home guild's — a real guild
        # the bot is in, even if target_guild_id is stale) + channel mode.
        cfg_guild = dm_home_guild_id(self.bot) if is_dm else guild_id
        async with session_scope() as session:
            config = await session.get(GuildConfig, cfg_guild)
            name_triggers = config.name_triggers if config else ["olisar"]
            reply_in_dms = config.reply_in_dms if config else True
            allowed_roles = config.allowed_role_ids if config else []
            blocked_roles = config.blocked_role_ids if config else []

            # Threads (incl. forum posts) inherit their parent channel's mode, so
            # Olisar engages in them per the parent's setting. Memory is still keyed
            # to the thread's own id below, so each thread is its own conversation.
            ch = message.channel
            mode_channel_id = (
                ch.parent_id if isinstance(ch, discord.Thread) and ch.parent_id else ch.id
            )
            mode = (
                ChannelMode.off
                if is_dm
                else await get_channel_mode(session, guild_id, mode_channel_id)
            )
            stores = is_dm or mode in (ChannelMode.memory, ChannelMode.both)
            if stores:
                await record_message(
                    session,
                    guild_id=guild_id,
                    channel_id=message.channel.id,
                    message_id=message.id,
                    author_id=message.author.id,
                    author_is_bot=False,
                    content=text_body,
                    reply_to=message.reference.message_id if message.reference else None,
                    display_name=message.author.display_name,
                    roles=extract_roles(message.author) if not is_dm else None,
                )

        trigger = detect_trigger(message, bot_user, name_triggers, is_dm)
        if trigger is None:
            return  # not addressed to Olisar — stay quiet
        can_speak = reply_in_dms if is_dm else mode in (ChannelMode.respond, ChannelMode.both)
        if not can_speak:
            # Someone addressed Olisar in a channel it isn't allowed to talk in —
            # log it (otherwise this is a silent no-reply that looks like a bug).
            log.info(
                "addressed (%s) by %s but channel #%s (mode=%s) can't speak",
                trigger, message.author, message.channel, mode.value,
            )
            return

        # Role gate: silently ignore people whose roles aren't allowed to use Olisar
        # (admins always pass; open to everyone when no roles are configured).
        member = resolve_member(self.bot, message.author)
        if not member_allowed(member, allowed=allowed_roles, blocked=blocked_roles, user_id=message.author.id):
            log.info("access denied (role gate) for %s", message.author)
            return

        log.info("trigger=%s from %s in #%s", trigger, message.author, message.channel)

        # Let Olisar actually see images in the message it's replying to.
        images = await download_images(message)

        async with message.channel.typing():
            async with session_scope() as session:
                text = await generate_reply(
                    session,
                    guild_id=guild_id,
                    home_guild_id=cfg_guild,  # DMs (guild_id 0) draw features from this real guild
                    channel_id=message.channel.id,
                    current_message_id=message.id,
                    bot_user_id=bot_user.id,
                    user_id=message.author.id,
                    display_name=message.author.display_name,
                    user_text=text_body,
                    actions=MessageActions(self.bot, message),
                    images=images,
                )
            sent = await send_reply(message.channel, text, reply_to=message)

        if stores:
            await record_bot_messages(
                sent, guild_id=guild_id, channel_id=message.channel.id, bot_user_id=bot_user.id
            )

    def _spawn_caption(self, message: discord.Message) -> None:
        task = asyncio.create_task(self._caption_media(message))
        self._media_tasks.add(task)
        task.add_done_callback(self._media_tasks.discard)

    async def _caption_media(self, message: discord.Message) -> None:
        """Describe a posted image once and fold the caption into its stored rows."""
        try:
            images = await download_images(message)
            if not images:
                return
            caption = await describe_images(images)
            if not caption:
                return
            async with session_scope() as session:
                await store_image_description(
                    session, message_id=message.id, caption=caption
                )
        except Exception:
            log.exception("live image captioning failed for message %s", message.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Conversation(bot))
