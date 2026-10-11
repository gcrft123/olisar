"""Advancing a replay run, step by step.

The bot calls ``advance`` for a run that's queued, running, or paused past its time. Each
step reads what it needs, writes its part of the report into the run's state, and the run
moves on; a run picked up again starts at the step it was on, and the model answers it
already had are kept (llm.py), so nothing read is lost.

How a run stops partway:

* The day's Gemini allowance runs out (``RateLimitExceeded``): paused until midnight
  Pacific, then it carries on by itself.
* A billed key reaches the month's budget with "stop" chosen (``BudgetSpent``): paused
  until the first of the month, Pacific.
* Anything else: failed, with the reason. Try again in the console queues it again.
* The bot shuts down: the run stays "running" and is picked up when the bot is back.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import Guild, GuildConfig, Message, ReplayRun, utcnow
from olisar.gemini.quota import PACIFIC, next_reset
from olisar.gemini.rate_limiter import BudgetSpent, RateLimitExceeded
from olisar.replay import stats, store
from olisar.replay.llm import Asker, Spend
from olisar.replay.scope import Roster, Scope, aware, build_scope, hidden_users

log = logging.getLogger("olisar.replay")

STEPS = ("gather", "count", "moments", "pick", "write", "check", "media")
# The four the remembering screen names: Gathering, Counting, Reading, Writing.
PHASE = {"gather": 0, "count": 1, "moments": 2, "pick": 2, "write": 3, "check": 3, "media": 3}
PROGRESS_EVERY = 0.75   # seconds between progress writes


class NothingToReplay(Exception):
    pass


@dataclass
class Context:
    run_id: int
    guild_id: int
    roster: Roster
    now: datetime
    state: dict
    progress: dict
    scope: Scope
    people: stats.People
    guild_model: str | None
    asker: Asker | None = None
    tally: stats.Tally | None = None
    _wrote: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def report(self) -> dict:
        return self.state.setdefault("report", {})

    async def tell(self, force: bool = False, **fields) -> None:
        """Update what the remembering screen shows; written at most every PROGRESS_EVERY."""
        self.progress.update(fields)
        if force or time.monotonic() - self._wrote >= PROGRESS_EVERY:
            self._wrote = time.monotonic()
            await store.save(self.run_id, progress=dict(self.progress))

    async def keep(self) -> None:
        """Persist the state: everything found so far survives a stop from here on."""
        self.report["people"] = self.people.seen
        await store.save(self.run_id, state=_copy(self.state), progress=dict(self.progress))

    async def tallied(self) -> stats.Tally:
        if self.tally is None:
            self.tally = await stats.gather(self.scope)
        return self.tally


def _copy(state: dict) -> dict:
    """A fresh object for SQLAlchemy to see as changed."""
    return copy.deepcopy(state)


# ── The steps ──

async def step_gather(ctx: Context) -> None:
    async def told(p: dict) -> None:
        await ctx.tell(**p)

    ctx.tally = await stats.gather(ctx.scope, told)
    if ctx.tally.messages == 0:
        raise NothingToReplay("There's nothing in the search index to replay yet.")
    await ctx.tell(force=True, read=ctx.tally.messages, total=ctx.tally.messages, where=None)


async def step_count(ctx: Context) -> None:
    tally = await ctx.tallied()
    roster, people, scope = ctx.roster, ctx.people, ctx.scope
    tz = stats.infer_offset(tally.hours)
    created = stats.created_at(ctx.guild_id)
    async with session_scope() as session:
        guild_row = await session.get(Guild, ctx.guild_id)
    bot_name = await stats.persona_name(ctx.guild_id, "Olisar")
    years = stats.years_between(created, ctx.now)
    await ctx.tell(force=True, people=roster.humans(), channels=len(tally.channels), years=years)

    arrive = await stats.arrival(scope, people, roster, guild_row.created_at if guild_row else None)
    r = ctx.report
    r.update({
        "v": 1,
        "guild": {"id": str(ctx.guild_id), "name": roster.guild_name or (guild_row.name if guild_row else ""), "icon": roster.guild_icon or (guild_row.icon if guild_row else "")},
        "bot": {"name": bot_name, "avatar": getattr(roster, "me_avatar", None)},
        "tz": tz,
        "created": stats.iso(created),
        "range": {"from": stats.iso(created), "to": stats.iso(ctx.now)},
        "totals": {"messages": tally.messages, "channels": len(tally.channels), "people": roster.humans(), "authors": len(tally.authors), "years": years},
        "first": await stats.first_messages(scope, people),
        "founders": await stats.founders(scope, tally, people),
        "arrivals": stats.arrivals(roster, people, start=created, now=ctx.now, bot_joined=aware(roster.me_joined_at), tz=tz or 0),
        "night": await stats.busiest_day(scope, tally, people, tz or 0),
        "arrive": arrive,
        "names": await stats.renames(ctx.guild_id, people),
        "pick": None,
        "moments": {"found": 0},
        "last_answer": await _last_answer(ctx.guild_id, ctx.run_id),
    })
    await ctx.keep()


async def step_moments(ctx: Context) -> None:
    from olisar.replay import moments
    await moments.run(ctx)


async def step_pick(ctx: Context) -> None:
    from olisar.replay import pick
    await pick.run(ctx)


async def step_write(ctx: Context) -> None:
    from olisar.replay import writing
    await writing.run(ctx)


async def step_check(ctx: Context) -> None:
    from olisar.replay import check
    await check.run(ctx)


async def step_media(ctx: Context) -> None:
    from olisar.replay import media
    await media.attach(ctx.report, ctx.roster)


STEP_FN = {
    "gather": step_gather, "count": step_count, "moments": step_moments,
    "pick": step_pick, "write": step_write, "check": step_check, "media": step_media,
}


async def _last_answer(guild_id: int, run_id: int) -> str | None:
    async with session_scope() as session:
        answer = await session.scalar(
            select(ReplayRun.answer).where(
                ReplayRun.guild_id == guild_id, ReplayRun.id != run_id, ReplayRun.answer != "",
            ).order_by(ReplayRun.id.desc()).limit(1)
        )
    return answer or None


def chapters(report: dict) -> int:
    """How many chapters the console will play (see web/src/replay/beats.ts)."""
    n = 2   # the opening and "now" are always there
    n += bool(report.get("arrivals", {}).get("now"))
    n += bool(report.get("night"))
    n += bool(report.get("pick"))
    n += bool(report.get("arrive"))
    return n


def _name_tokens(report: dict) -> None:
    """Names in the lines models wrote become <name:id> tokens, so they follow their owners
    out of the report (store.redact)."""
    people = report.get("people") or {}
    night = report.get("night") or {}
    for key in ("line", "sphere"):
        if night.get(key):
            night[key] = store.name_tokens(night[key], people)
    pick = report.get("pick") or {}
    for key in ("intro", "spread", "why"):
        if pick.get(key):
            pick[key] = store.name_tokens(pick[key], people)
    for mile in (report.get("arrivals") or {}).get("milestones", []):
        if mile.get("line"):
            mile["line"] = store.name_tokens(mile["line"], people)


def first_of_next_month(now: datetime) -> datetime:
    local = now.astimezone(PACIFIC)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return datetime(year, month, 1, tzinfo=PACIFIC).astimezone(timezone.utc)


async def _load(run_id: int, roster: Roster, client, now: datetime) -> Context | None:
    async with session_scope() as session:
        run = await session.get(ReplayRun, run_id)
        if run is None or run.status not in ("queued", "running", "paused"):
            return None
        guild_model = await session.scalar(select(GuildConfig.default_model).where(GuildConfig.guild_id == run.guild_id))
        # Bots that have since left aren't in the roster; the message table remembers them.
        gone_bots = set((await session.scalars(
            select(Message.author_id).where(Message.guild_id == run.guild_id, Message.author_is_bot.is_(True)).distinct()
        )).all())
        scope = await build_scope(session, run.guild_id, now=now, bots=roster.bot_ids() | {int(b) for b in gone_bots})
        state, progress, guild_id = dict(run.state or {}), dict(run.progress or {}), run.guild_id
    names = await stats.profile_names(guild_id, sorted(scope_authors_hint(state)))
    people = stats.People(roster, scope.hidden, fallback=names, seen=dict((state.get("report") or {}).get("people") or {}))
    ctx = Context(run_id=run_id, guild_id=guild_id, roster=roster, now=now, state=state, progress=progress,
                  scope=scope, people=people, guild_model=guild_model)

    async def spent(s: Spend) -> None:
        await store.add_spend(run_id, requests=s.requests, input_tokens=s.input_tokens, output_tokens=s.output_tokens, cost_usd=s.cost_usd)
        await store.save(run_id, state=_copy(ctx.state))

    ctx.asker = Asker(state, guild_model=guild_model, save=spent, client=client)
    return ctx


def scope_authors_hint(state: dict) -> set[int]:
    """Ids the report already names, so their fallback names load with the run."""
    return {int(k) for k in ((state.get("report") or {}).get("people") or {}) if str(k).isdigit()}


async def advance(run_id: int, roster: Roster, *, client=None) -> str:
    """Carry a run on from the step it's at. Returns the status it ends in."""
    now = utcnow()
    ctx = await _load(run_id, roster, client, now)
    if ctx is None:
        return "gone"
    async with session_scope() as session:
        run = await session.get(ReplayRun, run_id)
        start_step = run.step if run.step in STEPS else STEPS[0]
        run.status, run.error, run.paused_until, run.pause_reason = "running", "", None, ""
        run.updated_at = now
    log.info("replay %s: guild %s from %s", run_id, ctx.guild_id, start_step)
    try:
        for step in STEPS[STEPS.index(start_step):]:
            await store.save(run_id, step=step)
            await ctx.tell(force=True, phase=PHASE[step], **({"chapters": chapters(ctx.report)} if PHASE[step] == 3 else {}))
            await STEP_FN[step](ctx)
            await ctx.keep()
    except BudgetSpent:
        until = first_of_next_month(utcnow())
        await store.save(run_id, status="paused", paused_until=until, pause_reason="budget", state=_copy(ctx.state))
        log.info("replay %s: paused on the monthly budget until %s", run_id, until)
        return "paused"
    except RateLimitExceeded:
        until = next_reset()
        await store.save(run_id, status="paused", paused_until=until, pause_reason="quota", state=_copy(ctx.state))
        log.info("replay %s: paused on today's allowance until %s", run_id, until)
        return "paused"
    except asyncio.CancelledError:
        await store.save(run_id, state=_copy(ctx.state))
        raise
    except NothingToReplay as exc:
        await store.save(run_id, status="failed", error=str(exc), state=_copy(ctx.state))
        return "failed"
    except Exception as exc:  # noqa: BLE001 — any failure leaves the run for Try again
        log.exception("replay %s failed at %s", run_id, ctx.progress.get("phase"))
        await store.save(run_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:500], state=_copy(ctx.state))
        return "failed"
    report = ctx.report
    report["people"] = ctx.people.seen
    _name_tokens(report)
    # Anyone who opted out or was forgotten while this ran goes from what it kept.
    async with session_scope() as session:
        hidden = await hidden_users(session, ctx.guild_id, utcnow())
    report = store.redact(report, {str(u) for u in hidden}, resolve=False)
    await ctx.tell(force=True, phase=4, chapters=chapters(report))
    await store.save(run_id, status="done", finished_at=utcnow(), report=_copy(report), state={"calls": {}}, step="done")
    log.info("replay %s: done", run_id)
    return "done"


def eta_seconds(run: ReplayRun, now: datetime) -> int | None:
    """A rough time left, from the share of steps done and how long they've taken."""
    if run.status != "running" or run.step not in STEPS:
        return None
    done = STEPS.index(run.step)
    took = (now - (aware(run.started_at) or now)).total_seconds()
    if done == 0 or took <= 0:
        return None
    return int(took / done * (len(STEPS) - done))

