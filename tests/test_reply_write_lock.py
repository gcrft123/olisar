"""A reply doesn't hold SQLite's write lock while it waits on the model.

Run:  uv run python -m unittest tests.test_reply_write_lock -v

SQLite has one writer at a time. A reply runs in one session, and once a tool wrote through
it (set a status, remembered something) that session held the write lock until the whole
reply was done, model calls included. Every other member's message the bot tried to store
meanwhile waited out the 5 s busy timeout and was dropped; the reply's own usage accounting,
written on a second connection, waited behind its own caller's lock the same way.

These run against a real database file with the real busy timeout, so a regression shows up
as a multi-second stall or "database is locked", not as a mocked call count.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from sqlalchemy import func, select

from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import after_session_scope, session_scope
from olisar.db.models import GeminiUsage, Message
from olisar.gemini import rate_limiter
from olisar.guild_setup import ensure_guild_defaults
from olisar.memory.writer import record_message
from olisar.tools import ToolContext, execute_tool

GUILD = 1001


class _Actions:
    async def set_status(self, text: str) -> str:
        return f"status set to: {text}"


class WriteLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine.pin_database(str(Path(tmp.name) / "bot.db"))
        runtime_config.invalidate()
        await create_schema()
        async with session_scope() as s:
            await ensure_guild_defaults(s, GUILD, name="Home")

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def store_someone_elses_message(self, message_id: int) -> float:
        """What the conversation cog does for every message it sees, on its own session."""
        t = time.perf_counter()
        async with session_scope() as s:
            await record_message(s, guild_id=GUILD, channel_id=5, message_id=message_id,
                                 author_id=42, author_is_bot=False, content="hello?")
        return time.perf_counter() - t

    async def test_a_tool_that_wrote_doesnt_keep_other_messages_out(self) -> None:
        async with session_scope() as reply:
            ctx = ToolContext(session=reply, cfg_guild=GUILD, channel_id=5, user_id=7,
                              display_name="ada", actions=_Actions())
            out = await execute_tool("set_status", {"text": "vibing"}, ctx)
            self.assertTrue(out.startswith("status set"), out)
            # The reply is now waiting on its next model call. Another member talks.
            took = await asyncio.wait_for(self.store_someone_elses_message(900001), timeout=10)
        self.assertLess(took, 1.0, f"storing another member's message waited {took:.1f}s on the reply")
        async with session_scope() as s:
            n = await s.scalar(select(func.count()).select_from(Message).where(Message.message_id == 900001))
        self.assertEqual(n, 1)

    async def test_usage_is_written_after_the_callers_session_not_behind_it(self) -> None:
        model = "gemini-lock-test"
        async with session_scope() as reply:
            await record_message(reply, guild_id=GUILD, channel_id=5, message_id=900002,
                                 author_id=42, author_is_bot=False, content="x")
            await reply.flush()  # this session now holds the write lock
            t = time.perf_counter()
            await rate_limiter.record_usage(model, 10, grounding=1, source="conversation")
            self.assertLess(time.perf_counter() - t, 0.5)
            self.assertEqual(rate_limiter.pending_grounding(rate_limiter.quota_day()), 1)
        async with session_scope() as s:
            row = await s.scalar(select(GeminiUsage).where(GeminiUsage.model == model))
        self.assertIsNotNone(row, "the usage never landed")
        self.assertEqual((row.request_count, row.token_count, row.grounding_count), (1, 10, 1))
        self.assertEqual(rate_limiter.pending_grounding(rate_limiter.quota_day()), 0)

    async def test_usage_from_outside_any_session_is_written_straight_away(self) -> None:
        model = "gemini-direct"
        await asyncio.gather(*(rate_limiter.record_usage(model, 1) for _ in range(25)))
        async with session_scope() as s:
            row = await s.scalar(select(GeminiUsage).where(GeminiUsage.model == model))
        self.assertEqual(row.request_count, 25)

    async def test_work_queued_behind_a_session_runs_once_it_ends(self) -> None:
        ran: list[str] = []

        async def later() -> None:
            ran.append("ran")

        self.assertFalse(after_session_scope(later))  # no session open: the caller does it
        async with session_scope():
            async with session_scope():  # nested: waits for the outermost
                self.assertTrue(after_session_scope(later))
                self.assertTrue(after_session_scope(later))  # once, however often it's asked
            self.assertEqual(ran, [])
        self.assertEqual(ran, ["ran"])

        with self.assertRaises(RuntimeError):
            async with session_scope():
                after_session_scope(later)
                raise RuntimeError("the reply failed")
        self.assertEqual(ran, ["ran", "ran"])  # a failed reply's usage still counts


if __name__ == "__main__":
    unittest.main()
