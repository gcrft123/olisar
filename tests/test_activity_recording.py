"""What the bot writes down for the server app's activity feed (olisar/activity.py reads it).

Run:  uv run python -m unittest tests.test_activity_recording -v

Covered: each of Olisar's own replies keeps how it was reached and what it answered; a member
keeps when they joined and the guild when its roster was last synced (bots not counted); a
fact the ``remember`` tool saves keeps its server message but never a DM's; and the status it
sets and the images it posts are noted in ``bot_activity``, which keeps only the newest.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot import replies
from bot.cogs import members as members_cog
from bot.cogs import presence as presence_cog
from olisar import tools
from olisar.db.models import Base, BotActivity, Guild, Message, UserMemory, UserProfile
from olisar.memory import writer
from olisar.tools import STATUS_OK, ToolContext

GUILD = 6001
CHANNEL = 21
DM_CHANNEL = 98
ADA = 301
BOT = 900


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def rows(self, model):
        async with self.Session() as session:
            return (await session.scalars(select(model).order_by(model.id))).all()


class ReplyTriggerTests(_Db):
    async def test_every_part_of_a_reply_keeps_its_trigger_and_what_it_answered(self) -> None:
        sent = [SimpleNamespace(id=5001, content="it's at pyro"), SimpleNamespace(id=5002, content="want the route?")]
        with patch.object(replies, "session_scope", self.scope):
            await replies.record_bot_messages(
                sent, guild_id=GUILD, channel_id=CHANNEL, bot_user_id=BOT,
                trigger="mention", answering=5000,
            )
        stored = await self.rows(Message)
        self.assertEqual([(m.message_id, m.trigger, m.reply_to_message_id) for m in stored],
                         [(5001, "mention", 5000), (5002, "mention", 5000)])
        self.assertTrue(all(m.author_is_bot and m.author_name == "" for m in stored))

    async def test_a_reply_recorded_the_old_way_has_neither(self) -> None:
        with patch.object(replies, "session_scope", self.scope):
            await replies.record_bot_messages(
                [SimpleNamespace(id=5003, content="hi")], guild_id=GUILD, channel_id=CHANNEL, bot_user_id=BOT,
            )
        [m] = await self.rows(Message)
        self.assertEqual((m.trigger, m.reply_to_message_id), (None, None))

    async def test_only_olisar_s_own_rows_carry_a_trigger(self) -> None:
        async with self.scope() as session:
            await writer.record_message(
                session, guild_id=GUILD, channel_id=CHANNEL, message_id=5004, author_id=ADA,
                author_is_bot=False, content="olisar?", display_name="Ada", trigger="name",
            )
        [m] = await self.rows(Message)
        self.assertIsNone(m.trigger)


def _member(uid: int, name: str, *, bot: bool = False, joined: datetime | None = None, guild=None):
    return SimpleNamespace(
        id=uid, bot=bot, display_name=name, guild=guild, roles=[],
        display_avatar=SimpleNamespace(url=f"https://cdn.discordapp.com/avatars/{uid}/x.png"),
        joined_at=joined,
    )


class RosterTests(_Db):
    async def test_the_sync_keeps_join_dates_and_records_itself(self) -> None:
        async with self.scope() as session:
            session.add(Guild(id=GUILD, name="Home", active=True))
        guild = SimpleNamespace(id=GUILD)
        joined = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)
        people = [
            _member(ADA, "Ada", joined=joined, guild=guild),
            _member(302, "Ben", joined=datetime(2026, 9, 25, tzinfo=timezone.utc), guild=guild),
            _member(303, "MEE6", bot=True, joined=joined, guild=guild),
        ]

        async def fetch_members(limit=None):
            for p in people:
                yield p

        guild.fetch_members = fetch_members
        cog = members_cog.Members(SimpleNamespace(guilds=[guild]))
        with patch.object(members_cog, "session_scope", self.scope):
            await cog.on_ready()

        profiles = {p.user_id: p for p in await self.rows(UserProfile)}
        self.assertEqual(set(profiles), {ADA, 302})  # no profile, and no count, for a bot
        self.assertEqual(profiles[ADA].joined_at.replace(tzinfo=timezone.utc), joined)
        [row] = await self.rows_guild()
        self.assertEqual(row.roster_count, 2)
        self.assertIsNotNone(row.roster_synced_at)

    async def rows_guild(self):
        async with self.Session() as session:
            return (await session.scalars(select(Guild))).all()

    async def test_a_join_keeps_its_date_and_a_role_change_keeps_it(self) -> None:
        guild = SimpleNamespace(id=GUILD)
        joined = datetime(2026, 9, 26, 9, 30, tzinfo=timezone.utc)
        cog = members_cog.Members(SimpleNamespace(guilds=[]))
        with patch.object(members_cog, "session_scope", self.scope):
            await cog.on_member_join(_member(ADA, "Ada", joined=joined, guild=guild))
            # A member object without a join date must not clear the one stored.
            await cog._sync_member(_member(ADA, "Ada", joined=None, guild=guild))
        [p] = await self.rows(UserProfile)
        self.assertEqual(p.joined_at.replace(tzinfo=timezone.utc), joined)

    async def test_a_sync_before_the_guild_row_exists_is_skipped_quietly(self) -> None:
        cog = members_cog.Members(SimpleNamespace(guilds=[]))
        with patch.object(members_cog, "session_scope", self.scope):
            await cog._record_sync(GUILD, 5)
        self.assertEqual(await self.rows_guild(), [])


class ToolRecordTests(_Db):
    def ctx(self, session, *, dm: bool = False, message_id: int = 7000, actions=None) -> ToolContext:
        return ToolContext(
            session=session, cfg_guild=GUILD, channel_id=DM_CHANNEL if dm else CHANNEL, user_id=ADA,
            display_name="Ada", is_dm=dm, message_id=message_id, actions=actions,
        )

    async def test_a_fact_saved_in_a_server_keeps_its_message(self) -> None:
        async with self.scope() as session:
            await tools._dispatch("remember", {"fact": "Prefers cargo runs"}, self.ctx(session))
        [m] = await self.rows(UserMemory)
        self.assertEqual((m.guild_id, m.source_message_id), (GUILD, 7000))

    async def test_a_fact_saved_in_a_dm_never_points_at_the_dm(self) -> None:
        """Filed under the home server all the same; the empty source is what keeps it out
        of the activity feed."""
        async with self.scope() as session:
            await tools._dispatch("remember", {"fact": "Lives in Berlin"}, self.ctx(session, dm=True))
        [m] = await self.rows(UserMemory)
        self.assertEqual((m.guild_id, m.source_message_id), (GUILD, None))

    async def test_a_fact_from_ask_has_no_message(self) -> None:
        async with self.scope() as session:
            await tools._dispatch("remember", {"fact": "x"}, self.ctx(session, message_id=0))
        [m] = await self.rows(UserMemory)
        self.assertIsNone(m.source_message_id)

    async def test_a_status_discord_took_is_recorded(self) -> None:
        actions = MagicMock()
        actions.set_status = AsyncMock(return_value=f"{STATUS_OK} plotting a route")
        async with self.scope() as session:
            out = await tools._dispatch("set_status", {"text": "plotting a route"}, self.ctx(session, actions=actions))
        self.assertTrue(out.startswith(STATUS_OK))
        [row] = await self.rows(BotActivity)
        self.assertEqual((row.kind, row.text, row.guild_id, row.channel_id, row.request_message_id),
                         ("status", "plotting a route", GUILD, CHANNEL, 7000))

    async def test_a_status_set_in_a_dm_is_filed_under_the_dm(self) -> None:
        actions = MagicMock()
        actions.set_status = AsyncMock(return_value=f"{STATUS_OK} thinking")
        async with self.scope() as session:
            await tools._dispatch("set_status", {"text": "thinking"}, self.ctx(session, dm=True, actions=actions))
        [row] = await self.rows(BotActivity)
        self.assertEqual(row.guild_id, 0)

    async def test_a_status_discord_refused_is_not(self) -> None:
        actions = MagicMock()
        actions.set_status = AsyncMock(return_value="couldn't set status: 429")
        async with self.scope() as session:
            await tools._dispatch("set_status", {"text": "x"}, self.ctx(session, actions=actions))
        self.assertEqual(await self.rows(BotActivity), [])

    async def test_a_posted_image_is_recorded_with_what_it_answered(self) -> None:
        actions = MagicMock()
        actions.send_image = AsyncMock(return_value="image posted")
        with patch.object(tools, "image_is_configured", AsyncMock(return_value=True)), \
                patch.object(tools, "generate_image", AsyncMock(return_value=(b"jpg", "image/jpeg"))):
            async with self.scope() as session:
                await tools._dispatch("generate_image", {"prompt": "a Vulture over Pyro"},
                                      self.ctx(session, actions=actions))
        [row] = await self.rows(BotActivity)
        self.assertEqual((row.kind, row.text, row.guild_id, row.channel_id, row.request_message_id),
                         ("image", "a Vulture over Pyro", GUILD, CHANNEL, 7000))

    async def test_an_image_that_did_not_post_is_not(self) -> None:
        actions = MagicMock()
        actions.send_image = AsyncMock(return_value="can't post an image here — missing permission")
        with patch.object(tools, "image_is_configured", AsyncMock(return_value=True)), \
                patch.object(tools, "generate_image", AsyncMock(return_value=(b"jpg", "image/jpeg"))):
            async with self.scope() as session:
                await tools._dispatch("generate_image", {"prompt": "x"}, self.ctx(session, actions=actions))
        self.assertEqual(await self.rows(BotActivity), [])


class BotActivityTests(_Db):
    async def test_only_the_newest_of_each_kind_are_kept(self) -> None:
        for i in range(writer.BOT_ACTIVITY_KEEP + 5):
            async with self.scope() as session:
                await writer.record_bot_activity(session, kind="image", text=f"image {i}", guild_id=GUILD)
        async with self.scope() as session:
            await writer.record_bot_activity(session, kind="status", text="hello")
        async with self.Session() as session:
            images = (await session.scalars(
                select(BotActivity.text).where(BotActivity.kind == "image").order_by(BotActivity.id)
            )).all()
            statuses = await session.scalar(
                select(func.count()).select_from(BotActivity).where(BotActivity.kind == "status")
            )
        self.assertEqual(len(images), writer.BOT_ACTIVITY_KEEP)
        self.assertEqual(images[0], "image 5")
        self.assertEqual(statuses, 1)

    async def test_the_startup_status_is_recorded(self) -> None:
        bot = MagicMock()
        bot.change_presence = AsyncMock()
        cog = presence_cog.Presence(bot)
        with patch.object(presence_cog, "session_scope", self.scope), \
                patch.object(cog, "_invent_status", AsyncMock(return_value="watching the stars")):
            await cog.on_ready()
            await cog.on_ready()  # a reconnect doesn't set (or record) it again
        [row] = await self.rows(BotActivity)
        self.assertEqual((row.kind, row.text, row.guild_id, row.channel_id), ("status", "watching the stars", None, None))


if __name__ == "__main__":
    unittest.main()
