"""Discord OAuth2 login — admins only.

Flow: /auth/login -> Discord consent -> /auth/callback. We admit a user iff
they're in ADMIN_ALLOWLIST or have Manage Server in the target guild, then create
a server-side session and set the signed cookie.
"""

from __future__ import annotations

import logging
import secrets
import time
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import select

from api.auth.sessions import (
    COOKIE_NAME,
    SESSION_TTL_DAYS,
    create_session,
    delete_session,
    sign_sid,
)
from api.trust import is_local_request
from olisar import discord_app, runtime_config
from olisar.config import settings
from olisar.db.engine import session_scope
from olisar.db.models import AdminGrant, AdminUser, Guild, utcnow
from olisar.guild_setup import ensure_guild_defaults

log = logging.getLogger("olisar.api.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

# Desktop sign-in handoff: OAuth runs in the operator's real browser (not the chromeless
# app window — where Discord can reject the request and leave you stranded with no back
# button, and embedded-webview OAuth is disallowed anyway). The browser flow can't set the
# app's session cookie (separate cookie jar), so the callback parks the new session id here
# keyed by a nonce the app generated, and the app claims it over loopback. Short-lived,
# single-use, loopback-only.
_pending_desktop: dict[str, tuple[str, float]] = {}  # nonce -> (sid or "" for denied, expiry)
_PENDING_TTL = 300.0  # 5 min to finish signing in


_CHECK_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"'
    ' stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
)
_WARN_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"'
    ' stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4"/><path d="M12 17h.01"/>'
    '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/></svg>'
)

# Standalone dark page (no app CSS available) — tuned to the console's design tokens so the
# post-OAuth return page feels like part of Olisar, not a raw redirect. __TINT__/__SOFT__ are
# swapped per state; a plain string (not an f-string) so the CSS braces/percentages stay literal.
_HANDOFF_CSS = """
:root{color-scheme:dark}*{box-sizing:border-box}html,body{height:100%}
body{margin:0;display:grid;place-items:center;padding:24px;background:#020203;color:#ededee;
font-family:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
background:radial-gradient(52vw 52vw at 50% 6%,__SOFT__,transparent 62%)}
.card{position:relative;z-index:1;width:100%;max-width:392px;text-align:center;background:#08080a;
border:1px solid #26262a;border-radius:16px;padding:44px 40px;box-shadow:0 24px 70px rgba(0,0,0,.5);
animation:rise .32s cubic-bezier(.2,.9,.3,1) both}
@keyframes rise{from{opacity:0;transform:translateY(12px) scale(.98)}to{opacity:1;transform:none}}
@keyframes pop{0%{transform:scale(.4);opacity:0}55%{transform:scale(1.1)}100%{transform:scale(1);opacity:1}}
.badge{width:60px;height:60px;border-radius:50%;display:grid;place-items:center;margin:0 auto 22px;
background:__SOFT__;color:__TINT__;animation:pop .34s cubic-bezier(.2,.9,.3,1) both;animation-delay:.06s}
.badge svg{width:30px;height:30px}
h1{font-size:20px;font-weight:600;letter-spacing:-.014em;margin:0 0 9px}
p{color:#9d9da7;font-size:13.5px;line-height:1.55;margin:0}
.brand{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:28px;
color:#6a6a73;font-size:12px;font-weight:600;letter-spacing:.02em}
.brand img{width:18px;height:18px;border-radius:5px;object-fit:cover}
@media (prefers-reduced-motion:reduce){.card,.badge{animation:none}}
"""


def _handoff_html(title: str, sub: str, *, ok: bool) -> str:
    """The page the operator's browser lands on after OAuth — a small branded card telling
    them to return to the app (the console lives in the app, not this tab)."""
    tint = "#43cf8e" if ok else "#ff6369"
    soft = "rgba(67,207,142,.14)" if ok else "rgba(255,99,105,.13)"
    css = _HANDOFF_CSS.replace("__TINT__", tint).replace("__SOFT__", soft)
    icon = _CHECK_SVG if ok else _WARN_SVG
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        "<title>Olisar</title><style>" + css + "</style></head><body><div class=card>"
        '<div class=badge>' + icon + "</div>"
        "<h1>" + title + "</h1><p>" + sub + "</p>"
        '<div class=brand><img src="/logo.png" alt="">Olisar</div>'
        "</div></body></html>"
    )


class _ClaimIn(BaseModel):
    nonce: str


# ── Mock auth (OLISAR_MOCK_AUTH) — local dev/testing only ────────────────────────
MOCK_USER_ID = 424242424242424242
MOCK_GUILD_ID = 987654321987654321
MOCK_USERNAME = "mockoperator"


def _mock_consent_html(state: str) -> str:
    href = f"/auth/callback?code=mock&state={urllib.parse.quote(state)}"
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>Authorize Olisar</title><style>"
        "body{margin:0;height:100vh;display:grid;place-items:center;background:#020203;"
        "color:#ededee;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}"
        ".c{text-align:center;padding:40px;max-width:360px}h1{font-size:19px;margin:0 0 6px;font-weight:600}"
        "p{color:#9d9da7;font-size:13.5px;line-height:1.5;margin:0 0 22px}"
        "a.btn{display:inline-block;background:#5865f2;color:#fff;text-decoration:none;"
        "padding:11px 22px;border-radius:12px;font-weight:600;font-size:14px}"
        ".m{color:#e3a13a;font-size:11px;letter-spacing:.04em;text-transform:uppercase;margin-bottom:14px}"
        "</style></head><body><div class=c><div class=m>Mock sign-in</div>"
        "<h1>Authorize Olisar</h1><p>Sign in as a mock operator on a seeded test server. "
        "No real Discord account is used.</p>"
        f"<a class=btn href=\"{href}\">Authorize</a></div></body></html>"
    )


async def _finish_login(request: Request, sid: str, desktop_nonce: str | None) -> Response:
    """Complete a sign-in: for a desktop (browser-opened) flow, park the session for the app
    to claim and show a return-to-app page; otherwise set the cookie and redirect to the
    dashboard. Shared by the real Discord callback and the mock flow."""
    if desktop_nonce:
        _pending_desktop[desktop_nonce] = (sid, time.monotonic() + _PENDING_TTL)
        page = HTMLResponse(_handoff_html(
            "You’re signed in", "Head back to the Olisar app — you can close this tab.", ok=True))
        page.delete_cookie(STATE_COOKIE)
        return page
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


async def _mock_sign_in(request: Request, desktop_nonce: str | None) -> Response:
    """Seed a mock allowlisted operator on a seeded 'Mock Server', create a session, and
    finish the sign-in — no Discord round-trip."""
    async with session_scope() as session:
        guild = await session.get(Guild, MOCK_GUILD_ID)
        if guild is None:
            session.add(Guild(id=MOCK_GUILD_ID, name="Mock Server", active=True))
        else:
            guild.active = True
    async with session_scope() as session:
        await ensure_guild_defaults(session, MOCK_GUILD_ID)
    async with session_scope() as session:
        admin = await session.get(AdminUser, MOCK_USER_ID)
        if admin is None:
            admin = AdminUser(discord_user_id=MOCK_USER_ID)
            session.add(admin)
        admin.username = MOCK_USERNAME
        admin.is_allowlisted = True
        admin.granted_via = AdminGrant.allowlist
        admin.managed_guild_ids = [str(MOCK_GUILD_ID)]
        admin.last_login = utcnow()
    sid = await create_session(MOCK_USER_ID)
    return await _finish_login(request, sid, desktop_nonce)

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
async def login(request: Request, desktop: str | None = None) -> Response:
    """Begin Discord OAuth. ``?desktop=<nonce>`` marks a desktop-app sign-in opened in the
    system browser — the callback parks the session under that nonce for the app to claim."""
    state = secrets.token_urlsafe(16)
    # Mock auth: skip Discord — serve a mock consent page whose Authorize link comes back to
    # /auth/callback. The state cookie is set here so the callback's state check still passes.
    if settings.mock_auth:
        page = HTMLResponse(_mock_consent_html(state))
        page.set_cookie(
            STATE_COOKIE,
            (await _get_state_serializer()).dumps({"s": state, "d": desktop or ""}),
            max_age=600, httponly=True, samesite="lax", secure=_is_secure(request),
        )
        return page
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
        (await _get_state_serializer()).dumps({"s": state, "d": desktop or ""}),
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
        payload = serializer.loads(request.cookies.get(STATE_COOKIE, ""), max_age=600)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid or expired state")
    expected = payload.get("s") if isinstance(payload, dict) else payload
    desktop_nonce = (payload.get("d") or None) if isinstance(payload, dict) else None
    if expected != state:
        raise HTTPException(status_code=400, detail="state mismatch")

    # Mock auth: skip the Discord token exchange + profile fetch entirely.
    if settings.mock_auth:
        return await _mock_sign_in(request, desktop_nonce)

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
    # The operator is whoever is in ADMIN_ALLOWLIST *or* owns the bot's Discord
    # application — the latter is how a packaged (no-.env) install identifies its
    # operator with zero config. See olisar.discord_app.
    allowlisted = user_id in settings.admin_allowlist or user_id in await discord_app.owner_ids()

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
            if desktop_nonce:
                # Park a denied marker so the app stops polling and shows its access-denied screen.
                _pending_desktop[desktop_nonce] = ("", time.monotonic() + _PENDING_TTL)
                denied_page = HTMLResponse(_handoff_html(
                    "Can’t sign in",
                    "This account can’t manage Olisar. Return to the app and use the operator account.",
                    ok=False))
                denied_page.delete_cookie(STATE_COOKIE)
                return denied_page
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
    # Desktop flow parks the session for the app to claim over loopback (its cookie jar isn't
    # the browser's); otherwise set the cookie and redirect to the dashboard.
    return await _finish_login(request, sid, desktop_nonce)


@router.post("/desktop/claim")
async def desktop_claim(request: Request, body: _ClaimIn) -> JSONResponse:
    """Loopback-only: the desktop app claims the session created by a browser sign-in it
    started (keyed by its nonce), setting the session cookie in the app's own jar. Polled
    while the operator finishes in the browser."""
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="only available on this machine")
    entry = _pending_desktop.get(body.nonce)
    if not entry or entry[1] < time.monotonic():
        _pending_desktop.pop(body.nonce, None)
        return JSONResponse({"ok": False})
    sid, _exp = _pending_desktop.pop(body.nonce)
    if not sid:  # denied marker parked by the callback
        return JSONResponse({"ok": False, "denied": True})
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE_NAME,
        await sign_sid(sid),
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False,  # the desktop app is served over loopback http
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
