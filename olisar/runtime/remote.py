"""Drive a remote Olisar container over SSH (the "server shared hosting" mode).

The app generates its own SSH keypair once; the operator pastes the public key when
creating their cloud VM, so the private key never leaves this machine. With that we can,
with no terminal work from the operator:
  - install Docker + write the .env / compose file + start the container (`deploy`)
  - start/stop it later from the in-app control panel (`power`)
  - read whether it's running, recent logs, and the public URL (`status`)

Host-key checking is disabled: the target is the operator's own freshly-created VM,
addressed by IP, so there's no prior known-hosts entry to pin.
"""

from __future__ import annotations

import asyncio
import logging
import re

import asyncssh
from sqlalchemy import select

from olisar import runtime_config
from olisar.db.engine import session_scope
from olisar.db.models import AppConfig

log = logging.getLogger("olisar.remote")

APP_DIR = "olisar"          # ~/olisar on the VM holds .env + docker-compose.yml
CONNECT_TIMEOUT = 20        # seconds to establish the SSH connection
_TSNET_RE = re.compile(r"https://[\w.-]+\.ts\.net")

# The compose the app writes to the VM: pull the prebuilt image, read the .env beside it,
# persist state in a named volume. No published ports — Tailscale Funnel is the ingress.
COMPOSE_YML = """\
services:
  olisar:
    image: ghcr.io/gcrft123/olisar:latest
    env_file: .env
    volumes:
      - olisar-data:/var/lib/olisar
    restart: unless-stopped

volumes:
  olisar-data:
"""


# TODO(migration): cross-host data transfer (follow-up, not implemented here). Move a bot's
# data when its hosting changes, keeping the OLD copy as a backup:
#   - transfer the SQLite DB (self-contained: vectors + FTS live inside it) + the kb_uploads/
#     dir over SFTP via `conn.start_sftp_client()`; VM data is /var/lib/olisar/{olisar.db,kb_uploads},
#     local is profiles.db_path_for(id) (+ sibling kb_uploads).
#   - stop the source first (local: server.stop_all_supervisors + engine.reset_engine; VM:
#     `docker compose stop`) so WAL/SHM are flushed before copying.
#   - matrix: local→new-server (upload), server→local (download), server→server-same-IP (no-op),
#     server→server-diff-IP (transfer). Verify the copy, then leave the old copy in place.


async def _load() -> AppConfig | None:
    async with session_scope() as session:
        return await session.scalar(select(AppConfig).where(AppConfig.id == 1))


async def public_key() -> str:
    """The app's SSH public key, generating + persisting the keypair on first call. This
    is what the operator pastes into their cloud VM's 'SSH keys' box."""
    cfg = await _load()
    if cfg and cfg.server_ssh_pubkey and cfg.server_ssh_privkey:
        return cfg.server_ssh_pubkey
    key = asyncssh.generate_private_key("ssh-ed25519", comment="olisar-app")
    priv = key.export_private_key().decode()
    pub = key.export_public_key().decode().strip()
    await runtime_config.save(server_ssh_privkey=priv, server_ssh_pubkey=pub)
    return pub


async def _connect(host: str, user: str):
    """Open an SSH connection with the app's private key. Caller must close it."""
    cfg = await _load()
    priv = cfg.server_ssh_privkey if cfg else ""
    if not priv:
        raise RuntimeError("no SSH key yet — generate one first")
    ck = asyncssh.import_private_key(priv)
    return await asyncssh.connect(
        host, username=user, client_keys=[ck], known_hosts=None,
        connect_timeout=CONNECT_TIMEOUT,
    )


async def _run(conn, cmd: str, *, timeout: float = 180.0) -> str:
    """Run one command, returning combined stdout+stderr; raises on non-zero exit."""
    r = await asyncio.wait_for(conn.run(cmd, check=False), timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if r.exit_status != 0:
        raise RuntimeError(f"`{cmd.splitlines()[0]}…` failed ({r.exit_status}):\n{out.strip()[-800:]}")
    return out


async def deploy(host: str, user: str, env_text: str) -> dict:
    """Install Docker if needed, drop the .env + compose file, and start the container.
    On success, persist the connection and switch the app into server-hosting mode."""
    host = (host or "").strip()
    user = (user or "").strip() or "ubuntu"
    if not host:
        return {"ok": False, "error": "Enter the VM's public IP address."}
    log_lines: list[str] = []
    try:
        conn = await _connect(host, user)
    except Exception as exc:  # noqa: BLE001 — surfaced to the operator
        return {"ok": False, "error": f"Couldn't reach the VM over SSH: {exc}"}
    try:
        log_lines.append("Installing Docker (skipped if already present)…")
        await _run(conn, "command -v docker >/dev/null 2>&1 || (curl -fsSL https://get.docker.com | sudo sh)", timeout=300)
        log_lines.append("Writing configuration…")
        # File bodies go over stdin (via `input=`), so secrets never appear in the VM's
        # process list / shell history the way an inline command would.
        await _run(conn, f"mkdir -p ~/{APP_DIR}", timeout=30)
        await conn.run(f"cat > ~/{APP_DIR}/.env", input=env_text, check=True)
        await conn.run(f"cat > ~/{APP_DIR}/docker-compose.yml", input=COMPOSE_YML, check=True)
        log_lines.append("Pulling the Olisar image…")
        await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose pull", timeout=420)
        log_lines.append("Starting the container…")
        out = await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose up -d", timeout=180)
        log_lines.append(out.strip())
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {"ok": False, "error": str(exc), "log": "\n".join(log_lines)}
    conn.close()
    await runtime_config.save(
        server_host=host, server_ssh_user=user, hosting_mode="server", configured=True,
    )
    await runtime_config.session_secret()
    return {"ok": True, "log": "\n".join(log_lines)}


async def connect(host: str, user: str) -> dict:
    """Adopt a VM that's ALREADY running Olisar (deployed elsewhere, set up by hand, or
    before a reinstall of this app): verify the compose file is present over SSH, then
    persist the connection and switch to server-hosting mode — no install, no config
    overwrite. The app's public key must already be in the VM's authorized_keys."""
    host = (host or "").strip()
    user = (user or "").strip() or "ubuntu"
    if not host:
        return {"ok": False, "error": "Enter the VM's public IP address."}
    try:
        conn = await _connect(host, user)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM over SSH: {exc}"}
    try:
        r = await conn.run(f"test -f ~/{APP_DIR}/docker-compose.yml && echo OK", check=False)
        if "OK" not in (r.stdout or ""):
            conn.close()
            return {
                "ok": False,
                "error": f"No Olisar install found in ~/{APP_DIR} on this VM — deploy it "
                "first, or check the IP and that the SSH key was added.",
            }
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {"ok": False, "error": str(exc)}
    conn.close()
    await runtime_config.save(
        server_host=host, server_ssh_user=user, hosting_mode="server", configured=True,
    )
    await runtime_config.session_secret()
    return {"ok": True}


async def power(action: str) -> dict:
    """Start (`up`) or stop (`stop`) the container on the stored VM."""
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "No server configured yet."}
    sub = "up -d" if action == "up" else "stop"
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM: {exc}"}
    try:
        await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose {sub}", timeout=120)
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {"ok": False, "error": str(exc)}
    conn.close()
    return {"ok": True, "running": action == "up"}


async def logs(which: str = "bot", tail: int = 200) -> dict:
    """Recent VM logs over SSH for the control panel's Logs view. ``which='bot'`` returns the
    container logs; ``which='funnel'`` filters them to the Tailscale Funnel lines (the funnel
    runs in-process inside the same container, so its output is interleaved in the same logs)."""
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "No server configured yet."}
    n = max(1, min(int(tail or 200), 2000))
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM: {exc}"}
    cmd = f"cd ~/{APP_DIR} && sudo docker compose logs --tail {n} --no-color 2>/dev/null || true"
    if which == "funnel":
        # Match the local funnel-log filter: tunnel loggers + the sidecar URL/error markers.
        cmd += " | grep -Ei 'olisar\\.tunnel|olisar\\.api\\.tunnel|tailscale|funnel|ts\\.net|OLISAR_FUNNEL_(URL|ERROR)'"
    try:
        out = await _run(conn, cmd, timeout=40)
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {"ok": False, "error": str(exc)}
    conn.close()
    return {"ok": True, "logs": out.strip()[-8000:]}


async def status() -> dict:
    """Whether the remote container is running, recent logs, and the public URL."""
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"configured": False}
    base = {"configured": True, "host": cfg.server_host}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {**base, "reachable": False, "error": str(exc)}
    try:
        ps = await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose ps 2>/dev/null || true", timeout=30)
        logs = await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose logs --tail 40 2>/dev/null || true", timeout=30)
        # The public ts.net URL is logged once at funnel startup, which on a long-running bot
        # has usually scrolled past the 40-line display tail — grep the FULL logs for it so
        # "Open console" isn't left greyed out. Only the URL comes back over the wire.
        url_out = await _run(
            conn,
            f"cd ~/{APP_DIR} && sudo docker compose logs --no-color 2>/dev/null "
            "| grep -oiE 'https://[A-Za-z0-9._-]+\\.ts\\.net' | tail -1 || true",
            timeout=40,
        )
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {**base, "reachable": True, "error": str(exc)}
    conn.close()
    running = bool(re.search(r"\brunning\b|\bUp\b", ps))
    url = url_out.strip()
    if not url:
        m = _TSNET_RE.search(logs)
        url = m.group(0) if m else ""
    return {**base, "reachable": True, "running": running, "url": url, "logs": logs.strip()[-4000:]}
