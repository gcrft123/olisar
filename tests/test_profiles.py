"""Where each bot's data lives, and which database a process opens.

Run:  uv run python -m unittest tests.test_profiles -v

Every bot on the desktop app runs in its own process with its own data directory, and the
registry of bots sits above all of them. Two invariants matter for an upgrade: the original bot
keeps the directory it has always had (nothing moves), and a running bot's process stays on its
own database whichever bot the console is showing.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class ProfilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self._env = {k: os.environ.get(k) for k in ("OLISAR_DATA_DIR", "OLISAR_HOME")}
        os.environ["OLISAR_DATA_DIR"] = str(self.home)
        os.environ.pop("OLISAR_HOME", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_the_original_bot_keeps_its_directory(self) -> None:
        from olisar.runtime import profiles

        self.assertEqual(profiles.data_dir_for("default"), self.home)
        self.assertEqual(profiles.db_path_for("default"), self.home / "olisar.db")

    def test_every_other_bot_gets_its_own(self) -> None:
        from olisar.runtime import profiles

        pid = profiles.create("Second")["id"]
        self.assertEqual(profiles.data_dir_for(pid), self.home / "profiles" / pid)
        self.assertTrue((self.home / "profiles" / pid).is_dir())
        with self.assertRaises(KeyError):
            profiles.data_dir_for("gone")

    def test_a_worker_reads_the_registry_from_the_install_not_its_own_dir(self) -> None:
        from olisar.runtime import profiles

        pid = profiles.create("Second")["id"]
        os.environ["OLISAR_DATA_DIR"] = str(self.home / "profiles" / pid)
        os.environ["OLISAR_HOME"] = str(self.home)
        self.assertEqual([p["id"] for p in profiles.list()], ["default", pid])
        self.assertFalse((self.home / "profiles" / pid / "profiles.json").exists())

    def test_a_pinned_process_ignores_which_bot_is_on_screen(self) -> None:
        from olisar.db import engine
        from olisar.runtime import profiles

        pid = profiles.create("Second")["id"]
        engine.pin_database(str(profiles.db_path_for("default")))
        self.addCleanup(engine.pin_database, None)
        profiles.set_active(pid)
        self.assertEqual(engine.current_db_path(), str(self.home / "olisar.db"))
        engine.pin_database(None)
        self.assertEqual(engine.current_db_path(), str(self.home / "profiles" / pid / "olisar.db"))


class RegistryFileTests(unittest.TestCase):
    """profiles.json is the only list of an install's bots. A file that can't be read used to
    be replaced with a fresh one holding the original bot alone, and every other bot dropped
    off the list (their data still on disk, the console no longer knowing they exist)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        os.environ["OLISAR_DATA_DIR"] = str(self.home)
        self.addCleanup(os.environ.pop, "OLISAR_DATA_DIR", None)
        os.environ.pop("OLISAR_HOME", None)
        from olisar.runtime import profiles

        self.profiles = profiles
        self.second = profiles.create("Second")["id"]
        profiles.data_dir_for(self.second)  # as its process does when it starts
        profiles.rename("default", "Main")
        self.registry = self.home / "profiles.json"

    def names(self) -> list[str]:
        return [p["name"] for p in self.profiles.list()]

    def test_every_write_keeps_a_copy(self) -> None:
        self.assertEqual((self.home / "profiles.json.bak").read_text(), self.registry.read_text())

    def test_a_truncated_registry_falls_back_to_its_copy_and_isnt_replaced(self) -> None:
        self.registry.write_text('{"active": "default", "profi')
        self.assertEqual(self.names(), ["Main", "Second"])
        self.assertEqual(self.registry.read_text(), '{"active": "default", "profi')
        self.profiles.rename(self.second, "Renamed")  # the next change writes it whole again
        self.assertEqual(self.names(), ["Main", "Renamed"])
        self.assertIn('"Renamed"', self.registry.read_text())

    def test_with_no_readable_copy_the_bots_on_disk_are_kept(self) -> None:
        self.registry.write_text("")
        (self.home / "profiles.json.bak").write_text("{nope")
        (self.home / "olisar.db").touch()
        self.assertEqual([p["id"] for p in self.profiles.list()], ["default", self.second])
        self.assertEqual(self.registry.read_text(), "")  # nothing written over it
        self.assertTrue((self.home / "profiles.json.unreadable").exists())

    def test_the_file_is_on_disk_before_it_replaces_the_old_one(self) -> None:
        from unittest import mock

        order: list[str] = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch.object(os, "fsync", side_effect=lambda fd: (order.append("fsync"), real_fsync(fd))[1]), \
                mock.patch.object(os, "replace", side_effect=lambda a, b: (order.append("replace"), real_replace(a, b))[1]):
            self.profiles.rename(self.second, "Again")
        self.assertEqual(order[:2], ["fsync", "replace"])
        self.assertEqual(order.count("replace"), 2)  # the registry, then its copy


if __name__ == "__main__":
    unittest.main()
