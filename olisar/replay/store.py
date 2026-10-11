"""Replay runs in the database: starting one, finding the one in progress, and serving a
finished report.

A report quotes members, so it's handled like the rest of their data:

* Redacted each time it's served (``present``). Anyone who has opted out, paused, or been
  forgotten since it was made is dropped from it: their messages go, and anywhere they're
  named reads "Someone". Mentions stay as ``<@id>`` in the stored text for this reason, and
  are resolved here, so a name can't outlive its owner's opt-out inside someone else's
  message.
* Scrubbed on ``/forget-me`` (``scrub_user``, called from olisar/memory/purge.py), along
  with the run's working state, which holds what the models were shown.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, ReplayRun, UserProfile, utcnow
from olisar.replay.scope import aware

ACTIVE = ("queued", "running", "paused", "failed")
# A run the bot hasn't touched for this long isn't being worked on: the bot is offline,
# or stopped partway. The console shows it as stopped, with Try again.
STALL_AFTER = timedelta(minutes=3)
ANSWER_LIMIT = 120


async def active_run(session: AsyncSession, guild_id: int) -> ReplayRun | None:
    return (
        await session.scalars(
            select(ReplayRun).where(ReplayRun.guild_id == guild_id, ReplayRun.status.in_(ACTIVE))
            .order_by(ReplayRun.id.desc()).limit(1)
        )
    ).first()


async def latest_done(session: AsyncSession, guild_id: int) -> ReplayRun | None:
    return (
        await session.scalars(
            select(ReplayRun).where(ReplayRun.guild_id == guild_id, ReplayRun.status == "done")
            .order_by(ReplayRun.id.desc()).limit(1)
        )
    ).first()


async def due_runs(session: AsyncSession, now: datetime) -> list[tuple[int, int]]:
    """(run id, guild id) of every run the bot should be advancing now."""
    rows = (
        await session.execute(
            select(ReplayRun.id, ReplayRun.guild_id, ReplayRun.status, ReplayRun.paused_until)
            .where(ReplayRun.status.in_(("queued", "running", "paused")))
            .order_by(ReplayRun.id)
        )
    ).all()
    out = []
    for rid, gid, status, until in rows:
        if status == "paused" and (until is None or aware(until) > now):
            continue
        out.append((int(rid), int(gid)))
    return out


async def save(run_id: int, **fields) -> None:
    """Write fields on a run in a short transaction of its own, touching ``updated_at``."""
    fields.setdefault("updated_at", utcnow())
    async with session_scope() as session:
        await session.execute(update(ReplayRun).where(ReplayRun.id == run_id).values(**fields))


async def add_spend(run_id: int, *, requests: int, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    async with session_scope() as session:
        await session.execute(
            update(ReplayRun).where(ReplayRun.id == run_id).values(
                requests=ReplayRun.requests + requests,
                input_tokens=ReplayRun.input_tokens + input_tokens,
                output_tokens=ReplayRun.output_tokens + output_tokens,
                cost_usd=ReplayRun.cost_usd + cost_usd,
                updated_at=utcnow(),
            )
        )


def stalled(run: ReplayRun, now: datetime) -> bool:
    if run.status not in ("queued", "running"):
        return False
    return now - (aware(run.updated_at) or now) > STALL_AFTER


async def admin_name(session: AsyncSession, guild_id: int, user_id: int) -> str | None:
    name = await session.scalar(
        select(UserProfile.display_name).where(UserProfile.guild_id == guild_id, UserProfile.user_id == user_id)
    )
    if name:
        return name
    return await session.scalar(select(AdminUser.username).where(AdminUser.discord_user_id == user_id)) or None


def view(run: ReplayRun, *, viewer: int, starter: str | None, now: datetime) -> dict:
    """A run as the console's remembering screen reads it."""
    def iso(at):
        at = aware(at)
        return at.isoformat().replace("+00:00", "Z") if at else None

    return {
        "id": run.id,
        "status": "stopped" if stalled(run, now) else run.status,
        "step": run.step,
        "progress": run.progress or {},
        "started_by": {"id": str(run.started_by), "name": starter},
        "mine": int(run.started_by or 0) == int(viewer),
        "started_at": iso(run.started_at),
        "updated_at": iso(run.updated_at),
        "paused_until": iso(run.paused_until),
        "pause_reason": run.pause_reason or None,
        "error": run.error or None,
    }


# ── Redaction ──
# Two kinds of token in the stored text stand for people, so their names can go when they
# do: a Discord mention, <@id>, inside a member's message, and <name:id> in a line a model
# wrote ("<name:2> said it first"). Both resolve when the report is served.

_MENTION = re.compile(r"<@!?(\d+)>")
_NAME = re.compile(r"<name:(\d+)>")
TEXT_KEYS = ("text", "said", "intro", "spread", "why", "line", "sphere")


def _is_message(node) -> bool:
    return isinstance(node, dict) and "text" in node and "who" in node and "at" in node


def name_tokens(text: str, people: dict) -> str:
    """A model-written line with the names it uses turned into <name:id> tokens."""
    if not text:
        return text
    for key, p in sorted(people.items(), key=lambda kv: -len(kv[1].get("name") or "")):
        name = (p.get("name") or "").strip()
        if len(name) >= 2:
            text = re.sub(r"(?<![\w<:])" + re.escape(name) + r"(?![\w>])", f"<name:{key}>", text)
    return text


def redact(report: dict, hidden: set[str], *, resolve: bool = True) -> dict:
    """``report`` without anyone in ``hidden`` (user ids as strings): their messages go and
    their names read "Someone". With ``resolve``, every token is turned into the name the
    report knows (for serving); without it, only hidden people's are (for storing)."""
    people = {k: v for k, v in (report.get("people") or {}).items() if k not in hidden}

    def mention(m: re.Match) -> str:
        if m.group(1) in hidden or (resolve and m.group(1) not in people):
            return "@someone"
        return "@" + people[m.group(1)]["name"] if resolve else m.group(0)

    def name(m: re.Match) -> str:
        if m.group(1) in hidden or (resolve and m.group(1) not in people):
            return "Someone"
        return people[m.group(1)]["name"] if resolve else m.group(0)

    def walk(node):
        if isinstance(node, list):
            out = []
            for item in node:
                if _is_message(item) and item.get("who") in hidden:
                    continue
                if isinstance(item, str) and item in hidden:
                    continue
                out.append(walk(item))
            return out
        if isinstance(node, dict):
            out = {}
            gone = node.get("who") in hidden
            for key, value in node.items():
                if key in ("who", "by") and value in hidden:
                    out[key] = None
                elif key == "people" and isinstance(value, dict):
                    out[key] = {k: v for k, v in value.items() if k not in hidden}
                elif key in ("count", "said") and gone:
                    out[key] = None
                elif _is_message(value) and value.get("who") in hidden:
                    out[key] = None
                elif key in TEXT_KEYS and isinstance(value, str):
                    out[key] = _NAME.sub(name, _MENTION.sub(mention, value))
                else:
                    out[key] = walk(value)
            return out
        return node

    out = walk(report)
    # A chapter that lost what it stands on goes with it.
    pick = out.get("pick")
    if isinstance(pick, dict) and not pick.get("origin"):
        out["pick"] = None
    first = out.get("first")
    if isinstance(first, dict) and not first.get("messages"):
        out["first"] = None
    night = out.get("night")
    if isinstance(night, dict) and sum("text" in m for m in night.get("excerpt") or []) < 3:
        night["excerpt"] = []
    arrive = out.get("arrive")
    if isinstance(arrive, dict) and arrive.get("reply") and arrive["reply"][0].get("who") != "bot" and len(arrive["reply"]) < 2:
        arrive["reply"] = []
    return out


def present(report: dict, hidden: set[int]) -> dict:
    return redact(report, {str(u) for u in hidden})


async def scrub_user(session: AsyncSession, user_id: int, guild_ids: list[int]) -> int:
    """Take a forgotten member out of every replay of ``guild_ids``: the finished reports
    and the working state of runs still going. Returns how many runs changed."""
    key = {str(user_id)}
    runs = (await session.scalars(select(ReplayRun).where(ReplayRun.guild_id.in_(guild_ids)))).all()
    changed = 0
    for run in runs:
        touched = False
        if run.report and str(user_id) in _ids(run.report):
            run.report = redact(run.report, key, resolve=False)
            touched = True
        if run.state and str(user_id) in _ids(run.state):
            state = redact(run.state, key, resolve=False)
            # The judge's evidence is prose with their name and words in it: it goes, and so
            # do the answers that cited it, so the pick is made again without them.
            if isinstance(state.get("pick"), dict):
                state["pick"] = {}
                state["calls"] = {k: v for k, v in (state.get("calls") or {}).items()
                                  if not k.startswith(("judge", "check:c", "write:"))}
            run.state = state
            touched = True
        changed += touched
    return changed


def _ids(node) -> str:
    """The tree as text: a cheap first test for whether an id appears in it at all."""
    return json.dumps(node, ensure_ascii=False)
