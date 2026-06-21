"""Live catalog of SDK extensions (operator-authored + seeded built-ins).

Reads ``ExtensionPackage`` rows and builds runtime ``Extension`` objects, cached in
process with a short TTL (mirrors ``olisar.runtime_keys``) so the synchronous catalog
helpers in ``base`` can union it and an authoring save is picked up live — the API
calls ``invalidate()`` after every mutation. ``cached()`` returns the last snapshot
without a DB round-trip for the rare sync caller.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from sqlalchemy import select

from olisar.db.models import ExtensionPackage
from olisar.extensions import sdk_loader

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from olisar.extensions.base import Extension

log = logging.getLogger("olisar.extensions.user_registry")

_CACHE_TTL = 5.0
_cache: dict[str, "Extension"] | None = None
_cache_at = 0.0


def cached() -> dict[str, "Extension"]:
    """The last-loaded snapshot (possibly empty if never loaded). Sync."""
    return _cache or {}


async def load(session: "AsyncSession") -> dict[str, "Extension"]:
    """Build (or return cached) Extensions for every stored package."""
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < _CACHE_TTL:
        return _cache
    out: dict[str, "Extension"] = {}
    rows = (await session.scalars(select(ExtensionPackage))).all()
    for pkg in rows:
        try:
            out[pkg.key] = sdk_loader.build_extension(pkg)
        except Exception:
            log.exception("failed to build extension %s", pkg.key)
    _cache, _cache_at = out, now
    return _cache


def invalidate() -> None:
    """Drop the cache so the next load re-queries the DB (call after a save)."""
    global _cache
    _cache = None
