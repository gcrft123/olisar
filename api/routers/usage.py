"""Usage & rate-limit dashboard data.

Gemini quota is bot-wide (one account), so these are account-scoped (``require_admin``),
matching the legacy ``/stats`` endpoint. Two endpoints:

* ``GET /api/usage/live`` — what's left of today's allowance: every model in the selected
  server's fallback chain with its count, its daily limit and whether it can take a request,
  plus memory search and web search. The bot and API share this process, so the limiter
  singleton is the live source of truth for which models are parked; the page polls this
  every few seconds.
* ``GET /api/usage/summary`` — the slower-moving figures: requests by feature, the same time
  yesterday, the busiest minute, when the chain last ran out and requests per day.

A day is Google's quota day, midnight to midnight Pacific (see olisar.gemini.quota).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select

from api.auth.deps import require_admin
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, GeminiUsage, UsageDay, UsageHour, UsageSource
from olisar.gemini.client import get_gemini
from olisar.gemini.models import GROUNDING_RPD, rpd_for, rpm_for
from olisar.gemini.quota import aware, day_start, next_reset, quota_day, quota_hour
from olisar.gemini.rate_limiter import (
    chains_in_use,
    current_key,
    get_rate_limiter,
    union_chain,
)

router = APIRouter(prefix="/api/usage", tags=["usage"])

# How many days the per-day chart covers, today included.
DAYS_SHOWN = 14


def _iso(value: datetime | None) -> str | None:
    stamped = aware(value)
    return stamped.isoformat() if stamped else None


def _hour_spans(day: date) -> dict[int, tuple[datetime, timedelta]]:
    """When each hour of Google's ``day`` began, in UTC, and how long it really lasted. A
    wall-clock hour is usually an hour, but 1 AM lasts two the night the clocks go back
    and 2 AM never happens the night they go forward. Pacific offsets are whole hours, so
    stepping through the day an hour at a time lands on every local hour boundary."""
    spans: dict[int, tuple[datetime, timedelta]] = {}
    t, end = day_start(day), day_start(day + timedelta(days=1))
    while t < end:
        start, length = spans.get(quota_hour(t), (t, timedelta(0)))
        spans[quota_hour(t)] = (start, length + timedelta(hours=1))
        t += timedelta(hours=1)
    return spans


def _is_chat(model: str) -> bool:
    """Whether a model's requests count against the chat chain's daily limits. Memory search
    runs on the embedding model, which has a limit of its own."""
    return model != settings.gemini_embed_model


def _selected_guild(admin: AdminUser | None, x_guild_id: str | None) -> int | None:
    """The server the console has selected, if this admin may see it."""
    if not x_guild_id or not x_guild_id.isdigit():
        return None
    if admin is not None and not admin.is_allowlisted and x_guild_id not in (admin.managed_guild_ids or []):
        return None
    return int(x_guild_id)


@router.get("/live")
async def live(
    admin: AdminUser = Depends(require_admin),
    x_guild_id: Annotated[str | None, Header()] = None,
):
    """Today's allowance, model by model, in chain order.

    ``chain`` is the fallback chain the selected server (``X-Guild-Id``) replies through:
    its default model and everything ranked below it. Without one, it's every model any
    active server replies through.

    A model is ``spent`` once Google has refused it for the day, whatever Olisar counted:
    the quota is per Google Cloud project, so something else on the project can use it up.
    ``limit`` is the daily limit Google last named in a refusal, else the assumed figure in
    olisar/gemini/models.py.

    ``exhausted`` is bot-wide, for the sidebar: true only when no active server can be
    answered, every model in every server's chain parked or at its RPM cap. A single model
    cooling down is normal fallback, not this flag."""
    limiter = get_rate_limiter()
    # A key pasted into the dashboard is a new quota: un-park the old key's models now
    # rather than at the next reply.
    try:
        kid = await current_key()
    except Exception:  # noqa: BLE001 — the page still renders from what the limiter knows
        kid = limiter.key
    now = datetime.now(timezone.utc)
    day = quota_day(now)
    async with session_scope() as session:
        rows = (await session.scalars(select(GeminiUsage).where(GeminiUsage.day == day))).all()
        named = (
            await session.execute(
                select(GeminiUsage.model, GeminiUsage.quota_limit)
                .where(GeminiUsage.quota_limit.is_not(None))
                .order_by(GeminiUsage.day.desc())
            )
        ).all()
        chains = await chains_in_use(session)
    everyone = union_chain(chains.values())
    shown = chains.get(_selected_guild(admin, x_guild_id)) or everyone
    today = {r.model: r for r in rows}
    google_limit: dict[str, int] = {}
    for model, limit in named:
        google_limit.setdefault(model, int(limit))

    def spent_at(model: str) -> datetime | None:
        row = today.get(model)
        stored = row.exhausted_at if row and row.exhausted_key == kid else None
        return limiter.spent_at(model) or aware(stored)

    chain = []
    for name in shown:
        row = today.get(name)
        out = spent_at(name)
        resting = out is None and limiter.state(name) in ("cooldown", "rpm_full")
        chain.append({
            "model": name,
            "requests": row.request_count if row else 0,
            "tokens": row.token_count if row else 0,
            "limit": google_limit.get(name) or rpd_for(name),
            "limit_from_google": name in google_limit,
            "state": "spent" if out else "resting" if resting else "ok",
            "back_in": math.ceil(limiter.back_in(name)) if resting else None,
            "spent_at": _iso(out),
        })

    embed = settings.gemini_embed_model
    embed_row = today.get(embed)
    reset = next_reset(now)
    blocked = get_gemini().grounding_blocked_until
    return {
        "ts": now.isoformat(),
        "exhausted": limiter.chat_exhausted(everyone),
        "day": day.isoformat(),
        "day_start": day_start(day).isoformat(),
        "reset_at": reset.isoformat(),
        "chain": chain,
        "memory_search": {
            "model": embed,
            "requests": embed_row.request_count if embed_row else 0,
            "limit": google_limit.get(embed) or rpd_for(embed),
            "spent": spent_at(embed) is not None,
        },
        "web_search": {
            "requests": sum(r.grounding_count for r in rows),
            "limit": GROUNDING_RPD,
            # Blocked until the reset means Google said the day's allowance is gone; a
            # shorter block is a per-minute throttle.
            "spent": blocked is not None and blocked >= reset - timedelta(minutes=1),
        },
    }


@router.get("/summary")
async def summary(_: AdminUser = Depends(require_admin)):
    """Everything on the Usage page that doesn't need to move every few seconds.

    * ``features`` — requests by feature (``UsageSource.source``) for today, the last 7 days
      and the last 30. Memory search is left out: it has its own daily limit.
    * ``yesterday`` — yesterday's chat requests and tokens up to this time of day, or null
      before there's an hourly record to compare with.
    * ``busiest_minute`` — today's chat model that came closest to its per-minute limit.
    * ``last_ran_out`` — the last time every model in the chain was out for the day.
    * ``days`` — chat requests per day for the last ``DAYS_SHOWN`` days, today last, with
      when the chain ran out on any day it did."""
    now = datetime.now(timezone.utc)
    today = quota_day(now)
    first = today - timedelta(days=29)
    first_shown = today - timedelta(days=DAYS_SHOWN - 1)
    yesterday = today - timedelta(days=1)
    async with session_scope() as session:
        sources = (
            await session.scalars(select(UsageSource).where(UsageSource.day >= first))
        ).all()
        usage = (
            await session.scalars(select(GeminiUsage).where(GeminiUsage.day >= first_shown))
        ).all()
        hours = (
            await session.scalars(select(UsageHour).where(UsageHour.day == yesterday))
        ).all()
        ran_out = (
            await session.scalars(select(UsageDay).where(UsageDay.day >= first_shown))
        ).all()
        last_ran_out = await session.scalar(select(func.max(UsageDay.chain_out_at)))

    def by_source(since) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in sources:
            if s.day >= since and s.source != "embed":
                out[s.source] = out.get(s.source, 0) + s.request_count
        return out

    # The same stretch of yesterday: as long since yesterday began as today has been going.
    # Hours are wall-clock Pacific, so each counts for the real time it lasted; the hour
    # before this cutoff counts for its share.
    cutoff = day_start(yesterday) + (now - day_start(today))
    spans = _hour_spans(yesterday)
    so_far = {"requests": 0.0, "tokens": 0.0}
    for h in hours:
        if not _is_chat(h.model) or h.hour not in spans:
            continue
        start, length = spans[h.hour]
        weight = min(max((cutoff - start) / length, 0.0), 1.0)
        so_far["requests"] += h.request_count * weight
        so_far["tokens"] += h.token_count * weight

    busiest = max(
        (u for u in usage if u.day == today and _is_chat(u.model) and u.peak_rpm > 0),
        key=lambda u: u.peak_rpm / max(rpm_for(u.model), 1),
        default=None,
    )

    per_day = {first_shown + timedelta(days=i): 0 for i in range(DAYS_SHOWN)}
    for u in usage:
        if _is_chat(u.model) and u.day in per_day:
            per_day[u.day] += u.request_count
    out_at = {d.day: d.chain_out_at for d in ran_out}

    return {
        "day": today.isoformat(),
        "features": {
            "today": by_source(today),
            "7": by_source(today - timedelta(days=6)),
            "30": by_source(first),
        },
        "yesterday": (
            {"requests": round(so_far["requests"]), "tokens": round(so_far["tokens"])}
            if hours else None
        ),
        "busiest_minute": (
            {
                "model": busiest.model,
                "requests": busiest.peak_rpm,
                "limit": rpm_for(busiest.model),
                "at": _iso(busiest.peak_rpm_at),
            }
            if busiest else None
        ),
        "last_ran_out": _iso(last_ran_out),
        "days": [
            {"day": d.isoformat(), "requests": n, "ran_out_at": _iso(out_at.get(d))}
            for d, n in per_day.items()
        ],
    }
