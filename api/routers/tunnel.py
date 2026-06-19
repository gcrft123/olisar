"""Loopback-only control for remote access via Tailscale Funnel.

The Electron tray and the setup wizard toggle remote access through these endpoints.
They're local-only (the operator's machine) since they start/stop a process and flip the
public URL; the Tailscale auth key itself is never returned. The manager lives on
``app.state.tunnel`` and is None when running outside the unified backend (e.g. dev API).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from api.schemas import TunnelEnableIn
from api.trust import is_local_request
from olisar import runtime_config
from olisar.runtime.paths import tailscale_state_dir

log = logging.getLogger("olisar.api.tunnel")
router = APIRouter(prefix="/api/tunnel", tags=["tunnel"])


async def _local_only(request: Request) -> None:
    # Toggling remote access is a machine-level action: only a request made directly to the
    # loopback backend qualifies, never one proxied in through the Funnel itself.
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="remote-access control is local-only")


def _host_from_url(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").rstrip("/")


@router.get("/status")
async def status(request: Request) -> dict:
    from olisar.runtime.tunnel import funnel_helper_path

    mgr = getattr(request.app.state, "tunnel", None)
    return {
        "available": mgr is not None,
        "running": bool(mgr and mgr.running),
        "helper": bool(funnel_helper_path()),  # is the Funnel binary bundled?
        "hostname": await runtime_config.tunnel_hostname(),
        "public_url": await runtime_config.public_base_url(),
    }


@router.post("/enable", dependencies=[Depends(_local_only)])
async def enable(body: TunnelEnableIn, request: Request) -> dict:
    """Join the operator's tailnet with their auth key and expose the dashboard over
    Tailscale Funnel. Returns the stable public URL + the OAuth redirect to register."""
    mgr = getattr(request.app.state, "tunnel", None)
    if mgr is None:
        raise HTTPException(status_code=400, detail="remote access isn't available here")
    # The wizard supplies a fresh auth key; the tray re-enable falls back to the stored one.
    auth_key = (body.auth_key or "").strip() or await runtime_config.tunnel_token()
    node = (body.hostname or "").strip() or await runtime_config.tunnel_node() or "olisar"
    if not auth_key:
        raise HTTPException(status_code=400, detail="a Tailscale auth key is required")

    ok, result = await mgr.start(
        auth_key, node, runtime_config.local_base_url(), str(tailscale_state_dir())
    )
    if not ok:
        # ``result`` is the failure reason (may include Tailscale's "enable Funnel" URL).
        raise HTTPException(status_code=400, detail=result)

    public_url = result.rstrip("/")
    await runtime_config.save(
        tunnel_enabled=True,
        tunnel_token=auth_key,
        tunnel_node=node,
        tunnel_hostname=_host_from_url(public_url),
    )
    return {
        "ok": True,
        "public_url": public_url,
        "redirect_uri": f"{public_url}/auth/callback",
    }


@router.post("/disable", dependencies=[Depends(_local_only)])
async def disable(request: Request) -> dict:
    mgr = getattr(request.app.state, "tunnel", None)
    if mgr is not None:
        await mgr.stop()
    await runtime_config.save(tunnel_enabled=False)
    return {"ok": True}
