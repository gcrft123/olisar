"""The replay's code-only chapters: everything that's a count, a date or a first.

Nothing here calls a model. It reads the search index (within ``Scope``), the live roster,
Olisar's own messages and the audit log, and writes the report sections the console plays:

* ``created``: when the server started, from the snowflake in its id.
* ``first``: its first three messages that can be shown.
* ``founders``: the first five people to post, with their first words and how much they've
  said since. Anyone who has left is "Someone", with no words.
* ``arrivals``: everyone still here by the day they joined, the 100th or 1,000th of them,
  and the day the most people arrived at once.
* ``night``: the busiest day, which ``moments`` reads to say what it was about.
* ``arrive``: when Olisar joined and its first reply.
* ``names``: what the persona was renamed to, and back, from the audit log.

Times are stored in UTC. ``tz`` is the server's own hour offset, guessed from its quietest
six hours (taken as 2 to 8 a.m.), so "the busiest day" runs from 6 a.m. to 6 a.m. there
and the console can say "a Sunday night" in the server's time rather than the viewer's.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import func, select

from olisar.db.engine import session_scope
from olisar.db.models import AuditLog, Message, Persona, SearchMessage, UserProfile, snowflake_time
from olisar.replay.scope import Roster, Scope, aware, clean_text

Progress = Callable[[dict], Awaitable[None]]

# A replay needs this much history to be worth playing (the start sheet's "too little").
MIN_MESSAGES = 500
MIN_DAYS = 30
DAY_STARTS = 6        # a "day" runs 6 a.m. to 6 a.m. local, so a long night stays one day
NIGHT = (20, 6)       # local hours that make the busiest day "the longest night"


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat().replace("+00:00", "Z") if value else None


def infer_offset(hours: list[int]) -> int | None:
    """The server's UTC offset in whole hours, from when it's quietest: the six quietest
    hours in a row are taken as 2 to 8 a.m. None with too little to go on."""
    if sum(hours) < 300:
        return None
    start = min(range(24), key=lambda h: sum(hours[(h + k) % 24] for k in range(6)))
    off = (5 - (start + 3)) % 24
    return off - 24 if off > 14 else off


def local_day(at: datetime, tz: int) -> date:
    return (aware(at) + timedelta(hours=tz - DAY_STARTS)).date()


def day_bounds(day: date, tz: int) -> tuple[datetime, datetime]:
    """The UTC span of a local day (6 a.m. to 6 a.m.)."""
    start = datetime(day.year, day.month, day.day, DAY_STARTS, tzinfo=timezone.utc) - timedelta(hours=tz)
    return start, start + timedelta(days=1)


def _naive(at: datetime) -> datetime:
    """SQLite compares the stored text, which has no zone, so binds go in the same way."""
    return aware(at).astimezone(timezone.utc).replace(tzinfo=None)


@dataclass
class People:
    """Everyone the report names, by id, and the one rule for who can be named: present in
    the server and not hidden. Anyone else is ``None`` in the report ("Someone")."""

    roster: Roster
    hidden: set[int]
    fallback: dict[int, str] = field(default_factory=dict)
    seen: dict[str, dict] = field(default_factory=dict)

    def nameable(self, uid: int | None) -> bool:
        return bool(uid) and int(uid) not in self.hidden and self.roster.present(int(uid))

    def ref(self, uid: int | None) -> str | None:
        if not self.nameable(uid):
            return None
        key = str(int(uid))
        if key not in self.seen:
            p = self.roster.person(int(uid))
            name = (p.name if p else "") or self.fallback.get(int(uid)) or "Someone"
            self.seen[key] = {"name": name, "avatar": p.avatar if p else None}
        return key



def message(row, scope: Scope, people: People, *, who: str | None = None) -> dict:
    """A message as the report keeps it."""
    author = who if who is not None else people.ref(row.author_id)
    return {
        "id": str(row.message_id),
        "ch": str(row.channel_id),
        "chn": scope.name(row.channel_id),
        "at": iso(row.created_at),
        "who": author,
        "text": clean_text(row.content, nameable=people.ref, channel_of=scope.channels.get),
    }


@dataclass
class Tally:
    """What the gather pass counts, channel by channel."""

    messages: int = 0
    channels: dict[int, int] = field(default_factory=dict)
    first_at: datetime | None = None
    last_at: datetime | None = None
    hours: list[int] = field(default_factory=lambda: [0] * 24)
    hourly: Counter = field(default_factory=Counter)        # "YYYY-MM-DD HH" (UTC) → messages
    authors: dict[int, list] = field(default_factory=dict)  # id → [messages, first at]


async def gather(scope: Scope, on_progress: Progress | None = None) -> Tally:
    """Read the index channel by channel: counts, when, and who. Reports how far it's got
    after each channel, which is what the remembering screen's count climbs with."""
    tally = Tally()
    async with session_scope() as session:
        sizes = (
            await session.execute(
                select(SearchMessage.channel_id, func.count())
                .where(*scope.where()).group_by(SearchMessage.channel_id)
            )
        ).all()
    total = sum(n for _, n in sizes)
    for channel_id, n in sorted(sizes, key=lambda r: -r[1]):
        base = (*scope.where(), SearchMessage.channel_id == channel_id)
        async with session_scope() as session:
            lo, hi = (await session.execute(select(func.min(SearchMessage.created_at), func.max(SearchMessage.created_at)).where(*base))).one()
            buckets = (
                await session.execute(
                    select(func.strftime("%Y-%m-%d %H", SearchMessage.created_at), func.count())
                    .where(*base).group_by(func.strftime("%Y-%m-%d %H", SearchMessage.created_at))
                )
            ).all()
            authors = (
                await session.execute(
                    select(SearchMessage.author_id, func.count(), func.min(SearchMessage.created_at))
                    .where(*base).group_by(SearchMessage.author_id)
                )
            ).all()
        tally.messages += n
        tally.channels[int(channel_id)] = n
        lo, hi = aware(lo), aware(hi)
        tally.first_at = min(filter(None, [tally.first_at, lo]), default=None)
        tally.last_at = max(filter(None, [tally.last_at, hi]), default=None)
        for bucket, count in buckets:
            if bucket:
                tally.hourly[bucket] += count
                tally.hours[int(bucket[-2:])] += count
        for uid, count, first in authors:
            first = aware(first)
            entry = tally.authors.setdefault(int(uid), [0, first])
            entry[0] += count
            if first and (entry[1] is None or first < entry[1]):
                entry[1] = first
        if on_progress:
            busiest_year = Counter({b[:4]: c for b, c in buckets}).most_common(1)
            where = f"#{scope.name(channel_id)}" + (f" · {busiest_year[0][0]}" if busiest_year else "")
            await on_progress({"read": tally.messages, "total": total, "where": where})
    return tally


def daily(tally: Tally, tz: int) -> Counter:
    """Messages per local day (6 a.m. to 6 a.m.)."""
    days: Counter = Counter()
    for bucket, count in tally.hourly.items():
        at = datetime.strptime(bucket, "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        days[local_day(at, tz)] += count
    return days


# ── The sections ──

async def first_messages(scope: Scope, people: People) -> dict | None:
    """The first three messages that can be shown, from the channel the first was in."""
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(SearchMessage).where(*scope.where()).order_by(SearchMessage.created_at).limit(60)
            )
        ).all()
    rows = [r for r in rows if people.nameable(r.author_id) and (r.content or "").strip()]
    if not rows:
        return None
    channel = rows[0].channel_id
    picked = [r for r in rows if r.channel_id == channel][:3]
    return {"channel": scope.name(channel), "messages": [message(r, scope, people) for r in picked]}


async def founders(scope: Scope, tally: Tally, people: People) -> list[dict]:
    """The first five people to post. Anyone who has left keeps their place as Someone."""
    first = sorted(((at, uid) for uid, (_, at) in tally.authors.items() if at), key=lambda x: x[0])[:5]
    out = []
    for at, uid in first:
        who = people.ref(uid)
        entry = {"who": who, "at": iso(at), "count": tally.authors[uid][0] if who else None, "msg": None}
        if who:
            async with session_scope() as session:
                row = (
                    await session.scalars(
                        select(SearchMessage).where(*scope.where(), SearchMessage.author_id == uid)
                        .order_by(SearchMessage.created_at).limit(1)
                    )
                ).first()
            if row is not None:
                entry["msg"] = message(row, scope, people)
        out.append(entry)
    return out


MILESTONES = (10, 100, 1000, 10000, 100000)


def arrivals(roster: Roster, people: People, *, start: datetime, now: datetime, bot_joined: datetime | None, tz: int = 0) -> dict:
    """Everyone still here by the day they joined, as a running count the console plays,
    with up to two round-number arrivals and the day the most people came at once."""
    joins = sorted(((aware(at), uid) for uid, at in roster.join_dates() if at), key=lambda x: x[0])
    start = min([start] + [at for at, _ in joins[:1]])
    per_day: Counter = Counter(at.date() for at, _ in joins)
    points: list[list[int]] = []
    count = 0
    for day in sorted(per_day):
        count += per_day[day]
        points.append([(day - start.date()).days, count])
    if len(points) > 360:   # keep the shape, not every day
        step = len(points) / 360
        points = [points[int(i * step)] for i in range(360)] + [points[-1]]
    total = len(joins)
    marks = [n for n in MILESTONES if n <= total][-2:]
    milestones = []
    for n in marks:
        at, uid = joins[n - 1]
        mile = {"kind": "nth", "n": n, "at": iso(at), "who": people.ref(uid)}
        if bot_joined and timedelta(0) <= aware(bot_joined) - at <= timedelta(days=60):
            mile["weeks_before"] = max(1, round((aware(bot_joined) - at).days / 7))
        milestones.append(mile)
    # The day the most people arrived, if it stands out: a link posted somewhere, most
    # likely, which the moments step reads the day around to find.
    local = Counter((at + timedelta(hours=tz)).date() for at, _ in joins)
    if local:
        day, n = local.most_common(1)[0]
        if n >= max(12, 6 * statistics.median(local.values()), 0.02 * total):
            start_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=tz)
            milestones.append({"kind": "burst", "day": day.isoformat(), "at": iso(start_utc), "count": n, "line": None})
    milestones.sort(key=lambda m: m["at"])
    return {"start": iso(start), "end": iso(now), "points": points, "now": total, "milestones": milestones}


async def busiest_day(scope: Scope, tally: Tally, people: People, tz: int, *, rank: int = 0) -> dict | None:
    """The day the server said the most, the channel most of it was in, and who was there.
    ``moments`` reads it to say what it was about."""
    ranked = daily(tally, tz).most_common(rank + 1)
    if len(ranked) <= rank:
        return None
    day, count = ranked[rank]
    lo, hi = day_bounds(day, tz)
    window = (*scope.where(), SearchMessage.created_at >= _naive(lo), SearchMessage.created_at < _naive(hi))
    async with session_scope() as session:
        by_channel = (
            await session.execute(
                select(SearchMessage.channel_id, func.count()).where(*window)
                .group_by(SearchMessage.channel_id).order_by(func.count().desc()).limit(1)
            )
        ).first()
        if by_channel is None:
            return None
        channel = by_channel[0]
        in_channel = (*window, SearchMessage.channel_id == channel)
        authors = (
            await session.execute(
                select(SearchMessage.author_id, func.count()).where(*in_channel)
                .group_by(SearchMessage.author_id).order_by(func.count().desc()).limit(20)
            )
        ).all()
        span = (await session.execute(select(func.min(SearchMessage.created_at), func.max(SearchMessage.created_at)).where(*in_channel))).one()
        hours = (
            await session.execute(
                select(func.strftime("%H", SearchMessage.created_at), func.count()).where(*window)
                .group_by(func.strftime("%H", SearchMessage.created_at))
            )
        ).all()
    at_night = sum(c for h, c in hours if ((int(h) + tz) % 24 >= NIGHT[0] or (int(h) + tz) % 24 < NIGHT[1]))
    faces = [people.ref(uid) for uid, _ in authors if people.nameable(uid)][:5]
    return {
        "day": day.isoformat(),
        "channel": scope.name(channel),
        "channel_id": str(channel),
        "count": count,
        "in_channel": by_channel[1],
        "night": at_night >= count / 2,
        "from": iso(span[0]),
        "to": iso(span[1]),
        "faces": faces,
        # Filled in by the moments and writing steps.
        "line": None,
        "sphere": None,
        "excerpt": [],
    }


async def arrival(scope: Scope, people: People, roster: Roster, guild_row_created: datetime | None) -> dict | None:
    """When Olisar joined, and its first reply that can be shown (with what it answered)."""
    joined = aware(roster.me_joined_at) or aware(guild_row_created)
    if joined is None:
        return None
    out = {"at": iso(joined), "reply": []}
    async with session_scope() as session:
        mine = (
            await session.scalars(
                select(Message).where(
                    Message.guild_id == scope.guild_id,
                    Message.author_id == roster.me_id,
                    Message.channel_id.in_(list(scope.channels)),
                ).order_by(Message.created_at).limit(25)
            )
        ).all()
        for reply in mine:
            if not (reply.content or "").strip():
                continue
            ask = None
            if reply.reply_to_message_id:
                ask = (await session.scalars(select(Message).where(Message.message_id == reply.reply_to_message_id))).first()
                if ask is None:
                    ask = (await session.scalars(select(SearchMessage).where(SearchMessage.message_id == reply.reply_to_message_id))).first()
                if ask is not None and (not people.nameable(ask.author_id) or ask.author_id in scope.skip):
                    continue   # the question was someone who can't be shown
            rows = []
            if ask is not None:
                rows.append(message(ask, scope, people))
            bot = message(reply, scope, people, who="bot")
            bot["trigger"] = reply.trigger or ""
            rows.append(bot)
            out["reply"] = rows
            break
    return out


async def renames(guild_id: int, people: People) -> list[dict]:
    """Every time the persona's name changed, oldest first. Only the audit log keeps these,
    for changes made in the console or by asking in chat."""
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "update_persona", AuditLog.target_id == str(guild_id),
                ).order_by(AuditLog.ts)
            )
        ).all()
    out = []
    for row in rows:
        before, after = row.before or {}, row.after or {}
        old, new = (before.get("name") or "").strip(), (after.get("name") or "").strip()
        if not old or not new or old == new:
            continue
        actor = int(row.actor) if (row.actor or "").isdigit() else None
        out.append({
            "at": iso(row.ts), "from": old, "to": new,
            "by": people.ref(actor) if actor else None,
            "via": "chat" if after.get("via") == "chat" else "console",
        })
    return out[-10:]


async def persona_name(guild_id: int, fallback: str) -> str:
    async with session_scope() as session:
        name = await session.scalar(select(Persona.name).where(Persona.guild_id == guild_id))
    return (name or "").strip() or fallback


async def profile_names(guild_id: int, ids: list[int]) -> dict[int, str]:
    """Names from the roster sync, for anyone the live roster can't name (not cached)."""
    if not ids:
        return {}
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(UserProfile.user_id, UserProfile.display_name)
                .where(UserProfile.guild_id == guild_id, UserProfile.user_id.in_(ids[:2000]))
            )
        ).all()
    return {int(u): n for u, n in rows if n}


def enough(tally: Tally) -> bool:
    if tally.messages < MIN_MESSAGES or not tally.first_at or not tally.last_at:
        return False
    return (tally.last_at - tally.first_at) >= timedelta(days=MIN_DAYS)


def years_between(start: datetime, end: datetime) -> int:
    return max(1, int((aware(end) - aware(start)).days // 365.25))


def created_at(guild_id: int) -> datetime:
    return snowflake_time(int(guild_id))
