"""A tool call the model typed out instead of making never reaches the channel.

Run:  uv run python -m unittest tests.test_written_tool_calls -v

A bot answered a string of emoji with the message `react(emoji="🔥")`. The react tool never
ran; the model had written the call out as its reply, and the loop only looks for a
function call part, so it took the call for an answer and sent it. Replaying that
conversation reproduced it on the fallback models: Flash-Lite 3.1 wrote the same line, and
Flash 3 preview wrote `[reacted 🔥]`, the transcript's marker for a reaction, three times
out of four. Both reach the channel verbatim.

Such a reply is caught before it's sent. A typed-out reaction is carried out as the
reaction it asked for. Anything else is asked again, with a rule added to the model's
instructions rather than a turn added to the conversation: told in the conversation, the
model took it for the person it was talking to and apologised to them. If it still hasn't
answered in words by the end, the blank fallback goes out instead of the call.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from olisar.pipeline import (
    TYPED_CALL_RULE,
    _run_tool_loop,
    _settle_typed_calls,
    _split_typed_calls,
    _typed_call,
)
from olisar.tools import ACK_OK, TOOLS, ToolContext, ack_declarations, tools_with_extensions

NAMES = {"react", "acknowledge", "send_dm", "web_search", "catchup"}
WITH_ACKS = tools_with_extensions(ack_declarations())


def _actions() -> MagicMock:
    actions = MagicMock()
    actions.react = AsyncMock(side_effect=lambda e: f"reacted with {e}")
    actions.acknowledge = AsyncMock(side_effect=lambda e: f"{ACK_OK} {e}")
    return actions


def _ctx(actions=None, tools_run=None) -> ToolContext:
    return ToolContext(
        session=None,
        cfg_guild=0,
        channel_id=0,
        user_id=0,
        display_name="tester",
        actions=actions,
        tools_run=list(tools_run or []),
    )


def _resp_with_text(text: str):
    p = MagicMock()
    p.function_call = None
    p.text = text
    resp = MagicMock()
    resp.candidates[0].content.parts = [p]
    resp.text = text
    return resp


def _no_pin_gate():
    """The real execute_tool, minus the PIN gate's database lookup."""
    return patch("olisar.tools.toolpin.gate", new=AsyncMock(return_value=None))


def _settle(text: str, ctx: ToolContext, tools=WITH_ACKS):
    with _no_pin_gate():
        return asyncio.run(_settle_typed_calls(text, ctx, tools))


class WhatCountsAsATypedCall(unittest.TestCase):
    def test_the_line_that_was_sent(self):
        self.assertEqual(_typed_call('react(emoji="🔥")', NAMES), ("react", {"emoji": "🔥"}))

    def test_the_tool_code_wrapping(self):
        self.assertEqual(
            _typed_call('print(default_api.react(emoji="🔥"))', NAMES),
            ("react", {"emoji": "🔥"}),
        )

    def test_inside_backticks(self):
        self.assertEqual(_typed_call('`send_dm(message="hi")`', NAMES), ("send_dm", {"message": "hi"}))

    def test_spread_over_lines(self):
        self.assertEqual(_typed_call('react(\n    emoji="🔥",\n)', NAMES), ("react", {"emoji": "🔥"}))

    def test_code_that_is_not_one_of_this_replys_tools(self):
        for line in ('generate_image(prompt="a cat")', "print(x=1)", 'x.remember(fact="a")'):
            with self.subTest(line=line):
                self.assertIsNone(_typed_call(line, NAMES))

    def test_positional_or_computed_arguments(self):
        self.assertIsNone(_typed_call('react("🔥")', NAMES))
        self.assertIsNone(_typed_call("react(emoji=pick())", NAMES))
        self.assertIsNone(_typed_call("react(**{'emoji': '🔥'})", NAMES))

    def test_prose(self):
        for line in ("helikopter helikopter", "react with 🔥 if you agree", "lol (react)", ""):
            with self.subTest(line=line):
                self.assertIsNone(_typed_call(line, NAMES))

    def test_input_the_parser_chokes_on_is_prose(self):
        self.assertIsNone(_typed_call("-" * 200_000 + "1", NAMES))
        self.assertIsNone(_typed_call("react(emoji={[1]})", NAMES))


class SplittingAReply(unittest.TestCase):
    def test_a_reply_that_is_only_a_call(self):
        self.assertEqual(
            _split_typed_calls('react(emoji="🔥")', NAMES), ([("react", {"emoji": "🔥"})], "")
        )

    def test_the_marker(self):
        self.assertEqual(
            _split_typed_calls("[reacted 🔥]", NAMES), ([("acknowledge", {"emoji": "🔥"})], "")
        )

    def test_words_after_the_marker_stay(self):
        calls, rest = _split_typed_calls("[reacted 🔥] lmao", NAMES)
        self.assertEqual(calls, [("acknowledge", {"emoji": "🔥"})])
        self.assertEqual(rest, "lmao")

    def test_the_marker_without_its_brackets(self):
        """Flash-Lite 3.1 once replied with just this."""
        for text in ("reacted 🔥", "*reacted 🔥*", "Reacted 👍🏽"):
            with self.subTest(text=text):
                calls, rest = _split_typed_calls(text, NAMES)
                self.assertEqual((len(calls), rest), (1, ""))

    def test_a_sentence_that_starts_like_the_marker(self):
        for text in ("reacted to that lol", "reacted lol", "reacted 🔥 lol", "i reacted 🔥"):
            with self.subTest(text=text):
                self.assertEqual(_split_typed_calls(text, NAMES), ([], text))

    def test_a_tool_code_fence_goes_with_its_call(self):
        calls, rest = _split_typed_calls(
            'ok\n```tool_code\nprint(default_api.react(emoji="🔥"))\n```', NAMES
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(rest, "ok")

    def test_code_in_an_ordinary_fence_is_an_answer(self):
        """Someone asked how to call it. The code block is the answer."""
        for fence in ("```python", "```", "```c++"):
            with self.subTest(fence=fence):
                text = f'here\'s how:\n{fence}\nweb_search(query="x")\n```'
                self.assertEqual(_split_typed_calls(text, NAMES), ([], text))

    def test_nothing_left_but_a_break_marker_is_nothing_left(self):
        """split_messages("[[break]]") sends no message at all, so a leftover marker would
        have been a turn that ended with neither a reply nor a reaction."""
        for text in ('react(emoji="🔥")\n[[break]]', "[reacted 🔥]\n[[break]]\n"):
            with self.subTest(text=text):
                calls, rest = _split_typed_calls(text, NAMES)
                self.assertEqual(len(calls), 1)
                self.assertEqual(rest, "")

    def test_a_call_between_break_markers(self):
        """split_messages would have posted the call as a message of its own."""
        calls, rest = _split_typed_calls('react(emoji="🔥") [[break]] lol', NAMES)
        self.assertEqual(calls, [("react", {"emoji": "🔥"})])
        self.assertEqual(rest, "lol")

    def test_an_ordinary_reply_is_untouched(self):
        text = "ed please. linkin park is fine\n[[break]]\nbijelo dugme is actual soul"
        self.assertEqual(_split_typed_calls(text, NAMES), ([], text))


class Settling(unittest.TestCase):
    """What happens to a reply with a typed call in it, through the real execute_tool and
    acknowledge, so acknowledge's own refusals are the ones being exercised."""

    def test_a_typed_reaction_as_the_whole_reply_becomes_the_reaction(self):
        actions = _actions()
        ctx = _ctx(actions)
        self.assertEqual(_settle('react(emoji="🔥")', ctx), "")
        self.assertEqual(ctx.silent, "🔥")
        actions.acknowledge.assert_awaited_once_with("🔥")

    def test_not_where_the_server_has_it_off(self):
        actions = _actions()
        ctx = _ctx(actions)
        self.assertIsNone(_settle("[reacted 🔥]", ctx, tools=TOOLS))
        self.assertEqual(ctx.silent, "")
        actions.acknowledge.assert_not_awaited()

    def test_not_from_ask_which_has_no_message_to_react_to(self):
        from bot.actions import BotActions

        ctx = _ctx(BotActions(MagicMock()))
        self.assertIsNone(_settle('react(emoji="🔥")', ctx))
        self.assertEqual(ctx.silent, "")

    def test_not_after_another_tool_ran(self):
        """A reaction there could be standing in for a result or a failure. Asked again,
        the model can still call acknowledge itself and get its checks."""
        for ran in (["web_search"], ["send_dm"], ["uex_commodity_prices"]):
            with self.subTest(ran=ran):
                actions = _actions()
                ctx = _ctx(actions, tools_run=ran)
                self.assertIsNone(_settle("[reacted 👍]", ctx))
                actions.acknowledge.assert_not_awaited()

    def test_but_after_a_real_react_call_it_is_the_model_saying_that_was_all(self):
        """Counting react as "another tool" asked again after every real reaction, and a
        model that kept typing the marker spent the whole budget on the blank fallback."""
        actions = _actions()
        ctx = _ctx(actions, tools_run=["react"])
        self.assertEqual(_settle("[reacted 👍]", ctx), "")
        self.assertEqual(ctx.silent, "👍")

    def test_a_reaction_next_to_words_is_made_and_the_words_sent(self):
        actions = _actions()
        ctx = _ctx(actions)
        self.assertEqual(_settle('lmao ok\nreact(emoji="🔥")', ctx), "lmao ok")
        actions.react.assert_awaited_once_with("🔥")
        self.assertEqual(ctx.silent, "")

    def test_words_next_to_a_call_that_never_ran_are_not_sent(self):
        """'Done, I DMed them!' would confirm a DM nobody sent."""
        ctx = _ctx(_actions())
        self.assertIsNone(_settle('send_dm(user_id="123", message="hey")\nDone, I DMed them!', ctx))

    def test_a_typed_dm_is_never_confirmed_with_a_reaction(self):
        actions = _actions()
        ctx = _ctx(actions)
        self.assertIsNone(_settle('send_dm(message="see you at 9")\n[reacted 👍]', ctx))
        actions.acknowledge.assert_not_awaited()

    def test_a_call_that_already_ran_is_the_model_describing_it(self):
        """Asking again here would invite a second DM."""
        ctx = _ctx(_actions(), tools_run=["send_dm"])
        self.assertEqual(_settle('send_dm(message="hey")\nsent it', ctx), "sent it")
        self.assertEqual(_settle('send_dm(message="hey")', ctx), "")

    def test_an_ordinary_reply(self):
        ctx = _ctx(_actions())
        self.assertEqual(_settle("helikopter helikopter", ctx), "helikopter helikopter")


class TheLoop(unittest.TestCase):
    def _loop(self, replies, *, tools=WITH_ACKS, actions=None, forced="", contents=None):
        client = MagicMock()
        client.generate_with_tools = AsyncMock(side_effect=[_resp_with_text(r) for r in replies])
        contents = contents if contents is not None else []
        ctx = _ctx(actions if actions is not None else _actions())
        with patch("olisar.pipeline.get_gemini", return_value=client), _no_pin_gate(), patch(
            "olisar.pipeline._force_final_answer", new=AsyncMock(return_value=forced)
        ), patch("olisar.pipeline.MAX_TOOL_ITERS", len(replies)):
            out = asyncio.run(
                _run_tool_loop(contents, "sys", None, ctx, blank_fallback="blank", tools=tools)
            )
        return out, ctx, client

    def test_the_incident(self):
        out, ctx, _ = self._loop(['react(emoji="🔥")'])
        self.assertEqual((out, ctx.silent), ("", "🔥"))

    def test_asked_again_through_its_instructions_not_the_conversation(self):
        """In the conversation, the correction read as the person complaining, and the
        model apologised to them for a glitch they never saw."""
        contents = [MagicMock(role="user")]
        out, _, client = self._loop(
            ['react(emoji="🔥")', "lmao what is this"], tools=TOOLS, contents=contents
        )
        self.assertEqual(out, "lmao what is this")
        self.assertEqual(len(contents), 1)
        first, second = client.generate_with_tools.call_args_list
        self.assertNotIn(TYPED_CALL_RULE, first.kwargs["system_instruction"])
        self.assertIn(TYPED_CALL_RULE, second.kwargs["system_instruction"])

    def test_the_rule_is_added_once(self):
        _, _, client = self._loop(['web_search(query="x")'] * 3, tools=TOOLS)
        last = client.generate_with_tools.call_args_list[-1]
        self.assertEqual(last.kwargs["system_instruction"].count(TYPED_CALL_RULE), 1)

    def test_a_call_typed_every_time_ends_in_the_fallback(self):
        out, _, _ = self._loop(
            ['web_search(query="bijelo dugme")'] * 2,
            tools=TOOLS,
            forced='web_search(query="bijelo dugme")',
        )
        self.assertEqual(out, "blank")

    def test_a_forced_answer_that_is_a_reaction_becomes_the_reaction(self):
        out, ctx, _ = self._loop([""], forced="[reacted 🔥]")
        self.assertEqual((out, ctx.silent), ("", "🔥"))

    def test_a_reaction_and_a_break_marker(self):
        out, ctx, _ = self._loop(['react(emoji="🔥")\n[[break]]'])
        self.assertEqual((out, ctx.silent), ("", "🔥"))

    def test_a_call_in_a_continuation_is_not_sent(self):
        with patch(
            "olisar.pipeline._complete_truncated",
            new=AsyncMock(return_value='it was cut off\nreact(emoji="🔥")'),
        ):
            out, _, _ = self._loop(["it was cut off"])
        self.assertEqual(out, "it was cut off")

    def test_an_ordinary_reply_goes_out_as_written(self):
        actions = _actions()
        out, ctx, _ = self._loop(["helikopter helikopter"], actions=actions)
        self.assertEqual(out, "helikopter helikopter")
        self.assertEqual(ctx.tools_run, [])


if __name__ == "__main__":
    unittest.main()
