"""Request-trust helpers for the loopback-gated routers.

A request reaches the backend one of two ways: directly to the loopback socket
(``127.0.0.1:<port>``), or proxied in through the Tailscale Funnel sidecar. The sidecar
always sets ``X-Forwarded-Host``/``-Proto``, and uvicorn's ``proxy_headers`` can rewrite
``request.client.host`` from a **client-spoofable** ``X-Forwarded-For`` — so a loopback peer
IP alone is NOT proof a request came from the operator's own machine.

``is_local_request`` therefore also requires the *absence* of forwarding headers, which the
sidecar always adds to funnel traffic; that way a remote visitor can never masquerade as
local to reach operator-machine-only controls (tunnel toggle, setup, the ``.env`` prefill).
"""

from __future__ import annotations

from fastapi import Request

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


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
