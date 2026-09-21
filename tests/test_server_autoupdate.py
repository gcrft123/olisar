"""Coverage for when the app updates the operator's VM without being asked.

Run:  uv run python -m unittest tests.test_server_autoupdate -v

The VM used to update itself on a daily systemd timer. The client drives it now: it is the
side that learns a release exists — it just installed one on itself — so the launch after a
self-update carries the server along. That makes ``remote.decide`` a function that reaches
out and changes someone's server, from a trigger nobody pressed, so every reason it says
yes (and every reason it stands down) is pinned here.

The risky answers are the false yesses: a build that can't tell what version it is, and a
release the VM already pulled, failed and rolled back — which, retried on every launch,
would roll back on every launch.
"""

from __future__ import annotations

import unittest

from olisar.runtime.remote import decide
from olisar.updates import UNKNOWN_VERSION


class DecideTests(unittest.TestCase):
    def test_client_ahead_of_a_readable_server(self) -> None:
        """The plain case: the app updated itself, the VM is still behind."""
        self.assertEqual(
            decide(client="1.6.0", server="v1.5.0", synced="1.5.0", last={}), "client-ahead"
        )

    def test_level_with_the_server_does_nothing(self) -> None:
        self.assertEqual(decide(client="1.6.0", server="v1.6.0", synced="1.5.0", last={}), "")

    def test_a_server_ahead_of_the_app_is_left_alone(self) -> None:
        """An operator who declined this app's update doesn't get their VM rolled back."""
        self.assertEqual(decide(client="1.5.0", server="v1.6.0", synced="1.5.0", last={}), "")

    def test_tagged_and_bare_versions_compare_equal(self) -> None:
        """Image labels arrive as "v1.6.0", the app's own version as "1.6.0"."""
        self.assertEqual(decide(client="1.6.0", server="v1.6.0", synced="", last={}), "")

    def test_unreadable_server_falls_back_to_the_relaunch_signal(self) -> None:
        """No version to read (never started, or an image from before the OCI labels), so
        the app's own history answers instead: this build is newer than the one that last
        reconciled the VM, which is what a relaunch after a self-update looks like."""
        self.assertEqual(decide(client="1.6.0", server="", synced="1.5.0", last={}), "relaunched")

    def test_unreadable_server_and_no_history_stands_down(self) -> None:
        """A VM we've never reconciled and can't read: don't touch it blind."""
        self.assertEqual(decide(client="1.6.0", server="", synced="", last={}), "")

    def test_same_build_relaunching_is_not_a_trigger(self) -> None:
        """Ordinary launches must not re-run an update — only ones onto a newer build."""
        self.assertEqual(decide(client="1.6.0", server="", synced="1.6.0", last={}), "")

    def test_a_downgraded_app_does_not_push_a_release(self) -> None:
        self.assertEqual(decide(client="1.5.0", server="", synced="1.6.0", last={}), "")

    def test_a_build_that_cannot_tell_its_version_stands_down(self) -> None:
        """A source run with no package metadata reports the sentinel; comparing it to a
        real server version would say "client-ahead" about a version that doesn't exist."""
        self.assertEqual(
            decide(client=UNKNOWN_VERSION, server="v1.5.0", synced="1.5.0", last={}), ""
        )
        self.assertEqual(decide(client="", server="v1.5.0", synced="1.5.0", last={}), "")

    def test_a_rollback_is_not_retried_from_the_same_build(self) -> None:
        """The VM pulled the release, failed its healthcheck and restored the old image.
        Trying again from this build would repeat that on every single launch."""
        last = {"rolled_back": True, "tag": "v1.6.0", "ok": False}
        self.assertEqual(decide(client="1.6.0", server="v1.5.0", synced="1.6.0", last=last), "")

    def test_a_rollback_is_retried_once_the_app_moves_on(self) -> None:
        """A newer build is a new situation — the release that broke may be fixed in it."""
        last = {"rolled_back": True, "tag": "v1.6.0", "ok": False}
        self.assertEqual(
            decide(client="1.7.0", server="v1.5.0", synced="1.6.0", last=last), "client-ahead"
        )

    def test_a_successful_last_update_is_no_obstacle(self) -> None:
        last = {"rolled_back": False, "updated": True, "tag": "v1.5.0", "ok": True}
        self.assertEqual(
            decide(client="1.6.0", server="v1.5.0", synced="1.5.0", last=last), "client-ahead"
        )


if __name__ == "__main__":
    unittest.main()
