"""Forgetting a member and clearing a server's memory don't hold the write lock throughout.

Run:  uv run python -m unittest tests.test_batched_purge -v

Both ran as one transaction: the portal's "delete everything" took 40 s for a member with
33k messages and Clear memory took minutes, and SQLite's single write lock was held the
whole time, so every message the bot tried to store meanwhile waited out its 5 s busy
timeout and was dropped. The vectors also went one statement per row, or as
``rowid IN (...)``, which vec0 answers by scanning the whole table.

Now each purge deletes in committed batches, every batch taking its rows and their
vectors together, so one that's cut off partway leaves nothing half-deleted and running it
again finishes the job. The -wal file, which kept its 1.9 GB high-water mark, is capped by
journal_size_limit and truncated once a big purge is over.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, func, select, text

from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import get_engine, session_scope
from olisar.db.models import GuildChannelInfo, Message, SearchMessage, UserMemory
from olisar.guild_setup import ensure_guild_defaults
from olisar.memory import purge
from olisar.memory.vectors import upsert_embedding

HOME, OTHER = 1001, 2002
MEMBER, SOMEONE = 7, 8
BATCH = 4
DIM = 768


class BatchedPurgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "bot.db")
        engine.pin_database(self.path)
        runtime_config.invalidate()
        await create_schema()
        async with session_scope() as s:
            await ensure_guild_defaults(s, HOME, name="Home")
            await ensure_guild_defaults(s, OTHER, name="Other")
            s.add(GuildChannelInfo(channel_id=50, guild_id=HOME, name="general"))
        self._next = 1
        batch = patch.object(purge, "PURGE_BATCH", BATCH)
        batch.start()
        self.addCleanup(batch.stop)

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def _seed(self, guild_id: int, author_id: int, n: int) -> None:
        """``n`` embedded messages, search rows and embedded facts by ``author_id``."""
        async with session_scope() as s:
            for _ in range(n):
                i = self._next
                self._next += 1
                m = Message(guild_id=guild_id, channel_id=50, message_id=i, author_id=author_id,
                            content=f"message {i}", embedded=True)
                f = UserMemory(user_id=author_id, guild_id=guild_id, content=f"fact {i}",
                               embedded=True)
                s.add_all([m, f, SearchMessage(guild_id=guild_id, channel_id=50, message_id=i,
                                               author_id=author_id, content=f"message {i}")])
                await s.flush()
                await upsert_embedding(s, "message_embedding", m.id, [0.1] * DIM)
                await upsert_embedding(s, "user_memory_embedding", f.id, [0.1] * DIM)

    async def _count(self, sql: str) -> int:
        async with session_scope() as s:
            return int(await s.scalar(text(sql)))

    async def _orphans(self) -> tuple[int, int]:
        """Vectors without their row, and embedded rows without their vector."""
        async with session_scope() as s:
            vec_ids = {r for (r,) in (await s.execute(text("SELECT rowid FROM message_embedding"))).all()}
            row_ids = set(await s.scalars(select(Message.id).where(Message.embedded.is_(True))))
        return len(vec_ids - row_ids), len(row_ids - vec_ids)

    def _count_commits(self) -> list[int]:
        commits = [0]

        def on_commit(conn):  # noqa: ANN001
            commits[0] += 1

        sync_engine = get_engine().sync_engine
        event.listen(sync_engine, "commit", on_commit)
        self.addCleanup(event.remove, sync_engine, "commit", on_commit)
        return commits

    async def test_forget_deletes_in_committed_batches_and_only_that_member(self):
        await self._seed(HOME, MEMBER, 10)
        await self._seed(HOME, SOMEONE, 3)
        commits = self._count_commits()
        async with session_scope() as s:
            result = await purge.forget_user(s, guild_ids=[HOME, 0], user_id=MEMBER)
        self.assertEqual((result["messages"], result["facts"]), (10, 10))
        # 10 messages, 10 search rows and 10 facts, four at a time: at least nine
        # transactions where there used to be one.
        self.assertGreaterEqual(commits[0], 9)
        for table, column in (("message", "author_id"), ("search_message", "author_id"),
                              ("user_memory", "user_id")):
            with self.subTest(table=table):
                self.assertEqual(await self._count(
                    f"SELECT count(*) FROM {table} WHERE {column} = {MEMBER}"), 0)
                self.assertEqual(await self._count(
                    f"SELECT count(*) FROM {table} WHERE {column} = {SOMEONE}"), 3)
        self.assertEqual(await self._count("SELECT count(*) FROM message_embedding"), 3)
        self.assertEqual(await self._count("SELECT count(*) FROM user_memory_embedding"), 3)

    async def test_a_forget_cut_off_partway_leaves_nothing_half_deleted(self):
        await self._seed(HOME, MEMBER, 10)
        real = purge.delete_embedding
        calls = [0]

        async def dies_on_the_second_batch(session, table, *rowids):
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("process killed")
            await real(session, table, *rowids)

        with patch.object(purge, "delete_embedding", dies_on_the_second_batch):
            with self.assertRaises(RuntimeError):
                async with session_scope() as s:
                    await purge.forget_user(s, guild_ids=[HOME], user_id=MEMBER)
        # The first batch is gone with its vectors; the rest is still there with theirs.
        self.assertEqual(await self._count("SELECT count(*) FROM message"), 10 - BATCH)
        self.assertEqual(await self._orphans(), (0, 0))

        async with session_scope() as s:
            await purge.forget_user(s, guild_ids=[HOME], user_id=MEMBER)
        self.assertEqual(await self._count("SELECT count(*) FROM message"), 0)
        self.assertEqual(await self._count("SELECT count(*) FROM message_embedding"), 0)

    async def test_clear_memory_stays_in_its_server_and_halts_the_backfill(self):
        await self._seed(HOME, MEMBER, 6)
        await self._seed(OTHER, MEMBER, 2)
        await self._seed(0, MEMBER, 2)  # DMs belong to no one server
        commits = self._count_commits()
        async with session_scope() as s:
            counts = await purge.wipe_brain(s, guild_ids=[HOME])
        self.assertEqual(counts["messages"], 6)
        self.assertGreater(commits[0], 3)
        async with session_scope() as s:
            left = dict((await s.execute(
                select(Message.guild_id, func.count()).group_by(Message.guild_id))).all())
            channel = await s.get(GuildChannelInfo, 50)
        self.assertEqual(left, {OTHER: 2, 0: 2})
        self.assertEqual(await self._count("SELECT count(*) FROM message_embedding"), 4)
        self.assertEqual(await self._count("SELECT count(*) FROM user_memory_embedding"), 4)
        self.assertEqual(await self._orphans(), (0, 0))
        self.assertTrue(channel.backfill_done)

    async def test_the_wal_is_capped_and_truncated_after_a_big_purge(self):
        async with get_engine().connect() as conn:
            limit = (await conn.exec_driver_sql("PRAGMA journal_size_limit")).scalar()
        self.assertEqual(limit, 64 * 1024 * 1024)

        await self._seed(HOME, MEMBER, 10)
        async with session_scope() as s:
            await purge.forget_user(s, guild_ids=[HOME], user_id=MEMBER)
        self.assertEqual(os.path.getsize(self.path + "-wal"), 0)


if __name__ == "__main__":
    unittest.main()
