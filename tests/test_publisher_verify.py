"""The marketplace's verified badge comes from the registry's own Discord sign-in.

Run:  uv run python -m unittest tests.test_publisher_verify -v

The bot used to run the Discord sign-in through its own app and forward the token to the
registry, which accepted a token from any app: whoever ran an app someone signed in to could
claim that person's Discord account for their own publisher (and, for a developer, the
developer routes). Now the bot only asks the registry for a sign-in link, which the console
opens in a browser, and reads the result back. These pin that side against a mocked registry.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import marketplace
from olisar.db.models import Base, SigningIdentity
from olisar.extensions import signing

LINK = "https://discord.com/oauth2/authorize?client_id=registry-app&state=" + "a" * 64


class _Registry:
    """Stands in for the registry: ``_registry_post`` and ``_registry_get``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.verified = False
        self.stale = set()  # tokens the registry no longer accepts
        self.link = LINK

    async def post(self, path: str, body: dict, token: str | None = None) -> httpx.Response:
        self.calls.append((path, token))
        if token in self.stale:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "/v1/publishers/verify/start":
            return httpx.Response(200, json={"url": self.link, "expires_at": 0})
        return httpx.Response(404, json={"error": "not found"})

    async def get(self, path: str, params: dict | None = None, token: str | None = None) -> httpx.Response:
        self.calls.append((path, token))
        if path == "/v1/publishers/me":
            return httpx.Response(200, json={"handle": "mine", "verified": self.verified})
        return httpx.Response(404, json={"error": "not found"})


class VerifyTests(unittest.IsolatedAsyncioTestCase):
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
        self.registry = _Registry()
        for patcher in (
            patch.object(marketplace, "session_scope", scope),
            patch.object(marketplace, "_registry_post", self.registry.post),
            patch.object(marketplace, "_registry_get", self.registry.get),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.admin = SimpleNamespace(is_allowlisted=True, discord_user_id=1)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def claim(self, token: str = "tok-1") -> None:
        async with self.scope() as s:
            ident = await signing.ensure_identity(s)
            ident.registry_handle, ident.registry_token = "mine", token

    async def cached_verified(self) -> bool:
        async with self.scope() as s:
            return bool((await s.get(SigningIdentity, 1)).registry_verified)

    async def test_start_hands_back_the_registrys_link(self) -> None:
        await self.claim()
        out = await marketplace.verify_start(self.admin)
        self.assertEqual(out, {"url": LINK})
        self.assertEqual(self.registry.calls, [("/v1/publishers/verify/start", "tok-1")])

    async def test_a_link_that_isnt_discords_is_refused(self) -> None:
        await self.claim()
        self.registry.link = "https://evil.example/phish"
        with self.assertRaises(HTTPException) as refused:
            await marketplace.verify_start(self.admin)
        self.assertEqual(refused.exception.status_code, 502)

    async def test_a_stale_token_is_refreshed_once(self) -> None:
        await self.claim("old")
        self.registry.stale.add("old")

        async def reregister(_admin=None):
            return "new"

        with patch.object(marketplace, "_reregister_token", reregister):
            out = await marketplace.verify_start(self.admin)
        self.assertEqual(out["url"], LINK)
        self.assertEqual([t for _p, t in self.registry.calls], ["old", "new"])

    async def test_start_needs_a_handle_and_the_operator(self) -> None:
        with self.assertRaises(HTTPException) as no_handle:
            await marketplace.verify_start(self.admin)
        self.assertEqual(no_handle.exception.status_code, 400)
        await self.claim()
        with self.assertRaises(HTTPException) as not_operator:
            await marketplace.verify_start(SimpleNamespace(is_allowlisted=False, discord_user_id=2))
        self.assertEqual(not_operator.exception.status_code, 403)

    async def test_the_status_picks_up_a_verification_finished_in_the_browser(self) -> None:
        await self.claim()
        self.assertFalse((await marketplace.publisher(self.admin))["verified"])
        self.registry.verified = True  # the operator confirmed on the registry's page
        status = await marketplace.publisher(self.admin)
        self.assertTrue(status["verified"])
        self.assertTrue(await self.cached_verified())
        asked = len(self.registry.calls)
        await marketplace.publisher(self.admin)
        self.assertEqual(len(self.registry.calls), asked, "a verified publisher isn't asked again")

    async def test_the_old_bot_side_sign_in_is_gone(self) -> None:
        paths = {r.path for r in marketplace.router.routes}
        self.assertNotIn("/api/marketplace/verify/callback", paths)
        self.assertFalse(hasattr(marketplace, "complete_discord_verification"))


if __name__ == "__main__":
    unittest.main()
