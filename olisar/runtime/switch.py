"""Switching the active bot profile at runtime.

v1 runs one active local bot at a time. Switching means: stop the current bot, dispose its
engine, re-point the "current" database at the target profile, (re)build its schema if new,
drop the DB-backed caches, and start the target bot (unless it is server-hosted). The API
server itself stays up the whole time; the console re-fetches its status and re-renders.

Kept separate from :mod:`olisar.runtime.server` so it can be unit-tested and so the
``/api/bots`` router can import it without pulling in uvicorn. Heavy imports are deferred
into the functions.
"""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger("olisar.runtime.switch")

# Guards against a concurrent/double switch (operator double-click, two console tabs). A
# switch is a rare operator action, so a simple module flag + 409 is sufficient.
_switching = False


def is_switching() -> bool:
    return _switching


def point_settings_at(profile_id: str) -> None:
    """Re-point the process at a profile's database. The async engine resolves its path from
    the profiles registry (``current_db_path``); this also keeps ``settings.database_path`` in
    sync for the few places that read it directly (e.g. ``scripts.init_db`` mkdir/logging)."""
    from olisar.config import settings
    from olisar.runtime import profiles

    settings.database_path = str(profiles.db_path_for(profile_id))


async def _status() -> dict:
    """The freshly-active profile's `{configured, hosting_mode}` — read after caches are
    invalidated, so it reflects the new DB. Drives the console's re-route (wizard vs
    dashboard vs server control panel)."""
    from olisar import runtime_config

    return {
        "configured": await runtime_config.is_configured(),
        "hosting_mode": await runtime_config.hosting_mode(),
    }


def _invalidate_caches() -> None:
    """Drop every DB-backed in-process cache so reads hit the newly-active profile's DB."""
    from olisar import discord_app, runtime_config, runtime_keys

    runtime_config.invalidate()
    runtime_keys.invalidate()
    discord_app.invalidate()


async def _prepare(profile_id: str) -> None:
    """Re-point + rebuild schema/seed for ``profile_id`` and refresh caches. Shared by the
    forward switch and the compensating rollback."""
    from scripts import init_db

    from olisar.db import engine
    from olisar.runtime import profiles

    await engine.reset_engine()
    point_settings_at(profile_id)
    profiles.set_active(profile_id)
    _invalidate_caches()
    # Idempotent — also carries the ADD COLUMN migration, so switching into a profile made
    # by an older build self-upgrades it (mirrors what boot does).
    await init_db.create_schema()
    if not (profiles.get(profile_id) or {}).get("created"):
        await init_db.seed_defaults()
        await init_db.seed_builtins()
        profiles.mark_created(profile_id)


async def switch_profile(app, target_id: str) -> dict:
    """Stop the active bot, adopt ``target_id`` as the active profile, and start its bot
    (unless server-hosted). Returns the new profile's `{configured, hosting_mode}`.

    On any failure partway, compensates back to the previous profile and restarts it, so the
    operator is never left with a dead console."""
    global _switching
    from olisar import runtime_config
    from olisar.runtime import profiles, server

    if target_id == profiles.active_id():
        return await _status()
    if profiles.get(target_id) is None:
        raise ValueError(f"unknown bot: {target_id}")
    if _switching:
        raise RuntimeError("a bot switch is already in progress")

    _switching = True
    prev = profiles.active_id()
    try:
        await server.stop_all_supervisors(app)
        await _prepare(target_id)
        if await runtime_config.hosting_mode() == "local":
            await server.start_supervisor(app, target_id)  # server mode: no local bot
        return await _status()
    except Exception:
        log.exception("switch to profile %s failed; rolling back to %s", target_id, prev)
        with contextlib.suppress(Exception):
            await server.stop_all_supervisors(app)
            await _prepare(prev)
            if await runtime_config.hosting_mode() == "local":
                await server.start_supervisor(app, prev)
        raise
    finally:
        _switching = False
