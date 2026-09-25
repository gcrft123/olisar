"""Check that a pasted API key works before it's saved.

The keys Olisar asks for fail late and quietly when they're wrong: a bad Gemini key shows up
at the first reply, and a bad Cloudflare pair only when someone asks for an image. Each check
here makes the cheapest real call the key exists for and says whether it worked. An outage
raises ``Unreachable`` instead, so it's never reported as a wrong key.
"""

from __future__ import annotations

import httpx

_GEMINI_MODELS = "https://generativelanguage.googleapis.com/v1beta/models"
_CLOUDFLARE = "https://api.cloudflare.com/client/v4"


class Unreachable(Exception):
    """The service couldn't answer: a network failure or a 5xx, not a wrong key."""


async def _get(url: str, **kw) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, **kw)
    except httpx.HTTPError as exc:
        raise Unreachable(str(exc)) from exc
    if resp.status_code >= 500:
        raise Unreachable(f"HTTP {resp.status_code}")
    return resp


async def gemini(key: str) -> bool:
    """Whether Google accepts the key, by listing one model with it. The header rather than
    ``?key=``, so the key stays out of any logged URL."""
    resp = await _get(_GEMINI_MODELS, params={"pageSize": 1}, headers={"x-goog-api-key": key})
    return resp.status_code == 200


async def cloudflare(token: str, account_id: str = "") -> dict:
    """Whether the token can run Workers AI on the account, which is all image generation
    needs. Answers ``{"ok", "account_id", "problem"}``; ``problem`` is ``"token"`` or
    ``"account"`` when it isn't ok, so the field at fault can say so.

    Without an account ID, it's looked up from the token. That only works for a token that
    can read its account, which the Workers AI template's can't, so ``problem`` is
    ``"account"`` there: the operator pastes the ID shown beside the token.
    """
    auth = {"Authorization": f"Bearer {token}"}
    account_id = account_id.strip()
    if not account_id:
        resp = await _get(f"{_CLOUDFLARE}/accounts", params={"per_page": 2}, headers=auth)
        # A token Cloudflare doesn't know is a 403 here (code 9109), where a real token that
        # just can't read accounts gets a 200 with none listed.
        if resp.status_code != 200:
            return {"ok": False, "account_id": "", "problem": "token"}
        accounts = resp.json().get("result") or []
        if len(accounts) != 1:
            return {"ok": False, "account_id": "", "problem": "account"}
        account_id = accounts[0]["id"]
    resp = await _get(f"{_CLOUDFLARE}/accounts/{account_id}/ai/models/search", params={"per_page": 1}, headers=auth)
    if resp.status_code == 200:
        return {"ok": True, "account_id": account_id, "problem": ""}
    # 401: the token itself. 403: a real token for some other account. 404: not an account ID.
    return {"ok": False, "account_id": account_id, "problem": "token" if resp.status_code in (400, 401) else "account"}
