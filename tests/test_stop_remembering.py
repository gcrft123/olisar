"""/forget-me stop_remembering:true holds everywhere, including places Olisar hadn't seen them.

Run:  uv run python -m unittest tests.test_stop_remembering -v

"Stop remembering" only set a flag on the profiles someone already had. Someone who had
never DMed the bot had no DM profile, so their next DM made a fresh one that wasn't opted
out, and it was stored and search-indexed; the same went for a server the bot joined later.
The resource and feed channel snapshots and the remember tool never checked the flag at all.

The opt-out is now kept per person as well as on each profile. A profile made afterwards
starts opted out, a writer with no profile to read checks the per-person record, and the
snapshots and remember respect it. A profile that exists still decides for its own server,
so turning memory back on for one server in the member portal still works.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar.db.models import (
    Base,
    ChannelContextItem,
    Guild,
    MemoryOptOut,
    Message,
    SearchMessage,
    UserMemory,
    UserProfile,
)
from olisar.memory.channels import replace_context_items
from olisar.memory.purge import forget_user
from olisar.memory.writer import (
    opted_out,
    record_message,
    record_search_message,
    upsert_profile,
)
from olisar.tools import ToolContext, execute_tool

HOME = 1001
LATER = 2002  # a server the bot joins after the opt-out
LEFT = 8008   # a server the bot had left when they opted out
CHANNEL = 3003
DM_CHANNEL = 4004
USER = 5005
OTHER_USER = 6006


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.scope() as s:
            s.add(Guild(id=HOME, name="Home"))
            s.add(UserProfile(user_id=USER, guild_id=HOME, display_name="m"))
            s.add(UserProfile(user_id=USER, guild_id=LEFT, display_name="m"))

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def stop_remembering(self, guild_ids=(HOME, 0)) -> None:
        with patch("olisar.memory.purge.delete_embedding", AsyncMock()):
            async with self.scope() as s:
                await forget_user(s, guild_ids=list(guild_ids), user_id=USER, opt_out=True)

    async def store(self, guild_id: int, channel_id: int, message_id: int) -> tuple[bool, bool]:
        async with self.scope() as s:
            stored = await record_message(
                s, guild_id=guild_id, channel_id=channel_id, message_id=message_id,
                author_id=USER, author_is_bot=False, content="please don't keep this",
                display_name="m",
            )
            indexed = await record_search_message(
                s, guild_id=guild_id, channel_id=channel_id, channel_name="x",
                message_id=message_id, author_id=USER, author_name="m",
                content="please don't keep this",
            )
        return stored is not None, indexed

    async def count(self, model) -> int:
        async with self.Session() as s:
            return await s.scalar(select(func.count()).select_from(model))


class Messages(_Db):
    async def test_a_first_dm_afterwards_is_neither_stored_nor_indexed(self):
        await self.stop_remembering()
        self.assertEqual(await self.store(0, DM_CHANNEL, 41), (False, False))

    async def test_nor_is_a_message_in_a_server_joined_later(self):
        await self.stop_remembering()
        self.assertEqual(await self.store(LATER, CHANNEL, 42), (False, False))

    async def test_nor_is_indexing_where_they_have_no_profile(self):
        await self.stop_remembering()
        async with self.scope() as s:
            indexed = await record_search_message(
                s, guild_id=LATER, channel_id=CHANNEL, channel_name="general", message_id=43,
                author_id=USER, author_name="m", content="not in a memory channel",
            )
        self.assertFalse(indexed)

    async def test_a_server_the_bot_had_left_is_covered_too(self):
        await self.stop_remembering(guild_ids=(HOME, 0))
        async with self.Session() as s:
            self.assertTrue(await opted_out(s, USER, LEFT))

    async def test_a_new_profile_starts_opted_out(self):
        await self.stop_remembering()
        async with self.scope() as s:
            profile = await upsert_profile(s, LATER, USER, "m")
        self.assertTrue(profile.memory_opt_out)

    async def test_turning_memory_back_on_for_one_server_still_works(self):
        await self.stop_remembering()
        async with self.scope() as s:
            (await upsert_profile(s, HOME, USER, "m")).memory_opt_out = False
        self.assertEqual(await self.store(HOME, CHANNEL, 44), (True, True))
        self.assertEqual(await self.store(0, DM_CHANNEL, 45), (False, False))

    async def test_everyone_else_is_unaffected(self):
        await self.stop_remembering()
        async with self.scope() as s:
            stored = await record_message(
                s, guild_id=0, channel_id=DM_CHANNEL + 1, message_id=46, author_id=OTHER_USER,
                author_is_bot=False, content="keep this", display_name="o",
            )
        self.assertIsNotNone(stored)

    async def test_recorded_once_however_often_they_ask(self):
        await self.stop_remembering()
        await self.stop_remembering()
        self.assertEqual(await self.count(MemoryOptOut), 1)

    async def test_forgetting_without_opting_out_records_nothing(self):
        with patch("olisar.memory.purge.delete_embedding", AsyncMock()):
            async with self.scope() as s:
                await forget_user(s, guild_ids=[HOME, 0], user_id=USER)
        self.assertEqual(await self.count(MemoryOptOut), 0)
        self.assertEqual(await self.store(0, DM_CHANNEL, 47), (True, True))


class Snapshots(_Db):
    async def test_their_posts_are_left_out_of_channel_snapshots(self):
        await self.stop_remembering()
        async with self.scope() as s:
            await replace_context_items(
                s, guild_id=LATER, channel_id=CHANNEL, channel_name="announcements",
                items=[
                    {"message_id": 1, "author_id": USER, "author_name": "m", "content": "mine"},
                    {"message_id": 2, "author_id": OTHER_USER, "author_name": "o",
                     "content": "theirs"},
                ],
            )
        async with self.Session() as s:
            kept = (await s.scalars(select(ChannelContextItem.content))).all()
        self.assertEqual(kept, ["theirs"])


class Remember(_Db):
    async def remember(self, *, is_dm: bool) -> str:
        async with self.scope() as s:
            ctx = ToolContext(session=s, cfg_guild=HOME, channel_id=DM_CHANNEL if is_dm else CHANNEL,
                              user_id=USER, display_name="m", is_dm=is_dm)
            return await execute_tool("remember", {"fact": "they live on Elm St"}, ctx)

    async def test_nothing_is_saved_in_a_dm(self):
        await self.stop_remembering()
        out = await self.remember(is_dm=True)
        self.assertTrue(out.startswith("Not saved"), out)
        self.assertEqual(await self.count(UserMemory), 0)

    async def test_nor_in_a_server(self):
        await self.stop_remembering()
        out = await self.remember(is_dm=False)
        self.assertTrue(out.startswith("Not saved"), out)
        self.assertEqual(await self.count(UserMemory), 0)

    async def test_saved_as_before_otherwise(self):
        out = await self.remember(is_dm=False)
        self.assertTrue(out.startswith("Saved"), out)
        self.assertEqual(await self.count(UserMemory), 1)


if __name__ == "__main__":
    unittest.main()
