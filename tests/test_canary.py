"""The model self-test: it has to fail on the shape that actually broke, and only then.

Run:  uv run python -m unittest tests.test_canary -v

The point of the canary is to turn "every tool-backed reply is 400ing" from something a
user discovers into something the operator's log says. So the case that must go red is a
refusal on the *second* turn — handing the tool result back — and the cases that must not
are the ones that say nothing about request validity (rate limits, a chatty model).

The sweep matters as much as the round-trip. Measured against the live API, the shape that
caused the incident was rejected only by the two `-latest` aliases and accepted by every
pinned model, so a self-test that checked only the head of the chain would have stayed green
right through the outage.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from olisar.gemini import canary
from olisar.gemini.rate_limiter import RateLimitExceeded


def _call(name: str = "olisar_selftest_echo"):
    c = MagicMock()
    c.name = name
    c.args = {"token": "ping"}
    return c


def _resp_with_call():
    r = MagicMock()
    r.function_calls = [_call()]
    return r


def _resp_with_text(text: str):
    r = MagicMock()
    r.function_calls = []
    r.text = text
    return r


MODEL = "gemini-3.5-flash"


class _Api(Exception):
    """A provider error carrying a status code, as the SDK's APIError does."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _run(side_effect):
    client = MagicMock()
    client.generate_with_tools = AsyncMock(side_effect=side_effect)
    with patch("olisar.gemini.canary.get_gemini", return_value=client):
        return asyncio.run(canary.run_canary(MODEL)), client


class CanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        canary._last = None

    def test_each_model_is_tested_on_a_single_model_chain(self):
        """Without this the client's own fallback answers for a broken model and the
        self-test reports it healthy — the failure mode the sweep exists to avoid."""
        _, client = _run([_resp_with_call(), _resp_with_text("done")])
        for call in client.generate_with_tools.await_args_list:
            self.assertEqual(call.kwargs["chain"], [MODEL])
            self.assertEqual(call.kwargs["model"], MODEL)

    def test_a_complete_round_trip_is_ok(self):
        result, client = _run([_resp_with_call(), _resp_with_text("echo: ping received")])
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.ok)
        self.assertEqual(client.generate_with_tools.await_count, 2)

    def test_a_refusal_on_the_tool_result_turn_is_a_failure(self):
        """The incident: the tool call worked, handing its result back did not."""
        boom = Exception(
            "400 INVALID_ARGUMENT. Role 'tool' is not supported. Please use a valid role"
        )
        result, _ = _run([_resp_with_call(), boom])
        self.assertEqual(result.status, "failed")
        self.assertIn("tool result turn", result.detail)
        self.assertFalse(result.ok)

    def test_a_refusal_on_the_first_turn_is_a_failure(self):
        result, _ = _run([Exception("400 INVALID_ARGUMENT. something is wrong")])
        self.assertEqual(result.status, "failed")
        self.assertIn("first turn", result.detail)

    def test_the_model_echoes_its_own_turn_back_verbatim(self):
        """Newer models require the thought_signature carried on the model's own content
        object, so the second request must contain that object, not a rebuilt copy."""
        first = _resp_with_call()
        sentinel = object()
        first.candidates[0].content = sentinel
        client = MagicMock()
        client.generate_with_tools = AsyncMock(
            side_effect=[first, _resp_with_text("done")]
        )
        with patch("olisar.gemini.canary.get_gemini", return_value=client):
            asyncio.run(canary.run_canary(MODEL))
        sent = client.generate_with_tools.await_args_list[1].kwargs["contents"]
        self.assertIn(sentinel, sent)

    def test_the_tool_result_goes_back_as_a_user_turn(self):
        """Same rule the pipeline follows — role="tool" is what broke."""
        client = MagicMock()
        client.generate_with_tools = AsyncMock(
            side_effect=[_resp_with_call(), _resp_with_text("done")]
        )
        with patch("olisar.gemini.canary.get_gemini", return_value=client):
            asyncio.run(canary.run_canary(MODEL))
        sent = client.generate_with_tools.await_args_list[1].kwargs["contents"]
        roles = [getattr(c, "role", None) for c in sent if hasattr(c, "role")]
        self.assertNotIn("tool", roles)
        self.assertIn("user", roles)

    # ── things that must NOT cry wolf ────────────────────────────────────────
    def test_a_rate_limit_is_inconclusive_not_failed(self):
        result, _ = _run([RateLimitExceeded("gemini-3.5-flash", "daily")])
        self.assertEqual(result.status, "inconclusive")
        self.assertFalse(result.ok)

    def test_a_rate_limit_on_the_second_turn_is_inconclusive(self):
        result, _ = _run([_resp_with_call(), RateLimitExceeded("gemini-3.5-flash", "rpm")])
        self.assertEqual(result.status, "inconclusive")

    def test_a_raw_429_from_the_provider_is_inconclusive(self):
        """A single-model chain has nowhere to fall back to, so the client re-raises the
        provider's own 429 rather than RateLimitExceeded. Catching only the latter marked
        a merely-busy model broken — observed on a live sweep."""
        result, _ = _run([_Api(429, "RESOURCE_EXHAUSTED. You exceeded your current quota")])
        self.assertEqual(result.status, "inconclusive")

    def test_a_transient_server_error_is_inconclusive(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                result, _ = _run([_resp_with_call(), _Api(code, "overloaded")])
                self.assertEqual(result.status, "inconclusive")

    def test_a_retired_model_is_still_a_failure(self):
        """Permanent and actionable — the chain needs editing, so this must not be muted."""
        result, _ = _run([_Api(404, "This model is no longer available.")])
        self.assertEqual(result.status, "failed")

    def test_a_rejected_shape_is_still_a_failure(self):
        result, _ = _run([_resp_with_call(), _Api(400, "Role 'tool' is not supported")])
        self.assertEqual(result.status, "failed")

    def test_a_model_that_never_calls_the_tool_is_inconclusive(self):
        result, client = _run([_resp_with_text("Sure — ping!")])
        self.assertEqual(result.status, "inconclusive")
        self.assertEqual(client.generate_with_tools.await_count, 1)

    def test_empty_text_after_the_tool_result_is_inconclusive(self):
        result, _ = _run([_resp_with_call(), _resp_with_text("")])
        self.assertEqual(result.status, "inconclusive")

    def test_run_canary_never_raises(self):
        """It is driven by a timer; an exception here would kill the loop."""
        result, _ = _run([RuntimeError("network gone")])
        self.assertEqual(result.status, "failed")


class ChainSweepTests(unittest.TestCase):
    """The sweep's verdict, and what /api/health reads from it."""

    def setUp(self) -> None:
        canary._last = None

    def _sweep(self, per_model: dict[str, str]):
        """Drive run_chain_canary with a scripted verdict per model."""
        async def fake(model: str):
            return canary.CanaryResult(
                status=per_model[model], detail="", at=canary.datetime.now(canary.timezone.utc),
                model=model,
            )

        with patch("olisar.gemini.canary.run_canary", new=fake):
            return asyncio.run(canary.run_chain_canary(list(per_model)))

    def test_all_healthy_is_ok(self):
        r = self._sweep({"a": "ok", "b": "ok"})
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.ok)
        self.assertEqual(r.failed_models, ())

    def test_one_broken_model_fails_the_sweep(self):
        """A model deep in the chain still takes live traffic when the ones above it are
        busy, so 'the head is fine' is not good enough."""
        r = self._sweep({"a": "ok", "b": "failed", "c": "ok"})
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.failed_models, ("b",))

    def test_the_incident_shape_is_caught_where_it_actually_broke(self):
        """Live measurement: only the aliases rejected it. A head-only test stays green."""
        r = self._sweep({
            "gemini-3.5-flash": "ok",
            "gemini-flash-latest": "failed",
            "gemini-3.1-flash-lite": "ok",
            "gemini-flash-lite-latest": "failed",
        })
        self.assertEqual(r.status, "failed")
        self.assertEqual(
            set(r.failed_models), {"gemini-flash-latest", "gemini-flash-lite-latest"}
        )

    def test_some_untested_models_do_not_fail_the_sweep(self):
        r = self._sweep({"a": "ok", "b": "inconclusive"})
        self.assertEqual(r.status, "ok")

    def test_an_entirely_untestable_sweep_is_inconclusive_not_ok(self):
        """Everything rate-limited says nothing; claiming 'ok' would be a false all-clear."""
        r = self._sweep({"a": "inconclusive", "b": "inconclusive"})
        self.assertEqual(r.status, "inconclusive")
        self.assertFalse(r.ok)

    def test_a_failure_outranks_an_untested_model(self):
        r = self._sweep({"a": "inconclusive", "b": "failed"})
        self.assertEqual(r.status, "failed")

    def test_last_result_is_none_until_a_sweep_completes(self):
        self.assertIsNone(canary.last_result())

    def test_last_result_records_the_latest_sweep(self):
        self._sweep({"a": "ok"})
        self.assertEqual(canary.last_result().status, "ok")
        self._sweep({"a": "failed"})
        self.assertEqual(canary.last_result().status, "failed")
        self.assertEqual(canary.last_result().failed_models, ("a",))

    def _sweep_names(self) -> list[str]:
        seen: list[str] = []

        async def fake(model: str):
            seen.append(model)
            return canary.CanaryResult(
                status="ok", detail="", at=canary.datetime.now(canary.timezone.utc), model=model
            )

        with patch("olisar.gemini.canary.run_canary", new=fake):
            asyncio.run(canary.run_chain_canary())
        return seen

    def test_the_default_sweep_covers_chat_and_vision(self):
        """Vision is where a retired model actually bit — its chain started on one, so a
        chat-only sweep would have called that outage healthy."""
        from olisar.gemini.models import IMAGE_RANKED_NAMES, RANKED_NAMES

        seen = self._sweep_names()
        for name in (*RANKED_NAMES, *IMAGE_RANKED_NAMES):
            with self.subTest(model=name):
                self.assertIn(name, seen)

    def test_the_sweep_does_not_test_a_shared_model_twice(self):
        seen = self._sweep_names()
        self.assertEqual(len(seen), len(set(seen)))


if __name__ == "__main__":
    unittest.main()
