"""Move a bot between hosts, carrying its data and keeping the old copy as a backup.

Operates on the **active** profile only (the API gates it there). The matrix, keyed by the
profile's current ``hosting_mode``:

  - **local → server**: deploy the target VM (config rebuilt from this profile's DB), then
    upload the local data into it. The local DB is left in place as the backup.
  - **server → local**: download the VM's data, read the VM's ``.env`` back for the Discord
    creds/keys (server-hosted bots keep those on the VM, not locally), run the bot here. The
    old local DB is renamed to ``*.pre-move.bak`` and the VM's volume is left intact — two
    backups.
  - **server → server, same IP**: no-op.
  - **server → server, different IP**: download the old VM's data + ``.env``, deploy + upload
    to the new VM. The old VM is stopped but kept as a backup.

Integrity: the source writer is always stopped first — a local bot via
``server.stop_all_supervisors`` + ``engine.reset_engine`` (which disposes the pool and
flushes the WAL into the ``.db``), a VM via ``docker compose stop`` — so the SQLite file is
self-contained before it's copied. ``_finalize_db`` also merges any leftover WAL and re-points
uploaded-doc paths at the destination's ``kb_uploads`` dir (the absolute path differs between
a VM at ``/var/lib/olisar`` and the local per-user data dir).

Kept separate from :mod:`olisar.runtime.remote` (SSH plumbing) and
:mod:`olisar.runtime.switch` (profile activation); heavy imports are deferred into functions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path

log = logging.getLogger("olisar.runtime.migrate")

# Guards against a concurrent/double move (operator double-click, two console tabs), like
# switch._switching. A move is a rare, heavy operator action, so a flag + error is enough.
_moving = False


def is_moving() -> bool:
    return _moving


# ── local file / sqlite helpers ─────────────────────────────────────────────────


def _finalize_db(db_path: str, old_kb: str, new_kb: str) -> None:
    """Merge any WAL into the main file (so the single ``.db`` is self-contained) and
    re-point uploaded-doc URIs from the source's ``kb_uploads`` dir to the destination's.
    Runs in a worker thread — plain stdlib ``sqlite3``, no vec extension needed (it only
    touches ``kb_source`` rows and the journal mode, never the vec0 virtual tables)."""
    con = sqlite3.connect(db_path, timeout=60)
    try:
        con.execute("PRAGMA journal_mode=DELETE")  # checkpoint the WAL, drop -wal/-shm
        old, new = old_kb.rstrip("/"), new_kb.rstrip("/")
        if old and new and old != new:
            con.execute(
                "UPDATE kb_source SET uri = ? || substr(uri, ?) "
                "WHERE type = 'doc' AND uri LIKE ?",
                (new, len(old) + 1, old + "/%"),
            )
        con.commit()
    finally:
        con.close()


def _copy_db_files(src_db: str, dest_dir: Path) -> None:
    """Copy ``olisar.db`` + any WAL/SHM sidecars into ``dest_dir`` (names preserved)."""
    src = Path(src_db)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(src) + suffix)
        if f.exists():
            shutil.copy2(f, dest_dir / (src.name + suffix))


def _copy_kb(src_kb: str, dest_kb: Path) -> None:
    """Merge uploaded-doc files from one ``kb_uploads`` dir into another (names preserved)."""
    src = Path(src_kb)
    if not src.is_dir():
        return
    dest_kb.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dest_kb / f.name)


def _backup_and_replace(local_db: str, new_db: Path) -> None:
    """Rename the current profile DB to ``*.pre-move.bak`` (backup) and move the freshly
    downloaded DB into its place, clearing any stale WAL/SHM so the new file opens clean."""
    dst = Path(local_db)
    if dst.exists():
        bak = Path(str(dst) + ".pre-move.bak")
        if bak.exists():
            bak.unlink()
        dst.replace(bak)
    for suffix in ("-wal", "-shm"):
        f = Path(str(dst) + suffix)
        if f.exists():
            f.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(new_db), str(dst))


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


# ── config snapshot / rebuild ────────────────────────────────────────────────────


def _g(obj: object, attr: str) -> str:
    return (getattr(obj, attr, "") or "") if obj is not None else ""


async def _snapshot_config() -> dict:
    """The active profile's config as stored in its DB. Used to build the VM env for a
    local→server move, and to keep the SSH keypair + session secret across a server→local
    move (a local bot's creds live here; a server bot's live in the VM's .env)."""
    from olisar.db.engine import session_scope
    from olisar.db.models import AppConfig, AppSecret

    async with session_scope() as s:
        c = await s.get(AppConfig, 1)
        k = await s.get(AppSecret, 1)
        return {
            "discord_token": _g(c, "discord_token"),
            "discord_client_id": _g(c, "discord_client_id"),
            "discord_client_secret": _g(c, "discord_client_secret"),
            "target_guild_id": int((c.target_guild_id or 0) if c else 0),
            "session_secret": _g(c, "session_secret"),
            "tunnel_node": _g(c, "tunnel_node"),
            "tunnel_token": _g(c, "tunnel_token"),
            "server_host": _g(c, "server_host"),
            "server_ssh_user": _g(c, "server_ssh_user") or "ubuntu",
            "server_ssh_pubkey": _g(c, "server_ssh_pubkey"),
            "server_ssh_privkey": _g(c, "server_ssh_privkey"),
            "gemini_api_key": _g(k, "gemini_api_key"),
            "cloudflare_account_id": _g(k, "cloudflare_account_id"),
            "cloudflare_api_token": _g(k, "cloudflare_api_token"),
            "uex_api_key": _g(k, "uex_api_key"),
        }


async def _allowlisted_ids() -> list[int]:
    from sqlalchemy import select

    from olisar.db.engine import session_scope
    from olisar.db.models import AdminUser

    async with session_scope() as s:
        rows = await s.scalars(
            select(AdminUser.discord_user_id).where(AdminUser.is_allowlisted.is_(True))
        )
        return [int(r) for r in rows]


def _build_env(snap: dict, allow: list[int]) -> str:
    """The VM ``.env`` for a local→server move — mirrors the setup wizard's deploy package
    (web/src/setup.tsx envFile), rebuilt from the local profile's stored config."""
    lines = [
        f"DISCORD_TOKEN={snap['discord_token']}",
        f"DISCORD_CLIENT_ID={snap['discord_client_id']}",
        f"DISCORD_CLIENT_SECRET={snap['discord_client_secret']}",
    ]
    if snap.get("target_guild_id"):
        lines.append(f"TARGET_GUILD_ID={snap['target_guild_id']}")
    if allow:
        lines.append("ADMIN_ALLOWLIST=" + ",".join(str(i) for i in allow))
    lines.append(f"GEMINI_API_KEY={snap['gemini_api_key']}")
    if snap.get("tunnel_token"):
        lines.append(f"TAILSCALE_AUTH={snap['tunnel_token']}")
    lines.append(f"OLISAR_FUNNEL_HOSTNAME={snap.get('tunnel_node') or 'olisar'}")
    if snap.get("cloudflare_account_id"):
        lines.append(f"CLOUDFLARE_ACCOUNT_ID={snap['cloudflare_account_id']}")
    if snap.get("cloudflare_api_token"):
        lines.append(f"CLOUDFLARE_API_TOKEN={snap['cloudflare_api_token']}")
    return "\n".join(lines)


async def _restore_config(snap: dict, env: dict[str, str]) -> None:
    """After a server→local move, write config into the freshly downloaded DB and flip it to
    local hosting. The VM's ``.env`` is authoritative for creds/keys (that's where a
    server-hosted bot kept them); the SSH keypair + session secret come from the old local DB."""
    from olisar import discord_app, runtime_config, runtime_keys
    from olisar.db.engine import session_scope
    from olisar.db.models import AppSecret

    def pick(env_key: str, snap_key: str) -> str:
        return (env.get(env_key) or "").strip() or (snap.get(snap_key) or "")

    try:
        gid = int((env.get("TARGET_GUILD_ID") or "").strip() or (snap.get("target_guild_id") or 0))
    except ValueError:
        gid = 0

    await runtime_config.save(
        discord_token=pick("DISCORD_TOKEN", "discord_token"),
        discord_client_id=pick("DISCORD_CLIENT_ID", "discord_client_id"),
        discord_client_secret=pick("DISCORD_CLIENT_SECRET", "discord_client_secret"),
        target_guild_id=gid,
        session_secret=snap.get("session_secret") or "",
        tunnel_token=pick("TAILSCALE_AUTH", "tunnel_token"),
        tunnel_node=pick("OLISAR_FUNNEL_HOSTNAME", "tunnel_node"),
        tunnel_hostname="",     # re-resolved if Remote access is enabled locally
        tunnel_enabled=False,   # loopback is enough for the desktop app; re-enable to publish
        public_base_url="",
        server_ssh_user=snap.get("server_ssh_user") or "ubuntu",
        server_ssh_pubkey=snap.get("server_ssh_pubkey") or "",
        server_ssh_privkey=snap.get("server_ssh_privkey") or "",
        hosting_mode="local",
        server_host="",
        configured=True,
    )
    async with session_scope() as s:
        row = await s.get(AppSecret, 1)
        if row is None:
            row = AppSecret(id=1)
            s.add(row)
        row.gemini_api_key = pick("GEMINI_API_KEY", "gemini_api_key")
        row.cloudflare_account_id = pick("CLOUDFLARE_ACCOUNT_ID", "cloudflare_account_id")
        row.cloudflare_api_token = pick("CLOUDFLARE_API_TOKEN", "cloudflare_api_token")
        row.uex_api_key = snap.get("uex_api_key") or ""
    runtime_config.invalidate()
    runtime_keys.invalidate()
    discord_app.invalidate()  # drop any cached OAuth app so the restored client_id is used


# ── orchestration ─────────────────────────────────────────────────────────────────


async def move(app, target: str, host: str, user: str) -> dict:
    """Move the active bot to ``target`` ('local' or 'server'). ``host``/``user`` name the
    destination VM for a server target. Returns ``{ok, hosting_mode, host?, note?/log?, error?}``.
    """
    global _moving
    from olisar import runtime_config

    target = (target or "").strip()
    host = (host or "").strip()
    if target not in ("local", "server"):
        return {"ok": False, "error": "Target must be 'local' or 'server'."}

    cur_mode = await runtime_config.hosting_mode()
    snap = await _snapshot_config()
    cur_host = (snap.get("server_host") or "").strip()

    if cur_mode == "local" and target == "local":
        return {"ok": True, "hosting_mode": "local", "note": "This bot already runs on this computer."}
    if cur_mode == "server" and target == "server" and host and host == cur_host:
        return {"ok": True, "hosting_mode": "server", "note": "That’s the same server — nothing to move."}
    if target == "server" and not host:
        return {"ok": False, "error": "Enter the destination server’s IP address."}
    if cur_mode == "server" and not cur_host:
        return {"ok": False, "error": "No current server on record to move from."}

    if _moving:
        return {"ok": False, "error": "A move is already in progress."}
    _moving = True
    try:
        if cur_mode == "local" and target == "server":
            return await _local_to_server(app, host, (user or "ubuntu"), snap)
        if cur_mode == "server" and target == "local":
            return await _server_to_local(app, cur_host, snap)
        if cur_mode == "server" and target == "server":
            return await _server_to_server(app, cur_host, host, (user or "ubuntu"), snap)
        return {"ok": False, "error": f"Unsupported move: {cur_mode} → {target}."}
    except Exception as exc:  # noqa: BLE001 — surfaced to the operator
        log.exception("bot move %s → %s failed", cur_mode, target)
        return {"ok": False, "error": str(exc)}
    finally:
        _moving = False


async def _local_to_server(app, host: str, user: str, snap: dict) -> dict:
    from olisar import runtime_config
    from olisar.db import engine
    from olisar.runtime import paths, profiles, remote, server

    if not snap.get("discord_token"):
        return {"ok": False, "error": "This bot has no Discord token stored locally to deploy."}

    active_id = profiles.active_id()
    local_db = str(profiles.db_path_for(active_id))
    local_kb = str(paths.kb_uploads_dir())
    env = _build_env(snap, await _allowlisted_ids())
    steps: list[str] = []

    # Quiesce the local writer so the WAL is flushed into the .db before copying.
    await server.stop_all_supervisors(app)
    await engine.reset_engine(local_db)

    staging = Path(tempfile.mkdtemp(prefix="olisar-move-"))
    try:
        _copy_db_files(local_db, staging)
        _copy_kb(local_kb, staging / "kb_uploads")
        await asyncio.to_thread(_finalize_db, str(staging / "olisar.db"), local_kb, remote.VM_KB)

        steps.append(f"Deploying Olisar to {host} — Docker install + image pull (a few minutes)…")
        dep = await remote.deploy(host, user, env)  # flips this profile to server mode on success
        if not dep.get("ok"):
            raise RuntimeError(dep.get("error") or "Deploy failed.")

        steps.append("Uploading your bot’s data to the server…")
        await remote.import_data(host, user, staging)
    except Exception as exc:  # noqa: BLE001 — surfaced to the operator
        # If we never flipped to server (deploy didn't persist), the local bot was left stopped
        # — restart it so a failed move doesn't strand the operator with a dead bot.
        if await runtime_config.hosting_mode() == "local":
            with contextlib.suppress(Exception):
                await server.start_supervisor(app, active_id)
        return {"ok": False, "error": str(exc), "log": "\n".join(steps)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    steps.append("Done — the bot now runs on your server. The old local copy is kept as a backup.")
    return {"ok": True, "hosting_mode": "server", "host": host, "log": "\n".join(steps)}


async def _server_to_local(app, src_host: str, snap: dict) -> dict:
    from olisar.db import engine
    from olisar.runtime import paths, profiles, remote, server

    active_id = profiles.active_id()
    local_db = str(profiles.db_path_for(active_id))
    local_kb = str(paths.kb_uploads_dir())
    src_user = snap.get("server_ssh_user") or "ubuntu"
    steps = [f"Reading configuration from {src_host}…"]

    env = _parse_env(await remote.read_env(src_host, src_user))

    steps.append(f"Downloading your bot’s data from {src_host}…")
    staging = Path(tempfile.mkdtemp(prefix="olisar-move-"))
    try:
        await remote.export_data(src_host, src_user, staging)  # stops the VM container
        db_in = staging / "olisar.db"
        if not db_in.exists():
            return {"ok": False, "error": "No database found on the server to download."}
        await asyncio.to_thread(_finalize_db, str(db_in), remote.VM_KB, local_kb)

        await server.stop_all_supervisors(app)
        await engine.reset_engine(local_db)
        _backup_and_replace(local_db, db_in)
        _copy_kb(str(staging / "kb_uploads"), Path(local_kb))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    await engine.reset_engine(local_db)
    await _restore_config(snap, env)
    await server.start_supervisor(app, active_id)
    steps.append("Done — the bot now runs on this computer. The server is stopped but kept as a backup.")
    return {"ok": True, "hosting_mode": "local", "log": "\n".join(steps)}


async def _server_to_server(app, src_host: str, dst_host: str, user: str, snap: dict) -> dict:
    from olisar.runtime import remote

    src_user = snap.get("server_ssh_user") or "ubuntu"
    steps = [f"Reading configuration from {src_host}…"]
    env = await remote.read_env(src_host, src_user)
    if not env.strip():
        return {"ok": False, "error": f"Couldn’t read the current server’s configuration ({src_host})."}

    steps.append(f"Downloading data from {src_host}…")
    staging = Path(tempfile.mkdtemp(prefix="olisar-move-"))
    try:
        await remote.export_data(src_host, src_user, staging)  # stops the old VM container
        if not (staging / "olisar.db").exists():
            return {"ok": False, "error": "No database found on the current server to move."}
        # Both VMs use /var/lib/olisar, so doc paths need no rewrite — just merge the WAL.
        await asyncio.to_thread(_finalize_db, str(staging / "olisar.db"), "", "")

        steps.append(f"Deploying Olisar to {dst_host}…")
        dep = await remote.deploy(dst_host, user, env)  # updates this profile's server_host
        if not dep.get("ok"):
            return {"ok": False, "error": dep.get("error") or "Deploy failed.", "log": dep.get("log", "")}

        steps.append(f"Uploading data to {dst_host}…")
        await remote.import_data(dst_host, user, staging)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    steps.append("Done — the bot now runs on the new server. The old server is stopped but kept as a backup.")
    return {"ok": True, "hosting_mode": "server", "host": dst_host, "log": "\n".join(steps)}
