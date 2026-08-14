"""Message listener: record to memory, detect triggers, and reply.

Pipeline (Phase 1):
  record (if channel stores) -> detect trigger -> if addressed & allowed to
  speak: compose (quiet, then typing if it drags) -> generate reply -> send it at
  human pace, as the one to three messages it was written as -> record the reply.

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
from bot.content import (
    channel_identity,
    download_images,
    image_attachments,
    message_text,
    resolve_reply,
)
from bot.replies import anchor_for, composing, record_bot_messages, report_view, send_paced
from bot.triggers import detect_trigger
from olisar.addressing import AMBIGUOUS, PASSING, confirm_addressed, name_mention_kind
from olisar.failures import open_report
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

        # Server-wide search index: capture EVERY message (guild channels + DMs, per the
        # all-channel opt-in). DMs are indexed under guild 0 (channel = the DM channel)
        # and honour the per-user DM opt-out inside record_search_message; kept separate
        # from conversational memory — used only by the search_messages tool.
        channel_name = (
            f"DM · {message.author.display_name}"
            if is_dm
            else (getattr(message.channel, "name", "") or "")
        )
        async with session_scope() as session:
            indexed = await record_search_message(
                session,
                guild_id=guild_id,
                channel_id=message.channel.id,
                channel_name=channel_name,
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

        # Load this server's behaviour config (DMs borrow the home guild's — a real guild
        # the bot is in, even if target_guild_id is stale) + channel mode.
        cfg_guild = dm_home_guild_id(self.bot) if is_dm else guild_id
        async with session_scope() as session:
            config = await session.get(GuildConfig, cfg_guild)
            name_triggers = config.name_triggers if config else ["olisar"]
            reply_in_dms = config.reply_in_dms if config else True
            allowed_roles = config.allowed_role_ids if config else []
            blocked_roles = config.blocked_role_ids if config else []
            strict_name = config.name_requires_address if config else True
            see_bots = config.see_other_bots if config else False

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
            if stores and (not message.author.bot or see_bots):
                await record_message(
                    session,
                    guild_id=guild_id,
                    channel_id=message.channel.id,
                    message_id=message.id,
                    author_id=message.author.id,
                    author_is_bot=message.author.bot,
                    content=text_body,
                    reply_to=message.reference.message_id if message.reference else None,
                    display_name=message.author.display_name,
                    roles=extract_roles(message.author) if not is_dm else None,
                )

        if message.author.bot:
            # Another bot in the room. With `see_other_bots` on it's now part of the
            # conversation Olisar can see (17% of real Discord traffic is bots), but it
            # is never something to answer — two bots talking to each other is a loop.
            return

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

        # A name in a message isn't always a message to Olisar. Only the name path is
        # checked: an @mention, a reply, or a DM is unambiguous by construction.
        if trigger == "name" and strict_name:
            kind = name_mention_kind(text_body, name_triggers)
            if kind == PASSING or (
                kind == AMBIGUOUS
                and not await confirm_addressed(text_body, name_triggers[0] if name_triggers else "")
            ):
                log.info("name mentioned (%s) by %s but not addressed — staying quiet", kind, message.author)
                return

        log.info("trigger=%s from %s in #%s", trigger, message.author, message.channel)

        # Let Olisar actually see images in the message it's replying to.
        images = await download_images(message)
        # Surface which message this one replies to (used only when it's relevant).
        reply_to = await resolve_reply(message)

        room_name, room_topic = channel_identity(message.channel)

        # Compose in silence (see `composing`), then hand the finished text to `send_paced`,
        # which does the typing — the indicator should track what got written, not how long
        # the model took to write it.
        async with composing(message.channel):
            async with session_scope() as session:
                reply = await generate_reply(
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
                    reply_to=reply_to,
                    channel_name=room_name,
                    channel_topic=room_topic,
                )
                # Park the failure inside the same transaction that produced it: the button
                # about to be sent is a link to this row, so the row commits first or the
                # message goes out without it.
                report_url = (
                    await open_report(
                        session,
                        user_id=message.author.id,
                        guild_id=guild_id,
                        channel_id=message.channel.id,
                        trigger=trigger,
                        prompt=text_body,
                    )
                    if reply.blanked
                    else ""
                )
        sent = await send_paced(
            message.channel,
            reply.text,
            reply_to=anchor_for(self.bot, message),
            view=report_view(report_url),
        )

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
