"""The delivery marker must not escape into an extension's hands.

Reported from a live server: a welcome message arrived with "[[break]]" printed in it.
The chain is entirely inside the host, and any extension holding both permissions would
hit it — welcome.js is only the first that does.

  welcome.js asks for a greeting in the server's voice   host.generate
  the persona prompt teaches the model to mark beats     SPLIT_MARKER in DEFAULT_TONE_NOTES
  the model obliges                                      "welcome![[break]]glad you're here"
  the extension gets a plain string and forwards it      host.discord.send
  the event send path chunks for length, never splits    bot/cogs/sdk_events.py

bot/replies.py turns the marker into consecutive messages, which is what it is for. Nothing
on the extension path does, and an extension cannot be expected to know the marker exists.

Folding it into a newline stopped the leak, but the greeting then arrived as one message with
a line break in it where a reply would have sent two. So an extension posting generated text
as chat asks host.generate to ``split`` it and hands the array to host.discord.send, which
posts each piece as its own message (tests/test_generate_channel_context.py runs welcome.js
through it).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from olisar.persona import DEFAULT_TONE_NOTES, MAX_MESSAGE_PIECES, SPLIT_MARKER, strip_breaks
from olisar.sandbox import capabilities
from olisar.sandbox.capabilities import Invocation, dispatch


class TheMarkerIsTaught(unittest.TestCase):
    def test_the_persona_prompt_asks_for_it(self):
        """Why generated text contains it at all — this is the source of the leak."""
        self.assertIn(SPLIT_MARKER, DEFAULT_TONE_NOTES)


class FoldingAtTheBoundary(unittest.TestCase):
    def test_the_reported_shape(self):
        raw = f"welcome in, @rook!{SPLIT_MARKER}have a look at the rules when you get a sec"
        folded = strip_breaks(raw)
        self.assertNotIn(SPLIT_MARKER, folded)
        self.assertEqual(folded, "welcome in, @rook!\nhave a look at the rules when you get a sec")

    def test_several_markers(self):
        self.assertNotIn(SPLIT_MARKER, strip_breaks(f"a{SPLIT_MARKER}b{SPLIT_MARKER}c"))

    def test_text_without_one_is_untouched(self):
        plain = "welcome in, glad to have you"
        self.assertEqual(strip_breaks(plain), plain)

    def test_generate_folds_before_returning(self):
        """The fix, asserted at the line that ships it rather than on strip_breaks alone."""
        import inspect

        from olisar.sandbox.capabilities import _generate

        source = inspect.getsource(_generate)
        self.assertIn("strip_breaks", source)
        self.assertNotIn(
            'return (result.text or "").strip()',
            source,
            "host.generate returned raw model text again — the marker will leak",
        )


class _Gemini:
    def __init__(self, text: str) -> None:
        self.text = text

    async def generate(self, **_):
        return SimpleNamespace(text=self.text)


class SplitOnRequest(unittest.IsolatedAsyncioTestCase):
    async def _generate(self, reply: str, **opts) -> Any:
        inv = Invocation(ext_key="t", permissions={"model.generate"}, guild_id=1)
        with patch("olisar.gemini.client.get_gemini", return_value=_Gemini(reply)):
            return await dispatch(inv, "generate", "run", [{"task": "say hi", **opts}])

    async def test_split_returns_the_messages_a_reply_would_be(self):
        self.assertEqual(
            await self._generate(f"welcome in!{SPLIT_MARKER}glad you're here", split=True),
            ["welcome in!", "glad you're here"],
        )

    async def test_one_beat_is_one_message(self):
        self.assertEqual(await self._generate("welcome in", split=True), ["welcome in"])

    async def test_without_split_it_is_still_one_string(self):
        """An embed body or a button label can't be several messages."""
        self.assertEqual(
            await self._generate(f"welcome in!{SPLIT_MARKER}glad you're here"),
            "welcome in!\nglad you're here",
        )

    async def test_split_isnt_the_folded_string_split_again(self):
        """Four beats folded read as a four-line block, which split_messages keeps whole."""
        reply = SPLIT_MARKER.join("abcd")
        self.assertEqual(await self._generate(reply, split=True), ["a", "b", "c\nd"])


class _Bridge:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send(self, channel_id: str, payload: Any) -> None:
        self.sent.append((channel_id, payload))


class ArraySends(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        capabilities._send_history.clear()
        self.bridge = _Bridge()
        self.inv = Invocation(
            ext_key="t", permissions={"discord.send"}, guild_id=1, discord=self.bridge,
        )

    async def _send(self, payload: Any) -> Any:
        return await dispatch(self.inv, "discord", "send", ["100", payload])

    async def test_the_bridge_gets_the_messages(self):
        await self._send(["welcome in!", "glad you're here"])
        self.assertEqual(self.bridge.sent, [("100", ["welcome in!", "glad you're here"])])

    async def test_past_the_cap_the_rest_join_the_last(self):
        await self._send(list("abcde"))
        (_, pieces), = self.bridge.sent
        self.assertEqual(len(pieces), MAX_MESSAGE_PIECES)
        self.assertEqual(pieces, ["a", "b", "c\nd\ne"])

    async def test_blank_messages_are_dropped(self):
        await self._send(["  ", "hi", ""])
        self.assertEqual(self.bridge.sent, [("100", ["hi"])])

    async def test_an_array_of_nothing_posts_nothing_and_costs_nothing(self):
        self.assertEqual(await self._send(["", " "]), "nothing to post")
        self.assertEqual(self.bridge.sent, [])
        self.assertEqual(capabilities._send_history, {})

    async def test_only_strings(self):
        with self.assertRaises(ValueError):
            await self._send(["hi", {"content": "there"}])
        self.assertEqual(capabilities._send_history, {})

    async def test_an_array_counts_once_toward_the_limit(self):
        for _ in range(capabilities._SEND_MAX_PER_WINDOW):
            await self._send(["a", "b", "c"])
        self.assertEqual(len(self.bridge.sent), capabilities._SEND_MAX_PER_WINDOW)
        self.assertIn("rate-limited", await self._send(["a"]))


class ToolBridgeArrays(unittest.IsolatedAsyncioTestCase):
    def _bridge(self, *results: str):
        from olisar.sandbox.runner import _ToolBridge

        post = AsyncMock(side_effect=list(results))
        ctx = SimpleNamespace(channel_id=5, cfg_guild=1, actions=SimpleNamespace(post_components=post))
        return _ToolBridge(ctx, "t", trusted=True), post

    async def test_each_message_is_its_own_post(self):
        bridge, post = self._bridge("Posted in #a.", "Posted in #a.")
        self.assertEqual(await bridge.send("100", ["hi", "there"]), "Posted in #a.")
        self.assertEqual([c.kwargs["content"] for c in post.await_args_list], ["hi", "there"])

    async def test_a_failed_post_stops_the_rest(self):
        bridge, post = self._bridge("I don't have permission to post in #a.")
        self.assertIn("permission", await bridge.send("100", ["hi", "there"]))
        self.assertEqual(post.await_count, 1)


if __name__ == "__main__":
    unittest.main()
