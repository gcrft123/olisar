"""Regression coverage for DM recall scope and the synthesis-failure fallback.

Run:  uv run python -m unittest tests.test_dm_recall_scope -v

Both paths below combined to leak one member's private DMs to another user: message
search could be widened to the whole guild-0 DM bucket for a server admin, and when
synthesis failed the pipeline pasted raw tool results — including that search block —
straight into the reply. These tests pin the fixed behaviour:

  * a DM recalls only its own channel, for admins exactly as for anyone else
  * a DM with no resolvable home guild scopes to nothing, never to the DM bucket
  * `search_messages` offers no parameter that widens past one DM channel
  * the last-resort fallback never surfaces gathered tool output
"""

from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from olisar.memory.search import _scope_sql, search_messages
from olisar.pipeline import _fallback_when_synthesis_fails
from olisar.tools import ToolContext, execute_tool

HOME_GUILD = 111
MY_DM_CHANNEL = 222


def _dm_ctx(*, is_admin: bool, cfg_guild: int = HOME_GUILD, is_dm: bool = True) -> ToolContext:
    actions = MagicMock()
    actions.is_admin = AsyncMock(return_value=is_admin)
    return ToolContext(
        session=MagicMock(),
        cfg_guild=cfg_guild,
        channel_id=MY_DM_CHANNEL,
        user_id=999,
        display_name="someone",
        is_dm=is_dm,
        actions=actions,
    )


def _search_kwargs_for(ctx: ToolContext) -> dict:
    """Run the search_messages tool against `ctx`, returning the kwargs it passed down."""
    with patch("olisar.tools.search_messages", new=AsyncMock(return_value="")) as spy:
        asyncio.run(execute_tool("search_messages", {"query": "starlancer"}, ctx))
    spy.assert_awaited_once()
    return spy.await_args.kwargs


class DmRecallScopeTests(unittest.TestCase):
    def test_admin_in_dm_gets_no_wider_scope_than_anyone_else(self):
        """The leak: an admin DMing the bot could recall across every member's DMs."""
        admin = _search_kwargs_for(_dm_ctx(is_admin=True))
        member = _search_kwargs_for(_dm_ctx(is_admin=False))
        self.assertEqual(admin, member)
        self.assertEqual(admin["dm_channel_id"], MY_DM_CHANNEL)
        self.assertEqual(admin["guild_id"], HOME_GUILD)
        # Nothing may reintroduce a second bucket through the call site.
        self.assertNotIn("extra_guild_ids", admin)

    def test_channel_conversation_gets_no_dm_access(self):
        kwargs = _search_kwargs_for(_dm_ctx(is_admin=True, is_dm=False))
        self.assertIsNone(kwargs["dm_channel_id"])

    def test_search_messages_cannot_be_widened_past_one_dm_channel(self):
        """No caller — present or future — can ask for the whole DM bucket."""
        params = inspect.signature(search_messages).parameters
        self.assertNotIn("extra_guild_ids", params)
        self.assertIn("dm_channel_id", params)

    def test_unresolvable_home_guild_scopes_to_nothing_not_everything(self):
        """guild 0 *is* the DM bucket, so it must never be used as a server scope."""
        frag, params = _scope_sql([], [MY_DM_CHANNEL])
        self.assertNotIn("guild_id IN ()", frag)  # would be a SQL syntax error
        self.assertTrue(frag.startswith("(0 OR "), frag)
        self.assertEqual(list(params.values()), [MY_DM_CHANNEL])

    def test_normal_scope_still_matches_guild_and_dm_channel(self):
        frag, params = _scope_sql([HOME_GUILD], [MY_DM_CHANNEL])
        self.assertIn("guild_id IN (:sg0)", frag)
        self.assertIn("channel_id IN (:sc0)", frag)
        self.assertEqual(params, {"sg0": HOME_GUILD, "sc0": MY_DM_CHANNEL})


class SynthesisFallbackTests(unittest.TestCase):
    def test_gathered_tool_output_is_never_shown_to_the_user(self):
        """The amplifier: raw tool results used to be pasted into the reply verbatim."""
        blank = "My mind went blank — try me again?"
        gathered = [
            'Message search results (skim these and answer the question):\n'
            '- DM · SomeoneElse · 2026-07-11 19:51 UTC · "a private message"',
            "Web search is temporarily unavailable (rate limited) — answer from what you know.",
        ]
        out = _fallback_when_synthesis_fails(blank, gathered)
        self.assertEqual(out, blank)
        for leaked in ("SomeoneElse", "a private message", "answer from what you know"):
            self.assertNotIn(leaked, out)

    def test_blank_fallback_returned_with_nothing_gathered(self):
        blank = "My mind went blank — try me again?"
        self.assertEqual(_fallback_when_synthesis_fails(blank, []), blank)


if __name__ == "__main__":
    unittest.main()
