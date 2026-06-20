"""Operator settings surfaced in the dashboard's Settings popup: live logs, remote-access
status/logs/users, update checks, and the desktop menu-bar toggle. Account-scoped
(``require_admin``) — these are app-wide, not per-guild."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from api.auth.deps import require_admin
from api.schemas import DesktopSettingsIn
from olisar import logbuffer, runtime_config
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, AppConfig
from olisar.updates import check_latest

log = logging.getLogger("olisar.api.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/logs")
async def get_logs(lines: int = 500, _: AdminUser = Depends(require_admin)) -> dict:
    """Recent backend log lines (bot + API), newest last."""
    lines = max(1, min(lines, 4000))
    return {"lines": logbuffer.tail(lines)}


@router.get("/updates")
async def get_updates(_: AdminUser = Depends(require_admin)) -> dict:
    """Whether a newer Olisar release is on GitHub."""
    return await check_latest()


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
async def get_desktop(_: AdminUser = Depends(require_admin)) -> dict:
    """The desktop menu-bar toggle (honored by the Electron shell)."""
    async with session_scope() as session:
        cfg = await session.get(AppConfig, 1)
        return {"show_in_menu_bar": bool(cfg.show_in_menu_bar) if cfg else True}


@router.put("/desktop")
async def put_desktop(body: DesktopSettingsIn, _: AdminUser = Depends(require_admin)) -> dict:
    async with session_scope() as session:
        cfg = await session.get(AppConfig, 1)
        if cfg is None:
            cfg = AppConfig(id=1)
            session.add(cfg)
        if body.show_in_menu_bar is not None:
            cfg.show_in_menu_bar = body.show_in_menu_bar
    return {"ok": True}
