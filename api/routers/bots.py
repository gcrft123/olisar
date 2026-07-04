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
from olisar import runtime_config
from olisar.runtime import profiles, switch

log = logging.getLogger("olisar.api.bots")
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
    return {
        **_view(p),
        "active_id": p["id"],
        "configured": await runtime_config.is_configured(),
        "hosting_mode": await runtime_config.hosting_mode(),
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


@router.delete("/{profile_id}")
async def delete_bot(profile_id: str) -> dict:
    """Delete a bot and its database. Refuses to delete the active bot or the last one."""
    try:
        profiles.delete(profile_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}
