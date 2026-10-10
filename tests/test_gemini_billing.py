"""A Gemini key with billing on: telling it from a free one.

Run:  uv run python -m unittest tests.test_gemini_billing -v

Olisar used to assume every key was on the free tier, so a billed key was held to free-tier
per-minute caps. Covered here: asking Google which tier a key is on, what settles it without
asking, and the per-minute cap a billed key gets.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from olisar.gemini import tier
from olisar.gemini.models import rpm_for
from olisar.gemini.rate_limiter import RateLimiter, key_id

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


if __name__ == "__main__":
    unittest.main()
