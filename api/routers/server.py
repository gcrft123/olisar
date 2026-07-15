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


@router.post("/connect")
async def connect(body: ConnectIn) -> dict:
    """Adopt a VM that already runs Olisar (verify over SSH, persist — no reinstall)."""
    return await remote.connect(body.host, body.user or "ubuntu")


@router.post("/power")
async def power(body: PowerIn) -> dict:
    """Start (`up`) or stop (`stop`) the remote container."""
    return await remote.power(body.action)


@router.post("/update")
async def update() -> dict:
    """Pull the latest Olisar image on the VM; recreate the container if it was running."""
    return await remote.update_image()


@router.get("/status")
async def status() -> dict:
    """Whether the remote container is running, recent logs, and the public URL."""
    return await remote.status()


@router.get("/logs")
async def logs(which: str = "bot", tail: int = 200) -> dict:
    """Recent VM logs (``which`` = 'bot' or 'funnel') over SSH, for the control panel's Logs view."""
    return await remote.logs(which, tail)
