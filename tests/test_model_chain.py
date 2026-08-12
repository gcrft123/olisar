"""The fallback chain: what it starts on, and which errors move it along.

Run:  uv run python -m unittest tests.test_model_chain -v

Both behaviours come out of the blank-fallback incident. The chain started on an
auto-updating alias, so a provider-side change altered request validation under a
running bot; and a 400 was all-or-nothing, so there was no way to say "this model
can't do that" without also saying it about a request that is simply wrong.
"""

from __future__ import annotations

import unittest

from google.genai import errors as genai_errors

from olisar.config import settings
from olisar.db.models import GuildConfig
from olisar.gemini.client import _MODEL_RETIRED, _is_capability_400
from olisar.gemini.models import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_LITE_MODEL,
    DEFAULT_VISION_MODEL,
    IMAGE_RANKED_NAMES,
    LEGACY_DEFAULT_CHAT_MODEL,
    RANKED_NAMES,
    image_model_chain,
    model_chain,
)

# Retired upstream: generateContent answers 404 "no longer available". models.get still
# returns metadata for them, so only a real generation reveals it — which is how these sat
# in the chains unnoticed, one of them heading the vision chain.
RETIRED = ("gemini-2.0-flash", "gemini-2.0-flash-lite")


class _Err(genai_errors.APIError):
    """A real APIError — the chain's handler catches that type specifically — built
    without the SDK's response plumbing, since only code/message/details are read."""

    def __init__(self, code: int, message: str) -> None:
        Exception.__init__(self, message)  # bypass APIError's response parsing
        self.code = code
        self.message = message
        self.details = None
        self.status = None


class PinnedDefaultTests(unittest.TestCase):
    def test_no_alias_is_the_default_anywhere(self):
        """Four places used to spell this default; none of them may say `-latest`."""
        self.assertNotIn("latest", DEFAULT_CHAT_MODEL)
        self.assertNotIn("latest", DEFAULT_LITE_MODEL)
        self.assertNotIn("latest", settings.gemini_chat_model)
        self.assertNotIn("latest", settings.gemini_lite_model)
        self.assertNotIn("latest", GuildConfig.__table__.c.default_model.default.arg)

    def test_the_column_default_and_the_constant_agree(self):
        self.assertEqual(GuildConfig.__table__.c.default_model.default.arg, DEFAULT_CHAT_MODEL)
        self.assertEqual(settings.gemini_chat_model, DEFAULT_CHAT_MODEL)

    def test_chain_starts_on_the_pinned_model(self):
        self.assertEqual(RANKED_NAMES[0], DEFAULT_CHAT_MODEL)

    def test_aliases_are_kept_but_demoted(self):
        """Still reachable — an alias resolves when a pinned model is retired — but never
        first, and always below the concrete model they shadow."""
        for alias, pinned in (
            ("gemini-flash-latest", "gemini-3.5-flash"),
            ("gemini-flash-lite-latest", "gemini-3.1-flash-lite"),
        ):
            with self.subTest(alias=alias):
                self.assertIn(alias, RANKED_NAMES)
                self.assertGreater(RANKED_NAMES.index(alias), RANKED_NAMES.index(pinned))

    def test_legacy_default_is_still_a_real_chain_entry(self):
        """The migration moves guilds off it, so it has to remain selectable."""
        self.assertIn(LEGACY_DEFAULT_CHAT_MODEL, RANKED_NAMES)

    def test_chain_has_no_duplicates(self):
        self.assertEqual(len(RANKED_NAMES), len(set(RANKED_NAMES)))
        self.assertEqual(len(IMAGE_RANKED_NAMES), len(set(IMAGE_RANKED_NAMES)))

    def test_chain_from_the_default_covers_everything_below_it(self):
        self.assertEqual(model_chain(DEFAULT_CHAT_MODEL), RANKED_NAMES)

    def test_lite_default_starts_the_lite_half(self):
        chain = model_chain(DEFAULT_LITE_MODEL)
        self.assertEqual(chain[0], DEFAULT_LITE_MODEL)
        self.assertTrue(all("lite" in m for m in chain), chain)


class RetiredModelTests(unittest.TestCase):
    def test_no_chain_still_lists_a_retired_model(self):
        for name in RETIRED:
            with self.subTest(model=name):
                self.assertNotIn(name, RANKED_NAMES)
                self.assertNotIn(name, IMAGE_RANKED_NAMES)

    def test_the_vision_chain_does_not_start_on_a_retired_model(self):
        """It did, and because a 404 raised, every image description failed outright."""
        self.assertNotIn(DEFAULT_VISION_MODEL, RETIRED)
        self.assertEqual(image_model_chain()[0], DEFAULT_VISION_MODEL)
        self.assertEqual(settings.gemini_vision_model, DEFAULT_VISION_MODEL)

    def test_every_vision_model_is_reachable_from_the_rate_limiter(self):
        """rpm_for falls back to a default, but a vision model absent from the RPM table
        would silently get chat's throttle — worth catching if the chains drift apart."""
        from olisar.gemini.models import rpm_for

        for name in IMAGE_RANKED_NAMES:
            with self.subTest(model=name):
                self.assertGreater(rpm_for(name), 0)

    def test_a_retirement_is_its_own_status_code(self):
        """404 must not be lumped in with the transient 5xx or read as a capability gap —
        it needs a long cooldown, since retirement doesn't clear in fifteen seconds."""
        from olisar.gemini.client import _TRANSIENT_5XX

        self.assertEqual(_MODEL_RETIRED, 404)
        self.assertNotIn(_MODEL_RETIRED, _TRANSIENT_5XX)
        self.assertFalse(_is_capability_400(_Err(404, "no longer available")))


class Capability400Tests(unittest.TestCase):
    def test_the_role_bug_is_not_treated_as_a_capability_gap(self):
        """The regression that started all of this. Every model rejects it, so falling
        through the chain would spend nine requests and still return the blank fallback."""
        exc = _Err(400, (
            "Role 'tool' is not supported. Please use a valid role: SYSTEM, SYSTEM_1, "
            "USER, ASSISTANT, DEVELOPER, CONTEXT, USER_CONTEXT, MODEL, USER."
        ))
        self.assertFalse(_is_capability_400(exc))

    def test_other_request_shape_errors_still_raise(self):
        for msg in (
            "Invalid JSON payload received. Unknown name \"contents\".",
            "contents.parts must not be empty",
            "API key not valid. Please pass a valid API key.",
            "Please use a valid role",
        ):
            with self.subTest(msg=msg):
                self.assertFalse(_is_capability_400(_Err(400, msg)))

    def test_feature_gaps_fall_through(self):
        for msg in (
            "Search Grounding is not supported for this model.",
            "google_search is not supported for this model",
            "thinking_config is not supported by this model",
            "Setting thinking_budget is not supported",
            "code_execution is not enabled for this model",
            "response_schema is not supported",
            "response_mime_type application/json is unsupported here",
            "tool_config is not supported for this model",
            "url_context is not available for this model",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(_is_capability_400(_Err(400, msg)))

    def test_only_400s_qualify(self):
        """A 429/503 mentioning a feature must keep its own handling, not this one."""
        for code in (429, 500, 503, 404):
            with self.subTest(code=code):
                self.assertFalse(_is_capability_400(_Err(code, "google_search is not supported")))

    def test_classification_is_case_insensitive(self):
        self.assertTrue(_is_capability_400(_Err(400, "GOOGLE_SEARCH IS NOT SUPPORTED")))

    def test_an_error_without_a_code_is_not_a_capability_gap(self):
        self.assertFalse(_is_capability_400(Exception("google_search is not supported")))


class ChainWalkTests(unittest.IsolatedAsyncioTestCase):
    """How _raw_generate actually reacts, walking a scripted chain."""

    async def _walk(self, outcomes: dict[str, object]):
        """Run the chain; each model either raises its scripted error or returns a resp.
        Returns (served_model, attempted_models)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from olisar.gemini.client import GeminiClient

        attempted: list[str] = []

        async def fake_generate(*, model, contents, config):
            attempted.append(model)
            outcome = outcomes[model]
            if isinstance(outcome, Exception):
                raise outcome
            resp = MagicMock()
            resp.usage_metadata = None
            resp.__served__ = model
            return resp

        client = GeminiClient()
        sdk = MagicMock()
        sdk.aio.models.generate_content = AsyncMock(side_effect=fake_generate)
        limiter = MagicMock()
        limiter.state.return_value = "ok"

        with patch.object(GeminiClient, "aclient", AsyncMock(return_value=sdk)), patch(
            "olisar.gemini.client.get_rate_limiter", return_value=limiter
        ), patch("olisar.gemini.client.record_usage", new=AsyncMock()):
            resp = await client._raw_generate(
                contents=[], config=MagicMock(), model=RANKED_NAMES[0],
                chain=list(outcomes), thinking_budget=None,
            )
        return resp.__served__, attempted

    async def test_a_retired_model_hands_off_to_the_next_one(self):
        """The bug: this raised, so a dead entry killed the request instead of degrading."""
        served, attempted = await self._walk({
            "dead": _Err(404, "This model models/dead is no longer available."),
            "alive": None,
        })
        self.assertEqual(served, "alive")
        self.assertEqual(attempted, ["dead", "alive"])

    async def test_a_capability_400_hands_off_to_the_next_one(self):
        served, _ = await self._walk({
            "no-search": _Err(400, "google_search is not supported for this model"),
            "alive": None,
        })
        self.assertEqual(served, "alive")

    async def test_a_malformed_request_stops_at_the_first_model(self):
        """Every model would reject it, so nine more attempts help nobody."""
        with self.assertRaises(_Err):
            await self._walk({
                "first": _Err(400, "Role 'tool' is not supported. Please use a valid role"),
                "second": None,
            })

    async def test_the_preferred_model_is_used_when_it_works(self):
        served, attempted = await self._walk({"first": None, "second": None})
        self.assertEqual(served, "first")
        self.assertEqual(attempted, ["first"])


if __name__ == "__main__":
    unittest.main()
