"""Whether the Gemini key is on Google's free tier or has billing turned on.

Billing belongs to the key's Google Cloud project, and once it's on, everything the project
uses is billed: there's no free allowance left to spend first. So a key is one or the
other, and a good deal of what Olisar does and shows depends on which. Billed, it lifts its
own per-minute throttle, can search on the newest models and make images with Gemini, and
the Usage page shows money instead of requests left.

Google doesn't report the tier anywhere, so Olisar asks. A paid-only model has a free quota
of 0: on a free key it answers 429 naming a ``-FreeTier`` quota, and on a billed key it
answers. One request to it, for a few output tokens, tells them apart for well under a
hundredth of a cent. Any 429 that names a ``-FreeTier`` quota says the same thing for
nothing (``saw_free_tier``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from olisar import runtime_keys
from olisar.db.engine import session_scope
from olisar.db.models import GeminiKeyTier
from olisar.gemini.models import GEMINI_IMAGE_MODEL
from olisar.gemini.rate_limiter import get_rate_limiter, key_id

log = logging.getLogger("olisar.gemini.tier")

FREE, PAID = "free", "paid"

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
# Paid-only models, tried in order: the next one stands in if Google retires the first.
_PROBE_MODELS = ("gemini-3.1-pro-preview", GEMINI_IMAGE_MODEL)
_PROBE_BODY = {
    "contents": [{"role": "user", "parts": [{"text": "Reply with the word ok."}]}],
    "generationConfig": {"maxOutputTokens": 16},
}

# How long an answer holds before Google is asked again. A free key is asked more often
# because the change worth catching early is billing being turned on.
_FRESH = {FREE: timedelta(hours=6), PAID: timedelta(hours=24)}
# After a probe that couldn't tell (an outage, a bad key), when to try again.
_RETRY_UNKNOWN = timedelta(minutes=15)

_known: dict[str, tuple[str, datetime]] = {}  # key fingerprint -> (tier, when Google said)
_unknown_at: dict[str, datetime] = {}  # key fingerprint -> when a probe last couldn't tell
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None
_refreshing: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _the_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _remember(kid: str, tier: str, at: datetime) -> None:
    _known[kid] = (tier, at)
    _unknown_at.pop(kid, None)
    get_rate_limiter().set_paid(kid, tier == PAID)


async def probe(key: str) -> str | None:
    """Ask Google which tier ``key`` is on: ``"free"``, ``"paid"``, or None when it can't
    tell (no connection, a key Google rejects, or every probe model gone)."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for model in _PROBE_MODELS:
                resp = await client.post(
                    _URL.format(model), json=_PROBE_BODY, headers={"x-goog-api-key": key}
                )
                if resp.status_code == 200:
                    return PAID
                if resp.status_code == 429:
                    # A billed key can be throttled too; only a free-tier quota says free.
                    return FREE if "FreeTier" in resp.text else PAID
                if resp.status_code in (400, 404):
                    # The model is gone, or won't take this request shape: neither says
                    # anything about the key. A key Google rejects 400s on both.
                    continue
                return None
    except httpx.HTTPError as exc:
        log.info("couldn't ask Google which tier the Gemini key is on: %s", exc)
    return None


def known(kid: str | None = None) -> str | None:
    """The tier Google last gave for the key in use (or the key with fingerprint ``kid``),
    without asking. None before it's known."""
    kid = kid if kid is not None else get_rate_limiter().key
    entry = _known.get(kid) if kid else None
    return entry[0] if entry else None


def checked_at(kid: str | None = None) -> datetime | None:
    kid = kid if kid is not None else get_rate_limiter().key
    entry = _known.get(kid) if kid else None
    return entry[1] if entry else None


def is_paid() -> bool:
    """Whether the key in use has billing on, as far as Olisar knows."""
    return known() == PAID


def _stale(kid: str) -> bool:
    entry = _known.get(kid)
    now = _now()
    if entry is None:
        failed = _unknown_at.get(kid)
        return failed is None or now - failed >= _RETRY_UNKNOWN
    return now - entry[1] >= _FRESH[entry[0]]


async def _store(kid: str, tier: str, at: datetime) -> None:
    try:
        async with session_scope() as session:
            row = await session.get(GeminiKeyTier, kid)
            if row is None:
                session.add(GeminiKeyTier(key_id=kid, tier=tier, checked_at=at))
            else:
                row.tier, row.checked_at = tier, at
    except Exception:
        log.exception("couldn't save the Gemini key's tier")


async def check(key: str) -> str | None:
    """The tier of ``key``: Google's last answer while it's fresh, else a new one. For the
    key in use and for a key typed into the console alike."""
    kid = key_id(key)
    if kid is None:
        return None
    if not _stale(kid):
        return known(kid)
    async with _the_lock():
        if not _stale(kid):
            return known(kid)
        tier = await probe(key)
        now = _now()
        if tier is None:
            _unknown_at[kid] = now
            return known(kid)
        before = known(kid)
        _remember(kid, tier, now)
    if before != tier:
        log.warning(
            "the Gemini key %s", "has billing on" if tier == PAID else "is on the free tier"
        )
    await _store(kid, tier, now)
    return tier


async def current() -> str | None:
    """The tier of the key in use, asking Google when the last answer has gone stale."""
    key = await runtime_keys.gemini_api_key()
    return await check(key) if key else None


def refresh_soon() -> None:
    """Make sure the key in use has a fresh answer, without waiting for it. Cheap enough to
    call before every request: it only starts a probe when one is due."""
    global _refreshing
    kid = get_rate_limiter().key
    if kid is None or not _stale(kid):
        return
    if _refreshing is not None and not _refreshing.done():
        return
    try:
        _refreshing = asyncio.get_running_loop().create_task(current(), name="olisar-gemini-tier")
    except RuntimeError:
        pass


def saw_free_tier(kid: str | None) -> None:
    """Google named a ``-FreeTier`` quota refusing the key with fingerprint ``kid``, which
    settles it without a probe."""
    if not kid or known(kid) == FREE and not _stale(kid):
        return
    was = known(kid)
    now = _now()
    _remember(kid, FREE, now)
    if was == PAID:
        log.warning("the Gemini key is on the free tier again (Google named a free-tier quota)")
    try:
        asyncio.get_running_loop().create_task(_store(kid, FREE, now))
    except RuntimeError:
        pass


def recheck(kid: str | None = None) -> None:
    """Forget the last answer so the next request asks again. For when something suggests
    it changed: a model Google refused for the day took a request, which is what turning
    billing on looks like."""
    kid = kid if kid is not None else get_rate_limiter().key
    entry = _known.get(kid) if kid else None
    if entry is not None and entry[0] == FREE:
        _known[kid] = (FREE, datetime.min.replace(tzinfo=timezone.utc))
        refresh_soon()


async def restore() -> int:
    """Load every answer Google has given, so the tier is known from the first request
    after a restart. Returns how many."""
    async with session_scope() as session:
        rows = (await session.scalars(select(GeminiKeyTier))).all()
    for row in rows:
        _remember(row.key_id, row.tier, _aware(row.checked_at))
    return len(rows)
