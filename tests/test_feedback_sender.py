"""Who may send feedback, and what a turned-away account's feedback may carry.

Run:  uv run python -m unittest tests.test_feedback_sender -v

The access-denied screen offers one way out, telling the Olisar team you're stuck, and it
used to fail with 401 because a refused sign-in creates no session. The refusal now leaves
a signed cookie naming the Discord account. These pin what that cookie can and can't do:

  * it identifies the account to the feedback endpoint, and nothing forged or expired does
  * an OAuth state cookie (same secret, other salt) can't be replayed as one
  * sessions keep working exactly as before, and no cookie at all is still a 401
  * a turned-away sender never attaches this install's logs, however the request asks
  * and can't send more than a handful an hour
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from itsdangerous import URLSafeTimedSerializer

from api.auth import oauth
from api.routers import settings as settings_router
from api.schemas import FeedbackIn

SECRET = "test-session-secret"
USER = 424242


def _remote_request() -> MagicMock:
    req = MagicMock()
    req.client.host = "203.0.113.9"
    req.headers = {}
    return req


class DeniedCookieTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        p = patch.object(oauth.runtime_config, "session_secret", AsyncMock(return_value=SECRET))
        p.start()
        self.addCleanup(p.stop)

    async def test_round_trip(self) -> None:
        token = await oauth.denied_cookie_value(USER)
        self.assertEqual(await oauth.denied_identity(token), USER)

    async def test_missing_and_forged_are_nobody(self) -> None:
        self.assertIsNone(await oauth.denied_identity(None))
        self.assertIsNone(await oauth.denied_identity(""))
        self.assertIsNone(await oauth.denied_identity("not-a-token"))
        other = URLSafeTimedSerializer("some-other-secret", salt="olisar-denied-identity").dumps({"u": USER})
        self.assertIsNone(await oauth.denied_identity(other))

    async def test_state_cookie_cannot_stand_in(self) -> None:
        # Same secret, the OAuth state's salt: a cookie every login attempt gets.
        state = URLSafeTimedSerializer(SECRET, salt="olisar-oauth-state").dumps({"u": USER, "s": "x"})
        self.assertIsNone(await oauth.denied_identity(state))

    async def test_expired_is_nobody(self) -> None:
        token = await oauth.denied_cookie_value(USER)
        with patch.object(oauth, "DENIED_TTL", -1):
            self.assertIsNone(await oauth.denied_identity(token))


class FeedbackSenderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for p in (
            patch.object(oauth.runtime_config, "session_secret", AsyncMock(return_value=SECRET)),
            patch("api.auth.deps.is_local_request", return_value=False),
        ):
            p.start()
            self.addCleanup(p.stop)
        settings_router._denied_sent.clear()

    async def test_no_cookie_is_still_401(self) -> None:
        with self.assertRaises(HTTPException) as cm:
            await settings_router.feedback_sender(_remote_request(), None, None, None)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_denied_cookie_is_a_sender(self) -> None:
        token = await oauth.denied_cookie_value(USER)
        actor = await settings_router.feedback_sender(_remote_request(), None, None, token)
        self.assertEqual(actor, f"denied:{USER}")

    async def test_session_wins_over_denied_cookie(self) -> None:
        admin = MagicMock(discord_user_id=7)
        token = await oauth.denied_cookie_value(USER)
        with patch("api.auth.deps.get_admin_for_token", AsyncMock(return_value=admin)):
            actor = await settings_router.feedback_sender(_remote_request(), "sid", None, token)
        self.assertEqual(actor, "admin:7")

    async def _send(self, actor: str, **body) -> dict:
        sent: dict = {}

        async def fake_post(path: str, payload: dict):
            sent.update(payload)
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"ok": True, "emailed": True}
            return resp

        with patch.object(settings_router, "_registry_post", fake_post), \
             patch.object(settings_router.logbuffer, "tail", return_value=["member A said something private"]):
            await settings_router.send_feedback(FeedbackIn(message="stuck", **body), actor=actor, user_id=None)
        return sent

    async def test_turned_away_sender_never_attaches_logs(self) -> None:
        sent = await self._send(f"denied:{USER}", include_logs=True, logs="pasted text")
        self.assertEqual(sent["logs"], "")
        self.assertEqual(sent["reporter"], f"denied:{USER}")

    async def test_admin_still_attaches_logs(self) -> None:
        sent = await self._send("admin:7", include_logs=True)
        self.assertIn("member A said something private", sent["logs"])

    async def test_turned_away_sender_is_rate_limited(self) -> None:
        for _ in range(settings_router._DENIED_PER_HOUR):
            await self._send(f"denied:{USER}")
        with self.assertRaises(HTTPException) as cm:
            await self._send(f"denied:{USER}")
        self.assertEqual(cm.exception.status_code, 429)
        # Someone else turned away is counted separately.
        await self._send("denied:99")


if __name__ == "__main__":
    unittest.main()
