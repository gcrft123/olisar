"""Server-hosting control: drive the operator's remote Olisar VM over SSH.

All routes are loopback-gated (``require_setup_access``) — in server mode there's no local
Discord bot to authenticate against, so control lives with whoever's at the machine, exactly
like the first-run setup wizard. The heavy lifting is in ``olisar.runtime.remote``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.routers.setup import require_setup_access
from olisar.runtime import remote

router = APIRouter(prefix="/api/server", tags=["server"])


class DeployIn(BaseModel):
    host: str
    user: str | None = "ubuntu"
    env: str


class PowerIn(BaseModel):
    action: str  # 'up' | 'stop'


@router.get("/pubkey", dependencies=[Depends(require_setup_access)])
async def pubkey() -> dict:
    """The app's SSH public key to paste when creating the VM (generated on first call)."""
    return {"public_key": await remote.public_key()}


@router.post("/deploy", dependencies=[Depends(require_setup_access)])
async def deploy(body: DeployIn) -> dict:
    """SSH into the VM, install Docker + the config, and start the container."""
    return await remote.deploy(body.host, body.user or "ubuntu", body.env)


@router.post("/power", dependencies=[Depends(require_setup_access)])
async def power(body: PowerIn) -> dict:
    """Start (`up`) or stop (`stop`) the remote container."""
    return await remote.power(body.action)


@router.get("/status", dependencies=[Depends(require_setup_access)])
async def status() -> dict:
    """Whether the remote container is running, recent logs, and the public URL."""
    return await remote.status()
