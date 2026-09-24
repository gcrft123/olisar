"""Power the Discord bot on/off from the console — operator only.

The unified backend keeps uvicorn + the dashboard running; this just stops/starts the bot
task on ``app.state.bot_supervisor``, so the operator can take Olisar offline (and bring it
back) without quitting the app. Restricted to the allowlisted operator: powering the bot
down affects *every* server it's in, so per-guild admins can't do it.

``/reconnect`` is the way back from a bot Discord refused: it turns the missing intents on
where Discord lets the app do that itself, then starts the bot again.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth.deps import require_admin
from olisar import discord_app, runtime_config
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
        # Why it stopped, if it did so on its own: {kind: intents | token | other, ...}.
        "error": mgr.error if mgr is not None else None,
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


@router.post("/reconnect")
async def reconnect(request: Request, admin: AdminUser = Depends(require_admin)) -> dict:
    """Turn on the intents the bot is missing, where Discord allows the app to (fewer than
    100 servers), and start it again. Answers the bot's state plus ``intents_missing``: any
    left over are the operator's to switch on in the Developer Portal."""
    if not admin.is_allowlisted:
        raise HTTPException(status_code=403, detail="only the operator can reconnect the bot")
    mgr = _supervisor(request)
    if mgr is None:
        raise HTTPException(status_code=400, detail="bot control isn't available here")
    token = await runtime_config.discord_token()
    try:
        app = await discord_app.prepare(token)
    except discord_app.BadToken:
        raise HTTPException(status_code=400, detail="Discord rejected the bot token")
    except (aiohttp.ClientError, asyncio.TimeoutError, discord_app.DiscordUnavailable):
        raise HTTPException(status_code=502, detail="couldn't reach Discord — check your connection")
    discord_app.invalidate()
    if not app["intents_missing"]:
        await mgr.restart()
        log.info("bot reconnected by operator %s", admin.discord_user_id)
    return {**_state(mgr), "intents_missing": app["intents_missing"], "app_id": app["id"]}
