"""Coverage for the pre-upgrade database snapshot.

Run:  uv run python -m unittest tests.test_db_backup -v

Schema evolution is forward-only, and one step genuinely destroys data: create_schema drops
a table whose primary key changed (extension_state did), silently resetting which
extensions are enabled. There was no snapshot and no schema version stamp, so an operator
who updated into a bad release had nothing to go back to.

The marker is written only *after* the migration succeeds — otherwise a crash mid-upgrade
would record the new version and skip the backup on the retry, which is exactly the run
that needs it most.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from olisar.runtime import dbbackup


def make_db(path: Path, rows: int = 1) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO t (id) VALUES (?)", [(i,) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def row_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT count(*) FROM t").fetchone()[0])
    finally:
        conn.close()


class DbBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.db = self.dir / "olisar.db"

    def snapshots(self) -> list[Path]:
        return sorted(self.dir.glob("olisar.db.pre-*"))

    def test_fresh_install_takes_no_snapshot(self) -> None:
        self.assertIsNone(dbbackup.before_migration(self.db, "1.3.1"))
        self.assertEqual(self.snapshots(), [])

    def test_first_run_with_an_existing_db_snapshots_as_unknown(self) -> None:
        """No marker but a database present — the first boot after this guard shipped, and
        the case where the schema's age is least known."""
        make_db(self.db, rows=3)
        out = dbbackup.before_migration(self.db, "1.3.1")
        self.assertIsNotNone(out)
        self.assertTrue(str(out).endswith(".pre-unknown"))
        self.assertEqual(row_count(Path(str(out))), 3)

    def test_snapshot_on_version_change(self) -> None:
        make_db(self.db, rows=2)
        dbbackup.record_version(self.db, "1.3.0")
        out = dbbackup.before_migration(self.db, "1.3.1")
        self.assertIsNotNone(out)
        self.assertTrue(str(out).endswith(".pre-1.3.0"))
        self.assertEqual(row_count(Path(str(out))), 2)

    def test_unchanged_version_is_a_no_op(self) -> None:
        make_db(self.db)
        dbbackup.record_version(self.db, "1.3.1")
        self.assertIsNone(dbbackup.before_migration(self.db, "1.3.1"))
        self.assertEqual(self.snapshots(), [])

    def test_retains_only_the_two_newest(self) -> None:
        make_db(self.db)
        for i, version in enumerate(["1.0.0", "1.1.0", "1.2.0", "1.3.0"]):
            dbbackup.record_version(self.db, version)
            snap = dbbackup.before_migration(self.db, f"1.{i + 1}.9")
            self.assertIsNotNone(snap)
            # Keep mtimes strictly ordered so the newest-first prune is deterministic.
            import os

            os.utime(str(snap), (1_700_000_000 + i, 1_700_000_000 + i))
        self.assertEqual(len(self.snapshots()), dbbackup.KEEP)

    def test_marker_is_only_written_when_asked(self) -> None:
        """before_migration must not stamp the version itself — a crash between it and
        create_schema would otherwise skip the backup on the next attempt."""
        make_db(self.db)
        dbbackup.before_migration(self.db, "1.3.1")
        self.assertFalse((self.dir / "olisar.db.version").exists())
        dbbackup.record_version(self.db, "1.3.1")
        self.assertEqual((self.dir / "olisar.db.version").read_text("utf-8"), "1.3.1")

    def test_version_with_path_characters_cannot_escape_the_directory(self) -> None:
        make_db(self.db)
        dbbackup.record_version(self.db, "../../etc/evil")
        out = dbbackup.before_migration(self.db, "1.3.1")
        self.assertIsNotNone(out)
        self.assertEqual(Path(str(out)).parent, self.dir)

    def test_failure_to_snapshot_does_not_raise(self) -> None:
        """A precaution that can fail a boot is worse than the risk it guards against."""
        self.db.write_text("not a database", encoding="utf-8")
        dbbackup.record_version(self.db, "1.3.0")
        self.assertIsNone(dbbackup.before_migration(self.db, "1.3.1"))


if __name__ == "__main__":
    unittest.main()
