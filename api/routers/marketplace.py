"""Marketplace browse + install — bridges the console to the extension registry.

The console talks to the bot, the bot talks to the registry (a configurable URL). That
avoids browser CORS, centralises the registry location, and lets self-hosters point at
their own registry. Browsing and installing are operator-only (an install creates a
package, like authoring). Install reuses the file-import pipeline — re-transpile,
re-verify the signature, consent (granted ⊆ requested) — recorded with origin=marketplace.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth.deps import require_admin
from api.routers.extensions import _operator, _resync_commands, install_bundle, preview_bundle
from api.schemas import MarketplaceInstallIn, MarketplaceRefIn
from olisar.config import settings
from olisar.db.models import AdminUser

log = logging.getLogger("olisar.api.marketplace")
router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

_TIMEOUT = 15.0
_NS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def _registry_base() -> str:
    return (settings.registry_url or "").rstrip("/")


async def _registry_get(path: str, params: dict | None = None) -> httpx.Response:
    base = _registry_base()
    if not base:
        raise HTTPException(status_code=503, detail="no marketplace registry is configured")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            return await client.get(base + path, params=params)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"couldn't reach the marketplace: {exc}") from exc


def _check_ref(namespace: str, name: str, version: str) -> None:
    if not (_NS_RE.match(namespace) and _NS_RE.match(name) and _VER_RE.match(version)):
        raise HTTPException(status_code=400, detail="invalid marketplace reference")


@router.get("/search")
async def search(q: str = "", category: str = "", admin: AdminUser = Depends(require_admin)) -> dict:
    _operator(admin)
    r = await _registry_get("/v1/search", {"q": q or None, "category": category or None})
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="marketplace search failed")
    return r.json()


@router.get("/ext/{namespace}/{name}")
async def detail(namespace: str, name: str, admin: AdminUser = Depends(require_admin)) -> dict:
    _operator(admin)
    if not (_NS_RE.match(namespace) and _NS_RE.match(name)):
        raise HTTPException(status_code=400, detail="invalid marketplace reference")
    r = await _registry_get(f"/v1/ext/{namespace}/{name}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="not found in the marketplace")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="marketplace lookup failed")
    return r.json()


async def _fetch_olx(namespace: str, name: str, version: str) -> dict:
    _check_ref(namespace, name, version)
    r = await _registry_get(f"/v1/ext/{namespace}/{name}/{version}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="that version isn't in the marketplace")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="couldn't fetch the bundle from the marketplace")
    try:
        return r.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="the marketplace returned an invalid bundle") from exc


@router.post("/install/preview")
async def install_preview(body: MarketplaceRefIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Fetch a marketplace bundle and preview it (same shape as file-import preview), so the
    console can show the consent screen before granting capabilities."""
    _operator(admin)
    doc = await _fetch_olx(body.namespace, body.name, body.version)
    preview = await preview_bundle(doc)
    preview["source"] = "marketplace"
    return preview


@router.post("/install")
async def install(
    body: MarketplaceInstallIn, request: Request, admin: AdminUser = Depends(require_admin)
) -> dict:
    """Install a marketplace extension, granting only the approved capabilities."""
    _operator(admin)
    doc = await _fetch_olx(body.namespace, body.name, body.version)
    ref = {
        "registry": _registry_base(), "namespace": body.namespace,
        "name": body.name, "version": body.version,
    }
    result = await install_bundle(
        doc, body.granted_permissions, actor=admin.discord_user_id,
        origin="marketplace", marketplace_ref=ref,
    )
    _resync_commands(request)
    return result
