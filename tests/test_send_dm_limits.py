"""Asking Olisar to DM someone stays within the server, and within reason.

Run:  uv run python -m unittest tests.test_send_dm_limits -v

send_dm took any user id the bot could resolve and any text, as often as the model called
it. Anyone sharing a server with the bot could have it DM "your account is flagged, verify
at <link>" to members of every server it was in, from a trusted bot account, and one
message could fan out to 25 people in a single reply.

Now the recipient has to be a member of the server the conversation is in (the home server
in a DM), and so does the person asking. One reply sends at most five DMs, and a member can
have Olisar message at most twenty other people for them in a day. A DM to the person
asking is only held to the per-reply cap.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.actions import BotActions
from olisar import budgets, pipeline
from olisar.tools import DM_OK, DMS_PER_REPLY, ToolContext, execute_tool

HOME = 1001
CHANNEL = 3003
DM_CHANNEL = 4004
ASKER = 5005
MEMBER = 6006
STRANGER = 7007


def _not_found(*_a, **_k):
    raise discord.NotFound(NS(status=404, reason="nf"), "unknown member")


def _bot(members: set[int]):
    """A bot in HOME, whose members are ``members``, and able to DM anyone."""
    users: dict[int, NS] = {}

    def user(uid):
        return users.setdefault(uid, NS(id=uid, display_name=f"user{uid}", send=AsyncMock()))

    home = NS(
        id=HOME,
        get_member=lambda uid: NS(id=uid) if uid in members else None,
        fetch_member=AsyncMock(side_effect=_not_found),
        default_role=NS(id=HOME),
    )
    return NS(get_guild=lambda gid: home if gid == HOME else None, guilds=[home],
              get_user=user, fetch_user=AsyncMock(side_effect=user)), user


def _session():
    session = MagicMock()
    session.in_transaction.return_value = False
    return session


class _Case(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._dms = patch.object(budgets, "_dms", {})
        self._dms.start()

    def tearDown(self):
        self._dms.stop()

    def ctx(self, members: set[int], *, is_dm: bool = False) -> tuple[ToolContext, object]:
        bot, user = _bot(members)
        ctx = ToolContext(
            session=_session(), cfg_guild=HOME, channel_id=DM_CHANNEL if is_dm else CHANNEL,
            user_id=ASKER, display_name="asker", is_dm=is_dm, actions=BotActions(bot),
        )
        return ctx, user

    async def dm(self, ctx: ToolContext, target: int | None, text: str = "hi") -> str:
        args = {"message": text} if target is None else {"user_id": str(target), "message": text}
        return await execute_tool("send_dm", args, ctx)


class Recipients(_Case):
    async def test_someone_outside_the_server_isnt_messaged(self):
        ctx, user = self.ctx({ASKER, MEMBER})
        out = await self.dm(ctx, STRANGER, "Your account is flagged. Verify at https://evil.example")
        self.assertIn("isn't a member", out)
        user(STRANGER).send.assert_not_awaited()
        self.assertIn("send_dm", ctx.failed)

    async def test_a_member_is(self):
        ctx, user = self.ctx({ASKER, MEMBER})
        out = await self.dm(ctx, MEMBER, "raid's at 8")
        self.assertTrue(out.startswith(DM_OK), out)
        user(MEMBER).send.assert_awaited_once_with("raid's at 8")

    async def test_the_asker_can_always_be_messaged(self):
        ctx, user = self.ctx(set(), is_dm=True)
        out = await self.dm(ctx, None, "here's that list")
        self.assertTrue(out.startswith(DM_OK), out)
        user(ASKER).send.assert_awaited_once()

    async def test_a_dm_from_outside_the_home_server_cant_reach_its_members(self):
        ctx, user = self.ctx({MEMBER}, is_dm=True)
        out = await self.dm(ctx, MEMBER, "hello from a stranger")
        self.assertIn("isn't one", out)
        user(MEMBER).send.assert_not_awaited()

    async def test_a_home_member_can_from_a_dm(self):
        ctx, user = self.ctx({ASKER, MEMBER}, is_dm=True)
        out = await self.dm(ctx, MEMBER, "see you at 8")
        self.assertTrue(out.startswith(DM_OK), out)


class PerReply(_Case):
    async def test_one_reply_sends_at_most_five(self):
        ctx, _ = self.ctx({ASKER} | {10_000 + i for i in range(25)})
        calls = []
        for i in range(25):
            call = MagicMock()
            call.name, call.args = "send_dm", {"user_id": str(10_000 + i), "message": "spam"}
            calls.append(call)
        first = MagicMock()
        first.candidates[0].content.parts = [MagicMock(function_call=c, text=None) for c in calls]
        first.function_calls = []
        done = MagicMock()
        done.candidates[0].content.parts = []
        done.function_calls = []
        done.text = "done"
        client = MagicMock()
        client.generate_with_tools = AsyncMock(side_effect=[first, done])
        sent = AsyncMock(side_effect=lambda uid, text: f"{DM_OK} user{uid}")
        with patch.object(ctx.actions, "send_dm", sent), patch(
            "olisar.pipeline.get_gemini", return_value=client
        ), patch("olisar.pipeline._complete_truncated", new=AsyncMock(return_value="done")):
            await pipeline._run_tool_loop([], "sys", None, ctx, tools=pipeline.TOOLS)
        self.assertEqual(DMS_PER_REPLY, 5)
        self.assertEqual(sent.await_count, DMS_PER_REPLY)
        self.assertIn("send_dm", ctx.failed)

    async def test_the_cap_covers_dms_to_the_asker_too(self):
        ctx, user = self.ctx({ASKER})
        for _ in range(DMS_PER_REPLY + 3):
            await self.dm(ctx, None)
        self.assertEqual(user(ASKER).send.await_count, DMS_PER_REPLY)


class PerDay(_Case):
    async def reply_dms(self, targets: list[int]) -> list[str]:
        """One reply's worth of DMs from ASKER."""
        ctx, _ = self.ctx({ASKER, *targets})
        return [await self.dm(ctx, t) for t in targets]

    async def test_a_member_can_have_twenty_people_messaged_a_day(self):
        people = [20_000 + i for i in range(budgets.DMS_PER_DAY + 1)]
        results = []
        for i in range(0, len(people), DMS_PER_REPLY):
            results += await self.reply_dms(people[i : i + DMS_PER_REPLY])
        self.assertEqual(budgets.DMS_PER_DAY, 20)
        self.assertEqual(sum(r.startswith(DM_OK) for r in results), budgets.DMS_PER_DAY)
        self.assertIn("in the last day", results[-1])

    async def test_dms_to_the_asker_dont_count(self):
        for _ in range(budgets.DMS_PER_DAY + 1):
            ctx, _ = self.ctx({ASKER})
            await self.dm(ctx, None)
        self.assertEqual(budgets.dms_left(ASKER), budgets.DMS_PER_DAY)

    async def test_a_dm_that_didnt_go_through_doesnt_count(self):
        ctx, user = self.ctx({ASKER, MEMBER})
        user(MEMBER).send.side_effect = discord.Forbidden(NS(status=403, reason="closed"), "closed")
        out = await self.dm(ctx, MEMBER)
        self.assertIn("DMs are closed", out)
        self.assertEqual(budgets.dms_left(ASKER), budgets.DMS_PER_DAY)

    async def test_the_day_rolls(self):
        budgets.note_dm(ASKER)
        budgets._dms[ASKER][0] -= 24 * 3600 + 1
        self.assertEqual(budgets.dms_left(ASKER), budgets.DMS_PER_DAY)


class IsMember(unittest.IsolatedAsyncioTestCase):
    async def test_asks_the_cache_then_discord(self):
        home = NS(id=HOME, get_member=lambda uid: None,
                  fetch_member=AsyncMock(return_value=NS(id=MEMBER)))
        actions = BotActions(NS(get_guild=lambda gid: home if gid == HOME else None))
        self.assertTrue(await actions.is_member(MEMBER, HOME))
        home.fetch_member.side_effect = _not_found
        self.assertFalse(await actions.is_member(STRANGER, HOME))
        self.assertFalse(await actions.is_member(MEMBER, 999))


if __name__ == "__main__":
    unittest.main()
