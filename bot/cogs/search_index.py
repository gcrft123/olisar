"""Search backfill worker — fills the server-wide search index with history.

Live ingestion (the conversation cog) captures new messages going forward; this
worker walks each channel's *past* into ``search_message`` so ``search_messages``
can reach things posted before Olisar was watching (and in channels set to
``off``, per the admin's all-channel opt-in).

Resumable and bounded per tick: it pages older history per channel, tracking the
oldest id reached in ``GuildChannelInfo.last_indexed_message_id`` and stopping a
channel once history is exhausted (``backfill_done``). FTS indexes each row on
insert via triggers, so no embeddings and no rate-limit wall — only Discord's own
history rate limits, which discord.py handles. Channels Olisar can't read are
marked done and skipped.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks
from sqlalchemy import func, select

from bot.content import download_images, image_attachments, message_text
from olisar.context import name_map
from olisar.db.engine import session_scope
from olisar.db.models import GuildChannelInfo, Message, SearchMessage
from olisar.gemini.vision import describe_images
from olisar.memory.media import description_marker
from olisar.memory.writer import record_search_message

log = logging.getLogger("olisar.search_index")

# Tuned so the per-channel indexed count on the dashboard updates often (every tick =
# TICK_SECONDS) without the worker doing any more work per minute. The throughput, the
# Discord history request rate, and the caption rate are all the SAME as the old
# 3-pages-per-120s pacing — just spread into smaller, more frequent steps:
#   pages/min  = PAGES_PER_TICK * CHANNELS_PER_TICK * (60 / TICK_SECONDS)  (unchanged)
TICK_SECONDS = 40       # was 120 — a third, with a third of the work per tick
PAGE = 100              # messages per history request (max Discord allows; keeps calls efficient)
PAGES_PER_TICK = 1      # history pages per channel per tick (was 3)
CHANNELS_PER_TICK = 4   # channels advanced per tick
CAPTIONS_PER_TICK = 1   # historical images described per tick (was 4 — keeps captions/min ~constant)
ARCHIVE_LIMIT = 100     # archived threads/posts discovered per parent channel
DM_INDEX_PER_TICK = 150 # DM messages copied from the message table into the index per tick


class SearchIndex(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.tick.start()

    def cog_unload(self) -> None:
        self.tick.cancel()

    async def _set_progress(
        self, channel_id: int, *, oldest: int | None, done: bool
    ) -> None:
        async with session_scope() as session:
            row = await session.get(GuildChannelInfo, channel_id)
            if row is None:
                return
            if oldest is not None:
                row.last_indexed_message_id = oldest
            if done:
                row.backfill_done = True

    async def _prepare_content(self, message: discord.Message, budget: list[int]) -> str:
        """Searchable text for a historical message: embed-aware, plus a one-time
        image description while this tick's caption budget lasts (best-effort)."""
        content = message_text(message)
        if budget[0] <= 0 or message.author.bot or not image_attachments(message):
            return content
        images = await download_images(message)
        caption = await describe_images(images) if images else ""
        if caption:
            content = (content + description_marker(caption)).strip()
            budget[0] -= 1
        return content

    async def _resolve(
        self, guild: discord.Guild, channel_id: int
    ) -> discord.abc.GuildChannel | discord.Thread | None:
        """A channel or thread by id, fetching from the API for uncached (e.g.
        archived) threads. None if it's gone or unreadable."""
        ch = guild.get_channel_or_thread(channel_id)
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden):
            return None
        except Exception:
            log.exception("couldn't resolve channel %s", channel_id)
            return None

    async def _discover_threads(self, guild: discord.Guild, parent) -> None:
        """Roster a parent's threads (active + recent archived) so the backfill
        reaches them. New rows only — existing thread rows keep their progress."""
        threads = list(getattr(parent, "threads", []) or [])
        try:
            async for t in parent.archived_threads(limit=ARCHIVE_LIMIT):
                threads.append(t)
        except discord.Forbidden:
            pass
        except Exception:
            log.exception("couldn't list archived threads for %s", parent.id)
        if not threads:
            return
        added = 0
        async with session_scope() as session:
            for t in threads:
                if await session.get(GuildChannelInfo, t.id) is not None:
                    continue
                session.add(
                    GuildChannelInfo(
                        channel_id=t.id,
                        guild_id=guild.id,
                        name=t.name,
                        category=getattr(parent, "name", ""),
                        kind="thread",
                        parent_id=parent.id,
                    )
                )
                added += 1
        if added:
            log.info("rostered %d threads under #%s", added, getattr(parent, "name", parent.id))

    async def _backfill_channel(
        self, guild: discord.Guild, channel_id: int, last_id: int | None, budget: list[int]
    ) -> int:
        channel = await self._resolve(guild, channel_id)
        if channel is None:
            await self._set_progress(channel_id, oldest=None, done=True)
            return 0
        # Forums hold no messages of their own — roster their posts (threads) instead.
        if isinstance(channel, discord.ForumChannel):
            await self._discover_threads(guild, channel)
            await self._set_progress(channel_id, oldest=None, done=True)
            return 0
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await self._set_progress(channel_id, oldest=None, done=True)
            return 0
        # Discover a text channel's threads once, at the start of its backfill.
        if isinstance(channel, discord.TextChannel) and last_id is None:
            await self._discover_threads(guild, channel)

        before = discord.Object(id=last_id) if last_id else None
        oldest = last_id
        total = 0
        added = 0  # exact number of messages indexed this pass (new rows written)
        try:
            for _ in range(PAGES_PER_TICK):
                batch = [m async for m in channel.history(limit=PAGE, before=before)]
                if not batch:
                    await self._set_progress(channel_id, oldest=oldest, done=True)
                    await self._log_progress(channel_id, channel.name, added, done=True)
                    return total
                # Advance the cursor over the whole page (incl. our own messages) so
                # paging always progresses. Caption outside the DB transaction.
                page_min = min(m.id for m in batch)
                oldest = page_min if oldest is None else min(oldest, page_min)
                prepared: list[tuple[discord.Message, str]] = []
                for m in batch:
                    if self.bot.user and m.author.id == self.bot.user.id:
                        continue  # never index our own messages
                    prepared.append((m, await self._prepare_content(m, budget)))
                async with session_scope() as session:
                    for m, content in prepared:
                        if await record_search_message(
                            session,
                            guild_id=guild.id,
                            channel_id=channel.id,
                            channel_name=channel.name,
                            message_id=m.id,
                            author_id=m.author.id,
                            author_name=m.author.display_name,
                            content=content,
                        ):
                            added += 1
                total += len(batch)
                before = discord.Object(id=oldest)
                if len(batch) < PAGE:  # reached the start of the channel
                    await self._set_progress(channel_id, oldest=oldest, done=True)
                    await self._log_progress(channel_id, channel.name, added, done=True)
                    return total
            # Page budget for this tick spent; persist progress, resume next tick.
            await self._set_progress(channel_id, oldest=oldest, done=False)
            await self._log_progress(channel_id, channel.name, added, done=False)
        except discord.Forbidden:
            await self._set_progress(channel_id, oldest=None, done=True)  # unreadable
        except Exception:
            log.exception("search backfill failed for channel %s", channel_id)
        return total

    async def _log_progress(self, channel_id: int, name: str, added: int, *, done: bool) -> None:
        """Log the exact indexed count for a channel: how many were added this pass and
        the running total now in the index (which the Knowledge page also shows)."""
        async with session_scope() as session:
            indexed = await session.scalar(
                select(func.count()).select_from(SearchMessage).where(
                    SearchMessage.channel_id == channel_id
                )
            ) or 0
        if done:
            log.info("search-index: #%s done — %d message(s) indexed", name, indexed)
        elif added:
            log.info("search-index: #%s +%d this pass — %d indexed so far", name, added, indexed)

    async def _backfill_dm_index(self) -> None:
        """Index DM conversations into the search corpus from the message table (guild 0).
        Discord can't page DM history, but every DM is already stored in ``message``, so
        the DM index is built from there — oldest-first, bounded per tick. Honours the
        per-user DM opt-out and dedup inside record_search_message."""
        try:
            async with session_scope() as session:
                already = select(SearchMessage.message_id).where(SearchMessage.guild_id == 0)
                todo = [
                    m
                    for m in (
                        await session.scalars(
                            select(Message)
                            .where(
                                Message.guild_id == 0,
                                Message.author_is_bot == False,  # noqa: E712
                                Message.message_id.notin_(already),
                            )
                            .order_by(Message.created_at.asc())
                            .limit(DM_INDEX_PER_TICK)
                        )
                    ).all()
                    if (m.content or "").strip()
                ]
                if not todo:
                    return
                names = await name_map(session, {m.author_id for m in todo})
                added = 0
                for m in todo:
                    if await record_search_message(
                        session,
                        guild_id=0,
                        channel_id=m.channel_id,
                        channel_name="Direct messages",
                        message_id=m.message_id,
                        author_id=m.author_id,
                        author_name=names.get(m.author_id, str(m.author_id)),
                        content=m.content,
                    ):
                        added += 1
            if added:
                log.info("search-index: DMs +%d this pass", added)
        except Exception:
            log.exception("DM search backfill failed")

    @tasks.loop(seconds=TICK_SECONDS)
    async def tick(self) -> None:
        # Scan pending channels across every guild the bot is in (capped per tick).
        try:
            async with session_scope() as session:
                pending = (
                    await session.scalars(
                        select(GuildChannelInfo)
                        .where(
                            GuildChannelInfo.backfill_done.is_(False),
                            GuildChannelInfo.index_enabled.is_(True),  # never walk "not indexed" channels
                        )
                        .limit(CHANNELS_PER_TICK)
                    )
                ).all()
                targets = [(p.guild_id, p.channel_id, p.last_indexed_message_id) for p in pending]
        except Exception:
            log.exception("search backfill scan failed")
            return
        budget = [CAPTIONS_PER_TICK]  # shared across channels this tick
        for guild_id, channel_id, last_id in targets:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            await self._backfill_channel(guild, channel_id, last_id, budget)
        # DMs can't be paged from Discord, but they're already in the message table —
        # copy any not-yet-indexed DM messages (guild 0) into the search index.
        await self._backfill_dm_index()

    @tick.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SearchIndex(bot))
