"""This month's Gemini spend, and the budget it's held to.

The Usage page works its figures out from the usage rollup (olisar.gemini.pricing). The
budget can't wait for a query on every request, so this keeps a running total in memory:
restored from the rollup at startup, added to as each request is recorded, and started
again on the first of each month. Months are Pacific, like Google's quota day and its
billing.

On a free key nothing is charged and the budget does nothing; the total is what the month
would have cost.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import AppConfig, GeminiUsage
from olisar.gemini.pricing import (
    SEARCH_25_FREE_PER_DAY,
    SEARCH_25_PRICE,
    SEARCH_FREE_PER_MONTH,
    SEARCH_PRICE,
    daily_costs,
    is_gen25,
    request_cost,
)
from olisar.gemini.quota import quota_day

log = logging.getLogger("olisar.gemini.spend")

# Where replies go once the budget is spent and the operator chose to keep going: the
# cheapest model in the chain, by a distance.
CHEAPEST = "gemini-2.5-flash-lite"

# The share of the budget at which the console starts warning.
WARN_AT = 0.8

STOP, CHEAPEST_ACTION = "stop", "cheapest"
_SETTINGS_TTL = 10.0


@dataclass
class _Month:
    year: int
    month: int
    usd: float = 0.0
    searches_gen3: int = 0  # web searches on Gemini 3 and newer, this month
    searches_gen25: dict[date, int] = field(default_factory=dict)  # on Gemini 2.5, per day


@dataclass(frozen=True)
class Budget:
    usd: float  # 0 is no budget
    action: str  # STOP | CHEAPEST_ACTION
    gemini_images: bool


_month: _Month | None = None
_budget: Budget = Budget(0.0, CHEAPEST_ACTION, True)
_budget_at = 0.0
_warned_for: tuple[int, int, str] | None = None


def month_start(day: date) -> date:
    return day.replace(day=1)


def _this_month(day: date) -> _Month:
    global _month
    if _month is None or (_month.year, _month.month) != (day.year, day.month):
        _month = _Month(day.year, day.month)
    return _month


def add(
    model: str, day: date, *, input_tokens: int = 0, output_tokens: int = 0,
    images: int = 0, grounding: int = 0,
) -> None:
    """Count one recorded request's cost, searches included."""
    m = _this_month(day)
    m.usd += request_cost(
        model, day, input_tokens=input_tokens, output_tokens=output_tokens, images=images
    )
    if not grounding:
        return
    if is_gen25(model):
        before = m.searches_gen25.get(day, 0)
        m.searches_gen25[day] = before + grounding
        billed = max(0, before + grounding - SEARCH_25_FREE_PER_DAY) - max(0, before - SEARCH_25_FREE_PER_DAY)
        m.usd += billed * SEARCH_25_PRICE
    else:
        before = m.searches_gen3
        m.searches_gen3 = before + grounding
        billed = max(0, before + grounding - SEARCH_FREE_PER_MONTH) - max(0, before - SEARCH_FREE_PER_MONTH)
        m.usd += billed * SEARCH_PRICE


def month_usd(day: date | None = None) -> float:
    """What this month has cost so far (or would have, on a free key)."""
    return _this_month(day or quota_day()).usd


async def restore() -> float:
    """Start the running total from the rollup, so a restart doesn't forget the month."""
    global _month
    today = quota_day()
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(GeminiUsage).where(GeminiUsage.day >= month_start(today))
            )
        ).all()
    m = _Month(today.year, today.month)
    m.usd = sum(daily_costs(rows).values())
    for row in rows:
        if not row.grounding_count:
            continue
        if is_gen25(row.model):
            m.searches_gen25[row.day] = m.searches_gen25.get(row.day, 0) + row.grounding_count
        else:
            m.searches_gen3 += row.grounding_count
    _month = m
    return m.usd


async def budget() -> Budget:
    """The operator's budget settings, read at most every few seconds."""
    global _budget, _budget_at
    now = time.monotonic()
    if now - _budget_at < _SETTINGS_TTL:
        return _budget
    _budget_at = now
    try:
        async with session_scope() as session:
            row = await session.get(AppConfig, 1)
        if row is not None:
            action = row.budget_action if row.budget_action in (STOP, CHEAPEST_ACTION) else CHEAPEST_ACTION
            _budget = Budget(max(0.0, row.monthly_budget_usd or 0.0), action, bool(row.gemini_images))
    except Exception:
        log.exception("reading the Gemini budget failed; keeping the last one")
    return _budget


def invalidate() -> None:
    """Re-read the budget on next use (after the console saves it)."""
    global _budget_at
    _budget_at = 0.0


async def state() -> str:
    """``"none"`` with no budget, else ``"ok"``, ``"warn"`` (past WARN_AT) or ``"over"``."""
    b = await budget()
    if b.usd <= 0:
        return "none"
    spent = month_usd()
    status = "over" if spent >= b.usd else "warn" if spent >= b.usd * WARN_AT else "ok"
    _log_crossing(status, spent, b)
    return status


async def over_budget() -> bool:
    return await state() == "over"


def _log_crossing(status: str, spent: float, b: Budget) -> None:
    """Say so in the log the first time a month crosses each line."""
    global _warned_for
    if status not in ("warn", "over") or _month is None:
        return
    mark = (_month.year, _month.month, status)
    if _warned_for == mark:
        return
    _warned_for = mark
    if status == "warn":
        log.warning("Gemini spend this month is $%.2f, %d%% of the $%.2f budget", spent, round(spent / b.usd * 100), b.usd)
    else:
        log.warning(
            "Gemini spend this month ($%.2f) reached the $%.2f budget; %s", spent, b.usd,
            "stopping until next month" if b.action == STOP else f"replying on {CHEAPEST} only",
        )
