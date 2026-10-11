"""What model routing remembers between replies.

All of it in memory except the daily rollup (``usage_route``), and none of it message text:

* the last 24 hours of routed replies, for the share of them allowed on Pro;
* each member's Pro replies today, for the per-member cap;
* the tier of the last reply to each member in each channel, so a follow-up to a deep answer
  isn't routed below it;
* the routing switches (free, paid, and whether deep asks Pro), read from the database every
  few seconds.

A restart forgets the first three. That errs cheap: the Pro share then counts from zero, so
only the floor of DEEP_DAILY_FLOOR Pro replies is available until replies build back up.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from sqlalchemy import select

from olisar.db.engine import after_session_scope, session_scope
from olisar.db.models import AppConfig, UsageRoute
from olisar.gemini.quota import quota_day

log = logging.getLogger("olisar.routing")

# The Pro share: at most this fraction of the last 24 hours' routed replies, but always at
# least DEEP_DAILY_FLOOR of them, so a quiet server can still use Pro at all.
DEEP_SHARE = 0.05
DEEP_DAILY_FLOOR = 3
# Pro replies per member per quota day, so one person asking hard questions on purpose can't
# spend the owner's money.
DEEP_PER_MEMBER = 10
# How long a reply's tier floors the next message from the same member in the same channel.
FOLLOW_UP_SECONDS = 600.0
_FOLLOW_UP_MAX = 4096
_WINDOW = 24 * 3600.0
_SETTINGS_TTL = 10.0


@dataclass(frozen=True)
class Switches:
    free: bool
    paid: bool
    pro: bool = True  # the deep tier asks Pro first, on a billed key


_recent: deque[tuple[float, bool]] = deque()  # (monotonic time, went to Pro)
_member_pro: dict[tuple[object, int], int] = {}  # (quota day, user id) -> Pro replies
_reserved = 0  # Pro replies routed but not finished
_member_reserved: dict[int, int] = {}  # user id -> Pro replies routed but not finished
_follow_up: OrderedDict[tuple[int, int], tuple[str, float]] = OrderedDict()
_DEFAULT = Switches(free=False, paid=True, pro=True)
_switches = _DEFAULT
_switches_at = -_SETTINGS_TTL


async def switches() -> Switches:
    """The routing switches, read at most every few seconds. A failed read keeps the last
    answer, and before any read the defaults (off free, on paid)."""
    global _switches, _switches_at
    now = time.monotonic()
    if now - _switches_at < _SETTINGS_TTL:
        return _switches
    _switches_at = now
    try:
        async with session_scope() as session:
            row = await session.get(AppConfig, 1)
        _switches = (
            Switches(
                free=bool(row.routing_free), paid=bool(row.routing_paid),
                pro=bool(row.routing_pro),
            )
            if row is not None else _DEFAULT
        )
    except Exception:
        log.exception("reading the routing switches failed; keeping the last ones")
    return _switches


def cached_switches() -> Switches:
    """The switches as last read, without reading again: for code that can't wait."""
    return _switches


def invalidate() -> None:
    """Re-read the switches on next use (after the console saves them)."""
    global _switches_at
    _switches_at = -_SETTINGS_TTL


def _trim(now: float) -> None:
    while _recent and now - _recent[0][0] >= _WINDOW:
        _recent.popleft()


def pro_allowed(user_id: int) -> tuple[bool, str]:
    """Whether one more reply may go to Pro, and the cap that said no ("" when none did).
    Pro replies still being written count: a Pro reply takes 10 to 30 seconds, and counting
    only finished ones let a burst of deep questions all clear the caps at once."""
    now = time.monotonic()
    _trim(now)
    pro = sum(1 for _, p in _recent if p) + _reserved
    # Counting the reply being routed: 5% of the replies including this one.
    allowed = max(DEEP_DAILY_FLOOR, int(DEEP_SHARE * (len(_recent) + _reserved + 1)))
    if pro >= allowed:
        return False, "share"
    day = quota_day()
    if user_id and _member_pro.get((day, user_id), 0) + _member_reserved.get(user_id, 0) >= DEEP_PER_MEMBER:
        return False, "member"
    return True, ""


def reserve_pro(user_id: int) -> None:
    """Hold a Pro slot for a reply being routed there, until ``release_pro``."""
    global _reserved
    _reserved += 1
    if user_id:
        _member_reserved[user_id] = _member_reserved.get(user_id, 0) + 1


def release_pro(user_id: int) -> None:
    """Give back a slot ``reserve_pro`` held. The reply is counted (or not) by note_reply."""
    global _reserved
    _reserved = max(0, _reserved - 1)
    if user_id and _member_reserved.get(user_id):
        _member_reserved[user_id] -= 1
        if not _member_reserved[user_id]:
            del _member_reserved[user_id]


def note_reply(user_id: int, channel_id: int, tier: str, pro: bool) -> None:
    """Count a routed reply: toward the Pro share, the member's Pro replies, and the floor
    for their next message here."""
    now = time.monotonic()
    _trim(now)
    _recent.append((now, pro))
    day = quota_day()
    if pro and user_id:
        _member_pro[(day, user_id)] = _member_pro.get((day, user_id), 0) + 1
        for key in [k for k in _member_pro if k[0] != day]:
            del _member_pro[key]
    if user_id and channel_id:
        _follow_up[(channel_id, user_id)] = (tier, now)
        _follow_up.move_to_end((channel_id, user_id))
        while len(_follow_up) > _FOLLOW_UP_MAX:
            _follow_up.popitem(last=False)


def follow_up_tier(user_id: int, channel_id: int) -> str | None:
    """The tier of Olisar's last reply to this member in this channel, if it was recent."""
    entry = _follow_up.get((channel_id, user_id)) if user_id and channel_id else None
    if entry is None or time.monotonic() - entry[1] > FOLLOW_UP_SECONDS:
        return None
    return entry[0]


def reset() -> None:
    """Forget everything in memory (for tests)."""
    global _switches, _switches_at, _reserved
    _switches = _DEFAULT
    _reserved = 0
    _member_reserved.clear()
    _recent.clear()
    _member_pro.clear()
    _follow_up.clear()
    _switches_at = -_SETTINGS_TTL


# ─── The daily rollup ─────────────────────────────────────────────────────────────

_pending: dict[tuple[object, str], list[float]] = {}  # (day, tier) -> [replies, escalated, capped, cost, standard]
_flush_lock: asyncio.Lock | None = None
_flush_loop: asyncio.AbstractEventLoop | None = None


def _lock() -> asyncio.Lock:
    global _flush_lock, _flush_loop
    loop = asyncio.get_running_loop()
    if _flush_lock is None or _flush_loop is not loop:
        _flush_lock, _flush_loop = asyncio.Lock(), loop
    return _flush_lock


async def flush() -> None:
    """Write the counted replies. A batch that fails is kept for the next flush."""
    global _pending
    async with _lock():
        if not _pending:
            return
        batch, _pending = _pending, {}
        try:
            async with session_scope() as session:
                for (day, tier), (n, esc, capped, cost, std) in batch.items():
                    row = await session.scalar(
                        select(UsageRoute).where(UsageRoute.day == day, UsageRoute.tier == tier)
                    )
                    if row is None:
                        session.add(UsageRoute(
                            day=day, tier=tier, replies=int(n), escalated=int(esc),
                            capped=int(capped), cost_usd=cost, standard_usd=std,
                        ))
                    else:
                        row.replies += int(n)
                        row.escalated += int(esc)
                        row.capped += int(capped)
                        row.cost_usd = (row.cost_usd or 0.0) + cost
                        row.standard_usd = (row.standard_usd or 0.0) + std
        except Exception:
            for key, values in batch.items():
                mine = _pending.setdefault(key, [0, 0, 0, 0.0, 0.0])
                for i, v in enumerate(values):
                    mine[i] += v
            log.exception("failed to record the routing rollup; trying again with the next reply")


async def record(
    tier: str, *, escalated: bool, capped: bool, cost: float, standard_cost: float
) -> None:
    """Add one reply to today's rollup. Best-effort, and like the usage rollup it waits for
    the caller's session to end before writing (see rate_limiter.record_usage)."""
    try:
        row = _pending.setdefault((quota_day(), tier), [0, 0, 0, 0.0, 0.0])
        row[0] += 1
        row[1] += int(escalated)
        row[2] += int(capped)
        row[3] += cost
        row[4] += standard_cost
    except Exception:
        log.exception("failed to count a routed reply")
        return
    if not after_session_scope(flush):
        await flush()
