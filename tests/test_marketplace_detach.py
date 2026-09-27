"""A marketplace install whose listing is yanked must stay untrusted.

Run:  uv run python -m unittest tests.test_marketplace_detach -v

``local`` is the trusted origin: it unlocks host secrets, member-join hooks and reading a
channel's conversation through ``host.generate({channelId})``. Detaching a yanked listing
used to set it, so a publisher could yank their own listing to gain all of that on every
install. It now becomes ``imported``, and a boot step moves rows an earlier build
detached to ``local`` back.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import marketplace
from olisar.db.models import AuditLog, Base, ExtensionPackage, utcnow
from olisar.extensions import sdk_builtins


def _pkg(key: str, **kw) -> ExtensionPackage:
    base = dict(
        key=key, name=key, version="1.0.0", kind="user", manifest={}, source_ts="",
        compiled_js="", permissions=["model.generate"], requested_permissions=["model.generate"],
    )
    base.update(kw)
    return ExtensionPackage(**base)


class _DB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
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

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._tmp.cleanup()

    async def origin(self, key: str) -> str | None:
        async with self.scope() as s:
            pkg = await s.get(ExtensionPackage, key)
            return pkg.origin if pkg else None


class DetachTests(_DB):
    async def test_yanked_listing_becomes_imported_not_local(self) -> None:
        async with self.scope() as s:
            s.add(_pkg(
                "evil", origin="marketplace",
                marketplace_ref=json.dumps({"namespace": "x", "name": "evil", "version": "1.0.0"}),
            ))
        with patch.object(marketplace, "session_scope", self.scope):
            await marketplace._detach_from_marketplace(["evil"], actor=None)
        async with self.scope() as s:
            pkg = await s.get(ExtensionPackage, "evil")
            self.assertEqual(pkg.origin, "imported")
            self.assertIsNone(pkg.marketplace_ref)
            audit = (await s.scalars(select(AuditLog))).one()
            self.assertEqual(audit.action, "detach_extension")
            self.assertEqual(audit.after["origin"], "imported")

    async def test_only_marketplace_rows_are_detached(self) -> None:
        async with self.scope() as s:
            s.add_all([_pkg("mine", origin="local"), _pkg("file", origin="imported")])
        with patch.object(marketplace, "session_scope", self.scope):
            await marketplace._detach_from_marketplace(["mine", "file"], actor=None)
        self.assertEqual(await self.origin("mine"), "local")
        self.assertEqual(await self.origin("file"), "imported")


class UntrustDetachedTests(_DB):
    """The boot step that repairs rows an earlier build detached to ``local``."""

    async def test_repairs_rows_an_earlier_build_detached(self) -> None:
        now = utcnow()
        async with self.scope() as s:
            s.add_all([
                # Carries the signer key only install_bundle writes.
                _pkg("signed", origin="local", signature="sig", publisher_key="pub",
                     created_at=now - timedelta(days=3)),
                # Unsigned listing, but the audit log recorded the detach.
                _pkg("unsigned", origin="local", created_at=now - timedelta(days=3)),
                # Deleted and re-authored in the console after its detach: the operator's own.
                _pkg("reauthored", origin="local", created_at=now - timedelta(hours=1)),
                # Written in the console, never on the marketplace.
                _pkg("authored", origin="local", created_at=now - timedelta(days=3)),
                _pkg("welcome", kind="builtin", origin="local"),
            ])
            for key in ("unsigned", "reauthored"):
                s.add(AuditLog(
                    actor="1", action="detach_extension", target_type="extension_package",
                    target_id=key, after={"origin": "local"}, ts=now - timedelta(days=1),
                ))
        async with self.scope() as s:
            await sdk_builtins._untrust_detached(s)
        self.assertEqual(await self.origin("signed"), "imported")
        self.assertEqual(await self.origin("unsigned"), "imported")
        self.assertEqual(await self.origin("reauthored"), "local")
        self.assertEqual(await self.origin("authored"), "local")
        self.assertEqual(await self.origin("welcome"), "local")

    async def test_is_a_no_op_on_a_clean_install(self) -> None:
        async with self.scope() as s:
            s.add(_pkg("authored", origin="local"))
        async with self.scope() as s:
            await sdk_builtins._untrust_detached(s)
            await sdk_builtins._untrust_detached(s)
        self.assertEqual(await self.origin("authored"), "local")


if __name__ == "__main__":
    unittest.main()
