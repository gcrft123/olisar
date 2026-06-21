"""The ``.olx`` extension bundle format — the portable unit for sharing extensions.

An ``.olx`` file is a small JSON document carrying an extension's **source** (not its
compiled JS), its declared permissions, author metadata, and an integrity hash. It's the
artifact behind file-based import/export now, and the same artifact a marketplace would
serve later.

Design rules that keep importing a stranger's bundle safe:

* **Source only, never executable JS.** The importer always re-transpiles the source with
  the local toolchain (``olisar.sandbox.transpile``) and re-derives the manifest by running
  it in the sandbox. A bundle cannot smuggle in JS that differs from its readable source.
* **Declared permissions are a *request*, not a grant.** They're shown to the installer for
  consent; what the runtime enforces is the manifest the re-run code actually declares.
* **Content hash binds identity + source + permissions.** On import we recompute it and
  refuse a tampered file. ``signature`` (reserved) will sign this same hash.

This module is intentionally pure — no DB, no transpile, no clock — so it's trivial to
test. The API layer composes it with transpile + manifest extraction + persistence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from olisar.sandbox.transpile import SDK_VERSION

# Bump when the bundle envelope changes incompatibly. A bundle whose olx_version exceeds
# this can't be imported by an older runtime.
OLX_VERSION = 1

# Extension ids: lowercase, start with a letter, letters/digits/underscores, <= 64 chars.
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class BundleError(Exception):
    """A bundle is malformed, tampered with, or built for an incompatible version."""


def canonical_hash(ext_id: str, version: str, source: str, permissions: list[str]) -> str:
    """A stable ``sha256:<hex>`` over the integrity-relevant fields. Order-independent for
    permissions; whitespace-significant for source (it's code)."""
    payload = json.dumps(
        {
            "id": ext_id,
            "version": version,
            "permissions": sorted(str(p) for p in (permissions or [])),
            "source": source,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ParsedBundle:
    """A validated bundle. ``content_hash`` is recomputed locally (authoritative);
    ``declared_hash`` is whatever the file claimed (None if it omitted one)."""

    id: str
    name: str
    version: str
    category: str
    description: str
    permissions: list[str]  # declared/requested — shown to the installer for consent
    source: str
    sdk_version: str
    author_id: int | None
    author_name: str | None
    content_hash: str
    declared_hash: str | None

    @property
    def hash_ok(self) -> bool:
        return self.declared_hash is None or self.declared_hash == self.content_hash


def build_bundle(
    *,
    ext_id: str,
    name: str,
    version: str,
    category: str,
    description: str,
    source: str,
    permissions: list[str],
    sdk_version: str | None = None,
    author_id: int | None = None,
    author_name: str | None = None,
) -> dict:
    """Assemble an ``.olx`` document (a plain dict; the caller serialises it)."""
    perms = [str(p) for p in (permissions or [])]
    return {
        "olx_version": OLX_VERSION,
        "sdk_version": sdk_version or SDK_VERSION,
        "id": ext_id,
        "name": name,
        "version": version,
        "category": category,
        "description": description,
        "permissions": perms,
        "author": {"id": author_id, "name": author_name},
        "source": source,
        "content_hash": canonical_hash(ext_id, version, source, perms),
    }


def parse_bundle(data: dict) -> ParsedBundle:
    """Validate an ``.olx`` document and recompute its integrity hash. Raises
    ``BundleError`` on a malformed/incompatible/tampered bundle. Does NOT transpile or
    run the code — the caller does that next."""
    if not isinstance(data, dict):
        raise BundleError("not a valid .olx file (expected a JSON object)")

    olx_version = data.get("olx_version")
    if not isinstance(olx_version, int):
        raise BundleError("missing or invalid olx_version")
    if olx_version > OLX_VERSION:
        raise BundleError(
            f"this bundle was made with a newer format (olx_version {olx_version}); "
            "update Olisar to import it"
        )

    ext_id = data.get("id")
    if not isinstance(ext_id, str) or not KEY_RE.match(ext_id):
        raise BundleError("invalid extension id (lowercase letters/digits/underscores, start with a letter)")

    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise BundleError("bundle has no source")

    version = str(data.get("version") or "1.0.0")
    perms = data.get("permissions") or []
    if not isinstance(perms, list):
        raise BundleError("permissions must be a list")
    perms = [str(p) for p in perms]

    sdk_version = str(data.get("sdk_version") or "1")
    if _ver_tuple(sdk_version) > _ver_tuple(SDK_VERSION):
        raise BundleError(
            f"this extension targets a newer SDK (v{sdk_version}); update Olisar to import it"
        )

    author = data.get("author") or {}
    author = author if isinstance(author, dict) else {}
    author_id = author.get("id")
    author_id = int(author_id) if isinstance(author_id, (int, str)) and str(author_id).isdigit() else None

    declared_hash = data.get("content_hash")
    declared_hash = declared_hash if isinstance(declared_hash, str) and declared_hash else None
    recomputed = canonical_hash(ext_id, version, source, perms)
    if declared_hash is not None and declared_hash != recomputed:
        raise BundleError("bundle integrity check failed — the file has been modified or is corrupt")

    return ParsedBundle(
        id=ext_id,
        name=str(data.get("name") or ext_id),
        version=version,
        category=str(data.get("category") or "General"),
        description=str(data.get("description") or ""),
        permissions=perms,
        source=source,
        sdk_version=sdk_version,
        author_id=author_id,
        author_name=(str(author.get("name")) if author.get("name") else None),
        content_hash=recomputed,
        declared_hash=declared_hash,
    )


def bundle_filename(ext_id: str, version: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", f"{ext_id}-{version}")
    return f"{safe}.olx"


def _ver_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for p in str(v).split("."):
        m = re.match(r"\d+", p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


__all__ = [
    "OLX_VERSION",
    "KEY_RE",
    "BundleError",
    "ParsedBundle",
    "canonical_hash",
    "build_bundle",
    "parse_bundle",
    "bundle_filename",
]
