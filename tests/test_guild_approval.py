"""A server someone else adds the bot to waits for the operator's approval.

Run:  uv run python -m unittest tests.test_guild_approval -v

New Discord applications are public, so anyone could add the bot to a throwaway server of
their own, and with Manage Server there, sign in to the console. These check that such a
server gets nothing until the operator approves it: no console sign-in, no place in the
server switcher, no slash commands, no replies, nothing stored. And that the servers the
operator did mean to use (the home server, the first one, ones they own, ones from before
approval existed) are approved as they arrive.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import httpx

from api.auth import deps
from api.auth.sessions import COOKIE_NAME, create_session, sign_sid
from api.main import create_app
from olisar import discord_app, guild_approval, runtime_config, runtime_keys
from olisar.config import settings
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import AdminGrant, AdminUser, Guild, utcnow
from olisar.guild_setup import ensure_guild_defaults

HOME = 1001        # the operator's own server
STRANGER = 2002    # a throwaway server someone else added the bot to
OPERATOR = 999
STRANGER_ADMIN = 222
MANAGE_GUILD = 1 << 5


class _DB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"OLISAR_HOME": tmp.name, "OLISAR_DATA_DIR": tmp.name,
                                           "OLISAR_NO_DOTENV": "1"})
        env.start()
        self.addCleanup(env.stop)
        for patcher in (
            mock.patch.object(settings, "target_guild_id", HOME),
            mock.patch.object(settings, "admin_allowlist", [OPERATOR]),
            mock.patch.object(discord_app, "owner_ids", mock.AsyncMock(return_value=set())),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        engine.pin_database(str(self.tmp / "bot.db"))
        runtime_config.invalidate()
        runtime_keys.invalidate()
        await create_schema()
        deps._last_check.clear()
        guild_approval._pending.clear()
        self.addCleanup(guild_approval._pending.clear)

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def arrive(self, guild_id: int, owner_id: int = 7, name: str = "") -> bool:
        """The bot is added to a server: the guilds cog records it, as on_guild_join does."""
        from bot.cogs.guilds import Guilds

        cog = Guilds.__new__(Guilds)
        cog.bot = NS(user=NS(display_name="Olisar"))
        guild = NS(id=guild_id, owner_id=owner_id, name=name or f"g{guild_id}", icon=None, me=None)
        return await cog._provision(guild)

    async def approved(self, guild_id: int) -> bool:
        async with session_scope() as s:
            return bool((await s.get(Guild, guild_id)).approved)


class ArrivalTests(_DB):
    async def test_the_home_server_is_approved(self) -> None:
        await self.arrive(STRANGER)  # first in: approved as the one setup invited it to
        self.assertTrue(await self.arrive(HOME))

    async def test_the_first_server_is_approved_and_a_strangers_next_one_waits(self) -> None:
        with mock.patch.object(settings, "target_guild_id", 0):
            self.assertTrue(await self.arrive(3003))
            self.assertFalse(await self.arrive(STRANGER))
        self.assertTrue(guild_approval.is_pending(STRANGER))
        self.assertFalse(guild_approval.is_pending(3003))

    async def test_a_server_the_operator_owns_is_approved(self) -> None:
        await self.arrive(HOME)
        self.assertTrue(await self.arrive(4004, owner_id=OPERATOR))
        with mock.patch.object(discord_app, "owner_ids", mock.AsyncMock(return_value={77})):
            self.assertTrue(await self.arrive(5005, owner_id=77))  # an owner of the Discord app

    async def test_rejoining_keeps_the_decision(self) -> None:
        await self.arrive(HOME)
        self.assertFalse(await self.arrive(STRANGER))
        self.assertFalse(await self.arrive(STRANGER))  # re-added after leaving: still waits
        await guild_approval.approve(STRANGER)
        self.assertTrue(await self.arrive(STRANGER))

    async def test_servers_from_before_approval_existed_stay_approved(self) -> None:
        async with session_scope() as s:
            await ensure_guild_defaults(s, STRANGER, name="Old")
        for path in list(engine._engines):
            await engine.reset_engine(path)
        with sqlite3.connect(self.tmp / "bot.db") as con:
            con.execute("ALTER TABLE guild DROP COLUMN approved")
        from scripts.init_db import create_schema

        await create_schema()
        self.assertTrue(await self.approved(STRANGER))
        self.assertTrue(await self.arrive(STRANGER))


class ConsoleTests(_DB):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.arrive(HOME, owner_id=OPERATOR, name="Home")
        await self.arrive(STRANGER, name="Throwaway")
        async with session_scope() as s:
            s.add_all([
                AdminUser(discord_user_id=STRANGER_ADMIN, username="stranger", is_allowlisted=False,
                          granted_via=AdminGrant.manage_guild, managed_guild_ids=[str(STRANGER)],
                          last_login=utcnow()),
                AdminUser(discord_user_id=OPERATOR, username="op", is_allowlisted=True,
                          granted_via=AdminGrant.allowlist, managed_guild_ids=[], last_login=utcnow()),
            ])
        await runtime_config.save(discord_client_id="123", discord_client_secret="secret")
        self.app = create_app()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("198.51.100.4", 5)),
                                 base_url="http://127.0.0.1:8000")

    async def cookie(self, user_id: int) -> str:
        return f"{COOKIE_NAME}={await sign_sid(await create_session(user_id))}"

    async def sign_in(self, guilds: list[dict]) -> httpx.Response:
        """The whole OAuth callback, with Discord's answers faked."""
        async def discord(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/oauth2/token"):
                return httpx.Response(200, json={"access_token": "t"})
            if request.url.path.endswith("/users/@me"):
                return httpx.Response(200, json={"id": str(STRANGER_ADMIN), "username": "stranger"})
            return httpx.Response(200, json=guilds)

        real = httpx.AsyncClient
        async with self.client() as c:
            with mock.patch("api.auth.oauth.httpx.AsyncClient",
                            lambda *a, **kw: real(transport=httpx.MockTransport(discord))):
                login = await c.get("/auth/login", follow_redirects=False)
                state = urllib.parse.parse_qs(urllib.parse.urlsplit(login.headers["location"]).query)["state"][0]
                return await c.get(f"/auth/callback?code=x&state={urllib.parse.quote(state)}",
                                   follow_redirects=False)

    async def test_an_admin_of_only_a_pending_server_cant_sign_in(self) -> None:
        r = await self.sign_in([{"id": str(STRANGER), "permissions": str(MANAGE_GUILD)}])
        self.assertIn("denied=role", r.headers.get("location", ""))
        self.assertNotIn(COOKIE_NAME, r.headers.get("set-cookie", ""))

    async def test_once_approved_they_can(self) -> None:
        await guild_approval.approve(STRANGER)
        r = await self.sign_in([{"id": str(STRANGER), "permissions": str(MANAGE_GUILD)}])
        self.assertIn(f"{COOKIE_NAME}=", r.headers.get("set-cookie", ""))

    async def test_a_pending_server_is_nobodys_to_configure(self) -> None:
        async with self.client() as c:
            for who in (STRANGER_ADMIN, OPERATOR):
                headers = {"cookie": await self.cookie(who), "x-guild-id": str(STRANGER)}
                r = await c.get("/api/persona", headers=headers)
                self.assertEqual(r.status_code, 403, who)
                listed = await c.get("/api/guilds", headers=headers)
                self.assertNotIn(str(STRANGER), [g["id"] for g in listed.json()])

    async def test_the_operator_sees_it_waiting_and_approves_it(self) -> None:
        async with self.client() as c:
            op = {"cookie": await self.cookie(OPERATOR)}
            stranger = {"cookie": await self.cookie(STRANGER_ADMIN)}
            self.assertEqual((await c.get("/api/guilds/pending", headers=stranger)).status_code, 403)
            self.assertEqual((await c.post(f"/api/guilds/{STRANGER}/approve", headers=stranger)).status_code, 403)
            pending = await c.get("/api/guilds/pending", headers=op)
            self.assertEqual(pending.json(), [{"id": str(STRANGER), "name": "Throwaway", "icon": ""}])

            synced = mock.AsyncMock()
            bot = NS(is_ready=lambda: True, get_cog=lambda name: NS(approved=synced))
            self.app.state.bot_supervisor = NS(bot=bot)
            r = await c.post(f"/api/guilds/{STRANGER}/approve", headers=op)
            self.assertEqual(r.status_code, 200, r.text)
            synced.assert_awaited_once_with(STRANGER)
            self.assertEqual((await c.get("/api/guilds/pending", headers=op)).json(), [])
            listed = await c.get("/api/guilds", headers=op)
            self.assertIn(str(STRANGER), [g["id"] for g in listed.json()])
        self.assertFalse(guild_approval.is_pending(STRANGER))

    async def test_the_operator_can_have_the_bot_leave_instead(self) -> None:
        guild = NS(leave=mock.AsyncMock())
        bot = NS(is_ready=lambda: True, get_guild=lambda gid: guild if gid == STRANGER else None)
        self.app.state.bot_supervisor = NS(bot=bot)
        async with self.client() as c:
            op = {"cookie": await self.cookie(OPERATOR)}
            self.assertEqual((await c.post(f"/api/guilds/{HOME}/leave", headers=op)).status_code, 409)
            r = await c.post(f"/api/guilds/{STRANGER}/leave", headers=op)
        self.assertEqual(r.status_code, 200, r.text)
        guild.leave.assert_awaited_once()


class BotTests(_DB):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.arrive(HOME)
        await self.arrive(STRANGER)

    async def test_slash_commands_refuse_in_a_pending_server(self) -> None:
        from bot.client import _Tree

        tree = _Tree.__new__(_Tree)
        sent = mock.AsyncMock()
        it = NS(guild_id=STRANGER, response=NS(send_message=sent))
        self.assertFalse(await tree.interaction_check(it))
        sent.assert_awaited_once()
        self.assertTrue(await tree.interaction_check(NS(guild_id=HOME, response=NS(send_message=sent))))

    async def test_messages_there_are_neither_stored_nor_answered(self) -> None:
        from bot.cogs import conversation

        cog = conversation.Conversation.__new__(conversation.Conversation)
        cog.bot = NS(user=NS(id=1))
        message = NS(id=5, author=NS(id=2), guild=NS(id=STRANGER))
        with mock.patch.object(conversation, "message_text", side_effect=AssertionError("read")):
            await cog._handle(message)  # returns before touching the message

    async def test_sharing_only_a_pending_server_doesnt_count_in_dms(self) -> None:
        from bot.access import resolve_member

        member = NS(id=2)
        pending = NS(id=STRANGER, get_member=lambda uid: member)
        bot = NS(guilds=[pending], get_guild=lambda gid: None)
        self.assertIsNone(resolve_member(bot, NS(id=2)))


if __name__ == "__main__":
    unittest.main()
