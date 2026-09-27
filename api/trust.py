"""Request-trust helpers for the loopback-gated routers.

A request reaches the backend one of two ways: directly to the loopback socket
(``127.0.0.1:<port>``), or proxied in through the Tailscale Funnel sidecar. The sidecar
always sets ``X-Forwarded-Host``/``-Proto``, and uvicorn's ``proxy_headers`` can rewrite
``request.client.host`` from a **client-spoofable** ``X-Forwarded-For`` — so a loopback peer
IP alone is NOT proof a request came from the operator's own machine.

``is_local_request`` therefore also requires the *absence* of forwarding headers, which the
sidecar always adds to funnel traffic; that way a remote visitor can never masquerade as
local to reach operator-machine-only controls (tunnel toggle, setup, the ``.env`` prefill).

Being on loopback doesn't make a request the operator's, either: every web page the operator
has open can send one. ``ConsoleGuard`` refuses what a page other than the console sends.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_Origin = tuple[str, str, int]  # scheme, lowercased host name, port


def is_local_request(request: Request) -> bool:
    """True only for a request made directly to the loopback backend — never one proxied
    in through the Funnel (which always carries ``X-Forwarded-*`` headers)."""
    host = request.client.host if request.client else ""
    if host not in LOOPBACK:
        return False
    headers = request.headers
    if headers.get("x-forwarded-host") or headers.get("x-forwarded-for") or headers.get("forwarded"):
        return False
    return True


def require_local_request(request: Request) -> None:
    """Dependency: admit only loopback (operator-at-the-machine) requests. Unlike
    ``require_setup_access`` it has no "only before configured" clause, so it gates
    operator-machine controls that stay available after setup (managing bot profiles)."""
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="only available on this machine")


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _parse_origin(value: str) -> _Origin | None:
    """An ``Origin`` header as (scheme, host, port), or None for ``null`` and anything else
    that isn't a web origin."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts.scheme, parts.hostname.lower(), port or _default_port(parts.scheme)


def _authority(value: str) -> tuple[str, int | None] | None:
    """A ``Host`` header as (host, port or None), or None when it isn't one."""
    try:
        parts = urlsplit("//" + value.strip())
        return (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None


def _own_origins(request: Request) -> list[_Origin]:
    """The origins this request was addressed to: the one in its ``Host``, and, behind the
    Funnel sidecar, the public one it reports in ``X-Forwarded-Host`` (a cross-site page
    can't set that header without a CORS preflight, which a foreign origin never passes)."""
    scheme = request.url.scheme if request.url.scheme in ("http", "https") else "http"
    out: list[_Origin] = []
    for header in ("host", "x-forwarded-host"):
        value = request.headers.get(header)
        auth = _authority(value.split(",")[0]) if value else None
        if auth and auth[0]:
            out.append((scheme, auth[0], auth[1] or _default_port(scheme)))
    return out


def _same_origin(a: _Origin, b: _Origin) -> bool:
    if a == b:
        return True
    # The same address under another loopback name (localhost for 127.0.0.1): the same port on
    # the same machine, so the same server.
    return a[0] == b[0] and a[2] == b[2] and a[1] in LOOPBACK and b[1] in LOOPBACK


def is_foreign_origin(request: Request) -> bool:
    """A browser request that changes something, sent by a page other than the one it's
    addressed to.

    Any website the operator has open can send a simple POST to 127.0.0.1 (``no-cors``), and a
    body-less one (reconnect the bot, turn remote access off) gets through without CORS ever
    being asked. Browsers stamp those with the page's Origin, and the console's own pages are
    served by the server they talk to, so an Origin that isn't this request's own origin is
    refused. That covers a page on another loopback port (a local dev server) too, which
    isn't the console either. The Vite dev proxy keeps working: the browser addresses it, so
    the Host it forwards is the page's own. Requests with no Origin (the desktop shell, curl)
    aren't from a web page and pass."""
    if request.method in SAFE_METHODS:
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return False
    theirs = _parse_origin(origin)
    if theirs is None:
        return True
    return not any(_same_origin(theirs, ours) for ours in _own_origins(request))


def loopback_origin_regex(port: int) -> str:
    """CORS: the console at this port under any loopback name, and nothing else."""
    return rf"http://(127\.0\.0\.1|localhost|\[::1\]):{int(port)}"


class ConsoleGuard:
    """ASGI middleware refusing cross-site writes before any route sees them. Pure ASGI rather
    than ``BaseHTTPMiddleware`` so a streamed answer and ``request.is_disconnected()`` (how a
    cancelled marketplace publish is noticed) behave as they do without it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_foreign_origin(Request(scope)):
            await JSONResponse({"detail": "not from this console"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)
