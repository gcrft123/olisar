"""Someone taken off the allowlist or the app's team stops being the operator.

Run:  uv run python -m unittest tests.test_operator_revalidation -v

Operator status was decided once, at sign-in, and every request after that skipped the
re-check because the admin was "allowlisted". Someone removed from ADMIN_ALLOWLIST or from
the bot's Discord app team kept the API keys, extension authoring and every server until the
14-day session ran out. Sessions now re-apply the sign-in test every few minutes (the app
object is cached, so that isn't a Discord request per call). When Discord can't be read, the
standing from sign-in holds rather than locking the operator out.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import httpx

from api.auth import deps
from api.auth.sessions import COOKIE_NAME, create_session, sign_sid
from api.main import create_app
from olisar import discord_app, runtime_config, runtime_keys
from olisar.config import settings
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import AdminGrant, AdminUser, utcnow
from olisar.guild_setup import ensure_guild_defaults

HOME = 1001
OPERATOR = 999
APP = {"id": "1", "owner": {"id": str(OPERATOR)}}
APP_WITHOUT_THEM = {"id": "1", "owner": {"id": "5"}}


class OperatorRecheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"OLISAR_HOME": tmp.name, "OLISAR_DATA_DIR": tmp.name,
                                           "OLISAR_NO_DOTENV": "1"})
        env.start()
        self.addCleanup(env.stop)
        allow = mock.patch.object(settings, "admin_allowlist", [])
        allow.start()
        self.addCleanup(allow.stop)
        engine.pin_database(os.path.join(tmp.name, "bot.db"))
        runtime_config.invalidate()
        runtime_keys.invalidate()
        await create_schema()
        deps._last_check.clear()
        async with session_scope() as s:
            await ensure_guild_defaults(s, HOME, name="Home")
        async with session_scope() as s:
            s.add(AdminUser(discord_user_id=OPERATOR, username="op", is_allowlisted=True,
                            granted_via=AdminGrant.allowlist, managed_guild_ids=[str(HOME)],
                            last_login=utcnow()))
        self.cookie = f"{COOKIE_NAME}={await sign_sid(await create_session(OPERATOR))}"
        self.app = create_app()

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def get(self, path: str, application, **headers) -> int:
        """GET ``path`` as the operator with the bot's app answering ``application``."""
        transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 5000))
        with mock.patch.object(discord_app, "application", application):
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://127.0.0.1:8000") as c:
                r = await c.get(path, headers={"cookie": self.cookie, **headers})
        return r.status_code

    async def stored(self) -> AdminUser:
        async with session_scope() as s:
            return await s.get(AdminUser, OPERATOR)

    async def test_an_owner_of_the_app_stays_the_operator(self) -> None:
        self.assertEqual(await self.get("/api/keys", mock.AsyncMock(return_value=APP)), 200)
        self.assertTrue((await self.stored()).is_allowlisted)

    async def test_someone_off_the_team_loses_operator_routes(self) -> None:
        status = await self.get("/api/keys", mock.AsyncMock(return_value=APP_WITHOUT_THEM))
        self.assertEqual(status, 403)
        row = await self.stored()
        self.assertFalse(row.is_allowlisted)
        self.assertEqual(row.granted_via, AdminGrant.manage_guild)

    async def test_and_keeps_what_manage_server_gives_them(self) -> None:
        app = mock.AsyncMock(return_value=APP_WITHOUT_THEM)
        self.assertEqual(await self.get("/api/keys", app), 403)
        self.assertEqual(await self.get("/api/channels", app, **{"x-guild-id": str(HOME)}), 200)

    async def test_the_allowlist_alone_is_enough(self) -> None:
        app = mock.AsyncMock(return_value=APP_WITHOUT_THEM)
        with mock.patch.object(settings, "admin_allowlist", [OPERATOR]):
            self.assertEqual(await self.get("/api/keys", app), 200)
        app.assert_not_awaited()

    async def test_the_mock_operator_stays_one_while_mock_auth_is_on(self) -> None:
        """OLISAR_MOCK_AUTH signs its user in as the operator; a token set for testing
        alongside it mustn't take that away five minutes later."""
        from api.auth.oauth import MOCK_USER_ID

        async with session_scope() as s:
            s.add(AdminUser(discord_user_id=MOCK_USER_ID, username="mockoperator",
                            is_allowlisted=True, granted_via=AdminGrant.allowlist,
                            managed_guild_ids=[str(HOME)], last_login=utcnow()))
        self.cookie = f"{COOKIE_NAME}={await sign_sid(await create_session(MOCK_USER_ID))}"
        app = mock.AsyncMock(return_value=APP_WITHOUT_THEM)
        with mock.patch.object(settings, "mock_auth", True):
            self.assertEqual(await self.get("/api/keys", app), 200)
        deps._last_check.clear()
        with mock.patch.object(settings, "mock_auth", False):
            self.assertEqual(await self.get("/api/keys", app), 403)

    async def test_an_unreadable_app_keeps_the_standing_from_sign_in(self) -> None:
        """No token, or Discord down: the operator may be here to fix exactly that."""
        self.assertEqual(await self.get("/api/keys", mock.AsyncMock(return_value=None)), 200)
        self.assertTrue((await self.stored()).is_allowlisted)

    async def test_the_app_is_read_again_only_every_few_minutes(self) -> None:
        app = mock.AsyncMock(return_value=APP)
        self.assertEqual(await self.get("/api/keys", app), 200)
        self.assertEqual(app.await_args_list[0].kwargs.get("max_age"),
                         deps._OPERATOR_RECHECK_SECONDS)
        reads = app.await_count
        for _ in range(2):
            self.assertEqual(await self.get("/api/keys", app), 200)
        self.assertEqual(app.await_count, reads)


if __name__ == "__main__":
    unittest.main()
