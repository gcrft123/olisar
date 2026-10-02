"""An extension tool's ``host.discord.send`` posts only in the server it runs in.

Run:  uv run python -m unittest tests.test_extension_send_scope -v

A channel given by id was looked up in the bot's whole cache, which holds every server's
channels and its DMs, so a tool enabled in one server could post into another server, or into
someone's DMs, just by naming the id. Now an id resolves only to a channel of the invoking
server; the current channel (no id, or its own id) still works, DM included.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.actions import BotActions
from olisar.sandbox.runner import _ToolBridge

HOME, OTHER = 111, 222


def _channel(guild_id: int | None, name: str = "general"):
    guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
    return SimpleNamespace(name=name, guild=guild, send=AsyncMock())


def _bot(channel) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    return bot


class PostScopeTests(unittest.IsolatedAsyncioTestCase):
    async def post(self, actions: BotActions, channel) -> str:
        return await actions.post_components(
            channel=channel, content="hello", ext_key="ext", home_guild_id=HOME, trusted=False,
        )

    async def test_a_channel_of_this_server_by_id(self) -> None:
        here = _channel(HOME)
        out = await self.post(BotActions(_bot(here)), "555")
        here.send.assert_awaited_once()
        self.assertIn("Posted", out)

    async def test_a_channel_mention_of_this_server(self) -> None:
        here = _channel(HOME)
        await self.post(BotActions(_bot(here)), "<#555>")
        here.send.assert_awaited_once()

    async def test_another_servers_channel_is_refused(self) -> None:
        foreign = _channel(OTHER)
        out = await self.post(BotActions(_bot(foreign)), "555")
        foreign.send.assert_not_awaited()
        self.assertIn("couldn't find a channel", out)

    async def test_a_dm_channel_is_refused(self) -> None:
        dm = SimpleNamespace(recipient=SimpleNamespace(id=9), send=AsyncMock())
        await self.post(BotActions(_bot(dm)), "555")
        dm.send.assert_not_awaited()

    async def test_no_home_server_means_no_channel_by_id(self) -> None:
        here = _channel(HOME)
        await BotActions(_bot(here)).post_components(
            channel="555", content="hello", ext_key="ext", home_guild_id=0, trusted=False,
        )
        here.send.assert_not_awaited()

    async def test_the_current_channel_still_works(self) -> None:
        current = _channel(None)  # e.g. the DM a tool is replying in
        await self.post(BotActions(MagicMock(), channel=current), None)
        current.send.assert_awaited_once()


class ToolBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_tool_posts_within_its_own_server(self) -> None:
        foreign = _channel(OTHER)
        current = _channel(HOME, "here")
        actions = BotActions(_bot(foreign), channel=current)
        ctx = SimpleNamespace(channel_id=777, cfg_guild=HOME, actions=actions)
        bridge = _ToolBridge(ctx, "ext", trusted=False)
        await bridge.send("555", "spam")
        foreign.send.assert_not_awaited()
        await bridge.send("777", "hi")  # its own channel's id means "here"
        current.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
