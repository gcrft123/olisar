"""Coverage for what setup does to the operator's Discord application, and the invite link.

Run:  uv run python -m unittest tests.test_discord_setup -v

Setup writes to an application the operator owns, so the tests that matter most pin what
it leaves alone:

  * intents already on (either flavor) mean no PATCH at all
  * a custom install URL, or a guild install the operator switched off, is never rewritten
  * a fix Discord refuses is reported, not assumed

Plus the invite link's permission number, checked against discord.py's own names so a
typo'd bit can't hand every server a permission Olisar never uses.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from fastapi import HTTPException

from api.routers import admin, setup
from olisar import discord_app

MEMBERS_LIMITED = 1 << 15
CONTENT_LIMITED = 1 << 19
CONTENT_FULL = 1 << 18
MEMBERS_FULL = 1 << 14

DEFAULT_INSTALL = {
    "0": {"oauth2_install_params": {"scopes": ["applications.commands"], "permissions": "0"}},
    "1": {"oauth2_install_params": {"scopes": ["applications.commands"], "permissions": "0"}},
}
BOT_INSTALL = {
    "0": {"oauth2_install_params": {"scopes": ["bot", "applications.commands"], "permissions": "274878024768"}},
}


def _app(**over) -> dict:
    app = {
        "id": "1500", "name": "Olisar", "flags": 0, "bot_public": True, "bot_require_code_grant": False,
        "redirect_uris": [], "integration_types_config": BOT_INSTALL,
        "bot": {"id": "1500", "username": "Olisar", "avatar": "abc"},
    }
    app.update(over)
    return app


class FakeDiscord:
    """Stands in for ``discord_app._call``: answers GETs with the application, applies a
    PATCH's body to it (the way Discord answers with the updated application), and
    records every PATCH so a test can assert on what setup changed."""

    def __init__(self, app: dict, *, patch_status: int = 200) -> None:
        self.app = app
        self.patch_status = patch_status
        self.patches: list[dict] = []

    async def __call__(self, method, url, *, headers, json=None, data=None):
        if method == "GET":
            return 200, dict(self.app)
        if method == "PATCH":
            self.patches.append(json)
            if self.patch_status != 200:
                return self.patch_status, {"message": "nope"}
            if "flags" in json:
                self.app["flags"] = json["flags"]
            if "integration_types_config" in json:
                self.app["integration_types_config"] = json["integration_types_config"]
            # A PATCH's answer isn't promised to carry the bot user.
            return 200, {k: v for k, v in self.app.items() if k != "bot"}
        raise AssertionError(f"unexpected {method} {url}")


def _presence(on: bool):
    return patch("olisar.config.settings.enable_presence_intent", on)


class InviteLinkTests(unittest.TestCase):
    def test_permission_bits_match_discords_names(self) -> None:
        expected = discord.Permissions(**{name: True for name in discord_app.INVITE_PERMISSIONS})
        self.assertEqual(discord_app.INVITE_PERMISSIONS_VALUE, expected.value)
        self.assertEqual(discord_app.INVITE_PERMISSIONS_VALUE, 274878024768)

    def test_never_asks_for_more_than_messaging(self) -> None:
        granted = discord.Permissions(discord_app.INVITE_PERMISSIONS_VALUE)
        for risky in ("administrator", "mention_everyone", "manage_messages", "manage_roles", "manage_guild"):
            self.assertFalse(getattr(granted, risky), risky)

    def test_url_adds_the_bot_and_its_commands(self) -> None:
        url = discord_app.invite_url("1500")
        self.assertEqual(
            url,
            "https://discord.com/oauth2/authorize?client_id=1500"
            "&scope=bot+applications.commands&permissions=274878024768",
        )


class PrepareTests(unittest.IsolatedAsyncioTestCase):
    async def test_turns_on_the_missing_intents_with_the_self_serve_flags(self) -> None:
        fake = FakeDiscord(_app(flags=0))
        with patch.object(discord_app, "_call", fake), _presence(False):
            out = await discord_app.prepare("tok")
        self.assertEqual(fake.patches, [{"flags": MEMBERS_LIMITED | CONTENT_LIMITED}])
        self.assertEqual(out["intents_missing"], [])
        # The bot user came from the GET; the PATCH's answer left it out.
        self.assertEqual(out["username"], "Olisar")
        self.assertEqual(out["avatar"], "https://cdn.discordapp.com/avatars/1500/abc.png")

    async def test_keeps_the_flags_it_found(self) -> None:
        other = 1 << 23  # a flag setup has no business clearing
        fake = FakeDiscord(_app(flags=other | CONTENT_LIMITED))
        with patch.object(discord_app, "_call", fake), _presence(False):
            await discord_app.prepare("tok")
        self.assertEqual(fake.patches, [{"flags": other | CONTENT_LIMITED | MEMBERS_LIMITED}])

    async def test_leaves_an_app_alone_when_its_intents_are_on(self) -> None:
        for flags in (MEMBERS_LIMITED | CONTENT_LIMITED, MEMBERS_FULL | CONTENT_FULL):
            fake = FakeDiscord(_app(flags=flags))
            with patch.object(discord_app, "_call", fake), _presence(False):
                out = await discord_app.prepare("tok")
            self.assertEqual(fake.patches, [], flags)
            self.assertEqual(out["intents_missing"], [])

    async def test_presence_is_only_asked_for_when_the_host_uses_it(self) -> None:
        fake = FakeDiscord(_app(flags=MEMBERS_LIMITED | CONTENT_LIMITED))
        with patch.object(discord_app, "_call", fake), _presence(True):
            await discord_app.prepare("tok")
        self.assertEqual(fake.patches, [{"flags": MEMBERS_LIMITED | CONTENT_LIMITED | (1 << 13)}])

    async def test_reports_intents_discord_would_not_turn_on(self) -> None:
        fake = FakeDiscord(_app(flags=0), patch_status=403)
        with patch.object(discord_app, "_call", fake), _presence(False):
            out = await discord_app.prepare("tok")
        self.assertEqual(out["intents_missing"], ["message_content", "members"])

    async def test_makes_the_default_install_add_the_bot(self) -> None:
        fake = FakeDiscord(_app(flags=MEMBERS_LIMITED | CONTENT_LIMITED, integration_types_config=DEFAULT_INSTALL))
        with patch.object(discord_app, "_call", fake), _presence(False):
            await discord_app.prepare("tok")
        params = {"scopes": ["bot", "applications.commands"], "permissions": "274878024768"}
        self.assertEqual(fake.patches, [{
            "install_params": params,
            # The user install is handed back untouched.
            "integration_types_config": {"0": {"oauth2_install_params": params}, "1": DEFAULT_INSTALL["1"]},
        }])

    async def test_leaves_an_install_the_operator_chose(self) -> None:
        guild_off = {"1": DEFAULT_INSTALL["1"]}
        for over in ({"custom_install_url": "https://example.com/add"}, {"integration_types_config": guild_off}):
            app = _app(flags=MEMBERS_LIMITED | CONTENT_LIMITED, **({"integration_types_config": DEFAULT_INSTALL} | over))
            fake = FakeDiscord(app)
            with patch.object(discord_app, "_call", fake), _presence(False):
                await discord_app.prepare("tok")
            self.assertEqual(fake.patches, [], over)

    async def test_a_rejected_token_is_its_own_error(self) -> None:
        with patch.object(discord_app, "_call", AsyncMock(return_value=(401, {"message": "401: Unauthorized"}))):
            with self.assertRaises(discord_app.BadToken):
                await discord_app.prepare("tok")

    async def test_the_summary_carries_what_the_wizard_gates_on(self) -> None:
        fake = FakeDiscord(_app(
            flags=MEMBERS_LIMITED | CONTENT_LIMITED, bot_require_code_grant=True, bot_public=False,
            redirect_uris=["http://127.0.0.1:8723/auth/callback"],
        ))
        with patch.object(discord_app, "_call", fake), _presence(False):
            out = await discord_app.inspect("tok")
        self.assertEqual(out["id"], "1500")
        self.assertTrue(out["code_grant"])
        self.assertFalse(out["bot_public"])
        self.assertEqual(out["redirect_uris"], ["http://127.0.0.1:8723/auth/callback"])
        self.assertEqual(out["invite_url"], discord_app.invite_url("1500"))


class ClientSecretTests(unittest.IsolatedAsyncioTestCase):
    async def test_answers_by_status(self) -> None:
        for status, expected in ((200, True), (401, False), (400, False)):
            with patch.object(discord_app, "_call", AsyncMock(return_value=(status, {}))):
                self.assertIs(await discord_app.check_client_secret("1500", "s"), expected, status)

    async def test_an_outage_is_not_a_wrong_secret(self) -> None:
        with patch.object(discord_app, "_call", AsyncMock(return_value=(503, None))):
            with self.assertRaises(discord_app.DiscordUnavailable):
                await discord_app.check_client_secret("1500", "s")


class SetupErrorTests(unittest.IsolatedAsyncioTestCase):
    async def _status(self, exc: Exception) -> int:
        async def boom():
            raise exc
        with self.assertRaises(HTTPException) as ctx:
            await setup._ask_discord(boom())
        return ctx.exception.status_code

    async def test_a_bad_token_is_the_operators_to_fix(self) -> None:
        self.assertEqual(await self._status(discord_app.BadToken()), 400)

    async def test_an_unreachable_discord_is_not(self) -> None:
        import aiohttp
        self.assertEqual(await self._status(aiohttp.ClientConnectionError()), 502)
        self.assertEqual(await self._status(discord_app.DiscordUnavailable(500)), 502)


class InviteEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def _invite(self, app: dict | None, *, operator: bool) -> dict:
        admin_user = SimpleNamespace(is_allowlisted=operator)
        with patch.object(discord_app, "application", AsyncMock(return_value=app)), \
                patch.object(admin.runtime_config, "discord_client_id", AsyncMock(return_value="1500")):
            return await admin.invite(admin_user)

    async def test_a_private_bot_is_only_the_operators_to_add(self) -> None:
        self.assertFalse((await self._invite(_app(bot_public=False), operator=False))["available"])
        self.assertTrue((await self._invite(_app(bot_public=False), operator=True))["available"])
        self.assertTrue((await self._invite(_app(bot_public=True), operator=False))["available"])

    async def test_falls_back_to_the_saved_client_id(self) -> None:
        out = await self._invite(None, operator=True)
        self.assertEqual(out["url"], discord_app.invite_url("1500"))


if __name__ == "__main__":
    unittest.main()
