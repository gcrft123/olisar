"""First-run setup wizard API — a loopback-only, pre-OAuth surface.

The packaged desktop app ships with no ``.env``, so before Discord OAuth can work an
operator must supply their bot token + OAuth credentials. These endpoints accept that
config over loopback only, and only while the app is unconfigured; once setup completes
they return 403 and the normal OAuth-gated admin API takes over.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request

from api.schemas import ApiKeysIn, SetupKeyIn, SetupSaveIn, SetupSecretIn, SetupTokenIn
from api.trust import is_local_request
from olisar import discord_app, key_checks, runtime_config, runtime_keys
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AppSecret

log = logging.getLogger("olisar.api.setup")
router = APIRouter(prefix="/api/setup", tags=["setup"])

T = TypeVar("T")
_KEY_FIELDS = (
    "gemini_api_key",
    "cloudflare_account_id",
    "cloudflare_api_token",
    "uex_api_key",
)


async def require_setup_access(request: Request) -> None:
    """Admit only local, pre-configuration requests. This closes the pre-OAuth hole:
    remote (tunnel-forwarded or LAN) callers are refused, and once configured every
    mutating setup endpoint 403s."""
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="setup is only available on this machine")
    if await runtime_config.is_configured():
        raise HTTPException(status_code=403, detail="Olisar is already configured")


def _env_prefill() -> dict:
    """`.env` values for the wizard's fields, so an operator with a developer ``.env``
    doesn't retype them every install. Tailscale auth key comes from ``TAILSCALE_AUTH``
    (the ``settings.tunnel_token`` alias). Real values, not masked — single-operator,
    local-only, single-shot, see ``status()`` for the gate."""
    return {
        "discord_token": settings.discord_token or "",
        "discord_client_id": settings.discord_client_id or "",
        "discord_client_secret": settings.discord_client_secret or "",
        "target_guild_id": str(settings.target_guild_id or "") if settings.target_guild_id else "",
        "gemini_api_key": settings.gemini_api_key or "",
        "cloudflare_account_id": settings.cloudflare_account_id or "",
        "cloudflare_api_token": settings.cloudflare_api_token or "",
        "uex_api_key": settings.uex_api_key or "",
        "tunnel_token": settings.tunnel_token or "",
    }


@router.get("/status")
async def status(request: Request) -> dict:
    """Whether first-run setup is needed, plus the redirect URI to register.

    Includes a ``prefill`` block with the operator's ``.env`` values — but ONLY when
    (a) the request is local and (b) the app isn't configured yet, so secrets can't be
    read remotely or after setup."""
    configured = await runtime_config.is_configured()
    body: dict = {
        "configured": configured,
        "local_url": await runtime_config.public_base_url(),
        "redirect_uri": await runtime_config.oauth_redirect_uri(),
        "tunnel_enabled": await runtime_config.tunnel_enabled(),
        "hosting_mode": await runtime_config.hosting_mode(),
    }
    if not configured and is_local_request(request):
        body["prefill"] = _env_prefill()
    return body


def _token(body: SetupTokenIn) -> str:
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")
    return token


async def _ask_discord(call: Awaitable[T]) -> T:
    """Await a Discord call, turning its failures into the errors the wizard shows."""
    try:
        return await call
    except discord_app.BadToken:
        raise HTTPException(status_code=400, detail="Discord rejected that bot token")
    except (aiohttp.ClientError, asyncio.TimeoutError, discord_app.DiscordUnavailable) as exc:
        log.warning("Discord call failed during setup: %r", exc)
        raise HTTPException(status_code=502, detail="couldn't reach Discord — check your connection")


@router.post("/bot", dependencies=[Depends(require_setup_access)])
async def bot(body: SetupTokenIn) -> dict:
    """Check a pasted bot token and get its application ready: the client ID comes from
    it, and the intents Olisar needs are switched on where Discord allows it (see
    ``discord_app.prepare``)."""
    return await _ask_discord(discord_app.prepare(_token(body)))


@router.post("/discord-status", dependencies=[Depends(require_setup_access)])
async def discord_status(body: SetupTokenIn) -> dict:
    """What the wizard polls while the operator is in the Developer Portal or inviting
    the bot: the application's intents and redirect URLs, and the servers it's in."""
    token = _token(body)
    info = await _ask_discord(discord_app.inspect(token))
    info["guilds"] = await _ask_discord(discord_app.bot_guilds(token))
    return info


@router.post("/secret", dependencies=[Depends(require_setup_access)])
async def check_secret(body: SetupSecretIn) -> dict:
    """Whether a pasted client secret belongs to the bot's application."""
    client_id, secret = body.client_id.strip(), body.client_secret.strip()
    if not (client_id and secret):
        raise HTTPException(status_code=400, detail="client id and secret are required")
    return {"ok": await _ask_discord(discord_app.check_client_secret(client_id, secret))}


@router.post("/gemini", dependencies=[Depends(require_setup_access)])
async def check_gemini(body: SetupKeyIn) -> dict:
    """Whether Google accepts a pasted Gemini key."""
    key = body.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    try:
        return {"ok": await key_checks.gemini(key)}
    except key_checks.Unreachable:
        raise HTTPException(status_code=502, detail="couldn't reach Google — check your connection")


@router.post("/keys", dependencies=[Depends(require_setup_access)])
async def save_keys(body: ApiKeysIn) -> dict:
    """Store the operator's Gemini/Cloudflare/UEX keys during setup (the normal
    /api/keys requires an authenticated session, which doesn't exist yet)."""
    data = body.model_dump(exclude_unset=True)
    updates = {
        k: v.strip()
        for k, v in data.items()
        if k in _KEY_FIELDS and isinstance(v, str) and v.strip()
    }
    if updates:
        async with session_scope() as session:
            row = await session.get(AppSecret, 1)
            if row is None:
                row = AppSecret(id=1)
                session.add(row)
            for k, v in updates.items():
                setattr(row, k, v)
        runtime_keys.invalidate()
    return {"ok": True}


@router.post("/save", dependencies=[Depends(require_setup_access)])
async def save(body: SetupSaveIn, request: Request) -> dict:
    """Persist Discord credentials + tunnel choice, mark configured, (re)start the bot,
    and return the exact OAuth redirect URI to register in the Discord portal."""
    token = (body.discord_token or "").strip()
    client_id = (body.discord_client_id or "").strip()
    client_secret = (body.discord_client_secret or "").strip()
    if not (token and client_id and client_secret):
        raise HTTPException(
            status_code=400,
            detail="bot token, client id, and client secret are all required",
        )
    raw_guild = (body.target_guild_id or "").strip()
    guild_id = int(raw_guild) if raw_guild.isdigit() else 0

    # Tunnel config is set separately by /api/tunnel/enable, so we don't touch it here. Only
    # the local hosting choices save here (a server deploys or connects instead), so this bot
    # runs on this machine now, whatever it did before: left at 'server', the bot wouldn't
    # start and the console would open the server panel for a VM that isn't there.
    await runtime_config.save(
        discord_token=token,
        discord_client_id=client_id,
        discord_client_secret=client_secret,
        target_guild_id=guild_id,
        hosting_mode="local",
        server_host="",
        configured=True,
    )
    # Make sure a stable signing secret exists now that we're configured.
    await runtime_config.session_secret()
    # The cached application belongs to whatever token was configured before, if any.
    discord_app.invalidate()

    # (Re)start the bot in the packaged app; in dev there's no supervisor (the bot is a
    # separate process that reads .env), so this is a best-effort no-op.
    supervisor = getattr(request.app.state, "bot_supervisor", None)
    if supervisor is not None:
        try:
            await supervisor.restart()
        except Exception:
            log.exception("bot restart after setup failed")

    return {"ok": True, "redirect_uri": await runtime_config.oauth_redirect_uri()}
