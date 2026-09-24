"""Coverage for the gates that decide whether Olisar may speak up unasked.

Run:  uv run python -m unittest tests.test_proactive_gates -v

One message could get two replies: the addressed one and a proactive one. The proactive
scans treat a message as unanswered until Olisar's reply to it is stored, and the addressed
path only stores its reply after the last part of it has been sent. A slow answer ("olisar
what's the best mining ship?" plus a web search and a three-bubble reply) is still in flight
when the message turns 15 seconds old, so the scan picked it up, the classifier saw an open
question, and a second reply went out under the first.

The hourly cap never filled. The scan used one variable for the hour's timestamps and for the
rows it read inside the loop, so each chime was appended to a list of messages instead.

Nothing checked who was being answered. Someone the role gate or the global ban list
refuses is ignored when they ask Olisar by name, and was then answered anyway 15 seconds
later, because the scan never asked.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord.ext import tasks
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import bot.cogs.proactive as proactive
from bot.cogs.conversation import Conversation
from bot.replies import is_reply_pending, reply_pending
from olisar import moderation
from olisar.db.models import (
    Base,
    Guild,
    GuildConfig,
    Message,
    ProactivityConfig,
    ProactivityLevel,
    utcnow,
)

GUILD, CHANNEL, ASKER = 1, 10, 500
BLOCKED_ROLE = 77


class ReplyPendingTest(unittest.TestCase):
    def test_marked_for_the_block_only(self) -> None:
        self.assertFalse(is_reply_pending(42))
        with reply_pending(42):
            self.assertTrue(is_reply_pending(42))
        self.assertFalse(is_reply_pending(42))

    def test_cleared_when_the_reply_fails(self) -> None:
        """A reply that raises must not leave its message hidden from the scans forever."""
        with self.assertRaises(RuntimeError), reply_pending(42):
            raise RuntimeError("model call failed")
        self.assertFalse(is_reply_pending(42))


class ConversationHoldsTheMessageTest(unittest.IsolatedAsyncioTestCase):
    async def test_pending_for_the_whole_handler(self) -> None:
        seen: list[bool] = []

        async def handle(message) -> None:
            seen.append(is_reply_pending(message.id))

        cog = Conversation(SimpleNamespace())
        with patch.object(cog, "_handle", side_effect=handle):
            await cog.on_message(SimpleNamespace(id=77))
        self.assertEqual(seen, [True])
        self.assertFalse(is_reply_pending(77))


class _DbCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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
        async with scope() as session:
            session.add(Guild(id=GUILD))
            session.add(
                ProactivityConfig(
                    guild_id=GUILD,
                    enabled=True,
                    level=ProactivityLevel.high,
                    confidence_threshold=0.5,
                    global_cooldown_sec=0,
                    channel_cooldown_sec=0,
                    allowed_channels=[CHANNEL],
                    reaction_enabled=True,
                    reaction_cooldown_sec=0,
                )
            )
        # The scan loops aren't under test, and starting them needs a real client. No guild
        # in the cache means no member to look up: no roles, so open access lets them in.
        with patch.object(tasks.Loop, "start"):
            self.cog = proactive.Proactive(SimpleNamespace(get_guild=lambda _gid: None))

    async def asyncTearDown(self) -> None:
        self._patch.stop()
        await self.engine.dispose()
        self._tmp.cleanup()

    async def _store(self, message_id: int, content: str, *, age: float, bot: bool = False):
        async with self.scope() as session:
            session.add(
                Message(
                    guild_id=GUILD,
                    channel_id=CHANNEL,
                    message_id=message_id,
                    author_id=ASKER,
                    author_is_bot=bot,
                    content=content,
                    created_at=utcnow() - timedelta(seconds=age),
                )
            )

    @contextlib.contextmanager
    def _chiming(self, classify: AsyncMock, chime: AsyncMock):
        """Stages 2 and 3 of the chime cascade stubbed out, so only the scan's gates run."""
        with (
            patch.object(proactive, "classify", classify),
            patch.object(self.cog, "_transcript", AsyncMock(return_value="")),
            patch.object(self.cog, "_chime_in", chime),
        ):
            yield

    @contextlib.contextmanager
    def _reacting(self, pick: AsyncMock):
        with (
            patch.object(proactive, "pick_reaction_emoji", pick),
            patch.object(self.cog, "_transcript", AsyncMock(return_value="")),
        ):
            yield


class ScansSkipPendingTest(_DbCase):
    async def test_the_chime_scan_waits_for_the_addressed_reply(self) -> None:
        await self._store(1001, "olisar what's the best mining ship?", age=20)
        classify = AsyncMock(return_value=(True, 0.9, "open question"))
        chime = AsyncMock(return_value=True)
        with self._chiming(classify, chime):
            with reply_pending(1001):
                self.assertFalse(await self.cog._scan_guild(GUILD))
            classify.assert_not_awaited()
            chime.assert_not_awaited()

            # Skipped, not written off: had the addressed path decided the name was only
            # a passing mention, the message is still the scan's to judge.
            self.assertTrue(await self.cog._scan_guild(GUILD))
            chime.assert_awaited_once()

    async def test_the_reaction_scan_waits_too(self) -> None:
        await self._store(1002, "gg we finally shipped it lol", age=10)
        pick = AsyncMock(return_value=None)
        with self._reacting(pick):
            with reply_pending(1002):
                self.assertFalse(await self.cog._scan_reactions_guild(GUILD))
            pick.assert_not_awaited()

            await self.cog._scan_reactions_guild(GUILD)
            pick.assert_awaited_once()


class HourlyCapTest(_DbCase):
    async def test_a_chime_counts_toward_the_cap(self) -> None:
        async with self.scope() as session:
            (await session.get(ProactivityConfig, GUILD)).max_per_hour = 1
        classify = AsyncMock(return_value=(True, 0.9, "open question"))
        chime = AsyncMock(return_value=True)
        with self._chiming(classify, chime):
            await self._store(2001, "anyone know when the patch drops?", age=30)
            self.assertTrue(await self.cog._scan_guild(GUILD))

            await self._store(2002, "which ship is best for salvage?", age=20)
            self.assertFalse(await self.cog._scan_guild(GUILD))
        chime.assert_awaited_once()


class AccessTest(_DbCase):
    """Whoever the conversation handler refuses, the scans refuse too."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        async with self.scope() as session:
            session.add(GuildConfig(guild_id=GUILD, blocked_role_ids=[BLOCKED_ROLE]))

    def _member_with(self, role_id: int) -> None:
        role = SimpleNamespace(id=role_id, is_default=lambda: False)
        member = SimpleNamespace(
            id=ASKER, roles=[role], guild_permissions=SimpleNamespace(manage_guild=False)
        )
        guild = SimpleNamespace(get_member=lambda uid: member if uid == ASKER else None)
        self.cog.bot = SimpleNamespace(get_guild=lambda _gid: guild)

    async def _chimed(self) -> bool:
        await self._store(3001, "anyone know when the patch drops?", age=20)
        chime = AsyncMock(return_value=True)
        with self._chiming(AsyncMock(return_value=(True, 0.9, "open question")), chime):
            await self.cog._scan_guild(GUILD)
        return chime.await_count > 0

    async def _reacted(self) -> bool:
        await self._store(3002, "gg we finally shipped it lol", age=10)
        pick = AsyncMock(return_value=None)
        with self._reacting(pick):
            await self.cog._scan_reactions_guild(GUILD)
        return pick.await_count > 0

    async def test_a_blocked_role_is_not_answered(self) -> None:
        self._member_with(BLOCKED_ROLE)
        self.assertFalse(await self._chimed())

    async def test_a_blocked_role_gets_no_reaction(self) -> None:
        self._member_with(BLOCKED_ROLE)
        self.assertFalse(await self._reacted())

    async def test_a_banned_user_is_not_answered(self) -> None:
        self._member_with(1)
        with patch.object(moderation, "_banned", {ASKER}):
            self.assertFalse(await self._chimed())
            self.assertFalse(await self._reacted())

    async def test_everyone_else_still_is(self) -> None:
        self._member_with(1)
        self.assertTrue(await self._chimed())
        self.assertTrue(await self._reacted())


class ChimeRechecksBeforeSendingTest(_DbCase):
    async def _chime(self) -> tuple[bool, AsyncMock]:
        channel = SimpleNamespace(
            id=CHANNEL,
            name="general",
            topic="",
            fetch_message=AsyncMock(side_effect=Exception("not cached")),
        )
        self.cog.bot = SimpleNamespace(get_channel=lambda _cid: channel, user=SimpleNamespace(id=9))
        reply = SimpleNamespace(silent=False, text="the Prospector, if you're solo", emoji=None)
        send = AsyncMock(return_value=[])

        @contextlib.asynccontextmanager
        async def quiet(_channel):
            yield

        with (
            patch.object(proactive, "generate_reply", AsyncMock(return_value=reply)),
            patch.object(proactive, "composing", quiet),
            patch.object(proactive, "send_paced", send),
            patch.object(proactive, "record_bot_messages", AsyncMock()),
        ):
            sent = await self.cog._chime_in(GUILD, CHANNEL, 1003, ASKER, "best mining ship?")
        return sent, send

    async def test_sends_when_nothing_has_landed_since(self) -> None:
        await self._store(1003, "best mining ship?", age=20)
        sent, send = await self._chime()
        self.assertTrue(sent)
        send.assert_awaited_once()

    async def test_drops_the_reply_when_olisar_already_answered(self) -> None:
        await self._store(1003, "best mining ship?", age=20)
        await self._store(1004, "prospector for solo, mole with a crew", age=2, bot=True)
        sent, send = await self._chime()
        self.assertFalse(sent)
        send.assert_not_awaited()

    async def test_drops_the_reply_when_someone_else_spoke(self) -> None:
        await self._store(1003, "best mining ship?", age=20)
        await self._store(1005, "prospector, easy", age=2)
        sent, send = await self._chime()
        self.assertFalse(sent)
        send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
