"""Drive a remote Olisar container over SSH (the "server shared hosting" mode).

The app generates its own SSH keypair once; the operator pastes the public key when
creating their cloud VM, so the private key never leaves this machine. With that we can,
with no terminal work from the operator:
  - install Docker + write the .env / compose file + start the container (`deploy`)
  - start/stop it later from the in-app control panel (`power`)
  - pull a newer image and recreate the container when running (`update_image`)
  - read whether it's running, recent logs, and the public URL (`status`)

Host-key checking is disabled: the target is the operator's own freshly-created VM,
addressed by IP, so there's no prior known-hosts entry to pin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import asyncssh
from sqlalchemy import select

from olisar import runtime_config
from olisar.db.engine import session_scope
from olisar.db.models import AppConfig

log = logging.getLogger("olisar.remote")

APP_DIR = "olisar"          # ~/olisar on the VM holds .env + docker-compose.yml
CONNECT_TIMEOUT = 20        # seconds to establish the SSH connection
_TSNET_RE = re.compile(r"https://[\w.-]+\.ts\.net")

# The container mounts the olisar-data volume here, so the VM's DB + uploads live at these
# paths (used by the cross-host data migration; see olisar.runtime.migrate).
VM_DATA_DIR = "/var/lib/olisar"
VM_DB = f"{VM_DATA_DIR}/olisar.db"
VM_KB = f"{VM_DATA_DIR}/kb_uploads"
_HELPER_IMAGE = "alpine"    # tiny image to read/write the named volume while stopped

# Files the app owns on the VM. They're (re)installed on every deploy AND every connect,
# so a VM set up by an older client picks up the current layout instead of silently
# drifting — the compose file used to be written once at deploy and never again.
# `.env` is deliberately NOT in this set: it holds secrets the operator may have edited.
_MANAGED_ASSETS = ("olisar-update.sh", "olisar-update.service", "olisar-update.timer")

# The compose file itself is written by olisar-update.sh, pinned to an immutable digest —
# so "what is deployed" is a fact on disk rather than whatever :latest resolved to.


def _asset(name: str) -> str:
    """Read a ``deploy/`` asset that gets installed onto the VM. Resolves inside a
    PyInstaller bundle (backend.spec ships them under ``deploy/``) and from source."""
    for base in (
        Path(getattr(sys, "_MEIPASS", "")) / "deploy",
        Path(__file__).resolve().parents[2] / "deploy",
    ):
        candidate = base / name
        if candidate.exists():
            return candidate.read_text("utf-8")
    raise RuntimeError(f"missing deploy asset: {name}")


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


async def _read_json(conn, path: str) -> dict:
    """Read a small JSON file off the VM, or ``{}`` if it's missing or malformed."""
    r = await conn.run(f"cat {path} 2>/dev/null || true", check=False)
    try:
        parsed = json.loads((r.stdout or "").strip() or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _install_managed(conn, user: str) -> None:
    """Install/refresh the update script and its systemd timer on the VM.

    Idempotent, and run on connect as well as deploy — this is what brings a VM that an
    older client set up onto the current layout. The timer is best-effort: a host without
    systemd still gets the script (which the control panel can invoke directly)."""
    await _run(conn, f"mkdir -p ~/{APP_DIR}", timeout=30)
    await conn.run(f"cat > ~/{APP_DIR}/olisar-update.sh", input=_asset("olisar-update.sh"), check=True)
    await _run(conn, f"chmod +x ~/{APP_DIR}/olisar-update.sh", timeout=30)

    # systemd wants an absolute path and the unit's user baked in; the app dir lives under
    # the operator's home, which we don't know until we ask.
    abs_dir = (await _run(conn, f"cd ~/{APP_DIR} && pwd", timeout=30)).strip().splitlines()[-1]
    try:
        for unit in ("olisar-update.service", "olisar-update.timer"):
            body = _asset(unit).replace("@DIR@", abs_dir).replace("@USER@", user)
            await conn.run(f"cat > /tmp/{unit}", input=body, check=True)
            await _run(
                conn,
                f"sudo install -m 0644 /tmp/{unit} /etc/systemd/system/{unit} && rm -f /tmp/{unit}",
                timeout=60,
            )
        await _run(
            conn,
            "sudo systemctl daemon-reload && sudo systemctl enable --now olisar-update.timer",
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001 — a missing timer must not fail a deploy
        log.warning("could not install the update timer: %s", exc)


async def deploy(host: str, user: str, env_text: str) -> dict:
    """Install Docker and the updater, write the .env, then let the updater put the newest
    release on the VM and start it. On success, persist the connection and switch the app
    into server-hosting mode."""
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
        await _run(conn, f"chmod 600 ~/{APP_DIR}/.env", timeout=30)
        await _install_managed(conn, user)
        # A first deploy and an update are the same code path — the script resolves the
        # newest release, pins its digest into the compose file, starts it, and rolls back
        # if it doesn't pass its healthcheck. Nothing here duplicates that logic.
        log_lines.append("Pulling the latest Olisar release and starting it…")
        out = await _run(conn, f"bash ~/{APP_DIR}/olisar-update.sh --start", timeout=900)
        log_lines.append(out.strip()[-2000:])
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
    # Reconcile the files we own so a VM deployed by an older client picks up the update
    # script and timer. `.env` is never touched — the operator's secrets live there.
    try:
        await _install_managed(conn, user)
    except Exception as exc:  # noqa: BLE001 — adoption must still succeed
        log.warning("could not reconcile managed files on %s: %s", host, exc)
    conn.close()
    await runtime_config.save(
        server_host=host, server_ssh_user=user, hosting_mode="server", configured=True,
    )
    await runtime_config.session_secret()
    return {"ok": True}


# ── state probe ─────────────────────────────────────────────────────────────────
# One SSH round trip that collects everything the control panel needs. The signals come
# from Docker itself and from the backend's own state.json — not from parsing log text:
#   * run state + health  — `docker inspect` on the container (the image has defined a
#     HEALTHCHECK all along; the old `ps`-regex threw that verdict away, so a crashlooping
#     container under `restart: unless-stopped` reported "Running")
#   * version + revision + digest — the OCI labels CI already stamps on the image, which
#     resolve even while the container is stopped
#   * public URL — state.json, written by the backend into the data volume
# The log-grep URL fallback survives for containers built before state.json existed, and
# is skipped entirely when state.json answered — it was the expensive part.

_PROBE_SECTIONS = ("CONTAINER", "IMAGE", "STATE", "PS", "LOGS", "URL")

_FMT_CONTAINER = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}"
_FMT_IMAGE = (
    '{{index .Config.Labels "org.opencontainers.image.version"}}|'
    '{{index .Config.Labels "org.opencontainers.image.revision"}}|'
    "{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}"
)

# Placeholders rather than an f-string: the Go templates above are all braces.
_PROBE_TEMPLATE = """set +e
cd ~/@APP_DIR@ || exit 0
CID=$(sudo docker compose ps -q 2>/dev/null | head -1)
IMG=$(sudo docker compose images -q 2>/dev/null | head -1)
STATE=""
[ -n "$CID" ] && STATE=$(sudo docker exec "$CID" cat @DATA@/state.json 2>/dev/null)
echo '__OLISAR_CONTAINER__'
[ -n "$CID" ] && sudo docker inspect --format '@FMT_CONTAINER@' "$CID" 2>/dev/null
echo '__OLISAR_IMAGE__'
[ -n "$IMG" ] && sudo docker image inspect --format '@FMT_IMAGE@' "$IMG" 2>/dev/null
echo '__OLISAR_STATE__'
printf '%s\\n' "$STATE"
echo '__OLISAR_PS__'
sudo docker compose ps 2>/dev/null
echo '__OLISAR_LOGS__'
sudo docker compose logs --tail 40 --no-color 2>/dev/null
echo '__OLISAR_URL__'
case "$STATE" in
  *ts.net*) : ;;
  *)
    url=$(sudo docker compose logs --tail 5000 --no-color 2>/dev/null \\
      | grep -oiE 'https://[A-Za-z0-9._-]+\\.ts\\.net' | tail -1)
    if [ -z "$url" ]; then
      url=$(sudo docker compose logs --no-color 2>/dev/null \\
        | grep -m1 -oiE 'https://[A-Za-z0-9._-]+\\.ts\\.net')
    fi
    printf '%s\\n' "$url"
    ;;
esac
"""


def _probe_script() -> str:
    return (
        _PROBE_TEMPLATE.replace("@APP_DIR@", APP_DIR)
        .replace("@DATA@", VM_DATA_DIR)
        .replace("@FMT_CONTAINER@", _FMT_CONTAINER)
        .replace("@FMT_IMAGE@", _FMT_IMAGE)
    )


def _clean(value: str) -> str:
    """A Go template renders a missing map key as ``<no value>`` — treat that as absent
    so an image without OCI labels reports "" rather than that literal in the UI."""
    v = (value or "").strip()
    return "" if v in ("<no value>", "<nil>") else v


def _sections(out: str) -> dict[str, str]:
    """Split probe output on its markers. Sections are ordered, so each one ends where the
    next begins — that keeps stray docker stderr inside the section that produced it."""
    found: dict[str, str] = {}
    for i, name in enumerate(_PROBE_SECTIONS):
        marker = f"__OLISAR_{name}__"
        if marker not in out:
            continue
        rest = out.split(marker, 1)[1]
        if i + 1 < len(_PROBE_SECTIONS):
            rest = rest.split(f"__OLISAR_{_PROBE_SECTIONS[i + 1]}__", 1)[0]
        found[name] = rest.strip()
    return found


def parse_probe(out: str) -> dict:
    """Turn raw probe output into the status fields. Pure — the unit tests drive it with
    captured ``docker`` output for the running / stopped / starting / unhealthy shapes."""
    sec = _sections(out)

    container_state = health = ""
    lines = sec.get("CONTAINER", "").splitlines()
    if lines:
        parts = lines[0].split("|", 1)
        container_state = _clean(parts[0])
        health = _clean(parts[1]) if len(parts) > 1 else ""

    version = revision = digest = ""
    lines = sec.get("IMAGE", "").splitlines()
    if lines:
        parts = lines[0].split("|", 2)
        version = _clean(parts[0])
        revision = _clean(parts[1]) if len(parts) > 1 else ""
        repo_digest = _clean(parts[2]) if len(parts) > 2 else ""
        digest = repo_digest.split("@", 1)[1] if "@" in repo_digest else ""

    published: dict = {}
    raw_state = sec.get("STATE", "")
    if raw_state:
        try:
            parsed = json.loads(raw_state)
            published = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            published = {}  # a truncated/garbled read is just an absent state file

    logs = sec.get("LOGS", "")
    url = str(published.get("public_url") or "").rstrip("/")
    if not url:  # pre-state.json container — the old log-scrape path
        url_lines = sec.get("URL", "").splitlines()
        url = url_lines[0].strip() if url_lines else ""
    if not url:
        m = _TSNET_RE.search(logs)
        url = m.group(0) if m else ""

    # `docker inspect` is authoritative; the ps regex only covers a host whose compose
    # couldn't give us a container id (Compose v1), where "Stopped" would be a lie.
    running = container_state == "running"
    if not container_state:
        running = bool(re.search(r"\brunning\b|\bUp\b", sec.get("PS", "")))

    return {
        "running": running,
        "state": container_state,
        "health": health,  # healthy | unhealthy | starting | "" (no healthcheck)
        "version": version or str(published.get("version") or ""),
        "revision": revision,
        "digest": digest,
        "url": url,
        "logs": logs.strip()[-4000:],
    }


async def _probe(conn) -> dict:
    """Run the probe over an open connection and parse it. Raises on a failed probe."""
    r = await asyncio.wait_for(
        conn.run("bash -s", input=_probe_script(), check=False), timeout=45
    )
    out = (r.stdout or "") + (r.stderr or "")
    if r.exit_status not in (0, None):
        raise RuntimeError(f"status probe failed ({r.exit_status}):\n{out.strip()[-800:]}")
    return parse_probe(out)


async def last_update() -> dict:
    """What the VM's last update attempt did — including one the systemd timer ran while
    the app was closed, which is the only way the operator would ever learn about it."""
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception:  # noqa: BLE001 — informational only
        return {}
    try:
        return await _read_json(conn, f"~/{APP_DIR}/last-update.json")
    finally:
        conn.close()


async def update_image() -> dict:
    """Apply the newest Olisar *release* on the configured VM, by running the VM's own
    ``olisar-update.sh`` — which resolves the release tag, pins its digest into the compose
    file, applies it, waits for the container's healthcheck, and rolls back to the previous
    digest if it never comes up.

    Deliberately the same script the systemd timer runs, so a client-triggered update and
    an unattended one cannot diverge. Best-effort: SSH failures return ``ok: False``
    without raising so the panel can still show status.
    """
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "No server configured yet."}
    user = cfg.server_ssh_user or "ubuntu"
    base = {"host": cfg.server_host}
    try:
        conn = await _connect(cfg.server_host, user)
    except Exception as exc:  # noqa: BLE001
        return {**base, "ok": False, "reachable": False, "error": f"Couldn't reach the VM: {exc}"}
    try:
        # A VM last touched by an older client has no script yet — install it first.
        probe_script = await conn.run(f"test -x ~/{APP_DIR}/olisar-update.sh && echo OK", check=False)
        if "OK" not in (probe_script.stdout or ""):
            await _install_managed(conn, user)
        r = await asyncio.wait_for(
            conn.run(f"bash ~/{APP_DIR}/olisar-update.sh", check=False), timeout=1200
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        result = await _read_json(conn, f"~/{APP_DIR}/last-update.json")
        state = await _probe(conn)
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {**base, "ok": False, "reachable": True, "error": str(exc)}
    conn.close()

    ok = bool(result.get("ok")) if result else r.exit_status == 0
    out_dict = {
        **base,
        "ok": ok,
        "reachable": True,
        "updated": bool(result.get("updated")),
        "rolled_back": bool(result.get("rolled_back")),
        "status": result.get("status") or "",
        "message": result.get("message") or "",
        "tag": result.get("tag") or "",
        "running": state.get("running"),
        "health": state.get("health"),
        "version": state.get("version"),
        "log": out[-2000:],
    }
    if not ok:
        out_dict["error"] = result.get("message") or "The update did not complete."
    return out_dict


async def power(action: str) -> dict:
    """Start (`up`) or stop (`stop`) the container on the stored VM.

    ``up`` boots whatever digest the compose file is pinned to — it deliberately does NOT
    pull. Start used to pull first, which meant an operator who stopped their bot for a
    week silently came back on a different version; updating is now its own action.
    """
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "No server configured yet."}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM: {exc}"}
    try:
        if action == "up":
            await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose up -d", timeout=180)
        else:
            await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose stop", timeout=120)
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
    """What's running on the VM: run state, health, version, digest, public URL, logs.

    One SSH round-trip (connect + a single remote script — see ``_probe``). An earlier
    implementation ran three sequential ``docker compose`` commands and grepped the
    *entire* container log history for the funnel URL; on a long-running VM that routinely
    exceeded the control panel's 40s fetch budget, which the UI then painted as
    "Unreachable" even though SSH (and the Logs view) still worked.
    """
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"configured": False}
    base = {"configured": True, "host": cfg.server_host}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {**base, "reachable": False, "error": str(exc)}
    try:
        probe = await _probe(conn)
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {**base, "reachable": True, "error": str(exc)}
    conn.close()
    return {**base, "reachable": True, **probe}


# ── cross-host data transfer (used by olisar.runtime.migrate) ───────────────────
# The bot's data is the self-contained SQLite DB (vectors + FTS live inside it) plus the
# kb_uploads/ dir of uploaded documents. On the VM these sit in the `olisar-data` named
# volume, which isn't on the host filesystem — so we stage it through a throwaway helper
# container that mounts the volume, then SFTP the staged files. The caller stops the source
# first (so the WAL is flushed) and keeps the old copy as a backup.


async def _volume_name(conn) -> str:
    """The actual Docker volume name backing `olisar-data` (Compose prefixes it with the
    project name, e.g. `olisar_olisar-data`)."""
    out = await _run(conn, "sudo docker volume ls -q --filter name=olisar-data 2>/dev/null || true", timeout=30)
    for line in out.splitlines():
        if line.strip():
            return line.strip()
    raise RuntimeError("couldn't find the olisar-data volume on the VM")


async def _sftp_exists(sftp, path: str) -> bool:
    try:
        await sftp.stat(path)
        return True
    except Exception:  # noqa: BLE001 — any stat failure means "treat as absent"
        return False


async def read_env(host: str, user: str) -> str:
    """The VM's `.env` text. For server-hosted bots the Discord creds + API keys live here
    (not in the local DB), so a move to local / another server reads them from here."""
    conn = await _connect(host, (user or "ubuntu").strip() or "ubuntu")
    try:
        return await _run(conn, f"cat ~/{APP_DIR}/.env 2>/dev/null || true", timeout=30)
    finally:
        conn.close()


async def export_data(host: str, user: str, dest_dir: Path) -> None:
    """Stop the VM's container and copy its data — `olisar.db` (+ any WAL/SHM sidecars) and
    `kb_uploads/` — into the local `dest_dir` over SFTP. Leaves the container stopped and the
    volume intact, so the VM remains a full backup."""
    conn = await _connect(host, (user or "ubuntu").strip() or "ubuntu")
    try:
        await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose stop", timeout=120)
        vol = await _volume_name(conn)
        await _run(conn, f"mkdir -p ~/{APP_DIR}/export && sudo rm -rf ~/{APP_DIR}/export/*", timeout=30)
        await _run(
            conn,
            f"sudo docker run --rm -v {vol}:/v -v ~/{APP_DIR}/export:/out {_HELPER_IMAGE} sh -c "
            "'set -e; for f in olisar.db olisar.db-wal olisar.db-shm; do "
            "if [ -f /v/$f ]; then cp /v/$f /out/$f; fi; done; "
            "if [ -d /v/kb_uploads ]; then cp -a /v/kb_uploads /out/kb_uploads; fi; "
            "chmod -R a+rwX /out'",
            timeout=600,
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        async with conn.start_sftp_client() as sftp:
            base = f"{await sftp.realpath('.')}/{APP_DIR}/export"
            for name in ("olisar.db", "olisar.db-wal", "olisar.db-shm"):
                if await _sftp_exists(sftp, f"{base}/{name}"):
                    await sftp.get(f"{base}/{name}", str(dest_dir / name))
            if await _sftp_exists(sftp, f"{base}/kb_uploads"):
                await sftp.get(f"{base}/kb_uploads", str(dest_dir / "kb_uploads"), recurse=True)
    finally:
        conn.close()


async def import_data(host: str, user: str, src_dir: Path) -> None:
    """Load a staged `olisar.db` (+ `kb_uploads/`) from `src_dir` into the VM's `olisar-data`
    volume and (re)start the container. The compose file must already be present (deploy first).
    Removes any stale WAL/SHM so the replaced DB opens clean."""
    conn = await _connect(host, (user or "ubuntu").strip() or "ubuntu")
    try:
        await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose stop", timeout=120)
        await _run(conn, f"mkdir -p ~/{APP_DIR}/import && sudo rm -rf ~/{APP_DIR}/import/*", timeout=30)
        async with conn.start_sftp_client() as sftp:
            base = f"{await sftp.realpath('.')}/{APP_DIR}/import"
            await sftp.put(str(src_dir / "olisar.db"), f"{base}/olisar.db")
            kb = src_dir / "kb_uploads"
            if kb.is_dir():
                await sftp.put(str(kb), f"{base}/kb_uploads", recurse=True)
        vol = await _volume_name(conn)
        await _run(
            conn,
            f"sudo docker run --rm -v {vol}:/v -v ~/{APP_DIR}/import:/in {_HELPER_IMAGE} sh -c "
            "'set -e; rm -f /v/olisar.db /v/olisar.db-wal /v/olisar.db-shm; "
            "cp /in/olisar.db /v/olisar.db; rm -rf /v/kb_uploads; "
            "if [ -d /in/kb_uploads ]; then cp -a /in/kb_uploads /v/kb_uploads; fi; "
            "chmod -R a+rwX /v'",
            timeout=600,
        )
        await _run(conn, f"cd ~/{APP_DIR} && sudo docker compose up -d", timeout=180)
    finally:
        conn.close()
