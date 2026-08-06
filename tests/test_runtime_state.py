"""Coverage for the state file the backend publishes for out-of-band readers.

Run:  uv run python -m unittest tests.test_runtime_state -v

In server mode the desktop control panel used to learn about the remote container by
grepping ``docker compose logs`` — streaming the whole history on a miss. state.json
replaces that, so the things that matter here are: a reader never sees a half-written
file, a rewrite doesn't drop facts an earlier write published, and absent facts are
*missing keys* rather than empties a reader would have to special-case.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from olisar.runtime import state


class StateFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # Isolate module-level memo between tests — write() merges into it by design.
        state._last = {}
        patcher = patch.object(state, "state_path", lambda: self.dir / state.STATE_FILENAME)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _written(self) -> dict:
        return json.loads((self.dir / state.STATE_FILENAME).read_text("utf-8"))

    def test_writes_version_and_public_url(self) -> None:
        state.write(public_url="https://olisar.example.ts.net", vec_ok=True)
        data = self._written()
        self.assertEqual(data["public_url"], "https://olisar.example.ts.net")
        self.assertTrue(data["vec_ok"])
        self.assertTrue(data["version"])  # resolved from pyproject in a source run
        self.assertIn("started_at", data)

    def test_absent_url_is_a_missing_key_not_an_empty_string(self) -> None:
        """A backend with no tunnel must not publish public_url: "" — the reader treats a
        missing key as "no URL", and an empty string would sail past a truthiness check."""
        state.write(public_url="", vec_ok=True)
        self.assertNotIn("public_url", self._written())

    def test_rewrite_keeps_earlier_facts(self) -> None:
        """The tunnel comes up after boot, so /api/tunnel/enable rewrites with only a URL.
        That must not drop the self-check results boot published."""
        state.write(vec_ok=False, sandbox_ok=True)
        state.write(public_url="https://later.example.ts.net")
        data = self._written()
        self.assertEqual(data["public_url"], "https://later.example.ts.net")
        self.assertFalse(data["vec_ok"])  # False is a real value, not "unset"
        self.assertTrue(data["sandbox_ok"])

    def test_clearing_the_url_removes_it(self) -> None:
        state.write(public_url="https://olisar.example.ts.net")
        state.write(public_url="")
        self.assertNotIn("public_url", self._written())

    def test_started_at_is_stable_across_rewrites(self) -> None:
        state.write(public_url="https://a.example.ts.net")
        first = self._written()["started_at"]
        state.write(public_url="https://b.example.ts.net")
        self.assertEqual(self._written()["started_at"], first)

    def test_write_leaves_no_temp_file_behind(self) -> None:
        state.write(public_url="https://olisar.example.ts.net")
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_write_failure_does_not_raise(self) -> None:
        """Publishing state must never be able to fail a boot."""
        with patch.object(state, "state_path", lambda: self.dir / "nope" / "state.json"):
            self.assertEqual(state.write(public_url="https://x.example.ts.net"), {})

    def test_read_returns_empty_for_missing_or_corrupt(self) -> None:
        self.assertEqual(state.read(), {})
        (self.dir / state.STATE_FILENAME).write_text("{not json", encoding="utf-8")
        self.assertEqual(state.read(), {})


if __name__ == "__main__":
    unittest.main()
