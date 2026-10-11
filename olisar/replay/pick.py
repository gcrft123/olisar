"""Olisar's pick: the one thing that most belongs to this server (the replay's chapter 4).

The pipeline is design/memory-replay/PICK.md's, and the prompts are its first draft:

1. Candidates (code). Detectors propose things that recur: the glossary, the server's own words
   and phrases, custom emoji and stickers, weekly rituals, named things ("the lighthouse"), and
   the motifs channel summaries keep mentioning. Each is counted the same way, by phrase match
   in the search index, and goes through PICK.md's hard filters. The top 12 go on, at most 4
   from any one detector.
2. Evidence (code). For each: its counts, its earliest uses with context, and later uses
   sampled across time and channels. Messages get short ids only code can map back.
3. The judge (the strongest model in the chain, thinking on). Scores, disqualifications and up
   to three finalists, citing ids only. Code checks every id, re-orders by its own total and
   re-applies the floor.
4. The check (Flash-Lite). Per finalist, until one passes: does each quote use the thing in
   this sense, and could any of it hurt anyone. Anything short of a clear yes fails it: there's
   no review step, so this is the last gate.
5. The writer (the server's own model, in persona). Two lines, checked in code, one retry,
   then a template.

One difference from PICK.md: it bundles no general-English frequency list (the one worth
having would add tens of megabytes to the desktop app), so the "server words" detector's
unusualness comes from the motifs request instead, which is asked which of the shortlisted
words are ordinary English any server would use. Glossary entries skip that screen: Olisar
already judged them the server's own.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import bindparam, func, select, text

from olisar.db.engine import session_scope
from olisar.db.models import ChannelSummary, GuildChannelInfo, GuildConfig, GuildFact, GuildRole, Persona, SearchMessage
from olisar.replay import stats
from olisar.replay.scope import aware
from olisar.replay.words import blocked, is_filler, line_problem, tokens

log = logging.getLogger("olisar.replay")

SAMPLE = 60000          # messages read for discovery; counts come from the whole index
SHORTLIST = 12
PER_DETECTOR = 4
TEXT = 200
STAFFISH = re.compile(r"mod|staff|admin|nsfw|private|log|appeal|ticket|report|secret|vent", re.IGNORECASE)
WEEKDAYS = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]

JUDGE_SYSTEM = """\
You're choosing one chapter of a replay of a Discord server's history. The replay is a short \
fullscreen story that the server's admins watch and often show to everyone. Most chapters are \
fixed: when the server started, who arrived, its busiest day. This one is a free pick: the single \
thing that most belongs to this server. It can be anything: a word or phrase, a running joke, a \
ritual or regular event, something members made, a place, a game, a custom emoji.

The test: someone who has been here a year sees it and grins, and someone who joined last week \
understands it from one line.

You'll get candidates the server's data turned up, each with counts and messages. Judge only from \
that evidence. Score every candidate from 0 (not at all) to 3 (clearly) on each of these:

- ours: its meaning or importance is specific to this server. A generic activity ("gaming", \
"movie night") scores 0 unless the evidence shows what's particular about it here: who runs it, \
how it goes, what it's called.
- shared: many different members take part, in more than one channel. Mostly one person's habit \
scores 0 or 1 however often they do it.
- origin: one of the earliest messages clearly shows it beginning: coined, proposed, invented, \
first held, first built.
- lasting: it comes back over months or years. Still going scores highest.
- warm: it's fond, funny, or charmingly absurd, and showing it to the whole server would \
embarrass or hurt no one.
- plain: it can be explained to a stranger in ten plain words or fewer.

Disqualify, with a short reason and no scores, any candidate that:
- is about a conflict, a ban or kick, a breakup, members arguing, moderation, money, health, \
injury, grief, politics, religion, or anything sexual
- is a joke at someone's expense, or about a member's body, looks, age, family, or private life
- is mainly about one member, even fondly: their catchphrase, their pet, their cooking
- is internet slang or a meme that means the same thing everywhere
- is about Olisar, or is something another chapter already covers
- has evidence that doesn't hold together: the messages don't show the same thing, or the word \
is mostly used in its ordinary meaning

Be strict about warm. If you're unsure whether something could hurt someone, disqualify it.

Then name up to 3 finalists, best first. A finalist needs at least 2 on ours, shared, origin and \
warm. If no candidate qualifies, return no finalists: no chapter is better than a weak one.

For each finalist give:
- kind: one of word, joke, ritual, made, place, game, emoji, other.
- title: the thing's own name, written the way the server writes it.
- meaning: what it is here, in ten words or fewer, for someone who has never been on the server. \
No names, no numbers, no quotation marks.
- origin: the id of the one message that best shows it starting. It must be among the \
candidate's earliest messages and use the thing in the sense you mean.
- trail: 6 to 10 ids of later messages that show it carrying on, spread across time and, where \
possible, channels. Leave out any message that uses the word in its ordinary meaning.
- why: one sentence for the admin on why this one, in plain words.

Cite only ids that appear in the evidence. Don't quote or paraphrase messages and don't state \
counts; the replay shows the real messages and numbers.

Return only JSON in this shape:
{schema}
"""

SCHEMA = """{
  "candidates": [
    {"id": "c03", "disqualified": "a joke about one member's cooking"},
    {"id": "c07", "scores": {"ours": 3, "shared": 3, "origin": 3, "lasting": 3, "warm": 3, "plain": 3}}
  ],
  "finalists": [
    {
      "id": "c07", "kind": "word", "title": "goose", "meaning": "a win nobody saw coming",
      "origin": "m201", "trail": ["m203", "m204", "m205", "m206"],
      "why": "Most of the server uses it, and it started with one message in #games."
    }
  ]
}"""

CHECK_SYSTEM = """\
You check one chapter of a Discord server's replay before an admin sees it. The chapter is about \
"{title}", which on this server means: {meaning}.

For each message below, answer "yes" if it uses "{title}" in that sense, "no" if it uses it some \
other way, and "unclear" if you can't tell.

Then imagine the chapter on a big screen in front of the whole server, and answer:
- hurts: could anyone named or quoted reasonably be embarrassed, hurt, or singled out?
- sensitive: does it touch conflict, moderation, health, grief, money, politics, religion, or sex?

Return only JSON: {"uses": {"m201": "yes"}, "hurts": false, "sensitive": false, "reason": ""}
"""

WRITER_SYSTEM = """\
You're {name}, a member of the Discord server {server}, narrating one chapter of a replay of its \
history for the people in it. {persona}

This chapter is about {title}: {meaning}. It started with this message, which the replay shows \
under your words:
{origin_author} in {origin_channel}, {origin_date}: {origin_text}

Write two lines.
- intro, shown above that message: what {title} is here, and who started it. Two short sentences \
at most.
- spread, shown while it spreads across the screen: how it carried on. One or two short \
sentences. Where a number belongs, write {people} for how many members took part and \
{count} for how many times; the replay fills them in.

Voice: first person, plain, a little dry, fond without gushing, like a friend who was paying \
attention. Use people's names exactly as given, lowercase if they're lowercase. "Someone" stays \
"Someone".

Don't use exclamation marks, rhetorical questions, or emoji. Don't call anything legendary, \
iconic, a core memory, an era, or a vibe. Don't explain the joke, quote the messages, mention \
being a bot, or say anything the evidence below doesn't support.

Evidence, for understanding only:
{trail}

Return only JSON: {"intro": "...", "spread": "..."}
"""

MOTIFS_SYSTEM = """\
You help find what belongs to one Discord server, for a replay of its history.

1. Below are summaries of its channels. List up to 12 things that keep coming back in them: \
running jokes, rituals or regular events, things members made, places, games, nicknames for \
things. Each as the server would write it, in 1 to 4 words. Nothing about one member's private \
life, conflict, moderation, or anything sensitive.

2. Below that is a list of words and phrases this server uses a lot. List the ones that are \
ordinary English any server would use, or internet slang that means the same thing everywhere.

Return only JSON: {"motifs": ["..."], "ordinary": ["..."]}
"""


# ── Reading the index ──

def _fts(phrase: str) -> str:
    return '"' + phrase.replace('"', '""') + '"'


async def uses(ctx, phrase: str, *, skip_window: tuple | None) -> list:
    """Every use of a phrase in scope, oldest first: (row id, message id, channel, author, at)."""
    sql = text(
        "SELECT m.id, m.message_id, m.channel_id, m.author_id, m.created_at FROM search_message_fts f "
        "JOIN search_message m ON m.id = f.rowid WHERE search_message_fts MATCH :q AND m.guild_id = :g "
        "AND m.channel_id IN :chs AND m.author_id NOT IN :skip ORDER BY m.created_at LIMIT 50000"
    ).bindparams(bindparam("chs", expanding=True), bindparam("skip", expanding=True))
    async with session_scope() as session:
        try:
            rows = (await session.execute(sql, {
                "q": _fts(phrase), "g": ctx.scope.guild_id, "chs": list(ctx.scope.channels), "skip": list(ctx.scope.skip),
            })).all()
        except Exception:  # noqa: BLE001 — a phrase FTS can't parse is no candidate
            return []
    # Raw SQL hands back the stored text; every time here is UTC.
    rows = [(r[0], r[1], r[2], r[3], aware(r[4] if isinstance(r[4], datetime) else datetime.fromisoformat(r[4]))) for r in rows]
    if skip_window:
        lo, hi = skip_window
        rows = [r for r in rows if not (lo <= r[4] < hi)]
    return rows


async def rows_by_id(ids: list[int]) -> dict[int, SearchMessage]:
    async with session_scope() as session:
        rows = (await session.scalars(select(SearchMessage).where(SearchMessage.id.in_(ids)))).all()
    return {r.id: r for r in rows}


async def around(ctx, row: SearchMessage, before: int, after: int) -> tuple[list, list]:
    """The messages either side of one, in its channel and in scope."""
    base = (*ctx.scope.where(), SearchMessage.channel_id == row.channel_id)
    async with session_scope() as session:
        prev = (await session.scalars(select(SearchMessage).where(*base, SearchMessage.created_at < row.created_at)
                                      .order_by(SearchMessage.created_at.desc()).limit(before))).all() if before else []
        nxt = (await session.scalars(select(SearchMessage).where(*base, SearchMessage.created_at > row.created_at)
                                     .order_by(SearchMessage.created_at).limit(after))).all() if after else []
    return list(reversed(prev)), list(nxt)


# ── 1. Candidates ──

async def _names(ctx) -> set[str]:
    """Everything a pick can't be: members' names, channels, roles, and Olisar's own."""
    async with session_scope() as session:
        roles = (await session.scalars(select(GuildRole.name).where(GuildRole.guild_id == ctx.guild_id))).all()
        channels = (await session.scalars(select(GuildChannelInfo.name).where(GuildChannelInfo.guild_id == ctx.guild_id))).all()
        persona = await session.scalar(select(Persona.name).where(Persona.guild_id == ctx.guild_id))
        triggers = await session.scalar(select(GuildConfig.name_triggers).where(GuildConfig.guild_id == ctx.guild_id))
    out = {n.lower() for n in (*roles, *channels, persona or "", *(triggers or []), "olisar") if n}
    out |= {n.lower() for n in ctx.roster.names()}
    return {n.strip("#@ ") for n in out if n.strip("#@ ")}


async def _sample(ctx, total: int) -> list:
    step = max(1, total // SAMPLE)
    async with session_scope() as session:
        rows = (await session.execute(
            select(SearchMessage.author_id, SearchMessage.channel_id, SearchMessage.content, SearchMessage.created_at)
            .where(*ctx.scope.where(), (SearchMessage.id % step) == 0).limit(SAMPLE)
        )).all()
    return rows


_EMOJI = re.compile(r"<a?:([A-Za-z0-9_~]+):(\d+)>")
_STICKER = re.compile(r"\[sticker: ([^\]]{1,60})\]")
_THE = re.compile(r"(?<![\w'])the ([^\W\d_][\w'’]{2,}(?: [^\W\d_][\w'’]{2,})?)", re.UNICODE)
_CAPS = re.compile(r"(?<=[a-z,;] )((?:[A-Z][a-z'’]{2,}(?: |$)){1,3})")


def _proposals_from_sample(sample: list, names: set[str], tz: int) -> tuple[dict, list[str]]:
    """What the sample turns up, by detector. Returns {fts phrase: proposal} and the
    server-words shortlist the motifs request screens for ordinary English."""
    grams: Counter = Counter()
    for _, _, content, _ in sample:
        toks = tokens(content)
        for i, w in enumerate(toks):
            grams[w] += 1
            if i + 1 < len(toks):
                grams[w + " " + toks[i + 1]] += 1
    keep = {g for g, n in grams.items() if n >= 6 and not is_filler(g) and g not in names and not blocked(g)}
    people: dict[str, set] = defaultdict(set)
    days: dict[str, Counter] = defaultdict(Counter)
    weeks: dict[str, set] = defaultdict(set)
    emoji: dict[str, tuple[str, set]] = {}
    stickers: dict[str, set] = defaultdict(set)
    named: dict[str, set] = defaultdict(set)
    for author, _, content, at in sample:
        toks = tokens(content)
        seen = set()
        for i, w in enumerate(toks):
            for g in (w, w + " " + toks[i + 1] if i + 1 < len(toks) else None):
                if g and g in keep and g not in seen:
                    seen.add(g)
                    people[g].add(author)
                    local = aware(at) + timedelta(hours=tz)
                    days[g][local.weekday()] += 1
                    weeks[g].add(local.isocalendar()[:2])
        for name, eid in _EMOJI.findall(content or ""):
            emoji.setdefault(eid, (name, set()))[1].add(author)
        for st in _STICKER.findall(content or ""):
            stickers[st.strip().lower()].add(author)
        for m in _THE.findall((content or "").lower()):
            phrase = m.replace("’", "'")
            if not is_filler(phrase) and phrase not in names:
                named[phrase].add(author)
        for m in _CAPS.findall(content or ""):
            phrase = m.strip().lower()
            if not is_filler(phrase) and phrase not in names:
                named[phrase].add(author)

    out: dict[str, dict] = {}
    words = sorted(people, key=lambda g: -len(people[g]))[:80]
    for g in words:
        out[g] = {"phrase": g, "title": g, "detector": "words"}
        # Said on one weekday most weeks: a ritual rather than a word.
        if days[g]:
            weekday, on_it = days[g].most_common(1)[0]
            if on_it / sum(days[g].values()) >= 0.6 and len(weeks[g]) >= 4:
                out[g] = {"phrase": g, "title": g, "detector": "ritual", "weekday": weekday}
    for eid, (name, who) in sorted(emoji.items(), key=lambda kv: -len(kv[1][1]))[:10]:
        if len(who) >= 3 and name.lower() not in names:
            out[eid] = {"phrase": eid, "title": f":{name}:", "detector": "emoji"}
    for st, who in sorted(stickers.items(), key=lambda kv: -len(kv[1]))[:5]:
        if len(who) >= 3:
            out["sticker " + st] = {"phrase": "sticker " + st, "title": st, "detector": "emoji"}
    for phrase, who in sorted(named.items(), key=lambda kv: -len(kv[1]))[:15]:
        if len(who) >= 3 and phrase not in out:
            title = ("the " + phrase) if phrase.split()[0] not in ("the",) and len(phrase.split()) <= 2 else phrase
            out[phrase] = {"phrase": phrase, "title": title, "detector": "named"}
    return out, words[:60]


async def _rituals_by_channel(ctx, tz: int, names: set[str], sample: list) -> dict[str, dict]:
    """Channels whose activity pulses on one weekday, named by their commonest phrase then."""
    tally = await ctx.tallied()
    out: dict[str, dict] = {}
    for channel_id, n in sorted(tally.channels.items(), key=lambda kv: -kv[1])[:20]:
        if n < 60:
            continue
        async with session_scope() as session:
            rows = (await session.execute(
                select(func.strftime("%w %Y-%W", SearchMessage.created_at, f"{tz:+d} hours"), func.count())
                .where(*ctx.scope.where(), SearchMessage.channel_id == channel_id)
                .group_by(func.strftime("%w %Y-%W", SearchMessage.created_at, f"{tz:+d} hours"))
            )).all()
        by_day: Counter = Counter()
        weeks: dict[int, set] = defaultdict(set)
        for key, count in rows:
            wd, wk = key.split(" ")
            by_day[int(wd)] += count
            weeks[int(wd)].add(wk)
        if not by_day:
            continue
        wd, top = by_day.most_common(1)[0]
        if top / sum(by_day.values()) < 0.4 or len(weeks[wd]) < 8:
            continue
        weekday = (wd - 1) % 7   # SQLite's %w counts from Sunday, Python's from Monday
        grams: Counter = Counter()
        for _, ch, content, at in sample:
            if ch == channel_id and (aware(at) + timedelta(hours=tz)).weekday() == weekday:
                toks = tokens(content)
                for i in range(len(toks) - 1):
                    g = toks[i] + " " + toks[i + 1]
                    if not is_filler(g) and g not in names:
                        grams[g] += 1
        if grams:
            g = grams.most_common(1)[0][0]
            out[g] = {"phrase": g, "title": g, "detector": "ritual", "weekday": weekday}
    return out


async def _glossary(ctx, names: set[str]) -> dict[str, dict]:
    async with session_scope() as session:
        facts = (await session.execute(
            select(GuildFact.subject, GuildFact.fact, GuildFact.source_channel_id).where(GuildFact.guild_id == ctx.guild_id)
            .order_by(GuildFact.mentions.desc()).limit(60)
        )).all()
        known = set((await session.scalars(select(GuildChannelInfo.channel_id).where(GuildChannelInfo.guild_id == ctx.guild_id))).all())
    out = {}
    for subject, fact, source in facts:
        # Something said in a DM can be filed under the server; it never shows here.
        if source is not None and int(source) not in known:
            continue
        phrase = " ".join(tokens(subject))
        if not phrase or phrase in names or blocked(subject) or blocked(fact or ""):
            continue
        out[phrase] = {"phrase": phrase, "title": subject.strip(), "detector": "glossary", "gloss": (fact or "")[:200]}
    return out


async def _motifs(ctx, words: list[str]) -> dict:
    """PICK.md's motifs pass, which also screens the server-words list for ordinary English."""
    async with session_scope() as session:
        rows = (await session.execute(
            select(ChannelSummary.channel_id, ChannelSummary.summary).where(
                ChannelSummary.guild_id == ctx.guild_id, ChannelSummary.channel_id.in_(list(ctx.scope.channels)),
            ).order_by(ChannelSummary.id.desc()).limit(40)
        )).all()
    summaries = "\n".join(f"#{ctx.scope.name(ch)}: {' '.join((s or '').split())[:600]}" for ch, s in rows)
    prompt = f"Channel summaries:\n{summaries or '(none)'}\n\nWords and phrases:\n" + "\n".join(words)
    return await ctx.asker.ask("motifs", kind="lite", system=MOTIFS_SYSTEM, prompt=prompt, temperature=0.2, max_output_tokens=800) or {}


def _months(rows) -> int:
    return len({(aware(r[4]).year, aware(r[4]).month) for r in rows})


async def candidates(ctx) -> list[dict]:
    report = ctx.report
    tz = report.get("tz") or 0
    tally = await ctx.tallied()
    names = await _names(ctx)
    sample = await _sample(ctx, tally.messages)
    proposals, words = _proposals_from_sample(sample, names, tz)
    proposals.update(await _rituals_by_channel(ctx, tz, names, sample))
    motifs = await _motifs(ctx, words)
    ordinary = {" ".join(tokens(w)) for w in motifs.get("ordinary") or [] if isinstance(w, str)}
    for g in list(proposals):
        if proposals[g]["detector"] == "words" and g in ordinary:
            del proposals[g]
    for m in motifs.get("motifs") or []:
        phrase = " ".join(tokens(m)) if isinstance(m, str) else ""
        if phrase and phrase not in proposals and not is_filler(phrase) and phrase not in names:
            proposals[phrase] = {"phrase": phrase, "title": m.strip(), "detector": "motifs"}
    proposals.update(await _glossary(ctx, names))   # glossary wins a tie: the strongest source

    # Not the subject of another chapter: the busiest day's messages don't count.
    night = report.get("night")
    skip = stats.day_bounds(date.fromisoformat(night["day"]), tz) if night else None
    posters = max(1, len(tally.authors))
    floor = max(6, math.ceil(0.03 * posters))
    scored = []
    for p in list(proposals.values())[:80]:
        rows = await uses(ctx, p["phrase"], skip_window=skip)
        if not rows:
            continue
        per_author = Counter(r[3] for r in rows)
        people = len(per_author)
        channels = Counter(r[2] for r in rows)
        months = _months(rows)
        top_share = per_author.most_common(1)[0][1] / len(rows)
        staff = sum(n for ch, n in channels.items() if STAFFISH.search(ctx.scope.name(ch) or "") or ctx.roster.public(ch) is False)
        distinct_days = len({(aware(r[4]) + timedelta(hours=tz)).date() for r in rows})
        own_channel = p["detector"] == "ritual" and len(channels) == 1 and distinct_days >= 20
        # The hard filters (PICK.md).
        if people < floor or top_share > 0.35 or months < 3:
            continue
        if len(channels) < 2 and not own_channel:
            continue
        if staff / len(rows) >= 0.4:
            continue
        # The origin: the earliest use by someone who can be named, in a channel everyone sees.
        origin = next((r for r in rows[:25] if ctx.people.nameable(r[3]) and ctx.roster.public(r[2]) is not False
                       and not STAFFISH.search(ctx.scope.name(r[2]) or "")), None)
        if origin is None:
            continue
        unusual = 1.5 if p["detector"] == "glossary" else 1.0
        score = math.log(people) * math.sqrt(months) * len(channels) ** 0.3 * (1 - top_share) * unusual
        scored.append({**p, "rows": rows, "score": score, "people": people, "channels": len(channels),
                       "months": months, "top_share": top_share, "days": distinct_days})
    scored.sort(key=lambda c: -c["score"])
    chosen, per = [], Counter()
    for c in scored:
        if per[c["detector"]] >= PER_DETECTOR:
            continue
        per[c["detector"]] += 1
        chosen.append(c)
        if len(chosen) == SHORTLIST:
            break
    return chosen


# ── 2. Evidence ──

def _line(ctx, row, tz: int, handle: str | None) -> str:
    who = ctx.people.ref(row.author_id)
    name = ctx.people.seen[who]["name"] if who else "Someone"
    at = (aware(row.created_at) + timedelta(hours=tz)).strftime("%Y-%m-%d %H:%M")
    body = " ".join((row.content or "").split())[:TEXT]
    lead = handle if handle and who and not blocked(body) else "·"
    return f"    {lead} #{ctx.scope.name(row.channel_id)} {at} {name}: {body}"


async def evidence(ctx, c: dict, index: int) -> dict:
    """One candidate's block for the judge, and the messages its ids stand for."""
    tz = ctx.report.get("tz") or 0
    rows = c["rows"]
    earliest_ids = [r[0] for r in rows if ctx.people.nameable(r[3])][:4]
    later_pool = rows[len(rows) // 10:] if len(rows) > 40 else rows[4:]
    later_ids: list[int] = []
    if later_pool:
        # Spread across time (eight slices) and, within each, the channel least seen so far.
        seen_ch: Counter = Counter()
        for k in range(8):
            part = later_pool[k * len(later_pool) // 8:(k + 1) * len(later_pool) // 8]
            part = [r for r in part if ctx.people.nameable(r[3]) and r[0] not in earliest_ids]
            if part:
                pick = min(part, key=lambda r: seen_ch[r[2]])
                seen_ch[pick[2]] += 1
                later_ids.append(pick[0])
    full = await rows_by_id(earliest_ids + later_ids)
    handles: dict[str, dict] = {}
    lines = [f"c{index:02d} · from {c['detector']} · \"{c['title']}\""]
    if c.get("gloss"):
        lines.append(f"  glossary: {c['title']}: {c['gloss']}")
    first, last = aware(rows[0][4]), aware(rows[-1][4])
    lines.append(f"  uses {len(rows):,} · people {c['people']} · channels {c['channels']} · months {c['months']}"
                 f" · first {first:%Y-%m-%d} · last {last:%Y-%m-%d} · top person {round(c['top_share'] * 100)}%")
    n = 0

    def cite(row) -> str:
        nonlocal n
        n += 1
        handle = f"m{index}{n:02d}"
        handles[handle] = stats.message(row, ctx.scope, ctx.people)
        return handle

    lines.append("  earliest:")
    earliest_handles = []
    for rid in earliest_ids:
        row = full.get(rid)
        if row is None:
            continue
        before, after = await around(ctx, row, 2, 2)
        lines += [_line(ctx, b, tz, None) for b in before]
        h = cite(row)
        earliest_handles.append(h)
        lines.append(_line(ctx, row, tz, h))
        lines += [_line(ctx, a, tz, None) for a in after]
    lines.append("  later:")
    for rid in later_ids:
        row = full.get(rid)
        if row is None:
            continue
        before, _ = await around(ctx, row, 1, 0)
        lines += [_line(ctx, b, tz, None) for b in before]
        lines.append(_line(ctx, row, tz, cite(row)))
    return {"id": f"c{index:02d}", "text": "\n".join(lines), "handles": handles, "earliest": earliest_handles}


def other_chapters(report: dict) -> str:
    parts = ["1. The server's first day and first five members."]
    arrivals = report.get("arrivals") or {}
    if arrivals.get("points"):
        parts.append(f"2. People arriving, {arrivals['start'][:4]} to {arrivals['end'][:4]}.")
    night = report.get("night")
    if night:
        parts.append(f"3. {night['day']}, the busiest day" + (f": {night['sphere']}." if night.get("sphere") else "."))
    renamed = report.get("names") or []
    parts.append("5. Olisar joining" + (f", and being renamed {renamed[-1]['to']}." if renamed else "."))
    parts.append("6. Now.")
    return "Other chapters: " + " ".join(parts)


# ── 3. The judge, and what code does with its answer ──

TOTAL = {"ours": 3, "warm": 3, "shared": 2, "origin": 2, "lasting": 1, "plain": 1}
FLOOR = ("ours", "shared", "origin", "warm")


def finalists(answer: dict, blocks: dict[str, dict], names_known: set[str]) -> list[dict]:
    """The judge's finalists that hold up, best first by code's own total."""
    scores = {c.get("id"): c.get("scores") for c in answer.get("candidates") or [] if isinstance(c, dict) and c.get("scores")}
    out = []
    for f in (answer.get("finalists") or [])[:3]:
        if not isinstance(f, dict):
            continue
        block = blocks.get(f.get("id"))
        s = scores.get(f.get("id")) or {}
        if block is None:
            continue
        cited = [f.get("origin"), *(f.get("trail") or [])]
        if any(h not in block["handles"] for h in cited) or f.get("origin") not in block["earliest"]:
            log.info("replay pick: dropped %s, which cites ids outside its evidence", f.get("id"))
            continue
        if any(int(s.get(k) or 0) < 2 for k in FLOOR):
            continue
        meaning = " ".join(str(f.get("meaning") or "").split()).rstrip(".")
        if not meaning or len(meaning.split()) > 10 or any(ch.isdigit() for ch in meaning) or '"' in meaning:
            continue
        if any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", meaning, re.IGNORECASE) for n in names_known if n):
            continue
        title = str(f.get("title") or "").strip()
        origin_text = block["handles"][f["origin"]]["text"].lower()
        if not title or (f.get("kind") in ("word", "emoji") and title.lower().strip(":") not in origin_text):
            continue
        total = sum(w * int(s.get(k) or 0) for k, w in TOTAL.items())
        out.append({**f, "title": title, "meaning": meaning, "total": total, "trail": [h for h in f.get("trail") or [] if h != f["origin"]]})
    order = [f["id"] for f in out]
    out.sort(key=lambda f: -f["total"])
    if [f["id"] for f in out] != order:
        log.info("replay pick: re-ordered the judge's finalists %s as %s", order, [f["id"] for f in out])
    return out


# ── 4. The check, 5. the writer ──

async def check(ctx, f: dict, block: dict) -> list[str] | None:
    """The trail that passed, or None if the finalist fails."""
    msgs = [f["origin"], *f["trail"]]
    lines = []
    for h in msgs:
        m = block["handles"][h]
        who = ctx.people.seen.get(m["who"] or "", {}).get("name", "Someone")
        lines.append(f"{h} {who}: {' '.join(m['text'].split())[:TEXT]}")
    system = CHECK_SYSTEM.replace("{title}", f["title"]).replace("{meaning}", f["meaning"])
    answer = await ctx.asker.ask(f"check:{f['id']}", kind="lite", system=system, prompt="\n".join(lines),
                                 temperature=0, max_output_tokens=800)
    if not answer:
        return None
    verdicts = answer.get("uses") or {}
    if answer.get("hurts") is not False or answer.get("sensitive") is not False:
        return None
    if verdicts.get(f["origin"]) != "yes":
        return None
    trail = [h for h in f["trail"] if verdicts.get(h) == "yes"]
    return trail if len(trail) >= 4 else None


async def write(ctx, f: dict, block: dict, trail: list[str], persona: tuple[str, str]) -> tuple[str, str]:
    name, tone = persona
    origin = block["handles"][f["origin"]]
    author = ctx.people.seen.get(origin["who"] or "", {}).get("name", "Someone")
    tz = ctx.report.get("tz") or 0
    when = (aware(stats.parse(origin["at"])) + timedelta(hours=tz))
    evidence_lines = []
    allowed = {author}
    for h in trail:
        m = block["handles"][h]
        who = ctx.people.seen.get(m["who"] or "", {}).get("name", "Someone")
        allowed.add(who)
        evidence_lines.append(f"{who} in #{m['chn']}: {' '.join(m['text'].split())[:TEXT]}")
    fill = {
        "{name}": name, "{server}": ctx.report.get("guild", {}).get("name", ""), "{persona}": tone,
        "{title}": f["title"], "{meaning}": f["meaning"], "{origin_author}": author,
        "{origin_channel}": "#" + origin["chn"], "{origin_date}": f"{when:%B} {when.day}, {when.year}",
        "{origin_text}": " ".join(origin["text"].split())[:TEXT], "{trail}": "\n".join(evidence_lines),
    }
    system = WRITER_SYSTEM
    for k, v in fill.items():
        system = system.replace(k, v)
    known = {p["name"] for p in ctx.people.seen.values()} | {n for n in ctx.roster.names()}
    reason = ""
    for attempt in (1, 2):
        prompt = "Write the two lines." + (f" Your last try couldn't be used: {reason}." if reason else "")
        answer = await ctx.asker.ask(f"write:{f['id']}:{attempt}", kind="chat", system=system, prompt=prompt,
                                     temperature=0.8, max_output_tokens=500)
        intro, spread = (answer or {}).get("intro", ""), (answer or {}).get("spread", "")
        problem = None
        for label, line in (("intro", intro), ("spread", spread)):
            problem = line_problem(line, names_known=known, names_allowed=allowed, placeholders=("{people}", "{count}"))
            if not problem and any(ch.isdigit() for ch in line.replace("{people}", "").replace("{count}", "")):
                problem = "it writes a number instead of {people} or {count}"
            if problem:
                reason = f"the {label} line {problem}"
                break
        if not problem:
            return intro.strip(), spread.strip()
    # PICK.md's template, when two tries didn't hold up.
    return f"Here, {f['title']} means {f['meaning']}. {author} started it.", "{people} of you have kept it going. {count} times so far."


def _unit(f: dict, c: dict, tz: int) -> tuple[int, str]:
    rows = c["rows"]
    if f.get("kind") == "ritual":
        wd = Counter((aware(r[4]) + timedelta(hours=tz)).weekday() for r in rows).most_common(1)[0]
        days_on = {(aware(r[4]) + timedelta(hours=tz)).date() for r in rows if (aware(r[4]) + timedelta(hours=tz)).weekday() == wd[0]}
        if wd[1] / len(rows) >= 0.5:
            return len(days_on), WEEKDAYS[wd[0]]
        return c["days"], "days"
    if f.get("kind") in ("made", "place", "game"):
        return len(rows), "messages"
    return len(rows), "times"


def _spots(ctx, block: dict, trail: list[str], c: dict, tz: int) -> list[dict]:
    """Where the pick came up again: the trail's messages, then other uses for the rest."""
    def where(chn: str, at) -> str:
        local = aware(at) + timedelta(hours=tz)
        return f"#{chn} · {local:%b %Y}"

    out, seen = [], set()
    for h in trail:
        m = block["handles"][h]
        text_ = " ".join(m["text"].split())
        out.append({"where": where(m["chn"], stats.parse(m["at"])), "said": text_ if len(text_) <= 48 else None})
        seen.add(where(m["chn"], stats.parse(m["at"])))
    rows = c["rows"]
    step = max(1, len(rows) // 24)
    for r in rows[::step]:
        if len(out) >= 10:
            break
        w = where(ctx.scope.name(r[2]), r[4])
        if w not in seen:
            seen.add(w)
            out.append({"where": w, "said": None})
    return out[:10]


async def run(ctx) -> None:
    st = ctx.state.setdefault("pick", {})
    if st.get("done"):
        return
    tz = ctx.report.get("tz") or 0
    if "blocks" not in st:
        cands = await candidates(ctx)
        blocks = {}
        for i, c in enumerate(cands, 1):
            b = await evidence(ctx, c, i)
            b["stats"] = {k: c[k] for k in ("phrase", "title", "detector", "people", "channels", "months", "days")}
            blocks[b["id"]] = b
        st["blocks"] = blocks
        await ctx.keep()
    blocks = st["blocks"]
    ctx.report["pick"] = None
    if not blocks:
        st["done"] = True
        return
    prompt = "\n\n".join(b["text"] for b in blocks.values()) + "\n\n" + other_chapters(ctx.report)
    answer = await ctx.asker.ask("judge", kind="strong", system=JUDGE_SYSTEM.replace("{schema}", SCHEMA), prompt=prompt,
                                 temperature=0.2, max_output_tokens=8192, thinking_budget=2048)
    known = {p["name"] for p in ctx.people.seen.values()} | set(ctx.roster.names())
    async with session_scope() as session:
        p = (await session.execute(select(Persona.name, Persona.tone_notes).where(Persona.guild_id == ctx.guild_id))).first()
    persona = ((p[0] if p else "") or "Olisar", (p[1] if p else "") or "")
    for f in finalists(answer or {}, blocks, known):
        block = blocks[f["id"]]
        trail = await check(ctx, f, block)
        if trail is None:
            log.info("replay pick: %s didn't pass the check", f["id"])
            continue
        intro, spread = await write(ctx, f, block, trail, persona)
        rows = await uses(ctx, block["stats"]["phrase"], skip_window=_skip(ctx.report, tz))
        c = {**block["stats"], "rows": rows}
        count, unit = _unit(f, c, tz)
        ctx.report["pick"] = {
            "kind": f.get("kind") or "other", "title": f["title"], "meaning": f["meaning"], "why": str(f.get("why") or "").strip(),
            "origin": block["handles"][f["origin"]],
            "intro": intro, "spread": spread,
            "people": len({r[3] for r in rows}), "count": count, "unit": unit, "channels": len({r[2] for r in rows}),
            "spots": _spots(ctx, block, trail, c, tz),
        }
        break
    st["done"] = True
    await ctx.keep()


def _skip(report: dict, tz: int):
    night = report.get("night")
    return stats.day_bounds(date.fromisoformat(night["day"]), tz) if night else None
