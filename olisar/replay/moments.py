"""Moments: stretches of history worth a model's read.

Reading every message would be some 21 million tokens on a 400,000-message server, so code
picks the windows and a model reads about 200 messages around each, one request apiece:

* the busiest days (local, 6 a.m. to 6 a.m.), in the channel that carried most of each, read
  around their busiest stretch. The best of these that's safe to show is the replay's
  busiest-day chapter.
* the day the most people arrived, read across channels for why (a link posted somewhere).
* the days the most different people talked, and the busiest day of each year, so the reading
  spans the server's life.

How many depends on the history's size (preflight.plan). Each answer says what the stretch was
about, names it, picks the messages that tell it, and says whether it's warm and whether it's
safe to put on a big screen. Code checks every id it cites, and nothing flagged as sensitive
or about one member's private life is shown.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from olisar.db.engine import session_scope
from olisar.db.models import SearchMessage
from olisar.replay import stats
from olisar.replay.preflight import plan
from olisar.replay.scope import aware
from olisar.replay.words import blocked, line_problem

WINDOW = 200
TEXT = 200   # characters of each message a model is shown

MOMENT_SYSTEM = """\
You read one stretch of a Discord server's history for a replay of that server: a short \
fullscreen story its admins watch and often show to everyone. Say what happened here, from \
these messages only.

Lines marked · are context: you can read them but not cite them.

Return only JSON in this shape:
{"what": "...", "title": "...", "kind": "...", "warm": 2, "sensitive": false, "about_one": false, \
"excerpt": ["m12", "m14"], "why_came": null}

- what: one short sentence, past tense, at most 14 words, on what most of the messages were \
about, the way a friend who was there would put it ("Most of them were about a candle."). No \
names, no numbers, no quotation marks.
- title: 2 to 5 words naming the moment, lowercase unless a name needs capitals ("the night the \
power went out"). At most one first name, written exactly as in the messages.
- kind: one of conversation, event, game, celebration, newcomers, announcement, conflict, \
moderation, other.
- warm: 0 to 3, how fond, funny or charming it would be to show everyone.
- sensitive: true if it's about conflict, members arguing, a ban or kick, moderation, a breakup, \
health, injury, grief, money, politics, religion or anything sexual, or if showing it could \
embarrass, hurt or single out anyone. If you're unsure, true.
- about_one: true if it's mainly about one member's private life.
- excerpt: 4 to 8 ids of messages that tell it, in order, the way it would be quoted. Only ids \
that appear below; leave out anything private or unkind.
- why_came: {why_came}
"""

WHY_CAME_ASKED = """one sentence, at most 16 words, on why many new people arrived that day, if \
the messages say (for example: "Someone posted the server on r/cozygames."). No names of \
members. null if the messages don't say."""
WHY_CAME_NOT = "null."


def _day_bounds(day: date, tz: int) -> tuple[datetime, datetime]:
    return stats.day_bounds(day, tz)


def candidates(tally: stats.Tally, tz: int, report: dict, n: int) -> list[dict]:
    """The windows to read, most important first: the busiest days, the day a crowd
    arrived, the days the most people talked, and each year's busiest day."""
    days = stats.daily(tally, tz)
    out: list[dict] = []
    seen: set[str] = set()

    def add(kind: str, day: date) -> None:
        key = day.isoformat()
        if key in seen or len(out) >= n:
            return
        seen.add(key)
        out.append({"key": f"{kind}:{key}", "kind": kind, "day": key})

    busiest = [d for d, _ in days.most_common(max(3, n // 3))]
    if busiest:
        add("busy", busiest[0])
    for m in (report.get("arrivals") or {}).get("milestones", []):
        if m.get("kind") == "burst":
            key = f"burst:{m['day']}"
            if key not in {c["key"] for c in out}:
                out.append({"key": key, "kind": "burst", "day": m["day"]})
    for d in busiest[1:]:
        add("busy", d)
    by_year: dict[int, tuple[date, int]] = {}
    for d, c in days.items():
        if c > by_year.get(d.year, (d, -1))[1]:
            by_year[d.year] = (d, c)
    for d, _ in sorted(by_year.values()):
        add("year", d)
    for d, _ in days.most_common(n * 3):
        add("busy", d)
    return out[:max(n, 1)]


async def _window(ctx, cand: dict) -> list:
    """The messages a model reads for one candidate."""
    tz = ctx.report.get("tz") or 0
    day = date.fromisoformat(cand["day"])
    if cand["kind"] == "burst":
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(hours=tz)
        lo, hi = start - timedelta(hours=12), start + timedelta(days=1)
    else:
        lo, hi = _day_bounds(day, tz)
    base = (*ctx.scope.where(), SearchMessage.created_at >= stats._naive(lo), SearchMessage.created_at < stats._naive(hi))
    async with session_scope() as session:
        if cand["kind"] != "burst":
            top = (
                await session.execute(
                    select(SearchMessage.channel_id, func.count()).where(*base)
                    .group_by(SearchMessage.channel_id).order_by(func.count().desc()).limit(1)
                )
            ).first()
            if top is None:
                return []
            base = (*base, SearchMessage.channel_id == top[0])
        rows = (await session.scalars(select(SearchMessage).where(*base).order_by(SearchMessage.created_at))).all()
    if len(rows) <= WINDOW:
        return list(rows)
    if cand["kind"] == "burst":
        step = len(rows) / WINDOW
        return [rows[int(i * step)] for i in range(WINDOW)]
    # The densest run of WINDOW messages: where the day was busiest.
    times = [aware(r.created_at).timestamp() for r in rows]
    best = min(range(len(rows) - WINDOW + 1), key=lambda i: times[i + WINDOW - 1] - times[i])
    return list(rows[best:best + WINDOW])


def _render(ctx, rows: list, tz: int) -> tuple[str, dict[str, int]]:
    """The window as the model reads it, and the handles it may cite."""
    lines, ids = [], {}
    for i, r in enumerate(rows):
        who = ctx.people.ref(r.author_id)
        name = ctx.people.seen[who]["name"] if who else "Someone"
        when = (aware(r.created_at) + timedelta(hours=tz)).strftime("%H:%M")
        text = " ".join((r.content or "").split())[:TEXT]
        if who and not blocked(text):
            handle = f"m{i + 1}"
            ids[handle] = i
            lines.append(f"{handle} {when} {name}: {text}")
        else:
            lines.append(f"· {when} {name}: {text}")
    return "\n".join(lines), ids


def _gap(a, b, between: int) -> str | None:
    """What the replay says between two quoted messages that aren't next to each other."""
    secs = (aware(b.created_at) - aware(a.created_at)).total_seconds()
    if secs < 20 * 60 and between < 15:
        return None
    if secs >= 3600:
        hours = round(secs / 3600)
        lead = f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        lead = f"{max(1, round(secs / 60))} minutes"
    return f"{lead} and {between:,} messages later" if between >= 15 else f"{lead} later"


async def read(ctx, cand: dict) -> dict | None:
    """Read one window. Returns the moment, or None if there was nothing to read."""
    tz = ctx.report.get("tz") or 0
    rows = await _window(ctx, cand)
    if len(rows) < 8:
        return None
    text, ids = _render(ctx, rows, tz)
    system = MOMENT_SYSTEM.replace("{why_came}", WHY_CAME_ASKED if cand["kind"] == "burst" else WHY_CAME_NOT)
    answer = await ctx.asker.ask(
        "moment:" + cand["key"], kind="lite", system=system, prompt=text,
        temperature=0.2, max_output_tokens=900,
    )
    if not answer:
        return {"key": cand["key"], "ok": False}
    picked = [ids[h] for h in (answer.get("excerpt") or []) if isinstance(h, str) and h in ids]
    picked = sorted(dict.fromkeys(picked))[:8]
    excerpt: list[dict] = []
    for n, idx in enumerate(picked):
        if n:
            gap = _gap(rows[picked[n - 1]], rows[idx], idx - picked[n - 1] - 1)
            if gap:
                excerpt.append({"gap": gap})
        excerpt.append(stats.message(rows[idx], ctx.scope, ctx.people))
    what = (answer.get("what") or "").strip()
    title = (answer.get("title") or "").strip().strip('"“”')
    why = (answer.get("why_came") or "").strip() if cand["kind"] == "burst" else ""
    names = {p["name"] for p in ctx.people.seen.values()}
    safe = not answer.get("sensitive", True) and not answer.get("about_one", False) \
        and answer.get("kind") not in ("conflict", "moderation")
    faces = list(dict.fromkeys(m["who"] for m in excerpt if m.get("who")))
    return {
        "key": cand["key"], "ok": True, "kind": cand["kind"], "day": cand["day"],
        "safe": safe, "warm": int(answer.get("warm") or 0),
        "what": what if what and not line_problem(what, max_words=16, max_sentences=1) and not any(ch.isdigit() for ch in what) else None,
        "title": title if title and len(title) <= 48 and len(title.split()) <= 6 and not blocked(title) else None,
        "why_came": why if why and not line_problem(why, max_words=20, max_sentences=1, names_known=names, names_allowed=set()) else None,
        "excerpt": excerpt if len(excerpt) >= 3 else [],
        "faces": faces[:5],
        "channel": excerpt[0]["chn"] if excerpt else None,
    }


async def run(ctx) -> None:
    state = ctx.state.setdefault("moments", {})
    report = ctx.report
    tz = report.get("tz") or 0
    if "candidates" not in state:
        tally = await ctx.tallied()
        state["candidates"] = candidates(tally, tz, report, plan(tally.messages)["moments"])
        await ctx.keep()
    found = state.setdefault("found", {})
    for cand in state["candidates"]:
        if cand["key"] in found:
            continue
        found[cand["key"]] = await read(ctx, cand)
        shown = [m for m in found.values() if m and m.get("ok") and m.get("safe")]
        avatar = lambda w: (ctx.people.seen.get(w) or {}).get("avatar")  # noqa: E731
        await ctx.tell(
            moments=len(shown),
            found=[{"faces": [a for a in map(avatar, m["faces"][:3]) if a], "year": m["day"][:4]} for m in shown[:8]],
            where=" · ".join(filter(None, ["#" + m["channel"] if (m := found[cand["key"]] or {}).get("channel") else None, cand["day"][:4]])),
        )
        await ctx.keep()
    await apply(ctx)


async def apply(ctx) -> None:
    """Put what was read into the report: the busiest-day chapter (the busiest day that's
    safe to show) and why a crowd arrived."""
    report = ctx.report
    state = ctx.state["moments"]
    found = state.get("found", {})
    tz = report.get("tz") or 0
    shown = [m for m in found.values() if m and m.get("ok") and m.get("safe")]
    report["moments"] = {"found": len(shown)}

    night = report.get("night")
    if night:
        tally = await ctx.tallied()
        order = [d.isoformat() for d, _ in stats.daily(tally, tz).most_common()]
        rank_of = {d: i for i, d in enumerate(order)}
        days = sorted((c for c in state.get("candidates", []) if c["kind"] in ("busy", "year")),
                      key=lambda c: rank_of.get(c["day"], len(order)))
        chosen = next(((c, m) for c in days if (m := found.get(c["key"])) and m.get("ok") and m.get("safe") and m.get("excerpt")), None)
        if chosen is None:
            report["night"] = None   # nothing about the busiest days is safe to show
        else:
            cand, m = chosen
            if cand["day"] != night["day"]:
                night = await stats.busiest_day(ctx.scope, tally, ctx.people, tz, rank=rank_of[cand["day"]])
            night = dict(night)
            night["rank"] = rank_of.get(cand["day"], 0)
            night["line"] = m.get("what")
            night["sphere"] = m.get("title")
            night["excerpt"] = m["excerpt"]
            night["faces"] = list(dict.fromkeys(m["faces"] + night.get("faces", [])))[:5]
            report["night"] = night
    for mile in (report.get("arrivals") or {}).get("milestones", []):
        if mile.get("kind") == "burst":
            m = found.get(f"burst:{mile['day']}")
            mile["line"] = m.get("why_came") if m and m.get("ok") and m.get("safe") else None
    await ctx.keep()
