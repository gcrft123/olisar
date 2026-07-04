"""Bot-profile management — loopback-only operator controls for running multiple bots.

Each "bot" is an independent profile: its own Discord token, config, secrets, and database
(:mod:`olisar.runtime.profiles`). v1 runs one *local* bot at a time; switching stops the
current one and starts the selected profile's bot (server-hosted profiles run on their own
VM, so switching to one just shows its control panel). These routes are loopback-gated like
setup/server — no Discord auth, since switching precedes login and is per-machine.

Named ``/api/bots`` (not ``/api/profiles``, which already returns Discord *member* profiles).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.trust import require_local_request
from olisar import runtime_config, runtime_keys
from olisar.db.engine import current_profile, reset_engine, session_scope
from olisar.db.models import AppConfig, AppSecret
from olisar.runtime import profiles, switch

log = logging.getLogger("olisar.api.bots")

# Deployment config a "reset" clears (to defaults). Deliberately KEEPS the SSH keypair
# (server_ssh_pubkey/privkey — the app's identity, so reconnect works), session_secret, and
# hosting_mode (a routing hint so a reset server bot lands on Reconnect, not the full wizard).
_RESET_CONFIG = dict(
    discord_token="", discord_client_id="", discord_client_secret="",
    target_guild_id=0, public_base_url="",
    tunnel_enabled=False, tunnel_hostname="", tunnel_node="", tunnel_token="",
    server_host="", configured=False,
)


async def _clear_deployment_config() -> None:
    """Clear the current profile's deployment config + API keys (keeps learned data). Runs
    against whatever DB the current engine points at — set the ``current_profile`` contextvar
    first to target a non-active profile."""
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
router = APIRouter(
    prefix="/api/bots",
    tags=["bots"],
    dependencies=[Depends(require_local_request)],
)


class CreateIn(BaseModel):
    name: str = ""


class SwitchIn(BaseModel):
    id: str


class RenameIn(BaseModel):
    id: str
    name: str


class MoveIn(BaseModel):
    target: str            # 'local' | 'server'
    host: str | None = ""  # destination VM IP (server target)
    user: str | None = "ubuntu"


def _view(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p.get("name") or p["id"],
        "created": bool(p.get("created")),
        "created_at": p.get("created_at"),
    }


@router.get("")
async def list_bots() -> dict:
    return {
        "profiles": [_view(p) for p in profiles.list()],
        "active_id": profiles.active_id(),
        "default_id": profiles.default_id(),
    }


@router.get("/active")
async def active_bot() -> dict:
    p = profiles.active()
    async with session_scope() as session:
        cfg = await session.get(AppConfig, 1)
        server_host = (cfg.server_host if cfg else "") or ""
    return {
        **_view(p),
        "active_id": p["id"],
        "configured": await runtime_config.is_configured(),
        "hosting_mode": await runtime_config.hosting_mode(),
        "server_host": server_host,
    }


@router.post("")
async def create_bot(body: CreateIn) -> dict:
    """Register a new (unconfigured) bot. Its database is built lazily on first switch."""
    return _view(profiles.create(body.name))


@router.post("/switch")
async def switch_bot(body: SwitchIn, request: Request) -> dict:
    """Adopt another bot as the active one — stops the current local bot and starts the
    target's. Returns the new profile's `{configured, hosting_mode}` so the console can
    route to the wizard / dashboard / server control panel."""
    try:
        status = await switch.switch_profile(request.app, body.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception:
        log.exception("bot switch to %s failed", body.id)
        raise HTTPException(status_code=500, detail="couldn't switch bots — see logs")
    return {"ok": True, **status}


@router.post("/rename")
async def rename_bot(body: RenameIn) -> dict:
    """Change a bot's display name."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="a name is required")
    if profiles.get(body.id) is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    profiles.rename(body.id, name)
    return {"ok": True}


@router.post("/default")
async def default_bot(body: SwitchIn) -> dict:
    """Pin which bot the app opens on launch (independent of the active bot)."""
    try:
        profiles.set_default(body.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "default_id": profiles.default_id()}


@router.post("/{profile_id}/reset")
async def reset_bot(profile_id: str, request: Request) -> dict:
    """Reset a bot's deployment config (Discord creds, server, API keys → `configured=False`),
    keeping its learned data + SSH key. Returns `{active, hosting_mode}` so the console can
    route: a reset server bot → Reconnect, a local bot → the setup wizard."""
    p = profiles.get(profile_id)
    if p is None:
        raise HTTPException(status_code=404, detail="unknown bot")
    if not p.get("created"):
        return {"ok": True, "active": profile_id == profiles.active_id(), "hosting_mode": "local"}

    active = profile_id == profiles.active_id()
    if active:
        hosting = await runtime_config.hosting_mode()
        await _clear_deployment_config()
        # A local bot is now tokenless — stop it; the console reloads and re-routes.
        from olisar.runtime import server
        await server.stop_all_supervisors(request.app)
    else:
        # Point the engine at the target profile's DB for the duration, then dispose it.
        token = current_profile.set(profile_id)
        try:
            runtime_config.invalidate(); runtime_keys.invalidate()
            hosting = await runtime_config.hosting_mode()
            await _clear_deployment_config()
        finally:
            current_profile.reset(token)
            await reset_engine(str(profiles.db_path_for(profile_id)))
            runtime_config.invalidate(); runtime_keys.invalidate()
    return {"ok": True, "active": active, "hosting_mode": hosting}


@router.post("/{profile_id}/move")
async def move_bot(profile_id: str, body: MoveIn, request: Request) -> dict:
    """Move the bot between hosts (local ↔ cloud VM), carrying its data + keeping the old copy
    as a backup. Only the active bot can be moved (moving stops/starts its local bot and swaps
    its live DB), so callers switch to it first. Long-running — no timeout on the client."""
    if profile_id != profiles.active_id():
        raise HTTPException(status_code=409, detail="switch to this bot before moving it")
    from olisar.runtime import migrate

    return await migrate.move(request.app, body.target, body.host or "", body.user or "ubuntu")


@router.delete("/{profile_id}")
async def delete_bot(profile_id: str) -> dict:
    """Delete a bot and its database. Refuses to delete the active bot or the last one."""
    try:
        profiles.delete(profile_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}
