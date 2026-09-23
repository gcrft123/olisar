"""Bot-profile registry: the list of independent bots this install runs, and which one the
console is showing.

A **profile** is one bot — its own Discord token, config, secrets, SQLite database, uploads and
Tailscale node. In the desktop app every profile runs at once, each in its own process (see
:mod:`olisar.runtime.gateway`); ``active`` is only which one the console window is looking at,
and switching it never stops a bot. Server-hosted profiles run on the operator's VM, and their
local process is just the control panel.

This module is the tiny store that tracks the set of profiles, kept *outside* every
per-profile directory so it is readable before any engine exists. Only the gateway writes it;
bot workers read it (for their own name) and never modify it.

Deliberately stdlib-only (no ``olisar.config`` import, like :mod:`olisar.runtime.paths`) so
it is safe to call at any point in the boot sequence.

Storage: a JSON file at ``home_dir()/profiles.json``::

    { "active": "default", "profiles": [ {id, name, created_at, created, legacy}, ... ] }

- ``default`` is bound to the legacy data dir itself (``home_dir()/olisar.db``,
  ``legacy: true``) so existing installs upgrade with **zero file movement** — the file (and
  its live WAL/SHM sidecars) is never touched, and stored ``kb_uploads`` paths stay valid.
  Additional profiles live at ``home_dir()/profiles/<id>/``.
- ``created`` marks whether a profile's DB has had its schema built + been seeded. Every boot
  builds the schema anyway (idempotently), so this is informational.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from olisar.runtime.paths import home_dir

DEFAULT_ID = "default"


def _registry_path() -> Path:
    return home_dir() / "profiles.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _synthesize_default() -> dict:
    """The registry for a first boot / upgrade: a single ``default`` profile bound to the
    legacy ``home_dir()/olisar.db`` path. Marked ``created`` (boot builds/seeds its schema
    unconditionally)."""
    return {
        "active": DEFAULT_ID,
        "default": DEFAULT_ID,
        "profiles": [
            {
                "id": DEFAULT_ID,
                "name": "Default",
                "created_at": _now_iso(),
                "created": True,
                "legacy": True,
            }
        ],
    }


def _normalise(reg: dict) -> dict:
    """Fill in any missing keys so older/hand-edited registries stay valid."""
    profiles = reg.get("profiles") or []
    for p in profiles:
        p.setdefault("name", p.get("id", "Bot"))
        p.setdefault("created_at", _now_iso())
        p.setdefault("created", True)
        p.setdefault("legacy", p.get("id") == DEFAULT_ID)
    ids = {p["id"] for p in profiles}
    if not profiles or reg.get("active") not in ids:
        reg["active"] = profiles[0]["id"] if profiles else DEFAULT_ID
    # `default` = the bot the app opens on launch. Fall back to the active profile if unset
    # or pointing at a since-deleted bot.
    if reg.get("default") not in ids:
        reg["default"] = reg["active"]
    reg["profiles"] = profiles
    return reg


def _read() -> dict:
    path = _registry_path()
    if not path.exists():
        reg = _synthesize_default()
        _write(reg)
        return reg
    try:
        reg = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        reg = _synthesize_default()
        _write(reg)
        return reg
    return _normalise(reg)


def _write(reg: dict) -> None:
    """Atomic write: tmp file + ``os.replace`` (atomic on POSIX and Windows). The tmp name
    carries the pid, so a worker that synthesizes the file at the same moment the gateway
    writes it can't interleave into one half-written tmp file."""
    path = _registry_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(reg, indent=2), "utf-8")
    os.replace(tmp, path)


# ── public API ────────────────────────────────────────────────────────────────


def list() -> list[dict]:  # noqa: A001 - deliberate registry verb, matches call sites
    return _read()["profiles"]


def get(profile_id: str) -> dict | None:
    return next((p for p in _read()["profiles"] if p["id"] == profile_id), None)


def active_id() -> str:
    return _read()["active"]


def active() -> dict:
    reg = _read()
    aid = reg["active"]
    return next(p for p in reg["profiles"] if p["id"] == aid)


def default_id() -> str:
    """The profile the app opens on launch."""
    return _read()["default"]


def set_default(profile_id: str) -> None:
    """Pin which bot the app opens on launch. Independent of the currently-active bot —
    you can run another bot this session without changing the launch default."""
    reg = _read()
    if not any(p["id"] == profile_id for p in reg["profiles"]):
        raise ValueError(f"unknown profile: {profile_id}")
    reg["default"] = profile_id
    _write(reg)


def is_legacy(profile_id: str) -> bool:
    p = get(profile_id)
    return bool(p and p.get("legacy"))


def data_dir_for(profile_id: str) -> Path:
    """A profile's own directory: its DB, uploads, Tailscale node and ``state.json``. The
    legacy ``default`` is the install dir itself (where those have always lived); every other
    profile gets ``home_dir()/profiles/<id>/`` (created on demand). Raises for an unknown id —
    handing a deleted bot somebody else's directory is the one mistake this must not make."""
    p = get(profile_id)
    if p is None:
        raise KeyError(f"unknown profile: {profile_id}")
    if p.get("legacy"):
        return home_dir()
    d = home_dir() / "profiles" / profile_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path_for(profile_id: str) -> Path:
    """The SQLite path for a profile (``data_dir_for(id)/olisar.db``). Unknown ids fall back
    to the legacy path so the engine of a single-bot run never blows up."""
    try:
        return data_dir_for(profile_id) / "olisar.db"
    except KeyError:
        return home_dir() / "olisar.db"


def create(name: str) -> dict:
    """Register a new (unconfigured, uncreated) profile. Does NOT build its DB or select it —
    its process builds the schema when it first boots."""
    reg = _read()
    pid = secrets.token_hex(4)
    while any(p["id"] == pid for p in reg["profiles"]):
        pid = secrets.token_hex(4)
    profile = {
        "id": pid,
        "name": (name or "New bot").strip()[:60] or "New bot",
        "created_at": _now_iso(),
        "created": False,
        "legacy": False,
    }
    reg["profiles"].append(profile)
    _write(reg)
    return profile


def mark_created(profile_id: str) -> None:
    reg = _read()
    for p in reg["profiles"]:
        if p["id"] == profile_id:
            p["created"] = True
    _write(reg)


def set_active(profile_id: str) -> None:
    """Point the console at ``profile_id``. Only changes what the console shows."""
    reg = _read()
    if not any(p["id"] == profile_id for p in reg["profiles"]):
        raise ValueError(f"unknown profile: {profile_id}")
    reg["active"] = profile_id
    _write(reg)


def rename(profile_id: str, name: str) -> None:
    reg = _read()
    for p in reg["profiles"]:
        if p["id"] == profile_id:
            p["name"] = (name or p["name"]).strip()[:60] or p["name"]
    _write(reg)


def delete(profile_id: str) -> None:
    """Remove a profile and (for non-legacy profiles) its DB directory. Refuses to delete
    the active profile or the last remaining one."""
    reg = _read()
    if profile_id == reg["active"]:
        raise ValueError("switch to another bot before deleting this one")
    if len(reg["profiles"]) <= 1:
        raise ValueError("cannot delete the only bot")
    target = next((p for p in reg["profiles"] if p["id"] == profile_id), None)
    if target is None:
        raise ValueError(f"unknown profile: {profile_id}")
    reg["profiles"] = [p for p in reg["profiles"] if p["id"] != profile_id]
    # If the launch default was this bot, fall back to the active one (always valid — you
    # can't delete the active bot).
    if reg.get("default") == profile_id:
        reg["default"] = reg["active"]
    _write(reg)
    # Reclaim the DB dir — but never the shared legacy olisar.db (+ its sidecars).
    if not target.get("legacy"):
        shutil.rmtree(home_dir() / "profiles" / profile_id, ignore_errors=True)
