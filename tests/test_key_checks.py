"""Coverage for the API key checks.

Run:  uv run python -m unittest tests.test_key_checks -v

The checks answer "does this key work", so the tests that matter pin the difference between a
wrong key and an outage (one is the operator's to fix, the other isn't), and which Cloudflare
field is at fault. The status codes are the ones Cloudflare actually returned when these were
written: 401 for a bad token, 403 for a real token on someone else's account, 404 for
something that isn't an account ID, and a 403 on /accounts for a token it doesn't know.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from api.routers import admin
from api.schemas import CloudflareCheckIn, GeminiCheckIn
from olisar import key_checks

ACCOUNT = "ef35482b6aa340c2a33264314e559e9a"


def _resp(status: int, body: dict | None = None):
    return SimpleNamespace(status_code=status, json=lambda: body or {})


class FakeCloudflare:
    """Answers /accounts and the Workers AI model search from a table of statuses."""

    def __init__(self, *, accounts=(200, []), models=200) -> None:
        self.accounts = accounts
        self.models = models
        self.urls: list[str] = []

    async def __call__(self, url, **kw):
        self.urls.append(url)
        if url.endswith("/accounts"):
            status, result = self.accounts
            return _resp(status, {"result": result})
        return _resp(self.models)


class GeminiTests(unittest.IsolatedAsyncioTestCase):
    async def test_answers_by_status(self) -> None:
        for status, expected in ((200, True), (400, False), (403, False)):
            with patch.object(key_checks, "_get", AsyncMock(return_value=_resp(status))):
                self.assertIs(await key_checks.gemini("k"), expected, status)

    async def test_an_outage_is_not_a_wrong_key(self) -> None:
        async def down(*a, **kw):
            raise httpx.ConnectError("offline")
        with patch("httpx.AsyncClient.get", down):
            with self.assertRaises(key_checks.Unreachable):
                await key_checks.gemini("k")
        with patch("httpx.AsyncClient.get", AsyncMock(return_value=httpx.Response(503))):
            with self.assertRaises(key_checks.Unreachable):
                await key_checks.gemini("k")


class CloudflareTests(unittest.IsolatedAsyncioTestCase):
    async def _check(self, fake: FakeCloudflare, account: str = "") -> dict:
        with patch.object(key_checks, "_get", fake):
            return await key_checks.cloudflare("tok", account)

    async def test_the_field_at_fault(self) -> None:
        cases = ((200, True, ""), (401, False, "token"), (403, False, "account"), (404, False, "account"))
        for status, ok, problem in cases:
            out = await self._check(FakeCloudflare(models=status), ACCOUNT)
            self.assertEqual((out["ok"], out["problem"]), (ok, problem), status)

    async def test_finds_the_account_when_the_token_can_see_it(self) -> None:
        fake = FakeCloudflare(accounts=(200, [{"id": ACCOUNT}]))
        out = await self._check(fake)
        self.assertEqual(out, {"ok": True, "account_id": ACCOUNT, "problem": ""})
        self.assertIn(f"/accounts/{ACCOUNT}/ai/models/search", fake.urls[-1])

    async def test_asks_for_the_account_when_the_token_cant_see_it(self) -> None:
        # The Workers AI template's token: a 200 with no accounts listed.
        out = await self._check(FakeCloudflare(accounts=(200, [])))
        self.assertEqual((out["ok"], out["problem"]), (False, "account"))

    async def test_an_unknown_token_is_the_tokens_fault_even_without_an_account(self) -> None:
        out = await self._check(FakeCloudflare(accounts=(403, None)))
        self.assertEqual((out["ok"], out["problem"]), (False, "token"))


class KeyCheckEndpointTests(unittest.IsolatedAsyncioTestCase):
    admin_user = SimpleNamespace(is_allowlisted=True)

    async def _cloudflare(self, body: CloudflareCheckIn, *, saved_token="", saved_account="", result=None):
        check = AsyncMock(return_value=result or {"ok": True, "account_id": ACCOUNT, "problem": ""})
        with patch.object(admin.runtime_keys, "cloudflare_api_token", AsyncMock(return_value=saved_token)), \
                patch.object(admin.runtime_keys, "cloudflare_account_id", AsyncMock(return_value=saved_account)), \
                patch.object(key_checks, "cloudflare", check):
            return await admin.check_cloudflare_key(body, self.admin_user), check

    async def test_typed_values_stand_in_for_saved_ones(self) -> None:
        _, check = await self._cloudflare(CloudflareCheckIn(token="typed"), saved_token="saved", saved_account=ACCOUNT)
        check.assert_awaited_once_with("typed", ACCOUNT)

    async def test_a_saved_account_id_is_never_sent_back(self) -> None:
        out, _ = await self._cloudflare(CloudflareCheckIn(token="typed"), saved_account=ACCOUNT)
        self.assertNotIn("account_id", out)

    async def test_an_account_id_found_from_the_token_is(self) -> None:
        out, _ = await self._cloudflare(CloudflareCheckIn(token="typed"))
        self.assertEqual(out["account_id"], ACCOUNT)

    async def test_nothing_to_check(self) -> None:
        out, check = await self._cloudflare(CloudflareCheckIn())
        self.assertEqual(out["set"], False)
        check.assert_not_awaited()
        with patch.object(admin.runtime_keys, "gemini_api_key", AsyncMock(return_value="")):
            self.assertEqual((await admin.check_gemini_key(GeminiCheckIn(), self.admin_user))["set"], False)

    async def test_an_outage_reads_as_unknown(self) -> None:
        with patch.object(admin.runtime_keys, "gemini_api_key", AsyncMock(return_value="saved")), \
                patch.object(key_checks, "gemini", AsyncMock(side_effect=key_checks.Unreachable())):
            self.assertEqual(await admin.check_gemini_key(GeminiCheckIn(), self.admin_user), {"set": True, "ok": None})


if __name__ == "__main__":
    unittest.main()
