"""Rate limiting + usage accounting for Gemini.

Two cooperating mechanisms:
* **Proactive RPM throttle** — a per-model sliding window so we rarely trip the
  API's limits in the first place.
* **Reactive cooldown** — when the API returns 429 for a model, it's parked for
  a short while so the client falls back to the next-best model (see models.py)
  instead of hammering the limited one.

The client uses `state()` + `reserve()` + `penalize()` for the fallback chain;
single-model callers (embeddings, search) use the blocking `acquire()`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import GeminiUsage
from olisar.gemini.models import rpm_for

log = logging.getLogger("olisar.gemini.ratelimit")

# How long to avoid a model after it returns 429. Most free-tier 429s are
# per-minute; this self-heals while keeping replies fast via fallback.
COOLDOWN_SECONDS = 120.0


class RateLimitExceeded(Exception):
    """Raised when no model in the fallback chain is currently available."""

    def __init__(self, model: str, scope: str) -> None:
        super().__init__(f"{model} {scope} rate limit reached")
        self.model = model
        self.scope = scope


class RateLimiter:
    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._cooldown_until: dict[str, float] = {}

    def _clean(self, model: str, now: float) -> None:
        dq = self._calls[model]
        while dq and now - dq[0] >= 60.0:
            dq.popleft()

    def state(self, model: str) -> str:
        """'ok' | 'cooldown' | 'rpm_full' — without reserving a slot."""
        now = time.monotonic()
        if now < self._cooldown_until.get(model, 0.0):
            return "cooldown"
        self._clean(model, now)
        if len(self._calls[model]) >= rpm_for(model):
            return "rpm_full"
        return "ok"

    def reserve(self, model: str) -> None:
        self._calls[model].append(time.monotonic())

    def penalize(
        self, model: str, seconds: float = COOLDOWN_SECONDS, reason: str = "a rate limit"
    ) -> None:
        self._cooldown_until[model] = time.monotonic() + seconds
        log.info("model %s parked for %.0fs after %s", model, seconds, reason)

    async def acquire(self, model: str) -> None:
        """Block until `model` has a free slot. For single-model callers that
        can't fall back (embeddings, grounded search)."""
        while True:
            now = time.monotonic()
            cooldown = self._cooldown_until.get(model, 0.0)
            if now < cooldown:
                await asyncio.sleep(min(cooldown - now, 5.0))
                continue
            self._clean(model, now)
            dq = self._calls[model]
            if len(dq) >= rpm_for(model):
                await asyncio.sleep(max(60.0 - (now - dq[0]) + 0.05, 0.1))
                continue
            dq.append(time.monotonic())
            return


async def record_usage(model: str, tokens: int, grounding: int = 0) -> None:
    """Persist per-day usage for the dashboard. Best-effort — never blocks a reply."""
    try:
        async with session_scope() as session:
            day = datetime.now(timezone.utc).date()
            row = await session.scalar(
                select(GeminiUsage).where(
                    GeminiUsage.day == day, GeminiUsage.model == model
                )
            )
            if row is None:
                session.add(
                    GeminiUsage(
                        day=day,
                        model=model,
                        request_count=1,
                        token_count=tokens,
                        grounding_count=grounding,
                    )
                )
            else:
                row.request_count += 1
                row.token_count += tokens
                row.grounding_count += grounding
    except Exception:
        log.exception("failed to record gemini usage")


_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
