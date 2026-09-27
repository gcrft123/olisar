"""The Activity log reads back what a change replaced, and where it was made.

Run:  uv run python -m unittest tests.test_audit_log -v

`record_audit` has kept `before` for the changes that store it (a chat edit, the PIN actions,
a behavior save), and /api/audit never returned it: a system prompt a member rewrote by
asking the bot in chat was gone, and nothing told a chat edit from a console one.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar.audit import record_audit
from olisar.db.models import AuditLog, Base, Persona
from olisar.guild_setup import ensure_guild_defaults

GUILD = 6001


class _DbCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()


class ListAuditTests(_DbCase):
    async def _list(self) -> list[dict]:
        from api.routers.audit import list_audit

        with patch("api.routers.audit.session_scope", self.scope):
            return (await list_audit(limit=100, _=MagicMock()))["entries"]

    async def test_a_chat_edit_comes_back_with_what_it_replaced(self):
        async with self.scope() as session:
            await record_audit(
                session, actor=77, action="update_persona", target_type="persona", target_id=GUILD,
                before={"system_prompt": "Keep it short."}, after={"system_prompt": "Be a pirate.", "via": "chat"},
            )
        (entry,) = await self._list()
        self.assertEqual(entry["before"], {"system_prompt": "Keep it short."})
        # `via` is where it was made, not a field that changed.
        self.assertEqual(entry["after"], {"system_prompt": "Be a pirate."})
        self.assertEqual(entry["via"], "chat")

    async def test_a_console_change_has_no_via(self):
        async with self.scope() as session:
            await record_audit(
                session, actor=42, action="clear_memory", target_type="guild", target_id=GUILD,
                after={"messages": 3},
            )
        (entry,) = await self._list()
        self.assertIsNone(entry["via"])
        self.assertIsNone(entry["before"])
        self.assertEqual(entry["after"], {"messages": 3})


class PersonaSaveTests(_DbCase):
    """The console's persona save keeps what it overwrote too, so an overwritten system prompt
    can be recovered from the log whichever way it was overwritten."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.scope() as session:
            await ensure_guild_defaults(session, GUILD)
            (await session.get(Persona, GUILD)).system_prompt = "Keep it short."

    async def _put(self, body: dict):
        from api.routers.admin import put_persona
        from api.schemas import PersonaIn

        gctx = MagicMock()
        gctx.guild_id = GUILD
        gctx.admin.discord_user_id = 42
        with patch("api.routers.admin.session_scope", self.scope), \
             patch("api.routers.admin.settings.target_guild_id", None):
            return await put_persona(PersonaIn(**body), gctx)

    async def _audit(self) -> list[AuditLog]:
        async with self.scope() as session:
            return list((await session.scalars(select(AuditLog))).all())

    async def test_only_what_changed_is_logged_with_its_old_value(self):
        async with self.scope() as session:
            name = (await session.get(Persona, GUILD)).name
        await self._put({"name": name, "system_prompt": "Be a pirate."})
        (row,) = await self._audit()
        self.assertEqual(row.before, {"system_prompt": "Keep it short."})
        self.assertEqual(row.after, {"system_prompt": "Be a pirate."})

    async def test_saving_it_unchanged_logs_nothing(self):
        await self._put({"system_prompt": "Keep it short."})
        self.assertEqual(await self._audit(), [])


if __name__ == "__main__":
    unittest.main()
