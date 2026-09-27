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
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from olisar import discord_app
from olisar.runtime import remote
from olisar.runtime.remote import docker_time, parse_bot_on, parse_probe

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

    def test_loopback_url_is_not_a_console_address(self) -> None:
        """With the funnel down, a backend from before it published why left its loopback
        origin as ``public_url``, and "Open console" opened that on the operator's machine."""
        out = parse_probe(
            probe(container="running|healthy", state=json.dumps({"public_url": "http://127.0.0.1:8000"}))
        )
        self.assertEqual(out["url"], "")
        self.assertEqual(out["console_error"], "Tailscale didn't connect.")

    def test_a_published_tunnel_error_wins_over_any_url(self) -> None:
        """A failed funnel can sit beside a stale …ts.net host from an earlier boot, and the
        log scrape must not dig one back up either."""
        state = json.dumps({
            "public_url": URL,
            "tunnel_error": "couldn't join your tailnet: tsnet.Up: backend: invalid key: API key does not exist",
        })
        out = parse_probe(probe(container="running|healthy", state=state, url=URL, logs=f"up at {URL}"))
        self.assertEqual(out["url"], "")
        self.assertIn("rejected the auth key", out["console_error"])

    def test_other_tunnel_errors_are_passed_through(self) -> None:
        state = json.dumps({"tunnel_error": "Funnel not available; enable it at https://login.tailscale.com/f/funnel"})
        out = parse_probe(probe(container="running|healthy", state=state))
        self.assertEqual(
            out["console_error"],
            "Tailscale couldn't connect: Funnel not available; enable it at https://login.tailscale.com/f/funnel",
        )

    def test_a_stopped_server_has_no_console_error(self) -> None:
        """Nothing to reach while it's stopped, and nothing a new key would fix."""
        out = parse_probe(probe(container="exited|", state=json.dumps({"tunnel_error": "invalid key"})))
        self.assertEqual(out["console_error"], "")

    def test_a_healthy_console_has_no_error(self) -> None:
        out = parse_probe(probe(container="running|healthy", state=json.dumps({"public_url": URL})))
        self.assertEqual((out["url"], out["console_error"]), (URL, ""))

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


# What `docker inspect --format` prints for _FMT_CONTAINER: status|health|StartedAt|each
# healthcheck's End, JSON-quoted. Docker keeps the last five checks.
STARTED = "2026-09-26T08:15:02.123456789Z"
CHECKS = '"2026-09-26T09:14:02.401927512Z""2026-09-26T09:14:32.466503104Z""2026-09-26T09:15:02.532911867Z"'


class StartedAndCheckedTests(unittest.TestCase):
    """When the container started and when its healthcheck last ran, for the uptime and the
    heartbeat on the server screen."""

    def test_running_container_reports_both_in_utc_to_the_millisecond(self) -> None:
        out = parse_probe(probe(container=f"running|healthy|{STARTED}|{CHECKS}"))
        self.assertEqual(out["started_at"], "2026-09-26T08:15:02.123Z")
        self.assertEqual(out["health_at"], "2026-09-26T09:15:02.532Z")  # the last check, not the first
        self.assertEqual((out["state"], out["health"]), ("running", "healthy"))

    def test_a_stopped_container_has_no_start_time(self) -> None:
        """Docker keeps the last run's StartedAt after a stop; the uptime it implies is wrong."""
        out = parse_probe(probe(container=f"exited|unhealthy|{STARTED}|{CHECKS}"))
        self.assertEqual(out["started_at"], "")
        self.assertFalse(out["running"])

    def test_a_container_that_never_started_reports_docker_zero_time_as_nothing(self) -> None:
        out = parse_probe(probe(container="created||0001-01-01T00:00:00Z|"))
        self.assertEqual((out["started_at"], out["health_at"]), ("", ""))

    def test_no_healthcheck_means_no_check_time(self) -> None:
        out = parse_probe(probe(container=f"running||{STARTED}|"))
        self.assertEqual(out["health"], "")
        self.assertEqual(out["health_at"], "")
        self.assertEqual(out["started_at"], "2026-09-26T08:15:02.123Z")

    def test_a_first_check_still_running_has_no_log_yet(self) -> None:
        out = parse_probe(probe(container=f"running|starting|{STARTED}|"))
        self.assertEqual((out["health"], out["health_at"]), ("starting", ""))

    def test_a_daemon_off_utc_is_converted(self) -> None:
        """Health log times are the daemon's local time; the VM may not be on UTC."""
        out = parse_probe(probe(container=f'running|healthy|{STARTED}|"2026-09-26T11:15:02.5+02:00"'))
        self.assertEqual(out["health_at"], "2026-09-26T09:15:02.500Z")

    def test_old_two_field_output_still_parses(self) -> None:
        """A probe line without the new fields (or a hand-run one) keeps its old meaning."""
        out = parse_probe(probe(container="running|healthy"))
        self.assertEqual((out["running"], out["health"]), (True, "healthy"))
        self.assertEqual((out["started_at"], out["health_at"]), ("", ""))

    def test_new_fields_do_not_bleed_into_health(self) -> None:
        out = parse_probe(probe(container=f"running|unhealthy|{STARTED}|{CHECKS}"))
        self.assertEqual(out["health"], "unhealthy")

    def test_a_powered_down_bot_is_reported_while_the_container_runs(self) -> None:
        """The console's power button stops Discord, not Docker. The panel has to tell them apart."""
        out = parse_probe(probe(
            container="running|healthy",
            state=json.dumps({"public_url": URL, "bot_running": False}),
        ))
        self.assertTrue(out["running"])
        self.assertIs(out["bot_running"], False)
        self.assertEqual(out["url"], URL)

    def test_an_older_state_file_does_not_read_as_powered_down(self) -> None:
        """Images from before this field existed must stay 'Running', not 'Powered down'."""
        out = parse_probe(probe(container="running|healthy", state=json.dumps({"public_url": URL})))
        self.assertIsNone(out["bot_running"])
        self.assertTrue(out["running"])

    def test_bot_on_answer(self) -> None:
        self.assertEqual(parse_bot_on('{"ok": true, "running": true}\n'), {"ok": True, "running": True})

    def test_bot_on_404_is_an_old_image(self) -> None:
        out = parse_bot_on('{"ok": false, "status": 404}\n')
        self.assertFalse(out["ok"])
        self.assertIn("Open the console", out["error"])

    def test_docker_time_edge_cases(self) -> None:
        self.assertEqual(docker_time(""), "")
        self.assertEqual(docker_time("<no value>"), "")
        self.assertEqual(docker_time("not a time"), "")
        self.assertEqual(docker_time("2026-09-26T08:15:02Z"), "2026-09-26T08:15:02.000Z")
        # Go's own time format, in case a template prints one bare.
        self.assertEqual(
            docker_time("2026-09-26 08:15:02.123456789 +0000 UTC"), "2026-09-26T08:15:02.123Z"
        )


class DiscordCheckTests(unittest.IsolatedAsyncioTestCase):
    """The server bot's name and face for the final screen, from the same application read
    that checks its sign-in address and intents."""

    async def check(self, bot: dict, **app) -> dict:
        body = {"id": "1500", "name": "Olisar App", "flags": (1 << 19) | (1 << 15),
                "redirect_uris": ["https://olisar.example.ts.net/auth/callback"], "bot": bot, **app}
        cfg = SimpleNamespace(server_host="203.0.113.7", server_ssh_user="ubuntu", server_app_dir="")
        with patch.object(remote, "_load", AsyncMock(return_value=cfg)), \
                patch.object(remote, "_vm_token", AsyncMock(return_value="tok")), \
                patch.object(discord_app, "_call", AsyncMock(return_value=(200, body))):
            return await remote.discord_check(URL)

    async def test_the_name_discord_shows_and_the_avatar_url(self) -> None:
        out = await self.check({"id": "42", "username": "everest_bot", "global_name": "Everest", "avatar": "abc"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["bot_name"], "Everest")
        self.assertEqual(out["bot_avatar"], "https://cdn.discordapp.com/avatars/42/abc.png")
        self.assertTrue(out["added"])

    async def test_a_bot_without_a_display_name_goes_by_its_username(self) -> None:
        out = await self.check({"id": "42", "username": "everest_bot", "global_name": None, "avatar": None})
        self.assertEqual(out["bot_name"], "everest_bot")
        self.assertEqual(out["bot_avatar"], "")

    async def test_a_bot_without_an_avatar_shows_the_application_icon(self) -> None:
        out = await self.check({"id": "42", "username": "everest_bot", "avatar": None}, icon="ic0n")
        self.assertEqual(out["bot_avatar"], "https://cdn.discordapp.com/app-icons/1500/ic0n.png")


if __name__ == "__main__":
    unittest.main()
