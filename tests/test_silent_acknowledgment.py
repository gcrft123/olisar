"""Ending a turn with a reaction instead of a message.

Run:  uv run python -m unittest tests.test_silent_acknowledgment -v

Every path through the reply pipeline used to end in text. Asked to DM someone, Olisar
sent the DM and then wrote "done"; if it wrote nothing, ``_force_final_answer`` made it,
and failing that the user got the blank fallback and a Report button. The ``acknowledge``
tool is the way out: react to the message, send nothing.

The whole risk of that feature is one failure — Olisar goes quiet where it owed an answer,
and from the channel that is indistinguishable from a bot that crashed. So most of what's
here is the refusals, not the happy path:

  * the reaction has to actually land before the turn can go silent;
  * there has to be a message to react to at all (``/ask`` has none);
  * a turn that looked something up owes what it found;
  * the server has to have the feature on, and the console's test chat never gets it.

The last group covers the transcript. A silent turn leaves no Discord message, so without
a marker row the next reply reads a conversation where someone asked for something and
Olisar said nothing whatsoever.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from olisar import pipeline
from olisar.context import CONTEXT_NOTE
from olisar.memory.writer import ACK_MARKER
from olisar.pipeline import _ALL_TOOL_KEYS, _CORE_TOOL_KEYS, _run_tool_loop, render_tools_note
from olisar.tools import (
    ACK_OK,
    DEFAULT_ACK_EMOJI,
    LOOKUP_TOOLS,
    SANDBOX_TOOL_NAMES,
    ToolContext,
    _acknowledge,
    ack_declarations,
    sandbox_tools,
    tools_with_extensions,
)


def _ctx(actions=None, tools_run=None) -> ToolContext:
    return ToolContext(
        session=None,
        cfg_guild=1,
        channel_id=2,
        user_id=3,
        display_name="rook",
        actions=actions,
        tools_run=list(tools_run or []),
    )


def _actions(result: str) -> MagicMock:
    actions = MagicMock()
    actions.acknowledge = AsyncMock(return_value=result)
    return actions


def _call(name: str, **args):
    call = MagicMock()
    call.name = name
    call.args = args
    return call


def _resp_with_calls(*calls):
    parts = []
    for c in calls:
        p = MagicMock()
        p.function_call = c
        p.text = None
        parts.append(p)
    resp = MagicMock()
    resp.candidates[0].content.parts = parts
    resp.text = None
    return resp


class TheReactionHasToLand(unittest.TestCase):
    """``ctx.silent`` is the switch that stops the reply being sent, so it may only be set
    on the far side of a reaction Discord accepted. Set optimistically, a missing
    permission turns a request into nothing at all, with no error anyone can see."""

    def test_a_reaction_that_landed_silences_the_turn(self):
        ctx = _ctx(_actions(f"{ACK_OK} 👍"))
        result = asyncio.run(_acknowledge("👍", ctx))
        self.assertEqual(ctx.silent, "👍")
        self.assertIn("finished", result)

    def test_a_reaction_that_failed_does_not(self):
        ctx = _ctx(_actions("couldn't react with 👍 — I'm not allowed to add reactions here"))
        result = asyncio.run(_acknowledge("👍", ctx))
        self.assertEqual(ctx.silent, "")
        self.assertIn("couldn't react", result)

    def test_the_model_is_told_why_so_it_can_reply_instead(self):
        """A bare refusal leaves the model holding a turn it thinks is over, and an
        unfinished turn comes out as the blank fallback."""
        ctx = _ctx(_actions("couldn't react with 🧨: Unknown Emoji"))
        self.assertTrue(asyncio.run(_acknowledge("🧨", ctx)).strip())

    def test_the_emoji_is_the_one_that_was_reacted_with(self):
        ctx = _ctx(_actions(f"{ACK_OK} 🔥"))
        asyncio.run(_acknowledge("🔥", ctx))
        self.assertEqual(ctx.silent, "🔥")

    def test_a_missing_emoji_falls_back_to_the_default(self):
        actions = _actions(f"{ACK_OK} {DEFAULT_ACK_EMOJI}")
        asyncio.run(_acknowledge("", _ctx(actions)))
        actions.acknowledge.assert_awaited_once_with(DEFAULT_ACK_EMOJI)

    def test_an_emoji_with_prose_around_it_is_trimmed(self):
        """Discord rejects '👍 done' as a reaction, and a rejected reaction is a turn that
        can't go silent — so the argument is narrowed to the emoji before it's used."""
        actions = _actions(f"{ACK_OK} 👍")
        asyncio.run(_acknowledge("👍 done", _ctx(actions)))
        actions.acknowledge.assert_awaited_once_with("👍")


class NothingToReactTo(unittest.TestCase):
    """``/ask`` hands the pipeline a ``BotActions``, which has no triggering message. A
    silent reply there would leave a deferred interaction on "thinking…" forever."""

    def test_refused_without_a_discord_surface(self):
        ctx = _ctx(actions=None)
        result = asyncio.run(_acknowledge("👍", ctx))
        self.assertEqual(ctx.silent, "")
        self.assertIn("react", result.lower())

    def test_bot_actions_refuses(self):
        from bot.actions import BotActions

        result = asyncio.run(BotActions(MagicMock()).acknowledge("👍"))
        self.assertFalse(result.startswith(ACK_OK))


class NotAfterALookup(unittest.TestCase):
    """Searching for something and then reacting is the answer going missing. It is also
    the shape of the confabulation failure the tool briefing already guards against: an
    instruction that can't be obeyed, obeyed anyway."""

    def test_every_lookup_tool_blocks_silence(self):
        for tool in sorted(LOOKUP_TOOLS):
            with self.subTest(tool=tool):
                ctx = _ctx(_actions(f"{ACK_OK} 👍"), tools_run=[tool])
                asyncio.run(_acknowledge("👍", ctx))
                self.assertEqual(ctx.silent, "")

    def test_the_refusal_names_what_was_looked_up(self):
        ctx = _ctx(_actions(f"{ACK_OK} 👍"), tools_run=["search_messages"])
        self.assertIn("search_messages", asyncio.run(_acknowledge("👍", ctx)))

    def test_an_action_tool_does_not_block_silence(self):
        ctx = _ctx(_actions(f"{ACK_OK} 👍"), tools_run=["send_dm", "remember"])
        asyncio.run(_acknowledge("👍", ctx))
        self.assertEqual(ctx.silent, "👍")

    def test_the_reaction_is_never_attempted_after_a_lookup(self):
        actions = _actions(f"{ACK_OK} 👍")
        asyncio.run(_acknowledge("👍", _ctx(actions, tools_run=["web_search"])))
        actions.acknowledge.assert_not_awaited()


class TheLoopStopsThere(unittest.TestCase):
    """``_run_tool_loop`` exists to keep pushing until there's text. Once a turn has gone
    silent that machinery is exactly wrong: it would manufacture the "done" the reaction
    was chosen instead of."""

    def _loop(self, *, silent: bool, text: str = ""):
        client = MagicMock()
        resp = _resp_with_calls(_call("acknowledge", emoji="👍"))
        resp.text = text
        client.generate_with_tools = AsyncMock(return_value=resp)
        client.generate = AsyncMock(side_effect=AssertionError("forced an answer anyway"))
        ctx = _ctx()

        async def fake_execute(name, args, c):
            if silent:
                c.silent = "👍"
            return "ok"

        forced = AsyncMock(side_effect=AssertionError("forced an answer anyway"))
        with patch("olisar.pipeline.get_gemini", return_value=client), patch(
            "olisar.pipeline.execute_tool", new=AsyncMock(side_effect=fake_execute)
        ), patch("olisar.pipeline._force_final_answer", new=forced), patch(
            "olisar.pipeline.MAX_TOOL_ITERS", 3
        ):
            return asyncio.run(_run_tool_loop([], "sys", None, ctx, blank_fallback="blank"))

    def test_the_loop_returns_empty_without_forcing_an_answer(self):
        self.assertEqual(self._loop(silent=True), "")

    def test_text_written_alongside_the_call_is_logged_not_sent(self):
        with self.assertLogs("olisar.pipeline", level="INFO") as logs:
            out = self._loop(silent=True, text="sent it, want me to do anything else?")
        self.assertEqual(out, "")
        self.assertTrue(
            any("anything else" in line for line in logs.output),
            "the reply that wasn't sent should still reach the operator's log",
        )


class TheReplyItProduces(unittest.TestCase):
    """A silent reply and a blank one both carry empty text and mean opposite things:
    one worked, the other failed and offers the user a Report button."""

    def _reply(self, silent: str, text: str = ""):
        async def fake_loop(*a, **k):
            k_ctx = a[3]
            k_ctx.silent = silent
            return text

        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        with patch.object(pipeline, "_run_tool_loop", new=fake_loop), patch.object(
            pipeline, "build_contents", new=AsyncMock(return_value=([], set()))
        ), patch.object(pipeline, "people_directory", new=AsyncMock(return_value="")), patch.object(
            pipeline, "recall", new=AsyncMock(return_value="")
        ), patch.object(
            pipeline, "gather_enabled", new=AsyncMock(return_value=pipeline.GatheredExtensions())
        ):
            return asyncio.run(
                pipeline.generate_reply(
                    session,
                    guild_id=1,
                    channel_id=2,
                    current_message_id=3,
                    bot_user_id=4,
                    user_id=5,
                    display_name="rook",
                    user_text="dm dtrain the schedule",
                    actions=None,
                )
            )

    def test_a_silent_turn_is_not_a_blank(self):
        reply = self._reply("👍")
        self.assertTrue(reply.silent)
        self.assertFalse(reply.blanked)
        self.assertEqual(reply.text, "")
        self.assertEqual(reply.emoji, "👍")

    def test_an_ordinary_failure_is_still_a_blank(self):
        """The loop reports "I never reached an answer" by handing back the very fallback
        string it was given, so the silent check must not have swallowed that case."""
        reply = self._reply("", text=pipeline.FALLBACK_EMPTY)
        self.assertFalse(reply.silent)
        self.assertTrue(reply.blanked)


class WhereItIsOffered(unittest.TestCase):
    def test_it_is_not_a_core_tool(self):
        """Core tools are declared to every server. This one is per-guild, so it has to
        arrive through the same seam the presence tools use."""
        core = {d.name for d in tools_with_extensions([])[0].function_declarations}
        self.assertNotIn("acknowledge", core)

    def test_it_joins_the_set_when_it_is_declared(self):
        names = {
            d.name
            for d in tools_with_extensions(ack_declarations())[0].function_declarations
        }
        self.assertIn("acknowledge", names)

    def test_never_in_the_console_test_chat(self):
        """There is no message to react to in the sandbox, so the tool reduces to a way of
        producing an empty test reply."""
        self.assertNotIn("acknowledge", SANDBOX_TOOL_NAMES)
        names = {d.name for d in sandbox_tools([])[0].function_declarations}
        self.assertNotIn("acknowledge", names)

    def test_the_briefing_describes_it_only_when_it_is_there(self):
        """Describing a tool the model hasn't got is what made the test chat invent the
        lookups it was told to perform — the same trap, one tool further on."""
        self.assertNotIn("acknowledge", render_tools_note(_CORE_TOOL_KEYS))
        self.assertIn("acknowledge", render_tools_note(_ALL_TOOL_KEYS))

    def test_the_shipped_note_is_the_core_one(self):
        self.assertNotIn("acknowledge", pipeline.TOOLS_NOTE)


class TheTurnStaysInTheTranscript(unittest.TestCase):
    """Nothing was sent, so nothing is stored — and a transcript where someone asked for
    something and Olisar said nothing at all reads as having been ignored. The next reply
    re-does the action or apologises for missing it."""

    def test_the_marker_is_explained_to_the_model(self):
        self.assertIn(ACK_MARKER.format(emoji="👍"), CONTEXT_NOTE)

    def test_the_marker_renders_as_olisars_own_turn(self):
        """A bot row with no ``author_name`` is Olisar's own — that is what tells a
        transcript "me" from the server's other bots (olisar/db/models.py, Message)."""
        from olisar.context import is_own_message
        from olisar.db.models import Message

        row = Message(
            author_is_bot=True, author_name="", content=ACK_MARKER.format(emoji="👍")
        )
        self.assertTrue(is_own_message(row))


if __name__ == "__main__":
    unittest.main()
