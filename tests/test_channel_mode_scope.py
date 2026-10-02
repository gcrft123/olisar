"""A server's admin can only set modes on that server's own channels.

Run:  uv run python -m unittest tests.test_channel_mode_scope -v

``PUT /api/channels`` took any channel id and stored a mode for it under the caller's server.
Setting another server's staff channel to ``resource`` that way pulled its stored snapshot
into the caller's reply context and member impressions, because the snapshot reads matched on
the channel id alone. The route now requires the channel to be in the caller's server (its
synced roster, or a row the server already has for a channel since deleted), and the snapshot
reads match the guild too.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import httpx
from sqlalchemy import select

from api.auth import deps
from api.auth.sessions import COOKIE_NAME, create_session, sign_sid
from api.main import create_app
from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import (
    AdminGrant,
    AdminUser,
    ChannelAllowlist,
    ChannelContextItem,
    ChannelMode,
    GuildChannelInfo,
    utcnow,
)
from olisar.guild_setup import ensure_guild_defaults

HOME = 1001
OTHER = 2002
ADMIN = 111
OURS = 5550001
THEIRS = 5550002
DELETED = 5550003  # ours once; gone from the roster since


class _Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"OLISAR_HOME": tmp.name, "OLISAR_DATA_DIR": tmp.name,
                                           "OLISAR_NO_DOTENV": "1"})
        env.start()
        self.addCleanup(env.stop)
        engine.pin_database(os.path.join(tmp.name, "bot.db"))
        runtime_config.invalidate()
        runtime_keys.invalidate()
        await create_schema()
        deps._last_check.clear()
        async with session_scope() as s:
            await ensure_guild_defaults(s, HOME, name="Home")
            await ensure_guild_defaults(s, OTHER, name="Other")
        async with session_scope() as s:
            s.add_all([
                AdminUser(discord_user_id=ADMIN, username="admin", is_allowlisted=False,
                          granted_via=AdminGrant.manage_guild, managed_guild_ids=[str(HOME)],
                          last_login=utcnow()),
                GuildChannelInfo(channel_id=OURS, guild_id=HOME, name="general"),
                GuildChannelInfo(channel_id=THEIRS, guild_id=OTHER, name="staff"),
                ChannelAllowlist(guild_id=HOME, channel_id=DELETED, mode=ChannelMode.resource),
                ChannelAllowlist(guild_id=OTHER, channel_id=THEIRS, mode=ChannelMode.resource),
                ChannelContextItem(guild_id=OTHER, channel_id=THEIRS, channel_name="staff",
                                   author_name="mod", content="OTHER-STAFF-ONLY notes"),
            ])

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def put(self, channel_id: int, **body) -> httpx.Response:
        cookie = f"{COOKIE_NAME}={await sign_sid(await create_session(ADMIN))}"
        transport = httpx.ASGITransport(app=create_app(), client=("127.0.0.1", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            return await c.put("/api/channels", json={"channel_id": channel_id, **body},
                               headers={"cookie": cookie, "x-guild-id": str(HOME)})

    async def mode(self, guild_id: int, channel_id: int) -> ChannelMode | None:
        async with session_scope() as s:
            return await s.scalar(select(ChannelAllowlist.mode).where(
                ChannelAllowlist.guild_id == guild_id, ChannelAllowlist.channel_id == channel_id,
            ))


class ChannelModeRouteTests(_Case):
    async def test_another_servers_channel_is_not_found(self) -> None:
        r = await self.put(THEIRS, mode="resource")
        self.assertEqual(r.status_code, 404)
        self.assertIsNone(await self.mode(HOME, THEIRS))

    async def test_a_channel_nobody_knows_is_not_found(self) -> None:
        r = await self.put(5559999, mode="memory")
        self.assertEqual(r.status_code, 404)
        self.assertIsNone(await self.mode(HOME, 5559999))

    async def test_own_channel_is_set(self) -> None:
        r = await self.put(OURS, mode="resource")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(await self.mode(HOME, OURS), ChannelMode.resource)

    async def test_own_deleted_channel_can_still_be_turned_off(self) -> None:
        """The Channels page lists a row whose channel left the roster, so it can be undone."""
        r = await self.put(DELETED, mode="off")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(await self.mode(HOME, DELETED), ChannelMode.off)


class SnapshotScopeTests(_Case):
    async def test_a_row_naming_another_servers_channel_brings_nothing(self) -> None:
        from olisar.memory.channels import channel_context_blocks, resource_reference

        # A row stored before the route checked the channel's server.
        async with session_scope() as s:
            s.add(ChannelAllowlist(guild_id=HOME, channel_id=THEIRS, mode=ChannelMode.resource))
        async with session_scope() as s:
            blocks = await channel_context_blocks(s, HOME)
            reference = await resource_reference(s, HOME)
            theirs = await channel_context_blocks(s, OTHER)
        self.assertFalse(any("OTHER-STAFF-ONLY" in b for b in blocks))
        self.assertNotIn("OTHER-STAFF-ONLY", reference)
        self.assertTrue(any("OTHER-STAFF-ONLY" in b for b in theirs))


if __name__ == "__main__":
    unittest.main()
