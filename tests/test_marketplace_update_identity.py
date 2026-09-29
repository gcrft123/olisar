"""A marketplace update only replaces the extension it's for, and only from the same signer.

Run:  uv run python -m unittest tests.test_marketplace_update_identity -v

Applying an update fetched the listing's latest bundle and installed it over whatever
extension the bundle's code said it was. A publisher's "weather 9.0.0" whose code declared
the id ``victim`` replaced a different installed extension and inherited its stored data and
settings. The update also never looked at who signed it: one signed by another key, or not
signed at all, replaced an install whose publisher had signed it, and the new key simply
took over.

Now an update has to declare the id of the extension it's applied to, and once an
installed extension was signed, an update has to be signed by the same key. An unsigned
install takes the first key an update is signed with.
"""

from __future__ import annotations

import contextlib
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import extensions as ext_router
from api.routers import marketplace
from api.schemas import MarketplaceUpdateApplyIn
from olisar.db.models import Base, ExtensionKV, ExtensionPackage
from olisar.extensions import bundle, signing, user_registry


def _doc(ext_id: str, version: str = "1.0.0", key=None) -> dict:
    source = textwrap.dedent(f"""
        defineExtension({{
          id: "{ext_id}", name: "{ext_id}", version: "{version}", permissions: ["kv"],
          tools: [{{ name: "{ext_id}_tool", description: "d",
                     parameters: {{ type: "object", properties: {{}} }}, handler: async () => "ok" }}],
        }});
    """)
    doc = bundle.build_bundle(ext_id=ext_id, name=ext_id, version=version, category="General",
                              description="", source=source, permissions=["kv"])
    if key is not None:
        signing.sign_bundle(doc, key[0], key[1])
    return doc


class _Resp:
    def __init__(self, data) -> None:
        self.status_code, self._data = 200, data

    def json(self):
        return self._data


class UpdateTests(unittest.IsolatedAsyncioTestCase):
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
        for module in (ext_router, marketplace):
            p = patch.object(module, "session_scope", scope)
            p.start()
            self.addCleanup(p.stop)
        user_registry.invalidate()
        self.addCleanup(user_registry.invalidate)
        self.admin = SimpleNamespace(is_allowlisted=True, discord_user_id=1)
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def install(self, doc: dict, origin: str = "marketplace") -> None:
        ref = {"registry": "r", "namespace": "pub", "name": doc["id"], "version": doc["version"]}
        await ext_router.install_bundle(doc, ["kv"], actor=1, origin=origin,
                                        marketplace_ref=ref if origin == "marketplace" else None)

    async def update(self, key: str, latest: dict) -> None:
        routes = {f"/v1/ext/pub/{key}": {"version": latest["version"]},
                  f"/v1/ext/pub/{key}/{latest['version']}": latest}

        async def fake_get(path, params=None, token=None):
            return _Resp(routes[path])

        with patch.object(marketplace, "_registry_get", fake_get):
            await marketplace.update(MarketplaceUpdateApplyIn(key=key, granted_permissions=["kv"]),
                                     self.request, self.admin)

    async def pkg(self, key: str) -> ExtensionPackage:
        async with self.scope() as s:
            return await s.get(ExtensionPackage, key)

    async def test_an_update_cannot_overwrite_a_different_extension(self) -> None:
        await self.install(_doc("weather"))
        await self.install(_doc("victim"), origin="imported")
        async with self.scope() as s:
            s.add(ExtensionKV(ext_key="victim", guild_id=1, k="secret", v="victim-data"))
        with self.assertRaises(HTTPException) as refused:
            await self.update("weather", _doc("victim", version="9.0.0"))
        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual((await self.pkg("victim")).version, "1.0.0")
        self.assertEqual((await self.pkg("weather")).version, "1.0.0")

    async def test_a_signed_install_only_takes_updates_from_its_key(self) -> None:
        mine, theirs = signing.generate(), signing.generate()
        await self.install(_doc("weather", key=mine))
        for update in (_doc("weather", "2.0.0", key=theirs), _doc("weather", "2.0.0")):
            with self.assertRaises(HTTPException) as refused:
                await self.update("weather", update)
            self.assertEqual(refused.exception.status_code, 409)
            pkg = await self.pkg("weather")
            self.assertEqual((pkg.version, pkg.publisher_key), ("1.0.0", mine[1]))
        await self.update("weather", _doc("weather", "2.0.0", key=mine))
        self.assertEqual((await self.pkg("weather")).version, "2.0.0")

    async def test_an_unsigned_install_takes_the_first_key_it_sees(self) -> None:
        first, second = signing.generate(), signing.generate()
        await self.install(_doc("weather"))
        await self.update("weather", _doc("weather", "2.0.0", key=first))
        self.assertEqual((await self.pkg("weather")).publisher_key, first[1])
        with self.assertRaises(HTTPException):
            await self.update("weather", _doc("weather", "3.0.0", key=second))
        self.assertEqual((await self.pkg("weather")).version, "2.0.0")


if __name__ == "__main__":
    unittest.main()
