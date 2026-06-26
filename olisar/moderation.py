"""Global moderation — a small in-memory ban list synced from the marketplace registry.

A platform developer can ban a Discord id from the registry's developer console; every
bot pulls that blocklist on a short loop (see ``bot.cogs.moderation``) and refuses banned
users in chat and slash commands (enforced in ``bot.access.member_allowed``). The sync
interval is short so a ban takes effect within ~a minute — "checked constantly", not just
at startup. A failed refresh keeps whatever set we last had, so a transient registry
outage neither un-bans nor over-bans.
"""

from __future__ import annotations

import logging

import httpx

from olisar.config import settings

log = logging.getLogger("olisar.moderation")

_REFRESH_TIMEOUT = 10.0
_banned: set[int] = set()


def is_banned(user_id: int | None) -> bool:
    """Whether this Discord user id is on the synced global ban list."""
    if user_id is None:
        return False
    try:
        return int(user_id) in _banned
    except (TypeError, ValueError):
        return False


def banned_ids() -> set[int]:
    return set(_banned)


async def sync_bans() -> bool:
    """Refresh the ban set from the registry. Returns True on a successful refresh; keeps
    the previous set untouched on any error (so an outage doesn't change enforcement)."""
    base = (settings.registry_url or "").rstrip("/")
    if not base:
        return False
    try:
        async with httpx.AsyncClient(timeout=_REFRESH_TIMEOUT) as client:
            r = await client.get(base + "/v1/moderation/bans")
        if r.status_code != 200:
            return False
        ids = r.json().get("bans") or []
    except Exception:  # noqa: BLE001 - transient; keep the last good set
        log.debug("ban-list sync failed", exc_info=True)
        return False
    new: set[int] = set()
    for x in ids:
        try:
            new.add(int(x))
        except (TypeError, ValueError):
            continue
    global _banned
    _banned = new
    return True
