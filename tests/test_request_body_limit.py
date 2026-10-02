"""Request bodies are capped, and a large one is only read for someone signed in.

Run:  uv run python -m unittest tests.test_request_body_limit -v

FastAPI reads and parses a route's body before the route's sign-in check runs, and nothing
capped its size, so any visitor reaching the console over remote access could make the
backend buffer and parse as much as they sent. ``api.body_limit.BodyLimit`` now decides from
the headers first: past 64 KB a body needs a real credential (401 without one, before a byte
is read), and no body may pass its route's limit (1 MB, or 16 MB on the routes that take a
file; 413). A chunked body is counted as it arrives and stopped at the same points.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import httpx

from api import body_limit
from api.auth import deps
from api.auth.sessions import COOKIE_NAME, create_session, sign_sid
from api.main import create_app
from api.trust import LOCAL_HEADER, local_token
from olisar import runtime_config, runtime_keys
from olisar.config import settings
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import AdminGrant, AdminUser, utcnow

OPERATOR = 999
KB = 1024
MB = 1024 * KB


def _json(size: int, key: str = "gemini_api_key") -> bytes:
    """A JSON object of about ``size`` bytes."""
    return json.dumps({key: "a" * size}).encode()


async def _chunks(data: bytes, step: int = 16 * KB):
    for i in range(0, len(data), step):
        yield data[i:i + step]


class BodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"OLISAR_HOME": tmp.name, "OLISAR_DATA_DIR": tmp.name,
                                           "OLISAR_NO_DOTENV": "1"})
        env.start()
        self.addCleanup(env.stop)
        allow = mock.patch.object(settings, "admin_allowlist", [OPERATOR])
        allow.start()
        self.addCleanup(allow.stop)
        engine.pin_database(os.path.join(tmp.name, "bot.db"))
        runtime_config.invalidate()
        runtime_keys.invalidate()
        await create_schema()
        deps._last_check.clear()
        async with session_scope() as s:
            s.add(AdminUser(discord_user_id=OPERATOR, username="op", is_allowlisted=True,
                            granted_via=AdminGrant.allowlist, managed_guild_ids=[],
                            last_login=utcnow()))
        self.signed_in = {"cookie": f"{COOKIE_NAME}={await sign_sid(await create_session(OPERATOR))}"}
        self.app = create_app()
        self.read = 0

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def send(self, method: str, path: str, content, *, peer: str = "203.0.113.9",
                   headers: dict | None = None) -> httpx.Response:
        """Send a request as a visitor at ``peer`` and count the body bytes the app read."""
        async def counting(scope, receive, send):
            async def recv():
                message = await receive()
                if message.get("type") == "http.request":
                    self.read += len(message.get("body", b""))
                return message
            await self.app(scope, recv, send)

        transport = httpx.ASGITransport(app=counting, client=(peer, 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as c:
            return await c.request(method, path, content=content,
                                   headers={"content-type": "application/json", **(headers or {})})

    async def test_a_large_body_from_nobody_is_refused_unread(self) -> None:
        r = await self.send("PUT", "/api/keys", _json(4 * MB))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.read, 0)

    async def test_a_made_up_session_cookie_is_nobody(self) -> None:
        r = await self.send("PUT", "/api/keys", _json(200 * KB),
                            headers={"cookie": f"{COOKIE_NAME}=not-a-session"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.read, 0)

    async def test_a_small_body_from_nobody_reaches_the_route_as_before(self) -> None:
        r = await self.send("PUT", "/api/keys", _json(1 * KB))
        self.assertEqual(r.status_code, 401)
        self.assertGreater(self.read, 0)

    async def test_the_signed_in_are_capped_too(self) -> None:
        r = await self.send("PUT", "/api/keys", _json(2 * MB), headers=self.signed_in)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.read, 0)

    async def test_the_operator_at_the_machine_counts_as_signed_in(self) -> None:
        r = await self.send("PUT", "/api/keys", _json(200 * KB), peer="127.0.0.1",
                            headers={LOCAL_HEADER: local_token()})
        self.assertNotEqual(r.status_code, 413)
        self.assertGreater(self.read, 200 * KB)

    async def test_an_extension_bundle_gets_the_upload_limit(self) -> None:
        big = json.dumps({"bundle": {"pad": "a" * (2 * MB)}}).encode()
        r = await self.send("POST", "/api/extensions/authoring/import/preview", big,
                            headers=self.signed_in)
        self.assertNotIn(r.status_code, (401, 413), r.text)
        self.assertEqual(self.read, len(big))

        huge = json.dumps({"bundle": {"pad": "a" * (body_limit.UPLOAD_LIMIT + 1)}}).encode()
        self.read = 0
        r = await self.send("POST", "/api/extensions/authoring/import/preview", huge,
                            headers=self.signed_in)
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.read, 0)

    async def test_a_chunked_body_from_nobody_is_cut_off(self) -> None:
        r = await self.send("PUT", "/api/keys", _chunks(_json(4 * MB)))
        self.assertEqual(r.status_code, 401)
        self.assertLessEqual(self.read, body_limit.ANONYMOUS_LIMIT + 16 * KB)

    async def test_a_chunked_body_past_the_limit_is_cut_off(self) -> None:
        r = await self.send("PUT", "/api/keys", _chunks(_json(4 * MB)), headers=self.signed_in)
        self.assertEqual(r.status_code, 413)
        self.assertLessEqual(self.read, body_limit.DEFAULT_LIMIT + 16 * KB)

    async def test_a_chunked_body_within_the_limit_goes_through(self) -> None:
        body = json.dumps({"gemini_api_key": ""}).encode() + b" " * (200 * KB)
        r = await self.send("PUT", "/api/keys", _chunks(body), headers=self.signed_in)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.read, len(body))


class RouteLimitTests(unittest.TestCase):
    def test_upload_routes(self) -> None:
        for path in ("/api/extensions/authoring", "/api/extensions/authoring/import",
                     "/api/extensions/authoring/import/preview", "/api/extensions/authoring/x",
                     "/api/settings/feedback", "/api/marketplace/report"):
            self.assertEqual(body_limit.limit_for(path), body_limit.UPLOAD_LIMIT, path)

    def test_everything_else(self) -> None:
        for path in ("/api/keys", "/api/persona", "/api/extensions/authoringx",
                     "/api/settings/feedbackx", "/api/member/settings"):
            self.assertEqual(body_limit.limit_for(path), body_limit.DEFAULT_LIMIT, path)


if __name__ == "__main__":
    unittest.main()
