"""RateLimiter.chat_exhausted — the sidebar power button's "Rate-limited" gate."""

from __future__ import annotations

import unittest

from olisar.gemini.models import RANKED_NAMES
from olisar.gemini.rate_limiter import RateLimiter


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


if __name__ == "__main__":
    unittest.main()
