"""How fast a free key is spending its Flash allowance today.

A free key's Flash rungs each allow a few hundred requests a day, and once they're spent
every reply comes from Flash-Lite until midnight Pacific. With routing on, the light tier
already takes the easy turns off Flash; when the day is on pace to run Flash out anyway,
the router also lets borderline turns go light (olisar.routing.policy), so the Flash that's
left goes to the turns that need it.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import GeminiUsage
from olisar.gemini import rate_limiter
from olisar.gemini.models import quota_model, rpd_for
from olisar.gemini.quota import day_start, quota_day

log = logging.getLogger("olisar.routing")

_TTL = 60.0
_cache: tuple[float, tuple[str, ...], float] | None = None
# Before this share of the day has gone by, the pace is measured as if it had: a handful of
# requests just after midnight would otherwise project a day that runs out by breakfast.
_MIN_ELAPSED = 2 / 24


def _flash(chain: list[str]) -> list[str]:
    return [m for m in chain if "flash-lite" not in m and "flash" in m]


async def flash_pressure(chain: list[str], now: datetime | None = None) -> float:
    """Where the Flash rungs of ``chain`` are heading by the reset, as a share of their
    allowance: 1.0 runs out right at midnight Pacific, 2.0 halfway through the day. 0 when
    there's nothing to measure. Read at most once a minute."""
    global _cache
    flash = _flash(chain)
    mono = time.monotonic()
    if _cache is not None and _cache[1] == tuple(flash) and mono - _cache[0] < _TTL:
        return _cache[2]
    now = now or datetime.now(timezone.utc)
    day = quota_day(now)
    used: dict[str, int] = {m: 0 for m in flash}
    limits: dict[str, int] = {m: rpd_for(m) for m in flash}
    spent: set[str] = set()
    try:
        async with session_scope() as session:
            rows = (
                await session.scalars(
                    select(GeminiUsage).where(GeminiUsage.day == day, GeminiUsage.model.in_(flash))
                )
            ).all()
        for row in rows:
            used[row.model] += row.request_count or 0
        # Google's own figure, the last time it named one: its published free-tier limits are
        # far above what it grants some projects (20 a day on 3.8 Flash, against 250), and
        # waiting for today's refusal to learn that would pace most of the day on fiction.
        async with session_scope() as session:
            named = (
                await session.execute(
                    select(GeminiUsage.model, GeminiUsage.quota_limit)
                    .where(GeminiUsage.model.in_(flash), GeminiUsage.quota_limit.is_not(None))
                    .order_by(GeminiUsage.day)
                )
            ).all()
        for model, limit in named:
            limits[model] = limit
        for (d, model), m in rate_limiter._pending_usage.models.items():
            if d == day and model in used:
                used[model] += m["requests"]
        limiter = rate_limiter.get_rate_limiter()
        spent = {m for m in flash if limiter.spent_at(m) is not None}
    except Exception:
        log.exception("couldn't read today's Flash usage; pacing as if it were untouched")
        return 0.0
    # One allowance per model Google counts against: a -latest alias draws on the model it
    # points to, so the two are one allowance, not two.
    buckets: dict[str, list[str]] = {}
    for m in flash:
        buckets.setdefault(quota_model(m), []).append(m)
    allowance = taken = 0
    for owner, members in buckets.items():
        limit = limits.get(owner) or max(limits[m] for m in members)
        allowance += limit
        out = any(m in spent for m in members)
        taken += limit if out else min(sum(used[m] for m in members), limit)
    if allowance <= 0:
        return 0.0
    elapsed = (now - day_start(day)).total_seconds() / 86400
    pressure = (taken / allowance) / max(elapsed, _MIN_ELAPSED)
    _cache = (mono, tuple(flash), pressure)
    return pressure


def light_threshold(strict: float, loose: float, pressure: float) -> float:
    """The light tier's threshold for a free key at ``pressure``: the strict one while the
    day is on pace to keep some Flash, sliding to the loose one as it heads past the end."""
    if pressure <= 0.8:
        return strict
    if pressure >= 1.2:
        return loose
    return strict + (loose - strict) * (pressure - 0.8) / 0.4


def reset() -> None:
    global _cache
    _cache = None
