"""Check whether a newer Olisar release is available on GitHub.

Mirrors what the desktop tray's updater does, but as a backend call so the dashboard's
Settings → Updates works in any context (browser or desktop). Read-only: it only
queries the public Releases API and compares versions; it never downloads or installs
(that's the desktop app's job, from the tray).

An install follows one of two channels. **Stable** is GitHub's latest release, which
never includes a pre-release. **Beta** is the newest release of either kind, so a beta
tester moves onto each stable release as it ships and onto the next beta after it. The
choice lives in ``updates.json`` in the data directory, which is also where the Electron
shell's updater reads it (``desktop/updater.js``); see :mod:`olisar.versioning` for how
versions are spelled and ordered."""

from __future__ import annotations

import json
import logging
import os
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

import aiohttp

from olisar.versioning import display, is_beta, is_newer, parse

log = logging.getLogger("olisar.updates")

REPO = "gcrft123/olisar"
_LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
# Newest first. Thirty covers any realistic run of betas between two stable releases.
_RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

CHANNELS = ("stable", "beta")
CHANNEL_FILE = "updates.json"

# What ``current_version()`` reports when this build can't tell what it is. Callers that
# act on a version comparison (the server auto-update) have to recognise it and stand down.
UNKNOWN_VERSION = "0.0.0"


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
    return UNKNOWN_VERSION


# ── channel ──────────────────────────────────────────────────────────────────


def _channel_path() -> Path:
    from olisar.runtime.paths import data_dir

    return data_dir() / CHANNEL_FILE


def channel() -> str:
    """The channel this install follows. A choice made in Settings wins. Without one, a beta
    build follows beta, because the first beta has to be installed by hand (a stable build
    never sees pre-releases) and that should be all it takes to join."""
    try:
        chosen = json.loads(_channel_path().read_text("utf-8")).get("channel")
    except (OSError, ValueError, AttributeError):
        chosen = None
    if chosen in CHANNELS:
        return chosen
    return "beta" if is_beta(current_version()) else "stable"


def set_channel(value: str) -> str:
    if value not in CHANNELS:
        raise ValueError(f"unknown update channel: {value!r}")
    path = _channel_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"channel": value}), "utf-8")
    os.replace(tmp, path)
    return value


# ── releases ─────────────────────────────────────────────────────────────────


def pick_release(releases: list[dict], chan: str) -> dict | None:
    """The newest release ``chan`` should offer, from a list shaped like the releases API.

    Drafts never count, and neither does a tag that isn't a version. Stable also refuses a
    beta even when it isn't flagged as a pre-release, so a beta published by hand without
    the flag can't reach every stable install."""
    best = None
    for rel in releases:
        tag = (rel.get("tag_name") or "").strip()
        if rel.get("draft") or not parse(tag):
            continue
        if chan != "beta" and (rel.get("prerelease") or is_beta(tag)):
            continue
        if best is None or is_newer(tag, best["tag_name"]):
            best = rel
    return best


async def newest_release(chan: str | None = None) -> dict | None:
    """The newest release on ``chan`` (default: this install's channel) as
    ``{tag, url, published_at}``, or ``None`` when there isn't one yet.

    Raises on a network error or an HTTP error, so a caller about to act on the answer can
    tell "nothing newer" from "couldn't ask"."""
    chan = chan or channel()
    url = _RELEASES_API if chan == "beta" else _LATEST_API
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "olisar"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status == 404:
                return None  # no releases published yet
            if resp.status >= 400:
                raise RuntimeError(f"GitHub returned HTTP {resp.status}")
            data = await resp.json()
    rel = pick_release(data if isinstance(data, list) else [data], chan)
    if rel is None:
        return None
    return {
        "tag": rel["tag_name"].strip(),
        "url": rel.get("html_url") or RELEASES_PAGE,
        "published_at": rel.get("published_at"),
    }


async def check_latest() -> dict:
    """Compare the current build to the newest release on this install's channel.
    Best-effort: on any error returns ``available: False`` with the current version so the
    UI degrades gracefully rather than erroring."""
    current = current_version()
    chan = channel()
    out = {
        "current": display(current),
        "channel": chan,
        "latest": None,
        "available": False,
        "url": RELEASES_PAGE,
        "published_at": None,
        "error": None,
    }
    try:
        rel = await newest_release(chan)
    except RuntimeError as exc:
        out["error"] = str(exc)
        return out
    except Exception as exc:
        log.warning("update check failed: %s", exc)
        out["error"] = "couldn't reach GitHub"
        return out
    if rel:
        out["latest"] = "v" + display(rel["tag"])
        out["url"] = rel["url"]
        out["published_at"] = rel["published_at"]
        out["available"] = is_newer(rel["tag"], current)
    return out
