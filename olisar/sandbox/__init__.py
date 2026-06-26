"""Sandboxed execution of user-authored extensions.

Author TypeScript is transpiled to JS and run here in a hermetic QuickJS context
with no ambient authority — the only way out is the operator-approved capability
surface (``capabilities.py``). See ``engine.py`` for the VM driver and ``runner.py``
for the async entry points the rest of the app calls.
"""

from __future__ import annotations

from olisar.sandbox.engine import SandboxError, self_check
from olisar.sandbox.runner import (
    extract_manifest,
    run_command,
    run_component,
    run_event,
    run_on_enable,
    run_tool,
)

__all__ = [
    "SandboxError",
    "self_check",
    "extract_manifest",
    "run_tool",
    "run_command",
    "run_component",
    "run_event",
    "run_on_enable",
]
