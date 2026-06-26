"""Built-in SDK extensions, shipped as precompiled JS and seeded into the catalog.

These replace the former Python built-ins (dice/calculator/concise_mode/welcome):
each is authored against the Olisar SDK, runs in the sandbox like any user extension,
and is recorded as an ``ExtensionPackage`` with ``kind="builtin"`` (read-only in the
editor, no "Custom" badge). ``seed`` is idempotent and refreshes a row when the
bundled source changes, so updates ship with the app.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from olisar import sandbox
from olisar.db.models import (
    ExtensionKV,
    ExtensionPackage,
    ExtensionState,
    ExtensionVersion,
    utcnow,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("olisar.extensions.sdk_builtins")

_DIR = Path(__file__).parent
# The built-in SDK extensions shipped with the app. Order is irrelevant; listed for
# readability. Dropping one from this list (and deleting its .js) removes it everywhere:
# ``seed`` prunes built-in rows that are no longer shipped.
_FILES = ("welcome.js", "star_citizen.js")


def _ver_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted version into a comparable tuple (non-numeric parts -> 0)."""
    out: list[int] = []
    for part in str(v or "0").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


async def seed(session: "AsyncSession") -> None:
    """Insert/refresh the built-in extension packages. Idempotent.

    A built-in whose source can't be read or parsed — e.g. a packaging gap where the
    bundled ``.js`` is missing — is logged and skipped rather than aborting startup. A
    missing built-in must never take the bot, API, and dashboard down with it.

    Built-ins dropped from ``_FILES`` are pruned from the catalog (see ``_prune_removed``).
    """
    shipped: set[str] = set()
    complete = True  # did we enumerate every shipped built-in this run? (gates the prune)
    for fname in _FILES:
        try:
            src = (_DIR / fname).read_text(encoding="utf-8")
            manifest = await sandbox.extract_manifest(src)
        except Exception:
            log.exception("could not load built-in extension %s; skipping", fname)
            complete = False
            continue
        key = manifest.get("id")
        if not key:
            log.error("built-in %s has no id; skipping", fname)
            complete = False
            continue
        shipped.add(key)
        shipped_version = manifest.get("version", "1.0.0")
        row = await session.get(ExtensionPackage, key)
        if row is not None:
            if row.compiled_js == src:
                continue  # already current
            # Preserve operator edits to a built-in — UNLESS we ship a newer version, in
            # which case the shipped update wins (e.g. a built-in re-architected, like
            # Welcome moving from a Python cog to an SDK event handler). A same-version
            # edit is still preserved.
            if row.user_modified and _ver_tuple(row.version) >= _ver_tuple(shipped_version):
                continue
            if row.user_modified:
                log.info("built-in %s: shipped v%s supersedes an edited v%s", key, shipped_version, row.version)
                row.user_modified = False
        if row is None:
            row = ExtensionPackage(key=key)
            session.add(row)
        row.name = manifest.get("name", key)
        row.version = shipped_version
        row.kind = "builtin"
        row.category = manifest.get("category", "General")
        row.description = manifest.get("description", "")
        row.manifest = manifest
        row.source_ts = src
        row.compiled_js = src
        row.permissions = manifest.get("permissions", [])
        row.updated_at = utcnow()
        log.info("seeded built-in extension %s v%s", key, row.version)

    await _prune_removed(session, shipped, complete)


async def _prune_removed(session: "AsyncSession", shipped: set[str], complete: bool) -> None:
    """Delete built-in extensions no longer shipped — and their per-guild enable state, KV,
    and version history — so removing a built-in from ``_FILES`` fully removes it.

    Only runs when we successfully enumerated *every* shipped built-in this boot: a missing
    bundled ``.js`` (a packaging gap) must never look like a removed built-in and wrongly
    delete a still-shipped extension's data.
    """
    if not complete:
        return
    existing = (
        await session.scalars(
            select(ExtensionPackage.key).where(ExtensionPackage.kind == "builtin")
        )
    ).all()
    stale = [k for k in existing if k not in shipped]
    if not stale:
        return
    await session.execute(sa_delete(ExtensionState).where(ExtensionState.key.in_(stale)))
    await session.execute(sa_delete(ExtensionKV).where(ExtensionKV.ext_key.in_(stale)))
    await session.execute(sa_delete(ExtensionVersion).where(ExtensionVersion.key.in_(stale)))
    await session.execute(sa_delete(ExtensionPackage).where(ExtensionPackage.key.in_(stale)))
    log.info("pruned removed built-in extensions: %s", ", ".join(sorted(stale)))
