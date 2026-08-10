"""Coverage for what the reply pipeline does when it can't answer.

Run:  uv run python -m unittest tests.test_pipeline_failure_paths -v

Three behaviours from the DM incidents:
  * an exhausted quota must reach generate_reply as RateLimitExceeded, not be flattened
    into an empty string that reads as "the model had nothing to say"
  * lookup tools are capped per reply, so the model can't re-query its way through the
    whole iteration budget and never answer
  * function responses go back to the model under a role the API actually accepts
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import types

from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.pipeline import (
    LOOKUP_CALL_CAP,
    LOOKUP_TOOLS,
    _force_final_answer,
    _run_tool_loop,
)


def _call(name: str, **args):
    call = MagicMock()
    call.name = name
    call.args = args
    return call


def _resp_with_calls(*calls):
    """A model response carrying function calls and no text."""
    part_list = []
    for c in calls:
        p = MagicMock()
        p.function_call = c
        p.text = None
        part_list.append(p)
    resp = MagicMock()
    resp.candidates[0].content.parts = part_list
    resp.text = None
    return resp


def _resp_with_text(text: str):
    p = MagicMock()
    p.function_call = None
    p.text = text
    resp = MagicMock()
    resp.candidates[0].content.parts = [p]
    resp.text = text
    return resp


class RateLimitPropagationTests(unittest.TestCase):
    def test_rate_limit_while_barring_tools_propagates(self):
        client = MagicMock()
        client.generate_with_tools = AsyncMock(side_effect=RateLimitExceeded("gemini-flash", "daily"))
        client.generate = AsyncMock(side_effect=AssertionError("should not be reached"))
        with self.assertRaises(RateLimitExceeded):
            asyncio.run(_force_final_answer(client, [], "sys", None, []))

    def test_rate_limit_without_tools_propagates(self):
        """The second stage used to swallow it and return "" -> blank fallback."""
        client = MagicMock()
        client.generate_with_tools = AsyncMock(return_value=_resp_with_text(""))
        client.generate = AsyncMock(side_effect=RateLimitExceeded("gemini-flash", "daily"))
        with self.assertRaises(RateLimitExceeded):
            asyncio.run(_force_final_answer(client, [], "sys", None, []))

    def test_other_errors_still_degrade_to_empty(self):
        """Only rate limits get special treatment; anything else still falls back."""
        client = MagicMock()
        client.generate_with_tools = AsyncMock(side_effect=RuntimeError("boom"))
        client.generate = AsyncMock(side_effect=RuntimeError("boom"))
        self.assertEqual(asyncio.run(_force_final_answer(client, [], "sys", None, [])), "")


class LookupCapTests(unittest.TestCase):
    def _run_with_repeated_search(self, tool: str, rounds: int):
        """Model asks for `tool` every round; count how often it actually executes."""
        client = MagicMock()
        client.generate_with_tools = AsyncMock(
            return_value=_resp_with_calls(_call(tool, query="starlancer"))
        )
        client.generate = AsyncMock(return_value=MagicMock(text="final answer"))
        executed: list[str] = []

        async def fake_execute(name, args, ctx):
            executed.append(name)
            return f"- DM · someone · 2026-07-11 · {name} result"

        with patch("olisar.pipeline.get_gemini", return_value=client), patch(
            "olisar.pipeline.execute_tool", new=AsyncMock(side_effect=fake_execute)
        ), patch("olisar.pipeline.MAX_TOOL_ITERS", rounds):
            out = asyncio.run(_run_tool_loop([], "sys", None, MagicMock(), blank_fallback="blank"))
        return executed, out

    def test_lookup_tool_stops_executing_past_the_cap(self):
        executed, _ = self._run_with_repeated_search("search_messages", rounds=6)
        self.assertEqual(len(executed), LOOKUP_CALL_CAP)

    def test_action_tools_are_not_capped(self):
        executed, _ = self._run_with_repeated_search("react", rounds=6)
        self.assertEqual(len(executed), 6)

    def test_cap_covers_every_lookup_tool(self):
        for tool in sorted(LOOKUP_TOOLS):
            with self.subTest(tool=tool):
                executed, _ = self._run_with_repeated_search(tool, rounds=6)
                self.assertEqual(len(executed), LOOKUP_CALL_CAP)

    def test_under_the_cap_nothing_changes(self):
        executed, _ = self._run_with_repeated_search("search_messages", rounds=2)
        self.assertEqual(len(executed), 2)


class FunctionResponseRoleTests(unittest.TestCase):
    """The tool loop feeds each tool result back as its own turn. That turn used to carry
    role="tool", which the SDK never allowed (Content.role is documented as "either 'user'
    or 'model'") and which Gemini 2.x merely tolerated. Once the guild default
    `gemini-flash-latest` rolled onto a 3.x model, the stricter role check turned every
    tool-backed reply into a 400 -> `gemini generation failed` -> the blank fallback.
    """

    VALID_ROLES = {"user", "model"}

    def test_function_response_turn_uses_a_role_the_api_accepts(self):
        # The call that actually blanked: sc_ship_lookup(name='Starlancer TAC'). Built by
        # hand rather than via _call(), whose own first parameter is `name`.
        lookup = MagicMock()
        lookup.name = "sc_ship_lookup"
        lookup.args = {"name": "Starlancer TAC"}

        client = MagicMock()
        client.generate_with_tools = AsyncMock(
            side_effect=[
                _resp_with_calls(lookup),
                _resp_with_text("The Starlancer TAC is a MISC gunship."),
            ]
        )
        contents: list = []
        with patch("olisar.pipeline.get_gemini", return_value=client), patch(
            "olisar.pipeline.execute_tool",
            new=AsyncMock(return_value="**Starlancer TAC** — MISC role: Gunship"),
        ):
            out = asyncio.run(
                _run_tool_loop(contents, "sys", None, MagicMock(), blank_fallback="blank")
            )

        self.assertEqual(out, "The Starlancer TAC is a MISC gunship.")
        roles = [c.role for c in contents if isinstance(c, types.Content)]
        self.assertTrue(roles, "the tool loop never appended a function-response turn")
        for role in roles:
            with self.subTest(role=role):
                self.assertIn(role, self.VALID_ROLES)


if __name__ == "__main__":
    unittest.main()
