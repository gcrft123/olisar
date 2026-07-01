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
from olisar.db.models import GeminiUsage, UsageMinutePeak, UsageSource
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
        # Global (all-model) rolling 60s window of (timestamp, tokens), for peak TPM.
        self._tokens: deque[tuple[float, int]] = deque()

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

    def current(self, model: str) -> int:
        """Requests made against ``model`` in the last 60s — its instantaneous RPM."""
        now = time.monotonic()
        self._clean(model, now)
        return len(self._calls[model])

    def record_tokens(self, tokens: int) -> int:
        """Add a call's tokens to the global 60s window and return the current sum
        (tokens-per-minute right now), for peak-TPM accounting."""
        now = time.monotonic()
        self._tokens.append((now, tokens))
        while self._tokens and now - self._tokens[0][0] >= 60.0:
            self._tokens.popleft()
        return sum(t for _, t in self._tokens)

    def snapshot(self) -> list[dict]:
        """Live per-model RPM for the dashboard — only models that are active or cooling
        down. Read directly by the API (bot + API share this process, so this singleton is
        the live source of truth)."""
        now = time.monotonic()
        out: list[dict] = []
        for model in list(self._calls.keys()):
            self._clean(model, now)
            used = len(self._calls[model])
            cooling = self._cooldown_until.get(model, 0.0) > now
            if used or cooling:
                out.append(
                    {"model": model, "rpm": used, "cap": rpm_for(model), "cooldown": cooling}
                )
        out.sort(key=lambda r: r["rpm"] / max(r["cap"], 1), reverse=True)
        return out

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


async def record_usage(
    model: str, tokens: int, grounding: int = 0, source: str = "other"
) -> None:
    """Persist per-day usage for the dashboard. Best-effort — never blocks a reply.

    Records three things off the same call: the per-model daily rollup (with the day's
    peak RPM), the per-process request tally (``source``), and the day's peak TPM."""
    try:
        limiter = get_rate_limiter()
        rpm = limiter.current(model)          # this model's instantaneous RPM
        tpm = limiter.record_tokens(tokens)   # global tokens-in-60s after this call
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
                        peak_rpm=rpm,
                    )
                )
            else:
                row.request_count += 1
                row.token_count += tokens
                row.grounding_count += grounding
                if rpm > row.peak_rpm:
                    row.peak_rpm = rpm

            srow = await session.scalar(
                select(UsageSource).where(
                    UsageSource.day == day, UsageSource.source == source
                )
            )
            if srow is None:
                session.add(UsageSource(day=day, source=source, request_count=1))
            else:
                srow.request_count += 1

            peak = await session.get(UsageMinutePeak, day)
            if peak is None:
                session.add(UsageMinutePeak(day=day, peak_tpm=tpm))
            elif tpm > peak.peak_tpm:
                peak.peak_tpm = tpm
    except Exception:
        log.exception("failed to record gemini usage")


_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
