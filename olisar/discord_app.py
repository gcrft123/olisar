"""Resolve the bot's operator(s) from its Discord application owner.

A self-hosted Olisar packaged as a desktop app has no ``ADMIN_ALLOWLIST`` env
(that only exists for ``.env``-driven source runs), so the operator is defined as
**whoever owns the Discord application the bot runs as** — fetched from
``GET /applications/@me`` with the bot token. For a personally-owned app that's the
owner; for a team-owned app it's the team owner plus its members. The result is
cached (ownership is stable) and combined with ``ADMIN_ALLOWLIST`` in the auth flow,
so operator features work in the packaged app with zero configuration.
"""

from __future__ import annotations

import logging
import time

import aiohttp

log = logging.getLogger("olisar.discord_app")

_ENDPOINT = "https://discord.com/api/v10/applications/@me"
_TTL = 3600.0  # owner is stable; refresh hourly to pick up an ownership transfer
_cache: set[int] | None = None
_cache_at = 0.0


def _extract_owner_ids(app: dict) -> set[int]:
    """Pull the operator user IDs out of a Discord application object."""
    ids: set[int] = set()
    owner = app.get("owner") or {}
    if owner.get("id"):
        ids.add(int(owner["id"]))
    team = app.get("team") or {}
    if team:
        if team.get("owner_user_id"):
            ids.add(int(team["owner_user_id"]))
        for member in team.get("members") or []:
            uid = (member.get("user") or {}).get("id")
            if uid:
                ids.add(int(uid))
    return ids


async def owner_ids() -> set[int]:
    """Discord user IDs that own/control the bot's application — i.e. the operators.

    Best-effort and cached: returns an empty set if the token is missing or Discord
    is unreachable (callers treat that as "no app-owner operator", falling back to
    ``ADMIN_ALLOWLIST``). A successful lookup is cached for ``_TTL`` seconds; failures
    are not cached, so a transient error can't lock the operator out permanently.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < _TTL:
        return _cache

    from olisar.runtime_config import discord_token

    token = await discord_token()
    ids: set[int] = set()
    if token:
        try:
            headers = {"Authorization": f"Bot {token}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(_ENDPOINT, headers=headers) as resp:
                    if resp.status == 200:
                        ids = _extract_owner_ids(await resp.json())
                    else:
                        log.warning("app-owner fetch failed (HTTP %s)", resp.status)
        except Exception:
            log.exception("app-owner fetch errored")

    if ids:  # only cache a real answer; keep retrying while empty
        _cache, _cache_at = ids, now
    return ids


def invalidate() -> None:
    """Drop the cached owner set (e.g. after the bot token changes)."""
    global _cache
    _cache = None
