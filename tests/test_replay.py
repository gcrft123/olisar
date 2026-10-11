"""The memory replay: what it reads, what it leaves out, how a run stops and carries on,
and the API the console drives it with.

Run:  uv run python -m unittest tests.test_replay -v

A seeded server ("Hollow Pines", started March 14, 2021) with the things a real one throws
at it: a member who opted out of search, one who left, a bot, a DM, a channel that isn't
indexed, a night when #general stayed up, a day a crowd arrived, and a persona renamed in
chat and back.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import httpx
from sqlalchemy import select

from api.auth import deps
from api.auth.sessions import COOKIE_NAME, create_session, sign_sid
from api.main import create_app
from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import (
    AdminGrant,
    AdminUser,
    AuditLog,
    GuildChannelInfo,
    Message,
    Persona,
    ReplayRun,
    SearchMessage,
    UserProfile,
    utcnow,
)
from olisar.gemini.quota import next_reset
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.guild_setup import ensure_guild_defaults
from olisar.replay import runner, store
from olisar.replay.scope import Person

EPOCH_MS = 1420070400000
T0 = datetime(2021, 3, 14, 21, 0, tzinfo=timezone.utc)   # the server starts


def sf(at: datetime, n: int = 0) -> int:
    return (int(at.timestamp() * 1000) - EPOCH_MS) << 22 | (n & 0x3FFFFF)


HOME = sf(T0)
OTHER = sf(T0 + timedelta(days=3))
ADMIN, OUTSIDER = 111, 222
BOT_ID = 900
GENERAL, GAMES, STAFF = sf(T0, 1), sf(T0 + timedelta(days=40), 2), sf(T0, 3)
THREAD = sf(T0 + timedelta(days=60), 4)

# People. juniper started it; wren posted early and has left; quiet opted out of search;
# helperbot is another bot.
JUNIPER, OKAPI, BRAM, TESS, WREN, QUIET, HELPERBOT, NIGHTJAR = 1, 2, 3, 4, 5, 6, 7, 8
NAMES = {JUNIPER: "juniper", OKAPI: "okapi", BRAM: "bram", TESS: "tess", WREN: "wren", NIGHTJAR: "nightjar"}
NIGHT = datetime(2023, 2, 4, 2, 0, tzinfo=timezone.utc)   # 9 p.m. the night of Feb 3 in UTC-5
BURST = datetime(2022, 7, 9, 18, 0, tzinfo=timezone.utc)
BOT_JOINED = datetime(2025, 8, 19, 20, 0, tzinfo=timezone.utc)


class FakeRoster:
    guild_name = "Hollow Pines"
    guild_icon = ""
    me_id = BOT_ID
    me_joined_at = BOT_JOINED
    me_avatar = "https://cdn.example/olisar.png"

    def __init__(self) -> None:
        self.members = {JUNIPER, OKAPI, BRAM, TESS, QUIET, NIGHTJAR, HELPERBOT, BOT_ID}
        joins = [(JUNIPER, T0), (OKAPI, T0 + timedelta(minutes=2)), (BRAM, T0 + timedelta(days=1)), (TESS, T0 + timedelta(days=3))]
        # Everyone else still here, arriving over the years, with a crowd on the burst day.
        extra = []
        for i in range(400):
            at = T0 + timedelta(days=10 + i * 4)
            extra.append((10_000 + i, at))
        for i in range(60):
            extra.append((20_000 + i, BURST + timedelta(minutes=i)))
        extra.append((NIGHTJAR, T0 + timedelta(days=200)))
        self.joins = joins + extra
        self.members |= {uid for uid, _ in extra}

    def present(self, user_id: int) -> bool:
        return int(user_id) in self.members

    def bot_ids(self) -> set[int]:
        return {HELPERBOT, BOT_ID}

    def person(self, user_id: int):
        if int(user_id) not in self.members:
            return None
        return Person(NAMES.get(int(user_id), f"member{user_id}"), f"https://cdn.example/{user_id}.png")

    def join_dates(self):
        return self.joins

    def humans(self) -> int:
        return len([u for u in self.members if u not in (HELPERBOT, BOT_ID)])

    def public(self, channel_id: int):
        return channel_id != STAFF


_seq = iter(range(1, 1 << 20))


def msg(channel: int, author: int, at: datetime, text: str, guild: int = HOME) -> SearchMessage:
    return SearchMessage(guild_id=guild, channel_id=channel, channel_name="", message_id=sf(at, next(_seq)),
                         author_id=author, author_name=NAMES.get(author, "x"), content=text, created_at=at)


def history() -> list:
    rows = [
        msg(GENERAL, WREN, T0 + timedelta(minutes=1), "first! wren was here"),
        msg(GENERAL, JUNIPER, T0 + timedelta(minutes=12), "ok it exists. who’s in"),
        msg(GENERAL, OKAPI, T0 + timedelta(minutes=14), "me. what is it <@1>"),
        msg(GENERAL, JUNIPER, T0 + timedelta(minutes=15), "no idea yet"),
        msg(GENERAL, QUIET, T0 + timedelta(minutes=3), "QUIET-SECRET opted out of search"),
        msg(GENERAL, HELPERBOT, T0 + timedelta(minutes=4), "HELPERBOT-NOISE beep"),
        msg(GENERAL, BRAM, T0 + timedelta(days=1), "juniper sent me. i was promised snacks"),
        msg(GENERAL, TESS, T0 + timedelta(days=3), "hi!! is this the book club or the other thing"),
        msg(STAFF, JUNIPER, T0 + timedelta(days=2), "STAFF-ONLY mod notes"),
        msg(0, OKAPI, T0 + timedelta(days=2), "DM-PRIVATE hello olisar", guild=0),
    ]
    # Steady chatter for two years, through the waking hours of UTC-5.
    people = [JUNIPER, OKAPI, BRAM, TESS, NIGHTJAR]
    for d in range(4, 730, 2):
        midnight = datetime(2021, 3, 15, 5, tzinfo=timezone.utc) + timedelta(days=d)   # 0:00 local
        for k, hour in enumerate((8, 9, 12, 15, 18, 20, 22, 23, 24, 25)):
            at = midnight + timedelta(hours=hour, minutes=7)
            rows.append(msg(GAMES if d % 6 == 0 and at > T0 + timedelta(days=40) else GENERAL,
                            people[(d + k) % 5], at, f"chatter {d} {k}"))
    # The night bram's power went out.
    for k in range(120):
        who = [BRAM, TESS, OKAPI, NIGHTJAR][k % 4]
        rows.append(msg(GENERAL, who, NIGHT + timedelta(minutes=k * 2), f"candle talk {k}"))
    rows.append(msg(THREAD, BRAM, T0 + timedelta(days=61), "a thread under general"))
    return rows


class _Case(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"OLISAR_HOME": tmp.name, "OLISAR_DATA_DIR": tmp.name, "OLISAR_NO_DOTENV": "1"})
        env.start()
        self.addCleanup(env.stop)
        engine.pin_database(os.path.join(tmp.name, "bot.db"))
        runtime_config.invalidate()
        runtime_keys.invalidate()
        await create_schema()
        deps._last_check.clear()
        tier = mock.patch("olisar.replay.preflight.gemini_tier.current", mock.AsyncMock(return_value="free"))
        tier.start()
        self.addCleanup(tier.stop)
        async with session_scope() as s:
            await ensure_guild_defaults(s, HOME, name="Hollow Pines")
            await ensure_guild_defaults(s, OTHER, name="Other")
        async with session_scope() as s:
            s.add_all([
                AdminUser(discord_user_id=ADMIN, username="juniper", is_allowlisted=False,
                          granted_via=AdminGrant.manage_guild, managed_guild_ids=[str(HOME)], last_login=utcnow()),
                AdminUser(discord_user_id=OUTSIDER, username="outsider", is_allowlisted=False,
                          granted_via=AdminGrant.manage_guild, managed_guild_ids=[str(OTHER)], last_login=utcnow()),
                GuildChannelInfo(channel_id=GENERAL, guild_id=HOME, name="general", backfill_done=True),
                GuildChannelInfo(channel_id=GAMES, guild_id=HOME, name="games", backfill_done=True),
                GuildChannelInfo(channel_id=STAFF, guild_id=HOME, name="staff", index_enabled=False, backfill_done=True),
                GuildChannelInfo(channel_id=THREAD, guild_id=HOME, name="a thread", kind="thread", parent_id=GENERAL, backfill_done=True),
                UserProfile(user_id=QUIET, guild_id=HOME, display_name="quiet", search_opt_out=True),
                UserProfile(user_id=OKAPI, guild_id=HOME, display_name="okapi"),
            ])
            s.add_all(history())
            # Olisar's first reply, to juniper.
            ask_at = BOT_JOINED + timedelta(hours=1)
            s.add(Message(guild_id=HOME, channel_id=GENERAL, message_id=sf(ask_at, 7), author_id=JUNIPER,
                          author_name="juniper", content="olisar what’s the wifi password at the cabin", created_at=ask_at))
            s.add(Message(guild_id=HOME, channel_id=GENERAL, message_id=sf(ask_at, 8), author_id=BOT_ID, author_is_bot=True,
                          author_name="", content="I don’t have that, and I’d be a little worried if I did.",
                          reply_to_message_id=sf(ask_at, 7), created_at=ask_at + timedelta(seconds=20), trigger="ask"))
            # Renamed Pinecone in chat, and back four days later.
            s.add(AuditLog(ts=datetime(2025, 10, 3, 18, tzinfo=timezone.utc), actor=str(JUNIPER), action="update_persona",
                           target_type="persona", target_id=str(HOME), before={"name": "Olisar"}, after={"name": "Pinecone", "via": "chat"}))
            s.add(AuditLog(ts=datetime(2025, 10, 7, 18, tzinfo=timezone.utc), actor=str(JUNIPER), action="update_persona",
                           target_type="persona", target_id=str(HOME), before={"name": "Pinecone"}, after={"name": "Olisar", "via": "chat"}))
            persona = await s.scalar(select(Persona).where(Persona.guild_id == HOME))
            persona.name = "Olisar"

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def new_run(self, **fields) -> int:
        async with session_scope() as s:
            run = ReplayRun(guild_id=HOME, status="queued", started_by=ADMIN, **fields)
            s.add(run)
            await s.flush()
            return run.id

    async def run_row(self, run_id: int) -> ReplayRun:
        async with session_scope() as s:
            return await s.get(ReplayRun, run_id)

    async def client(self, admin: int = ADMIN, guild: int = HOME):
        cookie = f"{COOKIE_NAME}={await sign_sid(await create_session(admin))}"
        app = create_app()
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
        c = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000",
                              headers={"cookie": cookie, "x-guild-id": str(guild)})
        self.addAsyncCleanup(c.aclose)
        return c


class StatsRunTests(_Case):
    async def test_a_run_builds_the_code_chapters(self) -> None:
        rid = await self.new_run()
        self.assertEqual(await runner.advance(rid, FakeRoster()), "done")
        run = await self.run_row(rid)
        r = run.report
        self.assertEqual(r["created"], "2021-03-14T21:00:00Z")
        self.assertEqual(r["guild"]["name"], "Hollow Pines")
        self.assertEqual(r["tz"], -5)
        # The first messages that can be shown: wren has left, so juniper's come first.
        first = r["first"]["messages"]
        self.assertEqual([m["text"] for m in first], ["ok it exists. who’s in", "me. what is it <@1>", "no idea yet"])
        self.assertEqual(r["first"]["channel"], "general")
        # The first five: wren keeps a place as Someone, with no words.
        founders = r["founders"]
        self.assertEqual(founders[0], {"who": None, "at": founders[0]["at"], "count": None, "msg": None})
        self.assertEqual([r["people"][f["who"]]["name"] for f in founders[1:]], ["juniper", "okapi", "bram", "tess"])
        # The busiest day is the night of February 3, in #general, at night there.
        night = r["night"]
        self.assertEqual((night["day"], night["channel"], night["night"]), ("2023-02-03", "general", True))
        self.assertGreaterEqual(night["count"], 120)
        # Arrivals: the 100th member, and the day 60 people came.
        kinds = {m["kind"]: m for m in r["arrivals"]["milestones"]}
        self.assertEqual(kinds["nth"]["n"], 100)
        self.assertEqual(kinds["burst"]["day"], "2022-07-09")
        self.assertGreaterEqual(kinds["burst"]["count"], 60)
        self.assertEqual(r["arrivals"]["now"], FakeRoster().humans() - 1)   # quiet has no join date
        # Olisar's arrival and first reply, with the question it answered.
        reply = r["arrive"]["reply"]
        self.assertEqual([m["who"] for m in reply], [str(JUNIPER), "bot"])
        self.assertEqual(reply[1]["trigger"], "ask")
        # Renamed Pinecone, and back.
        self.assertEqual([(n["from"], n["to"], n["via"]) for n in r["names"]],
                         [("Olisar", "Pinecone", "chat"), ("Pinecone", "Olisar", "chat")])
        self.assertEqual(run.progress["chapters"], 6 - 1)   # no pick yet

    async def test_what_a_replay_never_shows(self) -> None:
        rid = await self.new_run()
        await runner.advance(rid, FakeRoster())
        text = json.dumps((await self.run_row(rid)).report, ensure_ascii=False)
        for secret in ("QUIET-SECRET", "HELPERBOT-NOISE", "STAFF-ONLY", "DM-PRIVATE", "wren was here"):
            self.assertNotIn(secret, text)
        self.assertNotIn(str(WREN), json.loads(text)["people"])
        self.assertNotIn(str(QUIET), json.loads(text)["people"])

    async def test_a_spent_allowance_pauses_and_carries_on(self) -> None:
        rid = await self.new_run()
        with mock.patch("olisar.replay.moments.run", mock.AsyncMock(side_effect=RateLimitExceeded("m", "daily"))):
            self.assertEqual(await runner.advance(rid, FakeRoster()), "paused")
        run = await self.run_row(rid)
        self.assertEqual((run.status, run.step, run.pause_reason), ("paused", "moments", "quota"))
        self.assertEqual(store.aware(run.paused_until), next_reset())
        self.assertEqual(run.state["report"]["first"]["channel"], "general")   # kept
        async with session_scope() as s:
            self.assertEqual(await store.due_runs(s, utcnow()), [])
            self.assertEqual(await store.due_runs(s, next_reset() + timedelta(seconds=1)), [(rid, HOME)])
        counted = mock.AsyncMock(wraps=runner.step_count)
        with mock.patch.dict(runner.STEP_FN, {"count": counted}):
            self.assertEqual(await runner.advance(rid, FakeRoster()), "done")
        counted.assert_not_called()   # carried on from moments, not from the start

    async def test_a_failure_waits_for_try_again(self) -> None:
        rid = await self.new_run()
        with mock.patch("olisar.replay.pick.run", mock.AsyncMock(side_effect=ValueError("boom"))):
            self.assertEqual(await runner.advance(rid, FakeRoster()), "failed")
        run = await self.run_row(rid)
        self.assertEqual((run.status, run.step), ("failed", "pick"))
        self.assertIn("boom", run.error)
        async with session_scope() as s:
            self.assertEqual(await store.due_runs(s, utcnow()), [])
        c = await self.client()
        r = await c.post("/api/replay/retry")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["run"]["status"], "queued")
        self.assertEqual(await runner.advance(rid, FakeRoster()), "done")


class RedactionTests(_Case):
    async def test_someone_who_opts_out_later_leaves_the_report(self) -> None:
        rid = await self.new_run()
        await runner.advance(rid, FakeRoster())
        report = (await self.run_row(rid)).report
        out = store.present(report, {OKAPI})
        text = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("me. what is it", text)
        self.assertNotIn(str(OKAPI), out["people"])
        self.assertIsNone(out["founders"][2]["who"])
        self.assertIsNone(out["founders"][2]["msg"])
        # Mentions resolve to names the report knows, and to "someone" otherwise.
        kept = store.present(report, set())
        self.assertEqual(kept["first"]["messages"][1]["text"], "me. what is it @juniper")
        self.assertEqual(store.present(report, {JUNIPER})["first"]["messages"][0]["text"], "me. what is it @someone")

    async def test_forget_me_scrubs_finished_replays(self) -> None:
        from olisar.memory.purge import forget_user

        rid = await self.new_run()
        await runner.advance(rid, FakeRoster())
        async with session_scope() as s:
            await forget_user(s, guild_ids=[HOME], user_id=BRAM)
        text = json.dumps((await self.run_row(rid)).report, ensure_ascii=False)
        self.assertNotIn("promised snacks", text)
        self.assertNotIn(f'"{BRAM}"', text)


class ApiTests(_Case):
    async def test_the_start_sheet_and_one_run_at_a_time(self) -> None:
        c = await self.client()
        state = (await c.get("/api/replay")).json()
        self.assertEqual(state["preflight"]["state"], "ready")
        self.assertGreater(state["preflight"]["messages"], 1000)
        self.assertEqual(state["preflight"]["channels"], 3)
        self.assertEqual(state["preflight"]["cost"]["tier"], "free")
        self.assertIsNone(state["run"])
        r = await c.post("/api/replay/start", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["run"]["mine"])
        self.assertEqual((await c.post("/api/replay/start", json={})).status_code, 409)
        # Another admin of the same server sees it as someone else's, to watch.
        async with session_scope() as s:
            s.add(AdminUser(discord_user_id=333, username="tess", is_allowlisted=False, granted_via=AdminGrant.manage_guild,
                            managed_guild_ids=[str(HOME)], last_login=utcnow()))
        other = await self.client(333)
        run = (await other.get("/api/replay")).json()["run"]
        self.assertFalse(run["mine"])
        self.assertEqual(run["started_by"]["name"], "juniper")

    async def test_an_admin_of_another_server_cant_see_it(self) -> None:
        c = await self.client(OUTSIDER)
        self.assertEqual((await c.get("/api/replay")).status_code, 403)

    async def test_still_indexing_needs_start_anyway(self) -> None:
        async with session_scope() as s:
            row = await s.get(GuildChannelInfo, GAMES)
            row.backfill_done = False
            row.last_indexed_message_id = sf(T0 + timedelta(days=300))
        c = await self.client()
        pre = (await c.get("/api/replay")).json()["preflight"]
        self.assertEqual(pre["state"], "indexing")
        self.assertEqual(pre["indexing"]["channels"], 1)
        self.assertTrue(pre["indexing"]["before"].startswith("2022-01"))
        self.assertEqual((await c.post("/api/replay/start", json={})).status_code, 400)
        self.assertEqual((await c.post("/api/replay/start", json={"anyway": True})).status_code, 200)

    async def test_too_little_history(self) -> None:
        async with session_scope() as s:
            for row in (await s.scalars(select(SearchMessage).where(SearchMessage.guild_id == HOME))).all()[40:]:
                await s.delete(row)
        c = await self.client()
        self.assertEqual((await c.get("/api/replay")).json()["preflight"]["state"], "short")
        self.assertEqual((await c.post("/api/replay/start", json={})).status_code, 400)

    async def test_not_enough_allowance(self) -> None:
        with mock.patch("olisar.replay.preflight.left_today", mock.AsyncMock(return_value=(8, 5000))):
            c = await self.client()
            pre = (await c.get("/api/replay")).json()["preflight"]
        self.assertEqual(pre["state"], "quota")
        self.assertTrue(pre["cost"]["resets_at"])

    async def test_a_billed_key_gets_dollars_and_its_budget(self) -> None:
        budget = SimpleNamespace(usd=0.05, action="stop", gemini_images=True)
        with mock.patch("olisar.replay.preflight.gemini_tier.current", mock.AsyncMock(return_value="paid")), \
                mock.patch("olisar.replay.preflight.spend.budget", mock.AsyncMock(return_value=budget)):
            c = await self.client()
            pre = (await c.get("/api/replay")).json()["preflight"]
        self.assertEqual(pre["cost"]["tier"], "paid")
        self.assertGreater(pre["cost"]["usd"], 0)
        self.assertEqual(pre["state"], "budget")

    async def test_the_report_and_its_answer(self) -> None:
        c = await self.client()
        self.assertEqual((await c.get("/api/replay/report")).status_code, 404)
        rid = await self.new_run()
        await runner.advance(rid, FakeRoster())
        report = (await c.get("/api/replay/report")).json()
        self.assertEqual(report["run"], rid)
        self.assertEqual(report["first"]["messages"][1]["text"], "me. what is it @juniper")
        r = await c.post("/api/replay/answer", json={"text": "  the   lighthouse  "})
        self.assertEqual(r.json()["answer"], "the lighthouse")
        self.assertEqual((await c.get("/api/replay")).json()["latest"]["answer"], "the lighthouse")
        # Next year's replay keeps it.
        rid2 = await self.new_run()
        await runner.advance(rid2, FakeRoster())
        self.assertEqual((await self.run_row(rid2)).report["last_answer"], "the lighthouse")


if __name__ == "__main__":
    unittest.main()
