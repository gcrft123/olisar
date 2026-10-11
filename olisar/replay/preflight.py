"""What the start sheet says before anything runs: what a replay would read, what it
costs, and whether it can start now.

The sheet has no settings, so this is everything it shows:

* How much it reads: the messages in the indexed channels, without the people a replay
  leaves out (scope.py).
* What it costs. A free key: about how long, and what share of today's Gemini allowance.
  A key with billing on: about how long, and an estimate in dollars from today's prices.
* Whether it can start, in this order:
  - ``short``: too little history to make a replay of (stats.MIN_MESSAGES, MIN_DAYS).
  - ``quota``: a free key without enough of today's allowance left. A replay may use at
    most half of what's left on either chain, so replies keep the rest. Resets at
    midnight Pacific.
  - ``budget``: a billed key whose monthly budget this would go past.
  - ``indexing``: the search index is still reading history. Starting is allowed ("Start
    anyway"), and the sheet says what a replay now would leave out.
  - ``ready``.

A run already going (someone else's, to watch) is the API's to add; see
api/routers/replay.py.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime

from sqlalchemy import func, select

from olisar.db.engine import session_scope
from olisar.db.models import GeminiUsage, GuildChannelInfo, GuildConfig, SearchMessage, snowflake_time
from olisar.gemini import spend
from olisar.gemini import tier as gemini_tier
from olisar.gemini.models import rpd_for
from olisar.gemini.pricing import request_cost
from olisar.gemini.quota import next_reset, quota_day
from olisar.gemini.rate_limiter import current_key, get_rate_limiter
from olisar.replay.llm import chain_for
from olisar.replay.scope import aware, build_scope
from olisar.replay.stats import MIN_DAYS, MIN_MESSAGES, iso

# The search backfill's pace (bot/cogs/search_index.py): 4 channels a tick, a 100-message
# page each, every 40 seconds.
INDEX_PER_MINUTE = 4 * 100 * 60 / 40
INDEX_PER_CHANNEL_MINUTE = 100 * 60 / 40
# A replay may take at most this share of what's left of today's allowance on a chain.
ALLOWANCE_SHARE = 0.5


def plan(messages: int) -> dict:
    """How many requests a replay of this many messages makes, by kind of model."""
    moments = max(6, min(40, messages // 8000))
    # lite: the moments, the motifs pass, up to two pick checks, the last check of quotes
    return {"moments": moments, "lite": moments + 4, "strong": 1, "chat": 2}


def tokens(kind: str, n: int, avg_chars: float, moments: int) -> tuple[int, int]:
    """Rough input and output tokens for ``n`` requests of a kind."""
    per_message = avg_chars / 4 + 12
    if kind == "lite":
        reading = moments * (200 * per_message + 700)
        others = 5000 + 2 * 1800 + 4000      # the motifs pass, two pick checks, the last check
        return int(reading + others), moments * 450 + 400 + 2 * 300 + 800
    if kind == "strong":
        return 11000 * n, 3500 * n
    return 3000 * n, 450 * n


async def left_today(models: list[str]) -> tuple[int, int]:
    """(left, whole day) of the free allowance across ``models``, as the Usage page counts
    it: Google's own limit where it has named one, the published one otherwise, and
    nothing left on a model it has refused for the day."""
    limiter = get_rate_limiter()
    try:
        kid = await current_key()
    except Exception:  # noqa: BLE001 — what the limiter knows is enough
        kid = limiter.key
    day = quota_day()
    async with session_scope() as session:
        rows = {r.model: r for r in (await session.scalars(select(GeminiUsage).where(GeminiUsage.day == day))).all()}
        named = dict(
            (await session.execute(
                select(GeminiUsage.model, GeminiUsage.quota_limit)
                .where(GeminiUsage.quota_limit.is_not(None)).order_by(GeminiUsage.day)
            )).all()
        )
    left = whole = 0
    for model in dict.fromkeys(models):
        limit = int(named.get(model) or rpd_for(model))
        row = rows.get(model)
        spent = limiter.spent_at(model) or (row.exhausted_at if row and row.exhausted_key == kid else None)
        whole += limit
        left += 0 if spent else max(0, limit - (row.request_count if row else 0))
    return left, whole


def indexing(rows, counts: dict[int, int], now: datetime) -> dict | None:
    """How far the search index has read back, while it's still reading: the share of
    history read (by time, since how many messages remain isn't known), what a replay now
    would leave out, and about how long the rest takes."""
    on = {int(r.channel_id) for r in rows if r.kind != "thread" and r.index_enabled}
    targets = [r for r in rows if (r.kind != "thread" and r.index_enabled) or (r.kind == "thread" and r.parent_id in on)]
    pending = [r for r in targets if not r.backfill_done]
    if not pending:
        return None
    whole = read = 0.0
    for r in targets:
        life = max(0.0, (now - snowflake_time(int(r.channel_id))).total_seconds())
        if r.backfill_done:
            got = life
        elif r.last_indexed_message_id:
            got = (now - snowflake_time(int(r.last_indexed_message_id))).total_seconds()
        else:
            got = 0.0
        whole += life
        read += min(life, max(0.0, got))
    started = [r for r in pending if r.last_indexed_message_id]
    densities, remaining = [], 0.0
    for r in started:
        reached = snowflake_time(int(r.last_indexed_message_id))
        span = max(3600.0, (now - reached).total_seconds())
        density = counts.get(int(r.channel_id), 0) / span
        densities.append(density)
        remaining += density * max(0.0, (reached - snowflake_time(int(r.channel_id))).total_seconds())
    typical = statistics.median(densities) if densities else 50 / 86400
    for r in pending:
        if not r.last_indexed_message_id:
            remaining += typical * max(0.0, (now - snowflake_time(int(r.channel_id))).total_seconds())
    minutes = remaining / INDEX_PER_MINUTE
    before = max((snowflake_time(int(r.last_indexed_message_id)) for r in started), default=None)
    return {
        "percent": int(100 * read / whole) if whole else 0,
        "channels": len(pending),
        "unread": len(pending) - len(started),
        "before": iso(before),
        "minutes": int(math.ceil(minutes)),
    }


async def check(guild_id: int, *, now: datetime, bots: set[int] | None = None) -> dict:
    async with session_scope() as session:
        scope = await build_scope(session, guild_id, now=now, bots=bots)
        count, channels, first, last, avg_chars = (
            await session.execute(
                select(
                    func.count(), func.count(func.distinct(SearchMessage.channel_id)),
                    func.min(SearchMessage.created_at), func.max(SearchMessage.created_at),
                    func.avg(func.length(SearchMessage.content)),
                ).where(*scope.where())
            )
        ).one()
        per_channel = dict(
            (await session.execute(
                select(SearchMessage.channel_id, func.count())
                .where(SearchMessage.guild_id == guild_id).group_by(SearchMessage.channel_id)
            )).all()
        )
        rows = (await session.scalars(select(GuildChannelInfo).where(GuildChannelInfo.guild_id == guild_id))).all()
        guild_model = await session.scalar(select(GuildConfig.default_model).where(GuildConfig.guild_id == guild_id))
    count = int(count or 0)
    first, last = aware(first), aware(last)
    idx = indexing(rows, {int(k): int(v) for k, v in per_channel.items()}, now)
    calls = plan(count)
    avg_chars = float(avg_chars or 60)

    try:
        tier = await gemini_tier.current()
    except Exception:  # noqa: BLE001 — unknown reads as the free tier
        tier = None
    paid = tier == gemini_tier.PAID
    requests = calls["lite"] + calls["strong"] + calls["chat"]
    seconds = 8 + count / 25000 + calls["lite"] * (4.5 if paid else 8.5) + calls["strong"] * 20 + calls["chat"] * 7
    cost: dict = {"tier": "paid" if paid else "free", "minutes": max(1, math.ceil(seconds / 60)), "requests": requests}
    blocked = None
    if paid:
        day = quota_day()
        usd = 0.0
        for kind in ("lite", "strong", "chat"):
            model = chain_for(kind, guild_model)[0]
            tin, tout = tokens(kind, calls[kind], avg_chars, calls["moments"])
            usd += request_cost(model, day, input_tokens=tin, output_tokens=tout)
        cost["usd"] = round(usd, 2)
        budget = await spend.budget()
        if budget.usd > 0:
            cost["budget"] = round(budget.usd, 2)
            if spend.month_usd() + usd > budget.usd:
                blocked = "budget"
    else:
        lite_left, lite_day = await left_today(chain_for("lite"))
        chat_left, chat_day = await left_today(chain_for("strong") + chain_for("chat", guild_model))
        cost["share"] = round(requests / max(1, lite_day + chat_day), 4)
        cost["resets_at"] = iso(next_reset(now))
        if calls["lite"] > lite_left * ALLOWANCE_SHARE or calls["strong"] + calls["chat"] > chat_left * ALLOWANCE_SHARE:
            blocked = "quota"

    short = count < MIN_MESSAGES or not first or not last or (last - first).days < MIN_DAYS
    state = "short" if short else blocked or ("indexing" if idx else "ready")
    return {
        "state": state,
        "messages": count,
        "channels": int(channels or 0),
        "first": iso(first),
        "indexing": idx,
        "cost": cost,
    }
