"""First-run setup wizard API — a loopback-only, pre-OAuth surface.

The packaged desktop app ships with no ``.env``, so before Discord OAuth can work an
operator must supply their bot token + OAuth credentials. These endpoints accept that
config over loopback only, and only while the app is unconfigured; once setup completes
they return 403 and the normal OAuth-gated admin API takes over.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from api.schemas import ApiKeysIn, SetupSaveIn, SetupTokenIn
from api.trust import is_local_request
from olisar import runtime_config, runtime_keys
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AppSecret

log = logging.getLogger("olisar.api.setup")
router = APIRouter(prefix="/api/setup", tags=["setup"])

_ME_URL = "https://discord.com/api/users/@me"
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
    }
    if not configured and is_local_request(request):
        body["prefill"] = _env_prefill()
    return body


@router.post("/validate-token", dependencies=[Depends(require_setup_access)])
async def validate_token(body: SetupTokenIn) -> dict:
    """Confirm a bot token by calling Discord as the bot, so the wizard can show
    'connected as <name>' before saving."""
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_ME_URL, headers={"Authorization": f"Bot {token}"})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="couldn't reach Discord — check your connection")
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Discord rejected that bot token")
    me = resp.json()
    return {"ok": True, "id": me.get("id"), "username": me.get("username")}


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

    # Tunnel config is set separately by /api/tunnel/enable, so we don't touch it here.
    await runtime_config.save(
        discord_token=token,
        discord_client_id=client_id,
        discord_client_secret=client_secret,
        target_guild_id=guild_id,
        configured=True,
    )
    # Make sure a stable signing secret exists now that we're configured.
    await runtime_config.session_secret()

    # (Re)start the bot in the packaged app; in dev there's no supervisor (the bot is a
    # separate process that reads .env), so this is a best-effort no-op.
    supervisor = getattr(request.app.state, "bot_supervisor", None)
    if supervisor is not None:
        try:
            await supervisor.restart()
        except Exception:
            log.exception("bot restart after setup failed")

    return {"ok": True, "redirect_uri": await runtime_config.oauth_redirect_uri()}
