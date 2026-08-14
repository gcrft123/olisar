"""Coverage for the member portal's authorization boundary and its data promises.

Run:  uv run python -m unittest tests.test_member_portal -v

The portal is the first surface that admits someone who is *not* an admin, so the tests
that matter most here are the ones that pin what a member cannot reach:

  * a member scoped to one server cannot address another, even one they're in
  * a server whose operator hasn't opened the portal is closed, membership regardless
  * CSRF is enforced on every mutating method and on none of the safe ones
  * a fact belonging to someone else is invisible, not merely forbidden

Plus the two promises the portal makes about data:

  * "stop recording me" actually stops the writers (pause + search opt-out)
  * "delete everything" empties every table the export lists — MEMBER_DATA_TABLES is the
    single contract both sides read, and this asserts they agree
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth.deps import _check_csrf, require_member_guild
from olisar.db.models import (
    Base,
    Guild,
    GuildChannelInfo,
    GuildConfig,
    Message,
    MemberUser,
    Reminder,
    SearchMessage,
    UserMemory,
    UserMemoryKind,
    UserProfile,
    utcnow,
)
from api.routers.member import _breakdowns, _hours_utc
from olisar.memory.purge import MEMBER_DATA_TABLES, forget_user
from olisar.memory.writer import _paused, record_message, record_search_message

GUILD = 4001
OTHER_GUILD = 4002
USER = 7777
OTHER_USER = 8888


def _request(method: str = "GET") -> MagicMock:
    req = MagicMock()
    req.method = method
    return req


def _member(guild_ids: list[str]) -> MemberUser:
    return MemberUser(discord_user_id=USER, username="ada", guild_ids=guild_ids)


class CsrfTests(unittest.TestCase):
    """The portal carries an explicit token rather than resting on SameSite alone."""

    def test_safe_methods_need_no_token(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            _check_csrf(_request(method), "secret", None)  # must not raise

    def test_mutating_methods_require_a_matching_token(self):
        for method in ("POST", "PATCH", "DELETE", "PUT"):
            with self.subTest(method=method):
                with self.assertRaises(HTTPException) as caught:
                    _check_csrf(_request(method), "secret", None)
                self.assertEqual(caught.exception.status_code, 403)
                with self.assertRaises(HTTPException):
                    _check_csrf(_request(method), "secret", "wrong")
                _check_csrf(_request(method), "secret", "secret")  # must not raise

    def test_a_session_with_no_secret_cannot_mutate(self):
        """An empty stored secret must fail closed, not match an empty header."""
        with self.assertRaises(HTTPException):
            _check_csrf(_request("POST"), "", "")


class PauseTests(unittest.TestCase):
    def test_pause_window(self):
        self.assertFalse(_paused(None))
        self.assertTrue(_paused(utcnow() + timedelta(hours=1)))
        self.assertFalse(_paused(utcnow() - timedelta(hours=1)))

    def test_naive_datetimes_are_treated_as_utc(self):
        """SQLite hands back naive datetimes; reading one as local time would silently
        shift the pause window by the host's offset."""
        future = (utcnow() + timedelta(hours=1)).replace(tzinfo=None)
        past = (utcnow() - timedelta(hours=1)).replace(tzinfo=None)
        self.assertTrue(_paused(future))
        self.assertFalse(_paused(past))


class _DbCase(unittest.IsolatedAsyncioTestCase):
    """A real SQLite schema, isolated per test. No vec0/FTS here — the vector helpers are
    patched where they're called, since these tests are about rows, not embeddings."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def seed_guild(self, guild_id: int = GUILD, *, portal: bool = True) -> None:
        async with self.scope() as session:
            session.add(Guild(id=guild_id, name=f"Server {guild_id}", active=True))
            session.add(GuildConfig(guild_id=guild_id, member_portal_enabled=portal))


class GuildScopingTests(_DbCase):
    """The isolation boundary. Everything else in the portal is recoverable; a member
    reading another server's data is not."""

    async def _authorize(self, member: MemberUser, guild_header: str):
        fake_scope = self.scope
        with patch("api.auth.deps.session_scope", fake_scope), \
             patch("api.auth.deps.get_member_for_token", AsyncMock(return_value=(member, "tok"))), \
             patch("api.auth.deps._revalidate_member", AsyncMock()):
            return await require_member_guild(
                _request("GET"),
                olisar_member="cookie",
                x_csrf_token=None,
                x_guild_id=guild_header,
            )

    async def test_member_can_reach_a_server_they_are_in(self):
        await self.seed_guild()
        ctx = await self._authorize(_member([str(GUILD)]), str(GUILD))
        self.assertEqual(ctx.guild_id, GUILD)
        self.assertFalse(ctx.show_persona)  # off unless the operator opted in

    async def test_member_cannot_reach_a_server_they_are_not_in(self):
        await self.seed_guild(OTHER_GUILD)
        with self.assertRaises(HTTPException) as caught:
            await self._authorize(_member([str(GUILD)]), str(OTHER_GUILD))
        self.assertEqual(caught.exception.status_code, 403)

    async def test_non_membership_is_answered_before_the_portal_switch(self):
        """Answering "the portal is off there" to a non-member would confirm the bot is in
        a server they can't see. Both are refusals; they must not be distinguishable."""
        await self.seed_guild(OTHER_GUILD, portal=False)
        with self.assertRaises(HTTPException) as caught:
            await self._authorize(_member([str(GUILD)]), str(OTHER_GUILD))
        self.assertIn("not in that server", caught.exception.detail)

    async def test_portal_disabled_server_is_closed_to_its_own_members(self):
        await self.seed_guild(portal=False)
        with self.assertRaises(HTTPException) as caught:
            await self._authorize(_member([str(GUILD)]), str(GUILD))
        self.assertEqual(caught.exception.status_code, 403)

    async def test_show_persona_follows_the_operators_second_toggle(self):
        await self.seed_guild()
        async with self.scope() as session:
            config = await session.get(GuildConfig, GUILD)
            config.member_portal_show_persona = True
        ctx = await self._authorize(_member([str(GUILD)]), str(GUILD))
        self.assertTrue(ctx.show_persona)

    async def test_missing_or_junk_guild_header_is_rejected(self):
        await self.seed_guild()
        for header in (None, "", "not-a-number"):
            with self.subTest(header=header):
                with self.assertRaises(HTTPException) as caught:
                    await self._authorize(_member([str(GUILD)]), header)
                self.assertEqual(caught.exception.status_code, 400)


class RecordingOptOutTests(_DbCase):
    """"Stop recording me" has to stop the writers, not just the page."""

    async def _profile(self, session, **flags) -> UserProfile:
        profile = UserProfile(
            user_id=USER, guild_id=GUILD, display_name="ada", avatar="", roles=[], notes={}
        )
        for key, value in flags.items():
            setattr(profile, key, value)
        session.add(profile)
        await session.flush()
        return profile

    async def test_search_opt_out_stops_indexing_but_not_conversation(self):
        async with self.scope() as session:
            await self._profile(session, search_opt_out=True)
            indexed = await record_search_message(
                session, guild_id=GUILD, channel_id=1, channel_name="general",
                message_id=101, author_id=USER, author_name="ada", content="findable?",
            )
            self.assertFalse(indexed)
            # The weaker flag must not have become the stronger one: conversation still works.
            stored = await record_message(
                session, guild_id=GUILD, channel_id=1, message_id=102,
                author_id=USER, author_is_bot=False, content="but still chatting",
            )
            self.assertIsNotNone(stored)

    async def test_pause_stops_both_writers(self):
        async with self.scope() as session:
            await self._profile(session, pause_until=utcnow() + timedelta(hours=2))
            self.assertFalse(await record_search_message(
                session, guild_id=GUILD, channel_id=1, channel_name="general",
                message_id=201, author_id=USER, author_name="ada", content="quiet please",
            ))
            self.assertIsNone(await record_message(
                session, guild_id=GUILD, channel_id=1, message_id=202,
                author_id=USER, author_is_bot=False, content="quiet please",
            ))

    async def test_an_expired_pause_resumes_on_its_own(self):
        """The point of a pause over an opt-out: nobody has to remember to undo it."""
        async with self.scope() as session:
            await self._profile(session, pause_until=utcnow() - timedelta(minutes=1))
            self.assertTrue(await record_search_message(
                session, guild_id=GUILD, channel_id=1, channel_name="general",
                message_id=301, author_id=USER, author_name="ada", content="back",
            ))
            self.assertIsNotNone(await record_message(
                session, guild_id=GUILD, channel_id=1, message_id=302,
                author_id=USER, author_is_bot=False, content="back",
            ))


class PortalEnableGateTests(_DbCase):
    """Enabling the portal on a loopback-only install would publish a door with no address.
    Refused at the API, not merely disabled in the console — a stale tab or a script is not
    the console. Runs against a real session so the write path is exercised too."""

    async def _put(self, body: dict, *, remote: bool):
        from api.routers.admin import put_config
        from api.schemas import ConfigIn

        gctx = MagicMock()
        gctx.guild_id = GUILD
        gctx.admin.discord_user_id = USER
        with patch("api.routers.admin.runtime_config.remote_access_configured",
                   AsyncMock(return_value=remote)), \
             patch("api.routers.admin.session_scope", self.scope):
            return await put_config(ConfigIn(**body), gctx)

    async def _stored(self) -> GuildConfig | None:
        async with self.scope() as session:
            return await session.get(GuildConfig, GUILD)

    async def test_enabling_without_remote_access_is_refused(self):
        await self.seed_guild(portal=False)
        with self.assertRaises(HTTPException) as caught:
            await self._put({"member_portal_enabled": True}, remote=False)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("remote access", caught.exception.detail)
        # Refused, not partially applied.
        self.assertFalse((await self._stored()).member_portal_enabled)

    async def test_disabling_is_always_allowed(self):
        """Never block the off switch on the condition that makes it reachable — an operator
        who turned remote access off must still be able to close the portal."""
        await self.seed_guild(portal=True)
        await self._put({"member_portal_enabled": False}, remote=False)
        self.assertFalse((await self._stored()).member_portal_enabled)

    async def test_enabling_with_remote_access_writes_the_flag(self):
        await self.seed_guild(portal=False)
        await self._put({"member_portal_enabled": True}, remote=True)
        self.assertTrue((await self._stored()).member_portal_enabled)


class BreakdownTests(_DbCase):
    """The per-figure donuts on the portal. Order is the contract: the client assigns a
    series hue by position and colours each chip after its largest slice, so a list sorted
    by size would put the biggest slice at us0 every time and turn every chip blue."""

    async def _seed(self) -> None:
        async with self.scope() as session:
            # Two channels, deliberately positioned so the busier one is NOT first.
            session.add(GuildChannelInfo(channel_id=11, guild_id=GUILD, name="general", position=0))
            session.add(GuildChannelInfo(channel_id=22, guild_id=GUILD, name="ship-talk", position=1))
            n = 0
            for channel, days in ((11, 2), (22, 5)):
                for d in range(days):
                    n += 1
                    session.add(Message(
                        guild_id=GUILD, channel_id=channel, message_id=90_000 + n,
                        author_id=USER, author_is_bot=False, content="x",
                        created_at=datetime(2026, 3 if d % 2 else 4, d + 1, 12, 0, tzinfo=timezone.utc),
                    ))
            for kind, count in ((UserMemoryKind.fact, 3), (UserMemoryKind.preference, 2)):
                for _ in range(count):
                    session.add(UserMemory(user_id=USER, guild_id=GUILD, kind=kind, content="c"))
            session.add(SearchMessage(
                guild_id=GUILD, channel_id=11, channel_name="general",
                message_id=70_001, author_id=USER, author_name="ada", content="x",
            ))

    async def test_channels_come_back_in_position_order_not_size_order(self):
        await self._seed()
        async with self.scope() as session:
            b = await _breakdowns(session, GUILD, USER)
        labels = [x["label"] for x in b["messages"]]
        self.assertEqual(labels, ["#general", "#ship-talk"])
        # #ship-talk is the bigger one; it must still come second.
        values = {x["label"]: x["value"] for x in b["messages"]}
        self.assertGreater(values["#ship-talk"], values["#general"])

    async def test_kinds_use_enum_order_and_drop_empty_ones(self):
        await self._seed()
        async with self.scope() as session:
            b = await _breakdowns(session, GUILD, USER)
        self.assertEqual([x["label"] for x in b["facts"]], ["Facts", "Preferences"])
        self.assertEqual([x["value"] for x in b["facts"]], [3, 2])

    async def test_days_are_distinct_dates_grouped_by_month(self):
        """A day counts once however much was said, and months read chronologically.

        The fixture writes 7 messages on 5 distinct dates (two channels overlap on three
        of them), so a count that returned 7 would be counting messages, not days.
        """
        await self._seed()
        async with self.scope() as session:
            b = await _breakdowns(session, GUILD, USER)
        self.assertEqual([x["label"] for x in b["days"]], ["March", "April"])
        self.assertEqual([x["value"] for x in b["days"]], [2, 3])  # Mar 2/4, Apr 1/3/5
        self.assertEqual(sum(x["value"] for x in b["days"]), 5)

    async def test_hours_come_back_raw_and_in_utc(self):
        """Deliberately unbucketed: the client rotates this by its own offset, because the
        server has no idea when the member's evening is."""
        async with self.scope() as session:
            for i, hour in enumerate((0, 3, 3, 21, 21, 21)):
                session.add(Message(
                    guild_id=GUILD, channel_id=11, message_id=80_000 + i,
                    author_id=USER, author_is_bot=False, content="x",
                    created_at=datetime(2026, 5, 4, hour, 30, tzinfo=timezone.utc),
                ))
        async with self.scope() as session:
            hours = await _hours_utc(session, GUILD, USER)
        self.assertEqual(len(hours), 24)
        self.assertEqual(hours[0], 1)
        self.assertEqual(hours[3], 2)
        self.assertEqual(hours[21], 3)
        self.assertEqual(sum(hours), 6)
        # Every other hour is zero rather than absent, so the client can index straight in.
        self.assertEqual([h for i, h in enumerate(hours) if i not in (0, 3, 21)], [0] * 21)

    async def test_hours_are_scoped_and_empty_for_a_stranger(self):
        async with self.scope() as session:
            hours = await _hours_utc(session, GUILD, OTHER_USER)
        self.assertEqual(hours, [0] * 24)

    async def test_breakdowns_are_scoped_to_the_caller(self):
        await self._seed()
        async with self.scope() as session:
            b = await _breakdowns(session, GUILD, OTHER_USER)
        self.assertEqual(b["messages"], [])
        self.assertEqual(b["facts"], [])
        self.assertEqual(b["days"], [])


class ForgetCoverageTests(_DbCase):
    """The export tells a member what Olisar holds; forget_user is what erasing it means.
    They read one list, so this asserts the list is honoured end to end."""

    async def _seed_everything(self, user_id: int, guild_id: int) -> None:
        # message_id is globally unique in both tables, so it has to vary by guild too —
        # these fixtures deliberately seed one user across two servers.
        snowflake = guild_id * 1_000_000 + user_id
        async with self.scope() as session:
            session.add(Message(
                guild_id=guild_id, channel_id=1, message_id=snowflake,
                author_id=user_id, author_is_bot=False, content="hello",
            ))
            session.add(SearchMessage(
                guild_id=guild_id, channel_id=1, channel_name="general",
                message_id=snowflake, author_id=user_id, author_name="x", content="hello",
            ))
            session.add(UserMemory(user_id=user_id, guild_id=guild_id, content="likes tea"))
            session.add(Reminder(
                guild_id=guild_id, channel_id=1, user_id=user_id,
                content="stand up", scheduled_at=utcnow() + timedelta(days=1),
            ))
            session.add(UserProfile(
                user_id=user_id, guild_id=guild_id, display_name="x", avatar="",
                roles=[], notes={}, persona_summary="a person who likes tea",
            ))

    async def _count(self, session, model, column: str, user_id: int, guild_id: int) -> int:
        return int(await session.scalar(
            select(func.count()).select_from(model).where(
                model.guild_id == guild_id, getattr(model, column) == user_id
            )
        ) or 0)

    async def test_forget_empties_every_table_the_export_lists(self):
        await self._seed_everything(USER, GUILD)
        async with self.scope() as session:
            for model, column in MEMBER_DATA_TABLES:
                self.assertEqual(
                    await self._count(session, model, column, USER, GUILD), 1,
                    f"fixture didn't seed {model.__tablename__}",
                )
            with patch("olisar.memory.purge.delete_embedding", AsyncMock()):
                await forget_user(session, guild_ids=[GUILD], user_id=USER)
            for model, column in MEMBER_DATA_TABLES:
                with self.subTest(table=model.__tablename__):
                    self.assertEqual(
                        await self._count(session, model, column, USER, GUILD), 0,
                        f"{model.__tablename__} survived forget_user — the export promises "
                        "this table is erased",
                    )

    async def test_reminders_do_not_outlive_the_person_who_set_them(self):
        """The gap this closed: a forgotten member could still be DMed weeks later by a
        reminder they'd asked Olisar to erase."""
        await self._seed_everything(USER, GUILD)
        async with self.scope() as session:
            with patch("olisar.memory.purge.delete_embedding", AsyncMock()):
                result = await forget_user(session, guild_ids=[GUILD], user_id=USER)
        self.assertEqual(result["reminders"], 1)

    async def test_forgetting_one_member_leaves_everyone_else_intact(self):
        await self._seed_everything(USER, GUILD)
        await self._seed_everything(OTHER_USER, GUILD)
        async with self.scope() as session:
            with patch("olisar.memory.purge.delete_embedding", AsyncMock()):
                await forget_user(session, guild_ids=[GUILD], user_id=USER)
            for model, column in MEMBER_DATA_TABLES:
                with self.subTest(table=model.__tablename__):
                    self.assertEqual(
                        await self._count(session, model, column, OTHER_USER, GUILD), 1
                    )

    async def test_forgetting_in_one_server_leaves_the_others_alone(self):
        """UserProfile is per (user, guild), and so is erasure — a member clearing their
        data in one server must not clear it in another they're also in."""
        await self._seed_everything(USER, GUILD)
        await self._seed_everything(USER, OTHER_GUILD)
        async with self.scope() as session:
            with patch("olisar.memory.purge.delete_embedding", AsyncMock()):
                await forget_user(session, guild_ids=[GUILD], user_id=USER)
            for model, column in MEMBER_DATA_TABLES:
                with self.subTest(table=model.__tablename__):
                    self.assertEqual(
                        await self._count(session, model, column, USER, OTHER_GUILD), 1
                    )
            profile = await session.scalar(select(UserProfile).where(
                UserProfile.user_id == USER, UserProfile.guild_id == OTHER_GUILD
            ))
            self.assertEqual(profile.persona_summary, "a person who likes tea")


if __name__ == "__main__":
    unittest.main()
