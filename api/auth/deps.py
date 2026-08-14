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

``require_member`` / ``require_member_guild`` are the member portal's counterparts. They
ask a strictly weaker question — *are you still in a server Olisar is in* — and grant
access only to the caller's own data. They additionally enforce CSRF on mutating requests
and refuse to serve a server whose operator hasn't opened the portal.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import discord
from fastapi import Cookie, Depends, Header, HTTPException, Request

from api.auth.sessions import (
    COOKIE_NAME,
    MEMBER_COOKIE_NAME,
    delete_member_session,
    delete_member_sessions_for,
    delete_session,
    get_admin_for_token,
    get_member_for_token,
)
from api.trust import is_local_request
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, Guild, GuildConfig, MemberUser, utcnow

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


async def require_admin_or_local(
    request: Request,
    olisar_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> AdminUser | None:
    """Admit an authenticated admin OR a loopback (operator-at-the-machine) request. Lets the
    "lite" settings panels (Updates / Desktop / Feedback / logs) work on the pre-auth
    login/onboarding screens of the desktop app, while remote (Funnel) callers still need a
    session. Loopback trust matches the setup wizard's boundary (local machine access)."""
    if is_local_request(request):
        return None
    return await require_admin(request, olisar_session)


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


# ── Member portal ───────────────────────────────────────────────────────────────

# Same shape and purpose as _last_check, for the weaker member re-check.
_last_member_check: dict[int, datetime] = {}

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def _still_member_of(bot, user_id: int, claimed: list[str]) -> list[str]:
    """Of the guilds the session claims the user is in, which they're still in right now.
    The membership counterpart of ``_still_managed`` — same cache-first lookup and same
    treatment of transient errors, minus the ``manage_guild`` test."""
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
                member = None  # genuinely left — drop this guild
            except discord.HTTPException:
                still.append(gid_str)  # transient error — keep, don't revoke on a blip
                continue
        if member is not None:
            still.append(gid_str)
    return still


def _member_recently_verified(member: MemberUser) -> bool:
    newest = _last_member_check.get(member.discord_user_id)
    login = member.last_login
    if login is not None:
        if login.tzinfo is None:
            login = login.replace(tzinfo=timezone.utc)
        if newest is None or login > newest:
            newest = login
    return newest is not None and (utcnow() - newest).total_seconds() < _OFFLINE_GRACE_SECONDS


async def _revalidate_member(request: Request, member: MemberUser, token: str) -> None:
    """Re-check that the member is still in at least one server Olisar is in, so leaving
    the server closes the portal on the next request rather than at session expiry."""
    bot = _live_bot(request)
    if bot is None:
        if _member_recently_verified(member):
            return
        await delete_member_session(token)
        raise HTTPException(status_code=401, detail="please sign in again — the bot is offline")
    claimed = [str(g) for g in (member.guild_ids or [])]
    fresh = await _still_member_of(bot, member.discord_user_id, claimed)
    if set(fresh) != set(claimed):
        async with session_scope() as session:
            row = await session.get(MemberUser, member.discord_user_id)
            if row is not None:
                row.guild_ids = fresh
        member.guild_ids = fresh
    if not fresh:
        # Left every server Olisar is in. Revoke every session, not just this browser's —
        # the portal's whole surface is personal data, so a second open tab shouldn't
        # outlive the membership that authorized it.
        _last_member_check.pop(member.discord_user_id, None)
        await delete_member_sessions_for(member.discord_user_id)
        raise HTTPException(status_code=401, detail="access revoked: you left the server")
    _last_member_check[member.discord_user_id] = utcnow()


def _check_csrf(request: Request, csrf_secret: str, header_token: str | None) -> None:
    """Enforce a double-submit CSRF token on mutating portal requests.

    The console has relied on ``SameSite=Lax`` alone, which does block cross-site POSTs.
    The portal is the first surface to expose mutating routes to every member of every
    server over a public tunnel URL, and its mutations are destructive (deleting facts,
    erasing an account), so it carries an explicit token as well rather than resting on a
    single cookie attribute.
    """
    if request.method in _SAFE_METHODS:
        return
    if not csrf_secret or not header_token or not secrets.compare_digest(
        header_token, csrf_secret
    ):
        raise HTTPException(status_code=403, detail="missing or invalid CSRF token")


@dataclass
class MemberContext:
    """An authenticated portal member. ``csrf`` is echoed to the client by
    ``GET /api/member/session`` so it can sign its own mutating calls."""
    member: MemberUser
    csrf: str


async def require_member(
    request: Request,
    olisar_member: str | None = Cookie(default=None, alias=MEMBER_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
) -> MemberContext:
    if not olisar_member:
        raise HTTPException(status_code=401, detail="not authenticated")
    resolved = await get_member_for_token(olisar_member)
    if resolved is None:
        raise HTTPException(status_code=401, detail="session invalid or expired")
    member, csrf_secret = resolved
    _check_csrf(request, csrf_secret, x_csrf_token)
    await _revalidate_member(request, member, olisar_member)
    return MemberContext(member=member, csrf=csrf_secret)


async def require_any_session(
    request: Request,
    olisar_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    olisar_member: str | None = Cookie(default=None, alias=MEMBER_COOKIE_NAME),
) -> str:
    """Any authenticated principal — operator at the machine, signed-in admin, or portal
    member. Returns a short actor label for logging.

    Used by Feedback, which the member portal offers. It is the one endpoint where "who are
    you" matters less than "are you someone at all": a member filing a bug about the bot
    shouldn't need Manage Server, and gating it on admin made the portal's Feedback pane a
    button that could only fail.
    """
    if is_local_request(request):
        return "operator"
    if olisar_session:
        admin = await get_admin_for_token(olisar_session)
        if admin is not None:
            return f"admin:{admin.discord_user_id}"
    if olisar_member:
        resolved = await get_member_for_token(olisar_member)
        if resolved is not None:
            return f"member:{resolved[0].discord_user_id}"
    raise HTTPException(status_code=401, detail="not authenticated")


async def discord_identity(
    olisar_session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    olisar_member: str | None = Cookie(default=None, alias=MEMBER_COOKIE_NAME),
) -> int | None:
    """The Discord user id behind this request — admin session or member session, whichever
    is present — or None if neither is. Used where a route must answer *for one Discord
    account* rather than for anyone authenticated: claiming a parked failure report
    (api/routers/settings.py), which belongs to the person the failure happened to.

    Loopback is deliberately **not** a principal here, unlike ``require_any_session``. Being
    at the operator's machine says nothing about which Discord account clicked the button,
    and a report is claimed by identity or not at all. The operator signs in to reach the
    console anyway, so this costs them nothing.

    No live re-check: this is weaker than either portal or console access and grants nothing
    on its own — the routes using it still match the id against the row's owner.
    """
    if olisar_session:
        admin = await get_admin_for_token(olisar_session)
        if admin is not None:
            return admin.discord_user_id
    if olisar_member:
        resolved = await get_member_for_token(olisar_member)
        if resolved is not None:
            return resolved[0].discord_user_id
    return None


async def require_discord_identity(
    user_id: int | None = Depends(discord_identity),
) -> int:
    """:func:`discord_identity`, but 401 rather than None when nobody is signed in."""
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user_id


@dataclass
class MemberGuildContext:
    """A portal request authorized for one specific server."""
    member: MemberUser
    guild_id: int
    show_persona: bool


async def require_member_guild(
    request: Request,
    olisar_member: str | None = Cookie(default=None, alias=MEMBER_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
    x_guild_id: str | None = Header(default=None),
) -> MemberGuildContext:
    ctx = await require_member(request, olisar_member, x_csrf_token)
    if not x_guild_id or not x_guild_id.isdigit():
        raise HTTPException(status_code=400, detail="missing or invalid X-Guild-Id header")
    gid = int(x_guild_id)
    # Membership first, then the operator's switch: answering "the portal is off here" to
    # someone who isn't in the server at all would confirm the bot is in it.
    if x_guild_id not in [str(g) for g in (ctx.member.guild_ids or [])]:
        raise HTTPException(status_code=403, detail="you're not in that server")
    async with session_scope() as session:
        guild = await session.get(Guild, gid)
        if guild is None or not guild.active:
            raise HTTPException(status_code=404, detail="Olisar isn't in that server")
        config = await session.get(GuildConfig, gid)
        if config is None or not config.member_portal_enabled:
            raise HTTPException(
                status_code=403, detail="this server hasn't opened the member portal"
            )
        show_persona = bool(config.member_portal_show_persona)
    return MemberGuildContext(member=ctx.member, guild_id=gid, show_persona=show_persona)
