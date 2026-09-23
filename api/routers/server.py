"""Server-hosting control: drive the operator's remote Olisar VM over SSH.

All routes are loopback-gated (``require_local_request``) — in server mode there's no local
Discord bot to authenticate against, so control lives with whoever's at the machine, exactly
like the first-run setup wizard. We use ``require_local_request`` (not ``require_setup_access``)
because the control panel is used AFTER setup: ``require_setup_access`` 403s once the app is
configured, which is precisely when server mode is active — that made the panel unreachable.
The heavy lifting is in ``olisar.runtime.remote``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.trust import require_local_request
from olisar.runtime import remote

router = APIRouter(prefix="/api/server", tags=["server"], dependencies=[Depends(require_local_request)])


class DeployIn(BaseModel):
    host: str
    user: str | None = "ubuntu"
    env: str


class PowerIn(BaseModel):
    action: str  # 'up' | 'stop'


@router.get("/pubkey")
async def pubkey() -> dict:
    """The app's SSH public key to paste when creating the VM (generated on first call)."""
    return {"public_key": await remote.public_key()}


@router.post("/deploy")
async def deploy(body: DeployIn) -> dict:
    """SSH into the VM, install Docker + the config, and start the container."""
    return await remote.deploy(body.host, body.user or "ubuntu", body.env)


class ConnectIn(BaseModel):
    host: str
    user: str | None = "ubuntu"
    app_dir: str | None = ""  # which install, on a VM that runs several bots


@router.post("/connect")
async def connect(body: ConnectIn) -> dict:
    """Adopt a VM that already runs Olisar (verify over SSH, persist — no reinstall). On a
    VM running several bots, answers ``choose`` until told which install this is."""
    return await remote.connect(body.host, body.user or "ubuntu", body.app_dir or "")


@router.post("/power")
async def power(body: PowerIn) -> dict:
    """Start (`up`) or stop (`stop`) the remote container."""
    return await remote.power(body.action)


@router.post("/update")
async def update() -> dict:
    """Apply the newest Olisar release on the VM, health-gated, rolling back on failure.

    On demand. The backend also runs this by itself, whenever it starts up ahead of the VM
    (see ``remote.autoupdate``) — ``/status`` reports that one as ``auto_updating``."""
    return await remote.update_image()


@router.get("/last-update")
async def last_update() -> dict:
    """The VM's last update attempt — including one the app applied at launch, unwatched."""
    return await remote.last_update()


@router.get("/status")
async def status() -> dict:
    """What's on the VM: run state, health, version/digest, public URL, recent logs, and
    whether an automatic update is in flight."""
    return await remote.status()


@router.get("/logs")
async def logs(which: str = "bot", tail: int = 200) -> dict:
    """Recent VM logs (``which`` = 'bot' or 'funnel') over SSH, for the control panel's Logs view."""
    return await remote.logs(which, tail)
