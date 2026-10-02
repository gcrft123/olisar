"""Deeply nested extension source is refused, not a crash, whatever the platform's thread stack.

Run:  uv run python -m unittest tests.test_transpile_stack -v

The TypeScript compiler runs in QuickJS with a 16 MB stack allowance, on a thread whose real
stack was whatever the platform hands out: 16 MB on macOS, but 8 MB under glibc's usual limit
on Linux (the VM). There, about a thousand nested parentheses, a 2 KB source, overflowed the
real stack before QuickJS's own check caught it, and the whole backend died on an import
preview. The transpile thread now asks for a 64 MB stack itself.

Each case runs in a fresh interpreter, since the default thread stack size has to be set
before the transpile module starts its thread.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent("""
    import sys, threading
    threading.stack_size(int(sys.argv[1]) * 1024)
    from olisar.sandbox import transpile
    for depth in (1000, 5000, 50000):
        src = "x = " + "(" * depth + "a" + ")" * depth + ";"
        try:
            transpile.transpile_sync(src)
            print(depth, "ok")
        except transpile.TranspileError:
            print(depth, "rejected")
    print("thread stack", threading.stack_size())
""")


class TranspileStackTests(unittest.TestCase):
    def _run(self, stack_kib: int) -> str:
        r = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(stack_kib)], cwd=REPO,
            capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(r.returncode, 0, f"process died (rc={r.returncode}): {r.stderr[-500:]}")
        return r.stdout

    def test_glibc_default_stack(self) -> None:
        out = self._run(8 * 1024)
        self.assertIn("50000 rejected", out)
        # The rest of the process keeps the stack size it had.
        self.assertIn(f"thread stack {8 * 1024 * 1024}", out)

    def test_small_default_stack(self) -> None:
        self.assertIn("50000 rejected", self._run(256))


if __name__ == "__main__":
    unittest.main()
