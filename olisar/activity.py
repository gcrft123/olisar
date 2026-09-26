"""The activity feed: what Olisar has been doing, read out of its own database.

The server app's final screen shows these as "memories". The bot runs in a container on
the operator's VM and the desktop app only reaches it over SSH, so this runs *inside* the
container, next to the database the running bot uses, and prints one JSON object::

    sudo docker exec <container> python -m olisar.activity

    {"ok": true, "supported": true, "items": [...], "members": {...} | null,
     "health": {...} | null}

``olisar.runtime.remote.activity`` is the other end. The item shapes are the contract the
console's final screen is written against; ``feed`` documents them.

It's polled every twenty seconds or so while that screen is open, so every query here is
bounded, and it never writes: the database is opened with SQLite's ``mode=ro`` and
``query_only``, which refuse a write whatever a query tries.

What it never shows:

* anything from a DM. DM rows are stored under guild 0, but some things said in a DM are
  filed under the home server instead (a fact the ``remember`` tool saved, a reminder, a
  glossary entry): those only appear when there's a server channel to show for them.
* anyone who opted out of being remembered, or has paused it, in any server.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from olisar.db.models import (
    BotActivity,
    Guild,
    GuildChannelInfo,
    GuildFact,
    KBChunk,
    KBSource,
    KBSourceType,
    KBStatus,
    Message,
    Reminder,
    SearchMessage,
    UserMemory,
    UserProfile,
)

ITEM_LIMIT = 24        # items in one answer, newest first
REPLY_LIMIT = 12       # of which replies, so the other kinds surface on a busy server
FACE_LIMIT = 11        # faces on the roster sync's memory
TEXT_LIMIT = 600       # characters of any one text
NEW_MEMBER_DAYS = 30   # how recent a join has to be to count as new
# The reply rows read to find the newest replies: one reply can be several messages.
REPLY_SCAN = 60
# A reply from before replies recorded what they answered is matched to the last message
# someone else sent in its channel within this long before it.
LEGACY_ANSWER_WINDOW = timedelta(minutes=10)
# Parts of one old reply are at most this far apart (they're sent seconds apart).
LEGACY_PART_GAP = timedelta(seconds=60)
# How many of a member's messages an impression is built from (olisar/memory/personas.py
# HISTORY_LIMIT; not imported, because that module pulls in the Gemini client).
IMPRESSION_HISTORY = 60
HEALTH_TIMEOUT = 2.0   # seconds for the backend's own /api/health
# Where the backend listens inside the container: deploy/Dockerfile's CMD passes --port 8000
# (so an OLISAR_PORT in the .env changes nothing), and its HEALTHCHECK asks the same port.
CONTAINER_PORT = 8000

# How Olisar was reached, as the feed names it. "dm" is stored too but never shown.
TRIGGERS = ("ask", "name", "mention", "reply", "proactive", "catchup")

_KB_HOW = {KBSourceType.website: "site", KBSourceType.url: "page", KBSourceType.doc: "doc"}


# ── shaping (pure) ───────────────────────────────────────────────────────────────


def reply_trigger(stored: str | None) -> str:
    """The feed's name for how a reply was called: one of ``TRIGGERS``, else ""."""
    value = (stored or "").strip().lower()
    return value if value in TRIGGERS else ""


def clip(text: str | None, limit: int = TEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands datetimes back naive; they're stored in UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    """ISO 8601 in UTC, to the millisecond: ``2026-09-26T08:15:02.123Z``."""
    value = as_utc(value)
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def person(name: str | None, avatar: str | None) -> dict:
    return {"name": (name or "").strip(), "avatar": (avatar or "").strip()}


def channel_label(name: str | None) -> str:
    return f"#{name}" if name else ""


def status_how(guild_id: int | None, where: str) -> str:
    """How a custom status came to be set: at startup, or by the ``set_status`` tool in a
    conversation (a DM's channel is never named)."""
    if guild_id is None:
        return "Set when it started"
    if guild_id and where:
        return f"Set during a conversation in {where}"
    return "Set during a conversation"


def group_replies(rows: list[dict]) -> list[dict]:
    """Fold Olisar's reply rows into one entry per reply, newest first.

    ``rows`` are ``{message_id, guild_id, channel_id, at, content, answering, trigger}``.
    A reply sent as several messages carries the id of the message it answered on each
    part, so those group on it. Rows from before that was recorded (``answering`` None)
    group when they follow each other in a channel within ``LEGACY_PART_GAP``; their
    answer is found later. Each entry is ``{id, message_id, guild_id, channel_id, at,
    text, answering, trigger}``, with the parts' texts joined in order.
    """
    groups: dict[tuple, dict] = {}
    last_legacy: dict[int, dict] = {}  # channel -> the old reply its next part would join
    for row in sorted(rows, key=lambda r: r["message_id"]):
        content = (row.get("content") or "").strip()
        if not content:
            continue
        at = as_utc(row["at"])
        if row.get("answering"):
            key = ("to", row["channel_id"], row["answering"])
        else:
            prev = last_legacy.get(row["channel_id"])
            if prev is not None and at - prev["last_at"] <= LEGACY_PART_GAP:
                key = prev["key"]
            else:
                key = ("old", row["channel_id"], row["message_id"])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key": key,
                "id": f"reply:{row['message_id']}",
                "message_id": row["message_id"],
                "guild_id": row["guild_id"],
                "channel_id": row["channel_id"],
                "at": at,
                "parts": [],
                "answering": row.get("answering"),
                "trigger": reply_trigger(row.get("trigger")),
            }
        group["parts"].append(content)
        group["last_at"] = at
        if not row.get("answering"):
            last_legacy[row["channel_id"]] = group
    out = []
    for group in groups.values():
        out.append({
            "id": group["id"],
            "message_id": group["message_id"],
            "guild_id": group["guild_id"],
            "channel_id": group["channel_id"],
            "at": group["at"],
            "text": "\n".join(group["parts"]),
            "answering": group["answering"],
            "trigger": group["trigger"],
        })
    out.sort(key=lambda g: g["message_id"], reverse=True)
    return out


def select_items(items: list[dict]) -> list[dict]:
    """Newest first, at most ``ITEM_LIMIT``, of which at most ``REPLY_LIMIT`` replies."""
    ordered = sorted(items, key=lambda i: i["at"] or "", reverse=True)
    out: list[dict] = []
    replies = 0
    for item in ordered:
        if item["kind"] == "reply":
            if replies >= REPLY_LIMIT:
                continue
            replies += 1
        out.append(item)
        if len(out) >= ITEM_LIMIT:
            break
    return out


# ── reading ──────────────────────────────────────────────────────────────────────


def _hidden_users(session: Session, now: datetime) -> set[int]:
    """Everyone who opted out of being remembered, or is paused, in any server or in DMs."""
    rows = session.execute(
        select(UserProfile.user_id, UserProfile.memory_opt_out, UserProfile.pause_until).where(
            or_(UserProfile.memory_opt_out.is_(True), UserProfile.pause_until.is_not(None))
        )
    ).all()
    return {
        uid for uid, opted, pause in rows
        if opted or (pause is not None and as_utc(pause) > now)
    }


class _Context:
    """What every kind needs: the servers, their channels, who's hidden, and a lookup of
    people by server."""

    def __init__(self, session: Session, now: datetime) -> None:
        self.session = session
        self.now = now
        self.hidden = _hidden_users(session, now)
        self.guilds = {
            g.id: g for g in session.scalars(
                select(Guild).where(Guild.id != 0, Guild.active.is_(True))
            ).all()
        }
        self.channels = {
            cid: name for cid, name in session.execute(
                select(GuildChannelInfo.channel_id, GuildChannelInfo.name).where(
                    GuildChannelInfo.guild_id != 0
                )
            ).all()
        }
        self._profiles: dict[tuple[int, int], UserProfile] = {}
        self._any_profile: dict[int, UserProfile] = {}
        self._loaded: set[int] = set()

    def where(self, channel_id: int | None) -> str:
        return channel_label(self.channels.get(channel_id or 0, ""))

    def load_people(self, user_ids: set[int]) -> None:
        """Fetch these people's profiles in every server, for ``who``."""
        wanted = {u for u in user_ids if u and u not in self._loaded}
        if not wanted:
            return
        self._loaded |= wanted
        for profile in self.session.scalars(
            select(UserProfile).where(UserProfile.user_id.in_(wanted), UserProfile.guild_id != 0)
        ).all():
            self._profiles[(profile.guild_id, profile.user_id)] = profile
            self._any_profile.setdefault(profile.user_id, profile)

    def who(self, guild_id: int, user_id: int | None, fallback_name: str = "") -> dict | None:
        """The person, or None when they're hidden or there's nobody to name."""
        if not user_id or user_id in self.hidden:
            return None
        profile = self._profiles.get((guild_id, user_id)) or self._any_profile.get(user_id)
        if profile is not None:
            return person(profile.display_name or fallback_name, profile.avatar)
        return person(fallback_name, "") if fallback_name else None

    def source_messages(self, message_ids: set[int]) -> dict[int, dict]:
        """``message_id -> {guild_id, channel_id, author_id, is_bot, name, content}`` from
        the conversation memory, else the search index (which covers every channel)."""
        ids = {m for m in message_ids if m and m > 0}
        found: dict[int, dict] = {}
        if not ids:
            return found
        for m in self.session.scalars(select(Message).where(Message.message_id.in_(ids))).all():
            found[m.message_id] = {
                "guild_id": m.guild_id, "channel_id": m.channel_id, "author_id": m.author_id,
                "is_bot": bool(m.author_is_bot), "name": m.author_name, "content": m.content,
            }
        missing = ids - set(found)
        if missing:
            for s in self.session.scalars(
                select(SearchMessage).where(SearchMessage.message_id.in_(missing))
            ).all():
                found[s.message_id] = {
                    "guild_id": s.guild_id, "channel_id": s.channel_id, "author_id": s.author_id,
                    "is_bot": False, "name": s.author_name, "content": s.content,
                }
        return found


def _legacy_answer(session: Session, reply: dict) -> int | None:
    """The message an old reply most likely answered: the last one someone else sent in
    its channel in the few minutes before it."""
    first = reply["message_id"]
    floor = as_utc(reply["at"]) - LEGACY_ANSWER_WINDOW
    row = session.execute(
        select(Message.message_id)
        .where(
            Message.channel_id == reply["channel_id"],
            Message.author_is_bot.is_(False),
            Message.message_id < first,
            Message.created_at >= floor.replace(tzinfo=None),
        )
        .order_by(Message.message_id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _replies(ctx: _Context) -> list[dict]:
    rows = ctx.session.scalars(
        select(Message)
        .where(
            Message.author_is_bot.is_(True),
            Message.author_name == "",  # Olisar's own (see Message.author_name)
            Message.guild_id != 0,
            Message.message_id > 0,     # not the markers of a turn answered with a reaction
        )
        .order_by(Message.created_at.desc())
        .limit(REPLY_SCAN)
    ).all()
    groups = [
        g for g in group_replies([
            {
                "message_id": m.message_id, "guild_id": m.guild_id, "channel_id": m.channel_id,
                "at": m.created_at, "content": m.content, "answering": m.reply_to_message_id,
                "trigger": m.trigger,
            }
            for m in rows
        ])
        if g["guild_id"] in ctx.guilds
    ]
    # Only as many old replies are matched up as could be shown, one query each.
    budget = REPLY_LIMIT + 4
    for group in groups:
        if group["answering"] is None and budget > 0:
            budget -= 1
            group["answering"] = _legacy_answer(ctx.session, group)
    answered = ctx.source_messages({g["answering"] for g in groups if g["answering"]})
    ctx.load_people({a["author_id"] for a in answered.values()})

    items = []
    for group in groups:
        asked = answered.get(group["answering"] or 0)
        if asked is None or asked["guild_id"] == 0 or asked["is_bot"]:
            continue  # nobody to show it answering
        who = ctx.who(group["guild_id"], asked["author_id"], asked["name"])
        if who is None:
            continue
        items.append({
            "id": group["id"],
            "kind": "reply",
            "at": iso(group["at"]),
            "who": who,
            "where": ctx.where(group["channel_id"]),
            "trigger": group["trigger"],
            "ask": clip(asked["content"]),
            "text": clip(group["text"]),
        })
        if len(items) >= REPLY_LIMIT:
            break
    return items


def _members(ctx: _Context) -> list[dict]:
    since = (ctx.now - timedelta(days=NEW_MEMBER_DAYS)).replace(tzinfo=None)
    rows = ctx.session.scalars(
        select(UserProfile)
        .where(
            UserProfile.guild_id.in_(list(ctx.guilds) or [-1]),
            UserProfile.joined_at.is_not(None),
            UserProfile.joined_at >= since,
        )
        .order_by(UserProfile.joined_at.desc())
        .limit(ITEM_LIMIT * 2)
    ).all()
    items = []
    for p in rows:
        guild = ctx.guilds[p.guild_id]
        # Only joins Olisar was there for: not everyone who arrived before it did.
        arrived = as_utc(guild.created_at)
        if arrived is not None and as_utc(p.joined_at) < arrived:
            continue
        if p.user_id in ctx.hidden:
            continue
        items.append({
            "id": f"member:{p.guild_id}:{p.user_id}",
            "kind": "member",
            "at": iso(p.joined_at),
            "who": person(p.display_name, p.avatar),
            "roles": [r.get("name", "") for r in (p.roles or []) if isinstance(r, dict) and r.get("name")],
        })
    return items


def _impressions(ctx: _Context) -> list[dict]:
    rows = ctx.session.scalars(
        select(UserProfile)
        .where(
            UserProfile.guild_id.in_(list(ctx.guilds) or [-1]),
            UserProfile.persona_summary != "",
            UserProfile.persona_updated_at.is_not(None),
        )
        .order_by(UserProfile.persona_updated_at.desc())
        .limit(ITEM_LIMIT)
    ).all()
    rows = [p for p in rows if p.user_id not in ctx.hidden and (p.persona_summary or "").strip()]
    counts: dict[tuple[int, int], int] = {}
    if rows:
        counts = {
            (gid, uid): n for gid, uid, n in ctx.session.execute(
                select(Message.guild_id, Message.author_id, func.count())
                .where(
                    Message.author_id.in_({p.user_id for p in rows}),
                    Message.author_is_bot.is_(False),
                    Message.guild_id.in_({p.guild_id for p in rows}),
                )
                .group_by(Message.guild_id, Message.author_id)
            ).all()
        }
    return [
        {
            "id": f"impression:{p.id}:{int(as_utc(p.persona_updated_at).timestamp())}",
            "kind": "impression",
            "at": iso(p.persona_updated_at),
            "who": person(p.display_name, p.avatar),
            "text": clip(p.persona_summary),
            "messages": min(counts.get((p.guild_id, p.user_id), 0), IMPRESSION_HISTORY),
        }
        for p in rows
    ]


def _remembered(ctx: _Context) -> list[dict]:
    # Only facts with a server message behind them: see UserMemory.source_message_id.
    rows = ctx.session.scalars(
        select(UserMemory)
        .where(
            UserMemory.guild_id.in_(list(ctx.guilds) or [-1]),
            UserMemory.source_message_id.is_not(None),
        )
        .order_by(UserMemory.created_at.desc())
        .limit(ITEM_LIMIT)
    ).all()
    sources = ctx.source_messages({m.source_message_id for m in rows})
    ctx.load_people({m.user_id for m in rows})
    items = []
    for m in rows:
        source = sources.get(m.source_message_id)
        if source is not None and source["guild_id"] == 0:
            continue
        who = ctx.who(m.guild_id, m.user_id)
        if who is None:
            continue
        items.append({
            "id": f"remembered:{m.id}",
            "kind": "remembered",
            "at": iso(m.created_at),
            "who": who,
            "type": m.kind.value if m.kind else "fact",
            "text": clip(m.content),
            "said": clip(source["content"]) if source else "",
            "where": ctx.where(source["channel_id"]) if source else "",
        })
    return items


def _glossary(ctx: _Context) -> list[dict]:
    rows = ctx.session.scalars(
        select(GuildFact)
        .where(GuildFact.guild_id.in_(list(ctx.guilds) or [-1]))
        .order_by(GuildFact.created_at.desc())
        .limit(ITEM_LIMIT)
    ).all()
    items = []
    for f in rows:
        # A named channel has to be one of the server's: remember_server_fact in a DM files
        # the fact under the home server with the DM as its channel.
        if f.source_channel_id and f.source_channel_id not in ctx.channels:
            continue
        items.append({
            "id": f"glossary:{f.id}",
            "kind": "glossary",
            "at": iso(f.created_at),
            "subject": clip(f.subject, 128),
            "text": clip(f.fact),
            "where": ctx.where(f.source_channel_id),
        })
    return items


def _statuses(ctx: _Context) -> list[dict]:
    rows = ctx.session.scalars(
        select(BotActivity)
        .where(BotActivity.kind == "status")
        .order_by(BotActivity.id.desc())
        .limit(ITEM_LIMIT)
    ).all()
    items = []
    for s in rows:
        text = clip(s.text, 128)
        if not text:
            continue
        where = ctx.where(s.channel_id) if s.guild_id else ""
        items.append({
            "id": f"status:{s.id}",
            "kind": "status",
            "at": iso(s.created_at),
            "text": text,
            "how": status_how(s.guild_id, where),
        })
    return items


def _learned(ctx: _Context) -> list[dict]:
    when = func.coalesce(KBSource.last_ingested_at, KBSource.created_at)
    rows = ctx.session.scalars(
        select(KBSource)
        .where(
            KBSource.guild_id.in_(list(ctx.guilds) or [-1]),
            KBSource.status == KBStatus.ready,
        )
        .order_by(when.desc())
        .limit(ITEM_LIMIT)
    ).all()
    counts: dict[int, int] = {}
    if rows:
        counts = dict(ctx.session.execute(
            select(KBChunk.source_id, func.count())
            .where(KBChunk.source_id.in_({s.id for s in rows}))
            .group_by(KBChunk.source_id)
        ).all())
    ctx.load_people({s.added_by for s in rows if s.added_by})
    items = []
    for s in rows:
        is_web = s.type in (KBSourceType.url, KBSourceType.website)
        # A document's uri is where the upload is stored on the server: not something to show.
        title = s.title or (s.uri if is_web else "") or "Document"
        items.append({
            "id": f"learned:{s.id}",
            "kind": "learned",
            "at": iso(s.last_ingested_at or s.created_at),
            "title": clip(title, 256),
            "url": s.uri if is_web else "",
            "count": int(counts.get(s.id, 0)),
            "who": ctx.who(s.guild_id, s.added_by) if s.added_by else None,
            "how": _KB_HOW.get(s.type, "doc"),
        })
    return items


def _reminders(ctx: _Context) -> list[dict]:
    rows = ctx.session.scalars(
        select(Reminder)
        .where(
            Reminder.guild_id.in_(list(ctx.guilds) or [-1]),
            Reminder.fired.is_(True),
            Reminder.scheduled_at <= ctx.now.replace(tzinfo=None),
        )
        .order_by(Reminder.scheduled_at.desc())
        .limit(ITEM_LIMIT)
    ).all()
    # Set in a server channel only: one set in a DM is filed under the home server with
    # the DM as its channel.
    rows = [r for r in rows if r.channel_id in ctx.channels]
    ctx.load_people({r.user_id for r in rows})
    items = []
    for r in rows:
        who = ctx.who(r.guild_id, r.user_id)
        if who is None:
            continue
        items.append({
            "id": f"reminder:{r.id}",
            "kind": "reminder",
            "at": iso(r.scheduled_at),
            "who": who,
            "text": clip(r.content),
            "where": ctx.where(r.channel_id),
        })
    return items


def _images(ctx: _Context) -> list[dict]:
    rows = ctx.session.scalars(
        select(BotActivity)
        .where(
            BotActivity.kind == "image",
            BotActivity.guild_id.in_(list(ctx.guilds) or [-1]),
        )
        .order_by(BotActivity.id.desc())
        .limit(ITEM_LIMIT)
    ).all()
    asked = ctx.source_messages({r.request_message_id for r in rows if r.request_message_id})
    ctx.load_people({a["author_id"] for a in asked.values()})
    items = []
    for r in rows:
        source = asked.get(r.request_message_id or 0)
        if source is None or source["guild_id"] == 0:
            continue
        who = ctx.who(r.guild_id, source["author_id"], source["name"])
        if who is None:
            continue
        items.append({
            "id": f"image:{r.id}",
            "kind": "image",
            "at": iso(r.created_at),
            "who": who,
            "text": clip(r.text),
            "where": ctx.where(r.channel_id),
        })
    return items


def _roster(ctx: _Context) -> dict | None:
    """The roster sync's memory: how many members it knows, when it last ran, and faces."""
    guilds = list(ctx.guilds.values())
    synced = [g for g in guilds if g.roster_count is not None]
    if synced:
        count = sum(int(g.roster_count or 0) for g in synced)
    else:  # a bot that hasn't synced since the count was recorded
        count = int(ctx.session.scalar(
            select(func.count()).select_from(UserProfile).where(
                UserProfile.guild_id.in_([g.id for g in guilds] or [-1])
            )
        ) or 0)
    stamps = [as_utc(g.roster_synced_at) for g in guilds if g.roster_synced_at is not None]
    faces: list[dict] = []
    seen: set[int] = set()
    for p in ctx.session.scalars(
        select(UserProfile)
        .where(
            UserProfile.guild_id.in_([g.id for g in guilds] or [-1]),
            UserProfile.avatar != "",
        )
        .order_by(UserProfile.last_seen.desc())
        .limit(FACE_LIMIT * 4)
    ).all():
        if p.user_id in ctx.hidden or p.user_id in seen:
            continue
        seen.add(p.user_id)
        faces.append(person(p.display_name, p.avatar))
        if len(faces) >= FACE_LIMIT:
            break
    if not count and not faces:
        return None
    return {"count": count, "at": iso(max(stamps)) if stamps else None, "faces": faces}


def feed(session: Session, now: datetime | None = None) -> dict:
    """``{items, members}`` from the database. Every item has ``id`` (stable), ``kind`` and
    ``at`` (ISO 8601 UTC); people are ``{name, avatar}`` with ``avatar`` a CDN URL or "".

    ============  ==================================================================
    reply         who, where, trigger (``TRIGGERS`` or ""), ask, text
    member        who, roles — a member who joined in the last ``NEW_MEMBER_DAYS``
    impression    who, text, messages
    remembered    who, type (the UserMemory kind), text, said, where
    glossary      subject, text, where
    status        text, how
    learned       title, url, count (passages), who (or None), how (site/page/doc)
    reminder      who, text, where — only ones that have fired
    image         who, text (the prompt), where
    ============  ==================================================================
    """
    now = as_utc(now) or datetime.now(timezone.utc)
    ctx = _Context(session, now)
    items: list[dict] = []
    for kind in (
        _replies, _members, _impressions, _remembered, _glossary, _statuses, _learned,
        _reminders, _images,
    ):
        items.extend(kind(ctx))
    return {"items": select_items(items), "members": _roster(ctx)}


# ── the container's side ─────────────────────────────────────────────────────────


def health(port: int | None = None, timeout: float = HEALTH_TIMEOUT) -> dict | None:
    """The backend's own self-checks, from its ``/api/health`` on loopback: ``{vec,
    sandbox, model}``, or None when it doesn't answer."""
    port = port or CONTAINER_PORT
    # No proxy: a proxy set in the container's environment has no business with loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as resp:
            body = json.loads(resp.read(65536).decode("utf-8"))
    except Exception:  # noqa: BLE001 — unreachable, slow or garbled all mean "can't tell"
        return None
    if not isinstance(body, dict):
        return None

    def flag(value) -> bool | None:
        return value if isinstance(value, bool) else None

    model = body.get("model")
    return {
        "vec": flag(body.get("vec")),
        "sandbox": flag(body.get("sandbox")),
        "model": model if isinstance(model, str) else None,
    }


def database_path() -> Path:
    """The database the running bot uses: its launch-default profile's, the way
    ``olisar.runtime.server.run`` pins it. Resolved without writing anything."""
    from olisar.runtime import paths, profiles

    home = paths.home_dir()
    if not (home / "profiles.json").exists():
        return home / "olisar.db"
    return profiles.db_path_for(profiles.default_id())


def open_readonly(path: Path):
    """A SQLAlchemy engine on ``path`` that can't write."""

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True, timeout=5)
        conn.execute("PRAGMA query_only = ON")
        return conn

    return create_engine("sqlite://", creator=connect, poolclass=NullPool)


def collect() -> dict:
    """The whole answer, as ``python -m olisar.activity`` prints it."""
    checks: dict = {}
    probe = threading.Thread(target=lambda: checks.update(health=health()), daemon=True)
    probe.start()  # alongside the queries: it's the slow part when the backend is stuck
    path = database_path()
    if not path.exists():
        return {"ok": False, "error": "The bot has no database yet."}
    engine = open_readonly(path)
    try:
        with Session(engine) as session:
            body = feed(session)
    finally:
        engine.dispose()
    probe.join(HEALTH_TIMEOUT + 1)
    return {"ok": True, "supported": True, **body, "health": checks.get("health")}


def main() -> int:
    try:
        answer = collect()
    except Exception as exc:  # noqa: BLE001 — the app shows it; a traceback helps nobody
        answer = {"ok": False, "error": f"Couldn't read the activity: {exc}"[:400]}
    sys.stdout.write(json.dumps(answer) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
