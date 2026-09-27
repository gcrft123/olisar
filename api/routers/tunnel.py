"""Loopback-only control for remote access via Tailscale Funnel.

The Electron tray, the setup wizard and Settings → Remote access drive remote access through
these endpoints. They're local-only (the operator's machine) since they start/stop a process
and flip the public URL; the Tailscale auth key itself is never returned. The manager lives on
``app.state.tunnel`` and is None when running outside the unified backend (e.g. dev API).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from api.schemas import TunnelEnableIn, TunnelRenameIn
from api.trust import is_local_request
from olisar import runtime_config
from olisar.config import settings
from olisar.runtime import state
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
        # Headless (server) deployments manage the funnel from env, so the console hides
        # the on/off toggle and treats remote access as always-on.
        "headless": settings.headless,
        "hostname": await runtime_config.tunnel_hostname(),
        "public_url": await runtime_config.public_base_url(),
        # Whether turning it on can reuse a stored auth key. Only shared hosting asks for one
        # in setup, so a bot set up for this machine alone has none, and the console has to
        # ask for it rather than offer a switch that can only fail. Never the key itself.
        "has_key": bool(await runtime_config.tunnel_token()),
        # The device name a stored key last came up under, "" before it ever has.
        "node": await runtime_config.tunnel_node(),
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
        auth_key, node, runtime_config.listen_url(), str(tailscale_state_dir())
    )
    if not ok:
        # ``result`` is the failure reason (may include Tailscale's "enable Funnel" URL).
        raise HTTPException(status_code=400, detail=result)

    public_url = await _record(node, result, auth_key=auth_key)
    return {
        "ok": True,
        "public_url": public_url,
        "redirect_uri": f"{public_url}/auth/callback",
    }


async def _record(node: str, url: str, *, auth_key: str | None = None) -> str:
    """Save the funnel that just came up, and return its public URL."""
    public_url = url.rstrip("/")
    await runtime_config.save(
        tunnel_enabled=True,
        tunnel_token=auth_key,  # None keeps the stored key
        tunnel_node=node,
        tunnel_hostname=_host_from_url(public_url),
    )
    # Republish state.json — boot may have written it before the tunnel existed, or with
    # the reason it couldn't bring one up.
    state.write(public_url=public_url, tunnel_error="")
    return public_url


@router.post("/rename", dependencies=[Depends(_local_only)])
async def rename(body: TunnelRenameIn, request: Request) -> dict:
    """Rename the Tailscale device, which is the first part of the console's address, and
    bring the funnel back up under the new name. The node keeps its identity, so no auth
    key is needed. Discord refuses sign-ins at the new address until its redirect is
    registered, which is why that comes back too, with a ``note`` when Tailscale gave the
    device some other name."""
    from olisar.runtime.tunnel import DEVICE_NAME_RULES, device_name, rename_note

    mgr = getattr(request.app.state, "tunnel", None)
    if mgr is None or not mgr.running:
        raise HTTPException(status_code=400, detail="Turn remote access on first.")
    node = device_name(body.hostname)
    if not node:
        raise HTTPException(status_code=400, detail=DEVICE_NAME_RULES)
    old = await runtime_config.tunnel_node() or "olisar"
    auth_key = await runtime_config.tunnel_token()
    target, state_dir = runtime_config.listen_url(), str(tailscale_state_dir())

    await mgr.stop()
    ok, result = await mgr.start(auth_key, node, target, state_dir)
    if not ok:
        # Back up under the old name, so a rename that fails doesn't take the console offline.
        back, url = await mgr.start(auth_key, old, target, state_dir)
        if back:
            await _record(old, url)
        else:
            state.write(tunnel_error=url)
        raise HTTPException(status_code=400, detail=f"Couldn't rename it: {result}")

    public_url = await _record(node, result)
    log.info("remote access renamed from %s to %s: %s", old, node, public_url)
    return {
        "ok": True,
        "public_url": public_url,
        "redirect_uri": f"{public_url}/auth/callback",
        "note": rename_note(node, public_url),
    }


@router.get("/discord", dependencies=[Depends(_local_only)])
async def discord() -> dict:
    """Whether the bot's Discord application lists the console's public sign-in address,
    which Discord refuses sign-ins at until it does: ``{ok, app_id, redirect, added}``, or
    ``{ok: False}`` with no public address or no answer from Discord."""
    from olisar import discord_app

    url = await runtime_config.public_base_url()
    if not url.startswith("https://"):
        return {"ok": False, "error": "remote access is off"}
    redirect = f"{url.rstrip('/')}/auth/callback"
    try:
        app = await discord_app.inspect(await runtime_config.discord_token())
    except Exception as exc:  # noqa: BLE001 (the pane just can't tell)
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    return {"ok": True, "app_id": app["id"], "redirect": redirect, "added": redirect in app["redirect_uris"]}


@router.post("/disable", dependencies=[Depends(_local_only)])
async def disable(request: Request) -> dict:
    mgr = getattr(request.app.state, "tunnel", None)
    if mgr is not None:
        await mgr.stop()
    await runtime_config.save(tunnel_enabled=False)
    state.write(public_url="")  # pruned back to an absent key
    return {"ok": True}
