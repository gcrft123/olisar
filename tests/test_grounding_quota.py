"""Coverage for grounded web search: why it failed, and not failing the same way twice.

Run:  uv run python -m unittest tests.test_grounding_quota -v

Web search reported "rate limited" on essentially every request while ordinary replies
worked fine. Three causes, all covered here:
  * search was pinned to one model and never walked the fallback chain
  * every 429 was flattened to GroundingUnavailable, discarding Google's reason
  * a refusal was never remembered, so each reply re-paid the round trip
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import errors as genai_errors

from olisar.gemini.client import (
    GeminiClient,
    GroundingUnavailable,
    _api_error_detail,
    _retry_after_seconds,
)
from olisar.gemini.rate_limiter import RateLimitExceeded


def _api_error(code: int, message: str, **extra) -> genai_errors.APIError:
    body = {"error": {"message": message, "status": "RESOURCE_EXHAUSTED", **extra}}
    return genai_errors.APIError(code, body)


QUOTA_429 = "You exceeded your current quota. quota_metric: generate_content_free_tier_requests"
THROTTLE_429 = 'Too many requests. [{"@type": "RetryInfo", "retryDelay": "23s"}]'


class ErrorDetailTests(unittest.TestCase):
    def test_detail_carries_googles_own_words(self):
        detail = _api_error_detail(_api_error(429, QUOTA_429))
        self.assertIn("quota_metric", detail)
        self.assertIn("RESOURCE_EXHAUSTED", detail)

    def test_retry_delay_parsed_when_present(self):
        self.assertEqual(_retry_after_seconds(_api_error(429, THROTTLE_429)), 23.0)

    def test_retry_delay_absent_reads_as_none(self):
        """No retryDelay is the signal that waiting won't help — the daily quota."""
        self.assertIsNone(_retry_after_seconds(_api_error(429, QUOTA_429)))

    def test_snake_case_retry_delay_also_parsed(self):
        err = _api_error(429, "rate limited, retry_delay { seconds: 7 }")
        self.assertEqual(_retry_after_seconds(err), 7.0)


class GroundingSuppressionTests(unittest.TestCase):
    def _client_raising(self, exc):
        client = GeminiClient()
        client._raw_generate = AsyncMock(side_effect=exc)
        return client

    def test_daily_quota_is_not_retried_on_the_next_reply(self):
        client = self._client_raising(_api_error(429, QUOTA_429))
        for _ in range(3):
            with self.assertRaises(GroundingUnavailable):
                asyncio.run(client.search("starlancer tac"))
        # Only the first attempt reached Google; the rest short-circuited.
        self.assertEqual(client._raw_generate.await_count, 1)

    def test_daily_quota_blocks_until_the_utc_day_rolls_over(self):
        client = self._client_raising(_api_error(429, QUOTA_429))
        with self.assertRaises(GroundingUnavailable):
            asyncio.run(client.search("q"))
        blocked = client._grounding_blocked_until
        self.assertIsNotNone(blocked)
        self.assertEqual((blocked.hour, blocked.minute, blocked.second), (0, 0, 0))
        self.assertLessEqual(blocked - datetime.now(timezone.utc), timedelta(days=1))

    def test_throttle_only_parks_for_the_delay_google_asked_for(self):
        """A per-minute throttle must not disable web search for the rest of the day."""
        client = self._client_raising(_api_error(429, THROTTLE_429))
        with self.assertRaises(GroundingUnavailable):
            asyncio.run(client.search("q"))
        wait = client._grounding_blocked_until - datetime.now(timezone.utc)
        self.assertLess(wait, timedelta(seconds=30))

    def test_search_resumes_once_the_window_passes(self):
        client = self._client_raising(_api_error(429, QUOTA_429))
        with self.assertRaises(GroundingUnavailable):
            asyncio.run(client.search("q"))
        client._grounding_blocked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        resp = MagicMock()
        resp.candidates[0].grounding_metadata.grounding_chunks = []
        client._raw_generate = AsyncMock(return_value=resp)
        with patch("olisar.gemini.client.safe_text", return_value="an answer"):
            text, sources = asyncio.run(client.search("q"))
        self.assertEqual(text, "an answer")
        self.assertIsNone(client._grounding_blocked_until)

    def test_all_models_parked_degrades_without_a_day_long_block(self):
        """RateLimitExceeded means the chain is busy, not that grounding is spent."""
        client = self._client_raising(RateLimitExceeded("gemini-flash-latest", "chain"))
        with self.assertRaises(GroundingUnavailable):
            asyncio.run(client.search("q"))
        self.assertIsNone(client._grounding_blocked_until)

    def test_non_429_still_surfaces(self):
        client = self._client_raising(_api_error(401, "bad key"))
        with self.assertRaises(genai_errors.APIError):
            asyncio.run(client.search("q"))


class GroundingFallbackChainTests(unittest.TestCase):
    """search() now walks the chat chain; a model that can't ground costs one hop."""

    def _run_chain(self, side_effects):
        client = GeminiClient()
        sdk = MagicMock()
        sdk.aio.models.generate_content = AsyncMock(side_effect=side_effects)
        client.aclient = AsyncMock(return_value=sdk)
        limiter = MagicMock()
        limiter.state.return_value = "ok"
        with patch("olisar.gemini.client.get_rate_limiter", return_value=limiter), patch(
            "olisar.gemini.client.record_usage", new=AsyncMock()
        ), patch("olisar.gemini.client.safe_text", return_value="grounded answer"):
            result = asyncio.run(client.search("q"))
        used = [c.kwargs["model"] for c in sdk.aio.models.generate_content.await_args_list]
        return result, used, limiter

    def test_model_rejecting_the_search_tool_falls_through_to_the_next(self):
        ok = MagicMock()
        ok.candidates[0].grounding_metadata.grounding_chunks = []
        ok.usage_metadata.total_token_count = 10
        (text, _), used, _ = self._run_chain(
            [_api_error(400, "search tool not supported"), ok]
        )
        self.assertEqual(text, "grounded answer")
        self.assertEqual(len(used), 2)
        self.assertNotEqual(used[0], used[1])

    def test_429_on_the_preferred_model_still_tries_the_next(self):
        ok = MagicMock()
        ok.candidates[0].grounding_metadata.grounding_chunks = []
        ok.usage_metadata.total_token_count = 10
        (text, _), used, limiter = self._run_chain([_api_error(429, QUOTA_429), ok])
        self.assertEqual(text, "grounded answer")
        self.assertEqual(len(used), 2)
        limiter.penalize.assert_called()  # the limited model gets parked


if __name__ == "__main__":
    unittest.main()
