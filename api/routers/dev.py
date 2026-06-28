"""Developer console proxy — platform-moderator tools over the marketplace registry.

The registry owns the developer whitelist + moderation state; this router proxies those
calls with the bot's publisher token (the registry maps token → publisher Discord id →
developer allowlist, and re-issues a fresh token on a 401). Management routes also require
the caller be this bot's operator. Moderation *standing* is readable by any admin, so a
warned/banned operator still sees their notice in the console.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth.deps import require_admin
from api.routers.extensions import _operator
from api.routers.marketplace import (
    _registry_error,
    _registry_get,
    _registry_post,
    _reregister_token,
)
from api.schemas import DevModerationIn, DevYankIn
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser
from olisar.extensions import signing

router = APIRouter(prefix="/api/dev", tags=["dev"])


async def _token() -> str:
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        return ident.registry_token or ""


async def _get(path: str, admin: AdminUser, params: dict | None = None):
    """Authenticated registry GET, with a one-shot token refresh on a stale (401) token."""
    token = await _token()
    if not token:
        raise HTTPException(status_code=400, detail="this bot isn't a registered publisher")
    r = await _registry_get(path, params=params, token=token)
    if r.status_code == 401:
        fresh = await _reregister_token(admin)
        if fresh:
            r = await _registry_get(path, params=params, token=fresh)
    return r


async def _post(path: str, body: dict, admin: AdminUser):
    token = await _token()
    if not token:
        raise HTTPException(status_code=400, detail="this bot isn't a registered publisher")
    r = await _registry_post(path, body, token=token)
    if r.status_code == 401:
        fresh = await _reregister_token(admin)
        if fresh:
            r = await _registry_post(path, body, token=fresh)
    return r


@router.get("/status")
async def status(admin: AdminUser = Depends(require_admin)) -> dict:
    """Whether this operator's bot is a whitelisted developer (drives the Developer tab)."""
    if not admin.is_allowlisted:
        return {"is_developer": False}
    try:
        r = await _get("/v1/dev/me", admin)
    except HTTPException:
        return {"is_developer": False}
    if r.status_code != 200:
        return {"is_developer": False}
    return {"is_developer": bool(r.json().get("is_developer"))}


@router.get("/extensions")
async def extensions(admin: AdminUser = Depends(require_admin)) -> dict:
    """Every marketplace extension with full metadata (developer-only)."""
    _operator(admin)
    r = await _get("/v1/dev/extensions", admin)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't load marketplace extensions")
    return r.json()


@router.get("/reports")
async def reports(admin: AdminUser = Depends(require_admin)) -> dict:
    _operator(admin)
    r = await _get("/v1/dev/reports", admin)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't load reports")
    return r.json()


@router.get("/blocked")
async def blocked(admin: AdminUser = Depends(require_admin)) -> dict:
    """Publishes blocked by a bot's risk review across the marketplace."""
    _operator(admin)
    r = await _get("/v1/dev/blocked", admin)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't load blocked publishes")
    return r.json()


@router.post("/reports/clear")
async def clear_reports(admin: AdminUser = Depends(require_admin)) -> dict:
    """Clear every standing abuse report (platform-developer only)."""
    _operator(admin)
    r = await _post("/v1/dev/reports/clear", {}, admin)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't clear reports")
    return r.json()


@router.post("/blocked/clear")
async def clear_blocked(admin: AdminUser = Depends(require_admin)) -> dict:
    """Clear every recorded blocked-publish (platform-developer only)."""
    _operator(admin)
    r = await _post("/v1/dev/blocked/clear", {}, admin)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't clear blocked publishes")
    return r.json()


@router.get("/source")
async def source(
    namespace: str, name: str, version: str = "", admin: AdminUser = Depends(require_admin)
) -> dict:
    _operator(admin)
    params = {"namespace": namespace, "name": name}
    if version:
        params["version"] = version
    r = await _get("/v1/dev/source", admin, params=params)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't load the source")
    return r.json()


@router.post("/yank")
async def yank(body: DevYankIn, admin: AdminUser = Depends(require_admin)) -> dict:
    _operator(admin)
    r = await _post(
        "/v1/dev/yank",
        {"namespace": body.namespace, "name": body.name, "version": body.version},
        admin,
    )
    if r.status_code != 200:
        raise _registry_error(r, "yank failed")
    return r.json()


@router.get("/moderation")
async def moderation_list(admin: AdminUser = Depends(require_admin)) -> dict:
    """Current warned/banned Discord ids (for the Moderation tab)."""
    _operator(admin)
    r = await _get("/v1/dev/moderation", admin)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't load moderation")
    return r.json()


@router.post("/moderation")
async def moderation(body: DevModerationIn, admin: AdminUser = Depends(require_admin)) -> dict:
    _operator(admin)
    r = await _post(
        "/v1/dev/moderation",
        {"discord_id": body.discord_id, "status": body.status, "message": body.message},
        admin,
    )
    if r.status_code != 200:
        raise _registry_error(r, "couldn't update moderation")
    return r.json()


@router.get("/standing")
async def standing(admin: AdminUser = Depends(require_admin)) -> dict:
    """This operator's own moderation standing (warn/ban), polled by the console."""
    try:
        r = await _registry_get("/v1/standing", params={"discord_id": str(admin.discord_user_id)})
    except HTTPException:
        return {"status": "ok"}
    if r.status_code != 200:
        return {"status": "ok"}
    return r.json()


@router.post("/standing/ack")
async def standing_ack(admin: AdminUser = Depends(require_admin)) -> dict:
    """Acknowledge a warning so the console stops showing it."""
    try:
        r = await _registry_post("/v1/standing/ack", {"discord_id": str(admin.discord_user_id)})
    except HTTPException:
        return {"ok": False}
    return {"ok": r.status_code == 200}
