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
from api.trust import is_local_request
from olisar import discord_app, runtime_config, updates
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
        # {to: "2.1"} while the VM's update script is moving this install onto a release.
        "updating": updates.updating(),
    }


@router.get("/status")
async def status(request: Request, admin: AdminUser = Depends(require_admin)) -> dict:
    return {**_state(_supervisor(request)), "can_power": bool(admin.is_allowlisted)}


async def _switch(mgr, on: bool) -> dict:
    from olisar.runtime.server import publish_bot_running

    if on:
        await mgr.start()
        # start() publishes once the task exists. A start that didn't (no token) leaves the
        # previous fact alone, so a bot that isn't set up isn't reported as powered down.
    else:
        await mgr.stop()
        await publish_bot_running(False)
    return _state(mgr)


def _from_this_machine(request: Request) -> bool:
    """A loopback request that didn't come from a page.

    ``docker exec`` curling the container sends no Origin. The console's own pages do, and a
    guild admin signed in there must not reach this: powering the bot stays on ``/power``,
    which demands the operator.
    """
    return is_local_request(request) and not request.headers.get("origin")


@router.post("/power")
async def power(body: PowerIn, request: Request, admin: AdminUser = Depends(require_admin)) -> dict:
    if not admin.is_allowlisted:
        raise HTTPException(status_code=403, detail="only the operator can power the bot on or off")
    mgr = _supervisor(request)
    if mgr is None:
        raise HTTPException(status_code=400, detail="bot control isn't available here")
    log.info("bot powered %s by operator %s", "on" if body.on else "off", admin.discord_user_id)
    return await _switch(mgr, body.on)


@router.post("/local")
async def local_power(body: PowerIn, request: Request) -> dict:
    """Start or stop the Discord bot from this machine, with no console session.

    The desktop app reaches this through ``docker exec`` on the VM. Anywhere else gets a 404,
    the same as a route that isn't there.
    """
    if not _from_this_machine(request):
        raise HTTPException(status_code=404, detail="Not Found")
    mgr = _supervisor(request)
    if mgr is None:
        raise HTTPException(status_code=400, detail="bot control isn't available here")
    log.info("bot powered %s from this machine", "on" if body.on else "off")
    return await _switch(mgr, body.on)


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
