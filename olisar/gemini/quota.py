"""Google's quota day, and what a 429 says about it.

Google resets requests-per-day at midnight Pacific time
(https://ai.google.dev/gemini-api/docs/rate-limits). Usage used to be bucketed by UTC day,
so for seven or eight hours every evening Olisar started a fresh day while Google was still
counting the old one: the Usage page showed a full allowance on a chain that was spent, and a
refusal parked "until midnight" came back hours before Google would take a request again.
Everything that counts a day's requests or waits for the reset reads the day from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    """A stored timestamp as UTC. SQLite hands ``DateTime(timezone=True)`` back naive."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def quota_day(now: datetime | None = None) -> date:
    """The day Google is counting requests against."""
    return _now(now).astimezone(PACIFIC).date()


def quota_hour(now: datetime | None = None) -> int:
    """The hour of Google's day, 0–23."""
    return _now(now).astimezone(PACIFIC).hour


def day_start(day: date) -> datetime:
    """When Google's ``day`` began, in UTC. Midnight always exists in Pacific time; the
    clocks change at 2 AM."""
    return datetime.combine(day, time(0), PACIFIC).astimezone(timezone.utc)


def next_reset(now: datetime | None = None) -> datetime:
    """When the daily limits next reset, in UTC."""
    return day_start(quota_day(now) + timedelta(days=1))


@dataclass(frozen=True)
class Refusal:
    """What a 429 says about the quota that ran out."""

    daily: bool
    # The quota's size, when Google named it. Google's docs stopped listing free-tier daily
    # limits, so this is the only authoritative source there is.
    limit: int | None = None


# A daily quota's id, e.g. `GenerateRequestsPerDayPerProjectPerModel-FreeTier`.
_DAILY_ID_RE = re.compile(r"PerDay", re.IGNORECASE)


def _quota_id(violation: dict) -> str:
    return str(violation.get("quotaId") or violation.get("quota_id") or "")


def _violations(exc: Exception) -> list[dict]:
    """The ``google.rpc.QuotaFailure`` violations Google attached to an error, if any."""
    body = getattr(exc, "details", None)
    if not isinstance(body, dict):
        return []
    error = body.get("error", body)
    details = error.get("details") if isinstance(error, dict) else None
    out: list[dict] = []
    for detail in details or []:
        if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("QuotaFailure"):
            out.extend(v for v in detail.get("violations") or [] if isinstance(v, dict))
    return out


def read_refusal(exc: Exception) -> Refusal:
    """Tell a spent daily quota from a per-minute throttle.

    Only a violation whose quota id says ``PerDay`` counts as daily. Getting this wrong costs
    far more in one direction: a per-minute throttle read as daily parks a model until
    midnight, where the other way round only costs a retry every couple of minutes."""
    violations = _violations(exc)
    daily = [v for v in violations if _DAILY_ID_RE.search(_quota_id(v))]
    if not daily:
        # No structured detail, but the words can still say so.
        text = f"{getattr(exc, 'message', '') or ''} {getattr(exc, 'details', '') or ''}"
        return Refusal(daily=bool(re.search(r"PerDay\w*-FreeTier", text)))
    # A request quota's size, not a token quota's, is the number the page counts against.
    requests = [v for v in daily if "request" in _quota_id(v).lower()]
    limit = None
    for v in requests or daily:
        try:
            limit = int(str(v.get("quotaValue") or v.get("quota_value") or ""))
            break
        except ValueError:
            continue
    return Refusal(daily=True, limit=limit)
