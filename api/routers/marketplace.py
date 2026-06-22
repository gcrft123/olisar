"""Marketplace browse + install — bridges the console to the extension registry.

The console talks to the bot, the bot talks to the registry (a configurable URL). That
avoids browser CORS, centralises the registry location, and lets self-hosters point at
their own registry. Browsing and installing are operator-only (an install creates a
package, like authoring). Install reuses the file-import pipeline — re-transpile,
re-verify the signature, consent (granted ⊆ requested) — recorded with origin=marketplace.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from api.auth.deps import require_admin
from api.auth.oauth import AUTHORIZE_URL, TOKEN_URL, _get_state_serializer, _is_secure, _origin
from olisar import runtime_config
from api.routers.extensions import (
    _operator,
    _resync_commands,
    build_signed_bundle,
    install_bundle,
    preview_bundle,
)
from api.schemas import (
    MarketplaceInstallIn,
    MarketplacePublishIn,
    MarketplaceRefIn,
    MarketplaceRegisterIn,
    MarketplaceUpdateApplyIn,
    MarketplaceUpdateIn,
    MarketplaceYankIn,
)
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, ExtensionPackage, utcnow
from olisar.extensions import signing

log = logging.getLogger("olisar.api.marketplace")
router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

_TIMEOUT = 15.0
_NS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
VERIFY_STATE_COOKIE = "olisar_verify_state"


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


async def _registry_post(path: str, body: dict, token: str | None = None) -> httpx.Response:
    base = _registry_base()
    if not base:
        raise HTTPException(status_code=503, detail="no marketplace registry is configured")
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            return await client.post(base + path, json=body, headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"couldn't reach the marketplace: {exc}") from exc


def _registry_error(r: httpx.Response, fallback: str) -> HTTPException:
    """Surface a registry error, passing through meaningful client-error statuses."""
    try:
        detail = r.json().get("error") or fallback
    except Exception:  # noqa: BLE001
        detail = fallback
    status = r.status_code if r.status_code in (400, 401, 403, 409, 413, 429, 507) else 502
    return HTTPException(status_code=status, detail=detail)


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


# ── updates & revocation ────────────────────────────────────────────────────
@router.get("/installed")
async def installed(admin: AdminUser = Depends(require_admin)) -> dict:
    """For every marketplace-installed extension, report whether a newer version is
    available or it's been yanked — so the catalog can surface Update / Removed."""
    _operator(admin)
    async with session_scope() as session:
        rows = [
            (p.key, p.version, p.marketplace_ref)
            for p in (await session.scalars(
                select(ExtensionPackage).where(ExtensionPackage.origin == "marketplace")
            )).all()
        ]
    out: dict = {}
    for key, ver, refj in rows:
        if not refj:
            continue
        ref = json.loads(refj)
        try:
            r = await _registry_get(f"/v1/ext/{ref['namespace']}/{ref['name']}")
        except HTTPException:
            continue
        if r.status_code == 404:
            out[key] = {"installed_version": ver, "yanked": True, "gone": True}
            continue
        if r.status_code != 200:
            continue
        d = r.json()
        latest = d.get("version")
        versions = d.get("versions") or []
        latest_yanked = any(v.get("version") == latest and v.get("yanked") for v in versions)
        yanked = d.get("status") == "yanked" or latest_yanked
        out[key] = {
            "installed_version": ver,
            "latest_version": latest,
            "update_available": bool(latest and latest != ver and not yanked),
            "yanked": yanked,
        }
    return out


async def _latest_for_installed(key: str) -> tuple:
    async with session_scope() as session:
        pkg = await session.get(ExtensionPackage, key)
    if pkg is None or pkg.origin != "marketplace" or not pkg.marketplace_ref:
        raise HTTPException(status_code=400, detail="not a marketplace-installed extension")
    ref = json.loads(pkg.marketplace_ref)
    r = await _registry_get(f"/v1/ext/{ref['namespace']}/{ref['name']}")
    if r.status_code == 404:
        raise HTTPException(status_code=410, detail="this extension is no longer in the marketplace")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="marketplace lookup failed")
    latest = r.json().get("version")
    doc = await _fetch_olx(ref["namespace"], ref["name"], latest)
    return pkg, ref, latest, doc


@router.post("/update/preview")
async def update_preview(body: MarketplaceUpdateIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Fetch the latest version of an installed marketplace extension and preview it, so the
    operator can re-consent (especially if the requested permissions changed)."""
    _operator(admin)
    pkg, _ref, latest, doc = await _latest_for_installed(body.key)
    preview = await preview_bundle(doc)
    preview["exists"] = False  # it's an update of this very extension, not a collision
    preview["source"] = "marketplace"
    preview["from_version"] = pkg.version
    preview["to_version"] = latest
    return preview


@router.post("/update")
async def update(
    body: MarketplaceUpdateApplyIn, request: Request, admin: AdminUser = Depends(require_admin)
) -> dict:
    """Apply the latest version of an installed marketplace extension (replace in place)."""
    _operator(admin)
    _pkg, ref, latest, doc = await _latest_for_installed(body.key)
    result = await install_bundle(
        doc, body.granted_permissions, actor=admin.discord_user_id, origin="marketplace",
        marketplace_ref={**ref, "version": latest}, replace=True,
    )
    _resync_commands(request)
    return result


# ── publishing (this bot as a publisher) ────────────────────────────────────
@router.get("/publisher")
async def publisher(admin: AdminUser = Depends(require_admin)) -> dict:
    """This bot's publisher identity + registration status."""
    _operator(admin)
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        return {
            "fingerprint": ident.fingerprint,
            "handle": ident.registry_handle,
            "registered": bool(ident.registry_token and ident.registry_handle),
            "verified": bool(ident.registry_verified),
        }


@router.post("/register")
async def register(body: MarketplaceRegisterIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Claim a publisher handle (namespace) on the registry, bound to this bot's signing
    key. The private key never leaves the bot; only the public key + handle are sent."""
    _operator(admin)
    handle = (body.handle or "").strip().lower()
    if not _NS_RE.match(handle):
        raise HTTPException(status_code=400, detail="handle must be 2-64 chars: a-z, 0-9, _ or -")
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        r = await _registry_post("/v1/publishers/register", {
            "public_key": ident.public_key, "handle": handle,
            "discord_id": str(admin.discord_user_id),
        })
        if r.status_code != 200:
            raise _registry_error(r, "registration failed")
        data = r.json()
        ident.registry_handle = data["handle"]
        ident.registry_token = data["token"]
        ident.registered_at = utcnow()
    return {"ok": True, "handle": data["handle"], "fingerprint": data.get("fingerprint")}


@router.post("/publish")
async def publish(body: MarketplacePublishIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Publish a local extension to the registry under this bot's handle (signed)."""
    _operator(admin)
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        if not (ident.registry_token and ident.registry_handle):
            raise HTTPException(status_code=400, detail="claim a publisher handle first")
        pkg = await session.get(ExtensionPackage, body.key)
        if pkg is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        if not (pkg.source_ts or "").strip():
            raise HTTPException(status_code=400, detail="this extension has no source to publish")
        doc = await build_signed_bundle(session, pkg)
        token = ident.registry_token
    r = await _registry_post("/v1/publish", {"bundle": doc}, token=token)
    if r.status_code != 200:
        raise _registry_error(r, "publish failed")
    return r.json()


@router.post("/yank")
async def yank(body: MarketplaceYankIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Pull a published extension (or one version) from the registry."""
    _operator(admin)
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        if not ident.registry_token:
            raise HTTPException(status_code=400, detail="this bot isn't a registered publisher")
        token = ident.registry_token
    r = await _registry_post("/v1/yank", {"name": body.name, "version": body.version}, token=token)
    if r.status_code != 200:
        raise _registry_error(r, "yank failed")
    return r.json()


# ── Discord-verified publisher ──────────────────────────────────────────────
# An isolated OAuth flow (separate from console login): the console opens /verify/start,
# the operator approves on Discord, /verify/callback forwards the short-lived `identify`
# token to the registry, which confirms it with Discord and sets the verified badge.
async def complete_discord_verification(discord_token: str) -> bool:
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        token = ident.registry_token
    if not token:
        return False
    try:
        r = await _registry_post("/v1/publishers/verify", {"discord_token": discord_token}, token=token)
    except HTTPException:
        return False
    if r.status_code != 200:
        return False
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        ident.registry_verified = True
    return True


@router.get("/verify/start")
async def verify_start(request: Request, admin: AdminUser = Depends(require_admin)) -> Response:
    """Begin Discord verification of this bot's publisher identity (operator only)."""
    _operator(admin)
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        if not ident.registry_token:
            raise HTTPException(status_code=400, detail="claim a publisher handle first")
    state = secrets.token_urlsafe(16)
    redirect_uri = _origin(request) + "/api/marketplace/verify/callback"
    params = {
        "client_id": await runtime_config.discord_client_id(),
        "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "identify", "state": state,
    }
    resp = RedirectResponse(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))
    resp.set_cookie(
        VERIFY_STATE_COOKIE, (await _get_state_serializer()).dumps(state),
        max_age=600, httponly=True, samesite="lax", secure=_is_secure(request),
    )
    return resp


@router.get("/verify/callback")
async def verify_callback(request: Request, code: str | None = None, state: str | None = None) -> Response:
    """Discord redirect target: exchange the code and forward the token to the registry.
    Validated by the state cookie set in /verify/start (which is operator-gated)."""
    origin = _origin(request)
    fail = RedirectResponse(origin + "/?verify=failed")
    fail.delete_cookie(VERIFY_STATE_COOKIE)
    if not code or not state:
        return fail
    try:
        expected = (await _get_state_serializer()).loads(request.cookies.get(VERIFY_STATE_COOKIE, ""), max_age=600)
    except Exception:  # noqa: BLE001
        return fail
    if expected != state:
        return fail
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            tok = await client.post(TOKEN_URL, data={
                "client_id": await runtime_config.discord_client_id(),
                "client_secret": await runtime_config.discord_client_secret(),
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": origin + "/api/marketplace/verify/callback",
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
            if tok.status_code != 200:
                return fail
            access_token = tok.json()["access_token"]
    except Exception:  # noqa: BLE001
        return fail
    ok = await complete_discord_verification(access_token)
    resp = RedirectResponse(origin + ("/?verified=1" if ok else "/?verify=failed"))
    resp.delete_cookie(VERIFY_STATE_COOKIE)
    return resp
