"""Bot-profile registry: the list of independent bots this install can run, and which
one is active.

A **profile** is one bot instance — its own Discord token, config, secrets, and SQLite
database. v1 runs one active *local* bot at a time; server-hosted profiles run on their own
VMs. This module is the tiny top-level store that tracks the set of profiles and the active
one, kept *outside* every per-profile DB so it is readable before any engine exists and
during a switch when the active engine is disposed.

Deliberately stdlib-only (no ``olisar.config`` import, like :mod:`olisar.runtime.paths`) so
it is safe to call at any point in the boot/switch sequence.

Storage: a JSON file at ``data_dir()/profiles.json``::

    { "active": "default", "profiles": [ {id, name, created_at, created, legacy}, ... ] }

- ``default`` is bound to the legacy ``data_dir()/olisar.db`` (``legacy: true``) so existing
  installs upgrade with **zero file movement** — the file (and its live WAL/SHM sidecars)
  is never touched, and stored ``kb_uploads`` paths stay valid. Additional profiles live at
  ``data_dir()/profiles/<id>/olisar.db``.
- ``created`` marks whether a profile's DB has had its schema built + been seeded, so a
  switch into a brand-new profile knows to initialise it.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from olisar.runtime.paths import data_dir

DEFAULT_ID = "default"


def _registry_path() -> Path:
    return data_dir() / "profiles.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _synthesize_default() -> dict:
    """The registry for a first boot / upgrade: a single ``default`` profile bound to the
    legacy ``data_dir()/olisar.db`` path. Marked ``created`` (boot builds/seeds its schema
    unconditionally), so switching is never needed to reach it."""
    return {
        "active": DEFAULT_ID,
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
    """Atomic write: tmp file + ``os.replace`` (atomic on POSIX and Windows)."""
    path = _registry_path()
    tmp = path.with_suffix(".json.tmp")
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


def db_path_for(profile_id: str) -> Path:
    """The SQLite path for a profile. The legacy ``default`` keeps ``data_dir()/olisar.db``;
    every other profile gets its own ``data_dir()/profiles/<id>/olisar.db`` (parent created
    on demand). Unknown ids fall back to the legacy path so the engine never blows up."""
    p = get(profile_id)
    if p is None or p.get("legacy"):
        return data_dir() / "olisar.db"
    d = data_dir() / "profiles" / profile_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "olisar.db"


def create(name: str) -> dict:
    """Register a new (unconfigured, uncreated) profile. Does NOT build its DB or switch —
    the switch orchestrator lazily builds the schema on first switch-in."""
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
    _write(reg)
    # Reclaim the DB dir — but never the shared legacy olisar.db (+ its sidecars).
    if not target.get("legacy"):
        shutil.rmtree(data_dir() / "profiles" / profile_id, ignore_errors=True)
