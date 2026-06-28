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
    MarketplacePolicyIn,
    MarketplacePublishIn,
    MarketplaceRefIn,
    MarketplaceRegisterIn,
    MarketplaceReportIn,
    MarketplaceUpdateApplyIn,
    MarketplaceUpdateIn,
    MarketplaceYankIn,
)
from olisar.audit import record_audit
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, ExtensionPackage, utcnow
from olisar.extensions import signing, user_registry
from olisar.extensions.review import review_source

log = logging.getLogger("olisar.api.marketplace")
router = APIRouter(prefix="/api/marketplace", tags=["marketplace"])

_TIMEOUT = 15.0
_NS_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
VERIFY_STATE_COOKIE = "olisar_verify_state"


def _registry_base() -> str:
    return (settings.registry_url or "").rstrip("/")


async def _registry_get(
    path: str, params: dict | None = None, token: str | None = None
) -> httpx.Response:
    base = _registry_base()
    if not base:
        raise HTTPException(status_code=503, detail="no marketplace registry is configured")
    headers = {"authorization": f"Bearer {token}"} if token else None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            return await client.get(base + path, params=params, headers=headers)
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
    if r.status_code == 401:
        # A 401 here means the registry rejected this bot's *publisher token* — NOT the
        # operator's console session. We must not return 401: the dashboard treats any 401
        # as a sign-in failure and bounces to the login screen (which can't fix a stale
        # publisher token). Surface it as an actionable publisher-identity error instead.
        return HTTPException(
            status_code=409,
            detail="the marketplace no longer recognises this bot's publisher token — "
            "re-register your publisher handle (Marketplace → Publishing as → re-register) "
            f"and try again. ({detail})",
        )
    status = r.status_code if r.status_code in (400, 403, 409, 413, 429, 507) else 502
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
    # Reuse the publish-time risk audit stamped on the listing instead of re-running the
    # (slow) AI review on every preview — the score is advisory (shown for consent, not a
    # hard install gate). Falls back to a live review only for listings with no stored score.
    prestored = None
    if "risk_score" in doc:
        prestored = {
            "score": int(doc.get("risk_score") or 0),
            "summary": "",
            "bullets": list(doc.get("risk_report") or []),
            "ok": True,
        }
    preview = await preview_bundle(doc, prestored_risk=prestored)
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
    try:  # best-effort install counter; never fail the install over it
        await _registry_post("/v1/install", {"namespace": body.namespace, "name": body.name})
    except HTTPException:
        pass
    return result


# ── updates & revocation ────────────────────────────────────────────────────
async def _detach_from_marketplace(keys: list[str], actor: int | None) -> None:
    """A yanked/removed marketplace extension reverts to a plain *local* extension: it keeps
    working and its granted capabilities, but sheds its 'Marketplace' provenance so it stops
    advertising a marketplace link it no longer has — and becomes publishable again, so the
    operator can re-list it under their own handle."""
    changed = False
    async with session_scope() as session:
        for key in keys:
            pkg = await session.get(ExtensionPackage, key)
            if pkg is None or pkg.origin != "marketplace":
                continue
            pkg.origin = "local"
            pkg.marketplace_ref = None
            changed = True
            await record_audit(
                session, actor=actor, action="detach_extension",
                target_type="extension_package", target_id=key,
                after={"origin": "local", "reason": "yanked from marketplace"},
            )
    if changed:
        user_registry.invalidate()


@router.get("/installed")
async def installed(admin: AdminUser = Depends(require_admin)) -> dict:
    """For every marketplace-installed extension, report whether a newer version is
    available or it's been yanked — so the catalog can surface Update / Removed. An
    extension that's been yanked (or fully removed) is detached back to a local extension
    so it loses the Marketplace label and can be re-published."""
    _operator(admin)
    async with session_scope() as session:
        rows = [
            (p.key, p.version, p.marketplace_ref)
            for p in (await session.scalars(
                select(ExtensionPackage).where(ExtensionPackage.origin == "marketplace")
            )).all()
        ]
    out: dict = {}
    detach: list[str] = []
    for key, ver, refj in rows:
        if not refj:
            continue
        ref = json.loads(refj)
        try:
            r = await _registry_get(f"/v1/ext/{ref['namespace']}/{ref['name']}")
        except HTTPException:
            continue
        if r.status_code == 404:
            out[key] = {"installed_version": ver, "yanked": True, "gone": True, "detached": True}
            detach.append(key)
            continue
        if r.status_code != 200:
            continue
        d = r.json()
        latest = d.get("version")
        versions = d.get("versions") or []
        latest_yanked = any(v.get("version") == latest and v.get("yanked") for v in versions)
        yanked = d.get("status") == "yanked" or latest_yanked
        entry = {
            "installed_version": ver,
            "latest_version": latest,
            "update_available": bool(latest and latest != ver and not yanked),
            "yanked": yanked,
        }
        if yanked:
            entry["detached"] = True  # reverting to local (below); UI should reload the catalog
            detach.append(key)
        out[key] = entry
    if detach:
        await _detach_from_marketplace(detach, admin.discord_user_id)
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


@router.get("/published")
async def published(admin: AdminUser = Depends(require_admin)) -> dict:
    """For every locally-authored extension, report whether it's published under this bot's
    handle and whether the local source has diverged from what's live — so the catalog can
    offer a "Push update". Keyed by extension key; only includes ones we've published."""
    _operator(admin)
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        handle = (ident.registry_handle or "").lower()
        registered = bool(ident.registry_token and handle)
        rows = [
            (p.key, p.version, p.content_hash)
            for p in (await session.scalars(
                select(ExtensionPackage).where(
                    ExtensionPackage.kind == "user", ExtensionPackage.origin == "local"
                )
            )).all()
        ]
    out: dict = {}
    if not registered:
        return out
    for key, ver, chash in rows:
        try:
            r = await _registry_get(f"/v1/ext/{handle}/{key}")
        except HTTPException:
            continue
        if r.status_code != 200:  # 404 → not published under our handle; skip
            continue
        d = r.json()
        if (d.get("publisher") or "").lower() != handle:  # someone else owns that name
            continue
        versions = d.get("versions") or []
        same = next((v for v in versions if v.get("version") == ver), None)
        # No matching version on the registry → there's a new (bumped) version to push.
        # Same version present → compare content to see if the local source diverged.
        has_changes = same is None or bool(
            chash and same.get("content_hash") and chash != same.get("content_hash")
        )
        out[key] = {
            "namespace": handle,
            "published_version": d.get("version"),
            "local_version": ver,
            "version_is_new": same is None,
            "has_changes": has_changes,
            "verified": bool(d.get("publisher_verified")),
        }
    return out


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


async def _reregister_token(admin: AdminUser) -> str | None:
    """Mint a fresh publisher token by re-registering this bot's handle with its own key.
    The registry rotates the token on every register, so a token can silently go stale if
    the same handle+key was re-registered from somewhere else (e.g. a publish script). The
    key never changes, so re-registering keeps the namespace and the verified badge; it just
    refreshes the token. Returns the new token, or None if we can't recover it."""
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        if not ident.registry_handle:
            return None
        r = await _registry_post("/v1/publishers/register", {
            "public_key": ident.public_key, "handle": ident.registry_handle,
            "discord_id": str(admin.discord_user_id),
        })
        if r.status_code != 200:
            return None
        data = r.json()
        ident.registry_token = data["token"]
        ident.registry_handle = data["handle"]
        ident.registered_at = utcnow()
        return data["token"]


async def _record_blocked(handle: str | None, key: str, version: str | None,
                          discord_id: int, score: int, threshold: int, bullets: list) -> None:
    """Best-effort: log a blocked publish to the registry for the developer console."""
    try:
        await _registry_post("/v1/blocked", {
            "namespace": handle or None, "name": key, "version": version,
            "reporter_discord_id": str(discord_id), "risk_score": score,
            "threshold": threshold, "bullets": bullets or [],
        })
    except HTTPException:
        pass


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
        handle = ident.registry_handle
        version = pkg.version
        manifest = pkg.manifest or {}
        requested = pkg.requested_permissions or pkg.permissions or []
    # AI risk review (operator's own Gemini). Block at/above the operator's threshold.
    review = await review_source(pkg.source_ts or "", manifest, requested_permissions=requested)
    if not review.get("ok"):
        # Fail CLOSED: no score means we can't vouch for this source, so we don't ship it.
        # Failing open here was a loophole — a publisher could exhaust their AI quota (or trip
        # a transient error) to push unscored code to everyone. Block until a review can run.
        raise HTTPException(status_code=400, detail={
            "code": "review_unavailable",
            "message": (
                "Couldn't complete the security review — the AI review is unavailable (for "
                "example your Gemini quota may be exhausted). Publishing is blocked until a "
                "review can run; try again later."
            ),
        })
    threshold = await runtime_config.extension_risk_threshold()
    if review["score"] >= threshold:
        await _record_blocked(handle, body.key, version, admin.discord_user_id,
                              review["score"], threshold, review.get("bullets") or [])
        # Structured so the console can render the risk readout (meter + reasons).
        raise HTTPException(status_code=400, detail={
            "code": "risk_blocked",
            "message": f"Publishing blocked — risk {review['score']}/100 (threshold {threshold}).",
            "risk_score": review["score"],
            "threshold": threshold,
            "summary": review.get("summary") or "",
            "bullets": review.get("bullets") or [],
        })
    # Unsigned metadata (not part of the canonical source hash) so the signature holds.
    doc["risk_score"] = review["score"]
    doc["risk_report"] = review.get("bullets") or []
    r = await _registry_post("/v1/publish", {"bundle": doc}, token=token)
    if r.status_code == 401:  # token rotated out from under us — refresh once and retry
        fresh = await _reregister_token(admin)
        if fresh:
            r = await _registry_post("/v1/publish", {"bundle": doc}, token=fresh)
    if r.status_code != 200:
        raise _registry_error(r, "publish failed")
    return r.json()


@router.post("/review")
async def review(body: MarketplacePublishIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Run the AI risk review on a local extension WITHOUT publishing — powers the console's
    scan screen so the operator sees the verdict (and can confirm) before shipping."""
    _operator(admin)
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        pkg = await session.get(ExtensionPackage, body.key)
        if pkg is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        if not (pkg.source_ts or "").strip():
            raise HTTPException(status_code=400, detail="this extension has no source to publish")
        manifest = pkg.manifest or {}
        requested = pkg.requested_permissions or pkg.permissions or []
        source = pkg.source_ts or ""
        handle = ident.registry_handle
        version = pkg.version
    result = await review_source(source, manifest, requested_permissions=requested)
    threshold = await runtime_config.extension_risk_threshold()
    blocked = bool(result.get("ok") and result["score"] >= threshold)
    if blocked:  # the user tried to publish and was stopped — log it for the dev console
        await _record_blocked(handle, body.key, version, admin.discord_user_id,
                              result["score"], threshold, result.get("bullets") or [])
    return {
        "review_available": bool(result.get("ok")),
        "risk_score": result.get("score", 0),
        "threshold": threshold,
        "summary": result.get("summary", ""),
        "bullets": result.get("bullets", []),
        "blocked": blocked,
    }


@router.get("/policy")
async def get_policy(admin: AdminUser = Depends(require_admin)) -> dict:
    """Marketplace publishing policy — currently the AI risk-score block threshold."""
    _operator(admin)
    return {"risk_threshold": await runtime_config.extension_risk_threshold()}


@router.put("/policy")
async def put_policy(body: MarketplacePolicyIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Set the risk-score threshold (1-100) at/above which publishing is blocked."""
    _operator(admin)
    value = max(1, min(int(body.risk_threshold), 100))
    await runtime_config.save(extension_risk_threshold=value)
    return {"ok": True, "risk_threshold": value}


@router.post("/report")
async def report(body: MarketplaceReportIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """File an abuse report against a marketplace extension. The registry stores it, emails
    the platform owner (with the publisher's Discord id to warn/ban), and surfaces it in the
    developer console. The reporter is this operator's Discord id."""
    _operator(admin)
    payload = {
        "namespace": body.namespace,
        "name": body.name,
        "version": body.version,
        "reporter_discord_id": str(admin.discord_user_id),
        "description": body.description,
        "logs": body.logs,
        "attachments": [a.model_dump() for a in body.attachments],
    }
    r = await _registry_post("/v1/report", payload)
    if r.status_code != 200:
        raise _registry_error(r, "couldn't file the report")
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
    if r.status_code == 401:  # token rotated out from under us — refresh once and retry
        fresh = await _reregister_token(admin)
        if fresh:
            r = await _registry_post("/v1/yank", {"name": body.name, "version": body.version}, token=fresh)
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
