"""Discord OAuth2 login — admins only.

Flow: /auth/login -> Discord consent -> /auth/callback. We admit a user iff
they're in ADMIN_ALLOWLIST or have Manage Server in the target guild, then create
a server-side session and set the signed cookie.
"""

from __future__ import annotations

import logging
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select

from api.auth.sessions import (
    COOKIE_NAME,
    SESSION_TTL_DAYS,
    create_session,
    delete_session,
    sign_sid,
)
from olisar import runtime_config
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AdminGrant, AdminUser, Guild, utcnow

log = logging.getLogger("olisar.api.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
ME_URL = "https://discord.com/api/users/@me"
MANAGE_GUILD = 0x20  # permission bit

STATE_COOKIE = "olisar_oauth_state"

# Built lazily from the resolved session secret (which may be auto-generated after
# this module is imported, and changes if the operator reconfigures).
_state_serializer: URLSafeTimedSerializer | None = None
_state_secret: str | None = None


async def _get_state_serializer() -> URLSafeTimedSerializer:
    global _state_serializer, _state_secret
    secret = await runtime_config.session_secret()
    if _state_serializer is None or _state_secret != secret:
        _state_serializer = URLSafeTimedSerializer(secret, salt="olisar-oauth-state")
        _state_secret = secret
    return _state_serializer


def _origin(request: Request) -> str:
    """The scheme + host the *browser* is actually using for this request — loopback
    when logging in from the desktop window, the tunnel host when a remote admin comes
    in through Tailscale Funnel (the sidecar forwards X-Forwarded-Proto/Host). The OAuth
    redirect URI and the state/session cookies must all match this origin, or the cookie
    set on /auth/login won't be sent back to /auth/callback."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}".rstrip("/")


def _redirect_uri(request: Request) -> str:
    return _origin(request) + "/auth/callback"


def _is_secure(request: Request) -> bool:
    return _origin(request).lower().startswith("https")


def _managed_guild_ids(guilds: object) -> list[str]:
    """Guild ids (as strings — snowflakes exceed JS's safe-integer range) where the
    user has Manage Server. This is the set of servers they may configure."""
    out: list[str] = []
    if isinstance(guilds, list):
        for guild in guilds:
            try:
                if int(guild.get("permissions", 0)) & MANAGE_GUILD:
                    out.append(str(int(guild.get("id", 0))))
            except (TypeError, ValueError):
                continue
    return out


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(16)
    redirect_uri = _redirect_uri(request)
    params = {
        "client_id": await runtime_config.discord_client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
    }
    resp = RedirectResponse(AUTHORIZE_URL + "?" + urllib.parse.urlencode(params))
    resp.set_cookie(
        STATE_COOKIE,
        (await _get_state_serializer()).dumps(state),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
    )
    return resp


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None):
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")
    try:
        serializer = await _get_state_serializer()
        expected = serializer.loads(request.cookies.get(STATE_COOKIE, ""), max_age=600)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid or expired state")
    if expected != state:
        raise HTTPException(status_code=400, detail="state mismatch")

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": await runtime_config.discord_client_id(),
                "client_secret": await runtime_config.discord_client_secret(),
                "grant_type": "authorization_code",
                "code": code,
                # Must match the redirect_uri used in /login (Discord enforces this).
                "redirect_uri": _redirect_uri(request),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            log.warning("token exchange failed: %s", token_resp.text[:200])
            raise HTTPException(status_code=400, detail="token exchange failed")
        access_token = token_resp.json()["access_token"]
        auth_header = {"Authorization": f"Bearer {access_token}"}
        me = (await client.get(ME_URL, headers=auth_header)).json()
        guilds = (await client.get(ME_URL + "/guilds", headers=auth_header)).json()

    user_id = int(me["id"])
    managed = _managed_guild_ids(guilds)
    allowlisted = user_id in settings.admin_allowlist

    async with session_scope() as session:
        # Admit if allowlisted (the operator) or you have Manage Server on at least
        # one guild Olisar is actually in. The allowlist gets every guild later.
        bot_guilds = set(await session.scalars(select(Guild.id).where(Guild.active.is_(True))))
        if not (allowlisted or any(int(g) in bot_guilds for g in managed)):
            # Authenticated with Discord, but not an admin of any server Olisar is in.
            # Bounce back to the dashboard with a flag so it can render a styled
            # "access denied" screen rather than a raw 403 JSON page. No session is
            # created, so this account stays signed out of the console.
            log.info("console access denied for user %s — no Manage Server on a bot guild", user_id)
            denied = RedirectResponse(_origin(request) + "/?denied=role")
            denied.delete_cookie(STATE_COOKIE)
            return denied
        grant = AdminGrant.allowlist if allowlisted else AdminGrant.manage_guild
        admin = await session.get(AdminUser, user_id)
        if admin is None:
            admin = AdminUser(discord_user_id=user_id)
            session.add(admin)
        admin.username = me.get("username", "")
        admin.is_allowlisted = allowlisted
        admin.granted_via = grant
        admin.managed_guild_ids = managed
        admin.last_login = utcnow()

    sid = await create_session(user_id)
    # Send the browser back to the origin it came from — the session cookie is set on
    # that origin too, so the dashboard reads it immediately.
    resp = RedirectResponse(_origin(request) + "/")
    resp.delete_cookie(STATE_COOKIE)
    resp.set_cookie(
        COOKIE_NAME,
        await sign_sid(sid),
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
    )
    return resp


@router.post("/logout")
async def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await delete_session(token)
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE_NAME)
    return resp
