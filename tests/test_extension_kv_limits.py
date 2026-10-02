"""An extension's ``host.kv`` store is bounded per extension per server.

Run:  uv run python -m unittest tests.test_extension_kv_limits -v

The store lives in the bot's own database and had no limits at all, so any handler a member
could set off (a tool in a reply, a slash command) could write tens of megabytes a call, and
keep doing it until the disk filled. Now a key is at most 128 characters, a value at most
1 MB of JSON, and one extension keeps at most 10,000 keys and 32 MB in one server. The key-count
and total-size limits are shrunk here so the tests stay fast.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar.db.models import Base
from olisar.sandbox import capabilities
from olisar.sandbox.capabilities import Invocation, dispatch


class KvLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self._tmp.name) / 't.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def run(ext_key: str = "polls", guild_id: int = 1):
            """One handler run: an invocation over a session committed at the end."""
            async with Session() as session:
                yield Invocation(ext_key=ext_key, permissions={"kv"}, guild_id=guild_id, session=session)
                await session.commit()

        self.run = run

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._tmp.cleanup()

    async def set(self, inv: Invocation, key: str, value) -> None:
        await dispatch(inv, "kv", "set", [key, value])

    async def test_a_value_over_the_limit_is_refused(self) -> None:
        async with self.run() as inv:
            with self.assertRaisesRegex(ValueError, "too large"):
                await self.set(inv, "k", "x" * 5_000_000)
            self.assertIsNone(await dispatch(inv, "kv", "get", ["k"]))

    async def test_a_key_over_the_limit_is_refused(self) -> None:
        async with self.run() as inv:
            with self.assertRaisesRegex(ValueError, "128 characters"):
                await self.set(inv, "k" * 129, 1)
            await self.set(inv, "k" * 128, 1)
            self.assertEqual(await dispatch(inv, "kv", "get", ["k" * 128]), 1)

    async def test_a_full_extension_can_still_update_and_delete_its_keys(self) -> None:
        with mock.patch.object(capabilities, "_KV_MAX_KEYS", 3):
            async with self.run() as inv:
                for n in range(3):
                    await self.set(inv, f"p:{n}", {"votes": {}})
            async with self.run() as inv:
                with self.assertRaisesRegex(ValueError, "full"):
                    await self.set(inv, "p:3", {"votes": {}})
                await self.set(inv, "p:0", {"votes": {"123": 1}})
                await dispatch(inv, "kv", "delete", ["p:1"])
                await self.set(inv, "p:3", {"votes": {}})

    async def test_writes_earlier_in_the_same_run_count(self) -> None:
        # One handler run looping over kv.set is the cheap way to fill the store.
        with mock.patch.object(capabilities, "_KV_MAX_KEYS", 5):
            async with self.run() as inv:
                with self.assertRaisesRegex(ValueError, "full"):
                    for n in range(10):
                        await self.set(inv, f"k{n}", n)

    async def test_the_total_size_is_capped(self) -> None:
        with mock.patch.object(capabilities, "_KV_MAX_TOTAL_BYTES", 1000):
            async with self.run() as inv:
                await self.set(inv, "a", "x" * 600)
                with self.assertRaisesRegex(ValueError, "full"):
                    await self.set(inv, "b", "x" * 600)
                # Replacing a value only counts the new one.
                await self.set(inv, "a", "y" * 900)

    async def test_limits_are_per_extension_and_per_server(self) -> None:
        with mock.patch.object(capabilities, "_KV_MAX_KEYS", 2):
            async with self.run("polls", 1) as inv:
                await self.set(inv, "a", 1)
                await self.set(inv, "b", 1)
            async with self.run("events", 1) as inv:
                await self.set(inv, "a", 1)
            async with self.run("polls", 2) as inv:
                await self.set(inv, "a", 1)

    async def test_a_large_member_directory_fits(self) -> None:
        # directory.js keeps every member in one key: name plus up to 500 characters of skills.
        directory = {str(10**17 + n): {"name": f"member{n}", "skills": "s" * 500} for n in range(1500)}
        async with self.run("directory") as inv:
            await self.set(inv, "dir", directory)
        async with self.run("directory") as inv:
            self.assertEqual(len(await dispatch(inv, "kv", "get", ["dir"])), 1500)


if __name__ == "__main__":
    unittest.main()
