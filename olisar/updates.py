"""Check whether a newer Olisar release is available on GitHub.

Mirrors what the desktop tray's updater does, but as a backend call so the dashboard's
Settings → Updates works in any context (browser or desktop). Read-only: it only
queries the public Releases API and compares versions; it never downloads or installs
(that's the desktop app's job, from the tray)."""

from __future__ import annotations

import logging
import os
import re
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

import aiohttp

log = logging.getLogger("olisar.updates")

REPO = "gcrft123/olisar"
_LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


@lru_cache(maxsize=1)
def current_version() -> str:
    """This build's version.

    In the packaged desktop app the Electron shell passes ``OLISAR_VERSION`` (its own
    ``app.getVersion()``), because a PyInstaller bundle has neither installed package
    metadata nor a readable ``pyproject.toml`` — without this the backend reported
    ``0.0.0``, so Settings → Updates showed v0.0.0 and "update available" forever.
    Falls back to installed metadata, then a bundled/source ``pyproject.toml``, then a
    sentinel."""
    env = os.environ.get("OLISAR_VERSION", "").strip().lstrip("vV")
    if env:
        return env
    try:
        from importlib.metadata import version

        return version("discord-olisar")
    except Exception:
        pass
    # Source runs read the repo's pyproject; the frozen bundle ships a copy at the
    # PyInstaller root (_MEIPASS == updates.py's parent.parent), so this still resolves.
    for cand in (
        Path(__file__).resolve().parent.parent / "pyproject.toml",
        Path(getattr(sys, "_MEIPASS", "")) / "pyproject.toml",
    ):
        try:
            data = tomllib.loads(cand.read_text("utf-8"))
            return str(data["project"]["version"])
        except Exception:
            continue
    return "0.0.0"


def _parts(v: str) -> tuple[int, ...]:
    """Numeric components of a version string ('v0.2.1' -> (0, 2, 1)) for comparison."""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums) or (0,)


def is_newer(remote: str, local: str) -> bool:
    return _parts(remote) > _parts(local)


async def check_latest() -> dict:
    """Compare the current build to the latest GitHub release. Best-effort: on any
    error returns ``available: False`` with the current version so the UI degrades
    gracefully rather than erroring."""
    current = current_version()
    out = {
        "current": current,
        "latest": None,
        "available": False,
        "url": RELEASES_PAGE,
        "published_at": None,
        "error": None,
    }
    try:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "olisar"}
        async with aiohttp.ClientSession() as session:
            async with session.get(_LATEST_API, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status == 404:
                    return out  # no releases published yet
                if resp.status >= 400:
                    out["error"] = f"GitHub returned HTTP {resp.status}"
                    return out
                data = await resp.json()
        tag = (data.get("tag_name") or data.get("name") or "").strip()
        out["latest"] = tag or None
        out["published_at"] = data.get("published_at")
        if data.get("html_url"):
            out["url"] = data["html_url"]
        out["available"] = bool(tag) and is_newer(tag, current)
    except Exception as exc:
        log.warning("update check failed: %s", exc)
        out["error"] = "couldn't reach GitHub"
    return out
