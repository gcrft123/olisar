"""Power the Discord bot on/off from the console — operator only.

The unified backend keeps uvicorn + the dashboard running; this just stops/starts the bot
task on ``app.state.bot_supervisor``, so the operator can take Olisar offline (and bring it
back) without quitting the app. Restricted to the allowlisted operator: powering the bot
down affects *every* server it's in, so per-guild admins can't do it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth.deps import require_admin
from olisar.db.models import AdminUser

log = logging.getLogger("olisar.api.bot")
router = APIRouter(prefix="/api/bot", tags=["bot"])


class PowerIn(BaseModel):
    on: bool


def _supervisor(request: Request):
    return getattr(request.app.state, "bot_supervisor", None)


def _state(mgr) -> dict:
    bot = getattr(mgr, "bot", None) if mgr is not None else None
    return {
        "available": mgr is not None,
        "running": bool(mgr is not None and mgr.running),
        "ready": bool(bot is not None and bot.is_ready()),
    }


@router.get("/status")
async def status(request: Request, admin: AdminUser = Depends(require_admin)) -> dict:
    return {**_state(_supervisor(request)), "can_power": bool(admin.is_allowlisted)}


@router.post("/power")
async def power(body: PowerIn, request: Request, admin: AdminUser = Depends(require_admin)) -> dict:
    if not admin.is_allowlisted:
        raise HTTPException(status_code=403, detail="only the operator can power the bot on or off")
    mgr = _supervisor(request)
    if mgr is None:
        raise HTTPException(status_code=400, detail="bot control isn't available here")
    if body.on:
        await mgr.start()
    else:
        await mgr.stop()
    log.info("bot powered %s by operator %s", "on" if body.on else "off", admin.discord_user_id)
    return _state(mgr)
