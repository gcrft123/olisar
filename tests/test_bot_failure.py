"""Coverage for the bot saying why Discord refused it, and the way back.

Run:  uv run python -m unittest tests.test_bot_failure -v

A crash used to be a log line and nothing else, so every status surface called the bot
"offline". These pin what it says instead: which intents are off (Discord's refusal doesn't
name them), that the reason outlives the crash until a later attempt gets through, and that
reconnecting only restarts a bot Discord will now let in.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from olisar.runtime.server import BotSupervisor, _describe_failure


class BotFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_intents_are_named_from_the_application(self) -> None:
        app = {"id": "1500", "intents_missing": ["message_content"]}
        with patch("olisar.discord_app.inspect", AsyncMock(return_value=app)):
            out = await _describe_failure(discord.PrivilegedIntentsRequired(None), "tok")
        self.assertEqual(out, {"kind": "intents", "missing": ["message_content"], "app_id": "1500"})

    async def test_every_intent_is_named_when_discord_cant_be_asked(self) -> None:
        with patch("olisar.discord_app.inspect", AsyncMock(side_effect=OSError("offline"))), \
                patch("olisar.config.settings.enable_presence_intent", False):
            out = await _describe_failure(discord.PrivilegedIntentsRequired(None), "tok")
        self.assertEqual(out["missing"], ["message_content", "members"])

    async def test_other_failures(self) -> None:
        self.assertEqual(await _describe_failure(discord.LoginFailure("bad"), "tok"), {"kind": "token"})
        out = await _describe_failure(RuntimeError("boom"), "tok")
        self.assertEqual(out, {"kind": "other", "message": "boom"})

    def test_the_error_clears_once_a_later_attempt_is_ready(self) -> None:
        sup = BotSupervisor()
        sup._error = {"kind": "token"}
        sup._bot = SimpleNamespace(is_ready=lambda: False)
        self.assertEqual(sup.error, {"kind": "token"})  # still connecting: keep the reason
        sup._bot = SimpleNamespace(is_ready=lambda: True)
        self.assertIsNone(sup.error)


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, sup):
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot_supervisor=sup)))

    async def _reconnect(self, missing: list[str], *, operator: bool = True):
        from api.routers import bot as bot_router

        sup = SimpleNamespace(restart=AsyncMock(), running=False, bot=None, error=None)
        app = {"id": "1500", "intents_missing": missing}
        with patch.object(bot_router.runtime_config, "discord_token", AsyncMock(return_value="tok")), \
                patch("olisar.discord_app.prepare", AsyncMock(return_value=app)):
            out = await bot_router.reconnect(self._request(sup), SimpleNamespace(is_allowlisted=operator, discord_user_id=1))
        return out, sup

    async def test_restarts_once_nothing_is_missing(self) -> None:
        out, sup = await self._reconnect([])
        sup.restart.assert_awaited_once()
        self.assertEqual(out["intents_missing"], [])

    async def test_leaves_the_bot_alone_while_an_intent_is_still_off(self) -> None:
        # Discord would only refuse it again. The operator gets the switch to flip instead.
        out, sup = await self._reconnect(["message_content"])
        sup.restart.assert_not_awaited()
        self.assertEqual((out["intents_missing"], out["app_id"]), (["message_content"], "1500"))

    async def test_only_the_operator(self) -> None:
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            await self._reconnect([], operator=False)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
