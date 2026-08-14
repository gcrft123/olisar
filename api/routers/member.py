"""Member portal — what Olisar knows about *you*, for members who aren't admins.

Every route here answers for the caller and nobody else. There is deliberately no way to
read another member's data, and no route returns message content the caller didn't write:
that keeps the portal clear of the channel-visibility problem entirely (the server-wide
search index spans channels the caller may not be able to see in Discord, so anything
rendering other people's messages would have to re-derive Discord's permission model, and
get it right every time). Your own words are always yours to see.

Authorization is ``require_member_guild`` (api/auth/deps.py): a valid member session, the
guild in the caller's membership list, the bot actually in it, and the operator having
opened the portal there. Mutating routes additionally carry a CSRF token.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from api.auth.deps import MemberContext, MemberGuildContext, require_member, require_member_guild
from api.schemas import MemberFactCorrectionIn, MemberForgetIn, MemberSettingsIn
from olisar.audit import record_audit
from olisar.db.engine import session_scope
from olisar.db.models import (
    FailureReport,
    Guild,
    GuildChannelInfo,
    GuildConfig,
    Message,
    Reminder,
    SearchMessage,
    UserMemory,
    UserMemoryKind,
    UserProfile,
    utcnow,
)
from olisar.memory.purge import forget_user
from olisar.memory.vectors import delete_embedding
from olisar.memory.writer import upsert_profile

log = logging.getLogger("olisar.api.member")
router = APIRouter(prefix="/api/member", tags=["member"])

_DM_GUILD_ID = 0  # DM preferences live on the guild-0 profile (matches /dm-indexing)

# Everything the portal writes is attributed under this prefix so the audit log can tell a
# member acting on their own data apart from an admin acting on the server's.
_ACTOR_PREFIX = "member:"


def _actor(user_id: int) -> str:
    return f"{_ACTOR_PREFIX}{user_id}"


def _client_ip(request: Request) -> str | None:
    # Behind the Funnel sidecar the socket peer is the proxy, so prefer the forwarded
    # address — same header the OAuth origin logic trusts.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ── Rate limiting ───────────────────────────────────────────────────────────────
# The expensive routes (a full export, a full purge) are reachable by every member of every
# server over a public URL. One in-process bucket per (user, route) is enough: the backend
# is a single process, and this bounds cost rather than defending a secret.
_last_call: dict[tuple[int, str], float] = {}


def _throttle(user_id: int, key: str, min_seconds: float) -> None:
    now = time.monotonic()
    previous = _last_call.get((user_id, key))
    if previous is not None and now - previous < min_seconds:
        wait = int(min_seconds - (now - previous)) + 1
        raise HTTPException(status_code=429, detail=f"too many requests — try again in {wait}s")
    _last_call[(user_id, key)] = now


def _jump_link(guild_id: int, channel_id: int | None, message_id: int | None) -> str | None:
    """A Discord deep link to the message a fact came from, so a member can go read the
    thing Olisar drew a conclusion from. None when the source message has since been pruned
    (summarized away) — the fact outlives the message it came from."""
    if not channel_id or not message_id:
        return None
    scope = "@me" if guild_id == _DM_GUILD_ID else str(guild_id)
    return f"https://discord.com/channels/{scope}/{channel_id}/{message_id}"


async def _profile(session, guild_id: int, user_id: int) -> UserProfile | None:
    return await session.scalar(
        select(UserProfile).where(
            UserProfile.guild_id == guild_id, UserProfile.user_id == user_id
        )
    )


_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


async def _breakdowns(session, guild_id: int, user_id: int) -> dict[str, list[dict]]:
    """Per-figure composition, for the donut behind each statistic on the portal.

    Every list is returned in a STABLE, meaningful order — channel position, enum order,
    chronological — and never sorted by size. The client assigns a series hue by position
    and colours the chip after its largest slice; sorting by value here would put the
    biggest slice at ``us0`` every time and turn every chip on the page blue.
    """
    # Stored messages by channel, in the order the server lists its channels.
    rows = (
        await session.execute(
            select(GuildChannelInfo.name, func.count(Message.id))
            .join(Message, Message.channel_id == GuildChannelInfo.channel_id)
            .where(Message.guild_id == guild_id, Message.author_id == user_id)
            .group_by(GuildChannelInfo.channel_id)
            .order_by(GuildChannelInfo.position.asc())
        )
    ).all()
    by_channel = [{"label": f"#{name}" if name else "unknown", "value": int(n)} for name, n in rows]

    # Indexed messages carry their channel name inline (the index spans channels the
    # roster may not cover), so this one groups on the stored name.
    rows = (
        await session.execute(
            select(SearchMessage.channel_name, func.count(SearchMessage.id))
            .where(SearchMessage.guild_id == guild_id, SearchMessage.author_id == user_id)
            .group_by(SearchMessage.channel_name)
            .order_by(SearchMessage.channel_name.asc())
        )
    ).all()
    by_index = [{"label": f"#{name}" if name else "unknown", "value": int(n)} for name, n in rows]

    # Remembered things by kind, in the enum's own order.
    rows = (
        await session.execute(
            select(UserMemory.kind, func.count(UserMemory.id))
            .where(UserMemory.guild_id == guild_id, UserMemory.user_id == user_id)
            .group_by(UserMemory.kind)
        )
    ).all()
    counts = {(k.value if hasattr(k, "value") else str(k)): int(n) for k, n in rows}
    by_kind = [
        {"label": label, "value": counts.get(key, 0)}
        for key, label in (("fact", "Facts"), ("preference", "Preferences"), ("event", "Events"))
        if counts.get(key, 0)
    ]

    # Days seen, by month — a day counts once however much was said that day.
    rows = (
        await session.execute(
            select(
                func.strftime("%Y-%m", Message.created_at).label("ym"),
                func.count(func.distinct(func.date(Message.created_at))),
            )
            .where(Message.guild_id == guild_id, Message.author_id == user_id)
            .group_by("ym")
            .order_by("ym")
        )
    ).all()
    by_month = []
    for ym, n in rows:
        try:
            month = _MONTHS[int(str(ym).split("-")[1]) - 1]
        except (IndexError, ValueError):
            month = str(ym)
        by_month.append({"label": month, "value": int(n)})

    return {"messages": by_channel, "indexed": by_index, "facts": by_kind, "days": by_month}


async def _hours_utc(session, guild_id: int, user_id: int) -> list[int]:
    """Message counts per hour of the day, indexed 0–23, in **UTC**.

    Deliberately not bucketed here. "You talk mostly in the evening" is a claim about the
    member's own evening, and the server has no idea what that is — core stores no per-member
    timezone (the marketplace timezone extension is not core, and most members won't have it).
    The browser does know, so the histogram is shipped raw and the client rotates it by its
    own offset before folding it into four buckets. Bucketing in UTC here would have made the
    figure wrong by up to half a day for anyone outside it.
    """
    rows = (
        await session.execute(
            select(func.strftime("%H", Message.created_at), func.count(Message.id))
            .where(Message.guild_id == guild_id, Message.author_id == user_id)
            .group_by(func.strftime("%H", Message.created_at))
        )
    ).all()
    hours = [0] * 24
    for raw, n in rows:
        try:
            hours[int(raw)] = int(n)
        except (TypeError, ValueError, IndexError):
            continue
    return hours


async def _count_for(session, model, user_column: str, guild_id: int, user_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.guild_id == guild_id, getattr(model, user_column) == user_id)
        )
        or 0
    )


# ── Identity ────────────────────────────────────────────────────────────────────


@router.get("/session")
async def member_session(ctx: MemberContext = Depends(require_member)) -> dict:
    """Who you are, the CSRF token for your mutating calls, and the servers where you can
    actually use the portal — the intersection of your membership with the servers whose
    operator has opened it. A server you're in that hasn't opened the portal is simply
    absent; the portal never advertises a door that isn't there."""
    member = ctx.member
    claimed = [int(g) for g in (member.guild_ids or [])]
    servers: list[dict] = []
    if claimed:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Guild.id, Guild.name, Guild.icon)
                    .join(GuildConfig, GuildConfig.guild_id == Guild.id)
                    .where(
                        Guild.id.in_(claimed),
                        Guild.active.is_(True),
                        GuildConfig.member_portal_enabled.is_(True),
                    )
                    .order_by(Guild.name.asc())
                )
            ).all()
            servers = [{"id": str(gid), "name": name, "icon": icon} for gid, name, icon in rows]
    return {
        "user_id": str(member.discord_user_id),
        "username": member.username,
        "avatar": member.avatar,
        "csrf": ctx.csrf,
        "servers": servers,
    }


# ── The mirror ──────────────────────────────────────────────────────────────────


@router.get("/overview")
async def overview(ctx: MemberGuildContext = Depends(require_member_guild)) -> dict:
    """Your footprint in this server, in numbers, plus the flags governing it — and the
    impression Olisar has formed of you, if this server's operator has chosen to show it."""
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    async with session_scope() as session:
        profile = await _profile(session, gid, uid)
        dm_profile = await _profile(session, _DM_GUILD_ID, uid)
        counts = {
            "messages": await _count_for(session, Message, "author_id", gid, uid),
            "indexed": await _count_for(session, SearchMessage, "author_id", gid, uid),
            "facts": await _count_for(session, UserMemory, "user_id", gid, uid),
            "reminders": await _count_for(session, Reminder, "user_id", gid, uid),
        }
        persona = ""
        persona_updated = None
        if ctx.show_persona and profile is not None:
            persona = profile.persona_summary or ""
            persona_updated = profile.persona_updated_at
        return {
            "counts": counts,
            "first_seen": profile.first_seen if profile else None,
            "last_seen": profile.last_seen if profile else None,
            "settings": {
                "memory_opt_out": bool(profile.memory_opt_out) if profile else False,
                "search_opt_out": bool(profile.search_opt_out) if profile else False,
                "pause_until": profile.pause_until if profile else None,
                # DM preferences aren't per-server; read from the guild-0 profile.
                "dm_opt_out": bool(dm_profile.dm_opt_out) if dm_profile else False,
            },
            # The client shouldn't have to infer *why* the impression is missing — an
            # operator who hasn't opened it and a member Olisar hasn't formed one of yet
            # are different states and read differently on the page.
            "persona_visible": ctx.show_persona,
            "persona": persona,
            "persona_updated_at": persona_updated,
            "breakdowns": await _breakdowns(session, gid, uid),
            # Raw, for the client to rotate into its own timezone — see _hours_utc.
            "hours_utc": await _hours_utc(session, gid, uid),
        }


@router.get("/facts")
async def facts(ctx: MemberGuildContext = Depends(require_member_guild)) -> dict:
    """Every durable thing Olisar has remembered about you here, each with a link back to
    the message it came from."""
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(UserMemory, Message.channel_id)
                .outerjoin(Message, Message.message_id == UserMemory.source_message_id)
                .where(UserMemory.user_id == uid, UserMemory.guild_id == gid)
                .order_by(UserMemory.created_at.desc())
            )
        ).all()
    return {
        "facts": [
            {
                "id": fact.id,
                "kind": fact.kind.value if fact.kind else "fact",
                "content": fact.content,
                "salience": fact.salience,
                "event_date": fact.event_date,
                "created_at": fact.created_at,
                "source_link": _jump_link(gid, channel_id, fact.source_message_id),
            }
            for fact, channel_id in rows
        ]
    }


@router.delete("/facts/{fact_id}")
async def delete_fact(
    request: Request, fact_id: int, ctx: MemberGuildContext = Depends(require_member_guild)
) -> dict:
    """Forget one thing. The point of the portal: today the only granularity a member has
    is /forget-me, which erases everything."""
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    async with session_scope() as session:
        fact = await session.get(UserMemory, fact_id)
        # Both scopes checked, and a mismatch 404s rather than 403s — a member probing ids
        # shouldn't be able to learn that a fact exists on someone else.
        if fact is None or fact.user_id != uid or fact.guild_id != gid:
            raise HTTPException(status_code=404, detail="no such fact")
        # The vector goes with the row. Deleting one without the other leaves a ghost that
        # semantic recall can still surface — see olisar/memory/purge.py.
        await delete_embedding(session, "user_memory_embedding", fact.id)
        await session.delete(fact)
        await record_audit(
            session,
            actor=_actor(uid),
            action="member_delete_fact",
            target_type="user_memory",
            target_id=fact_id,
            ip=_client_ip(request),
        )
    return {"ok": True}


@router.post("/facts/correction")
async def add_correction(
    request: Request,
    body: MemberFactCorrectionIn,
    ctx: MemberGuildContext = Depends(require_member_guild),
) -> dict:
    """Tell Olisar its read on you is wrong.

    Written as a new, high-salience fact rather than an edit to ``persona_summary``: the
    summary is regenerated from message history on a threshold, so an edit would be quietly
    overwritten on the member's next fifteen messages. A fact persists and is retrievable,
    so the correction actually reaches the model.
    """
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="say what's wrong")
    async with session_scope() as session:
        fact = UserMemory(
            user_id=uid,
            guild_id=gid,
            kind=UserMemoryKind.preference,
            content=f"Correction from the member themselves: {content}",
            # Above anything mined from messages: they're describing themselves directly,
            # which beats an inference drawn from what they happened to type.
            salience=0.95,
        )
        session.add(fact)
        await record_audit(
            session,
            actor=_actor(uid),
            action="member_add_correction",
            target_type="user_memory",
            ip=_client_ip(request),
        )
    return {"ok": True}


# ── Controls ────────────────────────────────────────────────────────────────────


@router.patch("/settings")
async def update_settings(
    request: Request,
    body: MemberSettingsIn,
    ctx: MemberGuildContext = Depends(require_member_guild),
) -> dict:
    """Change how you're recorded here. Partial — only the fields sent are written."""
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    data = body.model_dump(exclude_unset=True)
    if not data:
        return {"ok": True}
    async with session_scope() as session:
        # upsert rather than fetch: a member can reach the portal before Olisar has ever
        # recorded them (they've been in the server, just never spoken in a watched
        # channel), and their preferences must still stick.
        profile = await upsert_profile(session, gid, uid, ctx.member.username)
        before = {
            "memory_opt_out": profile.memory_opt_out,
            "search_opt_out": profile.search_opt_out,
            "pause_until": profile.pause_until.isoformat() if profile.pause_until else None,
        }
        if "memory_opt_out" in data:
            profile.memory_opt_out = bool(data["memory_opt_out"])
        if "search_opt_out" in data:
            profile.search_opt_out = bool(data["search_opt_out"])
        if "pause_hours" in data:
            hours = int(data["pause_hours"] or 0)
            profile.pause_until = utcnow() + timedelta(hours=hours) if hours else None
        if "dm_opt_out" in data:
            # DMs aren't per-guild — always the guild-0 profile, whatever server the portal
            # happens to be scoped to.
            dm_profile = await upsert_profile(
                session, _DM_GUILD_ID, uid, ctx.member.username
            )
            dm_profile.dm_opt_out = bool(data["dm_opt_out"])
            before["dm_opt_out"] = not bool(data["dm_opt_out"])
        await record_audit(
            session,
            actor=_actor(uid),
            action="member_update_settings",
            target_type="user_profile",
            target_id=uid,
            before=before,
            after=data,
            ip=_client_ip(request),
        )
    return {"ok": True}


# ── Reminders ───────────────────────────────────────────────────────────────────


@router.get("/reminders")
async def reminders(ctx: MemberGuildContext = Depends(require_member_guild)) -> dict:
    """Your pending reminders — including the ones Olisar created for you off a time-bound
    fact, which until now had no surface at all: they were set conversationally and then
    only reappeared when they fired."""
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(Reminder)
                .where(
                    Reminder.user_id == uid,
                    Reminder.guild_id == gid,
                    Reminder.fired == False,  # noqa: E712
                )
                .order_by(Reminder.scheduled_at.asc())
            )
        ).all()
        return {
            "reminders": [
                {
                    "id": r.id,
                    "content": r.content,
                    "scheduled_at": r.scheduled_at,
                    "target": r.target,
                    # "user" = you asked for it; "event_fact" = Olisar inferred it from
                    # something you said. Worth distinguishing on the page.
                    "source": r.source,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
        }


@router.delete("/reminders/{reminder_id}")
async def cancel_reminder(
    request: Request, reminder_id: int, ctx: MemberGuildContext = Depends(require_member_guild)
) -> dict:
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    async with session_scope() as session:
        reminder = await session.get(Reminder, reminder_id)
        if (
            reminder is None
            or reminder.user_id != uid
            or reminder.guild_id != gid
            or reminder.fired
        ):
            raise HTTPException(status_code=404, detail="no such pending reminder")
        # Marked fired rather than deleted, matching the cancel_reminder tool — the dispatch
        # loop skips fired rows, and keeping it preserves the record of what was asked for.
        reminder.fired = True
        await record_audit(
            session,
            actor=_actor(uid),
            action="member_cancel_reminder",
            target_type="reminder",
            target_id=reminder_id,
            ip=_client_ip(request),
        )
    return {"ok": True}


# ── Export & erasure ────────────────────────────────────────────────────────────


@router.get("/export")
async def export(request: Request, ctx: MemberGuildContext = Depends(require_member_guild)):
    """Everything Olisar holds about you in this server, as a JSON download.

    Each table is serialized by hand (the fields worth showing a person differ per table),
    but the *set* of tables is contractually ``MEMBER_DATA_TABLES`` in olisar/memory/purge.py
    — the same list ``forget_user`` deletes from. Parity between the two is enforced by
    test_member_portal, so an export can't quietly omit a table the purge covers, nor the
    purge miss one the export just promised.
    """
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    _throttle(uid, "export", 60.0)
    async with session_scope() as session:
        profile = await _profile(session, gid, uid)
        dm_profile = await _profile(session, _DM_GUILD_ID, uid)
        guild = await session.get(Guild, gid)
        messages = (
            await session.scalars(
                select(Message)
                .where(Message.guild_id == gid, Message.author_id == uid)
                .order_by(Message.created_at.asc())
            )
        ).all()
        indexed = (
            await session.scalars(
                select(SearchMessage)
                .where(SearchMessage.guild_id == gid, SearchMessage.author_id == uid)
                .order_by(SearchMessage.created_at.asc())
            )
        ).all()
        memories = (
            await session.scalars(
                select(UserMemory)
                .where(UserMemory.guild_id == gid, UserMemory.user_id == uid)
                .order_by(UserMemory.created_at.asc())
            )
        ).all()
        rows = (
            await session.scalars(
                select(Reminder)
                .where(Reminder.guild_id == gid, Reminder.user_id == uid)
                .order_by(Reminder.scheduled_at.asc())
            )
        ).all()
        blanks = (
            await session.scalars(
                select(FailureReport)
                .where(FailureReport.guild_id == gid, FailureReport.user_id == uid)
                .order_by(FailureReport.created_at.asc())
            )
        ).all()

        payload = {
            "exported_at": utcnow().isoformat(),
            "server": {"id": str(gid), "name": guild.name if guild else ""},
            "you": {
                "user_id": str(uid),
                "display_name": profile.display_name if profile else "",
                "first_seen": profile.first_seen.isoformat() if profile else None,
                "last_seen": profile.last_seen.isoformat() if profile else None,
                # Included whatever the operator's display setting: that toggle governs
                # what the *page* shows, and an export of "everything you hold about me"
                # that silently drops the impression would not be that.
                "impression": profile.persona_summary if profile else "",
                "memory_opt_out": bool(profile.memory_opt_out) if profile else False,
                "search_opt_out": bool(profile.search_opt_out) if profile else False,
                "dm_opt_out": bool(dm_profile.dm_opt_out) if dm_profile else False,
            },
            "messages": [
                {
                    "channel_id": str(m.channel_id),
                    "message_id": str(m.message_id),
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
            ],
            "search_index": [
                {
                    "channel": s.channel_name,
                    "message_id": str(s.message_id),
                    "content": s.content,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in indexed
            ],
            "remembered_facts": [
                {
                    "kind": f.kind.value if f.kind else "fact",
                    "content": f.content,
                    "salience": f.salience,
                    "event_date": f.event_date.isoformat() if f.event_date else None,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in memories
            ],
            "reminders": [
                {
                    "content": r.content,
                    "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
                    "source": r.source,
                    "fired": r.fired,
                }
                for r in rows
            ],
            # Times Olisar drew a blank on you, kept for a few days so you can report them
            # from the Feedback pane. Your prompt is here because it's yours. The bot logs
            # captured alongside it are not: they're the bot's record of everyone's
            # activity in that window, which is exactly why no reporter — member or
            # operator — is ever shown them (see olisar/db/models.py:FailureReport).
            "unreported_failures": [
                {
                    "prompt": f.prompt,
                    "how_you_reached_olisar": f.trigger,
                    "when": f.created_at.isoformat() if f.created_at else None,
                    "expires": f.expires_at.isoformat() if f.expires_at else None,
                }
                for f in blanks
            ],
        }
        await record_audit(
            session,
            actor=_actor(uid),
            action="member_export",
            target_type="guild",
            target_id=gid,
            ip=_client_ip(request),
        )
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="olisar-my-data-{gid}.json"'},
    )


@router.post("/forget")
async def forget(
    request: Request,
    body: MemberForgetIn,
    ctx: MemberGuildContext = Depends(require_member_guild),
) -> dict:
    """Erase everything Olisar holds about you in this server — the web equivalent of
    ``/forget-me``, and the same code path, so the two can't diverge."""
    uid, gid = ctx.member.discord_user_id, ctx.guild_id
    _throttle(uid, "forget", 30.0)
    async with session_scope() as session:
        result = await forget_user(
            session, guild_ids=[gid], user_id=uid, opt_out=body.stop_remembering
        )
        # The action is recorded; the erased content is not. Logging what someone deleted
        # would defeat the deletion, so `before`/`after` stay empty and only counts survive.
        await record_audit(
            session,
            actor=_actor(uid),
            action="member_forget",
            target_type="guild",
            target_id=gid,
            after={"counts": result, "opted_out": body.stop_remembering},
            ip=_client_ip(request),
        )
    return result
