"""The bot proves it owns its publisher key when it registers on the marketplace registry.

Run:  uv run python -m unittest tests.test_registry_register -v

The registry used to take whatever ``discord_id`` a register call sent, and asked for no
proof of the private key. Anyone could register with the owner's Discord id and get the
developer routes, or POST a victim's public key (it's in every signed bundle) and rotate
their token out. Now the registry issues a single-use nonce, the bot signs
``signing.register_message(nonce, handle)``, and no Discord id is sent at all. These tests
pin the bot side of that against a mocked registry: ``register()`` and
``_reregister_token()`` (which the developer console proxy reuses) both fetch a challenge,
sign the exact message the Worker checks, and never send ``discord_id``.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import dev, marketplace
from api.schemas import MarketplaceRegisterIn
from olisar.db.models import Base, SigningIdentity
from olisar.extensions import bundle, signing

CHALLENGE = "/v1/publishers/challenge"
REGISTER = "/v1/publishers/register"


class _FakeRegistry:
    """Stands in for ``_registry_post``: records every call and hands out fresh nonces."""

    def __init__(self, *, challenge_status: int = 200, register_status: int = 200):
        self.calls: list[tuple[str, dict, str | None]] = []
        self.nonces: list[str] = []
        self.challenge_status = challenge_status
        self.register_status = register_status

    async def __call__(self, path: str, body: dict, token: str | None = None) -> httpx.Response:
        self.calls.append((path, body, token))
        if path == CHALLENGE:
            if self.challenge_status != 200:
                return httpx.Response(self.challenge_status, json={"error": "not found"})
            nonce = f"{len(self.nonces):064x}"
            self.nonces.append(nonce)
            return httpx.Response(200, json={"nonce": nonce, "expires_at": 0})
        if path == REGISTER:
            if self.register_status != 200:
                return httpx.Response(self.register_status, json={"error": "refused"})
            n = sum(1 for p, _b, _t in self.calls if p == REGISTER)
            return httpx.Response(200, json={
                "ok": True, "handle": body["handle"], "fingerprint": "sha256:x",
                "token": f"token-{n}",
            })
        return httpx.Response(404, json={"error": "not found"})

    def bodies(self, path: str) -> list[dict]:
        return [b for p, b, _t in self.calls if p == path]


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
        self.admin = MagicMock(is_allowlisted=True, discord_user_id=1089250623490359378)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._tmp.cleanup()

    async def identity(self) -> SigningIdentity:
        async with self.scope() as s:
            return await signing.ensure_identity(s)

    @contextlib.contextmanager
    def wired(self, registry: _FakeRegistry):
        with patch.object(marketplace, "session_scope", self.scope), \
             patch.object(marketplace, "_registry_post", registry):
            yield

    def assert_signed_proof(self, body: dict, nonce: str, handle: str, public_key: str) -> None:
        self.assertEqual(set(body), {"public_key", "handle", "nonce", "signature"})
        self.assertNotIn("discord_id", body)
        self.assertEqual(body["public_key"], public_key)
        self.assertEqual(body["nonce"], nonce)
        self.assertEqual(body["handle"], handle)
        message = signing.register_message(nonce, handle)
        self.assertEqual(message, f"olisar-registry/register:{nonce}:{handle}")
        self.assertTrue(signing.verify(public_key, message, body["signature"]))
        # Bound to this nonce and this handle, so it's no good for anything else.
        other = f"{int(nonce, 16) + 1:064x}"
        self.assertFalse(signing.verify(public_key, signing.register_message(other, handle),
                                        body["signature"]))
        self.assertFalse(signing.verify(public_key, signing.register_message(nonce, "other"),
                                        body["signature"]))


class RegisterTests(_DB):
    async def test_register_signs_the_challenge_and_sends_no_discord_id(self) -> None:
        registry = _FakeRegistry()
        with self.wired(registry):
            out = await marketplace.register(MarketplaceRegisterIn(handle="acme"), self.admin)

        self.assertEqual([p for p, _b, _t in registry.calls], [CHALLENGE, REGISTER])
        ident = await self.identity()
        (body,) = registry.bodies(REGISTER)
        self.assert_signed_proof(body, registry.nonces[0], "acme", ident.public_key)
        self.assertNotIn(str(self.admin.discord_user_id), str(registry.calls))
        self.assertEqual(out["handle"], "acme")
        self.assertEqual(ident.registry_handle, "acme")
        self.assertEqual(ident.registry_token, "token-1")

    async def test_mixed_case_handle_is_signed_lowercased(self) -> None:
        """The registry lowercases the handle before rebuilding the message, so the bot
        must sign the same lowercased string or every register fails."""
        registry = _FakeRegistry()
        with self.wired(registry):
            await marketplace.register(MarketplaceRegisterIn(handle="  Acme "), self.admin)
        ident = await self.identity()
        (body,) = registry.bodies(REGISTER)
        self.assert_signed_proof(body, registry.nonces[0], "acme", ident.public_key)

    async def test_no_register_call_when_the_challenge_fails(self) -> None:
        registry = _FakeRegistry(challenge_status=404)
        with self.wired(registry):
            with self.assertRaises(HTTPException):
                await marketplace.register(MarketplaceRegisterIn(handle="acme"), self.admin)
        self.assertEqual(registry.bodies(REGISTER), [])
        ident = await self.identity()
        self.assertIsNone(ident.registry_token)

    async def test_registry_refusal_reaches_the_console(self) -> None:
        registry = _FakeRegistry(register_status=403)
        with self.wired(registry):
            with self.assertRaises(HTTPException) as caught:
                await marketplace.register(MarketplaceRegisterIn(handle="acme"), self.admin)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "refused")
        self.assertIsNone((await self.identity()).registry_token)


class ReregisterTests(_DB):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        async with self.scope() as s:
            ident = await signing.ensure_identity(s)
            ident.registry_handle = "acme"
            ident.registry_token = "stale"

    async def test_reregister_signs_a_fresh_challenge_each_time(self) -> None:
        registry = _FakeRegistry()
        with self.wired(registry):
            first = await marketplace._reregister_token(self.admin)
            second = await marketplace._reregister_token(self.admin)

        self.assertEqual((first, second), ("token-1", "token-2"))
        ident = await self.identity()
        self.assertEqual(ident.registry_token, "token-2")
        bodies = registry.bodies(REGISTER)
        self.assertEqual(len(bodies), 2)
        self.assertNotEqual(registry.nonces[0], registry.nonces[1])
        for body, nonce in zip(bodies, registry.nonces):
            self.assert_signed_proof(body, nonce, "acme", ident.public_key)

    async def test_reregister_keeps_the_old_token_when_refused(self) -> None:
        registry = _FakeRegistry(register_status=403)
        with self.wired(registry):
            self.assertIsNone(await marketplace._reregister_token(self.admin))
        self.assertEqual((await self.identity()).registry_token, "stale")

    async def test_reregister_without_a_handle_sends_nothing(self) -> None:
        async with self.scope() as s:
            (await signing.ensure_identity(s)).registry_handle = None
        registry = _FakeRegistry()
        with self.wired(registry):
            self.assertIsNone(await marketplace._reregister_token(self.admin))
        self.assertEqual(registry.calls, [])

    async def test_dev_proxy_refreshes_a_stale_token_through_the_signed_flow(self) -> None:
        """api/routers/dev.py imports ``_reregister_token``; a 401 from a /v1/dev/* route
        must still end in a signed re-register and a retry with the new token."""
        registry = _FakeRegistry()
        seen: list[str | None] = []

        async def dev_post(path: str, body: dict, token: str | None = None) -> httpx.Response:
            seen.append(token)
            return httpx.Response(401 if token == "stale" else 200, json={"ok": True})

        with self.wired(registry), \
             patch.object(dev, "session_scope", self.scope), \
             patch.object(dev, "_registry_post", dev_post):
            r = await dev._post("/v1/dev/reports/clear", {}, self.admin)

        self.assertEqual(r.status_code, 200)
        self.assertEqual(seen, ["stale", "token-1"])
        (body,) = registry.bodies(REGISTER)
        self.assert_signed_proof(body, registry.nonces[0], "acme",
                                 (await self.identity()).public_key)


class RegisterMessageTests(unittest.TestCase):
    def test_a_bundle_signature_is_not_a_register_proof(self) -> None:
        """Bundles sign their bare content_hash. The register message's domain prefix means
        a signature lifted from a published bundle can never pass as one."""
        priv, pub = signing.generate()
        doc = bundle.build_bundle(
            ext_id="demo", name="demo", version="1.0.0", category="General", description="",
            source="export default {}", permissions=[], sdk_version="1",
            author_id=1, author_name="t",
        )
        signing.sign_bundle(doc, priv, pub)
        content_hash = doc["content_hash"]
        self.assertTrue(signing.verify(pub, content_hash, doc["signature"]))
        for nonce in (content_hash, content_hash.removeprefix("sha256:"), "0" * 64):
            message = signing.register_message(nonce, "acme")
            self.assertTrue(message.startswith("olisar-registry/register:"))
            self.assertNotEqual(message, content_hash)
            self.assertFalse(signing.verify(pub, message, doc["signature"]))


if __name__ == "__main__":
    unittest.main()
