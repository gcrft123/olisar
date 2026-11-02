"""host.discord.send from an event hook: what reaches Discord, and what the log says.

The log line is how an operator tells "Welcome posted" from "Welcome ran and sent nothing",
so it has to count what was actually sent rather than that send() was called.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.cogs import sdk_events

CHANNEL = 100


class EventBridgeSendTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(
            id=1, get_channel=lambda cid: self.channel if cid == CHANNEL else None,
        )
        self.bridge = sdk_events._EventBridge(guild, "welcome")
        for p in (
            patch.object(sdk_events, "_pace", AsyncMock()),
            patch.object(sdk_events, "BREAK_PAUSE", (0, 0)),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def _send(self, payload) -> list[str]:
        with self.assertLogs("olisar.cogs.sdk_events", "INFO") as logs:
            await self.bridge.send(str(CHANNEL), payload)
        return logs.output

    async def test_an_embed_with_blank_text_still_goes_out(self) -> None:
        """chunk_text turns blank content into no chunks, which used to skip the embed too."""
        logs = await self._send({"content": "   ", "embed": {"title": "Rules"}})
        self.channel.send.assert_awaited_once()
        sent = self.channel.send.await_args.kwargs
        self.assertNotIn("content", sent)
        self.assertEqual(sent["embed"].title, "Rules")
        self.assertTrue(any("posted 1 message(s)" in line for line in logs), logs)

    async def test_each_piece_of_a_list_counts(self) -> None:
        logs = await self._send(["hey", "welcome in"])
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertTrue(any("posted 2 message(s)" in line for line in logs), logs)

    async def test_an_empty_payload_isnt_logged_as_posted(self) -> None:
        for payload in ([], {}, ["", ""]):
            with self.subTest(payload=payload):
                logs = await self._send(payload)
                self.channel.send.assert_not_awaited()
                self.assertTrue(any("had nothing to post" in line for line in logs), logs)
                self.assertFalse(any("posted" in line for line in logs), logs)


if __name__ == "__main__":
    unittest.main()
