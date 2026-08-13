"""Scheduling for knowledge sources that re-read themselves.

A source carries an interval in hours (0 = never). When its schedule comes due this module
flips it back to ``pending``; the existing ingest worker then picks it up like any other
queued source, and :func:`olisar.knowledge.ingest.process_pending_sources` replaces its
passages in place. Nothing here fetches anything.

The decision — *should this row be queued right now* — is :func:`refresh_action`, a pure
function. The database half is a thin loop around it, so the rules can be tested without a
schema, and the two datetime hazards SQLite hands back (naive values, and a NULL where a
timestamp was expected) are handled in one readable place instead of inside a WHERE clause.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import KBSource, KBSourceType, KBStatus, utcnow

log = logging.getLogger("olisar.knowledge.refresh")

# Only sources with an upstream that can change on its own. An uploaded document is a file
# in the operator's own data dir — re-reading it would either produce the identical text or,
# if the file has since been removed, turn a Ready source into an Error one on a timer. The
# API refuses a schedule on a doc; this is the second lock on the same door.
REFRESHABLE_TYPES = (KBSourceType.url, KBSourceType.website)

# Bounds the console and the API both enforce: hourly at the fastest, yearly at the slowest.
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 8760

# A source is claimed by flipping it to ``crawling``; if the process dies before it writes
# the result, the row is stranded there and its schedule stops forever. Anything still
# claimed after this long is treated as abandoned and re-queued. The ceiling is deliberately
# far above a real crawl — 100 pages at the crawler's 0.5s delay and 15s timeout is ~34
# minutes in the worst case — so this can only fire on a claim nobody is holding.
STALE_CLAIM_HOURS = 2

_IN_FLIGHT = (KBStatus.crawling, KBStatus.chunking)
_SETTLED = (KBStatus.ready, KBStatus.error)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite has no timezone type, so a ``DateTime(timezone=True)`` column reads back naive
    and comparing it to an aware ``utcnow()`` raises. Same coercion the rest of the codebase
    uses (see ``olisar.memory.search``); values were always written as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def next_run_at(now: datetime, interval_hours: int) -> datetime | None:
    """When a source on this interval should next be read. ``None`` when it's unscheduled."""
    if interval_hours <= 0:
        return None
    return now + timedelta(hours=interval_hours)


def refresh_action(
    *,
    status: KBStatus,
    interval_hours: int,
    next_refresh_at: datetime | None,
    last_checked_at: datetime | None,
    now: datetime,
) -> str | None:
    """Why this source should be queued now, or ``None`` to leave it alone.

    Returns ``"due"`` when its schedule has come round, or ``"recovered"`` when it has been
    claimed by a worker that never came back.
    """
    if interval_hours <= 0:
        return None

    if status in _SETTLED:
        due = _aware(next_refresh_at)
        # No stamp means a schedule was set without one (an older row, or one written
        # straight into the database). Read it now and stamp it on the way through rather
        # than leaving it scheduled-but-never-due.
        return "due" if due is None or due <= now else None

    if status in _IN_FLIGHT:
        started = _aware(last_checked_at)
        # The claim writes ``status`` and ``last_checked_at`` in one transaction, so the two
        # are never seen apart: in-flight with no clock at all means the claim was made by a
        # build that predates this column, and whatever process held it is long gone.
        if started is None:
            return "recovered"
        return "recovered" if now - started >= timedelta(hours=STALE_CLAIM_HOURS) else None

    # ``pending`` — already queued, and the worker will get to it.
    return None


async def queue_due_refreshes() -> int:
    """Queue every source whose schedule has come due. Returns how many were queued.

    Called from the bot's background tick, before the ingest pass, so a source that comes due
    is read on the same tick it becomes eligible.
    """
    now = utcnow()
    queued = 0
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(KBSource).where(
                    KBSource.refresh_interval_hours > 0,
                    KBSource.type.in_(REFRESHABLE_TYPES),
                )
            )
        ).all()
        for src in rows:
            action = refresh_action(
                status=src.status,
                interval_hours=src.refresh_interval_hours,
                next_refresh_at=src.next_refresh_at,
                last_checked_at=src.last_checked_at,
                now=now,
            )
            if action is None:
                continue
            if action == "recovered":
                log.warning(
                    "source %s was stuck on %s — re-queuing", src.id, src.status.value
                )
            src.status = KBStatus.pending
            # Stamped at queue time, not on completion: the operator asked for "every 6
            # hours", not "six hours after each crawl finishes".
            src.next_refresh_at = next_run_at(now, src.refresh_interval_hours)
            # The row is being read again, so the previous failure is no longer what's true
            # about it. A fresh failure will write a fresh message.
            src.error = None
            queued += 1
    if queued:
        log.info("queued %d source(s) for a scheduled re-read", queued)
    return queued
