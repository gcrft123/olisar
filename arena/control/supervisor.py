"""Start, stop, restart and observe the Olisar instance under test.

This is the "deploy" half of the loop. A code change reaches the arena in one restart of a
single process — the harness owns that process directly rather than going through the
desktop app, the update timer, or the release channel, none of which belong anywhere near
an iteration loop measured in seconds.

The instance runs detached in its own session with output appended to
``<data_dir>/arena.log``, so it survives the CLI invocation that launched it and every
later command can read the same log. That matters more than it sounds: the agent's normal
mode is one short CLI call at a time, and a bot that died with the shell that started it
would take its logs with it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from arena.config import REPO_ROOT, ArenaConfig

log = logging.getLogger("arena.supervisor")

_STOP_GRACE_SECONDS = 12.0


def pid_path(cfg: ArenaConfig) -> Path:
    return cfg.data_dir / "arena.pid"


def log_path(cfg: ArenaConfig) -> Path:
    return cfg.data_dir / "arena.log"


def _read_pid(cfg: ArenaConfig) -> int:
    try:
        return int(pid_path(cfg).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def running_pid(cfg: ArenaConfig) -> int:
    """The live instance's pid, or 0. Clears a stale pid file as a side effect so a
    crashed instance doesn't make ``start`` refuse forever."""
    pid = _read_pid(cfg)
    if pid and _alive(pid):
        return pid
    with contextlib.suppress(OSError):
        pid_path(cfg).unlink(missing_ok=True)
    return 0


def ensure_database(cfg: ArenaConfig) -> None:
    """Create the arena database and seed guild defaults if it isn't there yet.

    The unified runtime builds its own schema at boot, but the harness mints console
    sessions and reads config *before* the first boot, so the tables have to exist first.
    """
    db = cfg.data_dir / "olisar.db"
    if db.is_file():
        return
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    log.info("creating the arena database at %s", db)
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.init_db"],
        cwd=REPO_ROOT,
        env=cfg.child_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"arena database init failed:\n{proc.stderr or proc.stdout}")


def start(cfg: ArenaConfig) -> int:
    """Launch the instance if it isn't already up. Returns its pid."""
    cfg.require("discord_token", "guild_id", "operator_id")
    existing = running_pid(cfg)
    if existing:
        log.info("arena instance already running (pid %s)", existing)
        return existing

    ensure_database(cfg)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    # Roll the previous process's log aside. Anything reading it to decide what is true
    # *now* — the quota check, the starvation guard — would otherwise be answering with
    # yesterday's errors, which is exactly what a stale RESOURCE_EXHAUSTED line did.
    previous = log_path(cfg)
    if previous.is_file() and previous.stat().st_size:
        with contextlib.suppress(OSError):
            previous.replace(previous.with_suffix(".log.prev"))
    handle = open(log_path(cfg), "a", buffering=1, encoding="utf-8")
    handle.write(f"\n{'=' * 72}\n=== arena instance starting {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    process = subprocess.Popen(
        [sys.executable, "-m", "olisar.runtime", "--port", str(cfg.api_port)],
        cwd=REPO_ROOT,
        env=cfg.child_env(),
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # survive the CLI process that spawned it
    )
    pid_path(cfg).write_text(str(process.pid), encoding="utf-8")
    log.info("arena instance started (pid %s) — logs at %s", process.pid, log_path(cfg))
    return process.pid


def stop(cfg: ArenaConfig) -> bool:
    """Stop the instance. True if something was running."""
    pid = running_pid(cfg)
    if not pid:
        return False
    # SIGTERM the whole process group: the runtime spawns the Tailscale/tunnel helper and
    # any subprocess of its own, and orphaning those leaves the API port bound.
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    deadline = time.monotonic() + _STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _alive(pid):
            break
        time.sleep(0.25)
    else:
        log.warning("arena instance (pid %s) ignored SIGTERM — killing", pid)
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    pid_path(cfg).unlink(missing_ok=True)
    return True


def restart(cfg: ArenaConfig) -> int:
    """The deploy step: stop, start, and hand back the new pid."""
    stop(cfg)
    # SQLite's WAL and the API port both need a beat to be released; starting into a
    # half-closed port produces a confusing "address already in use" at boot.
    time.sleep(1.5)
    return start(cfg)


def tail(cfg: ArenaConfig, lines: int = 120, grep: str = "") -> list[str]:
    """The last ``lines`` log lines, optionally filtered by a regex.

    Reads the whole file: the arena log is bounded by how long an experiment runs, and a
    correct tail over a file being appended to is not worth the cleverness here.
    """
    path = log_path(cfg)
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if grep:
        pattern = re.compile(grep, re.IGNORECASE)
        content = [line for line in content if pattern.search(line)]
    return content[-lines:]


def truncate_log(cfg: ArenaConfig) -> None:
    """Clear the log — called at the start of a scenario so its capture is unambiguous."""
    with contextlib.suppress(OSError):
        log_path(cfg).write_text("", encoding="utf-8")
