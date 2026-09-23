"""Coverage for when the app updates the operator's VM without being asked.

Run:  uv run python -m unittest tests.test_server_autoupdate -v

The VM used to update itself on a daily systemd timer. The client drives it now: it is the
side that learns a release exists — it just installed one on itself — so the launch after a
self-update carries the server along. That makes ``remote.decide`` a function that reaches
out and changes someone's server, from a trigger nobody pressed, so every reason it says
yes (and every reason it stands down) is pinned here.

The risky answers are the false yesses — a build that can't tell what version it is, or a
reason that never stops being true, which would re-run the script on every launch for as
long as the app sits at that version. The two are held apart by ``server_synced_version``:
``decide`` won't repeat a run this build already made, and ``decided`` is what decides
whether a run counted as one.
"""

from __future__ import annotations

import unittest

from olisar.runtime.remote import decide, decided
from olisar.updates import UNKNOWN_VERSION


class DecideTests(unittest.TestCase):
    def test_client_ahead_of_a_readable_server(self) -> None:
        """The plain case: the app updated itself, the VM is still behind."""
        self.assertEqual(decide(client="1.6.0", server="v1.5.0", synced="1.5.0"), "client-ahead")

    def test_level_with_the_server_does_nothing(self) -> None:
        self.assertEqual(decide(client="1.6.0", server="v1.6.0", synced="1.5.0"), "")

    def test_a_server_ahead_of_the_app_is_left_alone(self) -> None:
        """An operator who declined this app's update doesn't get their VM rolled back."""
        self.assertEqual(decide(client="1.5.0", server="v1.6.0", synced="1.5.0"), "")

    def test_tagged_and_bare_versions_compare_equal(self) -> None:
        """Image labels arrive as "v1.6.0", the app's own version as "1.6.0"."""
        self.assertEqual(decide(client="1.6.0", server="v1.6.0", synced=""), "")

    def test_unreadable_server_falls_back_to_the_relaunch_signal(self) -> None:
        """No version to read (never started, or an image from before the OCI labels), so
        the app's own history answers instead: this build is newer than the one that last
        reconciled the VM, which is what a relaunch after a self-update looks like."""
        self.assertEqual(decide(client="1.6.0", server="", synced="1.5.0"), "relaunched")

    def test_unreadable_server_and_no_history_stands_down(self) -> None:
        """A VM we've never reconciled and can't read: don't touch it blind."""
        self.assertEqual(decide(client="1.6.0", server="", synced=""), "")

    def test_same_build_relaunching_is_not_a_trigger(self) -> None:
        """Ordinary launches must not re-run an update — only ones onto a newer build."""
        self.assertEqual(decide(client="1.6.0", server="", synced="1.6.0"), "")

    def test_a_downgraded_app_does_not_push_a_release(self) -> None:
        self.assertEqual(decide(client="1.5.0", server="", synced="1.6.0"), "")

    def test_a_build_that_cannot_tell_its_version_stands_down(self) -> None:
        """A source run with no package metadata reports the sentinel; comparing it to a
        real server version would say "client-ahead" about a version that doesn't exist."""
        self.assertEqual(decide(client=UNKNOWN_VERSION, server="v1.5.0", synced="1.5.0"), "")
        self.assertEqual(decide(client="", server="v1.5.0", synced="1.5.0"), "")

    def test_a_stopped_server_is_staged_once_not_on_every_launch(self) -> None:
        """The convergence case. A stopped VM is repinned but deliberately left down, so it
        goes on reporting the image it last ran — the version comparison stays true forever.
        The stamp from that first run is the only thing that stops the app re-running the
        script, re-locking the panel and re-pulling on every single launch."""
        self.assertEqual(decide(client="1.6.0", server="v1.5.0", synced="1.5.0"), "client-ahead")
        # …the run stages v1.6.0, the container stays down on v1.5.0, and synced becomes 1.6.0.
        self.assertEqual(decide(client="1.6.0", server="v1.5.0", synced="1.6.0"), "")

    def test_an_app_ahead_of_the_newest_release_settles(self) -> None:
        """Same shape without a stopped server: a build whose version is ahead of anything
        published (a source run at a bumped pyproject version). The script resolves "latest"
        to what the VM already runs and changes nothing, so only the stamp ends it."""
        self.assertEqual(decide(client="9.9.9", server="v1.5.0", synced="9.9.9"), "")

    def test_a_freshly_adopted_vm_is_reconciled(self) -> None:
        """The stamp describes one particular VM, so ``connect`` clears it when adopting
        another. Without that clearing this build would read its own earlier run against a
        *different* server as "already had my go" and leave the new one behind."""
        self.assertEqual(decide(client="1.6.0", server="v1.4.0", synced=""), "client-ahead")

    def test_a_rollback_is_not_retried_from_the_same_build(self) -> None:
        """The VM pulled the release, failed its healthcheck and restored the old image —
        a firm answer, so it stamps. Trying again from this build would repeat it."""
        self.assertEqual(decide(client="1.6.0", server="v1.5.0", synced="1.6.0"), "")

    def test_a_rollback_is_retried_once_the_app_moves_on(self) -> None:
        """A newer build is a new situation — the release that broke may be fixed in it."""
        self.assertEqual(decide(client="1.7.0", server="v1.5.0", synced="1.6.0"), "client-ahead")

    def test_the_first_beta_carries_a_1_x_server_along(self) -> None:
        """The app reports its semver spelling, the image label the tag's."""
        self.assertEqual(decide(client="2.0.0-beta.1", server="v1.5.0", synced="1.5.0"), "client-ahead")

    def test_a_beta_app_level_with_its_server_does_nothing(self) -> None:
        self.assertEqual(decide(client="2.0.0-beta.1", server="v2.0.beta-1", synced=""), "")

    def test_the_stable_release_overtakes_a_beta_server(self) -> None:
        self.assertEqual(decide(client="2.0.0", server="v2.0.beta-3", synced="2.0.0-beta.3"), "client-ahead")

    def test_a_beta_server_is_not_behind_a_stable_app_it_leads(self) -> None:
        """Switched to stable while the VM kept the newer beta: nothing to do until a stable
        release passes it."""
        self.assertEqual(decide(client="1.5.0", server="v2.0.beta-1", synced="1.5.0"), "")


class DecidedTests(unittest.TestCase):
    """Which outcomes count as "this build has had its go at this VM".

    Getting this wrong in either direction is a bug with a long tail: too generous and a
    transient network failure stands the auto-update down until the app itself updates; too
    stingy and ``decide`` never converges.
    """

    def test_an_applied_release_counts(self) -> None:
        self.assertTrue(decided({"status": "updated", "ok": True}))

    def test_already_current_counts(self) -> None:
        self.assertTrue(decided({"status": "up-to-date", "ok": True}))

    def test_staged_onto_a_stopped_server_counts(self) -> None:
        """The repin happened; the container is down because the operator wants it down."""
        self.assertTrue(decided({"status": "staged", "ok": True, "updated": True}))

    def test_a_rollback_counts(self) -> None:
        """Not a success, but an answer: the VM refused this release."""
        self.assertTrue(decided({"status": "rolled-back", "ok": False, "rolled_back": True}))
        self.assertTrue(decided({"status": "rollback-unhealthy", "ok": False}))
        self.assertTrue(decided({"status": "unhealthy", "ok": False}))

    def test_never_reaching_a_release_does_not_count(self) -> None:
        """GitHub or GHCR was unreachable, so nothing about this VM was settled. Stamping
        here would mean one bad minute of connectivity silently disables the feature until
        the operator's app next updates itself."""
        self.assertFalse(decided({"status": "no-release", "ok": False}))
        self.assertFalse(decided({"status": "pull-failed", "ok": False}))
        self.assertFalse(decided({"status": "no-digest", "ok": False}))

    def test_no_outcome_at_all_does_not_count(self) -> None:
        """The script never ran, or wrote nothing we could read."""
        self.assertFalse(decided({}))
        self.assertFalse(decided({"status": ""}))


if __name__ == "__main__":
    unittest.main()
