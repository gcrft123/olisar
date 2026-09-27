"""Coverage for deploy/olisar-update.sh — the VM's self-update.

Run:  uv run python -m unittest tests.test_update_script -v

This script is the only thing that puts a version onto a server-mode VM, and every trigger
runs it — the app's automatic reconcile at launch and a hand-run on the VM — so a bug here
is a bug everywhere. It's also the hardest thing in the tree to test by hand — it needs a
VM, a release, and a deliberately broken image to see the interesting path.

So we stub ``docker``/``curl``/``sudo`` on PATH and drive the real script. What matters:
the deployed image is pinned to an immutable digest (not a mutable tag), a release that
fails its healthcheck is rolled back rather than left broken, and a stopped server is
never silently started.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "deploy" / "olisar-update.sh"

TAG = "v9.9.9"
NEW_DIGEST = "sha256:" + "a" * 64
OLD_DIGEST = "sha256:" + "b" * 64
IMAGE = "ghcr.io/gcrft123/olisar"

# A fake `docker` whose behaviour is driven by files in $STUB_DIR, so a test can say
# "report unhealthy" or "fail the pull" without regenerating the stub.
DOCKER_STUB = r"""#!/usr/bin/env bash
STATE_DIR="$STUB_DIR"
log() { echo "$*" >> "$STATE_DIR/calls.log"; }
log "docker $*"
# An image's ID, made up from the digest it's pinned by.
id_of() { echo "sha256:id-${1##*@sha256:}" | cut -c1-24; }
# The version CI stamps on an image: $STUB_DIR/labels has a "<digest> <version>" line per image.
label_of() { grep -m1 "^${1##*@} " "$STATE_DIR/labels" 2>/dev/null | cut -d' ' -f2; }

case "$1 $2" in
  "compose version") exit 0 ;;
esac

if [ "$1" = "compose" ]; then
  shift
  case "$1" in
    ps)   [ -f "$STATE_DIR/running" ] && echo "fakecontainerid"; exit 0 ;;
    up)
      # up_fails: compose refuses (a bad .env, say), and whatever was running stays up.
      # up_keeps_old: compose says it's done, but the old container is still the one up.
      [ -f "$STATE_DIR/up_fails" ] && { echo "env file .env: bad line" >&2; exit 1; }
      touch "$STATE_DIR/running"
      [ -f "$STATE_DIR/up_keeps_old" ] || grep -m1 'image:' docker-compose.yml | awk '{print $2}' > "$STATE_DIR/container_ref"
      exit 0 ;;
    stop) rm -f "$STATE_DIR/running"; exit 0 ;;
    logs) echo "fake container log line"; exit 0 ;;
    exec)
      # `compose exec -T olisar <cmd…>` runs in the container, whose data directory is
      # $STUB_DIR/data here.
      shift; [ "$1" = "-T" ] && shift; shift
      mkdir -p "$STATE_DIR/data"
      set -- "${@//\/var\/lib\/olisar/$STATE_DIR/data}"
      "$@"; exit $? ;;
    *) exit 0 ;;
  esac
fi

case "$1" in
  pull)
    # What the console could read while the pull ran.
    [ -f "$STATE_DIR/data/updating.json" ] && cp "$STATE_DIR/data/updating.json" "$STATE_DIR/updating_during_pull.json"
    [ -f "$STATE_DIR/pull_fails" ] && { echo "manifest unknown" >&2; exit 1; }
    exit 0 ;;
  inspect)
    # `docker inspect --format <fmt> <id>` — the script asks for status then health.
    fmt="$3"
    case "$fmt" in
      *Config.Image*) ref="$(cat "$STATE_DIR/container_ref" 2>/dev/null)"; echo "$(id_of "$ref")|$ref" ;;
      *State.Status*) cat "$STATE_DIR/status" 2>/dev/null || echo running ;;
      *State.Health*) cat "$STATE_DIR/health" 2>/dev/null || echo healthy ;;
    esac
    exit 0 ;;
  image)
    case "$2" in
      inspect)
        # `docker image inspect --format <fmt> <ref>`
        case "$4" in
          *RepoDigests*) echo "${IMAGE_REF_DIGEST}" ;;
          *image.version*) label_of "$5" ;;
          *.Id*) id_of "$5" ;;
        esac
        exit 0 ;;
      prune)   touch "$STATE_DIR/pruned"; exit 0 ;;
    esac ;;
esac
exit 0
"""

CURL_STUB = r"""#!/usr/bin/env bash
# Only ever called for the GitHub releases API.
if [ -f "$STUB_DIR/no_release" ]; then exit 1; fi
printf '{"tag_name": "%s", "name": "%s"}\n' "$RELEASE_TAG" "$RELEASE_TAG"
"""

SUDO_STUB = """#!/usr/bin/env bash
exec "$@"
"""


class UpdateScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.app = root / "olisar"
        self.app.mkdir()
        self.stub_dir = root / "stub"
        self.stub_dir.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB), ("sudo", SUDO_STUB)):
            p = self.bin / name
            p.write_text(body, encoding="utf-8")
            p.chmod(0o755)
        shutil.copy(SCRIPT, self.app / "olisar-update.sh")
        (self.app / "olisar-update.sh").chmod(0o755)
        (self.app / ".env").write_text("DISCORD_TOKEN=x\n", encoding="utf-8")

    # ── helpers ──────────────────────────────────────────────────────────────
    def write_compose(self, digest: str) -> None:
        (self.app / "docker-compose.yml").write_text(
            f"services:\n  olisar:\n    image: {IMAGE}@{digest}\n", encoding="utf-8"
        )

    def set_running(self, running: bool) -> None:
        """Running means running the image the compose file pins now."""
        marker = self.stub_dir / "running"
        marker.touch() if running else marker.unlink(missing_ok=True)
        if running and (self.app / "docker-compose.yml").exists():
            (self.stub_dir / "container_ref").write_text(self.deployed_ref() + "\n", encoding="utf-8")

    def set_health(self, health: str, status: str = "running") -> None:
        (self.stub_dir / "health").write_text(health + "\n", encoding="utf-8")
        (self.stub_dir / "status").write_text(status + "\n", encoding="utf-8")

    def run_script(self, *args: str, **overrides: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "STUB_DIR": str(self.stub_dir),
            "RELEASE_TAG": TAG,
            "IMAGE_REF_DIGEST": f"{IMAGE}@{NEW_DIGEST}",
            "OLISAR_HEALTH_TIMEOUT": "6",  # keep the failure path quick
            **overrides,
        }
        return subprocess.run(
            ["bash", str(self.app / "olisar-update.sh"), *args],
            capture_output=True, text=True, env=env, timeout=120,
        )

    def last_update(self) -> dict:
        return json.loads((self.app / "last-update.json").read_text("utf-8"))

    def deployed_ref(self) -> str:
        for line in (self.app / "docker-compose.yml").read_text("utf-8").splitlines():
            if line.strip().startswith("image:"):
                return line.split("image:", 1)[1].strip()
        return ""

    # ── tests ────────────────────────────────────────────────────────────────
    def test_applies_a_new_release_pinned_by_digest(self) -> None:
        """The deployed image must be an immutable digest, never a mutable tag — that's
        what makes 'what is deployed' a fact on disk and rollback possible at all."""
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{NEW_DIGEST}")
        out = self.last_update()
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "updated")
        self.assertTrue(out["updated"])
        self.assertFalse(out["rolled_back"])
        self.assertEqual(out["previous_digest"], OLD_DIGEST)

    def test_records_the_previous_digest_for_rollback(self) -> None:
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        self.run_script()
        versions = json.loads((self.app / "versions.json").read_text("utf-8"))
        self.assertEqual(versions["digest"], NEW_DIGEST)
        self.assertEqual(versions["previous_digest"], OLD_DIGEST)
        self.assertEqual(versions["tag"], TAG)

    def test_unhealthy_release_is_rolled_back(self) -> None:
        """The whole point of the health gate: a release that doesn't come up must leave
        the operator on the version that worked, not on a broken one."""
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("unhealthy")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")
        out = self.last_update()
        self.assertFalse(out["ok"])
        self.assertTrue(out["rolled_back"])

    def test_prune_only_runs_after_a_verified_update(self) -> None:
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("unhealthy")
        self.run_script()
        self.assertFalse((self.stub_dir / "pruned").exists())
        self.set_health("healthy")
        self.run_script()
        self.assertTrue((self.stub_dir / "pruned").exists())

    def test_stopped_server_is_repinned_but_not_started(self) -> None:
        """Start used to pull, so a bot stopped for a week silently came back on a
        different version. Updating repins; starting stays the operator's call."""
        self.write_compose(OLD_DIGEST)
        self.set_running(False)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{NEW_DIGEST}")
        self.assertEqual(self.last_update()["status"], "staged")
        self.assertFalse((self.stub_dir / "running").exists())

    def test_start_flag_brings_a_fresh_deploy_up(self) -> None:
        """A first deploy has nothing running by definition — --start is what makes deploy
        and update the same code path."""
        self.set_running(False)
        self.set_health("healthy")
        r = self.run_script("--start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last_update()["status"], "updated")
        self.assertTrue((self.stub_dir / "running").exists())

    def test_already_current_is_a_no_op(self) -> None:
        self.write_compose(NEW_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last_update()["status"], "up-to-date")
        self.assertFalse(self.last_update()["updated"])

    def ups(self) -> int:
        calls = self.stub_dir / "calls.log"
        lines = calls.read_text("utf-8").splitlines() if calls.exists() else []
        return sum(1 for line in lines if line.startswith("docker compose") and " up " in f"{line} ")

    def test_a_redeploy_onto_the_current_release_applies_its_new_env(self) -> None:
        """A redeploy writes a new .env and runs --start. On a VM already on the release,
        with the container running, it used to report "already on" and leave that container
        on the environment it started with: a replaced Tailscale key never reached it."""
        self.write_compose(NEW_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        r = self.run_script("--start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ups(), 1)
        self.assertEqual(self.last_update()["status"], "up-to-date")
        self.assertIn("applied its configuration", self.last_update()["message"])

    def test_an_update_check_on_the_current_release_touches_nothing(self) -> None:
        self.write_compose(NEW_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        self.run_script()
        self.assertEqual(self.ups(), 0)

    def test_no_reachable_release_fails_loudly(self) -> None:
        """Better a clear 'could not resolve a release' than silently falling back to a
        mutable tag — which is the behaviour this replaced."""
        (self.stub_dir / "no_release").touch()
        self.write_compose(OLD_DIGEST)
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.last_update()["status"], "no-release")
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")  # untouched

    def test_failed_pull_leaves_the_deployment_alone(self) -> None:
        (self.stub_dir / "pull_fails").touch()
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.last_update()["status"], "pull-failed")
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")

    # ── a container that didn't come up on the new image ─────────────────────
    def test_a_failed_container_start_is_a_failed_update(self) -> None:
        """A failed `up` (a bad .env, say) leaves the old container running and healthy.
        That used to pass the health gate: "updated … and healthy", and the old images pruned."""
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        (self.stub_dir / "up_fails").touch()
        r = self.run_script("--tag", TAG)
        self.assertEqual(r.returncode, 1)
        out = self.last_update()
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "up-failed")
        self.assertFalse(out["updated"])
        self.assertTrue(out["rolled_back"])
        self.assertIn("rolled back to the previous image", out["message"])
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")
        self.assertFalse((self.stub_dir / "pruned").exists())
        self.assertIn("bad line", r.stderr)  # compose's own reason, for whoever reads the log

    def test_a_rollback_puts_the_version_record_back(self) -> None:
        previous = '{"tag": "v9.9.8", "digest": "%s"}' % OLD_DIGEST
        (self.app / "versions.json").write_text(previous + "\n", encoding="utf-8")
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        (self.stub_dir / "up_fails").touch()
        self.run_script("--tag", TAG)
        self.assertEqual((self.app / "versions.json").read_text("utf-8").strip(), previous)

    def test_the_old_container_s_health_does_not_pass_for_the_new_image(self) -> None:
        """Compose reports success but the old container is still the one running: the gate
        has to see the new image up, not whichever container answers."""
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        (self.stub_dir / "up_keeps_old").touch()
        r = self.run_script("--tag", TAG, OLISAR_HEALTH_TIMEOUT="3")
        self.assertEqual(r.returncode, 1)
        out = self.last_update()
        self.assertEqual(out["status"], "rolled-back")
        self.assertFalse(out["updated"])
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")
        self.assertFalse((self.stub_dir / "pruned").exists())

    def test_a_failed_start_on_the_current_release_is_reported(self) -> None:
        """A redeploy onto the release the VM already runs: a failed `up` means its new .env
        never reached the server, which used to read as "applied its configuration"."""
        self.write_compose(NEW_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        (self.stub_dir / "up_fails").touch()
        r = self.run_script("--start", "--tag", TAG)
        self.assertEqual(r.returncode, 1)
        out = self.last_update()
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "up-failed")
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{NEW_DIGEST}")

    # ── never backwards ──────────────────────────────────────────────────────
    def label(self, digest: str, version: str) -> None:
        """The version CI stamped on the image with this digest (the tag it was built from)."""
        with (self.stub_dir / "labels").open("a", encoding="utf-8") as f:
            f.write(f"{digest} {version}\n")

    def pulls(self) -> list[str]:
        calls = self.stub_dir / "calls.log"
        lines = calls.read_text("utf-8").splitlines() if calls.exists() else []
        return [line for line in lines if line.startswith("docker pull")]

    def on_a_newer_release(self, version: str = "v10.0.beta-2") -> None:
        self.write_compose(OLD_DIGEST)
        self.label(OLD_DIGEST, version)
        self.set_running(True)
        self.set_health("healthy")

    def test_a_run_by_hand_leaves_a_beta_server_on_its_beta(self) -> None:
        """Without --tag the script takes GitHub's latest release, which is stable. On a VM
        the app had moved onto a newer beta, that used to pin the older stable release."""
        self.on_a_newer_release()
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.last_update()
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "server-ahead")
        self.assertFalse(out["updated"])
        self.assertEqual(out["message"], "the server is on 10.0.beta-2, which is newer than 9.9.9")
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")
        self.assertEqual((self.pulls(), self.ups()), ([], 0))
        self.assertIn("--force", r.stderr)

    def test_an_older_tag_is_refused_the_same_way(self) -> None:
        self.on_a_newer_release("v10.0")
        self.run_script("--tag", TAG)
        self.assertEqual(self.last_update()["status"], "server-ahead")
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")

    def test_force_moves_the_server_back(self) -> None:
        self.on_a_newer_release()
        r = self.run_script("--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last_update()["status"], "updated")
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{NEW_DIGEST}")

    def test_a_redeploy_onto_a_server_that_is_ahead_still_applies_its_env(self) -> None:
        """--start is a redeploy: the release stays, the new .env still has to reach it."""
        self.on_a_newer_release()
        r = self.run_script("--start", "--tag", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.last_update()
        self.assertEqual(out["status"], "server-ahead")
        self.assertIn("applied its configuration", out["message"])
        self.assertEqual(self.ups(), 1)
        self.assertEqual(self.pulls(), [])
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")

    def test_releases_are_ordered_as_the_app_orders_them(self) -> None:
        cases = {
            "v9.9.9.beta-3": "updated",   # the stable release comes after its betas
            "v1.5.0": "updated",
            "v9.10": "server-ahead",      # by number, not by text
            "v10.0": "server-ahead",
            "main": "updated",            # not a version: nothing is refused on a guess
        }
        for deployed, status in cases.items():
            with self.subTest(deployed):
                (self.stub_dir / "labels").unlink(missing_ok=True)
                self.on_a_newer_release(deployed)
                self.run_script("--tag", TAG)
                self.assertEqual(self.last_update()["status"], status)

    def test_an_image_without_a_version_label_goes_by_the_record_of_its_digest(self) -> None:
        """Only the record of the digest that's pinned now: a rollback leaves the compose file
        on the old digest, and a record of another one says nothing about it."""
        for digest, status in ((OLD_DIGEST, "server-ahead"), ("sha256:" + "c" * 64, "updated")):
            with self.subTest(digest=digest[:10]):
                self.write_compose(OLD_DIGEST)
                self.set_running(True)
                self.set_health("healthy")
                (self.app / "versions.json").write_text(
                    json.dumps({"tag": "v10.0", "digest": digest}, indent=2), encoding="utf-8"
                )
                self.run_script()
                self.assertEqual(self.last_update()["status"], status)

    def test_without_a_tag_a_beta_is_never_picked(self) -> None:
        """GitHub calls a beta its latest release when it's published without the pre-release
        flag. The app's --tag is how a beta reaches a server, never this."""
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        r = self.run_script(RELEASE_TAG="v10.0.beta-1")
        self.assertEqual(r.returncode, 1)
        out = self.last_update()
        self.assertEqual(out["status"], "no-release")
        self.assertIn("v10.0.beta-1", out["message"])
        self.assertEqual(out["tag"], "")
        self.assertEqual(self.pulls(), [])
        self.assertEqual(self.deployed_ref(), f"{IMAGE}@{OLD_DIGEST}")

        self.set_health("healthy")
        r = self.run_script("--tag", "v10.0.beta-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last_update()["status"], "updated")

    # ── telling the console ──────────────────────────────────────────────────
    def marked_during_pull(self) -> dict | None:
        p = self.stub_dir / "updating_during_pull.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else None

    def marker_left(self) -> bool:
        return (self.stub_dir / "data" / "updating.json").exists()

    def test_the_console_is_told_while_the_pull_runs(self) -> None:
        """The old container serves the console through the pull, so that's when it has to
        be able to say "Updating…"."""
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        r = self.run_script("--tag", TAG)
        self.assertEqual(r.returncode, 0, r.stderr)
        marked = self.marked_during_pull()
        self.assertIsNotNone(marked)
        self.assertEqual(marked["tag"], TAG)
        self.assertRegex(marked["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_the_marker_is_gone_after_every_ending(self) -> None:
        """Left behind, it would keep the console saying "Updating…" for a VM that isn't."""
        endings = {
            "updated": lambda: self.set_health("healthy"),
            "rolled back": lambda: self.set_health("unhealthy"),
            "pull failed": lambda: (self.stub_dir / "pull_fails").touch(),
        }
        for name, arrange in endings.items():
            with self.subTest(name):
                (self.stub_dir / "pull_fails").unlink(missing_ok=True)
                (self.stub_dir / "updating_during_pull.json").unlink(missing_ok=True)
                self.write_compose(OLD_DIGEST)
                self.set_running(True)
                arrange()
                self.run_script("--tag", TAG)
                self.assertIsNotNone(self.marked_during_pull())
                self.assertFalse(self.marker_left())

    def test_a_stopped_server_has_no_console_to_tell(self) -> None:
        self.write_compose(OLD_DIGEST)
        self.set_running(False)
        self.run_script("--tag", TAG)
        self.assertIsNone(self.marked_during_pull())
        self.assertFalse(self.marker_left())

    def test_compose_keeps_the_env_file_and_data_volume(self) -> None:
        """The compose file is regenerated on every run — it must not drop the operator's
        .env or the named volume their database lives in."""
        self.write_compose(OLD_DIGEST)
        self.set_running(True)
        self.set_health("healthy")
        self.run_script()
        body = (self.app / "docker-compose.yml").read_text("utf-8")
        self.assertIn("env_file: .env", body)
        self.assertIn("olisar-data:/var/lib/olisar", body)
        self.assertIn("restart: unless-stopped", body)


if __name__ == "__main__":
    unittest.main()
