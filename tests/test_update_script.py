"""Coverage for deploy/olisar-update.sh — the VM's self-update.

Run:  uv run python -m unittest tests.test_update_script -v

This script is the only thing that puts a version onto a server-mode VM, and both triggers
(the systemd timer and the control panel's "Update now") run it, so a bug here is a bug
everywhere. It's also the hardest thing in the tree to test by hand — it needs a VM, a
release, and a deliberately broken image to see the interesting path.

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

case "$1 $2" in
  "compose version") exit 0 ;;
esac

if [ "$1" = "compose" ]; then
  shift
  case "$1" in
    ps)   [ -f "$STATE_DIR/running" ] && echo "fakecontainerid"; exit 0 ;;
    up)   touch "$STATE_DIR/running"; echo "$(cat "$STATE_DIR/generation" 2>/dev/null || echo 0)" > /dev/null; exit 0 ;;
    stop) rm -f "$STATE_DIR/running"; exit 0 ;;
    logs) echo "fake container log line"; exit 0 ;;
    *) exit 0 ;;
  esac
fi

case "$1" in
  pull)
    [ -f "$STATE_DIR/pull_fails" ] && { echo "manifest unknown" >&2; exit 1; }
    exit 0 ;;
  inspect)
    # `docker inspect --format <fmt> <id>` — the script asks for status then health.
    fmt="$3"
    case "$fmt" in
      *State.Status*) cat "$STATE_DIR/status" 2>/dev/null || echo running ;;
      *State.Health*) cat "$STATE_DIR/health" 2>/dev/null || echo healthy ;;
    esac
    exit 0 ;;
  image)
    case "$2" in
      inspect) echo "${IMAGE_REF_DIGEST}" ; exit 0 ;;
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
        marker = self.stub_dir / "running"
        marker.touch() if running else marker.unlink(missing_ok=True)

    def set_health(self, health: str, status: str = "running") -> None:
        (self.stub_dir / "health").write_text(health + "\n", encoding="utf-8")
        (self.stub_dir / "status").write_text(status + "\n", encoding="utf-8")

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "STUB_DIR": str(self.stub_dir),
            "RELEASE_TAG": TAG,
            "IMAGE_REF_DIGEST": f"{IMAGE}@{NEW_DIGEST}",
            "OLISAR_HEALTH_TIMEOUT": "6",  # keep the failure path quick
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
