"""Google's daily limits: which day it is, what a 429 says, and what the Usage page reads.

Run:  uv run python -m unittest tests.test_usage_quota -v

Google resets requests-per-day at midnight Pacific, and Olisar counted days in UTC, so for
seven or eight hours every evening it started a fresh day while Google was still counting the
old one. A model Google had refused for the day was parked for two minutes and asked again
all evening. Covered here: the day boundary, telling a spent day from a per-minute throttle,
parking until the reset (and surviving a restart), and the two endpoints the page polls.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import errors as genai_errors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import usage as usage_router
from olisar.db.models import (
    AdminUser,
    Base,
    GeminiUsage,
    Guild,
    GuildConfig,
    UsageDay,
    UsageHour,
    UsageSource,
)
from olisar.gemini import rate_limiter as rl
from olisar.gemini.client import GeminiClient
from olisar.gemini.models import RANKED, RANKED_NAMES, model_chain
from olisar.gemini.quota import day_start, next_reset, quota_day, quota_hour, read_refusal
from olisar.gemini.rate_limiter import RateLimiter, RateLimitExceeded, key_id


def _quota_429(quota_id: str, value: str = "250", retry: str | None = None):
    details = [{
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "quotaId": quota_id,
            "quotaDimensions": {"location": "global", "model": "gemini-3.5-flash"},
            "quotaValue": value,
        }],
    }]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    body = {"error": {
        "code": 429, "message": "You exceeded your current quota.",
        "status": "RESOURCE_EXHAUSTED", "details": details,
    }}
    return genai_errors.APIError(429, body)


DAILY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
PER_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


class QuotaDayTests(unittest.TestCase):
    def test_a_utc_evening_is_still_googles_day(self):
        # 03:00 UTC on the 27th is 8 PM Pacific on the 26th (PDT).
        now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(quota_day(now), date(2026, 9, 26))
        self.assertEqual(quota_hour(now), 20)

    def test_the_reset_is_midnight_pacific(self):
        now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(next_reset(now), datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc))

    def test_the_reset_follows_standard_time_in_winter(self):
        self.assertEqual(day_start(date(2026, 12, 1)), datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc))


class RefusalTests(unittest.TestCase):
    def test_a_daily_quota_is_daily_and_names_its_limit(self):
        refusal = read_refusal(_quota_429(DAILY, "250"))
        self.assertTrue(refusal.daily)
        self.assertEqual(refusal.limit, 250)

    def test_a_daily_token_quota_is_daily_but_names_no_request_limit(self):
        """1,000,000 input tokens a day isn't a million requests a day."""
        err = _quota_429("GenerateContentInputTokensPerModelPerDay-FreeTier", "1000000")
        refusal = read_refusal(err)
        self.assertTrue(refusal.daily)
        self.assertIsNone(refusal.limit)

    def test_the_request_quota_wins_when_both_ran_out(self):
        body = {"error": {"code": 429, "details": [{
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [
                {"quotaId": "GenerateContentInputTokensPerModelPerDay-FreeTier", "quotaValue": "1000000"},
                {"quotaId": DAILY, "quotaValue": "250"},
            ],
        }]}}
        self.assertEqual(read_refusal(genai_errors.APIError(429, body)).limit, 250)

    def test_a_per_minute_quota_is_not_daily(self):
        refusal = read_refusal(_quota_429(PER_MINUTE, "10", retry="23s"))
        self.assertFalse(refusal.daily)
        self.assertIsNone(refusal.limit)

    def test_a_bare_429_is_not_read_as_daily(self):
        """Parking a model until midnight over a throttle costs hours; the reverse, minutes."""
        err = genai_errors.APIError(429, {"error": {"message": "Too many requests."}})
        self.assertFalse(read_refusal(err).daily)

    def test_the_quota_id_in_the_words_alone_still_counts(self):
        err = genai_errors.APIError(429, {"error": {"message": f"Quota exceeded: {DAILY}"}})
        self.assertTrue(read_refusal(err).daily)


class LimiterTests(unittest.TestCase):
    def test_a_spent_model_is_skipped_until_the_reset(self):
        limiter = RateLimiter()
        limiter.exhaust(RANKED_NAMES[0])
        self.assertEqual(limiter.state(RANKED_NAMES[0]), "spent")
        self.assertEqual(limiter.state(RANKED_NAMES[1]), "ok")

    def test_the_reset_clears_it(self):
        limiter = RateLimiter()
        limiter.exhaust(RANKED_NAMES[0], datetime.now(timezone.utc) - timedelta(days=1))
        self.assertIsNone(limiter.spent_at(RANKED_NAMES[0]))
        self.assertEqual(limiter.state(RANKED_NAMES[0]), "ok")

    def test_the_whole_chain_spent(self):
        limiter = RateLimiter()
        for name in RANKED_NAMES[:-1]:
            limiter.exhaust(name)
        self.assertFalse(limiter.chain_spent())
        limiter.exhaust(RANKED_NAMES[-1])
        self.assertTrue(limiter.chain_spent())
        self.assertTrue(limiter.chat_exhausted())

    def test_acquire_refuses_rather_than_waiting_out_the_day(self):
        limiter = RateLimiter()
        limiter.exhaust("gemini-embedding-001")
        with self.assertRaises(RateLimitExceeded):
            asyncio.run(asyncio.wait_for(limiter.acquire("gemini-embedding-001"), 1))

    def test_back_in_counts_down_a_cooldown(self):
        limiter = RateLimiter()
        limiter.penalize(RANKED_NAMES[0], seconds=48)
        self.assertAlmostEqual(limiter.back_in(RANKED_NAMES[0]), 48, delta=1)
        self.assertEqual(limiter.back_in(RANKED_NAMES[1]), 0)

    def test_a_new_key_unparks_what_the_old_one_ran_out_on(self):
        """The quota is the key's project's; another key's refusal says nothing about it."""
        limiter = RateLimiter()
        limiter.use_key(key_id("KEY-A"))
        limiter.exhaust(RANKED_NAMES[0])
        self.assertEqual(limiter.state(RANKED_NAMES[0]), "spent")
        limiter.use_key(key_id("KEY-B"))
        self.assertEqual(limiter.state(RANKED_NAMES[0]), "ok")
        self.assertFalse(limiter.chain_spent())

    @staticmethod
    def _an_hour_passes(limiter, model):
        limiter._probe_at[model] -= rl.PROBE_SECONDS

    def test_a_parked_model_is_asked_again_about_once_an_hour(self):
        """So billing turned on in the afternoon is noticed before midnight."""
        limiter = RateLimiter()
        limiter.exhaust(RANKED_NAMES[0])
        self.assertFalse(limiter.claim_probe(RANKED_NAMES[0]))
        self._an_hour_passes(limiter, RANKED_NAMES[0])
        self.assertTrue(limiter.claim_probe(RANKED_NAMES[0]))
        self.assertFalse(limiter.claim_probe(RANKED_NAMES[0]), "one probe per hour, not one per reply")
        self.assertEqual(limiter.state(RANKED_NAMES[0]), "spent")
        self.assertFalse(limiter.claim_probe(RANKED_NAMES[1]), "only parked models are probed")

    def test_the_hour_counts_from_the_refusal_not_the_restart(self):
        limiter = RateLimiter()
        now = datetime.now(timezone.utc)
        ago = min(now - day_start(quota_day(now)), timedelta(minutes=30))  # still today
        limiter.exhaust(RANKED_NAMES[0], now - ago)
        due_in = limiter._probe_at[RANKED_NAMES[0]] - rl.time.monotonic()
        self.assertAlmostEqual(due_in, rl.PROBE_SECONDS - ago.total_seconds(), delta=2)

    def test_refused_again_keeps_when_it_first_ran_out(self):
        limiter = RateLimiter()
        limiter.exhaust(RANKED_NAMES[0])
        first = limiter.spent_at(RANKED_NAMES[0])
        self._an_hour_passes(limiter, RANKED_NAMES[0])
        self.assertTrue(limiter.claim_probe(RANKED_NAMES[0]))
        limiter.exhaust(RANKED_NAMES[0], first + timedelta(seconds=1))
        self.assertEqual(limiter.spent_at(RANKED_NAMES[0]), first)
        self.assertFalse(limiter.claim_probe(RANKED_NAMES[0]), "the refusal pushes the next probe back")

    def test_acquire_takes_the_hourly_probe(self):
        limiter = RateLimiter()
        limiter.exhaust("gemini-embedding-001")
        self._an_hour_passes(limiter, "gemini-embedding-001")
        asyncio.run(asyncio.wait_for(limiter.acquire("gemini-embedding-001"), 1))
        with self.assertRaises(RateLimitExceeded):
            asyncio.run(asyncio.wait_for(limiter.acquire("gemini-embedding-001"), 1))


class ClientTests(unittest.TestCase):
    def _run(self, first_error, *, grounding=0):
        client = GeminiClient()
        ok = MagicMock()
        ok.usage_metadata.total_token_count = 5
        sdk = MagicMock()
        sdk.aio.models.generate_content = AsyncMock(side_effect=[first_error, ok])
        client.aclient = AsyncMock(return_value=sdk)
        limiter = MagicMock()
        limiter.state.return_value = "ok"
        spent = AsyncMock()
        with patch("olisar.gemini.client.get_rate_limiter", return_value=limiter), patch(
            "olisar.gemini.client.record_usage", new=AsyncMock()
        ), patch("olisar.gemini.client.mark_spent", new=spent):
            asyncio.run(client._raw_generate(
                contents="hi", config=MagicMock(), model=RANKED_NAMES[0], grounding=grounding,
            ))
        return limiter, spent

    def test_a_daily_429_parks_the_model_until_the_reset(self):
        limiter, spent = self._run(_quota_429(DAILY, "250"))
        spent.assert_awaited_once_with(RANKED_NAMES[0], 250, key=None)
        limiter.penalize.assert_not_called()

    def test_a_per_minute_429_rests_it_as_before(self):
        limiter, spent = self._run(_quota_429(PER_MINUTE, "10"))
        spent.assert_not_awaited()
        limiter.penalize.assert_called_once()

    def test_a_grounded_call_never_parks_the_model_for_the_day(self):
        """Its daily refusal can be the search allowance's, not the model's."""
        limiter, spent = self._run(_quota_429(DAILY, "500"), grounding=1)
        spent.assert_not_awaited()
        limiter.penalize.assert_called_once()


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.limiter = RateLimiter()
        # The key in effect, as the dashboard or .env would give it. The limiter learns it
        # from the client's first call.
        self.key = {"value": "KEY-A"}
        self.limiter.use_key(key_id(self.key["value"]))
        self._patches = [
            patch.object(rl, "session_scope", self.scope),
            patch.object(usage_router, "session_scope", self.scope),
            patch.object(rl, "_rate_limiter", self.limiter),
            patch.object(
                rl.runtime_keys, "gemini_api_key",
                AsyncMock(side_effect=lambda: self.key["value"]),
            ),
        ]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self) -> None:
        for p in self._patches:
            p.stop()
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def rows(self, model):
        async with self.Session() as session:
            return (await session.scalars(select(model))).all()


class RecordingTests(_Db):
    async def test_a_request_is_counted_on_googles_day_and_hour(self):
        await rl.record_usage(RANKED_NAMES[0], 120, source="conversation")
        (row,) = await self.rows(GeminiUsage)
        self.assertEqual(row.day, quota_day())
        (hour,) = await self.rows(UsageHour)
        self.assertEqual((hour.day, hour.hour, hour.request_count, hour.token_count),
                         (quota_day(), quota_hour(), 1, 120))

    async def test_a_refusal_is_kept_with_the_limit_google_named(self):
        await rl.mark_spent(RANKED_NAMES[0], 250)
        (row,) = await self.rows(GeminiUsage)
        self.assertEqual((row.request_count, row.quota_limit), (0, 250))
        self.assertIsNotNone(row.exhausted_at)
        self.assertEqual(await self.rows(UsageDay), [])

    async def test_the_last_model_out_marks_the_day(self):
        for name in RANKED_NAMES:
            await rl.mark_spent(name)
        (marker,) = await self.rows(UsageDay)
        self.assertIsNotNone(marker.chain_out_at)

    async def test_a_restart_parks_what_google_already_refused(self):
        await rl.mark_spent(RANKED_NAMES[0])
        fresh = RateLimiter()
        with patch.object(rl, "_rate_limiter", fresh):
            self.assertEqual(await rl.restore_spent(), 1)
        self.assertEqual(fresh.state(RANKED_NAMES[0]), "spent")

    async def test_a_refusal_keeps_a_fingerprint_of_the_key_never_the_key(self):
        await rl.mark_spent(RANKED_NAMES[0])
        (row,) = await self.rows(GeminiUsage)
        self.assertEqual(row.exhausted_key, key_id("KEY-A"))
        self.assertNotIn("KEY-A", row.exhausted_key)

    async def test_a_restart_on_a_new_key_parks_nothing(self):
        for name in RANKED_NAMES:
            await rl.mark_spent(name)
        self.key["value"] = "KEY-B"
        fresh = RateLimiter()
        with patch.object(rl, "_rate_limiter", fresh):
            self.assertEqual(await rl.restore_spent(), 0)
        self.assertEqual(fresh.state(RANKED_NAMES[0]), "ok")

    async def test_a_refusal_recorded_before_keys_were_kept_isnt_restored(self):
        async with self.scope() as session:
            session.add(GeminiUsage(
                day=quota_day(), model=RANKED_NAMES[0], exhausted_at=datetime.now(timezone.utc),
            ))
        fresh = RateLimiter()
        with patch.object(rl, "_rate_limiter", fresh):
            self.assertEqual(await rl.restore_spent(), 0)

    async def test_a_request_google_takes_clears_the_refusal(self):
        """Billing turned on: the hourly retry went through, so it's no longer out."""
        await rl.mark_spent(RANKED_NAMES[0], 250)
        await rl.record_usage(RANKED_NAMES[0], 10)
        self.assertEqual(self.limiter.state(RANKED_NAMES[0]), "ok")
        (row,) = await self.rows(GeminiUsage)
        self.assertIsNone(row.exhausted_at)
        self.assertEqual(row.quota_limit, 250)
        fresh = RateLimiter()
        with patch.object(rl, "_rate_limiter", fresh):
            self.assertEqual(await rl.restore_spent(), 0)

    async def test_a_refusal_for_a_replaced_key_parks_nothing(self):
        await rl.mark_spent(RANKED_NAMES[0], key=key_id("OLD-KEY"))
        self.assertEqual(self.limiter.state(RANKED_NAMES[0]), "ok")


class ServerChainTests(_Db):
    """A server replies through ``model_chain(default_model)``, not the whole ranking."""

    LOWER = "gemini-2.5-flash"

    async def _servers(self, *defaults: str | None) -> None:
        async with self.scope() as session:
            for gid, default in enumerate(defaults, start=1):
                session.add(Guild(id=gid))
                if default is not None:
                    session.add(GuildConfig(guild_id=gid, default_model=default))
            session.add(Guild(id=99, active=False))
            session.add(GuildConfig(guild_id=99, default_model=RANKED_NAMES[-1]))

    async def _live(self, guild: str | None = None, admin=None):
        with patch.object(usage_router, "get_rate_limiter", return_value=self.limiter):
            return await usage_router.live(admin, guild)

    async def test_the_page_shows_the_selected_servers_chain(self):
        await self._servers(self.LOWER)
        chain = model_chain(self.LOWER)
        for name in chain:
            await rl.mark_spent(name)
        data = await self._live("1")
        self.assertEqual([m["model"] for m in data["chain"]], chain)
        self.assertEqual({m["state"] for m in data["chain"]}, {"spent"})
        self.assertTrue(data["exhausted"])
        (marker,) = await self.rows(UsageDay)
        self.assertIsNotNone(marker.chain_out_at)
        summary = await usage_router.summary(None)
        self.assertIsNotNone(summary["last_ran_out"])

    async def test_without_a_selection_it_is_every_chain_in_use(self):
        await self._servers(self.LOWER)
        data = await self._live()
        self.assertEqual([m["model"] for m in data["chain"]], model_chain(self.LOWER))

    async def test_rate_limited_only_when_no_server_can_be_answered(self):
        await self._servers(None, self.LOWER)  # server 1 on the default head
        for name in model_chain(self.LOWER):
            await rl.mark_spent(name)
        data = await self._live("2")
        self.assertEqual({m["state"] for m in data["chain"]}, {"spent"})
        self.assertFalse(data["exhausted"], "server 1 still replies through the models above")
        self.assertEqual(await self.rows(UsageDay), [])
        for name in RANKED_NAMES:
            await rl.mark_spent(name)
        self.assertTrue((await self._live("2"))["exhausted"])
        self.assertEqual(len(await self.rows(UsageDay)), 1)

    async def test_a_server_the_admin_doesnt_manage_isnt_shown(self):
        await self._servers(None, self.LOWER)
        admin = AdminUser(discord_user_id=5, is_allowlisted=False, managed_guild_ids=["1"])
        data = await self._live("2", admin)
        self.assertEqual([m["model"] for m in data["chain"]], RANKED_NAMES)
        data = await self._live("99")  # the bot left: not a chain in use
        self.assertEqual([m["model"] for m in data["chain"]], RANKED_NAMES)


class WipeTests(_Db):
    async def test_a_servers_wipe_keeps_the_installs_usage(self):
        """Usage belongs to the whole install, and the daily web-search cap is counted from
        it, so clearing one server's memory (Manage Server there is enough) leaves it."""
        from sqlalchemy import text

        from olisar.db.models import UsageMinutePeak
        from olisar.memory import purge

        async with self.scope() as session:
            for table, _ in purge._BRAIN_EMBEDDINGS:  # vec0 in the app; stand-ins here
                await session.execute(text(f"CREATE TABLE {table} (x)"))
            session.add(Guild(id=1))
        await rl.record_usage(RANKED_NAMES[0], 10, source="conversation")
        for name in RANKED_NAMES:
            await rl.mark_spent(name)
        tables = (GeminiUsage, UsageHour, UsageDay, UsageSource, UsageMinutePeak)
        before = {table: len(await self.rows(table)) for table in tables}
        for table in tables:
            self.assertTrue(before[table], table.__name__)
        async with self.scope() as session:
            await purge.wipe_brain(session, guild_ids=[1])
        for table in tables:
            self.assertEqual(len(await self.rows(table)), before[table], table.__name__)


class KeySwapTests(_Db):
    """The whole path: Google refuses key A for the day, the operator pastes key B."""

    async def _ask(self, client):
        from google.genai import types

        return await client._raw_generate(
            contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
            config=types.GenerateContentConfig(), model=RANKED_NAMES[0],
        )

    async def test_a_new_key_is_tried_at_once(self):
        from olisar.gemini import client as client_mod

        sent: list[tuple[str, str]] = []

        async def generate(*, model, contents, config):
            sent.append((self.key["value"], model))
            if self.key["value"] == "KEY-A":
                raise _quota_429(DAILY, "20")
            ok = MagicMock()
            ok.usage_metadata.total_token_count = 5
            return ok

        sdk = MagicMock()
        sdk.aio.models.generate_content = AsyncMock(side_effect=generate)
        with patch.object(client_mod.genai, "Client", return_value=sdk), patch.object(
            client_mod.runtime_keys, "gemini_api_key", AsyncMock(side_effect=lambda: self.key["value"]),
        ):
            client = GeminiClient()
            with self.assertRaises(genai_errors.APIError):
                await self._ask(client)
            self.assertTrue(self.limiter.chain_spent())
            self.assertEqual(len(sent), len(RANKED_NAMES))

            self.key["value"] = "KEY-B"
            sent.clear()
            await self._ask(client)
            self.assertEqual(sent, [("KEY-B", RANKED_NAMES[0])])

            # And a restart on key B doesn't park anything key A ran out of.
            fresh = RateLimiter()
            with patch.object(rl, "_rate_limiter", fresh):
                self.assertEqual(await rl.restore_spent(), 0)

    async def test_the_usage_page_follows_a_key_pasted_into_the_dashboard(self):
        for name in RANKED_NAMES:
            await rl.mark_spent(name)
        self.key["value"] = "KEY-B"
        with patch.object(usage_router, "get_rate_limiter", return_value=self.limiter):
            data = await usage_router.live(None)
        self.assertEqual({m["state"] for m in data["chain"]}, {"ok"})
        self.assertFalse(data["exhausted"])


class EndpointTests(_Db):
    async def _seed(self, model, requests, **extra):
        async with self.scope() as session:
            session.add(GeminiUsage(
                day=extra.pop("day", quota_day()), model=model, request_count=requests,
                token_count=requests * 10, grounding_count=extra.pop("grounding", 0),
                peak_rpm=extra.pop("peak_rpm", 0), **extra,
            ))

    async def _live(self):
        with patch.object(usage_router, "get_rate_limiter", return_value=self.limiter):
            return await usage_router.live(None)

    async def test_live_lists_every_chain_model_in_order(self):
        await self._seed(RANKED_NAMES[2], 40)
        data = await self._live()
        self.assertEqual([m["model"] for m in data["chain"]], RANKED_NAMES)
        third = data["chain"][2]
        self.assertEqual((third["requests"], third["limit"], third["state"]), (40, RANKED[2].rpd, "ok"))

    async def test_live_uses_googles_limit_once_it_named_one(self):
        await self._seed(RANKED_NAMES[0], 20, day=quota_day() - timedelta(days=3), quota_limit=20)
        data = await self._live()
        self.assertEqual(data["chain"][0]["limit"], 20)
        self.assertTrue(data["chain"][0]["limit_from_google"])

    async def test_live_reports_spent_and_resting_models(self):
        await rl.mark_spent(RANKED_NAMES[0], 250)
        self.limiter.penalize(RANKED_NAMES[1], seconds=48)
        data = await self._live()
        states = [m["state"] for m in data["chain"][:3]]
        self.assertEqual(states, ["spent", "resting", "ok"])
        self.assertIsNotNone(data["chain"][0]["spent_at"])
        self.assertLessEqual(data["chain"][1]["back_in"], 48)
        self.assertEqual(data["reset_at"], next_reset().isoformat())

    async def test_memory_search_and_web_search_have_their_own_limits(self):
        await self._seed("gemini-embedding-001", 12)
        await self._seed(RANKED_NAMES[0], 30, grounding=4)
        data = await self._live()
        self.assertEqual(data["memory_search"]["requests"], 12)
        self.assertEqual(data["web_search"]["requests"], 4)

    async def test_summary_leaves_memory_search_out_of_the_chain(self):
        async with self.scope() as session:
            session.add(UsageSource(day=quota_day(), source="conversation", request_count=9))
            session.add(UsageSource(day=quota_day(), source="embed", request_count=50))
            session.add(UsageSource(day=quota_day() - timedelta(days=5), source="summary", request_count=3))
        await self._seed(RANKED_NAMES[0], 9, peak_rpm=8)
        await self._seed("gemini-embedding-001", 50, peak_rpm=40)
        data = await usage_router.summary(None)
        self.assertEqual(data["features"]["today"], {"conversation": 9})
        self.assertEqual(data["features"]["7"], {"conversation": 9, "summary": 3})
        self.assertEqual(len(data["days"]), usage_router.DAYS_SHOWN)
        self.assertEqual(data["days"][-1]["requests"], 9)
        self.assertEqual(data["busiest_minute"]["model"], RANKED_NAMES[0])
        self.assertIsNone(data["yesterday"])

    async def test_summary_compares_with_the_same_time_yesterday(self):
        yesterday = quota_day() - timedelta(days=1)
        async with self.scope() as session:
            for hour in range(24):
                session.add(UsageHour(day=yesterday, hour=hour, model=RANKED_NAMES[0],
                                      request_count=60, token_count=600))
        data = await usage_router.summary(None)
        # Every full hour so far, plus the share of this one.
        self.assertGreaterEqual(data["yesterday"]["requests"], quota_hour() * 60)
        self.assertLessEqual(data["yesterday"]["requests"], (quota_hour() + 1) * 60)

    async def _same_time_yesterday(self, now: datetime) -> tuple[float, int]:
        """A flat request a minute all yesterday, bucketed the way record_usage does.
        Returns (minutes today so far, what the page compares them with)."""
        today = quota_day(now)
        yesterday = today - timedelta(days=1)
        counts: dict[int, int] = {}
        t = day_start(yesterday)
        while t < day_start(today):
            counts[quota_hour(t)] = counts.get(quota_hour(t), 0) + 1
            t += timedelta(minutes=1)
        async with self.scope() as session:
            for hour, n in counts.items():
                session.add(UsageHour(day=yesterday, hour=hour, model=RANKED_NAMES[0],
                                      request_count=n, token_count=0))

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with patch.object(usage_router, "datetime", Frozen):
            data = await usage_router.summary(None)
        return (now - day_start(today)).total_seconds() / 60, data["yesterday"]["requests"]

    async def test_same_time_yesterday_across_the_clocks_going_back(self):
        """Nov 1 has two 1 AMs; the day after compares against the 25-hour day."""
        for now in (
            datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc),   # Nov 1, the second 1:30 AM
            datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc),   # Nov 2, 1:30 AM
            datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc),   # Nov 2, noon
        ):
            with self.subTest(now=now):
                async with self.scope() as session:
                    await session.execute(UsageHour.__table__.delete())
                so_far, yesterday = await self._same_time_yesterday(now)
                self.assertEqual(yesterday, round(so_far))

    async def test_same_time_yesterday_across_the_clocks_going_forward(self):
        """Mar 14 has no 2 AM; the day after compares against the 23-hour day."""
        for now in (
            datetime(2027, 3, 14, 10, 30, tzinfo=timezone.utc),  # Mar 14, 3:30 AM
            datetime(2027, 3, 15, 10, 30, tzinfo=timezone.utc),  # Mar 15, 3:30 AM
            datetime(2027, 3, 15, 19, 0, tzinfo=timezone.utc),   # Mar 15, noon
        ):
            with self.subTest(now=now):
                async with self.scope() as session:
                    await session.execute(UsageHour.__table__.delete())
                so_far, yesterday = await self._same_time_yesterday(now)
                self.assertEqual(yesterday, round(so_far))


if __name__ == "__main__":
    unittest.main()
