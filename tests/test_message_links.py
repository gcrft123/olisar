"""Citing past messages: who gets shown a message, and which links survive into a reply.

Run:  uv run python -m unittest tests.test_message_links -v

Olisar pastes a message's jump-link when an answer rests on it. That's only safe if
two things hold, and these tests pin both:

  * search and recall only show the model messages the asker can open. The search index
    holds every channel Olisar can read, which is wider than what most members can, so an
    unfiltered hit from a staff channel told a member what was said there and by whom
    (arena scenario rt-index-crosschannel), and would now hand them a link to it too
  * a link in a reply is one the model was given. Copying a 19-digit id can slip a digit,
    and a link to the wrong message is worse than none
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from google.genai import types

from bot.actions import _DELETED_CHANNELS, BotActions
from olisar.memory.retriever import recall
from olisar.memory.search import _Cand, _only_readable
from olisar.message_links import channel_filter, link_ids, message_link, strip_unoffered_links
from olisar.pipeline import _without_invented_links, render_tools_note
from olisar.tools import SANDBOX_TOOL_NAMES

GUILD = 100
HERE = 200
OPEN = 300
HIDDEN = 400

GIVEN = message_link(GUILD, OPEN, 1111111111111111111)
# The same link with one digit changed — what a slipped copy looks like.
SLIPPED = message_link(GUILD, OPEN, 1111111111111111112)


def _run(coro):
    return asyncio.run(coro)


class StripUnofferedLinks(unittest.TestCase):
    offered = link_ids(GIVEN)

    def test_given_link_is_kept_verbatim(self):
        reply = f"rook posted it last week {GIVEN}"
        self.assertEqual(strip_unoffered_links(reply, self.offered), (reply, []))

    def test_a_slipped_digit_is_removed(self):
        text, removed = strip_unoffered_links(f"rook posted it {SLIPPED} a while back", self.offered)
        self.assertEqual(text, "rook posted it a while back")
        self.assertEqual(removed, [SLIPPED])

    def test_separator_goes_with_the_link(self):
        text, _ = strip_unoffered_links(f"it's here: {SLIPPED}", self.offered)
        self.assertEqual(text, "it's here")

    def test_bracketed_forms_leave_nothing_behind(self):
        for reply in (f"posted in general (<{SLIPPED}>).", f"posted in general ({SLIPPED})."):
            with self.subTest(reply=reply):
                text, removed = strip_unoffered_links(reply, self.offered)
                self.assertEqual(removed, [SLIPPED])
                self.assertEqual(text, "posted in general.")
        text, _ = strip_unoffered_links(f"see <{SLIPPED}> for it", self.offered)
        self.assertEqual(text, "see for it")

    def test_masked_link_keeps_its_label(self):
        text, _ = strip_unoffered_links(f"rook [posted it here]({SLIPPED})", self.offered)
        self.assertEqual(text, "rook posted it here")

    def test_other_discord_hostnames_count_as_given(self):
        ptb = GIVEN.replace("https://discord.com", "https://ptb.discord.com")
        self.assertEqual(strip_unoffered_links(ptb, self.offered), (ptb, []))

    def test_channel_links_and_other_urls_are_left_alone(self):
        reply = "it's in https://discord.com/channels/100/300 and on https://example.org/guide"
        self.assertEqual(strip_unoffered_links(reply, set()), (reply, []))


class WithoutInventedLinks(unittest.TestCase):
    """What counts as given: the system prompt, the chat, and every tool result."""

    def test_links_from_each_source_survive_and_nothing_else(self):
        from_recall = message_link(GUILD, OPEN, 1)
        from_chat = message_link(GUILD, OPEN, 2)
        from_tool = message_link(GUILD, OPEN, 3)
        contents = [
            types.Content(role="user", parts=[types.Part(text=f"mika: saw this? {from_chat}")]),
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name="search_messages", response={"result": f"- #general · {from_tool}"}
                    )
                ],
            ),
        ]
        invented = message_link(GUILD, OPEN, 4)
        reply = f"a {from_recall} b {from_chat} c {from_tool} d {invented}"
        out = _without_invented_links(reply, f"older: {from_recall}", contents)
        self.assertEqual(out, f"a {from_recall} b {from_chat} c {from_tool} d")


class ChannelFilterFailsClosed(unittest.TestCase):
    def test_no_discord_connection_means_only_this_channel(self):
        readable = channel_filter(None, guild_id=GUILD, requester_id=1, here=HERE)
        self.assertEqual(_run(readable({HERE, OPEN, HIDDEN})), {HERE})

    def test_a_failed_check_means_only_this_channel(self):
        actions = MagicMock()
        actions.readable_channels = AsyncMock(side_effect=RuntimeError("gateway gone"))
        readable = channel_filter(actions, guild_id=GUILD, requester_id=1, here=HERE)
        self.assertEqual(_run(readable({HERE, OPEN})), {HERE})

    def test_asks_discord_about_everything_but_this_channel(self):
        actions = MagicMock()
        actions.readable_channels = AsyncMock(return_value={OPEN})
        readable = channel_filter(actions, guild_id=GUILD, requester_id=7, here=HERE)
        self.assertEqual(_run(readable({HERE, OPEN, HIDDEN})), {HERE, OPEN})
        actions.readable_channels.assert_awaited_once_with(GUILD, {OPEN, HIDDEN}, requester_id=7)


def _cand(channel_id: int, *, is_dm: bool = False) -> _Cand:
    return _Cand(
        channel_id=channel_id, channel_name="", author_name="rook", content="the guide",
        created_at=datetime.now(timezone.utc), message_id=channel_id * 10, is_dm=is_dm,
    )


class SearchHidesUnreadableChannels(unittest.TestCase):
    def test_hidden_channel_hits_are_dropped_and_dm_hits_kept(self):
        async def readable(ids):
            return ids & {OPEN}

        cands = [_cand(HIDDEN), _cand(OPEN), _cand(HIDDEN), _cand(999, is_dm=True)]
        kept = _run(_only_readable(cands, readable, "guide"))
        self.assertEqual([c.channel_id for c in kept], [OPEN, 999])

    def test_none_is_no_filter(self):
        cands = [_cand(HIDDEN), _cand(OPEN)]
        self.assertEqual(_run(_only_readable(cands, None, "guide")), cands)


def _msg(pk: int, *, guild_id: int, channel_id: int, content: str):
    return SimpleNamespace(
        id=pk, guild_id=guild_id, channel_id=channel_id, message_id=pk * 1000,
        content=content, author_is_bot=False, author_id=5, author_name="rook",
    )


class RecallScope(unittest.TestCase):
    """Older messages: this channel, or a channel of this server the asker can open."""

    def _recall(self, rows, *, channel_id=HERE):
        async def knn(_session, table, _qvec, k=5):
            return [(r.id, 0.1) for r in rows] if table == "message_embedding" else []

        async def readable(ids):
            return ids & {OPEN}

        session = MagicMock()
        session.scalar = AsyncMock(return_value=None)
        session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
        with (
            patch("olisar.memory.retriever.glossary_block", AsyncMock(return_value="")),
            patch("olisar.memory.retriever.channel_context_blocks", AsyncMock(return_value=[])),
            patch("olisar.memory.retriever.embed_query", AsyncMock(return_value=[0.1])),
            patch("olisar.memory.retriever.knn", knn),
            patch("olisar.memory.retriever.name_map", AsyncMock(return_value={})),
            patch("olisar.memory.retriever.kb_block_from_qvec", AsyncMock(return_value="")),
        ):
            return _run(recall(
                session, cfg_guild=GUILD, user_id=5, query_text="guide", recent_ids=set(),
                channel_id=channel_id, readable=readable, k_msgs=10,
            ))

    def test_only_messages_the_asker_can_open_come_back_with_links(self):
        rows = [
            _msg(1, guild_id=GUILD, channel_id=HERE, content="here-msg"),
            _msg(2, guild_id=GUILD, channel_id=OPEN, content="open-msg"),
            _msg(3, guild_id=GUILD, channel_id=HIDDEN, content="hidden-msg"),
            _msg(4, guild_id=999, channel_id=OPEN, content="other-server-msg"),
            _msg(5, guild_id=0, channel_id=555, content="someone-elses-dm"),
        ]
        block = self._recall(rows)
        self.assertIn(f"here-msg · {message_link(GUILD, HERE, 1000)}", block)
        self.assertIn(f"open-msg · {message_link(GUILD, OPEN, 2000)}", block)
        for gone in ("hidden-msg", "other-server-msg", "someone-elses-dm"):
            self.assertNotIn(gone, block)

    def test_own_dm_is_recalled_without_a_link(self):
        rows = [_msg(1, guild_id=0, channel_id=555, content="my-dm")]
        block = self._recall(rows, channel_id=555)
        self.assertIn("rook: my-dm\n", block + "\n")
        self.assertNotIn("discord.com", block)


class _Perms(SimpleNamespace):
    view_channel = True
    read_message_history = True
    manage_threads = False


def _channel(perms: _Perms, *, private_thread: bool = False):
    if private_thread:
        ch = MagicMock(spec=discord.Thread)
        ch.is_private.return_value = True
    else:
        ch = MagicMock()
    ch.permissions_for.return_value = perms
    return ch


class ReadableChannels(unittest.TestCase):
    """The Discord side of the filter."""

    def _actions(self, guild):
        bot = MagicMock()
        bot.get_guild.return_value = guild
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        return BotActions(bot)

    def _guild(self, channels: dict, member):
        guild = MagicMock()
        guild.id = GUILD
        guild.get_member.return_value = member
        guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "no"))
        guild.get_channel_or_thread.side_effect = channels.get
        return guild

    def test_view_and_history_are_both_required(self):
        channels = {
            1: _channel(_Perms()),
            2: _channel(_Perms(view_channel=False)),
            3: _channel(_Perms(read_message_history=False)),
        }
        actions = self._actions(self._guild(channels, member=MagicMock(id=7)))
        self.assertEqual(_run(actions.readable_channels(GUILD, {1, 2, 3}, requester_id=7)), {1})

    def test_non_member_is_checked_as_everyone(self):
        channels = {1: _channel(_Perms())}
        guild = self._guild(channels, member=None)
        _run(self._actions(guild).readable_channels(GUILD, {1}, requester_id=7))
        channels[1].permissions_for.assert_called_once_with(guild.default_role)

    def test_private_thread_needs_membership(self):
        thread = _channel(_Perms(), private_thread=True)
        thread.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "no"))
        actions = self._actions(self._guild({1: thread}, member=MagicMock(id=7)))
        self.assertEqual(_run(actions.readable_channels(GUILD, {1}, requester_id=7)), set())
        thread.fetch_member = AsyncMock(return_value=MagicMock())
        self.assertEqual(_run(actions.readable_channels(GUILD, {1}, requester_id=7)), {1})

    def test_deleted_channel_is_hidden_and_not_fetched_twice(self):
        _DELETED_CHANNELS.discard(42)
        actions = self._actions(self._guild({}, member=MagicMock(id=7)))
        for _ in range(2):
            self.assertEqual(_run(actions.readable_channels(GUILD, {42}, requester_id=7)), set())
        actions.bot.fetch_channel.assert_awaited_once_with(42)


class Briefing(unittest.TestCase):
    def test_citing_messages_is_only_briefed_where_search_exists(self):
        self.assertIn("jump-link", render_tools_note())
        sandbox = render_tools_note(set(SANDBOX_TOOL_NAMES))
        self.assertNotIn("jump-link", sandbox)
        self.assertIn("Only cite or link a source when you used web_search", sandbox)


if __name__ == "__main__":
    unittest.main()
