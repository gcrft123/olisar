"""Expose Olisar's dashboard over Tailscale Funnel — a free, stable public HTTPS URL
(``https://<host>.<tailnet>.ts.net``) with no domain required.

We don't run the heavyweight ``tailscaled`` daemon. Instead we manage a tiny bundled Go
sidecar (``olisar-funnel``, built from ``desktop/funnel-sidecar`` with Tailscale's
``tsnet`` library) that joins the operator's tailnet using their auth key, turns on
Funnel, and reverse-proxies public traffic to Olisar's local port. The auth key is passed
to the sidecar via the environment and never leaves the machine.

The sidecar prints one machine-readable line we parse:
  ``OLISAR_FUNNEL_URL=https://...``   on success
  ``OLISAR_FUNNEL_ERROR=<reason>``    on failure (e.g. Funnel not enabled — the reason
                                       includes Tailscale's enable URL)

The helper is a process of its own, and nothing ties it to ours: the sidecar never reads its
stdin, so it can't notice we're gone, and it outlives a backend that exits without stopping it
(killed outright, or cut short by the worker's shutdown deadline). An orphan keeps the bot's
node and its public address, serving a port nobody listens on. So the helper's PID is kept in
its state directory (one per bot), and a backend starting up stops a helper still holding that
directory before it starts its own; a backend about to exit abruptly stops its own first
(``kill_helpers``). The helper gets a stdin pipe of its own, which closes whenever this process
ends, for a sidecar that learns to exit on EOF.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("olisar.tunnel")

_START_TIMEOUT = 100  # seconds to wait for the funnel to come up
PIDFILE = "olisar-funnel.pid"  # in the helper's state directory

# Helpers this process started and hasn't stopped yet, for ``kill_helpers``.
_live: set[int] = set()

# A Tailscale device name Tailscale keeps as typed: one DNS label, since it becomes the first
# part of the console's address.
_DEVICE_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DEVICE_NAME_RULES = "Use letters, numbers and hyphens, starting and ending with a letter or number."


def device_name(raw: str | None) -> str | None:
    """``raw`` as a device name, lowercased, or None if it isn't one."""
    name = (raw or "").strip().lower()
    return name if _DEVICE_NAME_RE.match(name) else None


def rename_note(requested: str, url: str) -> str:
    """What to tell the operator when a renamed node's address doesn't start with the name
    they asked for, or "" when it does."""
    label = url.split("://", 1)[-1].split(".", 1)[0].lower()
    if not label or label == requested:
        return ""
    if re.fullmatch(rf"{re.escape(requested)}-\d+", label):
        return f"Another device in your tailnet is already called {requested}, so Tailscale named this one {label}."
    # Tailscale stops following the hostname a node sends once its name is set by hand.
    return (
        f"Tailscale kept the name {label}. If it was renamed in Tailscale's admin console, "
        "turn on Auto-generate from OS hostname there, then rename it again."
    )


def funnel_helper_path() -> str | None:
    """Locate the bundled Funnel sidecar: explicit env (Electron sets this) → next to a
    frozen bundle → anything on PATH (dev)."""
    env = os.environ.get("OLISAR_FUNNEL")
    if env and Path(env).exists():
        return env
    name = "olisar-funnel.exe" if os.name == "nt" else "olisar-funnel"
    if getattr(sys, "frozen", False):
        cand = Path(getattr(sys, "_MEIPASS", "")) / name
        if cand.exists():
            return str(cand)
    return shutil.which("olisar-funnel")


def _command_line(pid: int) -> str | None:
    """What process ``pid`` is running, or None if there's no such process."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            return out if f'"{pid}"' in out else None
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def _is_helper_for(command: str, state_dir: str) -> bool:
    if os.name == "nt":  # tasklist names the image, not its arguments
        return "olisar-funnel" in command.lower()
    return f"--state {state_dir}" in command


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill(pids: list[int], grace: float) -> None:
    """Terminate each of ``pids``, and kill any still there ``grace`` seconds later."""
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    if os.name == "nt":  # SIGTERM is TerminateProcess there: already gone
        return
    deadline = time.monotonic() + grace
    left = list(pids)
    while left and time.monotonic() < deadline:
        time.sleep(0.1)
        left = [pid for pid in left if _alive(pid)]
    for pid in left:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


def reap_stale_helper(state_dir: str) -> int | None:
    """Stop a helper an earlier backend left running on ``state_dir`` (this bot's Tailscale
    node), and return its PID. Blocking; only ever called before we start a helper of our own,
    so a helper found there isn't ours."""
    pidfile = Path(state_dir) / PIDFILE
    try:
        pid = int(pidfile.read_text("utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    command = _command_line(pid) if pid > 0 and pid not in _live else None
    found = pid if command and _is_helper_for(command, str(state_dir)) else None
    if found is not None:
        log.warning("stopping a Tailscale helper left running by an earlier start (pid %d)", pid)
        _kill([pid], grace=3.0)
    with contextlib.suppress(OSError):
        pidfile.unlink()
    return found


def kill_helpers() -> None:
    """Stop every helper this process started. Safe from any thread and without the event
    loop: the last thing a backend does before it exits without its usual cleanup."""
    _kill(sorted(_live), grace=1.0)
    _live.clear()


class FunnelManager:
    """Owns at most one ``olisar-funnel`` sidecar process and the public URL it reports."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._url: str = ""
        self._pidfile: Path | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def url(self) -> str:
        return self._url

    async def start(
        self, auth_key: str, hostname: str, target: str, state_dir: str
    ) -> tuple[bool, str]:
        """Bring the funnel up. Returns ``(True, public_url)`` or ``(False, reason)``.
        ``auth_key`` may be empty on a re-launch once the node identity is persisted in
        ``state_dir``."""
        if self.running:
            return True, self._url or "running"
        exe = funnel_helper_path()
        if not exe:
            return False, "the Tailscale helper isn't bundled with this build"
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        # Two helpers can't run one node: one that outlived an earlier backend goes first.
        await asyncio.to_thread(reap_stale_helper, state_dir)
        env = {**os.environ}
        if auth_key:
            env["TS_AUTHKEY"] = auth_key
        try:
            self._proc = await asyncio.create_subprocess_exec(
                exe, "--hostname", hostname or "olisar", "--target", target,
                "--state", state_dir,
                # Its own pipe, not ours: it closes when this process ends, however it ends.
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001 — surface to the caller
            log.exception("failed to launch the funnel helper")
            return False, f"failed to launch the helper: {exc}"
        _live.add(self._proc.pid)
        self._pidfile = Path(state_dir) / PIDFILE
        try:
            self._pidfile.write_text(f"{self._proc.pid}\n", "utf-8")
        except OSError as exc:
            log.warning("couldn't record the Tailscale helper's pid: %s", exc)

        try:
            async with asyncio.timeout(_START_TIMEOUT):
                assert self._proc.stdout is not None
                while True:
                    raw = await self._proc.stdout.readline()
                    if not raw:
                        break  # the sidecar exited without a marker
                    line = raw.decode(errors="replace").strip()
                    if line.startswith("OLISAR_FUNNEL_URL="):
                        self._url = line.split("=", 1)[1]
                        asyncio.create_task(self._drain())  # keep its stdout flowing
                        log.info("Tailscale Funnel up at %s", self._url)
                        return True, self._url
                    if line.startswith("OLISAR_FUNNEL_ERROR="):
                        reason = line.split("=", 1)[1]
                        await self.stop()
                        return False, reason
        except asyncio.TimeoutError:
            await self.stop()
            return False, "timed out bringing up the tunnel"
        await self.stop()
        return False, "the tunnel helper exited unexpectedly"

    async def _drain(self) -> None:
        """Consume the sidecar's remaining stdout so its pipe never blocks."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        with contextlib.suppress(Exception):
            while True:
                if not await proc.stdout.readline():
                    break

    async def stop(self) -> None:
        proc, self._proc, self._url = self._proc, None, ""
        pidfile, self._pidfile = self._pidfile, None
        if proc is None:
            return
        _live.discard(proc.pid)
        if pidfile is not None:
            # Only if it's still ours: a later backend may already have recorded its own.
            with contextlib.suppress(OSError, ValueError, IndexError):
                if int(pidfile.read_text("utf-8").split()[0]) == proc.pid:
                    pidfile.unlink()
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        log.info("Tailscale Funnel stopped")
