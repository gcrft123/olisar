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
