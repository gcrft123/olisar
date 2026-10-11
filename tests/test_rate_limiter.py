"""RateLimiter.chat_exhausted — the sidebar power button's "Rate-limited" gate."""

from __future__ import annotations

import unittest

from olisar.gemini.models import RANKED_NAMES, model_chain
from olisar.gemini.rate_limiter import RateLimiter, reply_chain, union_chain


class ChatExhaustedTests(unittest.TestCase):
    def test_fresh_limiter_is_not_exhausted(self):
        self.assertFalse(RateLimiter().chat_exhausted())

    def test_one_parked_model_is_not_exhausted(self):
        limiter = RateLimiter()
        limiter.penalize(RANKED_NAMES[0])
        self.assertFalse(limiter.chat_exhausted())

    def test_every_chat_model_parked_is_exhausted(self):
        limiter = RateLimiter()
        for name in RANKED_NAMES:
            limiter.penalize(name)
        self.assertTrue(limiter.chat_exhausted())

    def test_embedding_alone_does_not_count(self):
        """The chat chain is what replies; parking embeddings isn't "can't function"."""
        limiter = RateLimiter()
        limiter.penalize("gemini-embedding-001")
        self.assertFalse(limiter.chat_exhausted())

    def test_a_chain_that_starts_lower_down_is_out_when_its_own_models_are(self):
        """A server on gemini-2.5-flash never reaches the models ranked above it."""
        limiter = RateLimiter()
        chain = model_chain("gemini-2.5-flash")
        for name in chain:
            limiter.penalize(name)
        self.assertTrue(limiter.chat_exhausted(chain))
        self.assertFalse(limiter.chat_exhausted())


class UnionChainTests(unittest.TestCase):
    def test_every_model_any_server_replies_through_best_first(self):
        chains = [model_chain("gemini-2.5-flash"), model_chain("gemini-3.6-flash")]
        self.assertEqual(union_chain(chains), model_chain("gemini-3.6-flash"))

    def test_an_unranked_default_comes_first(self):
        chains = [model_chain("gemini-2.5-flash"), model_chain("gemini-custom")]
        self.assertEqual(union_chain(chains), ["gemini-custom", *RANKED_NAMES])

    def test_no_servers_is_the_default_chain(self):
        self.assertEqual(union_chain([]), reply_chain(None))


if __name__ == "__main__":
    unittest.main()


class EmbeddingBatchTests(unittest.TestCase):
    """Google counts every text in an embedding batch as a request, so a batch takes one
    per-minute slot per text. It used to take one, which let indexing blow through a free
    key's 100 a minute."""

    def test_a_batch_takes_a_slot_per_text(self):
        import asyncio

        limiter = RateLimiter()
        asyncio.run(limiter.acquire("gemini-embedding-001", n=50))
        self.assertEqual(limiter.current("gemini-embedding-001"), 50)

    def test_a_batch_that_wont_fit_waits(self):
        import asyncio

        limiter = RateLimiter()
        asyncio.run(limiter.acquire("gemini-embedding-001", n=60))  # a free key allows 100
        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(asyncio.wait_for(limiter.acquire("gemini-embedding-001", n=50), 0.5))
        asyncio.run(asyncio.wait_for(limiter.acquire("gemini-embedding-001", n=40), 0.5))

    def test_the_rollup_counts_each_text(self):
        from datetime import date, datetime, timezone

        from olisar.gemini.rate_limiter import _UsageBuffer

        buf = _UsageBuffer()
        buf.add(day=date(2026, 10, 10), hour=3, model="gemini-embedding-001", tokens=0,
                grounding=0, source="embed", rpm=50, rpm_at=datetime.now(timezone.utc),
                tpm=0, key="k", requests=50)
        self.assertEqual(buf.models[(date(2026, 10, 10), "gemini-embedding-001")]["requests"], 50)
        self.assertEqual(buf.sources[(date(2026, 10, 10), "embed")], 50)
