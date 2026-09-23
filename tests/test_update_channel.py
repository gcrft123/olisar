"""Coverage for the stable/beta update channel.

Run:  uv run python -m unittest tests.test_update_channel -v

The channel decides which releases an install is offered and, in server-hosting mode, which
one the app puts on the operator's VM. The failures worth pinning are the ones that move
someone onto a build they didn't choose: a beta reaching a stable install, a first beta that
can't find the next one, and a VM pushed backwards after a switch from beta to stable.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from olisar import updates
from olisar.runtime.remote import hold


def _rel(tag: str, *, prerelease: bool = False, draft: bool = False) -> dict:
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "html_url": f"https://x/{tag}"}


class PickReleaseTests(unittest.TestCase):
    RELEASES = [
        _rel("v2.0.beta-2", prerelease=True),
        _rel("v2.0.beta-1", prerelease=True),
        _rel("v1.5.0"),
        _rel("v1.4.5"),
    ]

    def test_stable_skips_betas(self) -> None:
        self.assertEqual(updates.pick_release(self.RELEASES, "stable")["tag_name"], "v1.5.0")

    def test_beta_takes_the_newest_of_either_kind(self) -> None:
        self.assertEqual(updates.pick_release(self.RELEASES, "beta")["tag_name"], "v2.0.beta-2")

    def test_beta_moves_onto_the_stable_release_when_it_ships(self) -> None:
        releases = [_rel("v2.0"), *self.RELEASES]
        self.assertEqual(updates.pick_release(releases, "beta")["tag_name"], "v2.0")

    def test_stable_refuses_a_beta_that_was_not_flagged(self) -> None:
        """Published by hand without the pre-release box ticked. GitHub would call it the
        latest release; every stable install would otherwise take it."""
        self.assertEqual(updates.pick_release([_rel("v2.0.beta-1")], "stable"), None)

    def test_drafts_and_non_version_tags_never_count(self) -> None:
        releases = [_rel("v9.0", draft=True), _rel("nightly", prerelease=True), _rel("v1.5.0")]
        self.assertEqual(updates.pick_release(releases, "beta")["tag_name"], "v1.5.0")


class ChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = mock.patch.dict(os.environ, {"OLISAR_DATA_DIR": self.dir.name})
        env.start()
        self.addCleanup(env.stop)

    def _running(self, version: str):
        updates.current_version.cache_clear()
        self.addCleanup(updates.current_version.cache_clear)
        return mock.patch.dict(os.environ, {"OLISAR_VERSION": version})

    def test_a_stable_build_defaults_to_stable(self) -> None:
        with self._running("1.5.0"):
            self.assertEqual(updates.channel(), "stable")

    def test_a_beta_build_defaults_to_beta(self) -> None:
        """The first beta is installed by hand, since a stable build never sees one. That
        alone has to be enough to get the second."""
        with self._running("2.0.0-beta.1"):
            self.assertEqual(updates.channel(), "beta")

    def test_a_saved_choice_wins(self) -> None:
        with self._running("2.0.0-beta.1"):
            updates.set_channel("stable")
            self.assertEqual(updates.channel(), "stable")

    def test_an_unknown_channel_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            updates.set_channel("nightly")

    def test_a_corrupt_file_falls_back_to_the_default(self) -> None:
        with open(os.path.join(self.dir.name, updates.CHANNEL_FILE), "w") as fh:
            fh.write("{not json")
        with self._running("1.5.0"):
            self.assertEqual(updates.channel(), "stable")


class HoldTests(unittest.TestCase):
    """When the app must not run the VM's update script, because it would pin an older
    release than the one the VM runs."""

    def test_a_newer_target_runs(self) -> None:
        self.assertIsNone(hold(target="v2.0.beta-2", server="v2.0.beta-1", channel="beta"))

    def test_the_same_release_runs(self) -> None:
        """The script says "already on it" by itself, and --start still has work to do."""
        self.assertIsNone(hold(target="v2.0", server="v2.0", channel="stable"))

    def test_a_server_past_the_target_is_left_alone(self) -> None:
        """Switched from beta to stable: the newest stable is older than the beta on the VM."""
        result = hold(target="v1.5.0", server="v2.0.beta-2", channel="stable")
        self.assertEqual(result["status"], "server-ahead")
        self.assertTrue(result["ok"])

    def test_an_unresolved_beta_does_not_fall_back_to_stable(self) -> None:
        """With no --tag the script takes GitHub's latest release, which is stable."""
        result = hold(target=None, server="v2.0.beta-1", channel="beta")
        self.assertEqual(result["status"], "no-release")
        self.assertFalse(result["ok"])

    def test_an_unresolved_stable_lets_the_script_find_it(self) -> None:
        """Today's behavior: the VM asks GitHub itself, which is right for stable."""
        self.assertIsNone(hold(target=None, server="v1.5.0", channel="stable"))

    def test_an_unreadable_server_runs(self) -> None:
        self.assertIsNone(hold(target="v2.0", server="", channel="stable"))


if __name__ == "__main__":
    unittest.main()
