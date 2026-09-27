"""PUT /api/config writes and audits only what changes.

Run:  uv run python -m unittest tests.test_config_put -v

The console's pages send more than they edit, and a tab left open sends what it loaded. When
every submitted field was applied, a Behavior save carried the Access page's settings along
with it: an admin who turned the tool PIN on in one tab had it turned off again by a Behavior
save in another, and every Behavior save logged "Changed what needs the PIN". These run the
real router against a real session.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar.db.models import AuditLog, Base, GuildConfig
from olisar.guild_setup import ensure_guild_defaults

GUILD = 5001
ADMIN = 42


class PutConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.scope() as session:
            await ensure_guild_defaults(session, GUILD)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def _put(self, body: dict, *, remote: bool = False):
        from api.routers.admin import put_config
        from api.schemas import ConfigIn

        gctx = MagicMock()
        gctx.guild_id = GUILD
        gctx.admin.discord_user_id = ADMIN
        with patch("api.routers.admin.runtime_config.remote_access_configured",
                   AsyncMock(return_value=remote)), \
             patch("api.routers.admin.session_scope", self.scope):
            return await put_config(ConfigIn(**body), gctx)

    async def _get(self) -> dict:
        from api.routers.admin import get_config

        gctx = MagicMock()
        gctx.guild_id = GUILD
        with patch("api.routers.admin.runtime_config.remote_access_configured",
                   AsyncMock(return_value=False)), \
             patch("api.routers.admin.session_scope", self.scope):
            return await get_config(gctx)

    async def _stored(self) -> GuildConfig:
        async with self.scope() as session:
            return await session.get(GuildConfig, GUILD)

    async def _audit(self) -> list[AuditLog]:
        async with self.scope() as session:
            return list((await session.scalars(select(AuditLog).order_by(AuditLog.id))).all())

    async def test_resaving_what_is_stored_changes_nothing(self):
        """The whole GET payload sent straight back: no write, no audit, no version bump."""
        loaded = await self._get()
        version = (await self._stored()).version
        await self._put(loaded)
        self.assertEqual(await self._audit(), [])
        self.assertEqual((await self._stored()).version, version)

    async def test_only_the_changed_field_is_written_and_audited(self):
        loaded = await self._get()
        await self._put({**loaded, "context_message_limit": 30})
        rows = await self._audit()
        self.assertEqual([r.action for r in rows], ["update_config"])
        self.assertEqual(rows[0].after, {"context_message_limit": 30})
        # What it replaced, so the ledger can show it and an operator can put it back.
        self.assertEqual(rows[0].before, {"context_message_limit": loaded["context_message_limit"]})
        self.assertEqual((await self._stored()).context_message_limit, 30)

    async def test_a_pin_turned_on_elsewhere_survives_a_behavior_save(self):
        """Admin A opens Behavior; admin B turns the PIN requirement back on under Access;
        A saves. What Behavior sends doesn't include pin_actions, and the requirement stays."""
        await self._put({"pin_actions": []})
        behavior_tab = await self._get()  # A loads Behavior with the PIN off
        await self._put({"pin_actions": ["self_edit"]})  # B turns it on
        # A's save: the Behavior page's own fields, one of them edited.
        behavior_fields = {
            k: v for k, v in behavior_tab.items()
            if k not in {"pin_actions", "allowed_role_ids", "blocked_role_ids",
                         "member_portal_enabled", "member_portal_show_persona",
                         "remote_access_configured"}
        }
        await self._put({**behavior_fields, "reply_in_dms": not behavior_tab["reply_in_dms"]})
        self.assertEqual((await self._stored()).pin_actions, ["self_edit"])
        actions = [r.action for r in await self._audit()]
        self.assertEqual(actions, ["set_pin_actions", "set_pin_actions", "update_config"])

    async def test_resending_the_pin_actions_is_not_a_change(self):
        stored = (await self._stored()).pin_actions
        await self._put({"pin_actions": list(stored)})
        self.assertEqual(await self._audit(), [])

    async def test_a_stored_portal_isnt_refused_when_remote_access_is_off(self):
        """Refusing to *enable* the portal without remote access is right; refusing a save
        because it was enabled earlier would block every other setting on the page."""
        async with self.scope() as session:
            (await session.get(GuildConfig, GUILD)).member_portal_enabled = True
        await self._put({"member_portal_enabled": True, "see_other_bots": True}, remote=False)
        stored = await self._stored()
        self.assertTrue(stored.member_portal_enabled)
        self.assertTrue(stored.see_other_bots)

    async def test_role_ids_stored_as_ints_match_the_same_ids_as_text(self):
        async with self.scope() as session:
            (await session.get(GuildConfig, GUILD)).allowed_role_ids = [111, 222]
        await self._put({"allowed_role_ids": ["111", "222"]})
        self.assertEqual(await self._audit(), [])


if __name__ == "__main__":
    unittest.main()
