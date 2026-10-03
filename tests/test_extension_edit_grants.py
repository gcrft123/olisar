"""Saving an installed extension's code keeps the capabilities the operator left unticked off.

Run:  uv run python -m unittest tests.test_extension_edit_grants -v

The install screen lets the operator grant a subset of what an imported or marketplace
extension asks for. Saving its code in the editor then granted everything the code listed,
so one Save undid every box they'd unticked. Now an edit keeps the install's grants; only a
capability the edit itself adds is granted, the way authoring grants what it declares.
"""

from __future__ import annotations

import contextlib
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import extensions as ext_router
from api.schemas import ExtensionAuthoringIn
from olisar.db.models import Base, ExtensionPackage
from olisar.extensions import bundle, user_registry


def _source(ext_id: str, permissions: list[str], version: str = "1.0.0") -> str:
    perms = ", ".join(f'"{p}"' for p in permissions)
    return textwrap.dedent(f"""
        defineExtension({{
          id: "{ext_id}", name: "{ext_id}", version: "{version}", permissions: [{perms}],
          tools: [{{ name: "{ext_id}_tool", description: "d",
                     parameters: {{ type: "object", properties: {{}} }}, handler: async () => "ok" }}],
        }});
    """)


class EditGrantTests(unittest.IsolatedAsyncioTestCase):
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
        p = patch.object(ext_router, "session_scope", scope)
        p.start()
        self.addCleanup(p.stop)
        user_registry.invalidate()
        self.addCleanup(user_registry.invalidate)
        self.admin = SimpleNamespace(is_allowlisted=True, discord_user_id=1)
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def import_(self, ext_id: str, requested: list[str], granted: list[str]) -> None:
        source = _source(ext_id, requested)
        doc = bundle.build_bundle(ext_id=ext_id, name=ext_id, version="1.0.0", category="General",
                                  description="", source=source, permissions=requested)
        await ext_router.install_bundle(doc, granted, actor=1, origin="imported")

    async def save(self, ext_id: str, permissions: list[str], version: str = "1.1.0") -> None:
        body = ExtensionAuthoringIn(source_ts=_source(ext_id, permissions, version))
        await ext_router.update_package(ext_id, body, self.request, self.admin)

    async def grants(self, key: str) -> tuple[list, list]:
        async with self.scope() as s:
            pkg = await s.get(ExtensionPackage, key)
            return pkg.permissions, pkg.requested_permissions

    async def test_saving_keeps_an_unticked_capability_off(self) -> None:
        await self.import_("poll", ["kv", "fetch"], granted=["kv"])
        await self.save("poll", ["kv", "fetch"])
        self.assertEqual(await self.grants("poll"), (["kv"], ["kv", "fetch"]))

    async def test_a_capability_the_edit_adds_is_granted(self) -> None:
        await self.import_("poll", ["kv", "fetch"], granted=["kv"])
        await self.save("poll", ["kv", "fetch", "discord.reply"])
        self.assertEqual(
            await self.grants("poll"), (["kv", "discord.reply"], ["kv", "fetch", "discord.reply"])
        )

    async def test_a_capability_the_edit_drops_is_no_longer_granted(self) -> None:
        await self.import_("poll", ["kv", "fetch"], granted=["kv", "fetch"])
        await self.save("poll", ["kv"])
        self.assertEqual(await self.grants("poll"), (["kv"], ["kv"]))

    async def test_a_locally_authored_extension_is_granted_what_it_asks_for(self) -> None:
        body = ExtensionAuthoringIn(source_ts=_source("mine", ["kv"]))
        await ext_router.create_package(body, self.request, self.admin)
        await self.save("mine", ["kv", "fetch"])
        self.assertEqual(await self.grants("mine"), (["kv", "fetch"], ["kv", "fetch"]))


if __name__ == "__main__":
    unittest.main()
