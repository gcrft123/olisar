"""The glossary pass costs what's left to mine, not the size of the history.

Run:  uv run python -m unittest tests.test_glossary_backlog -v

Every 20 seconds the memory worker looks for channels with enough unmined text to mine
for glossary facts. It opened a session and ran a query per memory channel and per DM,
each reading the channel's unmined rows through an index on the whole channel's history.
And the unmined set only grew: the miner reads people, never bots, so the bot's own rows
were never marked mined. On a 500k-message server a tick took about two seconds.

Now bot rows are stored as mined (and startup marks the ones stored before), one query
finds the channels worth mining, and a partial index holds only the rows still to mine.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, select, text

from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import get_engine, session_scope
from olisar.db.models import ChannelAllowlist, ChannelMode, Message
from olisar.guild_setup import ensure_guild_defaults
from olisar.memory import maintenance
from olisar.memory.writer import record_message

GUILD = 1001
BOT = 999


class GlossaryBacklogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine.pin_database(str(Path(tmp.name) / "bot.db"))
        runtime_config.invalidate()
        await create_schema()
        async with session_scope() as s:
            await ensure_guild_defaults(s, GUILD, name="Home")
        self._next_id = 1

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def _channel(self, channel_id: int, messages: int, *, words: int = 3) -> None:
        """A memory channel holding ``messages`` unmined human messages, oldest first."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        async with session_scope() as s:
            s.add(ChannelAllowlist(guild_id=GUILD, channel_id=channel_id, mode=ChannelMode.both))
            for i in range(messages):
                s.add(Message(
                    guild_id=GUILD, channel_id=channel_id, message_id=self._next_id,
                    author_id=7, content=" ".join(["word"] * words),
                    created_at=start + timedelta(seconds=i),
                ))
                self._next_id += 1

    async def _mined(self, channel_id: int) -> list[bool]:
        async with session_scope() as s:
            return list(await s.scalars(
                select(Message.fact_mined).where(Message.channel_id == channel_id)
            ))

    async def test_the_bots_own_messages_are_stored_as_mined(self):
        async with session_scope() as s:
            bot = await record_message(s, guild_id=GUILD, channel_id=5, message_id=1,
                                       author_id=BOT, author_is_bot=True, content="hi all")
            human = await record_message(s, guild_id=GUILD, channel_id=5, message_id=2,
                                         author_id=7, author_is_bot=False, content="hey",
                                         display_name="ada")
        self.assertTrue(bot.fact_mined)
        self.assertFalse(human.fact_mined)

    async def test_startup_marks_bot_rows_stored_before_as_mined(self):
        from scripts.init_db import create_schema

        async with session_scope() as s:
            s.add_all([
                Message(guild_id=GUILD, channel_id=5, message_id=1, author_id=BOT,
                        author_is_bot=True, content="an old reply", fact_mined=False),
                Message(guild_id=GUILD, channel_id=5, message_id=2, author_id=7,
                        content="not mined yet", fact_mined=False),
            ])
        await create_schema()
        async with session_scope() as s:
            rows = dict((await s.execute(select(Message.message_id, Message.fact_mined))).all())
        self.assertEqual(rows, {1: True, 2: False})

    async def test_quiet_channels_cost_one_query_not_one_each(self):
        for channel_id in range(100, 130):
            await self._channel(channel_id, 3)  # below the minimum: nothing to mine

        statements: list[str] = []

        def capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            statements.append(statement)

        sync_engine = get_engine().sync_engine
        event.listen(sync_engine, "before_cursor_execute", capture)
        mine = AsyncMock(return_value=0)
        try:
            with patch.object(maintenance, "extract_and_store_facts", mine):
                await maintenance.run_glossary()
        finally:
            event.remove(sync_engine, "before_cursor_execute", capture)
        reads = [s for s in statements if "FROM message" in s or "JOIN message" in s]
        self.assertEqual(len(reads), 1, reads)
        mine.assert_not_awaited()

    async def test_a_channel_over_the_threshold_is_still_mined(self):
        await self._channel(200, 10, words=200)  # 10 messages of ~1000 chars: over 1500 tokens
        await self._channel(201, 10)             # 10 short ones: under it
        mine = AsyncMock(return_value=1)
        with patch.object(maintenance, "extract_and_store_facts", mine):
            await maintenance.run_glossary()
        mine.assert_awaited_once()
        self.assertEqual(mine.await_args.kwargs["channel_id"], 200)
        self.assertEqual(await self._mined(200), [True] * 10)
        self.assertEqual(await self._mined(201), [False] * 10)

    async def test_the_miner_reads_unmined_rows_off_the_partial_index(self):
        stmt = (
            select(Message)
            .where(Message.channel_id == 200, Message.fact_mined == False,  # noqa: E712
                   Message.author_is_bot == False)  # noqa: E712
            .order_by(Message.created_at.asc())
            .limit(maintenance.GLOSSARY_MINE_BATCH)
        )
        compiled = stmt.compile(get_engine().sync_engine)
        params = tuple(compiled.params[name] for name in compiled.positiontup)
        async with session_scope() as s:
            conn = await s.connection()
            plan = [r[3] for r in (await conn.exec_driver_sql(
                "EXPLAIN QUERY PLAN " + str(compiled), params)).all()]
            index_sql = await s.scalar(text(
                "SELECT sql FROM sqlite_master WHERE name = 'ix_message_unmined'"
            ))
        self.assertTrue(any("ix_message_unmined" in line for line in plan), plan)
        self.assertFalse(any("TEMP B-TREE" in line for line in plan), plan)
        self.assertIn("WHERE fact_mined = 0 AND author_is_bot = 0", index_sql)


if __name__ == "__main__":
    unittest.main()
