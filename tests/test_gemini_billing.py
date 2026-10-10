"""A Gemini key with billing on: which tier a key is on, what usage costs, and the budget.

Run:  uv run python -m unittest tests.test_gemini_billing -v

Olisar used to assume every key was on the free tier: its per-minute caps, its web search,
its images and its Usage page all measured a billed key against free-tier figures. Covered
here: telling the tiers apart, pricing a day's usage (searches' free allowances included),
the running total the budget reads, what a spent budget does to a request, and each server's
own search cap.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import GuildConfig
from olisar.gemini import pricing, spend, tier
from olisar.gemini.client import GeminiClient, token_split
from olisar.gemini.models import GEMINI_IMAGE_MODEL, rpm_for
from olisar.gemini.rate_limiter import BudgetSpent, RateLimiter, key_id

DAY = date(2026, 10, 9)


def _row(model, *, day=DAY, requests=1, tokens=0, inp=0, out=0, grounding=0):
    return SimpleNamespace(
        day=day, model=model, request_count=requests, token_count=tokens,
        input_tokens=inp, output_tokens=out, grounding_count=grounding,
    )


class PricingTests(unittest.TestCase):
    def test_input_and_output_are_priced_apart(self):
        # 3.1 Flash-Lite: $0.25 in, $1.50 out, per million.
        row = _row("gemini-3.1-flash-lite", tokens=3_000_000, inp=2_000_000, out=1_000_000)
        self.assertAlmostEqual(pricing.tokens_cost(row), 0.5 + 1.5)

    def test_tokens_from_before_the_split_are_priced_as_input(self):
        row = _row("gemini-2.5-flash-lite", tokens=1_000_000)
        self.assertAlmostEqual(pricing.tokens_cost(row), 0.10)

    def test_an_image_is_priced_per_image(self):
        row = _row(GEMINI_IMAGE_MODEL, requests=10)
        self.assertAlmostEqual(pricing.tokens_cost(row), 0.336)

    def test_flash_doubles_in_2027(self):
        self.assertEqual(pricing.price_for("gemini-3.5-flash", date(2026, 12, 31)).input, 0.75)
        self.assertEqual(pricing.price_for("gemini-3.5-flash", date(2027, 1, 1)).input, 1.50)

    def test_an_unknown_model_is_never_free(self):
        self.assertGreater(pricing.price_for("gemini-9-flash", DAY).input, 0)

    def test_gemini_3_searches_are_free_up_to_the_months_allowance(self):
        rows = [
            _row("gemini-3.5-flash", day=date(2026, 10, 1), grounding=4999),
            _row("gemini-3.5-flash", day=date(2026, 10, 2), grounding=11),
        ]
        costs = pricing.daily_costs(rows)
        self.assertAlmostEqual(costs[date(2026, 10, 1)], 0)
        self.assertAlmostEqual(costs[date(2026, 10, 2)], 10 * 0.014)

    def test_the_allowance_starts_again_each_month(self):
        rows = [
            _row("gemini-3.5-flash", day=date(2026, 9, 30), grounding=5000),
            _row("gemini-3.5-flash", day=date(2026, 10, 1), grounding=5000),
        ]
        self.assertAlmostEqual(sum(pricing.daily_costs(rows).values()), 0)

    def test_gemini_25_searches_have_a_daily_allowance(self):
        rows = [
            _row("gemini-2.5-flash", grounding=1000),
            _row("gemini-2.5-flash-lite", grounding=600),
        ]
        self.assertAlmostEqual(pricing.daily_costs(rows)[DAY], 100 * 0.035)


class TokenSplitTests(unittest.TestCase):
    def test_thinking_and_search_results_land_where_google_bills_them(self):
        usage = SimpleNamespace(
            total_token_count=640, prompt_token_count=12, tool_use_prompt_token_count=92,
            candidates_token_count=56, thoughts_token_count=480,
        )
        self.assertEqual(token_split(SimpleNamespace(usage_metadata=usage)), (640, 104, 536))

    def test_missing_counts_are_zero(self):
        usage = SimpleNamespace(total_token_count=None, prompt_token_count=None)
        self.assertEqual(token_split(SimpleNamespace(usage_metadata=usage)), (0, 0, 0))


class SpendTests(unittest.TestCase):
    def setUp(self):
        spend._month = None

    def tearDown(self):
        spend._month = None

    def test_the_running_total_agrees_with_the_rollup(self):
        rows = [
            _row("gemini-3.5-flash", tokens=5000, inp=4500, out=500),
            _row("gemini-2.5-flash", grounding=1600, requests=1600),
            _row("gemini-3.5-flash", grounding=5001, requests=5001),
            _row(GEMINI_IMAGE_MODEL, requests=1),
        ]
        # The same requests, one at a time.
        spend.add("gemini-3.5-flash", DAY, input_tokens=4500, output_tokens=500)
        for _ in range(1600):
            spend.add("gemini-2.5-flash", DAY, grounding=1)
        for _ in range(5001):
            spend.add("gemini-3.5-flash", DAY, grounding=1)
        spend.add(GEMINI_IMAGE_MODEL, DAY, images=1)
        self.assertAlmostEqual(spend.month_usd(DAY), sum(pricing.daily_costs(rows).values()))

    def test_a_new_month_starts_from_zero(self):
        spend.add("gemini-3.5-flash", date(2026, 9, 30), input_tokens=1_000_000)
        self.assertEqual(spend.month_usd(date(2026, 10, 1)), 0.0)


def _transport(*responses):
    """An httpx client factory answering each request with the next (status, body)."""
    queue = list(responses)

    def handler(request):
        status, body = queue.pop(0)
        return httpx.Response(status, text=body)

    real = httpx.AsyncClient
    return lambda **kw: real(transport=httpx.MockTransport(handler), **kw)


FREE_429 = '{"error": {"code": 429, "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "0"}]}]}}'


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, *responses):
        with patch.object(tier.httpx, "AsyncClient", _transport(*responses)):
            return await tier.probe("AQ.key")

    async def test_a_paid_only_model_answering_means_billing(self):
        self.assertEqual(await self.probe((200, "{}")), tier.PAID)

    async def test_a_free_tier_quota_means_free(self):
        self.assertEqual(await self.probe((429, FREE_429)), tier.FREE)

    async def test_a_billed_key_being_throttled_is_still_billed(self):
        self.assertEqual(await self.probe((429, '{"error": {"code": 429}}')), tier.PAID)

    async def test_a_retired_probe_model_moves_on_to_the_next(self):
        self.assertEqual(await self.probe((404, "gone"), (429, FREE_429)), tier.FREE)

    async def test_a_key_google_rejects_says_nothing(self):
        self.assertIsNone(await self.probe((400, "API key not valid"), (400, "API key not valid")))

    async def test_an_outage_says_nothing(self):
        self.assertIsNone(await self.probe((503, "overloaded")))


class TierStateTests(unittest.TestCase):
    def setUp(self):
        self.limiter = RateLimiter()
        self.kid = key_id("AQ.key")
        self.limiter.use_key(self.kid)
        patcher = patch.object(tier, "get_rate_limiter", return_value=self.limiter)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(tier._known.clear)

    def test_a_billed_key_gets_the_billed_per_minute_cap(self):
        tier._remember(self.kid, tier.PAID, datetime.now(timezone.utc))
        self.assertTrue(self.limiter.paid)
        for _ in range(rpm_for("gemini-3.5-flash")):
            self.limiter.reserve("gemini-3.5-flash")
        self.assertEqual(self.limiter.state("gemini-3.5-flash"), "ok")

    def test_a_free_tier_refusal_settles_it_without_a_probe(self):
        tier._remember(self.kid, tier.PAID, datetime.now(timezone.utc))
        with patch.object(tier.asyncio, "get_running_loop", side_effect=RuntimeError):
            tier.saw_free_tier(self.kid)
        self.assertEqual(tier.known(self.kid), tier.FREE)
        self.assertFalse(self.limiter.paid)

    def test_an_answer_goes_stale(self):
        tier._remember(self.kid, tier.FREE, datetime.now(timezone.utc) - timedelta(hours=7))
        self.assertTrue(tier._stale(self.kid))
        tier._remember(self.kid, tier.PAID, datetime.now(timezone.utc) - timedelta(hours=7))
        self.assertFalse(tier._stale(self.kid))


class BudgetTests(unittest.TestCase):
    """What a request does once a billed key's month is past its budget."""

    def _run(self, action, *, grounding=0):
        client = GeminiClient()
        sdk = MagicMock()
        ok = MagicMock()
        ok.usage_metadata = None
        sdk.aio.models.generate_content = AsyncMock(return_value=ok)
        client.aclient = AsyncMock(return_value=sdk)
        client._key = "AQ.key"
        limiter = MagicMock()
        limiter.paid = True
        limiter.state.return_value = "ok"
        budget = spend.Budget(10.0, action, True)
        with patch("olisar.gemini.client.get_rate_limiter", return_value=limiter), \
                patch("olisar.gemini.client.record_usage", new=AsyncMock()), \
                patch.object(spend, "over_budget", AsyncMock(return_value=True)), \
                patch.object(spend, "budget", AsyncMock(return_value=budget)):
            asyncio.run(client._raw_generate(
                contents="hi", config=MagicMock(), model="gemini-3.5-flash", grounding=grounding,
            ))
        return [c.kwargs["model"] for c in sdk.aio.models.generate_content.await_args_list]

    def test_cheapest_answers_on_the_cheapest_models_alone(self):
        self.assertEqual(self._run(spend.CHEAPEST_ACTION), spend.BUDGET_CHAIN[:1])

    def test_stop_refuses_like_a_rate_limit(self):
        with self.assertRaises(BudgetSpent):
            self._run(spend.STOP)

    def test_web_search_stops_either_way(self):
        with self.assertRaises(BudgetSpent):
            self._run(spend.CHEAPEST_ACTION, grounding=1)


GUILD = 1001


class SearchCapTests(unittest.IsolatedAsyncioTestCase):
    """Each server's search cap counts that server's searches: a day's on a free key, a
    month's on a billed one. It used to count the whole install's."""

    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema
        from olisar.guild_setup import ensure_guild_defaults

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine.pin_database(str(Path(tmp.name) / "bot.db"))
        runtime_config.invalidate()
        await create_schema()
        async with session_scope() as s:
            await ensure_guild_defaults(s, GUILD, name="Home")
            await ensure_guild_defaults(s, GUILD + 1, name="Other")
            for gid in (GUILD, GUILD + 1):
                cfg = await s.get(GuildConfig, gid)
                cfg.grounding_daily_cap = 2
                cfg.grounding_monthly_cap = 3

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def _allowed(self, guild, *, paid):
        from olisar import tools

        limiter = SimpleNamespace(paid=paid)
        with patch.object(tools, "get_rate_limiter", return_value=limiter):
            async with session_scope() as s:
                return await tools._grounding_allowed(s, guild)

    async def _search(self, guild, day=None):
        from olisar import tools

        with patch.object(tools, "quota_day", return_value=day or tools.quota_day()):
            async with session_scope() as s:
                await tools._count_search(s, guild)

    async def test_another_servers_searches_dont_count(self):
        await self._search(GUILD + 1)
        await self._search(GUILD + 1)
        self.assertTrue(await self._allowed(GUILD, paid=False))
        self.assertFalse(await self._allowed(GUILD + 1, paid=False))

    async def test_a_free_key_counts_the_day_and_a_billed_one_the_month(self):
        from olisar.gemini.quota import quota_day

        today = quota_day()
        earlier = today.replace(day=1) if today.day > 1 else today
        await self._search(GUILD, earlier)
        await self._search(GUILD, earlier)
        await self._search(GUILD)
        if earlier != today:
            self.assertTrue(await self._allowed(GUILD, paid=False))  # 1 today, cap 2
        self.assertFalse(await self._allowed(GUILD, paid=True))  # 3 this month, cap 3


if __name__ == "__main__":
    unittest.main()
