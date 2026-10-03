"""The history backfill leaves servers waiting for the operator's approval alone.

Run:  uv run python -m unittest tests.test_backfill_approval -v

A server the operator hasn't approved gets no replies and stores nothing live, but the search
backfill still walked its history and described its images, spending the operator's Gemini
quota on a server they never let in. It now only walks approved servers the bot is in, and
the filter is in the query, so a waiting server's channels can't take the per-tick slots.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.cogs import search_index
from olisar.db.models import Base, Guild, GuildChannelInfo

APPROVED = 1001
WAITING = 2002
UNRECORDED = 3003
LEFT = 4004


class BackfillApprovalTests(unittest.IsolatedAsyncioTestCase):
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
        p = patch.object(search_index, "session_scope", scope)
        p.start()
        self.addCleanup(p.stop)

        guilds = {gid: NS(id=gid) for gid in (APPROVED, WAITING, UNRECORDED)}
        self.cog = search_index.SearchIndex.__new__(search_index.SearchIndex)
        self.cog.bot = NS(guilds=list(guilds.values()), get_guild=guilds.get)
        self.cog._backfill_channel = AsyncMock(return_value=0)
        self.cog._backfill_dm_index = AsyncMock()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def add_channels(self, guild_id: int, count: int) -> None:
        async with self.scope() as s:
            for n in range(count):
                s.add(GuildChannelInfo(channel_id=guild_id * 100 + n, guild_id=guild_id))

    async def walked(self) -> set[int]:
        await search_index.SearchIndex.tick.coro(self.cog)
        return {call.args[0].id for call in self.cog._backfill_channel.await_args_list}

    async def test_a_waiting_server_isnt_walked(self) -> None:
        async with self.scope() as s:
            s.add(Guild(id=APPROVED, approved=True))
            s.add(Guild(id=WAITING, approved=False))
        # More waiting channels than one tick takes, listed first.
        await self.add_channels(WAITING, search_index.CHANNELS_PER_TICK + 2)
        await self.add_channels(APPROVED, 1)
        self.assertEqual(await self.walked(), {APPROVED})

    async def test_a_server_the_bot_hasnt_recorded_isnt_walked(self) -> None:
        async with self.scope() as s:
            s.add(Guild(id=APPROVED, approved=True))
        await self.add_channels(UNRECORDED, 2)
        await self.add_channels(APPROVED, 1)
        self.assertEqual(await self.walked(), {APPROVED})

    async def test_a_server_the_bot_left_doesnt_take_the_slots(self) -> None:
        async with self.scope() as s:
            s.add(Guild(id=APPROVED, approved=True))
            s.add(Guild(id=LEFT, approved=True, active=False))
        await self.add_channels(LEFT, search_index.CHANNELS_PER_TICK + 2)
        await self.add_channels(APPROVED, 1)
        self.assertEqual(await self.walked(), {APPROVED})

    async def test_approving_a_server_starts_its_backfill(self) -> None:
        async with self.scope() as s:
            s.add(Guild(id=WAITING, approved=False))
        await self.add_channels(WAITING, 1)
        self.assertEqual(await self.walked(), set())
        async with self.scope() as s:
            (await s.get(Guild, WAITING)).approved = True
        self.assertEqual(await self.walked(), {WAITING})


if __name__ == "__main__":
    unittest.main()
