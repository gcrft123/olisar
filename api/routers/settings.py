"""Operator settings surfaced in the dashboard's Settings popup: live logs, remote-access
status/logs/users, update checks, and the desktop menu-bar toggle. Account-scoped
(``require_admin``) — these are app-wide, not per-guild."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from api.auth.deps import (
    discord_identity,
    require_admin,
    require_admin_or_local,
    require_any_session,
    require_discord_identity,
)
from api.routers.marketplace import _registry_error, _registry_post
from api.schemas import DesktopSettingsIn, FeedbackIn
from olisar import logbuffer, runtime_config
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, AppConfig, Guild, GuildChannelInfo
from olisar.failures import claim as claim_failure
from olisar.updates import check_latest

log = logging.getLogger("olisar.api.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/logs")
async def get_logs(lines: int = 500, _: AdminUser | None = Depends(require_admin_or_local)) -> dict:
    """Recent backend log lines (bot + API), newest last."""
    lines = max(1, min(lines, 4000))
    return {"lines": logbuffer.tail(lines)}


@router.get("/updates")
async def get_updates(_: AdminUser | None = Depends(require_admin_or_local)) -> dict:
    """Whether a newer Olisar release is on GitHub."""
    return await check_latest()


@router.get("/report/{token}")
async def get_report(token: str, user_id: int = Depends(require_discord_identity)) -> dict:
    """A parked blank-reply failure, for the Feedback pane to open pre-filled.

    Reached by the **Report this** button Olisar puts on a blank reply. Claimable only by
    the Discord account the failure happened to (olisar/failures.py) — the button sits in a
    channel, so the link is readable by anyone who can see the message, and a DM prompt is
    nobody else's to read back.

    The captured logs are *not* in this response and never reach the browser. They go with
    the feedback when it's submitted, attached server-side — the same rule ``include_logs``
    already follows, for the same reason: they span every member's activity.
    """
    async with session_scope() as session:
        row = await claim_failure(session, token, user_id=user_id)
        if row is None:
            # One status for "no such token", "expired" and "not yours". Distinguishing
            # them would tell a stranger which tokens are real.
            raise HTTPException(status_code=404, detail="that report link isn't valid any more")
        guild = await session.get(Guild, row.guild_id) if row.guild_id else None
        channel = await session.get(GuildChannelInfo, row.channel_id) if row.channel_id else None
        return {
            "prompt": row.prompt,
            "trigger": row.trigger,
            "when": row.created_at.isoformat() if row.created_at else None,
            # Empty server name means a DM (guild 0) — the client says so rather than
            # naming a channel the reporter would have to recognize.
            "server": guild.name if guild else "",
            "channel": channel.name if channel else "",
            "has_logs": bool(row.logs),
        }


@router.post("/feedback")
async def send_feedback(
    body: FeedbackIn,
    actor: str = Depends(require_any_session),
    user_id: int | None = Depends(discord_identity),
) -> dict:
    """Email feedback (feedback / bug report / question) to the platform owner via the
    registry's Resend integration. Optional bot logs + attachments ride along.

    Logs are gathered **here**, not posted by the client. Reading them takes admin, so a
    member ticking "add bot logs" could only ever fail — and handing a member the bot's
    logs so they can attach them would leak every other member's activity to file one bug.
    The report still carries them; the reporter just never sees them.

    ``report_token`` changes *which* logs. A report filed from a blank reply attaches the
    ones captured at the failure, not the buffer as it stands now — by the time someone
    reaches the console the lines that explain it may be hundreds of messages back, and a
    bug report carrying the wrong hour of logs is worse than one carrying none. The toggle
    still decides whether any are attached; the token only decides which.
    """
    logs = body.logs
    if body.include_logs:
        captured = ""
        if body.report_token and user_id is not None:
            # Same ownership rule as the GET: a token from someone else's button attaches
            # nothing, and the feedback still sends.
            async with session_scope() as session:
                row = await claim_failure(session, body.report_token, user_id=user_id)
                captured = row.logs if row is not None else ""
            if not captured:
                log.info(
                    "feedback cited report %s… but it wasn't claimable; attaching live logs",
                    body.report_token[:8],
                )
        logs = captured or "\n".join(logbuffer.tail(800))
    payload = {
        "category": body.category,
        "message": body.message,
        "email": body.email,
        "logs": logs,
        # Who filed it, so a member report is distinguishable from an operator's without
        # the reporter having to say so.
        "reporter": actor,
        "attachments": [a.model_dump() for a in body.attachments],
    }
    r = await _registry_post("/v1/feedback", payload)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't send feedback")
    return r.json()


@router.get("/remote")
async def get_remote(request: Request, _: AdminUser = Depends(require_admin)) -> dict:
    """Remote-access (Tailscale Funnel) status, recent funnel/tunnel logs, and the list
    of admins who can reach the console."""
    from olisar.runtime.tunnel import funnel_helper_path

    mgr = getattr(request.app.state, "tunnel", None)
    status = {
        "available": mgr is not None,
        "running": bool(mgr and mgr.running),
        "helper": bool(funnel_helper_path()),
        # In a headless server deployment the funnel is env-managed (always on); the
        # console uses this to hide the on/off toggle it can't drive here.
        "headless": settings.headless,
        "hostname": await runtime_config.tunnel_hostname(),
        "public_url": await runtime_config.public_base_url(),
    }
    # Tunnel-related lines from the in-memory log (the funnel helper + our manager).
    logs = logbuffer.tail(300, contains="olisar.tunnel") + logbuffer.tail(300, contains="olisar.api.tunnel")
    logs = sorted(set(logs))[-200:]

    async with session_scope() as session:
        rows = (await session.execute(select(AdminUser).order_by(AdminUser.last_login.desc()))).scalars().all()
        users = [
            {
                "username": u.username or str(u.discord_user_id),
                "granted_via": (u.granted_via.value if hasattr(u.granted_via, "value") else str(u.granted_via)),
                "is_allowlisted": bool(u.is_allowlisted),
                "last_login": u.last_login.isoformat() if u.last_login else None,
                "guild_count": len(u.managed_guild_ids or []),
            }
            for u in rows
        ]
    return {"status": status, "logs": logs, "users": users}


@router.get("/desktop")
async def get_desktop(_: AdminUser | None = Depends(require_admin_or_local)) -> dict:
    """The desktop menu-bar toggle (honored by the Electron shell)."""
    async with session_scope() as session:
        cfg = await session.get(AppConfig, 1)
        return {"show_in_menu_bar": bool(cfg.show_in_menu_bar) if cfg else True}


@router.put("/desktop")
async def put_desktop(body: DesktopSettingsIn, _: AdminUser | None = Depends(require_admin_or_local)) -> dict:
    async with session_scope() as session:
        cfg = await session.get(AppConfig, 1)
        if cfg is None:
            cfg = AppConfig(id=1)
            session.add(cfg)
        if body.show_in_menu_bar is not None:
            cfg.show_in_menu_bar = body.show_in_menu_bar
    return {"ok": True}
