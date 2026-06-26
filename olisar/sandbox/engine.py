"""Low-level QuickJS driver for the extension sandbox.

This module is **synchronous** and asyncio-agnostic — it runs on a worker thread
(``runner`` owns the thread pool). It loads the JS ``bootstrap`` plus an extension's
transpiled JS into a fresh, hermetic QuickJS context and either extracts the manifest
or invokes one handler.

The sandbox boundary: a fresh QuickJS context has **no ambient authority** (no
``fetch``/``require``/``process``/filesystem/clock-beyond-``Date``). The only way out is
the outbox the bootstrap defines; ``invoke`` drains it each turn and calls back into the
caller's ``perform`` to do the real (host-side) work. A CPU **time limit** and a
**memory limit** bound each run; because we never register a JS->Python callback (we
drive everything via ``eval``), the time limit is allowed to stay armed.
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import quickjs

log = logging.getLogger("olisar.sandbox.engine")

# Resource caps. Tools should be quick; commands may run a short interactive flow.
DEFAULT_MEMORY_BYTES = 64 * 1024 * 1024
TOOL_CPU_SECONDS = 5.0
COMMAND_CPU_SECONDS = 10.0
# Wall-clock ceiling for the whole pump (CPU + host I/O between turns).
TOOL_WALL_SECONDS = 20.0
COMMAND_WALL_SECONDS = 900.0  # an interactive flow may wait on the user (modal/buttons)
COMPONENT_WALL_SECONDS = 30.0  # one button/select click — quick state update + edit, never waits
EVENT_WALL_SECONDS = 60.0  # a gateway-event hook — never waits on a user, but may call the model once

# A capability performer: (cap, method, args) -> JSON-serialisable value. May raise.
Perform = Callable[[str, str, list], Any]


class SandboxError(Exception):
    """A handler threw, the extension was malformed, or a limit was exceeded."""


@lru_cache(maxsize=1)
def _bootstrap_src() -> str:
    return (Path(__file__).with_name("bootstrap.js")).read_text(encoding="utf-8")


def _new_context(memory_bytes: int, cpu_seconds: float | None) -> quickjs.Context:
    ctx = quickjs.Context()
    ctx.set_memory_limit(memory_bytes)
    if cpu_seconds is not None:
        ctx.set_time_limit(cpu_seconds)
    return ctx


def extract_manifest(compiled_js: str) -> dict:
    """Run an extension's compiled JS once and return its declarative manifest
    (the ``defineExtension`` spec with handler functions stripped). Raises
    ``SandboxError`` if the code is malformed or never calls ``defineExtension``."""
    ctx = _new_context(DEFAULT_MEMORY_BYTES, TOOL_CPU_SECONDS)
    try:
        ctx.eval(_bootstrap_src())
        ctx.eval(compiled_js)
        raw = ctx.eval("__collectManifest()")
    except Exception as exc:  # quickjs.JSException et al.
        raise SandboxError(f"extension failed to load: {exc}") from exc
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SandboxError(f"manifest was not valid JSON: {exc}") from exc


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
    runs on this same (worker) thread; the caller bridges it to the asyncio loop.
    Returns the handler's JSON-decoded return value. Raises ``SandboxError``.
    """
    ctx = _new_context(memory_bytes, cpu_seconds)
    try:
        ctx.eval(_bootstrap_src())
        ctx.eval(compiled_js)
        ctx.eval(f"__invoke({json.dumps(kind)}, {json.dumps(name)}, {json.dumps(json.dumps(payload))})")
    except Exception as exc:
        raise SandboxError(f"extension failed to start {kind} {name!r}: {exc}") from exc

    deadline = time.monotonic() + wall_seconds
    idle_spins = 0
    while True:
        try:
            done = bool(ctx.eval("__DONE"))
        except Exception as exc:
            raise SandboxError(f"sandbox aborted (likely CPU limit): {exc}") from exc
        if done:
            break

        ran = False
        try:
            ran = bool(ctx.execute_pending_job())
        except Exception as exc:
            raise SandboxError(f"sandbox aborted (likely CPU limit): {exc}") from exc

        # Drain queued capability requests and settle their promises.
        outbox = json.loads(ctx.eval("__drainOutbox()"))
        for req in outbox:
            rid = req["id"]
            try:
                value = perform(req["cap"], req["method"], req.get("args") or [])
                ctx.eval(f"__settle({json.dumps(rid)}, true, {json.dumps(json.dumps(value))})")
            except Exception as exc:  # noqa: BLE001 — surface as a JS rejection the author can catch
                ctx.eval(f"__settle({json.dumps(rid)}, false, {json.dumps(str(exc))})")

        if not ran and not outbox:
            idle_spins += 1
            if idle_spins > 100000:
                raise SandboxError("sandbox stalled (no progress)")
        else:
            idle_spins = 0

        if time.monotonic() > deadline:
            raise SandboxError("extension exceeded its time budget")

    err = ctx.eval("__ERROR")
    if err:
        raise SandboxError(str(err))
    raw = ctx.eval("__RESULT")
    if raw is None:
        return None
    return json.loads(raw)


def self_check() -> bool:
    """Instantiate the VM once and run a trivial extension end-to-end. Surfaced on
    /api/health so a packaging failure (missing C-extension wheel) is obvious."""
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
