"""Coverage for cancelling a marketplace publish while the security review runs.

Run:  uv run python -m unittest tests.test_publish_cancel -v

The server re-runs the AI risk review before shipping, which takes about a minute, and the
console offers a Stop for that window. Stop is only worth offering if it beats the registry
POST: a publish is public, and anyone who installs in the meantime keeps their copy. So the
invariant here is narrow and absolute —

    if the operator disconnects, nothing reaches the registry.

Covered: the disconnect watcher itself, a Stop landing *during* the review, a Stop landing
after the review passed but before the POST, and the two things that must not change — a
connected publish still ships, and a blocked review is still a block rather than a cancel.
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.routers.marketplace import _watch_disconnect, publish
from api.schemas import MarketplacePublishIn


class _StubRequest:
    """Stands in for Starlette's Request — only ``is_disconnected()`` is exercised here."""

    def __init__(self, *, disconnect_after: int | None = None):
        self.disconnected = False
        self._after = disconnect_after
        self.polls = 0

    def disconnect(self) -> None:
        self.disconnected = True

    async def is_disconnected(self) -> bool:
        self.polls += 1
        if self._after is not None and self.polls > self._after:
            self.disconnected = True
        return self.disconnected


@contextlib.asynccontextmanager
async def _scope(session):
    yield session


def _package():
    pkg = MagicMock()
    pkg.source_ts = "export default {}"
    pkg.manifest = {"name": "demo"}
    pkg.requested_permissions = ["net"]
    pkg.permissions = ["net"]
    pkg.version = "1.0.0"
    return pkg


class WatchDisconnectTests(unittest.TestCase):
    def test_cancels_the_review_when_the_client_goes_away(self):
        async def go():
            task = asyncio.create_task(asyncio.sleep(5))
            stopped = asyncio.Event()
            await _watch_disconnect(_StubRequest(disconnect_after=1), task, stopped)
            self.assertTrue(stopped.is_set())
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(go())

    def test_leaves_a_review_that_finishes_first_alone(self):
        async def go():
            task = asyncio.create_task(asyncio.sleep(0))
            await asyncio.sleep(0.05)  # let it finish before the watcher looks
            request, stopped = _StubRequest(), asyncio.Event()
            await _watch_disconnect(request, task, stopped)
            self.assertFalse(stopped.is_set())
            self.assertFalse(task.cancelled())
            self.assertEqual(request.polls, 0)  # a finished review is never polled about

        asyncio.run(go())

    def test_the_flag_is_set_before_the_cancel(self):
        """The caller reads ``stopped`` the moment the review raises, so ordering matters:
        a flag set after the cancel would race and read as a server teardown instead."""

        async def go():
            task = asyncio.create_task(asyncio.sleep(5))
            stopped = asyncio.Event()
            watcher = asyncio.create_task(
                _watch_disconnect(_StubRequest(disconnect_after=0), task, stopped)
            )
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stopped.is_set())
            await watcher

        asyncio.run(go())


class PublishCancelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = MagicMock()
        self.session.get = AsyncMock(return_value=_package())
        self.identity = MagicMock(registry_token="tok", registry_handle="acme")
        self.registry = AsyncMock(return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={"id": "acme/demo", "version": "1.0.0"}),
        ))
        self.admin = MagicMock(is_allowlisted=True, discord_user_id=1)
        self.body = MarketplacePublishIn(key="demo")

    @contextlib.contextmanager
    def _wired(self, review):
        """Everything around the publish stubbed out, so only the cancel logic is under test."""
        with contextlib.ExitStack() as stack:
            for p in (
                patch("api.routers.marketplace.session_scope", lambda: _scope(self.session)),
                patch("api.routers.marketplace.signing.ensure_identity",
                      AsyncMock(return_value=self.identity)),
                patch("api.routers.marketplace.build_signed_bundle",
                      AsyncMock(return_value={"manifest": {}})),
                patch("api.routers.marketplace.review_source", review),
                patch("api.routers.marketplace.runtime_config.extension_risk_threshold",
                      AsyncMock(return_value=70)),
                patch("api.routers.marketplace._record_blocked", AsyncMock()),
                patch("api.routers.marketplace._registry_post", self.registry),
            ):
                stack.enter_context(p)
            yield

    async def test_stop_during_the_review_never_reaches_the_registry(self):
        async def slow_review(*_a, **_k):
            await asyncio.sleep(5)
            return {"ok": True, "score": 0, "bullets": []}

        with self._wired(slow_review):
            with self.assertRaises(HTTPException) as caught:
                await publish(_StubRequest(disconnect_after=1), self.body, self.admin)

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail["code"], "cancelled")
        self.registry.assert_not_awaited()

    async def test_stop_after_the_review_passes_still_ships_nothing(self):
        """The expensive part finished, the verdict was clean — and the operator is gone.
        The bundle has not left the machine yet, so it must not leave at all."""
        request = _StubRequest()

        async def review(*_a, **_k):
            request.disconnect()  # Stop pressed while the review was running
            return {"ok": True, "score": 10, "bullets": []}

        with self._wired(review):
            with self.assertRaises(HTTPException) as caught:
                await publish(request, self.body, self.admin)

        self.assertEqual(caught.exception.detail["code"], "cancelled")
        self.registry.assert_not_awaited()

    async def test_a_connected_publish_still_ships(self):
        review = AsyncMock(return_value={"ok": True, "score": 10, "bullets": []})
        with self._wired(review):
            out = await publish(_StubRequest(), self.body, self.admin)

        self.assertEqual(out["id"], "acme/demo")
        self.registry.assert_awaited_once()

    async def test_a_blocked_review_is_still_a_block_not_a_cancel(self):
        """The risk gate predates all of this and has to keep reporting itself as itself —
        the console renders a different screen for each code."""
        review = AsyncMock(return_value={"ok": True, "score": 90, "bullets": ["reads env"]})
        with self._wired(review):
            with self.assertRaises(HTTPException) as caught:
                await publish(_StubRequest(), self.body, self.admin)

        self.assertEqual(caught.exception.detail["code"], "risk_blocked")
        self.registry.assert_not_awaited()

    async def test_an_unavailable_review_still_fails_closed(self):
        review = AsyncMock(return_value={"ok": False})
        with self._wired(review):
            with self.assertRaises(HTTPException) as caught:
                await publish(_StubRequest(), self.body, self.admin)

        self.assertEqual(caught.exception.detail["code"], "review_unavailable")
        self.registry.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
