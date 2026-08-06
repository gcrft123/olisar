"""Coverage for parsing the server-mode status probe.

Run:  uv run python -m unittest tests.test_remote_status -v

The control panel used to infer the remote container's state by regexing ``docker compose
ps`` for "running|Up" and grepping logs for a …ts.net URL. Three consequences, all covered
here:
  * a crashlooping container under ``restart: unless-stopped`` reported "Running" — the
    image has defined a HEALTHCHECK all along and the verdict was simply discarded
  * there was no way to learn which version was deployed
  * a URL-grep miss disabled "Open console" on a perfectly healthy server

The probe now reads Docker's own health verdict, the image's OCI labels, and the backend's
state.json — with the old log-scrape kept only as a fallback for containers built before
state.json existed. That fallback is the one that has to keep working, so it's asserted
explicitly.
"""

from __future__ import annotations

import json
import unittest

from olisar.runtime.remote import parse_probe

URL = "https://olisar.example.ts.net"
DIGEST = "sha256:1111111111111111111111111111111111111111111111111111111111111111"


def probe(
    *, container: str = "", image: str = "", state: str = "", ps: str = "", logs: str = "", url: str = ""
) -> str:
    """Assemble probe output the way the remote bash script emits it."""
    return (
        f"__OLISAR_CONTAINER__\n{container}\n"
        f"__OLISAR_IMAGE__\n{image}\n"
        f"__OLISAR_STATE__\n{state}\n"
        f"__OLISAR_PS__\n{ps}\n"
        f"__OLISAR_LOGS__\n{logs}\n"
        f"__OLISAR_URL__\n{url}\n"
    )


class ParseProbeTests(unittest.TestCase):
    def test_running_and_healthy(self) -> None:
        out = parse_probe(
            probe(
                container="running|healthy",
                image=f"1.3.1|abc123|ghcr.io/gcrft123/olisar@{DIGEST}",
                state=json.dumps({"public_url": URL, "version": "1.3.1"}),
            )
        )
        self.assertTrue(out["running"])
        self.assertEqual(out["health"], "healthy")
        self.assertEqual(out["version"], "1.3.1")
        self.assertEqual(out["revision"], "abc123")
        self.assertEqual(out["digest"], DIGEST)
        self.assertEqual(out["url"], URL)

    def test_crashlooping_container_is_not_reported_as_fine(self) -> None:
        """The bug this replaced: `restart: unless-stopped` keeps the container "running"
        while every request fails, and the old ps-regex called that healthy."""
        out = parse_probe(probe(container="running|unhealthy", ps="olisar  running"))
        self.assertTrue(out["running"])
        self.assertEqual(out["health"], "unhealthy")

    def test_starting_is_distinct_from_healthy(self) -> None:
        out = parse_probe(probe(container="running|starting"))
        self.assertEqual(out["health"], "starting")

    def test_stopped_container_still_reports_its_version(self) -> None:
        """Version comes from the image, not the container, so it survives a stop — that's
        what lets the panel say what a stopped server *would* boot."""
        out = parse_probe(
            probe(container="exited|", image=f"1.3.0||ghcr.io/gcrft123/olisar@{DIGEST}")
        )
        self.assertFalse(out["running"])
        self.assertEqual(out["state"], "exited")
        self.assertEqual(out["health"], "")
        self.assertEqual(out["version"], "1.3.0")

    def test_image_without_oci_labels_reports_empty_not_no_value(self) -> None:
        """A Go template renders a missing map key as the literal `<no value>`; that must
        never reach the UI."""
        out = parse_probe(probe(container="running|healthy", image="<no value>|<no value>|"))
        self.assertEqual(out["version"], "")
        self.assertEqual(out["revision"], "")
        self.assertEqual(out["digest"], "")

    def test_old_container_falls_back_to_the_log_scrape(self) -> None:
        """A VM still running a pre-state.json image: `docker exec cat` yields nothing, so
        the URL has to come from the log grep the script still performs."""
        out = parse_probe(probe(container="running|healthy", state="", url=URL))
        self.assertEqual(out["url"], URL)

    def test_url_falls_back_to_the_recent_log_tail(self) -> None:
        out = parse_probe(
            probe(container="running|healthy", logs=f"OLISAR_FUNNEL_URL={URL}\nbot ready")
        )
        self.assertEqual(out["url"], URL)

    def test_corrupt_state_json_degrades_to_the_fallback(self) -> None:
        out = parse_probe(probe(container="running|healthy", state="{truncated", url=URL))
        self.assertEqual(out["url"], URL)

    def test_no_container_id_falls_back_to_the_ps_regex(self) -> None:
        """Compose v1 can't give us a container id; reporting "Stopped" would be a lie."""
        out = parse_probe(probe(container="", ps="olisar   Up 3 hours"))
        self.assertTrue(out["running"])

    def test_nothing_deployed_yet(self) -> None:
        out = parse_probe(probe())
        self.assertFalse(out["running"])
        self.assertEqual(out["version"], "")
        self.assertEqual(out["url"], "")

    def test_sections_do_not_bleed_into_each_other(self) -> None:
        """Docker chatter in one section must not be read as another's payload."""
        out = parse_probe(
            probe(
                container="running|healthy",
                image=f"1.3.1||ghcr.io/gcrft123/olisar@{DIGEST}",
                state=json.dumps({"public_url": URL}),
                logs="Cannot connect to the Docker daemon\nrunning|unhealthy",
            )
        )
        self.assertEqual(out["health"], "healthy")
        self.assertEqual(out["version"], "1.3.1")


if __name__ == "__main__":
    unittest.main()
