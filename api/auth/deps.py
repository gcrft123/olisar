"""FastAPI dependencies that gate admin endpoints.

``require_admin`` checks a valid session (used by account- and global-scope routes).
``require_guild_admin`` additionally authorizes the selected server (sent as the
``X-Guild-Id`` header): the user must have Manage Server on it — or be allowlisted —
and the bot must actually be in it. Every per-server endpoint depends on it.

Permissions are also **re-validated live on every request**: Manage Server can be
revoked in Discord after login, so on each call we re-derive — from the bot's own view
of the guild — which of the servers the session claims the user still actually manages.
If they've lost it everywhere, the session is revoked immediately rather than lingering
until it expires. Allowlisted operators are exempt (admitted by user id, not roles).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from fastapi import Cookie, Header, HTTPException, Request

from api.auth.sessions import COOKIE_NAME, delete_session, get_admin_for_token
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, Guild, utcnow

# When each non-allowlisted admin was last verified against the live bot. Bounds how long a
# session may coast while the bot is unavailable (restarting, or powered off) before it must
# re-authenticate — so a just-revoked admin can't ride a powered-down bot. Cleared on restart
# (the bot is up at startup, so sessions re-verify on their next request).
_last_check: dict[int, datetime] = {}
_OFFLINE_GRACE_SECONDS = 300  # 5 min: comfortably covers restarts; bounds powered-down exposure


def _live_bot(request: Request):
    """The running discord.py client (same process), or None if the bot isn't ready —
    in which case we skip the live re-check and fall back to the session's stored grant
    (a temporary bot outage shouldn't lock admins out of the console)."""
    supervisor = getattr(request.app.state, "bot_supervisor", None)
    bot = getattr(supervisor, "bot", None) if supervisor is not None else None
    if bot is None or not bot.is_ready():
        return None
    return bot


async def _still_managed(bot, user_id: int, claimed: list[str]) -> list[str]:
    """Of the guilds the session *claims* the user manages, which they still have Manage
    Server on right now. Bounded to the claimed set (usually 1–3) and cache-first; a cache
    miss is confirmed with a single ``fetch_member`` so a stale/cold cache can't wrongly
    lock anyone out (a real non-member raises NotFound and is dropped). ``manage_guild`` is
    True for owners and Administrators too, matching the OAuth login check."""
    still: list[str] = []
    for gid_str in claimed:
        try:
            guild = bot.get_guild(int(gid_str))
        except (TypeError, ValueError):
            continue
        if guild is None:
            continue  # the bot is no longer in that guild
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                member = None  # genuinely not a member — drop this guild
            except discord.HTTPException:
                still.append(gid_str)  # transient error — keep, don't revoke on a blip
                continue
        if member is not None and member.guild_permissions.manage_guild:
            still.append(gid_str)
    return still


def _recently_verified(admin: AdminUser) -> bool:
    """Whether ``admin`` was verified recently enough — by a live re-check or a fresh OAuth
    login — to keep their session while the bot is temporarily unavailable."""
    newest = _last_check.get(admin.discord_user_id)
    login = admin.last_login
    if login is not None:
        if login.tzinfo is None:
            login = login.replace(tzinfo=timezone.utc)
        if newest is None or login > newest:
            newest = login
    return newest is not None and (utcnow() - newest).total_seconds() < _OFFLINE_GRACE_SECONDS


async def _revalidate(request: Request, admin: AdminUser, token: str) -> None:
    """Re-check the admin's Discord permissions so a Manage-Server revocation takes
    effect on the next request, not only when the session expires."""
    if admin.is_allowlisted:
        return  # the operator — admitted by user id, not by Discord roles
    bot = _live_bot(request)
    if bot is None:
        # Can't verify against Discord right now (bot restarting or powered off). Coast on
        # the last good check/login for a short grace window, then fail closed so a revoked
        # admin can't keep access by virtue of the bot being down.
        if _recently_verified(admin):
            return
        await delete_session(token)
        raise HTTPException(status_code=401, detail="please sign in again — the bot is offline")
    claimed = [str(g) for g in (admin.managed_guild_ids or [])]
    fresh = await _still_managed(bot, admin.discord_user_id, claimed)
    if set(fresh) != set(claimed):
        # Persist the narrowed set so /guilds and require_guild_admin reflect reality.
        async with session_scope() as session:
            row = await session.get(AdminUser, admin.discord_user_id)
            if row is not None:
                row.managed_guild_ids = fresh
        admin.managed_guild_ids = fresh
    if not fresh:
        # Lost Manage Server everywhere Olisar is — revoke the session outright.
        _last_check.pop(admin.discord_user_id, None)
        await delete_session(token)
        raise HTTPException(status_code=401, detail="access revoked: Manage Server removed")
    _last_check[admin.discord_user_id] = utcnow()  # record this successful live verification


async def require_admin(
    request: Request,
    olisar_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> AdminUser:
    if not olisar_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    admin = await get_admin_for_token(olisar_session)
    if admin is None:
        raise HTTPException(status_code=401, detail="session invalid or expired")
    await _revalidate(request, admin, olisar_session)
    return admin


@dataclass
class GuildContext:
    """A request authorized for one specific server."""
    admin: AdminUser
    guild_id: int


async def require_guild_admin(
    request: Request,
    olisar_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    x_guild_id: str | None = Header(default=None),
) -> GuildContext:
    admin = await require_admin(request, olisar_session)
    if not x_guild_id or not x_guild_id.isdigit():
        raise HTTPException(status_code=400, detail="missing or invalid X-Guild-Id header")
    gid = int(x_guild_id)
    async with session_scope() as session:
        guild = await session.get(Guild, gid)
        if guild is None or not guild.active:
            raise HTTPException(status_code=404, detail="Olisar isn't in that server")
    if not admin.is_allowlisted and x_guild_id not in (admin.managed_guild_ids or []):
        raise HTTPException(status_code=403, detail="you don't have Manage Server on this server")
    return GuildContext(admin=admin, guild_id=gid)
