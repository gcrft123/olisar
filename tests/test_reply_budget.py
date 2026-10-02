"""One member, or one server, can't have Olisar spend the install's quota without limit.

Run:  uv run python -m unittest tests.test_reply_budget -v

Every reply is several model calls against a quota the whole install shares, and nothing
limited how many one person could ask for: thirty @mentions got thirty full replies, and a
single message could have the model generate a dozen images. The proactive per-member
cooldown was stored and shown in the API but never read.

Replies (in chat, /ask and /catchup) now come out of a token bucket per member and one per
server. Someone over budget is told once and then left unanswered until it refills. One
reply makes at most two images, and a proactive chime-in waits out the member cooldown
before it answers the same person again.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import tasks
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.cogs.conversation as conv
import bot.cogs.proactive as proactive
from bot.cogs import slash as slash_mod
from olisar import budgets, pipeline
from olisar.db.models import (
    Base,
    ChannelAllowlist,
    ChannelMode,
    Guild,
    Message,
    ProactivityConfig,
    ProactivityLevel,
    utcnow,
)
from olisar.guild_setup import ensure_guild_defaults
from olisar.messages import DEFAULT_COMMAND_MESSAGES
from olisar.tools import IMAGES_PER_REPLY, ToolContext

GUILD = 1001
OTHER = 2002
CHANNEL = 3003
SECOND_CHANNEL = 3004
USER = 5005
BOT_ID = 9


@contextlib.contextmanager
def fresh_budgets():
    """Empty buckets for one test, so no test spends another's."""
    members = budgets.TokenBucket(budgets.MEMBER_BURST, budgets.MEMBER_PER_MINUTE)
    servers = budgets.TokenBucket(budgets.SERVER_BURST, budgets.SERVER_PER_MINUTE)
    with patch.object(budgets, "_members", members), patch.object(
        budgets, "_servers", servers
    ), patch.object(budgets, "_told", set()):
        yield


class Buckets(unittest.TestCase):
    def test_a_burst_then_a_steady_rate(self):
        bucket = budgets.TokenBucket(burst=3, per_minute=6)  # one every 10 s
        for _ in range(3):
            self.assertTrue(bucket.has(USER, 0.0))
            bucket.take(USER, 0.0)
        self.assertFalse(bucket.has(USER, 0.0))
        self.assertFalse(bucket.has(USER, 9.0))
        self.assertTrue(bucket.has(USER, 10.0))

    def test_never_holds_more_than_its_burst(self):
        bucket = budgets.TokenBucket(burst=2, per_minute=60)
        bucket.take(USER, 0.0)
        for _ in range(2):
            bucket.take(USER, 3600.0)
        self.assertFalse(bucket.has(USER, 3600.0))

    def test_one_member_running_dry_leaves_the_rest_alone(self):
        with fresh_budgets():
            taken = sum(budgets.take_reply(USER, GUILD) for _ in range(30))
            self.assertEqual(taken, budgets.MEMBER_BURST)
            self.assertTrue(budgets.take_reply(USER + 1, GUILD))

    def test_a_server_running_dry_stops_it_but_no_other(self):
        with fresh_budgets():
            taken = sum(budgets.take_reply(USER + i, GUILD) for i in range(100))
            self.assertEqual(taken, budgets.SERVER_BURST)
            self.assertTrue(budgets.take_reply(USER, OTHER))

    def test_a_refusal_spends_nothing(self):
        with fresh_budgets():
            for i in range(budgets.SERVER_BURST):
                budgets.take_reply(USER + 100 + i, GUILD)
            self.assertFalse(budgets.take_reply(USER, GUILD))
            # The server refused it, so the member's own bucket is still full.
            self.assertEqual(
                sum(budgets.take_reply(USER, OTHER) for _ in range(30)), budgets.MEMBER_BURST
            )

    def test_told_once_per_run_of_refusals(self):
        with fresh_budgets():
            self.assertTrue(budgets.first_refusal(USER))
            self.assertFalse(budgets.first_refusal(USER))
            budgets.take_reply(USER, GUILD)
            self.assertTrue(budgets.first_refusal(USER))


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.scope() as s:
            await ensure_guild_defaults(s, GUILD, name="Home")
            s.add(ChannelAllowlist(guild_id=GUILD, channel_id=CHANNEL, mode=ChannelMode.both))

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()


@contextlib.asynccontextmanager
async def _quiet(*_a, **_k):
    yield


class InChat(_Db):
    async def test_thirty_mentions_get_a_burst_of_replies_and_one_notice(self):
        me = NS(id=BOT_ID)
        cog = conv.Conversation.__new__(conv.Conversation)
        cog.bot = NS(user=me, get_guild=lambda gid: None, guilds=[], cached_messages=[])
        cog._media_tasks = set()
        chan = NS(id=CHANNEL, name="general", topic="")
        gen = AsyncMock(return_value=pipeline.Reply("ok"))
        send = AsyncMock(return_value=[])
        with fresh_budgets(), patch.object(conv, "session_scope", self.scope), patch.object(
            conv, "generate_reply", gen
        ), patch.object(conv, "send_paced", send), patch.object(
            conv, "record_bot_messages", AsyncMock()
        ), patch.object(conv, "composing", _quiet):
            for i in range(30):
                await cog._handle(NS(
                    id=20_000_000_000_000_000 + i,
                    author=NS(id=USER, bot=False, display_name="m", roles=[]),
                    guild=NS(id=GUILD), channel=chan, content=f"<@{BOT_ID}> question {i}",
                    embeds=[], attachments=[], stickers=[], reference=None, mentions=[me],
                    created_at=datetime.now(timezone.utc),
                ))
        self.assertEqual(gen.await_count, budgets.MEMBER_BURST)
        notices = [c for c in send.await_args_list if c.args[1] == DEFAULT_COMMAND_MESSAGES["rate_limit"]]
        self.assertEqual(len(notices), 1)
        self.assertEqual(send.await_count, budgets.MEMBER_BURST + 1)


class InSlashCommands(_Db):
    async def test_ask_says_so_once_the_budget_is_spent(self):
        member = NS(id=USER, roles=[], guild_permissions=NS(manage_guild=False), display_name="m")
        guild = NS(id=GUILD, get_member=lambda uid: member)
        cog = slash_mod.Slash.__new__(slash_mod.Slash)
        cog.bot = NS(user=NS(id=BOT_ID), guilds=[guild], get_guild={GUILD: guild}.get)
        gen = AsyncMock(return_value=pipeline.Reply("hi"))
        refusals = 0
        with fresh_budgets(), patch.object(slash_mod, "session_scope", self.scope), patch.object(
            slash_mod, "generate_reply", gen
        ):
            for _ in range(budgets.MEMBER_BURST + 2):
                inter = NS(
                    guild_id=GUILD, channel_id=CHANNEL, channel=NS(id=CHANNEL, name="general", topic=""),
                    user=member, id=77, response=NS(send_message=AsyncMock(), defer=AsyncMock()),
                    followup=NS(send=AsyncMock()),
                )
                await slash_mod.Slash.ask.callback(cog, inter, "hello")
                if inter.response.send_message.await_count:
                    refusals += 1
                    self.assertEqual(
                        inter.response.send_message.await_args.args[0],
                        DEFAULT_COMMAND_MESSAGES["rate_limit"],
                    )
        self.assertEqual(gen.await_count, budgets.MEMBER_BURST)
        self.assertEqual(refusals, 2)


def _resp(*calls):
    resp = MagicMock()
    parts = []
    for c in calls:
        part = MagicMock()
        part.function_call = c
        part.text = None
        parts.append(part)
    resp.candidates[0].content.parts = parts
    resp.function_calls = []
    resp.text = None
    return resp


def _call(name: str, **args):
    call = MagicMock()
    call.name = name
    call.args = args
    return call


class Images(unittest.IsolatedAsyncioTestCase):
    async def test_one_reply_makes_at_most_two(self):
        session = MagicMock()
        session.in_transaction.return_value = False
        ctx = ToolContext(session=session, cfg_guild=GUILD, channel_id=CHANNEL, user_id=USER,
                          display_name="m")
        ctx.actions = MagicMock()
        ctx.actions.send_image = AsyncMock(return_value="image posted")
        make = AsyncMock(return_value=(b"\x89PNG....", "image/png"))
        answered = MagicMock()
        answered.candidates[0].content.parts = []
        answered.function_calls = []
        answered.text = "here you go"
        client = MagicMock()
        client.generate_with_tools = AsyncMock(side_effect=[
            _resp(*[_call("generate_image", prompt=f"cat {i}") for i in range(12)]), answered,
        ])
        with patch("olisar.pipeline.get_gemini", return_value=client), patch(
            "olisar.pipeline._complete_truncated", new=AsyncMock(return_value="here you go")
        ), patch("olisar.tools.generate_image", make), patch(
            "olisar.tools.image_is_configured", AsyncMock(return_value=True)
        ), patch("olisar.tools.record_bot_activity", AsyncMock()):
            await pipeline._run_tool_loop([], "sys", None, ctx, tools=pipeline.TOOLS)
        self.assertEqual(IMAGES_PER_REPLY, 2)
        self.assertEqual(make.await_count, IMAGES_PER_REPLY)
        self.assertEqual(ctx.actions.send_image.await_count, IMAGES_PER_REPLY)
        # The rest were refused, so a silent reaction can't paper over them.
        self.assertIn("generate_image", ctx.failed)


class ProactiveMemberCooldown(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def scope():
            async with self.Session() as session:
                yield session
                await session.commit()

        self.scope = scope
        self._patch = patch.object(proactive, "session_scope", scope)
        self._patch.start()
        async with scope() as s:
            s.add(Guild(id=GUILD))
            s.add(ProactivityConfig(
                guild_id=GUILD, enabled=True, level=ProactivityLevel.high,
                confidence_threshold=0.5, global_cooldown_sec=0, channel_cooldown_sec=0,
                user_cooldown_sec=120, max_per_hour=100,
                allowed_channels=[CHANNEL, SECOND_CHANNEL],
            ))
        with patch.object(tasks.Loop, "start"):
            self.cog = proactive.Proactive(NS(get_guild=lambda _gid: None))

    async def asyncTearDown(self):
        self._patch.stop()
        await self.engine.dispose()
        self._tmp.cleanup()

    async def _store(self, message_id: int, channel_id: int) -> None:
        async with self.scope() as s:
            s.add(Message(guild_id=GUILD, channel_id=channel_id, message_id=message_id,
                          author_id=USER, content="anyone know when the patch drops?",
                          created_at=utcnow() - timedelta(seconds=20)))

    async def test_the_same_member_waits_out_the_cooldown(self):
        chime = AsyncMock(return_value=True)
        with patch.object(
            proactive, "classify", AsyncMock(return_value=(True, 0.9, "q"))
        ), patch.object(self.cog, "_transcript", AsyncMock(return_value="")), patch.object(
            self.cog, "_chime_in", chime
        ):
            await self._store(1, CHANNEL)
            self.assertTrue(await self.cog._scan_guild(GUILD))
            await self._store(2, SECOND_CHANNEL)
            self.assertFalse(await self.cog._scan_guild(GUILD))
            # Not written off: once the cooldown is up, the message is still considered.
            self.cog._user_cooldown[(GUILD, USER)] -= 121
            self.assertTrue(await self.cog._scan_guild(GUILD))
        self.assertEqual(chime.await_count, 2)


if __name__ == "__main__":
    unittest.main()
