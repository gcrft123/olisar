"""The desktop gateway's private line to this bot.

In the desktop app every bot is its own process behind the gateway (olisar/runtime/gateway.py),
which serves the console and forwards it to whichever bot the operator is looking at. Some of
what the console asks for is *about* a bot rather than *of* it — a status line for every bot in
the switcher, resetting or moving one you aren't looking at, lending one bot's VM to another —
and the gateway asks the bot itself for those here. The bot owns its data; the gateway never
opens another process's database.

Mounted only when a gateway started this process (``OLISAR_GATEWAY_TOKEN`` is set), and every
route demands that token on a loopback request, so neither the console nor a visitor through
the Funnel can call these directly. The gateway also refuses to forward ``/api/instance``.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.trust import is_local_request
from olisar import runtime_config, runtime_keys
from olisar.db.engine import session_scope
from olisar.db.models import AppConfig, AppSecret

log = logging.getLogger("olisar.api.instance")

GATEWAY_HEADER = "x-olisar-gateway"


def require_gateway(request: Request) -> None:
    expected = os.environ.get("OLISAR_GATEWAY_TOKEN", "")
    given = request.headers.get(GATEWAY_HEADER, "")
    if not (expected and is_local_request(request) and hmac.compare_digest(given, expected)):
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/api/instance", tags=["instance"], dependencies=[Depends(require_gateway)])

# Deployment config a "reset" clears (to defaults). Deliberately KEEPS the SSH keypair
# (server_ssh_pubkey/privkey — the app's identity, so reconnect works), session_secret,
# hosting_mode (a routing hint so a reset server bot lands on Reconnect, not the full wizard)
# and server_app_dir (so Reconnect finds this bot's install on a VM that runs several).
_RESET_CONFIG = dict(
    discord_token="", discord_client_id="", discord_client_secret="",
    target_guild_id=0, public_base_url="",
    tunnel_enabled=False, tunnel_hostname="", tunnel_node="", tunnel_token="",
    server_host="", configured=False,
)


class MoveIn(BaseModel):
    target: str            # 'local' | 'server'
    host: str | None = ""  # destination VM IP (server target)
    user: str | None = "ubuntu"


class KeyIn(BaseModel):
    pubkey: str


@router.get("")
async def status(request: Request) -> dict:
    """One bot's line in the switcher: whether it's set up, where it runs, and whether it's
    connected to Discord (and as whom)."""
    async with session_scope() as session:
        cfg = await session.get(AppConfig, 1)
    supervisor = getattr(request.app.state, "bot_supervisor", None)
    bot = getattr(supervisor, "bot", None) if supervisor is not None else None
    user = getattr(bot, "user", None) if bot is not None else None
    ready = bool(bot is not None and bot.is_ready())
    tunnel = getattr(request.app.state, "tunnel", None)
    return {
        "profile_id": getattr(request.app.state, "profile_id", None),
        "configured": await runtime_config.is_configured(),
        "hosting_mode": await runtime_config.hosting_mode(),
        "server_host": (cfg.server_host if cfg else "") or "",
        "bot": {
            "running": bool(supervisor is not None and supervisor.running),
            "ready": ready,
            "id": str(user.id) if ready and user else "",
            "name": str(user.name) if ready and user else "",
            "avatar": str(user.display_avatar.url) if ready and user else "",
            # Why it stopped, if it did so on its own (see BotSupervisor.error).
            "error": supervisor.error if supervisor is not None else None,
        },
        "remote": bool(tunnel is not None and tunnel.running),
    }


@router.post("/reset")
async def reset(request: Request) -> dict:
    """Clear this bot's deployment config (Discord creds, server, API keys → unconfigured),
    keeping its learned data + SSH key, and take its bot offline. Returns the hosting mode it
    had, so the console can route a reset server bot to Reconnect."""
    hosting = await runtime_config.hosting_mode()
    runtime_config.invalidate()
    await runtime_config.save(**_RESET_CONFIG)
    async with session_scope() as session:
        row = await session.get(AppSecret, 1)
        if row is not None:
            row.gemini_api_key = ""
            row.cloudflare_account_id = ""
            row.cloudflare_api_token = ""
            row.uex_api_key = ""
    runtime_keys.invalidate()
    runtime_config.invalidate()
    from olisar import discord_app
    from olisar.runtime import server

    discord_app.invalidate()
    await server.stop_bot(request.app)
    return {"ok": True, "hosting_mode": hosting}


@router.post("/move")
async def move(body: MoveIn, request: Request) -> dict:
    """Move this bot between hosts (local ↔ cloud VM), carrying its data + keeping the old
    copy as a backup. Long-running — the gateway waits on it with no timeout."""
    from olisar.runtime import migrate

    return await migrate.move(request.app, body.target, body.host or "", body.user or "ubuntu")


@router.get("/server")
async def server_share() -> dict:
    """What another bot needs to deploy onto this bot's VM (read from the VM's .env)."""
    from olisar.runtime import remote

    return await remote.share_info()


@router.post("/authorize-key")
async def authorize_key(body: KeyIn) -> dict:
    """Let another bot's SSH key into this bot's VM."""
    from olisar.runtime import remote

    return await remote.authorize_key(body.pubkey)
