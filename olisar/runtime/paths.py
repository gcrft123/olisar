"""Per-user data directory resolution for the packaged desktop app.

Kept dependency-light (stdlib + platformdirs only) and deliberately free of any
``olisar.config`` import: the runtime entry point calls ``bootstrap_env()`` to set
``DATABASE_PATH`` BEFORE ``olisar.config`` (an lru_cached singleton) is first loaded,
so importing this module must not drag config in early.

Resolution order for the writable base dir:
  1. ``OLISAR_DATA_DIR`` env (set by Electron, or by a test/CLI run)
  2. the OS per-user data dir when frozen (macOS ``~/Library/Application Support/Olisar``,
     Windows ``%APPDATA%\\Olisar``) — a packaged binary must not write next to itself
  3. the repo's ``data/`` in development (unchanged from today)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

_APP_NAME = "Olisar"


def _repo_root() -> Path:
    # olisar/runtime/paths.py -> repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    """The writable base dir for the DB (+ WAL/SHM) and uploads. Created on demand."""
    override = os.environ.get("OLISAR_DATA_DIR")
    if override:
        base = Path(override)
    elif is_frozen():
        base = Path(platformdirs.user_data_dir(_APP_NAME, _APP_NAME))
    else:
        base = _repo_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "olisar.db"


def kb_uploads_dir() -> Path:
    """Where uploaded knowledge-base documents are stored (callers mkdir on write)."""
    return data_dir() / "kb_uploads"


def tailscale_state_dir() -> Path:
    """Persisted Tailscale node identity for the Funnel sidecar, so the public URL is
    stable across launches (callers mkdir on use)."""
    return data_dir() / "tailscale"


def web_dist_dir() -> Path:
    """The built dashboard (``web/dist``). In a PyInstaller bundle it's added as a
    data dir under ``_MEIPASS``; in dev it's the repo's ``web/dist``."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "web_dist"
    return _repo_root() / "web" / "dist"


def bootstrap_env() -> None:
    """Point ``DATABASE_PATH`` at the per-user data dir before ``olisar.config`` loads.

    No-op in plain development (not frozen, no ``OLISAR_DATA_DIR``) so the existing
    ``.env``/``data/olisar.db`` behaviour is preserved exactly. Uses ``setdefault`` so
    an explicit ``DATABASE_PATH`` in the environment always wins.
    """
    if not is_frozen() and not os.environ.get("OLISAR_DATA_DIR"):
        return
    os.environ.setdefault("DATABASE_PATH", str(db_path()))
