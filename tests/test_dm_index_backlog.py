"""The DM index finishes, and the Knowledge page says so.

Run:  uv run python -m unittest tests.test_dm_index_backlog -v

The Knowledge page's "Direct messages" row said "indexing" forever. It compared the DMs in
the search index with every DM stored, but some DMs are never indexed: one with no text (an
image or sticker on its own), and anyone opted out of memory, search or DMs, or paused. One
such message was enough. The DM backfill had the same blind spot: it read the oldest 150
unindexed DMs and dropped the ones without text afterwards, so they came back every tick,
and 150 of them would have stopped it reaching anything newer.

Both now read ``dm_index_backlog``, the DMs ``record_search_message`` would still index.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import knowledge
from bot.cogs import search_index
from olisar.db.models import Base, MemoryOptOut, Message, SearchMessage, UserProfile, utcnow
from olisar.memory.writer import dm_index_backlog, record_search_message

HOME = 1001
DM_CHANNEL = 4004


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self._tmp.name) / 't.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def scope():
            async with Session() as session:
                yield session
                await session.commit()

        self.scope = scope
        for module in (search_index, knowledge):
            p = patch.object(module, "session_scope", scope)
            p.start()
            self.addCleanup(p.stop)
        self._next_id = 9000

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def dm(self, author_id: int, content: str = "hello", *, bot: bool = False, age: int = 0) -> int:
        """Store one DM, ``age`` minutes old, and return its Discord id."""
        self._next_id += 1
        async with self.scope() as s:
            s.add(
                Message(
                    guild_id=0,
                    channel_id=DM_CHANNEL,
                    message_id=self._next_id,
                    author_id=author_id,
                    author_is_bot=bot,
                    content=content,
                    created_at=utcnow() - timedelta(minutes=age),
                )
            )
        return self._next_id

    async def backlog(self) -> set[int]:
        async with self.scope() as s:
            return set((await s.scalars(dm_index_backlog().with_only_columns(Message.message_id))).all())


class BacklogMatchesTheIndexGate(_Db):
    async def test_backlog_is_exactly_what_record_search_message_would_add(self) -> None:
        now = utcnow()
        async with self.scope() as s:
            s.add_all([
                UserProfile(user_id=11, guild_id=0, memory_opt_out=True),
                UserProfile(user_id=12, guild_id=0, search_opt_out=True),
                UserProfile(user_id=13, guild_id=0, dm_opt_out=True),
                UserProfile(user_id=14, guild_id=0, pause_until=now + timedelta(hours=1)),
                UserProfile(user_id=15, guild_id=0, pause_until=now - timedelta(hours=1)),
                # /forget-me with no DM profile keeps them out; a DM profile that's been
                # switched back on decides for itself.
                MemoryOptOut(user_id=16),
                MemoryOptOut(user_id=17),
                UserProfile(user_id=17, guild_id=0),
                # Opted out in a server only, which doesn't reach their DMs.
                UserProfile(user_id=18, guild_id=HOME, search_opt_out=True),
            ])
        ids = {
            "plain": await self.dm(10),
            "no text": await self.dm(10, ""),
            "only spaces": await self.dm(10, "  \n "),
            "bot": await self.dm(99, bot=True),
            "memory opt-out": await self.dm(11),
            "search opt-out": await self.dm(12),
            "dm opt-out": await self.dm(13),
            "paused": await self.dm(14),
            "pause over": await self.dm(15),
            "forget-me, no profile": await self.dm(16),
            "forget-me, profile back on": await self.dm(17),
            "server-only opt-out": await self.dm(18),
            "already indexed": await self.dm(19),
        }
        async with self.scope() as s:
            s.add(SearchMessage(
                guild_id=0, channel_id=DM_CHANNEL, message_id=ids["already indexed"],
                author_id=19, author_name="x", content="hello",
            ))

        backlog = await self.backlog()
        self.assertEqual(
            {label for label, mid in ids.items() if mid in backlog},
            {"plain", "pause over", "forget-me, profile back on", "server-only opt-out"},
        )

        # And record_search_message agrees with the backlog on every human DM.
        async with self.scope() as s:
            rows = (await s.scalars(select(Message).where(Message.author_is_bot.is_(False)))).all()
            for m in rows:
                added = await record_search_message(
                    s, guild_id=0, channel_id=m.channel_id, channel_name="Direct messages",
                    message_id=m.message_id, author_id=m.author_id, author_name="x",
                    content=m.content,
                )
                self.assertEqual(added, m.message_id in backlog, m.message_id)


class BackfillAndStatus(_Db):
    async def run_backfill(self) -> None:
        cog = search_index.SearchIndex.__new__(search_index.SearchIndex)
        cog.bot = NS()
        await cog._backfill_dm_index()

    async def dm_row(self) -> dict:
        status = await knowledge.reindex_status(NS(guild_id=HOME))
        return next(c for c in status["channels"] if c["channel_id"] == "dm")

    async def test_a_page_of_dms_without_text_doesnt_hold_back_newer_ones(self) -> None:
        for _ in range(search_index.DM_INDEX_PER_TICK + 5):
            await self.dm(10, "", age=60)
        newer = await self.dm(10, "the one worth finding", age=1)
        await self.run_backfill()
        async with self.scope() as s:
            indexed = await s.scalar(
                select(func.count()).select_from(SearchMessage).where(SearchMessage.message_id == newer)
            )
        self.assertEqual(indexed, 1)

    async def test_dm_row_is_done_once_only_unindexable_dms_are_left(self) -> None:
        async with self.scope() as s:
            s.add(UserProfile(user_id=12, guild_id=0, search_opt_out=True))
        await self.dm(10, "hello")
        await self.dm(10, "")
        await self.dm(12, "keep me out of search")
        self.assertEqual((await self.dm_row())["status"], "indexing")

        await self.run_backfill()
        row = await self.dm_row()
        self.assertEqual((row["status"], row["indexed"]), ("done", 1))

    async def test_a_new_dm_puts_the_row_back_to_indexing(self) -> None:
        await self.dm(10, "hello")
        await self.run_backfill()
        self.assertEqual((await self.dm_row())["status"], "done")
        await self.dm(10, "and another")
        self.assertEqual((await self.dm_row())["status"], "indexing")


if __name__ == "__main__":
    unittest.main()
