"""A channel's newest messages come off an index, not a sort of the whole channel.

Run:  uv run python -m unittest tests.test_channel_history_index -v

Every reply reads its channel's newest dozen messages (and the newest 40 for the people
directory), and the proactive scan reads the newest two of every channel it watches every
25 seconds. With only single-column indexes on message.channel_id and message.created_at,
SQLite fetched every row of the channel and sorted it to keep those few: about 55 ms per
reply on an 80k-message channel and half a second per proactive tick. A (channel_id,
created_at) index makes each one a short walk back from the newest row.

create_all only builds indexes along with a new table, so a database that already has the
message table gets the index from create_schema, which also drops the channel_id-only index
the new one makes redundant.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import event, select, text

from olisar import runtime_config, runtime_keys
from olisar.context import build_contents, people_directory
from olisar.db import engine
from olisar.db.engine import get_engine, session_scope
from olisar.db.models import Message

CHANNEL = 42


class ChannelHistoryIndexTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine.pin_database(str(Path(tmp.name) / "bot.db"))
        runtime_config.invalidate()
        await create_schema()

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def _plans(self, statements: list[tuple[str, object]]) -> list[list[str]]:
        async with session_scope() as s:
            conn = await s.connection()
            return [
                [row[3] for row in (await conn.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params)).all()]
                for sql, params in statements
                if sql.lstrip().upper().startswith("SELECT") and " message" in sql
            ]

    async def _index_names(self) -> set[str]:
        async with session_scope() as s:
            rows = await s.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'message'"
            ))
            return {r[0] for r in rows}

    def _assert_walks_the_index(self, plans: list[list[str]]) -> None:
        self.assertTrue(plans)
        for plan in plans:
            with self.subTest(plan=plan):
                self.assertTrue(any("ix_message_channel_created" in line for line in plan), plan)
                self.assertFalse(any("TEMP B-TREE" in line for line in plan), plan)

    async def test_reply_history_reads_the_newest_rows_off_the_index(self):
        seen: list[tuple[str, object]] = []

        def capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            seen.append((statement, parameters))

        sync_engine = get_engine().sync_engine
        event.listen(sync_engine, "before_cursor_execute", capture)
        try:
            async with session_scope() as s:
                await build_contents(s, channel_id=CHANNEL, current_message_id=1, bot_user_id=2,
                                     current_display_name="ada", current_text="hi")
                await people_directory(s, channel_id=CHANNEL, current_user_id=3,
                                       current_display_name="ada")
        finally:
            event.remove(sync_engine, "before_cursor_execute", capture)
        self._assert_walks_the_index(await self._plans(seen))

    async def test_proactive_scan_reads_the_newest_rows_off_the_index(self):
        stmt = (select(Message).where(Message.channel_id == CHANNEL)
                .order_by(Message.created_at.desc()).limit(2))
        compiled = stmt.compile(get_engine().sync_engine)
        params = tuple(compiled.params[name] for name in compiled.positiontup)
        self._assert_walks_the_index(await self._plans([(str(compiled), params)]))

    async def test_an_existing_database_gets_the_index_on_upgrade(self):
        from scripts.init_db import create_schema

        # The message table as a database created before the index existed has it.
        async with session_scope() as s:
            await s.execute(text("DROP INDEX ix_message_channel_created"))
            await s.execute(text("CREATE INDEX ix_message_channel_id ON message (channel_id)"))

        await create_schema()
        names = await self._index_names()
        self.assertIn("ix_message_channel_created", names)
        self.assertNotIn("ix_message_channel_id", names)
        # created_at alone still serves the activity feed's newest-replies scan.
        self.assertIn("ix_message_created_at", names)

        await create_schema()  # runs on every start: a second pass changes nothing
        self.assertEqual(await self._index_names(), names)


if __name__ == "__main__":
    unittest.main()
