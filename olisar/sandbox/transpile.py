"""Server-side TypeScript -> JS transpile.

The runtime — not the browser — is the source of truth for the JS that actually runs
in the sandbox. An operator (or, later, an imported ``.olx`` bundle) supplies *source*;
this module derives the executable ``compiled_js`` from it, so we never run client- or
publisher-supplied JS we didn't produce ourselves. That's the load-bearing trust
boundary for the extension marketplace.

Implementation: we run the vendored ``typescript.js`` compiler inside the *same* QuickJS
engine the sandbox already ships — no Node, no wasm runtime, no new native dependency, so
it works identically in dev and in the frozen desktop bundle. ``ts.transpileModule`` is a
single-file, type-stripping transform (no type-checking, no module resolution), which is
exactly the SDK's shape: authors ``import`` nothing and call the global ``defineExtension``.

QuickJS contexts are thread-affine, so all work runs on one dedicated worker thread; the
9 MB compiler is parsed once and the context is reused across transpiles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import quickjs

log = logging.getLogger("olisar.sandbox.transpile")

# The SDK surface version the transpiler/runtime targets. Stamped onto packages so an
# imported bundle built against a newer SDK can be flagged for compatibility.
# v2 adds persistent component handlers (spec.components + ComponentInteraction).
SDK_VERSION = "2"

_VENDOR = Path(__file__).with_name("vendor")
_TS_PATH = _VENDOR / "typescript.js"
_TS_COMPILER_MEMORY = 256 * 1024 * 1024  # the compiler itself is large; give it headroom

# One thread, one long-lived context. QuickJS is not safe to touch from multiple threads,
# so every transpile is funnelled through this single-worker pool.
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ts-transpile")
_local = threading.local()
# Recreate the context periodically so a long-lived compiler can't accumulate memory.
_RECYCLE_AFTER = 200


class TranspileError(Exception):
    """The source could not be transpiled (a syntax error, or the compiler failed)."""


def _context() -> quickjs.Context:
    ctx = getattr(_local, "ctx", None)
    count = getattr(_local, "count", 0)
    if ctx is not None and count >= _RECYCLE_AFTER:
        ctx = None  # drop it; rebuild below
    if ctx is None:
        if not _TS_PATH.exists():
            raise TranspileError(f"vendored typescript.js is missing at {_TS_PATH}")
        ctx = quickjs.Context()
        ctx.set_memory_limit(_TS_COMPILER_MEMORY)
        # The TS emitter recurses deeply; QuickJS's default 256 KB stack overflows on
        # some ordinary extensions. Give it generous headroom (well under the mem cap).
        ctx.set_max_stack_size(16 * 1024 * 1024)
        try:
            ctx.eval(_TS_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - only on a corrupt vendor file
            raise TranspileError(f"failed to load the TypeScript compiler: {exc}") from exc
        _local.ctx = ctx
        _local.count = 0
    return ctx


# Single-file, type-stripping transform. ``module: None`` keeps it a plain top-level
# script (QuickJS has no module loader and the SDK uses globals); ``isolatedModules``
# matches the single-file model; we ask for diagnostics so real syntax errors surface.
# It reads its input from a global (set per call) and evaluates to a JSON string — the
# binding returns JS strings as Python strings, and a returned function object isn't.
_TRANSFORM = (
    "(function(){"
    "var src = globalThis.__TS_IN;"
    "var r = ts.transpileModule(src, {"
    "  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None,"
    "    isolatedModules: true, removeComments: false },"
    "  reportDiagnostics: true });"
    "var errs = (r.diagnostics || []).filter(function(d){ return d.category === 1; })"
    "  .map(function(d){ return ts.flattenDiagnosticMessageText(d.messageText, '\\n'); });"
    "return JSON.stringify({ code: r.outputText, errors: errs });"
    "})()"
)


def _do(source: str) -> str:
    ctx = _context()
    try:
        ctx.eval("globalThis.__TS_IN = " + json.dumps(source) + ";")
        raw = ctx.eval(_TRANSFORM)
        ctx.eval("globalThis.__TS_IN = undefined;")
        _local.count = getattr(_local, "count", 0) + 1
    except Exception as exc:
        raise TranspileError(f"transpile failed: {exc}") from exc
    result = json.loads(raw)
    errors = result.get("errors") or []
    if errors:
        raise TranspileError("; ".join(errors))
    code = result.get("code")
    if not isinstance(code, str):
        raise TranspileError("transpiler returned no code")
    return code


def transpile_sync(source: str) -> str:
    """Transpile TypeScript source to JS (blocking). Raises ``TranspileError``."""
    return _pool.submit(_do, source).result(timeout=30)


async def transpile(source: str) -> str:
    """Transpile TypeScript source to JS. Raises ``TranspileError``."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, _do, source)


def self_check() -> bool:
    """Confirm the vendored compiler loads and transpiles — surfaced on /api/health."""
    try:
        out = transpile_sync("const x: number = 1;\ninterface Y { z: string }")
        return "x = 1" in out and "interface" not in out
    except Exception:
        log.exception("transpile self-check failed")
        return False


__all__ = ["transpile", "transpile_sync", "TranspileError", "SDK_VERSION", "self_check"]
