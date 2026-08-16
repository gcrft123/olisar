"""The measuring lock: one command owns the instance and the guild at a time.

Regression cover for a silent corruption. Two `arena ab` runs shared one instance; both
kept producing runs, neither failed, and every run recorded a variant name that was not
reliably the variant that produced it. The bug survived a check for a running command
because one was invoked as `python -m arena` and the check looked for `arena.cli`.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from arena import exclusive
from arena.cli import _EXCLUSIVE


class _Cfg:
    """Just enough of ArenaConfig for the lock, which only reads data_dir."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


class Lock(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _Cfg(Path(self._tmp.name))
        self.path = self.cfg.data_dir / exclusive.LOCK_NAME
        self.addCleanup(self._tmp.cleanup)

    def test_holds_then_releases(self):
        with exclusive.held(self.cfg, "ab"):
            self.assertTrue(self.path.is_file())
            self.assertEqual(json.loads(self.path.read_text())["pid"], os.getpid())
        self.assertFalse(self.path.is_file())

    def test_released_even_when_the_command_raises(self):
        with self.assertRaises(ValueError):
            with exclusive.held(self.cfg, "run"):
                raise ValueError("scenario blew up")
        self.assertFalse(self.path.is_file())

    def test_second_command_is_refused(self):
        """The actual bug: a live owner must block, not queue and not proceed."""
        self.path.write_text(json.dumps({"pid": 424242, "command": "ab"}))
        with mock.patch.object(exclusive, "_alive", return_value=True):
            with self.assertRaises(exclusive.Busy) as caught:
                with exclusive.held(self.cfg, "overnight"):
                    self.fail("should not have acquired the lock")
        message = str(caught.exception)
        self.assertIn("424242", message)
        self.assertIn("ab", message)
        self.assertIn("kill 424242", message)  # actionable, not just a complaint

    def test_dead_owner_is_taken_over(self):
        """A killed run must not wedge the arena until someone finds a stale file."""
        self.path.write_text(json.dumps({"pid": 424242, "command": "ab"}))
        with mock.patch.object(exclusive, "_alive", return_value=False):
            with exclusive.held(self.cfg, "loop"):
                self.assertEqual(json.loads(self.path.read_text())["pid"], os.getpid())
        self.assertFalse(self.path.is_file())

    def test_unreadable_lock_does_not_wedge(self):
        self.path.write_text("{ this is not json")
        with exclusive.held(self.cfg, "run"):
            self.assertEqual(json.loads(self.path.read_text())["command"], "run")

    def test_takeover_does_not_delete_someone_elses_lock(self):
        """If another process legitimately took over, releasing must leave it alone."""
        with exclusive.held(self.cfg, "run"):
            self.path.write_text(json.dumps({"pid": 424242, "command": "ab"}))
        self.assertTrue(self.path.is_file())
        self.assertEqual(json.loads(self.path.read_text())["pid"], 424242)


class WhichCommandsLock(unittest.TestCase):
    def test_every_measuring_command_is_covered(self):
        for command in ("run", "ab", "loop", "overnight", "redteam"):
            self.assertIn(command, _EXCLUSIVE)

    def test_watching_a_run_stays_possible(self):
        """Read-only commands must not lock, or a round in flight can't be observed."""
        for command in ("status", "logs", "report", "journal", "scenarios", "calibrate"):
            self.assertNotIn(command, _EXCLUSIVE)


if __name__ == "__main__":
    unittest.main()
