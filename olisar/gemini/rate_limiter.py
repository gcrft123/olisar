"""Rate limiting + usage accounting for Gemini.

Two cooperating mechanisms:
* **Proactive RPM throttle** — a per-model sliding window so we rarely trip the
  API's limits in the first place.
* **Reactive cooldown** — when the API returns 429 for a model, it's parked for
  a short while so the client falls back to the next-best model (see models.py)
  instead of hammering the limited one. A 429 that says the day's quota is spent
  parks the model until Google's reset instead (``mark_spent``).

The client uses `state()` + `reserve()` + `penalize()` for the fallback chain;
single-model callers (embeddings, search) use the blocking `acquire()`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone

from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import GeminiUsage, UsageDay, UsageHour, UsageMinutePeak, UsageSource
from olisar.gemini.models import RANKED_NAMES, rpm_for
from olisar.gemini.quota import aware, next_reset, quota_day, quota_hour

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
        # Models Google has refused for the rest of its day: the quota day and when. This
        # used to be an ordinary two-minute cooldown, so a model that was out for the day
        # was asked again every two minutes all evening, each time a wasted round trip.
        self._spent: dict[str, tuple[date, datetime]] = {}
        # Global (all-model) rolling 60s window of (timestamp, tokens), for peak TPM.
        self._tokens: deque[tuple[float, int]] = deque()

    def _clean(self, model: str, now: float) -> None:
        dq = self._calls[model]
        while dq and now - dq[0] >= 60.0:
            dq.popleft()

    def state(self, model: str) -> str:
        """'ok' | 'spent' | 'cooldown' | 'rpm_full' — without reserving a slot."""
        if self.spent_at(model) is not None:
            return "spent"
        now = time.monotonic()
        if now < self._cooldown_until.get(model, 0.0):
            return "cooldown"
        self._clean(model, now)
        if len(self._calls[model]) >= rpm_for(model):
            return "rpm_full"
        return "ok"

    def spent_at(self, model: str) -> datetime | None:
        """When Google refused ``model`` for the rest of today, or None if it hasn't. A
        refusal from an earlier day has been cleared by the reset."""
        entry = self._spent.get(model)
        if entry is None:
            return None
        if entry[0] != quota_day():
            del self._spent[model]
            return None
        return entry[1]

    def exhaust(self, model: str, at: datetime | None = None) -> None:
        """Park ``model`` until Google's daily reset."""
        at = at or datetime.now(timezone.utc)
        self._spent[model] = (quota_day(at), at)

    def chain_spent(self) -> bool:
        """True when Google has refused every model in the chat chain for the day."""
        return all(self.spent_at(name) is not None for name in RANKED_NAMES)

    def back_in(self, model: str) -> float:
        """Seconds until a resting model can take a request again: the rest of its cooldown,
        or until the oldest call in a full minute ages out. 0 when it can take one now."""
        now = time.monotonic()
        wait = max(0.0, self._cooldown_until.get(model, 0.0) - now)
        self._clean(model, now)
        calls = self._calls[model]
        if len(calls) >= rpm_for(model):
            wait = max(wait, 60.0 - (now - calls[0]))
        return wait

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

    def chat_exhausted(self) -> bool:
        """True when every model in the chat fallback chain is busy or cooling — the
        bot can't answer until one clears. One parked model is normal (the client walks
        the rest of the chain); this is the all-models-gone case."""
        return all(self.state(name) != "ok" for name in RANKED_NAMES)

    def reserve(self, model: str) -> None:
        self._calls[model].append(time.monotonic())

    def penalize(
        self, model: str, seconds: float = COOLDOWN_SECONDS, reason: str = "a rate limit"
    ) -> None:
        self._cooldown_until[model] = time.monotonic() + seconds
        log.info("model %s parked for %.0fs after %s", model, seconds, reason)

    async def acquire(self, model: str) -> None:
        """Block until `model` has a free slot. For single-model callers that
        can't fall back (embeddings, grounded search). Raises RateLimitExceeded rather
        than waiting for the daily reset, which can be most of a day away."""
        while True:
            if self.spent_at(model) is not None:
                raise RateLimitExceeded(model, "daily")
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

    Records four things off the same call: the per-model daily rollup (with the day's
    peak RPM), the per-hour tally the same-time-yesterday comparison reads, the
    per-process request tally (``source``), and the day's peak TPM. The day is Google's
    (see olisar.gemini.quota), so it lines up with the daily limit being counted."""
    try:
        limiter = get_rate_limiter()
        rpm = limiter.current(model)          # this model's instantaneous RPM
        tpm = limiter.record_tokens(tokens)   # global tokens-in-60s after this call
        now = datetime.now(timezone.utc)
        day, hour = quota_day(now), quota_hour(now)
        async with session_scope() as session:
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
                        peak_rpm_at=now,
                    )
                )
            else:
                row.request_count += 1
                row.token_count += tokens
                row.grounding_count += grounding
                if rpm > row.peak_rpm:
                    row.peak_rpm = rpm
                    row.peak_rpm_at = now

            hrow = await session.scalar(
                select(UsageHour).where(
                    UsageHour.day == day, UsageHour.hour == hour, UsageHour.model == model
                )
            )
            if hrow is None:
                session.add(
                    UsageHour(day=day, hour=hour, model=model, request_count=1, token_count=tokens)
                )
            else:
                hrow.request_count += 1
                hrow.token_count += tokens

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


async def mark_spent(model: str, limit: int | None = None) -> None:
    """Google refused ``model`` for the rest of its day: park it until the reset, and keep
    the refusal (and the limit Google named) for the Usage page. Parking happens first and
    can't fail; the record is best-effort."""
    limiter = get_rate_limiter()
    now = datetime.now(timezone.utc)
    limiter.exhaust(model, now)
    log.warning(
        "model %s is out of requests for the day%s; parked until the reset at %s",
        model, f" (Google's limit: {limit})" if limit else "",
        next_reset(now).isoformat(timespec="minutes"),
    )
    day = quota_day(now)
    try:
        async with session_scope() as session:
            row = await session.scalar(
                select(GeminiUsage).where(GeminiUsage.day == day, GeminiUsage.model == model)
            )
            if row is None:
                # Refused before Olisar made a request today: something else on the same
                # Google Cloud project spent the quota.
                row = GeminiUsage(
                    day=day, model=model, request_count=0, token_count=0,
                    grounding_count=0, peak_rpm=0,
                )
                session.add(row)
            if row.exhausted_at is None:
                row.exhausted_at = now
            if limit:
                row.quota_limit = limit
            if model in RANKED_NAMES and limiter.chain_spent():
                log.warning("every model in the chat chain is out for the day")
                marker = await session.get(UsageDay, day)
                if marker is None:
                    session.add(UsageDay(day=day, chain_out_at=now))
                elif marker.chain_out_at is None:
                    marker.chain_out_at = now
    except Exception:
        log.exception("failed to record that %s ran out", model)


async def restore_spent() -> int:
    """Park the models Google already refused today, so a restart doesn't spend a request
    on each of them to be told again. Returns how many."""
    limiter = get_rate_limiter()
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(GeminiUsage).where(
                    GeminiUsage.day == quota_day(), GeminiUsage.exhausted_at.is_not(None)
                )
            )
        ).all()
    for row in rows:
        limiter.exhaust(row.model, aware(row.exhausted_at))
    return len(rows)


_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
