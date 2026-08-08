"""Usage & rate-limit dashboard data.

Gemini quota is bot-wide (one account), so these are account-scoped (``require_admin``),
matching the legacy ``/stats`` endpoint. Two endpoints:

* ``GET /api/usage/summary?days=N`` — daily rollups (per-model, per-process) plus today's
  totals and peaks, for the Usage page's charts and tables.
* ``GET /api/usage/live`` — the in-memory limiter's current per-model RPM. The bot and API
  share this process, so the limiter singleton is the live source of truth; the page polls
  this every few seconds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from api.auth.deps import require_admin
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, GeminiUsage, UsageMinutePeak, UsageSource
from olisar.gemini.models import RANKED, rpm_for
from olisar.gemini.rate_limiter import get_rate_limiter

router = APIRouter(prefix="/api/usage", tags=["usage"])

# Free-tier tokens-per-minute ceiling, drawn as the TPM limit line on the dashboard.
TPM_LIMIT = 1_000_000


@router.get("/summary")
async def summary(days: int = Query(7, ge=0, le=3650), _: AdminUser = Depends(require_admin)):
    """Daily usage over the last ``days`` days: per-day requests/tokens (with per-model
    split), per-process request share, per-model totals + today's peak RPM, and today's
    peaks. Everything the Usage page needs except the live per-minute snapshot.

    ``days=0`` means all-time: the window starts at the earliest recorded day. A long
    window is bucketed (weekly past ~10 weeks, monthly past ~2 years) so the response
    stays a chart's worth of points rather than one row per day since install."""
    all_time = days == 0
    if not all_time:
        days = max(1, min(days, 3650))
    today = datetime.now(timezone.utc).date()

    async with session_scope() as session:
        if all_time:
            earliest = await session.scalar(select(func.min(GeminiUsage.day)))
            start = earliest or today
        else:
            start = today - timedelta(days=days - 1)
        usage = (
            await session.scalars(select(GeminiUsage).where(GeminiUsage.day >= start))
        ).all()
        sources = (
            await session.scalars(select(UsageSource).where(UsageSource.day >= start))
        ).all()
        peaks = (
            await session.scalars(select(UsageMinutePeak).where(UsageMinutePeak.day >= start))
        ).all()

    if all_time:
        days = (today - start).days + 1

    # Zero-filled daily buckets, chronological, so the chart never has gaps.
    day_list = [start + timedelta(days=i) for i in range(days)]
    peak_by_day = {p.day: int(p.peak_tpm) for p in peaks}
    daily = {
        d: {
            "day": d.isoformat(),
            "requests": 0,
            "tokens": 0,
            "peak_tpm": peak_by_day.get(d, 0),
            "by_model": {},
        }
        for d in day_list
    }

    # Seed the full fallback roster (the chat chain + embeddings) so every model Olisar
    # can reach shows up — even ones not used yet (they stay idle until the models above
    # them get rate-limited).
    embed_model = "gemini-embedding-001"

    def _blank(name: str) -> dict:
        return {
            "model": name,
            "cap": rpm_for(name),
            "role": "embed" if name == embed_model else "chat",
            "requests": 0,
            "tokens": 0,
            "requests_today": 0,
            "peak_rpm_today": 0,
        }

    models: dict[str, dict] = {mi.name: _blank(mi.name) for mi in RANKED}
    models.setdefault(embed_model, _blank(embed_model))

    for r in usage:
        bucket = daily.get(r.day)
        if bucket is not None:
            bucket["requests"] += r.request_count
            bucket["tokens"] += r.token_count
            bucket["by_model"][r.model] = bucket["by_model"].get(r.model, 0) + r.request_count
        m = models.setdefault(r.model, _blank(r.model))
        m["requests"] += r.request_count
        m["tokens"] += r.token_count
        if r.day == today:
            m["requests_today"] += r.request_count
            m["peak_rpm_today"] = max(m["peak_rpm_today"], r.peak_rpm)

    by_source: dict[str, int] = {}
    for s in sources:
        by_source[s.source] = by_source.get(s.source, 0) + s.request_count

    by_model = sorted(models.values(), key=lambda m: m["requests"], reverse=True)
    today_bucket = daily[today]
    today_grounding = sum(r.grounding_count for r in usage if r.day == today)
    # Today's headline peak RPM = the model that got closest to its own cap.
    peak_rpm = max(
        (m for m in by_model if m["peak_rpm_today"] > 0),
        key=lambda m: m["peak_rpm_today"] / max(m["cap"], 1),
        default=None,
    )

    # Collapse a long window so the client gets a chart, not a ledger. Requests and tokens
    # sum across the bucket; peak_tpm is a peak, so it takes the max.
    series = [daily[d] for d in day_list]
    step = 1 if days <= 70 else (7 if days <= 730 else 30)
    if step > 1:
        bucketed: list[dict] = []
        for i in range(0, len(series), step):
            chunk = series[i : i + step]
            merged_models: dict[str, int] = {}
            for c in chunk:
                for name, n in c["by_model"].items():
                    merged_models[name] = merged_models.get(name, 0) + n
            bucketed.append({
                "day": chunk[0]["day"],
                "requests": sum(c["requests"] for c in chunk),
                "tokens": sum(c["tokens"] for c in chunk),
                "peak_tpm": max(c["peak_tpm"] for c in chunk),
                "by_model": merged_models,
            })
        series = bucketed

    return {
        "window_days": days,
        "all_time": all_time,
        "start": start.isoformat(),
        # 1 = one point per day, 7 = per week, 30 = per month. The axis labels itself
        # from this rather than guessing from the point count.
        "bucket_days": step,
        "today": {
            "requests": today_bucket["requests"],
            "tokens": today_bucket["tokens"],
            "grounding": today_grounding,
        },
        "peak": {
            "rpm": (
                {"value": peak_rpm["peak_rpm_today"], "cap": peak_rpm["cap"], "model": peak_rpm["model"]}
                if peak_rpm
                else {"value": 0, "cap": 0, "model": None}
            ),
            "tpm": peak_by_day.get(today, 0),
            "tpm_limit": TPM_LIMIT,
        },
        "daily": series,
        "by_model": by_model,
        "by_source": sorted(
            ({"source": k, "requests": v} for k, v in by_source.items()),
            key=lambda x: x["requests"],
            reverse=True,
        ),
    }


@router.get("/live")
async def live(_: AdminUser = Depends(require_admin)):
    """Current per-model requests-in-the-last-60s, read straight off the limiter."""
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "models": get_rate_limiter().snapshot(),
    }
