"""Authoring API for SDK extensions — operator only.

Operators write TypeScript in the console; the editor transpiles it to JS and posts
both here. We re-derive the manifest by running the compiled JS in the sandbox
(``compile_check``), store the package, and refresh the live catalog so the new code
takes effect on the next reply (and slash commands re-sync). Per-guild *enable* stays
on the existing ``/api/extensions`` routes (any server admin); only allowlisted
operators can create/edit the code itself.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from api.auth.deps import require_admin
from api.schemas import (
    ExtensionAuthoringIn,
    ExtensionImportConfirmIn,
    ExtensionImportIn,
    ExtensionValidateIn,
)
from olisar import sandbox
from olisar.audit import record_audit
from olisar.db.engine import session_scope
from olisar.db.models import (
    AdminUser,
    ExtensionKV,
    ExtensionPackage,
    ExtensionState,
    ExtensionVersion,
    utcnow,
)
from olisar.extensions import bundle, signing, user_registry
from olisar.extensions.base import _REGISTRY  # built-in (Python) keys, reserved
from olisar.sandbox import transpile
from olisar.sandbox.transpile import SDK_VERSION

log = logging.getLogger("olisar.api.extensions")
router = APIRouter(prefix="/api/extensions/authoring", tags=["extensions-authoring"])


def _operator(admin: AdminUser) -> None:
    if not admin.is_allowlisted:
        raise HTTPException(status_code=403, detail="only the operator can author extensions")


def _source_of(body: ExtensionAuthoringIn | ExtensionValidateIn) -> str:
    """The author's source — the source of truth. ``compiled_js`` from the client is
    deliberately ignored; we transpile ourselves."""
    return ((getattr(body, "source_ts", "") or getattr(body, "source", None)) or "").strip()


def _resync_commands(request: Request) -> None:
    """Nudge the dynamic slash-command cog to rebuild (no-op if it isn't loaded)."""
    sup = getattr(request.app.state, "bot_supervisor", None)
    bot = getattr(sup, "bot", None) if sup is not None else None
    if bot is None:
        return
    cog = bot.get_cog("SdkCommands")
    if cog is not None and hasattr(cog, "request_resync"):
        try:
            cog.request_resync()
        except Exception:
            log.exception("slash-command resync nudge failed")


async def _build(source: str) -> tuple[str, dict]:
    """Transpile author source -> JS *server-side* (the source of truth), then derive the
    manifest by running that JS in the sandbox. Returns ``(compiled_js, manifest)``."""
    if not source:
        raise HTTPException(status_code=400, detail="no source provided")
    try:
        compiled_js = await transpile.transpile(source)
    except transpile.TranspileError as exc:
        raise HTTPException(status_code=400, detail=f"transpile error: {exc}") from exc
    try:
        manifest = await sandbox.extract_manifest(compiled_js)
    except sandbox.SandboxError as exc:
        raise HTTPException(status_code=400, detail=f"compile error: {exc}") from exc
    key = manifest.get("id")
    if not key or not bundle.KEY_RE.match(str(key)):
        raise HTTPException(
            status_code=400,
            detail="extension id must be lowercase letters/digits/underscores (start with a letter)",
        )
    return compiled_js, manifest


def _summary(pkg: ExtensionPackage) -> dict:
    return {
        "key": pkg.key, "name": pkg.name, "version": pkg.version, "kind": pkg.kind,
        "category": pkg.category, "description": pkg.description,
        "permissions": pkg.permissions or [],
        "requested_permissions": pkg.requested_permissions or [],
        "origin": pkg.origin or "local", "sdk_version": pkg.sdk_version or SDK_VERSION,
        "editable": pkg.kind == "user",
        "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
    }


@router.get("")
async def list_packages(admin: AdminUser = Depends(require_admin)):
    _operator(admin)
    async with session_scope() as session:
        rows = (await session.scalars(
            select(ExtensionPackage).order_by(ExtensionPackage.kind, ExtensionPackage.name)
        )).all()
    return [_summary(p) for p in rows]


@router.get("/sdk-types")
async def sdk_types(admin: AdminUser = Depends(require_admin)) -> dict:
    """The SDK .d.ts the editor loads for autocomplete."""
    _operator(admin)
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "olisar" / "sandbox" / "olisar-sdk.d.ts"
    return {"dts": path.read_text(encoding="utf-8")}


@router.get("/signing")
async def signing_identity(admin: AdminUser = Depends(require_admin)) -> dict:
    """This bot's publisher identity — the public key/fingerprint that signs exports, so
    the operator can share it. The private key is never returned. Created on first view."""
    _operator(admin)
    if not signing.available():
        return {"available": False}
    async with session_scope() as session:
        ident = await signing.ensure_identity(session)
        return {
            "available": True, "algo": ident.algo,
            "public_key": ident.public_key, "fingerprint": ident.fingerprint,
            "created_at": ident.created_at.isoformat() if ident.created_at else None,
        }


@router.get("/{key}")
async def get_package(key: str, admin: AdminUser = Depends(require_admin)):
    _operator(admin)
    async with session_scope() as session:
        pkg = await session.get(ExtensionPackage, key)
        if pkg is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        versions = (await session.scalars(
            select(ExtensionVersion).where(ExtensionVersion.key == key)
            .order_by(ExtensionVersion.saved_at.desc())
        )).all()
    return {
        **_summary(pkg),
        "source_ts": pkg.source_ts,
        "compiled_js": pkg.compiled_js,
        "manifest": pkg.manifest,
        "versions": [
            {"version": v.version, "saved_at": v.saved_at.isoformat() if v.saved_at else None}
            for v in versions
        ],
    }


@router.get("/{key}/export")
async def export_package(key: str, admin: AdminUser = Depends(require_admin)) -> dict:
    """Export a package as an ``.olx`` bundle (source-only; the importer re-transpiles).
    The client turns the returned JSON into a downloadable file."""
    _operator(admin)
    async with session_scope() as session:
        pkg = await session.get(ExtensionPackage, key)
        if pkg is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        if not (pkg.source_ts or "").strip():
            raise HTTPException(status_code=400, detail="this extension has no exportable source")
        doc = bundle.build_bundle(
            ext_id=pkg.key, name=pkg.name, version=pkg.version,
            category=pkg.category, description=pkg.description,
            source=pkg.source_ts or "",
            permissions=pkg.requested_permissions or pkg.permissions or [],
            sdk_version=pkg.sdk_version, author_id=pkg.author_id, author_name=pkg.publisher_name,
        )
        # Sign with this bot's publisher identity (created on first export) so importers
        # can verify authorship + integrity.
        if signing.available():
            ident = await signing.ensure_identity(session)
            signing.sign_bundle(doc, ident.private_key, ident.public_key)
        return doc


async def _prepare_import(bundle_data: dict) -> tuple[object, str, dict]:
    """Validate a bundle, then re-transpile its source and re-derive the manifest with the
    local toolchain — the manifest the re-run code declares is authoritative, never the
    file's metadata. Returns ``(parsed, compiled_js, manifest)``."""
    try:
        parsed = bundle.parse_bundle(bundle_data)
    except bundle.BundleError as exc:
        raise HTTPException(status_code=400, detail=f"invalid bundle: {exc}") from exc
    compiled_js, manifest = await _build(parsed.source)
    return parsed, compiled_js, manifest


@router.post("/import/preview")
async def import_preview(body: ExtensionImportIn, admin: AdminUser = Depends(require_admin)) -> dict:
    """Inspect an uploaded bundle without installing it: what it adds and, crucially, the
    capabilities it asks for — so the operator can review before granting."""
    _operator(admin)
    parsed, _, manifest = await _prepare_import(body.bundle)
    key = manifest["id"]
    sig_status, sig_fingerprint, _ = signing.verify_bundle(body.bundle, parsed.content_hash)
    async with session_scope() as session:
        exists = await session.get(ExtensionPackage, key) is not None
    return {
        "id": key,
        "name": manifest.get("name", key),
        "version": manifest.get("version", "1.0.0"),
        "category": manifest.get("category", "General"),
        "description": manifest.get("description", ""),
        "tools": [t.get("name") for t in manifest.get("tools", []) if t.get("name")],
        "commands": [c.get("name") for c in manifest.get("commands", []) if c.get("name")],
        # Authoritative ask, from re-running the code (the file's list is only a hint).
        "requested_permissions": manifest.get("permissions", []),
        "behavior": bool(manifest.get("system_note")),
        "author": {"id": parsed.author_id, "name": parsed.author_name},
        "sdk_version": parsed.sdk_version,
        "exists": exists,
        "is_builtin_key": key in _REGISTRY,
        # unsigned | valid | invalid — an invalid signature blocks install (likely tampered).
        "signature": {"status": sig_status, "fingerprint": sig_fingerprint},
    }


@router.post("/import")
async def import_package(
    body: ExtensionImportConfirmIn, request: Request, admin: AdminUser = Depends(require_admin)
) -> dict:
    """Install an uploaded bundle, granting only the capabilities the operator approved."""
    _operator(admin)
    parsed, compiled_js, manifest = await _prepare_import(body.bundle)
    key = manifest["id"]
    if key in _REGISTRY:
        raise HTTPException(status_code=409, detail=f"'{key}' is a built-in extension name")
    sig_status, _, sig_pub = signing.verify_bundle(body.bundle, parsed.content_hash)
    if sig_status == "invalid":
        raise HTTPException(
            status_code=400,
            detail="this bundle's signature is invalid — it may have been tampered with; not importing",
        )
    requested = manifest.get("permissions", [])
    granted = [p for p in (body.granted_permissions or []) if p in requested]  # granted ⊆ requested
    version = manifest.get("version", "1.0.0")
    async with session_scope() as session:
        if await session.get(ExtensionPackage, key) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"an extension named '{key}' already exists — delete it first to re-import",
            )
        pkg = ExtensionPackage(
            key=key, name=manifest.get("name", key), version=version, kind="user",
            category=manifest.get("category", "General"),
            description=manifest.get("description", ""), manifest=manifest,
            source_ts=parsed.source, compiled_js=compiled_js,
            permissions=granted, requested_permissions=requested,
            author_id=parsed.author_id, publisher_id=parsed.author_id,
            publisher_name=parsed.author_name, origin="imported",
            sdk_version=parsed.sdk_version or SDK_VERSION, content_hash=parsed.content_hash,
            signature=body.bundle.get("signature"), publisher_key=sig_pub,
            signature_verified=(sig_status == "valid"),
        )
        session.add(pkg)
        await record_audit(
            session, actor=admin.discord_user_id, action="import_extension",
            target_type="extension_package", target_id=key,
            after={
                "version": version, "granted": granted, "requested": requested,
                "origin": "imported", "signature": sig_status,
            },
        )
    user_registry.invalidate()
    _resync_commands(request)
    return {"ok": True, "key": key, "granted": granted}


@router.post("/validate")
async def validate(body: ExtensionValidateIn, admin: AdminUser = Depends(require_admin)):
    _operator(admin)
    _, manifest = await _build(_source_of(body))
    return {"ok": True, "manifest": manifest, "permissions": manifest.get("permissions", [])}


@router.post("")
async def create_package(
    body: ExtensionAuthoringIn, request: Request, admin: AdminUser = Depends(require_admin)
):
    _operator(admin)
    source = _source_of(body)
    compiled_js, manifest = await _build(source)
    key = manifest["id"]
    if key in _REGISTRY:
        raise HTTPException(status_code=409, detail=f"'{key}' is a built-in extension name")
    version = manifest.get("version", "1.0.0")
    perms = manifest.get("permissions", [])
    async with session_scope() as session:
        if await session.get(ExtensionPackage, key) is not None:
            raise HTTPException(status_code=409, detail=f"an extension named '{key}' already exists")
        pkg = ExtensionPackage(
            key=key, name=body.name or manifest.get("name", key),
            version=version, kind="user",
            category=manifest.get("category", "General"),
            description=manifest.get("description", ""), manifest=manifest,
            source_ts=source, compiled_js=compiled_js,
            # Operator authoring grants what it declares (you trust your own code).
            permissions=perms, requested_permissions=perms,
            author_id=admin.discord_user_id, publisher_id=admin.discord_user_id,
            origin="local", sdk_version=SDK_VERSION,
            content_hash=bundle.canonical_hash(key, version, source, perms),
        )
        session.add(pkg)
        await record_audit(
            session, actor=admin.discord_user_id, action="create_extension",
            target_type="extension_package", target_id=key,
            after={"name": pkg.name, "version": pkg.version, "permissions": pkg.permissions},
        )
    user_registry.invalidate()
    _resync_commands(request)
    return {"ok": True, "key": key}


@router.put("/{key}")
async def update_package(
    key: str, body: ExtensionAuthoringIn, request: Request,
    admin: AdminUser = Depends(require_admin),
):
    _operator(admin)
    source = _source_of(body)
    compiled_js, manifest = await _build(source)
    if manifest["id"] != key:
        raise HTTPException(status_code=400, detail="changing an extension's id isn't allowed")
    version = manifest.get("version", "1.0.0")
    perms = manifest.get("permissions", [])
    async with session_scope() as session:
        pkg = await session.get(ExtensionPackage, key)
        if pkg is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        # Built-ins are editable too; once edited, the seeder stops overwriting them.
        if pkg.kind == "builtin":
            pkg.user_modified = True
        # Snapshot the current version before overwriting.
        session.add(ExtensionVersion(
            key=key, version=pkg.version, source_ts=pkg.source_ts,
            compiled_js=pkg.compiled_js, manifest=pkg.manifest, saved_by=admin.discord_user_id,
        ))
        pkg.name = body.name or manifest.get("name", key)
        pkg.version = version
        pkg.category = manifest.get("category", "General")
        pkg.description = manifest.get("description", "")
        pkg.manifest = manifest
        pkg.source_ts = source
        pkg.compiled_js = compiled_js
        pkg.permissions = perms
        pkg.requested_permissions = perms
        pkg.sdk_version = SDK_VERSION
        pkg.content_hash = bundle.canonical_hash(key, version, source, perms)
        pkg.updated_at = utcnow()
        await record_audit(
            session, actor=admin.discord_user_id, action="update_extension",
            target_type="extension_package", target_id=key,
            after={"version": pkg.version, "permissions": pkg.permissions},
        )
    user_registry.invalidate()
    _resync_commands(request)
    return {"ok": True, "key": key}


@router.delete("/{key}")
async def delete_package(
    key: str, request: Request, admin: AdminUser = Depends(require_admin)
):
    _operator(admin)
    async with session_scope() as session:
        pkg = await session.get(ExtensionPackage, key)
        if pkg is None:
            raise HTTPException(status_code=404, detail="unknown extension")
        if pkg.kind != "user":
            raise HTTPException(status_code=403, detail="built-in extensions can't be deleted")
        await session.execute(sa_delete(ExtensionState).where(ExtensionState.key == key))
        await session.execute(sa_delete(ExtensionVersion).where(ExtensionVersion.key == key))
        await session.execute(sa_delete(ExtensionKV).where(ExtensionKV.ext_key == key))
        await session.delete(pkg)
        await record_audit(
            session, actor=admin.discord_user_id, action="delete_extension",
            target_type="extension_package", target_id=key, after={"deleted": True},
        )
    user_registry.invalidate()
    _resync_commands(request)
    return {"ok": True}
