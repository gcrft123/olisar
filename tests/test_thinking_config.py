"""Coverage for thinking_config negotiation.

Run:  uv run python -m unittest tests.test_thinking_config -v

`gemini-flash-lite-latest` moved to a Gemini 3 model, which cannot switch thinking off,
so the `thinking_budget=0` that every background caller of generate() sends began
returning 400 INVALID_ARGUMENT. The 400 was non-transient, so the chain re-raised
instead of falling back — persona synthesis, channel summaries, glossary extraction,
catchup and proactivity all failed silently while chat (which sends a non-zero budget)
kept working. The client now retries once without the field and remembers.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from google.genai import errors as genai_errors

import olisar.gemini.client as client_mod
from olisar.gemini.client import GeminiClient

MODEL = "gemini-flash-lite-latest"


def _api_error(code: int, message: str) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": message, "status": "INVALID_ARGUMENT"}})


BAD_ARG = _api_error(400, "Request contains an invalid argument.")


def _ok_response():
    resp = MagicMock()
    resp.usage_metadata.total_token_count = 12
    return resp


class ThinkingRetryTests(unittest.TestCase):
    def setUp(self):
        client_mod._THINKING_REJECTED.clear()
        self.addCleanup(client_mod._THINKING_REJECTED.clear)

    def _client(self, side_effects):
        client = GeminiClient()
        sdk = MagicMock()
        sdk.aio.models.generate_content = AsyncMock(side_effect=side_effects)
        client.aclient = AsyncMock(return_value=sdk)
        return client, sdk

    def _generate(self, client, budget=0):
        limiter = MagicMock()
        limiter.state.return_value = "ok"
        with patch("olisar.gemini.client.get_rate_limiter", return_value=limiter), patch(
            "olisar.gemini.client.record_usage", new=AsyncMock()
        ):
            return asyncio.run(
                client._raw_generate(
                    contents=["hi"],
                    config=MagicMock(),
                    model=MODEL,
                    thinking_budget=budget,
                )
            ), limiter

    def test_400_on_thinking_budget_retries_the_same_model_without_it(self):
        client, sdk = self._client([BAD_ARG, _ok_response()])
        self._generate(client)
        calls = sdk.aio.models.generate_content.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual([c.kwargs["model"] for c in calls], [MODEL, MODEL])  # same model
        self.assertIn(MODEL, client_mod._THINKING_REJECTED)

    def test_the_lesson_is_remembered_for_later_calls(self):
        client, sdk = self._client([BAD_ARG, _ok_response(), _ok_response()])
        self._generate(client)
        self._generate(client)
        # 2 for the first call (reject + retry), 1 for the second — not 4.
        self.assertEqual(sdk.aio.models.generate_content.await_count, 3)

    def test_a_400_we_did_not_cause_still_surfaces(self):
        """Without a thinking_config in play, a 400 is a real bad request."""
        client, _ = self._client([BAD_ARG])
        with self.assertRaises(genai_errors.APIError):
            self._generate(client, budget=None)

    def test_a_400_that_survives_the_retry_still_surfaces(self):
        client, sdk = self._client([BAD_ARG, BAD_ARG])
        with self.assertRaises(genai_errors.APIError):
            self._generate(client)
        self.assertEqual(sdk.aio.models.generate_content.await_count, 2)

    def test_models_known_to_reject_the_field_never_get_it(self):
        """The cheap pre-filter still applies — 2.0 models are never sent the field."""
        self.assertFalse(client_mod._supports_thinking("gemini-2.0-flash-lite"))
        self.assertTrue(client_mod._supports_thinking(MODEL))

    def test_successful_thinking_call_is_left_alone(self):
        client, sdk = self._client([_ok_response()])
        self._generate(client)
        self.assertEqual(sdk.aio.models.generate_content.await_count, 1)
        self.assertNotIn(MODEL, client_mod._THINKING_REJECTED)


if __name__ == "__main__":
    unittest.main()
