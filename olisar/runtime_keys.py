"""Effective API keys: dashboard-stored value (``app_secret`` row) when set, else
the ``.env`` value from ``settings``.

Lets an operator paste their own Gemini / Cloudflare / UEX keys into the dashboard
without editing ``.env`` or restarting — the call sites read these getters live. A
short in-process cache keeps a busy reply (many Gemini calls) from hitting SQLite
each time; the API process calls ``invalidate()`` after a save so its own reads are
instant, and the bot process refreshes within ``_CACHE_TTL``.
"""

from __future__ import annotations

import logging
import time

from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AppSecret

log = logging.getLogger("olisar.runtime_keys")

_FIELDS = (
    "gemini_api_key",
    "cloudflare_account_id",
    "cloudflare_api_token",
    "uex_api_key",
)
_CACHE_TTL = 5.0  # seconds

_cache: dict[str, str] | None = None
_cache_at = 0.0


async def _load() -> dict[str, str]:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < _CACHE_TTL:
        return _cache
    values = {f: "" for f in _FIELDS}
    try:
        async with session_scope() as session:
            row = await session.get(AppSecret, 1)
            if row is not None:
                for f in _FIELDS:
                    values[f] = (getattr(row, f, "") or "")
    except Exception:
        log.exception("reading app_secret failed; falling back to .env keys")
    _cache, _cache_at = values, now
    return values


async def _resolve(field: str) -> str:
    """Dashboard value if set, otherwise the .env value."""
    db_value = (await _load()).get(field) or ""
    return db_value or getattr(settings, field, "") or ""


async def gemini_api_key() -> str:
    return await _resolve("gemini_api_key")


async def cloudflare_account_id() -> str:
    return await _resolve("cloudflare_account_id")


async def cloudflare_api_token() -> str:
    return await _resolve("cloudflare_api_token")


async def uex_api_key() -> str:
    return await _resolve("uex_api_key")


def invalidate() -> None:
    """Drop the cache so the next read re-queries the DB (call after a save)."""
    global _cache
    _cache = None
