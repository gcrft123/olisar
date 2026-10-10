"""Rate limiting + usage accounting for Gemini.

Two cooperating mechanisms:
* **Proactive RPM throttle** — a per-model sliding window so we rarely trip the
  API's limits in the first place.
* **Reactive cooldown** — when the API returns 429 for a model, it's parked for
  a short while so the client falls back to the next-best model (see models.py)
  instead of hammering the limited one. A 429 that says the day's quota is spent
  parks the model until Google's reset instead (``mark_spent``), for the API key
  Google refused: a different key is a different quota.

The client uses `state()` + `reserve()` + `penalize()` for the fallback chain;
single-model callers (embeddings, search) use the blocking `acquire()`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone

from sqlalchemy import select

from olisar import runtime_keys
from olisar.config import settings
from olisar.db.engine import after_session_scope, session_scope
from olisar.db.models import (
    GeminiUsage,
    Guild,
    GuildConfig,
    UsageDay,
    UsageHour,
    UsageMinutePeak,
    UsageSource,
)
from olisar.gemini import spend
from olisar.gemini.models import RANKED_NAMES, model_chain, rpm_for
from olisar.gemini.quota import aware, next_reset, quota_day, quota_hour

log = logging.getLogger("olisar.gemini.ratelimit")

# How long to avoid a model after it returns 429. Most 429s are per-minute; this
# self-heals while keeping replies fast via fallback.
COOLDOWN_SECONDS = 120.0

# How often a model Google refused for the day is asked again anyway. The refusal holds
# until midnight Pacific on a free-tier key, but turning on billing lifts it at once, and
# without a retry the bot would sit out the rest of the day regardless.
PROBE_SECONDS = 3600.0


def key_id(key: str | None) -> str | None:
    """A short one-way fingerprint of an API key, so a refusal can be tied to the key it
    was for without the key itself being stored anywhere."""
    if not key:
        return None
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def reply_chain(default_model: str | None) -> list[str]:
    """The fallback chain a server replies through: its ``default_model`` (else
    GEMINI_CHAT_MODEL, as the pipeline does) and everything ranked below it."""
    return model_chain(default_model or settings.gemini_chat_model)


async def chains_in_use(session) -> dict[int, list[str]]:
    """Each active server's reply chain, by guild id."""
    rows = (
        await session.execute(
            select(Guild.id, GuildConfig.default_model)
            .outerjoin(GuildConfig, GuildConfig.guild_id == Guild.id)
            .where(Guild.active.is_(True))
        )
    ).all()
    return {gid: reply_chain(model) for gid, model in rows}


def union_chain(chains) -> list[str]:
    """Every model any of ``chains`` replies through, best first. The bot is out of
    requests only when all of them are: each server's chain is out exactly when every
    model in it is. With no servers, the default chain."""
    seen: dict[str, None] = {}
    for chain in chains:
        seen.update(dict.fromkeys(chain))
    if not seen:
        return reply_chain(None)
    rank = {name: i for i, name in enumerate(RANKED_NAMES)}
    return sorted(seen, key=lambda name: rank.get(name, -1))


class RateLimitExceeded(Exception):
    """Raised when no model in the fallback chain is currently available."""

    def __init__(self, model: str, scope: str) -> None:
        super().__init__(f"{model} {scope} rate limit reached")
        self.model = model
        self.scope = scope


class BudgetSpent(RateLimitExceeded):
    """The month's Gemini budget is spent and the operator chose to stop there. A rate limit
    to everything that calls Gemini: replies get the rate-limit message, background work
    waits, and none of it needs to know why."""

    def __init__(self, model: str) -> None:
        super().__init__(model, "monthly budget")


class RateLimiter:
    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._cooldown_until: dict[str, float] = {}
        # Models Google has refused for the rest of its day: the quota day, when, and the
        # fingerprint of the key it refused. This used to be an ordinary two-minute
        # cooldown, so a model that was out for the day was asked again every two minutes
        # all evening, each time a wasted round trip.
        self._spent: dict[str, tuple[date, datetime, str | None]] = {}
        # When each parked model may be asked again (monotonic); see PROBE_SECONDS.
        self._probe_at: dict[str, float] = {}
        # Fingerprint of the API key requests go out with (see use_key).
        self._key: str | None = None
        # Fingerprints of keys Google says have billing on (olisar.gemini.tier). Their
        # per-minute throttle is the billed one.
        self._paid_keys: set[str] = set()
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
        if len(self._calls[model]) >= rpm_for(model, self.paid):
            return "rpm_full"
        return "ok"

    @property
    def key(self) -> str | None:
        """Fingerprint of the API key in use (``key_id``), None before one is known."""
        return self._key

    @property
    def paid(self) -> bool:
        """Whether the key in use has billing on. False until Google has said so."""
        return self._key is not None and self._key in self._paid_keys

    def set_paid(self, key: str, paid: bool) -> None:
        """Record whether the key with fingerprint ``key`` has billing on."""
        if paid:
            self._paid_keys.add(key)
        else:
            self._paid_keys.discard(key)

    def use_key(self, key: str | None) -> None:
        """Record which key requests go out with. The daily quota belongs to the key's
        Google Cloud project, so a new key un-parks every model the old one ran out on."""
        if key == self._key:
            return
        dropped = [m for m, entry in self._spent.items() if entry[2] != key]
        for model in dropped:
            del self._spent[model]
            self._probe_at.pop(model, None)
        if dropped and self._key is not None:
            log.warning(
                "the Gemini API key changed; %d model(s) parked for the old key can take "
                "requests again", len(dropped),
            )
        self._key = key

    def spent_at(self, model: str) -> datetime | None:
        """When Google refused ``model`` for the rest of today, or None if it hasn't. A
        refusal from an earlier day has been cleared by the reset, and one for another key
        says nothing about this one."""
        entry = self._spent.get(model)
        if entry is None:
            return None
        if entry[0] != quota_day() or entry[2] != self._key:
            del self._spent[model]
            self._probe_at.pop(model, None)
            return None
        return entry[1]

    def exhaust(self, model: str, at: datetime | None = None) -> None:
        """Park ``model`` until Google's daily reset, or until it's next worth asking again
        (``claim_probe``). Refusing a model that's already parked keeps when it first ran
        out and only pushes the next retry back."""
        at = at or datetime.now(timezone.utc)
        since = (datetime.now(timezone.utc) - at).total_seconds()
        self._probe_at[model] = time.monotonic() + max(0.0, PROBE_SECONDS - since)
        first = self.spent_at(model)
        if first is not None and quota_day(first) == quota_day(at):
            at = min(at, first)
        self._spent[model] = (quota_day(at), at, self._key)

    def claim_probe(self, model: str) -> bool:
        """Whether to spend one request asking Google again about a model it refused for the
        day: true about once an hour per model, so billing turned on this afternoon is
        noticed this afternoon. Claiming pushes the next one back an hour, so a burst of
        replies sends one request, not one each."""
        if self.spent_at(model) is None:
            return False
        now = time.monotonic()
        if now < self._probe_at.get(model, 0.0):
            return False
        self._probe_at[model] = now + PROBE_SECONDS
        return True

    def recover(self, model: str) -> bool:
        """Google took a request for ``model``, so it isn't out after all. True if it was
        parked."""
        self._probe_at.pop(model, None)
        return self._spent.pop(model, None) is not None

    def chain_spent(self, chain: list[str] | None = None) -> bool:
        """True when Google has refused every model in ``chain`` for the day. Pass the
        chain the bot actually replies through (``union_chain(chains_in_use(...))``); the
        default is the whole ranking."""
        return all(self.spent_at(name) is not None for name in chain or RANKED_NAMES)

    def back_in(self, model: str) -> float:
        """Seconds until a resting model can take a request again: the rest of its cooldown,
        or until the oldest call in a full minute ages out. 0 when it can take one now."""
        now = time.monotonic()
        wait = max(0.0, self._cooldown_until.get(model, 0.0) - now)
        self._clean(model, now)
        calls = self._calls[model]
        if len(calls) >= rpm_for(model, self.paid):
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

    def chat_exhausted(self, chain: list[str] | None = None) -> bool:
        """True when every model in the chat fallback chain is busy or cooling — the
        bot can't answer until one clears. One parked model is normal (the client walks
        the rest of the chain); this is the all-models-gone case. ``chain`` as for
        ``chain_spent``."""
        return all(self.state(name) != "ok" for name in chain or RANKED_NAMES)

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
            if self.spent_at(model) is not None and not self.claim_probe(model):
                raise RateLimitExceeded(model, "daily")
            now = time.monotonic()
            cooldown = self._cooldown_until.get(model, 0.0)
            if now < cooldown:
                await asyncio.sleep(min(cooldown - now, 5.0))
                continue
            self._clean(model, now)
            dq = self._calls[model]
            if len(dq) >= rpm_for(model, self.paid):
                await asyncio.sleep(max(60.0 - (now - dq[0]) + 0.05, 0.1))
                continue
            dq.append(time.monotonic())
            return


class _UsageBuffer:
    """Usage counted but not yet written, summed per row it will land in."""

    def __init__(self) -> None:
        self.models: dict[tuple[date, str], dict] = {}
        self.hours: dict[tuple[date, int, str], list[int]] = {}
        self.sources: dict[tuple[date, str], int] = {}
        self.peak_tpm: dict[date, int] = {}

    def __bool__(self) -> bool:
        return bool(self.models or self.hours or self.sources or self.peak_tpm)

    def add(self, *, day: date, hour: int, model: str, tokens: int, grounding: int,
            source: str, rpm: int, rpm_at: datetime, tpm: int, key: str | None,
            input_tokens: int = 0, output_tokens: int = 0) -> None:
        m = self.models.setdefault((day, model), {
            "requests": 0, "tokens": 0, "input": 0, "output": 0, "grounding": 0,
            "peak_rpm": 0, "peak_rpm_at": rpm_at, "key": key,
        })
        m["requests"] += 1
        m["tokens"] += tokens
        m["input"] += input_tokens
        m["output"] += output_tokens
        m["grounding"] += grounding
        m["key"] = key
        if rpm > m["peak_rpm"]:
            m["peak_rpm"], m["peak_rpm_at"] = rpm, rpm_at
        h = self.hours.setdefault((day, hour, model), [0, 0])
        h[0] += 1
        h[1] += tokens
        self.sources[(day, source)] = self.sources.get((day, source), 0) + 1
        self.peak_tpm[day] = max(self.peak_tpm.get(day, 0), tpm)

    def merge(self, other: "_UsageBuffer") -> None:
        """Add ``other``, counted after this, into this."""
        for k, m in other.models.items():
            mine = self.models.get(k)
            if mine is None:
                self.models[k] = m
                continue
            for f in ("requests", "tokens", "input", "output", "grounding"):
                mine[f] += m[f]
            mine["key"] = m["key"]
            if m["peak_rpm"] > mine["peak_rpm"]:
                mine["peak_rpm"], mine["peak_rpm_at"] = m["peak_rpm"], m["peak_rpm_at"]
        for k, (n, t) in other.hours.items():
            h = self.hours.setdefault(k, [0, 0])
            h[0] += n
            h[1] += t
        for k, n in other.sources.items():
            self.sources[k] = self.sources.get(k, 0) + n
        for k, t in other.peak_tpm.items():
            self.peak_tpm[k] = max(self.peak_tpm.get(k, 0), t)

    async def write(self, session) -> None:
        for (day, model), m in self.models.items():
            row = await session.scalar(
                select(GeminiUsage).where(GeminiUsage.day == day, GeminiUsage.model == model)
            )
            if row is None:
                session.add(GeminiUsage(
                    day=day, model=model, request_count=m["requests"], token_count=m["tokens"],
                    input_tokens=m["input"], output_tokens=m["output"],
                    grounding_count=m["grounding"], peak_rpm=m["peak_rpm"], peak_rpm_at=m["peak_rpm_at"],
                ))
                continue
            row.request_count += m["requests"]
            row.token_count += m["tokens"]
            row.input_tokens = (row.input_tokens or 0) + m["input"]
            row.output_tokens = (row.output_tokens or 0) + m["output"]
            row.grounding_count += m["grounding"]
            if m["peak_rpm"] > row.peak_rpm:
                row.peak_rpm = m["peak_rpm"]
                row.peak_rpm_at = m["peak_rpm_at"]
            if row.exhausted_at is not None and row.exhausted_key == m["key"]:
                # Google took a request after refusing this key for the day (billing
                # turned on, most likely), so a restart mustn't park it again.
                row.exhausted_at = None
                row.exhausted_key = None
        for (day, hour, model), (n, tokens) in self.hours.items():
            hrow = await session.scalar(
                select(UsageHour).where(
                    UsageHour.day == day, UsageHour.hour == hour, UsageHour.model == model
                )
            )
            if hrow is None:
                session.add(UsageHour(day=day, hour=hour, model=model, request_count=n, token_count=tokens))
            else:
                hrow.request_count += n
                hrow.token_count += tokens
        for (day, source), n in self.sources.items():
            srow = await session.scalar(
                select(UsageSource).where(UsageSource.day == day, UsageSource.source == source)
            )
            if srow is None:
                session.add(UsageSource(day=day, source=source, request_count=n))
            else:
                srow.request_count += n
        for day, tpm in self.peak_tpm.items():
            peak = await session.get(UsageMinutePeak, day)
            if peak is None:
                session.add(UsageMinutePeak(day=day, peak_tpm=tpm))
            elif tpm > peak.peak_tpm:
                peak.peak_tpm = tpm


_pending_usage = _UsageBuffer()
_flush_lock: asyncio.Lock | None = None
_flush_lock_loop: asyncio.AbstractEventLoop | None = None
_retry: asyncio.Task | None = None
USAGE_RETRY_SECONDS = 5.0


def _lock() -> asyncio.Lock:
    global _flush_lock, _flush_lock_loop
    loop = asyncio.get_running_loop()
    if _flush_lock is None or _flush_lock_loop is not loop:
        _flush_lock, _flush_lock_loop = asyncio.Lock(), loop
    return _flush_lock


def pending_grounding(day: date) -> int:
    """Web searches counted for ``day`` that haven't been written yet."""
    return sum(m["grounding"] for (d, _), m in _pending_usage.models.items() if d == day)


async def flush_usage() -> None:
    """Write the usage counted so far. One write at a time, so concurrent calls can't lose
    each other's increments; a batch that fails to write is kept and tried again."""
    global _pending_usage
    async with _lock():
        if not _pending_usage:
            return
        batch, _pending_usage = _pending_usage, _UsageBuffer()
        try:
            async with session_scope() as session:
                await batch.write(session)
        except Exception:
            batch.merge(_pending_usage)
            _pending_usage = batch
            log.exception("failed to record gemini usage; trying again shortly")
            _retry_soon()


def _retry_soon() -> None:
    global _retry
    if _retry is not None and not _retry.done():
        return

    async def later() -> None:
        await asyncio.sleep(USAGE_RETRY_SECONDS)
        await flush_usage()

    _retry = asyncio.get_running_loop().create_task(later(), name="olisar-usage-retry")


async def record_usage(
    model: str, tokens: int, grounding: int = 0, source: str = "other", *,
    input_tokens: int = 0, output_tokens: int = 0, images: int = 0,
) -> None:
    """Persist per-day usage for the dashboard. Best-effort — never blocks a reply.

    Records four things off the same call: the per-model daily rollup (with the day's
    peak RPM), the per-hour tally the same-time-yesterday comparison reads, the
    per-process request tally (``source``), and the day's peak TPM. The day is Google's
    (see olisar.gemini.quota), so it lines up with the daily limit being counted.

    ``input_tokens`` and ``output_tokens`` are ``tokens`` split the way Google prices them,
    and ``images`` the images the request made; together they're what the request cost
    (olisar.gemini.spend), which the budget counts against.

    Called from inside a ``session_scope`` (a reply, a background job), the write waits
    until that scope has ended: its session may hold SQLite's write lock, and writing on
    another connection meanwhile would wait out busy_timeout behind the caller's own lock,
    then fail."""
    try:
        limiter = get_rate_limiter()
        if limiter.recover(model):
            log.warning("model %s is taking requests again", model)
        rpm = limiter.current(model)          # this model's instantaneous RPM
        tpm = limiter.record_tokens(tokens)   # global tokens-in-60s after this call
        now = datetime.now(timezone.utc)
        day = quota_day(now)
        _pending_usage.add(
            day=day, hour=quota_hour(now), model=model, tokens=tokens,
            grounding=grounding, source=source, rpm=rpm, rpm_at=now, tpm=tpm, key=limiter.key,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
        spend.add(
            model, day, input_tokens=input_tokens + max(0, tokens - input_tokens - output_tokens),
            output_tokens=output_tokens, images=images, grounding=grounding,
        )
    except Exception:
        log.exception("failed to record gemini usage")
        return
    if not after_session_scope(flush_usage):
        await flush_usage()


async def mark_spent(model: str, limit: int | None = None, *, key: str | None = None) -> None:
    """Google refused ``model`` for the rest of its day: park it until the reset, and keep
    the refusal (and the limit Google named) for the Usage page. Parking happens first and
    can't fail; the record is best-effort.

    ``key`` is the fingerprint (``key_id``) of the key the refused request went out with,
    by default the one in use. A refusal for a key that has since been replaced is
    recorded but parks nothing."""
    limiter = get_rate_limiter()
    kid = key if key is not None else limiter.key
    now = datetime.now(timezone.utc)
    if kid == limiter.key:
        again = limiter.spent_at(model) is not None
        limiter.exhaust(model, now)
        if again:
            log.info("model %s is still out of requests for today; asking again in an hour", model)
        else:
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
            if row.exhausted_at is None or row.exhausted_key != kid:
                row.exhausted_at = now
                row.exhausted_key = kid
            if limit:
                row.quota_limit = limit
            # The chain the servers reply through, not the whole ranking: a server that
            # starts lower down is out of requests once its own models are.
            chain = union_chain((await chains_in_use(session)).values())
            if model in chain and limiter.chain_spent(chain):
                log.warning("every model in the chat chain is out for the day")
                marker = await session.get(UsageDay, day)
                if marker is None:
                    session.add(UsageDay(day=day, chain_out_at=now))
                elif marker.chain_out_at is None:
                    marker.chain_out_at = now
    except Exception:
        log.exception("failed to record that %s ran out", model)


async def current_key() -> str | None:
    """Fingerprint of the Gemini key in effect now (the dashboard's, else .env's), made the
    limiter's, so a key pasted into the dashboard un-parks the old key's models at once."""
    kid = key_id(await runtime_keys.gemini_api_key())
    get_rate_limiter().use_key(kid)
    return kid


async def restore_spent() -> int:
    """Park the models Google already refused today for the key in use, so a restart
    doesn't spend a request on each of them to be told again. A refusal for another key, or
    one recorded before refusals carried their key, is left out: that quota says nothing
    about this key's. Returns how many."""
    kid = await current_key()
    if kid is None:
        return 0
    limiter = get_rate_limiter()
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(GeminiUsage).where(
                    GeminiUsage.day == quota_day(),
                    GeminiUsage.exhausted_at.is_not(None),
                    GeminiUsage.exhausted_key == kid,
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
