"""Coverage for the update marker the console reads to say "Updating…".

Run:  uv run python -m unittest tests.test_update_marker -v

deploy/olisar-update.sh leaves ``updating.json`` in the container's data directory while it
moves a VM onto a release (tests/test_update_script.py covers that side). What matters here
is the backend not reporting an update that isn't happening: once the new version is the
one answering, when a run died without cleaning up, or when the file is unreadable.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from olisar import updates


class UpdatingMarkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        env = mock.patch.dict(os.environ, {"OLISAR_DATA_DIR": str(self.dir)})
        env.start()
        self.addCleanup(env.stop)
        version = mock.patch.object(updates, "current_version", return_value="2.0.0-beta.5")
        version.start()
        self.addCleanup(version.stop)

    def mark(self, tag: str = "v2.0", *, ago: timedelta = timedelta(minutes=2), raw: str | None = None) -> None:
        at = (datetime.now(timezone.utc) - ago).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = raw if raw is not None else json.dumps({"tag": tag, "at": at})
        (self.dir / updates.UPDATING_FILE).write_text(body, encoding="utf-8")

    def test_nothing_updating_without_the_file(self) -> None:
        self.assertIsNone(updates.updating())

    def test_reports_the_release_being_applied(self) -> None:
        self.mark("v2.0")
        self.assertEqual(updates.updating(), {"to": "2.0"})

    def test_the_new_version_answering_is_not_still_updating(self) -> None:
        """The new container starts on the same volume before the script removes the file,
        so the file names the version that is already running."""
        self.mark("v2.0.beta-5")
        self.assertIsNone(updates.updating())

    def test_a_file_a_dead_run_left_behind_goes_stale(self) -> None:
        self.mark("v2.0", ago=updates.UPDATING_STALE_AFTER + timedelta(minutes=1))
        self.assertIsNone(updates.updating())

    def test_an_unreadable_file_is_no_update(self) -> None:
        for raw in ("", "not json", "[]", '{"tag": "v2.0"}', '{"tag": "v2.0", "at": "yesterday"}',
                    '{"tag": "v2.0", "at": "2026-09-26T10:00:00"}'):
            with self.subTest(raw=raw):
                self.mark(raw=raw)
                self.assertIsNone(updates.updating())


if __name__ == "__main__":
    unittest.main()
