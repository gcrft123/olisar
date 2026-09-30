"""The sandbox host: a child process that runs extension code in QuickJS.

``olisar.sandbox.engine`` keeps a few of these warm and hands each one run at a time. Extension
code used to run on a thread of the backend itself, and a thread can't be stopped: QuickJS's
regex engine never polls the interrupt handler, so a backtracking pattern ran for as long as it
liked, on an import preview as easily as in a tool call. A process can be killed.

The parent and the host speak length-prefixed JSON frames over stdin/stdout (a 4-byte
big-endian length, then UTF-8 JSON). The parent sends a job (``invoke`` or ``manifest``). For
an invocation the host sends every batch of ``host.*`` requests the code queues as ``calls`` and
waits for the parent's ``settle``; either job ends with ``done`` or ``error``.

It's started as ``python -m olisar.runtime --sandbox-host`` (``olisar-backend --sandbox-host``
when frozen), which imports this module and QuickJS and none of the app. The parent gives it an
environment without the backend's tokens and keys, and it exits when its stdin closes, which is
how it learns the parent has gone.
"""

from __future__ import annotations

import json
import os
import signal
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

import quickjs

_BOOTSTRAP = Path(__file__).parents[1] / "sandbox" / "bootstrap.js"

_HEADER = struct.Struct(">I")
# Far above any legitimate message (a 20 MB file read as base64 is ~27 MB of JSON), and low
# enough that a corrupt length can't make the reader allocate gigabytes.
MAX_FRAME_BYTES = 256 * 1024 * 1024


def write_frame(stream: BinaryIO, message: Any) -> None:
    # ASCII-escaped JSON, so a lone surrogate in a JS string survives the trip.
    data = json.dumps(message).encode("ascii")
    if len(data) > MAX_FRAME_BYTES:
        raise ValueError("message too large for the sandbox")
    stream.write(_HEADER.pack(len(data)))
    stream.write(data)
    stream.flush()


def read_frame(stream: BinaryIO) -> Any:
    """The next message, or None when the other side has closed the stream."""
    header = stream.read(_HEADER.size)
    if not header:
        return None
    if len(header) < _HEADER.size:
        raise EOFError("the sandbox stream ended mid-message")
    (size,) = _HEADER.unpack(header)
    if size > MAX_FRAME_BYTES:
        raise ValueError("message too large for the sandbox")
    data = stream.read(size)
    if len(data) < size:
        raise EOFError("the sandbox stream ended mid-message")
    return json.loads(data)


class _ParentGone(Exception):
    """stdin closed while a run was waiting on the parent."""


class _Watchdog:
    """Ends this process when a run holds it far past its CPU budget.

    While the parent is alive it kills a run that overstays long before this fires. This is
    for when it isn't: a run stuck in a regex never reads stdin, so it would never see that
    the parent had gone, and would keep a core busy for as long as the regex took."""

    def __init__(self) -> None:
        self.limit: float | None = None  # a process_time() value, while a run is going

    def run(self) -> None:
        while True:
            time.sleep(0.5)
            limit = self.limit
            if limit is not None and time.process_time() > limit:
                os._exit(3)


_watchdog = _Watchdog()


class _Budget:
    """One run's CPU allowance, spent across every turn of the pump.

    QuickJS's time limit restarts its clock on each ``eval``/``execute_pending_job``, so
    arming it once let code that awaits between bursts of work run until the wall-clock
    budget. Each turn is armed with only what's left."""

    def __init__(self, ctx: quickjs.Context, seconds: float) -> None:
        self._ctx = ctx
        self._ends = time.process_time() + seconds
        _watchdog.limit = self._ends + seconds + 1.0

    def call(self, fn, *args):
        left = self._ends - time.process_time()
        if left <= 0:
            raise RuntimeError("extension exceeded its CPU budget")
        self._ctx.set_time_limit(left)
        return fn(*args)


def _manifest(ctx: quickjs.Context, budget: _Budget, bootstrap: str, js: str) -> dict:
    try:
        budget.call(ctx.eval, bootstrap)
        budget.call(ctx.eval, js)
        raw = budget.call(ctx.eval, "__collectManifest()")
    except Exception as exc:  # quickjs.JSException et al.
        raise RuntimeError(f"extension failed to load: {exc}") from exc
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"manifest was not valid JSON: {exc}") from exc


def _invoke(
    ctx: quickjs.Context, budget: _Budget, bootstrap: str, job: dict,
    inp: BinaryIO, out: BinaryIO,
) -> Any:
    kind, name = job["kind"], job["name"]
    try:
        budget.call(ctx.eval, bootstrap)
        budget.call(ctx.eval, job["js"])
        budget.call(
            ctx.eval,
            f"__invoke({json.dumps(kind)}, {json.dumps(name)}, {json.dumps(json.dumps(job['payload']))})",
        )
    except Exception as exc:
        raise RuntimeError(f"extension failed to start {kind} {name!r}: {exc}") from exc

    idle_spins = 0
    while True:
        try:
            done = bool(budget.call(ctx.eval, "__DONE"))
            if done:
                break
            ran = bool(budget.call(ctx.execute_pending_job))
        except Exception as exc:
            raise RuntimeError(f"sandbox aborted (likely CPU limit): {exc}") from exc

        # Hand queued capability requests to the parent, which performs them, and settle
        # their promises with what comes back.
        outbox = json.loads(budget.call(ctx.eval, "__drainOutbox()"))
        if outbox:
            write_frame(out, {"op": "calls", "calls": outbox})
            reply = read_frame(inp)
            if reply is None:
                raise _ParentGone
            for rid, ok, text in reply["results"]:
                budget.call(
                    ctx.eval,
                    f"__settle({json.dumps(rid)}, {'true' if ok else 'false'}, {json.dumps(text)})",
                )

        if not ran and not outbox:
            idle_spins += 1
            if idle_spins > 100000:
                raise RuntimeError("sandbox stalled (no progress)")
        else:
            idle_spins = 0

    err = budget.call(ctx.eval, "__ERROR")
    if err:
        raise RuntimeError(str(err))
    raw = budget.call(ctx.eval, "__RESULT")
    return None if raw is None else json.loads(raw)


def _run(job: dict, bootstrap: str, inp: BinaryIO, out: BinaryIO) -> Any:
    # A fresh, hermetic context per run: nothing one extension leaves behind reaches the next.
    ctx = quickjs.Context()
    ctx.set_memory_limit(int(job["memory"]))
    budget = _Budget(ctx, float(job["cpu"]))
    if job.get("op") == "manifest":
        return _manifest(ctx, budget, bootstrap, job["js"])
    return _invoke(ctx, budget, bootstrap, job, inp, out)


def main() -> None:
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # stdout carries frames only; a stray print goes to the log instead
    # Ctrl-C in the operator's terminal reaches the whole process group. The parent decides
    # when this process ends, by closing stdin.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    bootstrap = _BOOTSTRAP.read_text(encoding="utf-8")
    threading.Thread(target=_watchdog.run, name="sandbox-watchdog", daemon=True).start()
    write_frame(out, {"op": "ready"})
    while True:
        job = read_frame(inp)
        if job is None:
            return
        try:
            reply = {"op": "done", "result": _run(job, bootstrap, inp, out)}
        except _ParentGone:
            return
        except Exception as exc:  # noqa: BLE001 - every failure reaches the parent as a SandboxError
            reply = {"op": "error", "message": str(exc)}
        finally:
            _watchdog.limit = None
        try:
            write_frame(out, reply)
        except OSError:
            return  # the parent has gone
        except Exception as exc:  # noqa: BLE001 - e.g. a result too large to send
            write_frame(out, {"op": "error", "message": f"couldn't return the result: {exc}"})
