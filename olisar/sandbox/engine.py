"""Low-level driver for the extension sandbox.

This module is **synchronous** and asyncio-agnostic — it runs on a worker thread
(``runner`` owns the thread pool). It hands an extension's transpiled JS to a sandbox host,
a child process (``olisar/runtime/sandbox_host.py``) that loads the JS ``bootstrap`` plus the
extension into a fresh, hermetic QuickJS context and either extracts the manifest or invokes
one handler.

The sandbox boundary: a fresh QuickJS context has **no ambient authority** (no
``fetch``/``require``/``process``/filesystem/clock-beyond-``Date``). The only way out is
the outbox the bootstrap defines; the host drains it each turn and sends the requests here,
and ``invoke`` calls back into the caller's ``perform`` to do the real (host-side) work.

Limits: a **memory limit** per context, and a **CPU budget** the host spends across every
turn of a run. The host interrupts code that runs past it, but QuickJS can't interrupt
everything (its regex engine never checks), so this side also kills a host that keeps the
turn well past the budget, and one whose run passes its **wall-clock budget**. Killing a
process always works, which is why the code runs in one rather than on a thread.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from olisar.runtime.sandbox_host import read_frame, write_frame

log = logging.getLogger("olisar.sandbox.engine")

# Resource caps. Tools should be quick; commands may run a short interactive flow.
DEFAULT_MEMORY_BYTES = 64 * 1024 * 1024
# Slash-command handlers may touch base64-encoded files (≤ 20 MB); give the VM more headroom.
COMMAND_MEMORY_BYTES = 128 * 1024 * 1024
TOOL_CPU_SECONDS = 5.0
COMMAND_CPU_SECONDS = 10.0
# Wall-clock ceiling for the whole run (CPU + host I/O between turns).
TOOL_WALL_SECONDS = 20.0
COMMAND_WALL_SECONDS = 900.0  # an interactive flow may wait on the user (modal/buttons)
COMPONENT_WALL_SECONDS = 30.0  # one button/select click — quick state update + edit, never waits
EVENT_WALL_SECONDS = 60.0  # a gateway-event hook — never waits on a user, but may call the model once

# A capability performer: (cap, method, args) -> JSON-serialisable value. May raise; a
# SandboxError ends the run, anything else rejects the promise in JS.
Perform = Callable[[str, str, list], Any]

# How long past its CPU budget a run may keep the host busy before it's killed. The host stops
# code that polls QuickJS's interrupt right on budget, so this only catches code that never
# polls. It's measured in wall time here, which runs ahead of CPU time on a busy machine, so
# the slack grows with the budget.
_KILL_GRACE_SECONDS = 0.25
_POOL_SIZE = 4  # idle hosts kept warm: one per ext-sandbox thread in ``runner``
_RECYCLE_AFTER = 500  # runs per host, so one long-lived process can't accumulate memory
_START_TIMEOUT = 30.0  # a first launch of the frozen app can be slow (antivirus scans)
# The host runs untrusted code, so it inherits none of the backend's environment (the bot
# token, API keys): only what an OS needs to start a process.
_ENV_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE")
_SOURCE_ROOT = Path(__file__).resolve().parents[2]  # where ``-m olisar.runtime`` resolves


class SandboxError(Exception):
    """A handler threw, the extension was malformed, or a limit was exceeded."""


_TIMEOUT = object()
_GONE = object()


def _command() -> list[str]:
    if getattr(sys, "frozen", False):  # the PyInstaller bundle is the backend binary itself
        return [sys.executable, "--sandbox-host"]
    return [sys.executable, "-m", "olisar.runtime", "--sandbox-host"]


class _Host:
    """One sandbox host process, and a thread that queues up what it sends back."""

    def __init__(self) -> None:
        env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP}
        env["PYTHONUTF8"] = "1"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(
                _command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
                cwd=None if getattr(sys, "frozen", False) else _SOURCE_ROOT,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise SandboxError(f"couldn't start the extension sandbox: {exc}") from exc
        self.runs = 0
        self._replies: queue.SimpleQueue = queue.SimpleQueue()
        # A reader thread rather than select(): Windows can't select() on a pipe.
        threading.Thread(target=self._read, name="ext-sandbox-reader", daemon=True).start()
        if self.receive(_START_TIMEOUT) != {"op": "ready"}:
            self.kill()
            raise SandboxError("the extension sandbox didn't start")

    def _read(self) -> None:
        stdout = self.proc.stdout
        try:
            while (msg := read_frame(stdout)) is not None:
                self._replies.put(msg)
        except Exception as exc:  # noqa: BLE001 - a garbled stream ends this host like a crash
            log.warning("extension sandbox sent a bad message: %s", exc)
        finally:
            self._replies.put(_GONE)
            with contextlib.suppress(OSError):
                stdout.close()

    def send(self, msg: dict) -> None:
        try:
            write_frame(self.proc.stdin, msg)
        except (OSError, TypeError, ValueError) as exc:
            raise SandboxError(f"couldn't reach the extension sandbox: {exc}") from exc

    def receive(self, timeout: float) -> Any:
        """The host's next message, ``_TIMEOUT`` if none came in time, or ``_GONE``."""
        try:
            return self._replies.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return _TIMEOUT

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        """Let it exit on its own: it stops when stdin closes."""
        with contextlib.suppress(OSError):
            self.proc.stdin.close()
        try:
            self.proc.wait(2)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self) -> None:
        with contextlib.suppress(OSError):
            self.proc.kill()
        with contextlib.suppress(OSError):
            self.proc.stdin.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(5)


_idle: list[_Host] = []
_idle_lock = threading.Lock()


def _checkout() -> _Host:
    """A warm host if one is free, else a new one. Each host runs one job at a time."""
    with _idle_lock:
        while _idle:
            host = _idle.pop()
            if host.alive():
                return host
            host.kill()
    return _Host()


def _checkin(host: _Host, reusable: bool) -> None:
    host.runs += 1
    if not reusable:
        host.kill()  # it may be mid-run (stuck, or waiting on a host call): never reuse it
        return
    if host.runs < _RECYCLE_AFTER:
        with _idle_lock:
            if len(_idle) < _POOL_SIZE:
                _idle.append(host)
                return
    host.close()


def _perform_all(calls: Any, perform: Perform) -> list:
    """Perform one batch of queued capability requests; ``[id, ok, json or message]`` each."""
    if not isinstance(calls, list):
        raise SandboxError("extension sent malformed host calls")
    results = []
    for req in calls:
        if not isinstance(req, dict):
            raise SandboxError("extension sent malformed host calls")
        rid = req.get("id")
        try:
            value = perform(req.get("cap"), req.get("method"), req.get("args") or [])
            results.append([rid, True, json.dumps(value)])
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as a JS rejection the author can catch
            results.append([rid, False, str(exc)])
    return results


def _run(job: dict, perform: Perform | None, *, cpu_seconds: float, wall_seconds: float) -> Any:
    """Send ``job`` to a host, perform its host calls, and return its result."""
    deadline = time.monotonic() + wall_seconds
    # Time the host may keep the turn (from handing it work to hearing back), summed over the run.
    allowance = cpu_seconds + max(_KILL_GRACE_SECONDS, cpu_seconds / 2)
    host = _checkout()
    finished = False
    try:
        host.send(job)
        while True:
            sent = time.monotonic()
            msg = host.receive(min(allowance, deadline - sent))
            allowance -= time.monotonic() - sent
            if msg is _TIMEOUT:
                if time.monotonic() >= deadline:
                    raise SandboxError("extension exceeded its time budget")
                raise SandboxError("extension exceeded its CPU budget")
            if msg is _GONE:
                raise SandboxError("the extension sandbox stopped unexpectedly")
            op = msg.get("op") if isinstance(msg, dict) else None
            if op == "done":
                finished = True
                return msg.get("result")
            if op == "error":
                finished = True
                raise SandboxError(str(msg.get("message") or "the extension failed"))
            if op != "calls" or perform is None:
                raise SandboxError("the extension sandbox sent something unexpected")
            results = _perform_all(msg.get("calls"), perform)
            if time.monotonic() > deadline:
                raise SandboxError("extension exceeded its time budget")
            host.send({"op": "settle", "results": results})
    finally:
        _checkin(host, finished)


def extract_manifest(compiled_js: str) -> dict:
    """Run an extension's compiled JS once and return its declarative manifest
    (the ``defineExtension`` spec with handler functions stripped). Raises
    ``SandboxError`` if the code is malformed or never calls ``defineExtension``."""
    job = {"op": "manifest", "js": compiled_js, "cpu": TOOL_CPU_SECONDS, "memory": DEFAULT_MEMORY_BYTES}
    manifest = _run(job, None, cpu_seconds=TOOL_CPU_SECONDS, wall_seconds=TOOL_WALL_SECONDS)
    if not isinstance(manifest, dict):
        raise SandboxError("manifest was not a JSON object")
    return manifest


def invoke(
    compiled_js: str,
    kind: str,
    name: str,
    payload: dict,
    perform: Perform,
    *,
    cpu_seconds: float = TOOL_CPU_SECONDS,
    wall_seconds: float = TOOL_WALL_SECONDS,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
) -> Any:
    """Run one handler (``kind`` in {tool, command, onEnable}) to completion.

    ``perform`` does the real host-side work for each queued capability request and
    runs on this same (worker) thread; the caller bridges it to the asyncio loop, and has
    to give up by the wall-clock budget, since nothing here can interrupt it.
    Returns the handler's JSON-decoded return value. Raises ``SandboxError``.
    """
    job = {
        "op": "invoke", "js": compiled_js, "kind": kind, "name": name, "payload": payload,
        "cpu": cpu_seconds, "memory": memory_bytes,
    }
    return _run(job, perform, cpu_seconds=cpu_seconds, wall_seconds=wall_seconds)


def self_check() -> bool:
    """Start a sandbox host and run a trivial extension end-to-end. Surfaced on
    /api/health so a packaging failure (missing C-extension wheel, a frozen build that
    can't start its own sandbox host) is obvious."""
    probe = (
        "defineExtension({id:'__probe',name:'probe',tools:[{name:'p',"
        "parameters:{type:'object',properties:{}},handler:function(){return 'ok';}}]});"
    )
    try:
        result = invoke(probe, "tool", "p", {"args": {}, "ctx": {}}, lambda *_: None)
        return result == "ok"
    except Exception:
        log.exception("sandbox self-check failed")
        return False
