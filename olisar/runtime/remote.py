"""Drive a remote Olisar container over SSH (the "server shared hosting" mode).

The app generates its own SSH keypair once; the operator pastes the public key when
creating their cloud VM, so the private key never leaves this machine. With that we can,
with no terminal work from the operator:
  - install Docker + write the .env / compose file + start the container (`deploy`)
  - start/stop it later from the in-app control panel (`power`)
  - apply the newest release whenever this client is ahead of the VM (`autoupdate`)
  - read whether it's running, recent logs, and the public URL (`status`)

One VM can run several bots. Each is its own Docker Compose project in its own directory
(``~/olisar`` for the first, ``~/olisar-<profile id>`` for the rest), so each has its own
``.env``, container, data volume and Tailscale node — the same isolation the desktop app gives
bots that run locally. A deploy finds its directory by which Discord application an install
belongs to, so redeploying a bot replaces it and deploying a different one sits alongside.

Host-key checking is disabled: the target is the operator's own freshly-created VM,
addressed by IP, so there's no prior known-hosts entry to pin.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import sys
from pathlib import Path

import asyncssh
from sqlalchemy import select

from olisar import runtime_config, updates
from olisar.db.engine import session_scope
from olisar.db.models import AppConfig
from olisar.updates import UNKNOWN_VERSION, current_version
from olisar.versioning import display, is_newer, same_version

log = logging.getLogger("olisar.remote")

APP_DIR = "olisar"          # ~/olisar holds the first bot's .env + docker-compose.yml
# Every other bot on the same VM gets ``~/olisar-<suffix>``. The name reaches shell commands,
# so anything that doesn't match this is refused rather than quoted.
_APP_DIR_RE = re.compile(r"^olisar(-[a-z0-9]{1,32})?$")
CONNECT_TIMEOUT = 20        # seconds to establish the SSH connection
KEEPALIVE_INTERVAL = 15     # seconds between SSH keepalives (4 unanswered = dead)
# Seconds to wait for a recreated container to pass its first healthcheck. The funnel alone
# may take 100 before the backend serves, and the check runs every 30.
SETTLE_TIMEOUT = 240
_TSNET_RE = re.compile(r"https://[\w.-]+\.ts\.net")

# The container mounts the olisar-data volume here, so the VM's DB + uploads live at these
# paths (used by the cross-host data migration; see olisar.runtime.migrate).
VM_DATA_DIR = "/var/lib/olisar"
VM_DB = f"{VM_DATA_DIR}/olisar.db"
VM_KB = f"{VM_DATA_DIR}/kb_uploads"
_HELPER_IMAGE = "alpine"    # tiny image to read/write the named volume while stopped

# The one file the app owns on the VM. It's (re)installed on every deploy AND every
# connect, so a VM set up by an older client picks up the current script instead of
# silently drifting — it used to be written once at deploy and never again.
# `.env` is deliberately NOT managed: it holds secrets the operator may have edited.
UPDATE_SCRIPT = "olisar-update.sh"

# systemd units an older client installed alongside it, to run that script on a daily
# timer. Updates are the client's job now (see ``autoupdate``), so these are removed
# wherever they're still armed — otherwise a VM keeps a second, invisible updater that
# can move it to a release the operator's app knows nothing about.
_RETIRED_UNITS = ("olisar-update.timer", "olisar-update.service")

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


def valid_app_dir(name: str) -> bool:
    return bool(_APP_DIR_RE.match(name or ""))


def app_dir_of(cfg: AppConfig | None) -> str:
    """The VM directory this bot's install lives in. Blank — every VM set up before one could
    host several bots — is the original ``~/olisar``."""
    return app_dir_of_name(getattr(cfg, "server_app_dir", "") if cfg is not None else "")


def app_dir_of_name(name: str | None) -> str:
    name = (name or "").strip()
    return name if valid_app_dir(name) else APP_DIR


def _own_app_dir() -> str:
    """The directory a bot claims on a VM whose ``~/olisar`` already belongs to another bot."""
    raw = (os.environ.get("OLISAR_PROFILE_ID") or "").lower()
    suffix = re.sub(r"[^a-z0-9]", "", raw)[:32] or secrets.token_hex(4)
    return f"{APP_DIR}-{suffix}"


def parse_env(text: str) -> dict[str, str]:
    """``KEY=value`` lines of a ``.env`` (comments and blanks skipped)."""
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


# ── which installs a VM already has ──────────────────────────────────────────────

_INSTALLS_SCRIPT = r"""set +e
for d in ~/olisar ~/olisar-*; do
  [ -d "$d" ] || continue
  [ -f "$d/.env" ] || [ -f "$d/docker-compose.yml" ] || continue
  cid=$(grep -m1 '^DISCORD_CLIENT_ID=' "$d/.env" 2>/dev/null | cut -d= -f2- | tr -d "\r\"' ")
  node=$(grep -m1 '^OLISAR_FUNNEL_HOSTNAME=' "$d/.env" 2>/dev/null | cut -d= -f2- | tr -d "\r\"' ")
  compose=0; [ -f "$d/docker-compose.yml" ] && compose=1
  owner=$(tr -dc 'a-f0-9' < "$d/.olisar-owner" 2>/dev/null | head -c 64)
  printf '__OLISAR_INSTALL__|%s|%s|%s|%s|%s\n' "$(basename "$d")" "$cid" "$node" "$compose" "$owner"
done
"""


def parse_installs(out: str) -> list[dict]:
    """Turn the installs scan into ``[{dir, client_id, node, compose, owner}]``. Pure, for the
    tests."""
    installs: list[dict] = []
    for line in (out or "").splitlines():
        if not line.startswith("__OLISAR_INSTALL__|"):
            continue
        parts = (line.split("|") + [""] * 5)[1:6]
        name, client_id, node, compose, owner = (p.strip() for p in parts)
        if valid_app_dir(name):
            installs.append({
                "dir": name, "client_id": client_id, "node": node, "compose": compose == "1",
                "owner": owner,
            })
    return installs


def owner_of(cfg: AppConfig | None) -> str:
    """Which bot an install belongs to, as recorded on the VM (``.olisar-owner``): a digest of
    the bot's SSH public key. The key is the one thing about a bot that survives a reset — its
    Discord application may not — and every bot has its own."""
    pub = ((getattr(cfg, "server_ssh_pubkey", "") or "") if cfg is not None else "").strip()
    return hashlib.sha256(pub.encode()).hexdigest()[:32] if pub else ""


async def _mark_owner(conn, app_dir: str, owner: str) -> None:
    if owner:  # hex only, so it's safe in the command
        await _run(conn, f"printf '%s' {owner} > ~/{app_dir}/.olisar-owner", timeout=30)


async def _list_installs(conn) -> list[dict]:
    r = await asyncio.wait_for(conn.run("bash -s", input=_INSTALLS_SCRIPT, check=False), timeout=30)
    return parse_installs(r.stdout or "")


def choose_app_dir(installs: list[dict], *, client_id: str, own: str, owner: str = "") -> str:
    """Where a deploy of the Discord application ``client_id`` goes on a VM with ``installs``.

    Pure, because it's the whole difference between "redeploy this bot" and "add a bot next
    to that one", and getting it backwards either runs one bot twice or overwrites another's
    configuration:
      1. this bot's own install (its ``owner`` mark) — replace it, even if the bot has been
         reset onto a different Discord application since
      2. an install of this same application — replace it (a redeploy, or a retry of one
         that failed partway, or one set up before installs were marked)
      3. ``~/olisar`` if nothing is there — a VM with one bot looks exactly as it always has
      4. otherwise this bot's own ``~/olisar-<id>``
    """
    if owner:
        for install in installs:
            if install.get("owner") == owner:
                return install["dir"]
    if client_id:
        for install in installs:
            if install.get("client_id") == client_id:
                return install["dir"]
    if not any(i.get("dir") == APP_DIR for i in installs):
        return APP_DIR
    return own


def distinct_node(node: str, taken: set[str], app_dir: str) -> str:
    """A Tailscale device name no other bot on this VM already uses. Two nodes asking for one
    name would both get it, suffixed by Tailscale in whatever order they came up — so the
    address each console ends up at wouldn't be predictable."""
    base = (node or "olisar").strip() or "olisar"
    if base not in taken:
        return base
    suffix = app_dir.split("-", 1)[1] if "-" in app_dir else "2"
    return f"{base}-{suffix}"


def _set_env_line(env_text: str, key: str, value: str) -> str:
    lines = [ln for ln in (env_text or "").splitlines() if not ln.strip().startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    return "\n".join(lines)


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
        # A silently-dead peer (the VM rebooted, a NAT dropped the flow) would otherwise
        # leave a `conn.run` waiting on the OS TCP timeout — tens of minutes, during which
        # an automatic update holds the panel in "Updating…". Keepalives turn that into a
        # raised ConnectionLost in about a minute.
        keepalive_interval=KEEPALIVE_INTERVAL, keepalive_count_max=4,
    )


async def _run(conn, cmd: str, *, timeout: float = 180.0) -> str:
    """Run one command, returning combined stdout+stderr; raises on non-zero exit."""
    r = await asyncio.wait_for(conn.run(cmd, check=False), timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if r.exit_status != 0:
        raise RuntimeError(f"`{cmd.splitlines()[0]}…` failed ({r.exit_status}):\n{out.strip()[-800:]}")
    return out


async def _read_json(conn, path: str, *, timeout: float = 30.0) -> dict:
    """Read a small JSON file off the VM, or ``{}`` if it's missing or malformed. Bounded
    like every other remote call: this one runs inside the automatic update's "in flight"
    window, and a read that never returns would strand the panel there."""
    r = await asyncio.wait_for(conn.run(f"cat {path} 2>/dev/null || true", check=False), timeout)
    try:
        parsed = json.loads((r.stdout or "").strip() or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _install_managed(conn, app_dir: str) -> None:
    """Install/refresh the update script in ``~/<app_dir>``, and retire the old daily timer.

    Idempotent, and run on connect as well as deploy — this is what brings a VM that an
    older client set up onto the current layout."""
    await _run(conn, f"mkdir -p ~/{app_dir}", timeout=30)
    await asyncio.wait_for(
        conn.run(f"cat > ~/{app_dir}/{UPDATE_SCRIPT}", input=_asset(UPDATE_SCRIPT), check=True),
        timeout=60,
    )
    await _run(conn, f"chmod +x ~/{app_dir}/{UPDATE_SCRIPT}", timeout=30)
    await _retire_timer(conn, app_dir)


async def _retire_timer(conn, app_dir: str) -> None:
    """Disable and delete the systemd update timer an older client installed.

    Best-effort: a host without systemd never had one, and a VM that keeps it doesn't
    break — it just updates on a schedule nobody asked for any more."""
    units = " ".join(f"/etc/systemd/system/{unit}" for unit in _RETIRED_UNITS)
    try:
        await _run(
            conn,
            "if command -v systemctl >/dev/null 2>&1; then "
            "  sudo systemctl disable --now olisar-update.timer >/dev/null 2>&1 || true; "
            f"  sudo rm -f {units}; "
            "  sudo systemctl daemon-reload || true; "
            "fi; "
            f"rm -f ~/{app_dir}/olisar-update.timer ~/{app_dir}/olisar-update.service",
            timeout=90,
        )
    except Exception as exc:  # noqa: BLE001 — a leftover timer must not fail a deploy
        log.warning("could not retire the VM's update timer: %s", exc)


async def deploy(host: str, user: str, env_text: str) -> dict:
    """Install Docker and the updater, write the .env, then let the updater put the newest
    release on the VM and start it. On success, persist the connection and switch the app
    into server-hosting mode.

    The install goes wherever ``choose_app_dir`` says: over this bot's own install if the VM
    has one, else alongside whatever other bots are there. Returns ``app_dir`` so a move can
    load data into the same place."""
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
        installs = await _list_installs(conn)
        env = parse_env(env_text)
        owner = owner_of(await _load())
        app_dir = choose_app_dir(
            installs, client_id=env.get("DISCORD_CLIENT_ID", ""), own=_own_app_dir(), owner=owner,
        )
        taken = {i["node"] for i in installs if i["dir"] != app_dir and i.get("node")}
        node = distinct_node(env.get("OLISAR_FUNNEL_HOSTNAME", ""), taken, app_dir)
        if node != env.get("OLISAR_FUNNEL_HOSTNAME"):
            env_text = _set_env_line(env_text, "OLISAR_FUNNEL_HOSTNAME", node)
        others = [i["dir"] for i in installs if i["dir"] != app_dir]
        if others:
            log_lines.append(f"This server already runs {len(others)} other bot(s) — adding this one in ~/{app_dir}.")
        log_lines.append("Writing configuration…")
        # File bodies go over stdin (via `input=`), so secrets never appear in the VM's
        # process list / shell history the way an inline command would.
        await _run(conn, f"mkdir -p ~/{app_dir}", timeout=30)
        await conn.run(f"cat > ~/{app_dir}/.env", input=env_text, check=True)
        await _run(conn, f"chmod 600 ~/{app_dir}/.env", timeout=30)
        await _mark_owner(conn, app_dir, owner)
        await _install_managed(conn, app_dir)
        # A first deploy and an update are the same code path — the script resolves the
        # newest release, pins its digest into the compose file, starts it, and rolls back
        # if it doesn't pass its healthcheck. Nothing here duplicates that logic.
        log_lines.append("Pulling the latest Olisar release and starting it…")
        tag = await _target()
        pin = f" --tag {shlex.quote(tag)}" if tag else ""
        # Long enough to wait out another bot's update on the same VM (the script serialises
        # them) and then run this one.
        out = await _run(conn, f"bash ~/{app_dir}/{UPDATE_SCRIPT} --start{pin}", timeout=1500)
        log_lines.append(out.strip()[-2000:])
        # The script only returns once the container is healthy, and the backend publishes
        # whether its funnel came up before it answers that check. A refused Tailscale key
        # leaves the bot running with no console address, which used to read as a success.
        console_error = ""
        try:
            console_error = (await _probe(conn, app_dir))["console_error"]
        except Exception as exc:  # noqa: BLE001 — the control panel reads it again
            log.warning("couldn't read the new install's state: %s", exc)
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {"ok": False, "error": str(exc), "log": "\n".join(log_lines)}
    conn.close()
    await runtime_config.save(
        server_host=host, server_ssh_user=user, server_app_dir=app_dir,
        hosting_mode="server", configured=True,
        # The VM is on the newest release as of this build, so the launch after this one
        # has nothing to reconcile (see ``autoupdate``).
        server_synced_version=current_version(),
    )
    await runtime_config.session_secret()
    # Still ``ok``: the bot is installed and running, and saved as this app's server, so the
    # control panel can replace the key. The wizard shows ``console_error`` instead of moving on.
    return {"ok": True, "app_dir": app_dir, "console_error": console_error, "log": "\n".join(log_lines)}


async def connect(host: str, user: str, app_dir: str = "") -> dict:
    """Adopt a VM that's ALREADY running Olisar (deployed elsewhere, set up by hand, or
    before a reinstall of this app): find the install over SSH, then persist the connection
    and switch to server-hosting mode — no install, no config overwrite. The app's public key
    must already be in the VM's authorized_keys.

    A VM running several bots needs to be told which one this is: pass ``app_dir``, or get
    back ``choose`` — the installs, named — for the operator to pick from."""
    host = (host or "").strip()
    user = (user or "").strip() or "ubuntu"
    app_dir = (app_dir or "").strip()
    if not host:
        return {"ok": False, "error": "Enter the VM's public IP address."}
    if app_dir and not valid_app_dir(app_dir):
        return {"ok": False, "error": "That isn't an Olisar install directory."}
    try:
        conn = await _connect(host, user)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM over SSH: {exc}"}
    try:
        installs = [i for i in await _list_installs(conn) if i["compose"]]
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {"ok": False, "error": str(exc)}
    if not installs:
        conn.close()
        return {
            "ok": False,
            "error": "No Olisar install found on this VM — deploy it first, or check the IP "
            "and that the SSH key was added.",
        }
    cfg = await _load()
    owner = owner_of(cfg)
    if not app_dir:
        remembered = (getattr(cfg, "server_app_dir", "") or "") if cfg is not None else ""
        mine = [i["dir"] for i in installs if owner and i.get("owner") == owner]
        if len(mine) == 1:
            app_dir = mine[0]  # the one this bot deployed
        elif any(i["dir"] == remembered for i in installs):
            app_dir = remembered  # the one this bot ran as before a reset
        elif len(installs) == 1:
            app_dir = installs[0]["dir"]
        else:
            conn.close()
            return {
                "ok": False,
                "error": "This server runs more than one bot. Pick which one this is.",
                "choose": await _name_installs(installs),
            }
    elif not any(i["dir"] == app_dir for i in installs):
        conn.close()
        return {"ok": False, "error": f"No Olisar install in ~/{app_dir} on this VM."}
    # Reconcile the files we own so a VM deployed by an older client picks up the current
    # update script, and the install is marked as this bot's. `.env` is never touched — the
    # operator's secrets live there.
    try:
        await _mark_owner(conn, app_dir, owner)
        await _install_managed(conn, app_dir)
    except Exception as exc:  # noqa: BLE001 — adoption must still succeed
        log.warning("could not reconcile managed files on %s: %s", host, exc)
    conn.close()
    await runtime_config.save(
        server_host=host, server_ssh_user=user, server_app_dir=app_dir,
        hosting_mode="server", configured=True,
        # The stamp describes a *particular* VM (``_apply_update`` only writes it while the
        # app is still pointed at the one it updated), and this is a different one — or the
        # same one reset behind our back, which is what Reconnect is for. Either way what we
        # last reconciled says nothing about what's here now, so clear it rather than let
        # ``decide`` read it as "this build has already had its go at this server".
        server_synced_version="",
    )
    await runtime_config.session_secret()
    # A VM we've just adopted may be behind this build — bring it up without making the
    # operator go looking for a button. In the background: adoption shouldn't wait on an
    # image pull, and the panel reports the update through ``auto_updating``.
    spawn_autoupdate()
    return {"ok": True, "app_dir": app_dir}


async def _name_installs(installs: list[dict]) -> list[dict]:
    """Label each install with its Discord application's name, for the operator to pick
    from. The public RPC endpoint needs no token; a failed lookup falls back to the device
    name and directory, which the operator also chose."""
    import aiohttp

    async def one(session, install: dict) -> dict:
        name = ""
        cid = install.get("client_id") or ""
        if cid.isdigit():
            try:
                url = f"https://discord.com/api/v10/applications/{cid}/rpc"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        name = str((await resp.json()).get("name") or "")
            except Exception:  # noqa: BLE001 — a label, not a requirement
                name = ""
        return {"dir": install["dir"], "name": name or install.get("node") or install["dir"]}

    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return list(await asyncio.gather(*(one(session, i) for i in installs)))


# ── sharing this bot's VM with another bot ──────────────────────────────────────
# A second bot on the same VM needs nothing new from the operator: the app authorizes the
# new bot's own SSH key with this bot's connection, and hands over what the new install
# should reuse (the host, the Tailscale key, who may sign in).

_PUBKEY_RE = re.compile(r"^ssh-(ed25519|rsa) [A-Za-z0-9+/=]{16,}( [A-Za-z0-9@._-]{0,64})?$")


# The VM's bot token, read once from its .env and kept for this process: the app hands the
# token to the VM when it deploys and keeps no copy, and checking the VM's sign-in address
# against the Discord app needs it. Keyed by host and install, so a reconnect elsewhere
# reads again.
_vm_tokens: dict[str, str] = {}


async def _vm_token(cfg: AppConfig) -> str:
    key = f"{cfg.server_host}/{app_dir_of(cfg)}"
    if key not in _vm_tokens:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
        try:
            env = parse_env(await _run(conn, f"cat ~/{app_dir_of(cfg)}/.env 2>/dev/null || true", timeout=30))
        finally:
            conn.close()
        if env.get("DISCORD_TOKEN"):
            _vm_tokens[key] = env["DISCORD_TOKEN"]
    return _vm_tokens.get(key, "")


async def discord_check(public_url: str = "") -> dict:
    """What Discord says about the server's bot, which nothing on the VM reports: whether
    its console's sign-in address is registered, and whether its intents are on.

    Discord login there redirects to ``<public_url>/auth/callback`` and is refused unless the
    app lists it; nothing registers it for the operator (the API ignores ``redirect_uris``).
    A bot whose intents are off is refused by Discord outright, while the container still
    reads as running and healthy."""
    from olisar import discord_app

    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "This bot isn't running on a server."}
    url = (public_url or "").rstrip("/")
    redirect = f"{url}/auth/callback" if url.startswith("https://") else ""
    try:
        token = await _vm_token(cfg)
        if not token:
            return {"ok": False, "error": "Couldn't read the bot token on the server."}
        app = await discord_app.inspect(token)
    except Exception as exc:  # noqa: BLE001 (SSH or Discord; either way the panel just can't tell)
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    return {
        "ok": True,
        "app_id": app["id"],
        "redirect": redirect,
        "added": bool(redirect) and redirect in app["redirect_uris"],
        "intents_missing": app["intents_missing"],
    }


async def reconnect() -> dict:
    """Turn the server bot's missing intents on, where Discord lets the app do that itself,
    and restart its container so the bot connects with them. Any intents left over come
    back in ``intents_missing``, for the operator to switch on in the Developer Portal."""
    from olisar import discord_app

    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "No server configured yet."}
    try:
        token = await _vm_token(cfg)
        app = await discord_app.prepare(token)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    if app["intents_missing"]:
        return {"ok": False, "intents_missing": app["intents_missing"], "app_id": app["id"]}
    return {**(await power("restart")), "intents_missing": []}


async def share_info() -> dict:
    """What another bot needs to deploy onto this bot's VM."""
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "This bot isn't running on a server."}
    user = cfg.server_ssh_user or "ubuntu"
    try:
        conn = await _connect(cfg.server_host, user)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM: {exc}"}
    try:
        env = parse_env(await _run(conn, f"cat ~/{app_dir_of(cfg)}/.env 2>/dev/null || true", timeout=30))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()
    # Not its Tailscale key: that one has already joined the VM's first node, and a key that
    # was single-use or has since expired can't join another. Each bot brings its own.
    return {
        "ok": True,
        "host": cfg.server_host,
        "user": user,
        "admin_allowlist": env.get("ADMIN_ALLOWLIST", ""),
    }


_TSKEY_RE = re.compile(r"^tskey-[A-Za-z0-9_-]+$")


async def set_tunnel_key(key: str) -> dict:
    """Put a new Tailscale auth key in this bot's ``.env`` on the VM and recreate its
    container on it, then report whether the console came up: ``{ok, url}`` or
    ``{ok: False, error}``.

    Recreated, not restarted: a restarted container keeps the environment it was created
    with, dead key included. A node that joined before keeps its identity in the data
    volume and never reads the key again, so this only changes anything for one that hasn't."""
    key = (key or "").strip()
    if not _TSKEY_RE.match(key):
        return {"ok": False, "error": "That isn't a Tailscale auth key. They start with tskey-."}
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "No server configured yet."}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM: {exc}"}
    app_dir = app_dir_of(cfg)
    try:
        async with _gate:  # not while an update is recreating the same container
            env_text = await _run(conn, f"cat ~/{app_dir}/.env", timeout=30)
            # Over stdin, like deploy, so the key never shows in the VM's process list.
            await asyncio.wait_for(
                conn.run(
                    f"cat > ~/{app_dir}/.env",
                    input=_set_env_line(env_text, "TAILSCALE_AUTH", key) + "\n",
                    check=True,
                ),
                timeout=30,
            )
            await _run(conn, f"chmod 600 ~/{app_dir}/.env", timeout=30)
            await _run(conn, f"cd ~/{app_dir} && sudo docker compose up -d --force-recreate", timeout=180)
            probe = await _settled_probe(conn, app_dir)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()
    if probe["url"]:
        return {"ok": True, "url": probe["url"]}
    if probe["health"] == "starting":
        return {"ok": False, "error": "The server is still starting. Check back in a minute."}
    return {"ok": False, "error": probe["console_error"] or "The server didn't come back up."}


async def authorize_key(pubkey: str) -> dict:
    """Add another bot's SSH public key to this bot's VM, so that bot can deploy there and
    drive its own install without the operator touching the VM."""
    key = " ".join((pubkey or "").split())
    if not _PUBKEY_RE.match(key):
        return {"ok": False, "error": "That isn't an SSH public key."}
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "This bot isn't running on a server."}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM: {exc}"}
    # The key matched a pattern with no quotes or shell metacharacters, so single quotes
    # hold it safely.
    script = (
        "set -e; umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
        f"grep -qxF '{key}' ~/.ssh/authorized_keys || printf '%s\\n' '{key}' >> ~/.ssh/authorized_keys"
    )
    try:
        await _run(conn, script, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()
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
  *ts.net*|*tunnel_error*) : ;;
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


def _probe_script(app_dir: str = APP_DIR) -> str:
    return (
        _PROBE_TEMPLATE.replace("@APP_DIR@", app_dir)
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
    tunnel_error = str(published.get("tunnel_error") or "")
    url = str(published.get("public_url") or "").rstrip("/")
    if not url and not tunnel_error:  # pre-state.json container — the old log-scrape path
        url_lines = sec.get("URL", "").splitlines()
        url = url_lines[0].strip() if url_lines else ""
    if not url and not tunnel_error:
        m = _TSNET_RE.search(logs)
        url = m.group(0) if m else ""
    # Only a funnel address reaches the console. With the funnel down the backend publishes
    # its own loopback origin, which "Open console" used to open on this machine instead.
    if tunnel_error or not url.startswith("https://"):
        url = ""

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
        # Why a running server's console has no address. Also set for an image from before
        # the backend published a reason, which only left its loopback origin behind.
        "console_error": tunnel_problem(tunnel_error) if running and not url else "",
        "logs": logs.strip()[-4000:],
    }


def tunnel_problem(reason: str) -> str:
    """Why the console has no address, from the reason the funnel gave. Tailscale reports a
    refused auth key as ``invalid key: …`` whatever the cause; that's the case the operator
    can fix from the panel, so it gets said plainly."""
    reason = " ".join((reason or "").split())
    if "invalid key" in reason.lower():
        return "Tailscale rejected the auth key: it has expired, was revoked, or was already used. Use a new one."
    if reason:
        return f"Tailscale couldn't connect: {reason}"
    return "Tailscale didn't connect."


async def _probe(conn, app_dir: str) -> dict:
    """Run the probe over an open connection and parse it. Raises on a failed probe."""
    r = await asyncio.wait_for(
        conn.run("bash -s", input=_probe_script(app_dir), check=False), timeout=45
    )
    out = (r.stdout or "") + (r.stderr or "")
    if r.exit_status not in (0, None):
        raise RuntimeError(f"status probe failed ({r.exit_status}):\n{out.strip()[-800:]}")
    return parse_probe(out)


async def _settled_probe(conn, app_dir: str) -> dict:
    """Probe once the container has finished starting. The data volume keeps the previous
    boot's state.json until the new backend replaces it, just before it starts answering
    its healthcheck, so a probe taken while the health is still "starting" reads the old
    boot's funnel outcome."""
    deadline = asyncio.get_running_loop().time() + SETTLE_TIMEOUT
    while True:
        probe = await _probe(conn, app_dir)
        if probe["health"] != "starting" or asyncio.get_running_loop().time() >= deadline:
            return probe
        await asyncio.sleep(3)


async def last_update() -> dict:
    """What the VM's last update attempt did — including one this app started at launch and
    the operator never watched, which is the only way they'd learn how it went."""
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception:  # noqa: BLE001 — informational only
        return {}
    try:
        return await _read_json(conn, f"~/{app_dir_of(cfg)}/last-update.json")
    finally:
        conn.close()


async def _target() -> str | None:
    """The release the VM should run: the newest on this app's update channel, so a beta
    tester's server gets the betas too. The VM's script can find a release by itself, but
    only GitHub's latest, which is always stable. ``None`` when GitHub couldn't be asked or
    has nothing on the channel."""
    try:
        rel = await updates.newest_release()
    except Exception as exc:  # noqa: BLE001 — the caller decides what "unknown" means
        log.warning("couldn't find the newest %s release: %s", updates.channel(), exc)
        return None
    return rel["tag"] if rel else None


def hold(*, target: str | None, server: str, channel: str) -> dict | None:
    """Why the VM's update script must not run, as the result it would have reported, or
    ``None`` to run it. The script pins whatever release it's handed, so both of these
    would move a server backwards:

    - a beta app that couldn't resolve its release: with no ``--tag`` the script falls back
      to GitHub's latest release, which is stable
    - a server already past the target, which is where switching from beta to stable
      leaves it until the next stable release overtakes the beta it's on
    """
    if not target:
        if channel == "beta":
            return {"ok": False, "status": "no-release", "message": "couldn't find the newest beta on GitHub"}
        return None
    if server and is_newer(server, target):
        return {
            "ok": True,
            "status": "server-ahead",
            "message": f"the server is on {display(server)}, which is newer than {display(target)}",
            "tag": target,
        }
    return None


async def _apply_update(conn, host: str, app_dir: str, server_version: str) -> dict:
    """Run the VM's update script over an open connection and report what it did.

    ``host`` and ``app_dir`` are the install this connection belongs to, so the outcome is
    recorded against the right bot. ``server_version`` is what the VM reports running.
    """
    tag = await _target()
    result = hold(target=tag, server=server_version, channel=updates.channel())
    out = ""
    script_ok = False
    if result is None:
        # Always this build's script: one an older client left behind may predate the lock
        # that keeps two bots on the same VM from updating at once.
        await _install_managed(conn, app_dir)
        pin = f" --tag {shlex.quote(tag)}" if tag else ""
        # Long enough to wait out another bot's update on the same VM (the script serialises
        # them) and then run this one.
        r = await asyncio.wait_for(
            conn.run(f"bash ~/{app_dir}/{UPDATE_SCRIPT}{pin}", check=False), timeout=2400
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        script_ok = r.exit_status == 0
        result = await _read_json(conn, f"~/{app_dir}/last-update.json")
    state = await _probe(conn, app_dir)

    # Stamp the build that reconciled this VM, so ``decide`` doesn't keep repeating a run
    # that already said its piece — a retry belongs to a *newer* client build, not to every
    # launch of this one. Skipped when the run decided nothing (see ``decided``), because
    # then the next launch genuinely should try again. Only if the app is still pointed at
    # this VM — the operator may have switched bots while we ran, and the config we'd be
    # writing then belongs to someone else's server.
    if decided(result):
        try:
            current = await _load()
            if current and current.server_host == host and app_dir_of(current) == app_dir:
                await runtime_config.save(server_synced_version=current_version())
        except Exception as exc:  # noqa: BLE001 — the update itself already happened
            log.warning("could not record the synced version: %s", exc)

    ok = bool(result.get("ok")) if result else script_ok
    applied = {
        "ok": ok,
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
        applied["error"] = result.get("message") or "The update did not complete."
    return applied


# ── automatic updates ───────────────────────────────────────────────────────────
# The VM used to update itself on a daily systemd timer, because the app was the only
# trigger and an operator who rarely opened it left their server months behind. That traded
# one drift for another: the timer moved a server onto a release nobody had asked for, up to
# a day after the fact, and the app could sit at a different version the whole time.
#
# So the client drives it. It is the side that knows a release exists — it just installed
# one on itself — and the moment it comes up ahead of the VM is exactly the moment the two
# should be brought back together.

_auto: dict = {"running": False, "reason": ""}
_tasks: set[asyncio.Task] = set()
_gate = asyncio.Lock()  # one reconcile at a time — a boot and an adopt can land together


# Statuses ``olisar-update.sh`` emits when it never reached a release at all: GitHub or
# GHCR was unreachable, so nothing about this VM was settled and the next launch should try
# again. Every other status is an answer — applied, already current, staged onto a stopped
# server, rolled back — and running the script again from the same build only repeats it.
_UNDECIDED_STATUSES = frozenset({"no-release", "pull-failed", "no-digest"})


def decided(result: dict) -> bool:
    """Whether an update run actually settled what this VM is on.

    The test for stamping ``server_synced_version``, which is in turn what stops ``decide``
    repeating a run — so the distinction it draws is "did we learn anything", not "did it
    succeed". A rollback is a firm answer; a VM we couldn't fetch a release for is not.
    """
    status = str(result.get("status") or "")
    return bool(status) and status not in _UNDECIDED_STATUSES


def decide(*, client: str, server: str, synced: str) -> str:
    """Why the VM should be updated right now, or ``""`` to leave it alone.

    ``client``  the version of this build        ``server``  the version the VM is running
    ``synced``  the build that last reconciled this VM ("" = never)

    Pure, because the triggers are the whole feature: an app that quietly reinstalls a
    release on someone's server needs its reasons to be readable and tested.
    """
    if not client or same_version(client, UNKNOWN_VERSION):
        return ""  # a build that can't tell what it is has no business moving a server
    # This build already had its go at this VM, and the script it runs is deterministic —
    # so whatever that run settled on (applied, already current, staged onto a stopped
    # server, rolled back), running it again now would settle on the same thing. Wait until
    # the app itself moves forward. Without this the reasons below never converge whenever
    # the script can't raise the version the VM *reports*: a stopped server is repinned but
    # deliberately left down, so it keeps reporting the image it last ran, and every launch
    # would re-run the script, re-lock the panel, and re-pull.
    if synced and not is_newer(client, synced):
        return ""
    if server:
        # The VM's version is readable, so it answers the question on its own.
        return "client-ahead" if is_newer(client, server) else ""
    # No readable version (a container that has never started, or an image from before the
    # OCI labels). Fall back to the app's own history: this build is newer than the one that
    # last reconciled the VM, which is what a relaunch after a self-update looks like.
    return "relaunched" if synced else ""


def spawn_autoupdate() -> None:
    """Kick off ``autoupdate`` in the background. Fire-and-forget by design: it is an SSH
    round trip and possibly a multi-minute image pull, and nothing local waits on it. The
    task is held in a module-level set so it can't be garbage-collected mid-flight."""
    task = asyncio.create_task(autoupdate(), name="olisar-server-autoupdate")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def autoupdate() -> dict:
    """Bring the VM onto the newest release when this client has moved ahead of it.

    Runs at backend startup and after adopting a VM — so the app relaunching onto a newer
    build (which is what every self-update ends in) carries the server along with it, and a
    server found behind is caught the next time the app opens. Never raises: a VM we
    couldn't reach, or couldn't fetch a release for, is simply retried on the next launch
    (neither leaves a ``server_synced_version`` stamp — see ``decided``).
    """
    async with _gate:
        return await _reconcile()


async def _reconcile() -> dict:
    """One pass: read what the VM is running, decide, and apply the release if there's a
    reason to. Serialised by ``_gate`` so two triggers can't pull into the same VM at once."""
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"skipped": "no-server"}
    client = current_version()
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        log.info("auto-update: %s unreachable (%s) — retrying on the next launch", cfg.server_host, exc)
        return {"skipped": "unreachable"}
    app_dir = app_dir_of(cfg)
    try:
        state = await _probe(conn, app_dir)
        reason = decide(
            client=client,
            server=state.get("version") or "",
            synced=cfg.server_synced_version or "",
        )
        if not reason:
            return {"skipped": "up-to-date", "version": state.get("version") or ""}
        log.info(
            "auto-update (%s): this app is v%s, the server is v%s — applying the newest release",
            reason, display(client), display(state.get("version")) or "unknown",
        )
        _auto.update(running=True, reason=reason)
        try:
            applied = await _apply_update(conn, cfg.server_host, app_dir, state.get("version") or "")
        finally:
            _auto.update(running=False, reason="")
    except Exception as exc:  # noqa: BLE001 — a failed update must not take the app down
        log.warning("auto-update failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()
    log.info("auto-update: %s", applied.get("message") or applied.get("status") or "done")
    return {**applied, "reason": reason}


async def power(action: str) -> dict:
    """Start (`up`), stop (`stop`) or `restart` the container on the stored VM.

    ``up`` boots whatever digest the compose file is pinned to — it deliberately does NOT
    pull. Start used to pull first, which meant an operator who stopped their bot for a
    week silently came back on a different version. Only ``autoupdate`` moves the release.
    """
    cfg = await _load()
    if not (cfg and cfg.server_host):
        return {"ok": False, "error": "No server configured yet."}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach the VM: {exc}"}
    app_dir = app_dir_of(cfg)
    try:
        if action == "up":
            await _run(conn, f"cd ~/{app_dir} && sudo docker compose up -d", timeout=180)
        elif action == "restart":
            await _run(conn, f"cd ~/{app_dir} && sudo docker compose restart", timeout=180)
        else:
            await _run(conn, f"cd ~/{app_dir} && sudo docker compose stop", timeout=120)
    except Exception as exc:  # noqa: BLE001
        conn.close()
        return {"ok": False, "error": str(exc)}
    conn.close()
    return {"ok": True, "running": action != "stop"}


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
    cmd = f"cd ~/{app_dir_of(cfg)} && sudo docker compose logs --tail {n} --no-color 2>/dev/null || true"
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
    # ``auto_updating`` rides along on every answer, including the failures: while an
    # automatic update recreates the container, the probe legitimately reads as stopped or
    # unreachable, and the panel would otherwise report that as a server that fell over.
    base = {"configured": True, "host": cfg.server_host, "auto_updating": _auto["running"]}
    try:
        conn = await _connect(cfg.server_host, cfg.server_ssh_user or "ubuntu")
    except Exception as exc:  # noqa: BLE001
        return {**base, "reachable": False, "error": str(exc)}
    try:
        probe = await _probe(conn, app_dir_of(cfg))
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


def pick_volume(names: list[str], app_dir: str) -> str:
    """The data volume of the compose project in ``~/<app_dir>`` among a VM's volumes.
    Compose names a project after its directory, so that project's volume is
    ``<app_dir>_olisar-data``. Pure, for the tests."""
    names = [n.strip() for n in names if n.strip()]
    exact = f"{app_dir}_olisar-data"
    if exact in names:
        return exact
    # A VM with only ever one install, whose volume predates this naming (a hand-edited
    # compose file): the old substring match, but only when it can't be someone else's.
    candidates = [n for n in names if n.endswith("olisar-data")]
    if app_dir == APP_DIR and len(candidates) == 1:
        return candidates[0]
    return ""


async def _volume_name(conn, app_dir: str) -> str:
    """The actual Docker volume backing ``~/<app_dir>``'s `olisar-data` (Compose prefixes
    it with the project name, e.g. `olisar_olisar-data`)."""
    out = await _run(conn, "sudo docker volume ls -q 2>/dev/null || true", timeout=30)
    name = pick_volume(out.splitlines(), app_dir)
    if not name:
        raise RuntimeError(f"couldn't find the data volume for ~/{app_dir} on the VM")
    return name


async def _sftp_exists(sftp, path: str) -> bool:
    try:
        await sftp.stat(path)
        return True
    except Exception:  # noqa: BLE001 — any stat failure means "treat as absent"
        return False


async def read_env(host: str, user: str, app_dir: str = APP_DIR) -> str:
    """The VM's `.env` text. For server-hosted bots the Discord creds + API keys live here
    (not in the local DB), so a move to local / another server reads them from here."""
    conn = await _connect(host, (user or "ubuntu").strip() or "ubuntu")
    try:
        return await _run(conn, f"cat ~/{app_dir}/.env 2>/dev/null || true", timeout=30)
    finally:
        conn.close()


async def export_data(host: str, user: str, dest_dir: Path, app_dir: str = APP_DIR) -> None:
    """Stop the VM's container and copy its data — `olisar.db` (+ any WAL/SHM sidecars) and
    `kb_uploads/` — into the local `dest_dir` over SFTP. Leaves the container stopped and the
    volume intact, so the VM remains a full backup."""
    conn = await _connect(host, (user or "ubuntu").strip() or "ubuntu")
    try:
        await _run(conn, f"cd ~/{app_dir} && sudo docker compose stop", timeout=120)
        vol = await _volume_name(conn, app_dir)
        await _run(conn, f"mkdir -p ~/{app_dir}/export && sudo rm -rf ~/{app_dir}/export/*", timeout=30)
        await _run(
            conn,
            f"sudo docker run --rm -v {vol}:/v -v ~/{app_dir}/export:/out {_HELPER_IMAGE} sh -c "
            "'set -e; for f in olisar.db olisar.db-wal olisar.db-shm; do "
            "if [ -f /v/$f ]; then cp /v/$f /out/$f; fi; done; "
            "if [ -d /v/kb_uploads ]; then cp -a /v/kb_uploads /out/kb_uploads; fi; "
            "chmod -R a+rwX /out'",
            timeout=600,
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        async with conn.start_sftp_client() as sftp:
            base = f"{await sftp.realpath('.')}/{app_dir}/export"
            for name in ("olisar.db", "olisar.db-wal", "olisar.db-shm"):
                if await _sftp_exists(sftp, f"{base}/{name}"):
                    await sftp.get(f"{base}/{name}", str(dest_dir / name))
            if await _sftp_exists(sftp, f"{base}/kb_uploads"):
                await sftp.get(f"{base}/kb_uploads", str(dest_dir / "kb_uploads"), recurse=True)
    finally:
        conn.close()


async def import_data(host: str, user: str, src_dir: Path, app_dir: str = APP_DIR) -> None:
    """Load a staged `olisar.db` (+ `kb_uploads/`) from `src_dir` into ``~/<app_dir>``'s data
    volume and (re)start the container. The compose file must already be present (deploy
    first). Removes any stale WAL/SHM so the replaced DB opens clean."""
    conn = await _connect(host, (user or "ubuntu").strip() or "ubuntu")
    try:
        await _run(conn, f"cd ~/{app_dir} && sudo docker compose stop", timeout=120)
        await _run(conn, f"mkdir -p ~/{app_dir}/import && sudo rm -rf ~/{app_dir}/import/*", timeout=30)
        async with conn.start_sftp_client() as sftp:
            base = f"{await sftp.realpath('.')}/{app_dir}/import"
            await sftp.put(str(src_dir / "olisar.db"), f"{base}/olisar.db")
            kb = src_dir / "kb_uploads"
            if kb.is_dir():
                await sftp.put(str(kb), f"{base}/kb_uploads", recurse=True)
        vol = await _volume_name(conn, app_dir)
        await _run(
            conn,
            f"sudo docker run --rm -v {vol}:/v -v ~/{app_dir}/import:/in {_HELPER_IMAGE} sh -c "
            "'set -e; rm -f /v/olisar.db /v/olisar.db-wal /v/olisar.db-shm; "
            "cp /in/olisar.db /v/olisar.db; rm -rf /v/kb_uploads; "
            "if [ -d /in/kb_uploads ]; then cp -a /in/kb_uploads /v/kb_uploads; fi; "
            "chmod -R a+rwX /v'",
            timeout=600,
        )
        await _run(conn, f"cd ~/{app_dir} && sudo docker compose up -d", timeout=180)
    finally:
        conn.close()
