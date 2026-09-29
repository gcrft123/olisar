"""Vector search reads only the asking server's vectors.

Run:  uv run python -m unittest tests.test_vector_partitions -v

Every vector table was one brute-force KNN across all servers: 300 ms per reply at 500k
messages, growing with every server the bot is in. The server was filtered in afterwards,
so a server with 8% of the messages got about 3 of its own among 37 candidates, and a
member's facts rarely surfaced at all.

Each vector is now filed under its row's guild (0 for DMs) as a vec0 partition key, and
KNN searches only the partitions it's given. Existing tables are rebuilt at startup:
copied a batch per transaction, then swapped in by one transaction, so an interrupted
rebuild resumes where it stopped and the table is always either the old or the new one.
"""

from __future__ import annotations

import math
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sqlite_vec

from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import Message, UserMemory
from olisar.memory import vectors
from olisar.memory.vectors import VECTOR_TABLES, knn, upsert_embedding

HOME, OTHER = 1001, 2002
DIM = 768


def _unit(seed: int) -> list[float]:
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _near(base: list[float], seed: int, noise: float) -> list[float]:
    rng = random.Random(seed)
    v = [x + rng.gauss(0, noise) for x in base]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "bot.db")
        engine.pin_database(self.path)
        runtime_config.invalidate()
        await create_schema()

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def _message(self, guild_id: int, message_id: int, vector: list[float]) -> int:
        async with session_scope() as s:
            m = Message(guild_id=guild_id, channel_id=guild_id + 1, message_id=message_id,
                        author_id=7, content=f"m{message_id}", embedded=True)
            s.add(m)
            await s.flush()
            await upsert_embedding(s, "message_embedding", m.id, vector)
            return m.id

    async def _knn(self, table: str, query: list[float], k: int, guild_ids: list[int]):
        async with session_scope() as s:
            return await knn(s, table, query, k=k, guild_ids=guild_ids)

    def _raw(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, isolation_level=None)
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        return con


class PartitionedSearchTests(_Db):
    async def test_every_vector_table_is_partitioned_by_guild(self):
        con = self._raw()
        try:
            for name in VECTOR_TABLES:
                ddl = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]
                self.assertIn("guild_id integer partition key", ddl)
                self.assertIn(f"chunk_size={vectors.CHUNK_SIZE}", ddl)
        finally:
            con.close()

    async def test_a_vector_is_filed_under_its_rows_guild(self):
        home = await self._message(HOME, 1, _unit(1))
        dm = await self._message(0, 2, _unit(2))
        con = self._raw()
        try:
            filed = dict(con.execute("SELECT rowid, guild_id FROM message_embedding").fetchall())
        finally:
            con.close()
        self.assertEqual(filed, {home: HOME, dm: 0})

    async def test_a_small_server_gets_its_own_nearest_not_whats_left_of_a_global_top_k(self):
        query = _unit(0)
        # The busy server's vectors are all nearer the query than the small server's.
        for i in range(40):
            await self._message(OTHER, 100 + i, _near(query, 100 + i, 0.02))
        mine = [await self._message(HOME, 200 + i, _near(query, 200 + i, 0.2)) for i in range(5)]

        hits = await self._knn("message_embedding", query, 5, [HOME])
        self.assertEqual(sorted(r for r, _ in hits), sorted(mine))

    async def test_several_guilds_give_the_top_k_across_them(self):
        query = _unit(0)
        near = await self._message(HOME, 1, _near(query, 1, 0.01))
        far = await self._message(HOME, 2, _near(query, 2, 1.0))
        dm = await self._message(0, 3, _near(query, 3, 0.05))
        await self._message(OTHER, 4, _near(query, 4, 0.0))  # nearest, but not asked for

        hits = await self._knn("message_embedding", query, 2, [HOME, 0])
        self.assertEqual([r for r, _ in hits], [near, dm])
        self.assertNotIn(far, [r for r, _ in hits])

    async def test_remembered_facts_come_from_the_asking_server(self):
        from olisar.memory import retriever

        query = _unit(0)
        async with session_scope() as s:
            here = UserMemory(user_id=7, guild_id=HOME, content="likes mining", embedded=True)
            there = UserMemory(user_id=7, guild_id=OTHER, content="told the other server",
                               embedded=True)
            s.add_all([here, there])
            await s.flush()
            await upsert_embedding(s, "user_memory_embedding", here.id, _near(query, 1, 0.3))
            await upsert_embedding(s, "user_memory_embedding", there.id, _near(query, 2, 0.01))

        async def everything(ids):
            return set(ids)

        async def embed(_text):
            return query

        with patch.object(retriever, "embed_query", embed):
            async with session_scope() as s:
                block = await retriever.recall(
                    s, cfg_guild=HOME, user_id=7, query_text="what do I like",
                    recent_ids=set(), channel_id=HOME + 1, readable=everything,
                )
        self.assertIn("likes mining", block)
        self.assertNotIn("told the other server", block)


class RebuildTests(_Db):
    """A database from before partitioning, rebuilt by the startup schema step."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.vectors: dict[int, list[float]] = {}
        async with session_scope() as s:
            for i in range(1, 11):
                s.add(Message(id=i, guild_id=0 if i > 8 else HOME, channel_id=5, message_id=i,
                              author_id=7, content=f"m{i}", embedded=True))
            s.add(UserMemory(id=1, user_id=7, guild_id=OTHER, content="a fact", embedded=True))
        con = self._raw()
        try:
            for name in VECTOR_TABLES:
                con.execute(f"DROP TABLE {name}")
                con.execute(f"CREATE VIRTUAL TABLE {name} USING vec0(embedding float[{DIM}])")
            # Messages 1-10, plus a vector whose message is long gone.
            for rowid in (*range(1, 11), 99):
                self.vectors[rowid] = _unit(rowid)
                con.execute("INSERT INTO message_embedding(rowid, embedding) VALUES (?, ?)",
                            (rowid, sqlite_vec.serialize_float32(self.vectors[rowid])))
            con.execute("INSERT INTO user_memory_embedding(rowid, embedding) VALUES (1, ?)",
                        (sqlite_vec.serialize_float32(_unit(500)),))
        finally:
            con.close()

    def _filed(self, table: str) -> dict[int, int]:
        con = self._raw()
        try:
            return dict(con.execute(f"SELECT rowid, guild_id FROM {table}").fetchall())
        finally:
            con.close()

    def _tables(self) -> set[str]:
        con = self._raw()
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            con.close()

    async def _assert_rebuilt(self) -> None:
        expected = {i: (0 if i > 8 else HOME) for i in range(1, 11)}
        self.assertEqual(self._filed("message_embedding"), expected)
        self.assertEqual(self._filed("user_memory_embedding"), {1: OTHER})
        self.assertFalse(any("_rebuild" in t for t in self._tables()))
        hits = await self._knn("message_embedding", self.vectors[3], 1, [HOME])
        self.assertEqual(hits[0][0], 3)
        self.assertAlmostEqual(hits[0][1], 0.0, places=4)  # the same vector came across

    async def test_startup_files_existing_vectors_by_guild(self):
        from scripts.init_db import create_schema

        await create_schema()
        await self._assert_rebuilt()
        await create_schema()  # runs on every start: nothing left to do
        await self._assert_rebuilt()

    async def test_a_rebuild_cut_off_partway_resumes(self):
        from scripts.init_db import create_schema

        # What a process killed after its first committed batch (of three) leaves behind.
        con = self._raw()
        try:
            con.execute(vectors._vec0_ddl("message_embedding_rebuild", DIM))
            con.execute(
                "INSERT INTO message_embedding_rebuild(rowid, guild_id, embedding) "
                "SELECT p.id, p.guild_id, v.embedding FROM message p "
                "CROSS JOIN message_embedding v ON v.rowid = p.id WHERE p.id BETWEEN 1 AND 3"
            )
        finally:
            con.close()

        await create_schema()
        await self._assert_rebuilt()

    async def test_a_swap_that_fails_leaves_the_old_table_in_place(self):
        con = self._raw()
        try:
            # A table the swap would have to rename onto one that already exists.
            con.execute("CREATE TABLE message_embedding_rebuild_x (a)")
            con.execute("CREATE TABLE message_embedding_x (a)")
        finally:
            con.close()
        with self.assertRaises(sqlite3.OperationalError):
            vectors.partition_vector_tables(self.path)
        con = self._raw()
        try:
            ddl = con.execute("SELECT sql FROM sqlite_master WHERE name = 'message_embedding'").fetchone()[0]
            count = con.execute("SELECT count(*) FROM message_embedding").fetchone()[0]
        finally:
            con.close()
        self.assertNotIn("partition key", ddl)
        self.assertEqual(count, 11)


if __name__ == "__main__":
    unittest.main()
