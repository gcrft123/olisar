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

_MENTION = re.compile(r"<@!?(\d+)>")


def _is_message(node) -> bool:
    return isinstance(node, dict) and "text" in node and "who" in node and "at" in node


def redact(report: dict, hidden: set[str]) -> dict:
    """``report`` without anyone in ``hidden`` (user ids as strings), with mentions
    resolved to the names the report knows."""
    people = {k: v for k, v in (report.get("people") or {}).items() if k not in hidden}

    def mention(m: re.Match) -> str:
        p = people.get(m.group(1))
        return "@" + (p["name"] if p else "someone")

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
                elif key == "count" and gone:
                    out[key] = None
                elif _is_message(value) and value.get("who") in hidden:
                    out[key] = None
                elif key == "text" and isinstance(value, str):
                    out[key] = _MENTION.sub(mention, value)
                else:
                    out[key] = walk(value)
            return out
        return node

    return walk(report)


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
            run.report = redact(run.report, key)
            touched = True
        if run.state and str(user_id) in _ids(run.state):
            run.state = redact(run.state, key)
            touched = True
        changed += touched
    return changed


def _ids(node) -> str:
    """The tree as text: a cheap first test for whether an id appears in it at all."""
    return json.dumps(node, ensure_ascii=False)
