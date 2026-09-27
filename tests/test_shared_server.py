"""Two bots, one VM: the server-hosting code driven over a real SSH connection.

Run:  uv run python -m unittest tests.test_shared_server -v

A VM that runs several bots keeps each in its own directory (``~/olisar``, then
``~/olisar-<profile id>``), and which directory a deploy lands in is the whole difference
between "add a bot next to that one" and "overwrite that one's configuration". The only honest
way to check it is to let ``olisar.runtime.remote`` do what it does on a real VM, so this starts
an SSH server in-process (asyncssh), backed by a throwaway home directory, with ``docker``,
``sudo`` and ``curl`` stubbed on PATH — and runs the real ``deploy/olisar-update.sh`` behind it.
The app's own GitHub and Discord lookups are patched too, so nothing leaves the machine.

Covered: the first bot deploys into ``~/olisar`` as it always has; a second bot can't get in
until the first lets its key in; once in, it deploys alongside rather than over the first,
under its own Tailscale device name; redeploying a bot reuses its directory, even as a different
Discord application; adopting a VM that runs two bots asks which one; every command runs in the
directory of the bot that sent it; an update replaces an older client's update script; a bot
whose Tailscale key is refused is reported as such and can be given a new one; and a bot's
Tailscale device can be renamed, but not to a name another bot on the VM uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import asyncssh

from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.runtime import remote

HERE = Path(__file__).resolve().parent

# A docker that logs where it was called from and remembers only which tag each image was
# pulled as — enough for the update script to "pull", pin a digest, start and pass its health
# gate, and for the status probe to read the release a bot runs (every tag is its own image,
# labeled with that tag, as CI labels them). A container it brings up publishes the backend's
# state.json from its .env: a Tailscale key with "dead" in it is one Tailscale refuses, so
# that bot's console gets no address, and otherwise the address starts with the device name.
DOCKER_STUB = r"""#!/usr/bin/env bash
echo "$PWD|docker $*" >> "$STUB_LOG"
IMAGES="$(dirname "$STUB_LOG")/images"  # one file per digest, holding the tag pulled as it
mkdir -p "$IMAGES"
digest_of() { printf '%s' "$1" | shasum -a 256 | cut -c1-64; }
pinned() { grep -m1 'image:' docker-compose.yml 2>/dev/null | awk '{print $2}'; }
case "$1" in
  compose)
    case "$2" in
      ps) [ -f stopped.stub ] || echo "cid-$(basename "$PWD")" ;;
      images) pinned ;;
      up)
        if grep -q '^TAILSCALE_AUTH=.*dead' .env 2>/dev/null; then
          printf '{"public_url": "http://127.0.0.1:8000", "tunnel_error": "tsnet.Up: backend: invalid key: API key does not exist"}\n' > state.json.stub
        else
          node=$(grep -m1 '^OLISAR_FUNNEL_HOSTNAME=' .env 2>/dev/null | cut -d= -f2-)
          printf '{"public_url": "https://%s.example.ts.net"}\n' "${node:-$(basename "$PWD")}" > state.json.stub
        fi ;;
    esac
    exit 0 ;;
  pull)
    tag="${2##*:}"
    echo "$tag" > "$IMAGES/$(digest_of "$tag")"
    exit 0 ;;
  exec)
    case "$*" in
      *olisar.activity*)
        # An image from before the feed has no such module.
        [ -f activity.stub ] || { echo "/app/.venv/bin/python: No module named olisar.activity" >&2; exit 1; }
        cat activity.stub ;;
      *) cat state.json.stub 2>/dev/null ;;
    esac
    exit 0 ;;
  inspect)
    case "$3" in
      *State.StartedAt*) echo 'running|healthy|2026-09-26T08:15:02.123456789Z|"2026-09-26T09:14:32.4Z""2026-09-26T09:15:02.5Z"' ;;
      *Config.Image*) echo "|$(pinned)" ;;
      *State.Status*) echo running ;;
      *State.Health*) echo healthy ;;
    esac
    exit 0 ;;
  image)
    [ "$2" = "inspect" ] || exit 0
    # docker image inspect --format <fmt> <ref>, the ref by tag or by digest
    case "$5" in
      *@sha256:*) digest="${5##*@sha256:}" ;;
      *) digest="$(digest_of "${5##*:}")" ;;
    esac
    tag="$(cat "$IMAGES/$digest" 2>/dev/null)"
    case "$4" in
      *revision*) echo "$tag||ghcr.io/gcrft123/olisar@sha256:$digest" ;;  # the status probe
      *image.version*) echo "$tag" ;;
      *RepoDigests*) echo "ghcr.io/gcrft123/olisar@sha256:$digest" ;;
      *) echo "sha256:$digest" ;;
    esac
    exit 0 ;;
esac
exit 0
"""

CURL_STUB = r"""#!/usr/bin/env bash
printf '{"tag_name": "v9.9.9"}\n'
"""

SUDO_STUB = """#!/usr/bin/env bash
exec "$@"
"""


class _VM(asyncssh.SSHServer):
    """Admits exactly the keys in the fake home's ``~/.ssh/authorized_keys`` — re-read on
    every connection, so a key added mid-test counts from the next connect."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._conn = None

    def connection_made(self, conn) -> None:
        self._conn = conn

    def begin_auth(self, username: str) -> bool:
        path = self.home / ".ssh" / "authorized_keys"
        if path.exists() and path.read_text().strip():
            self._conn.set_authorized_keys(str(path))
        return True

    def public_key_auth_supported(self) -> bool:
        return True


async def _run_command(process, *, home: Path, env: dict) -> None:
    """Run the client's command with bash in the fake home. Only the commands remote.py
    feeds input to read stdin; for the rest it's left alone, as on a real shell."""
    cmd = process.command or ""
    wants_input = cmd == "bash -s" or cmd.startswith("cat >")
    data = (await process.stdin.read()) if wants_input else ""
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", cmd,
        stdin=asyncio.subprocess.PIPE if wants_input else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(home), env=env,
    )
    out, err = await proc.communicate(data.encode() if wants_input else None)
    process.stdout.write(out.decode())
    process.stderr.write(err.decode())
    process.exit(proc.returncode or 0)


class SharedServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "vm-home"
        (self.home / ".ssh").mkdir(parents=True)
        (self.home / ".ssh" / "authorized_keys").write_text("")
        stubs = root / "bin"
        stubs.mkdir()
        for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB), ("sudo", SUDO_STUB)):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        self.log = root / "calls.log"
        env = {
            "HOME": str(self.home),
            "PATH": f"{stubs}:/usr/bin:/bin:/usr/sbin:/sbin",
            "STUB_LOG": str(self.log),
            "OLISAR_HEALTH_TIMEOUT": "10",
        }
        server_key = asyncssh.generate_private_key("ssh-ed25519")
        self.server = await asyncssh.create_server(
            lambda: _VM(self.home), "127.0.0.1", 0,
            server_host_keys=[server_key],
            process_factory=functools.partial(_run_command, home=self.home, env=env),
        )
        port = self.server.sockets[0].getsockname()[1]
        connect = asyncssh.connect
        patcher = mock.patch.object(remote.asyncssh, "connect", functools.partial(connect, port=port))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Keep the automatic update a caller kicks off after adopting a VM out of this test.
        spawn = mock.patch.object(remote, "spawn_autoupdate", lambda: None)
        spawn.start()
        self.addCleanup(spawn.stop)
        # Nothing here leaves the machine. GitHub's newest release on the app's channel is
        # ``self.release`` (None: GitHub couldn't be asked), and naming the installs for the
        # operator to pick from doesn't ask Discord.
        self.release: str | None = "v9.9.9"
        self.channel = "stable"

        async def newest_release(chan=None):
            return {"tag": self.release, "url": "", "published_at": None} if self.release else None

        async def name_installs(installs):
            return [{"dir": i["dir"], "name": i.get("node") or i["dir"]} for i in installs]

        for patcher in (
            mock.patch.object(remote.updates, "newest_release", newest_release),
            mock.patch.object(remote.updates, "channel", lambda: self.channel),
            mock.patch.object(remote, "_name_installs", name_installs),
            # ``as_bot`` sets the profile id the way a gateway worker is started with one.
            mock.patch.dict(os.environ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dbs = root / "bots"
        self.dbs.mkdir()

    async def asyncTearDown(self) -> None:
        self.server.close()
        with contextlib.suppress(Exception):
            await self.server.wait_closed()
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    # ── helpers ──────────────────────────────────────────────────────────────

    async def as_bot(self, name: str) -> None:
        """Make ``name`` the bot this process is, as a gateway worker would be."""
        from scripts.init_db import create_schema

        path = self.dbs / f"{name}.db"
        engine.pin_database(str(path))
        os.environ["OLISAR_PROFILE_ID"] = name
        runtime_config.invalidate()
        runtime_keys.invalidate()
        if not path.exists():
            await create_schema()

    def authorize(self, pubkey: str) -> None:
        with (self.home / ".ssh" / "authorized_keys").open("a") as f:
            f.write(pubkey.strip() + "\n")

    def env_file(self, client_id: str, node: str = "olisar") -> str:
        return (
            f"DISCORD_TOKEN=token-{client_id}\nDISCORD_CLIENT_ID={client_id}\n"
            f"DISCORD_CLIENT_SECRET=secret\nGEMINI_API_KEY=g\n"
            f"TAILSCALE_AUTH=tskey-auth-shared\nADMIN_ALLOWLIST=424242\n"
            f"OLISAR_FUNNEL_HOSTNAME={node}\n"
        )

    def read_env(self, app_dir: str) -> dict:
        return remote.parse_env((self.home / app_dir / ".env").read_text())

    # ── the scenario ─────────────────────────────────────────────────────────

    async def test_two_bots_share_one_vm(self) -> None:
        # The first bot: the operator pasted its key when creating the VM.
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        first = await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["app_dir"], "olisar")  # a one-bot VM looks as it always has
        self.assertTrue((self.home / "olisar" / "docker-compose.yml").exists())
        cfg = await remote._load()
        self.assertEqual((cfg.server_host, cfg.server_app_dir, cfg.hosting_mode), ("127.0.0.1", "olisar", "server"))

        # A second bot has its own key, which the VM has never seen.
        await self.as_bot("beta")
        beta_key = await remote.public_key()
        refused = await remote.deploy("127.0.0.1", "tester", self.env_file("222"))
        self.assertFalse(refused["ok"])

        # The first bot lets it in (twice — it must not duplicate the line) and says what to
        # reuse. Not its Tailscale key: each bot needs its own.
        await self.as_bot("alpha")
        self.assertTrue((await remote.authorize_key(beta_key))["ok"])
        self.assertTrue((await remote.authorize_key(beta_key))["ok"])
        keys = (self.home / ".ssh" / "authorized_keys").read_text().splitlines()
        self.assertEqual(sum(1 for k in keys if k.split()[:2] == beta_key.split()[:2]), 1)
        info = await remote.share_info()
        self.assertEqual(
            (info["host"], info["user"], info["admin_allowlist"]), ("127.0.0.1", "tester", "424242"),
        )
        self.assertNotIn("tailscale_auth", info)

        # Now the second bot deploys — alongside, not over, and under its own device name.
        await self.as_bot("beta")
        second = await remote.deploy("127.0.0.1", "tester", self.env_file("222"))
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["console_error"], "")
        self.assertEqual(second["app_dir"], "olisar-beta")
        self.assertEqual(self.read_env("olisar")["DISCORD_CLIENT_ID"], "111")
        self.assertEqual(self.read_env("olisar-beta")["DISCORD_CLIENT_ID"], "222")
        self.assertNotEqual(
            self.read_env("olisar")["OLISAR_FUNNEL_HOSTNAME"],
            self.read_env("olisar-beta")["OLISAR_FUNNEL_HOSTNAME"],
        )
        self.assertEqual((await remote._load()).server_app_dir, "olisar-beta")

        # Redeploying a bot replaces its own install; it never grows a third one.
        again = await remote.deploy("127.0.0.1", "tester", self.env_file("222"))
        self.assertEqual(again["app_dir"], "olisar-beta")
        installs = sorted(p.name for p in self.home.iterdir() if p.name.startswith("olisar"))
        self.assertEqual(installs, ["olisar", "olisar-beta"])

        # Each bot's commands run in its own directory.
        self.log.write_text("")
        self.assertTrue((await remote.power("stop"))["ok"])
        stops = [ln for ln in self.log.read_text().splitlines() if "compose stop" in ln]
        self.assertEqual([ln.split("|")[0] for ln in stops], [str(self.home / "olisar-beta")])

        # A bot adopting this VM (a reinstall, a reset) has to be told which install it is.
        await self.as_bot("gamma")
        self.authorize(await remote.public_key())
        ask = await remote.connect("127.0.0.1", "tester")
        self.assertFalse(ask["ok"])
        self.assertEqual(sorted(c["dir"] for c in ask["choose"]), ["olisar", "olisar-beta"])
        chosen = await remote.connect("127.0.0.1", "tester", "olisar-beta")
        self.assertTrue(chosen["ok"], chosen)
        self.assertEqual((await remote._load()).server_app_dir, "olisar-beta")

    async def test_a_bot_that_ran_here_before_reconnects_to_its_own_install(self) -> None:
        """A reset keeps ``server_app_dir``, so Reconnect finds the bot's install on a VM that
        runs several without asking."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        await self.as_bot("beta")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("222"))
        await runtime_config.save(hosting_mode="local", server_host="", configured=False)  # what a reset leaves
        back = await remote.connect("127.0.0.1", "tester")
        self.assertTrue(back["ok"], back)
        self.assertEqual(back["app_dir"], "olisar-beta")

    async def test_a_bot_reset_onto_another_discord_app_replaces_its_own_install(self) -> None:
        """The owner mark, not the Discord application, says whose an install is: a bot reset
        and redeployed as a different application must not leave its old container running."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        await self.as_bot("beta")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("222"))
        await self.as_bot("alpha")
        again = await remote.deploy("127.0.0.1", "tester", self.env_file("333"))
        self.assertEqual(again["app_dir"], "olisar")
        self.assertEqual(self.read_env("olisar")["DISCORD_CLIENT_ID"], "333")
        installs = sorted(p.name for p in self.home.iterdir() if p.name.startswith("olisar"))
        self.assertEqual(installs, ["olisar", "olisar-beta"])

    async def test_an_update_brings_an_older_update_script_up_to_date(self) -> None:
        """A VM set up by an older client keeps its old script until something replaces it,
        and that one doesn't take turns with other bots' updates."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        script = self.home / "olisar" / remote.UPDATE_SCRIPT
        script.write_text("#!/usr/bin/env bash\n# an old script\nexit 0\n")
        # The app relaunching onto a newer build than the one that deployed the VM.
        with mock.patch.object(remote, "current_version", lambda: "99.0.0"):
            await remote.autoupdate()
        self.assertEqual(script.read_text(), remote._asset(remote.UPDATE_SCRIPT))

    async def test_an_update_with_no_release_to_pin_changes_nothing(self) -> None:
        """GitHub couldn't be asked. The script would fall back to GitHub's latest release on
        its own, which is stable and may be older than the server, so it isn't run; nor is the
        run recorded as settled, so the next launch tries again."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        synced = (await remote._load()).server_synced_version
        self.release = None
        self.log.write_text("")
        with mock.patch.object(remote, "current_version", lambda: "99.0.0"):
            result = await remote.autoupdate()
        self.assertEqual(result["status"], "no-release")
        self.assertFalse(result["ok"])
        self.assertNotIn("docker pull", self.log.read_text())
        self.assertEqual((await remote._load()).server_synced_version, synced)

    def pinned_tag(self, app_dir: str = "olisar") -> str:
        return json.loads((self.home / app_dir / "versions.json").read_text())["tag"]

    def pulls(self) -> list[str]:
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [ln.split("docker pull ", 1)[1] for ln in lines if "docker pull" in ln]

    async def test_a_redeploy_leaves_a_server_on_a_newer_release_than_the_app_s_channel(self) -> None:
        """Re-running setup (or moving a bot onto its server) pinned whatever the app's channel
        offered: switched from beta to stable, that put the server back on the older stable
        release. It stays on its beta, and the redeploy's new .env still reaches it."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        self.release, self.channel = "v2.1.beta-1", "beta"
        self.assertTrue((await remote.deploy("127.0.0.1", "tester", self.env_file("111")))["ok"])
        self.assertEqual(self.pinned_tag(), "v2.1.beta-1")

        self.release, self.channel = "v2.0", "stable"
        self.log.write_text("")
        again = await remote.deploy("127.0.0.1", "tester", self.env_file("111", node="everest"))
        self.assertTrue(again["ok"], again)
        self.assertIn("2.1.beta-1", again["log"])
        self.assertEqual(self.pinned_tag(), "v2.1.beta-1")
        self.assertEqual(self.pulls(), ["ghcr.io/gcrft123/olisar:v2.1.beta-1"])  # its own tag
        self.assertIn("compose up -d", self.log.read_text())
        self.assertEqual((await remote.status())["url"], "https://everest.example.ts.net")  # the new .env
        # Not "synced": nothing reconciled this server with the app's channel.
        self.assertEqual((await remote._load()).server_synced_version, "")

    async def test_a_redeploy_with_no_release_to_pin_keeps_the_server_s_own(self) -> None:
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        self.release = None
        again = await remote.deploy("127.0.0.1", "tester", self.env_file("111", node="everest"))
        self.assertTrue(again["ok"], again)
        self.assertEqual(self.pinned_tag(), "v9.9.9")
        self.assertEqual((await remote.status())["url"], "https://everest.example.ts.net")
        self.assertEqual((await remote._load()).server_synced_version, "")

    async def test_a_first_deploy_with_no_release_to_pin_is_refused(self) -> None:
        """Untagged, the script would take GitHub's latest release, off the app's channel."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        self.release = None
        dep = await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        self.assertFalse(dep["ok"])
        self.assertIn("GitHub", dep["error"])
        self.assertFalse((self.home / "olisar" / ".env").exists())  # nothing written
        self.assertEqual(self.pulls(), [])
        self.assertNotEqual((await remote._load()).hosting_mode, "server")

    async def test_status_and_activity_come_from_the_bot_s_own_container(self) -> None:
        """The probe's start and healthcheck times, and the activity feed read from inside the
        container — or, on an image from before the feed, an empty one rather than an error."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        status = await remote.status()
        self.assertEqual((status["started_at"], status["health_at"]),
                         ("2026-09-26T08:15:02.123Z", "2026-09-26T09:15:02.500Z"))

        self.assertEqual(await remote.activity(), {"ok": True, "supported": False, "items": []})

        feed = {"ok": True, "supported": True, "items": [{"id": "status:1", "kind": "status"}],
                "members": {"count": 2, "at": None, "faces": []}, "health": {"vec": True, "sandbox": True, "model": None}}
        (self.home / "olisar" / "activity.stub").write_text(json.dumps(feed) + "\n")
        self.log.write_text("")
        self.assertEqual(await remote.activity(), feed)
        [call] = [ln for ln in self.log.read_text().splitlines() if "olisar.activity" in ln]
        self.assertEqual(call.split("|")[0], str(self.home / "olisar"))
        self.assertIn("docker exec cid-olisar timeout", call)

        (self.home / "olisar" / "stopped.stub").write_text("")
        stopped = await remote.activity()
        self.assertFalse(stopped["ok"])
        self.assertIn("isn't running", stopped["error"])

    async def test_a_refused_tailscale_key_is_reported_and_can_be_replaced(self) -> None:
        """A bot whose Tailscale key is refused runs with no console address. The deploy says
        so instead of reporting a success, the panel doesn't offer the loopback origin as the
        console, and a new key from the panel brings it up."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        env = self.env_file("111").replace("tskey-auth-shared", "tskey-auth-dead")
        dep = await remote.deploy("127.0.0.1", "tester", env)
        self.assertTrue(dep["ok"], dep)  # installed and running, so saved as this bot's server
        self.assertIn("rejected the auth key", dep["console_error"])
        status = await remote.status()
        self.assertTrue(status["running"])
        self.assertEqual(status["url"], "")
        self.assertIn("rejected the auth key", status["console_error"])

        # A key that's refused again says so; one that isn't a key never reaches the .env.
        again = await remote.set_tunnel_key("tskey-auth-dead-too")
        self.assertFalse(again["ok"])
        self.assertIn("rejected the auth key", again["error"])
        self.assertFalse((await remote.set_tunnel_key("tskey-auth-x\nDISCORD_TOKEN=evil"))["ok"])
        self.assertEqual(self.read_env("olisar")["DISCORD_TOKEN"], "token-111")

        self.log.write_text("")
        fixed = await remote.set_tunnel_key(" tskey-auth-fresh ")
        self.assertEqual(fixed, {"ok": True, "url": "https://olisar.example.ts.net"})
        env_now = self.read_env("olisar")
        self.assertEqual(env_now["TAILSCALE_AUTH"], "tskey-auth-fresh")
        self.assertEqual(env_now["DISCORD_CLIENT_ID"], "111")  # the rest of the .env is kept
        self.assertEqual((self.home / "olisar" / ".env").stat().st_mode & 0o777, 0o600)
        # Recreated, not restarted: a restart keeps the environment the container was made with.
        self.assertIn("compose up -d --force-recreate", self.log.read_text())
        self.assertEqual((await remote.status())["url"], "https://olisar.example.ts.net")

    async def test_renaming_the_device_moves_the_console(self) -> None:
        """The name goes in the bot's own .env and the container is recreated on it. A name
        another bot on the VM already uses is refused, as deploy would have refused it."""
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        await self.as_bot("beta")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("222"))
        self.assertEqual(self.read_env("olisar-beta")["OLISAR_FUNNEL_HOSTNAME"], "olisar-beta")

        await self.as_bot("alpha")
        taken = await remote.set_tunnel_node("olisar-beta")
        self.assertFalse(taken["ok"])
        self.assertIn("Another bot on this server", taken["error"])
        self.assertFalse((await remote.set_tunnel_node("my bot"))["ok"])
        self.assertFalse((await remote.set_tunnel_node("x\nDISCORD_TOKEN=evil"))["ok"])
        self.assertEqual(self.read_env("olisar")["OLISAR_FUNNEL_HOSTNAME"], "olisar")

        self.log.write_text("")
        moved = await remote.set_tunnel_node(" Everest ")
        self.assertEqual(moved, {"ok": True, "url": "https://everest.example.ts.net", "note": ""})
        env_now = self.read_env("olisar")
        self.assertEqual(env_now["OLISAR_FUNNEL_HOSTNAME"], "everest")
        self.assertEqual(env_now["TAILSCALE_AUTH"], "tskey-auth-shared")  # the rest is kept
        [recreate] = [ln for ln in self.log.read_text().splitlines() if "--force-recreate" in ln]
        self.assertEqual(recreate.split("|")[0], str(self.home / "olisar"))
        self.assertEqual(self.read_env("olisar-beta")["OLISAR_FUNNEL_HOSTNAME"], "olisar-beta")
        self.assertEqual((await remote.status())["url"], "https://everest.example.ts.net")

    async def test_refuses_something_that_isnt_a_key(self) -> None:
        await self.as_bot("alpha")
        self.authorize(await remote.public_key())
        await remote.deploy("127.0.0.1", "tester", self.env_file("111"))
        bad = await remote.authorize_key("ssh-ed25519 AAAA'; rm -rf ~ #")
        self.assertFalse(bad["ok"])
        self.assertTrue((self.home / "olisar").exists())


class PureHelpersTests(unittest.TestCase):
    def test_choose_app_dir(self) -> None:
        one = [{"dir": "olisar", "client_id": "111"}]
        self.assertEqual(remote.choose_app_dir([], client_id="111", own="olisar-b"), "olisar")
        self.assertEqual(remote.choose_app_dir(one, client_id="111", own="olisar-b"), "olisar")
        self.assertEqual(remote.choose_app_dir(one, client_id="222", own="olisar-b"), "olisar-b")
        two = one + [{"dir": "olisar-b", "client_id": "222"}]
        self.assertEqual(remote.choose_app_dir(two, client_id="222", own="olisar-c"), "olisar-b")
        # The owner mark wins over the application: a bot reset onto a new app keeps its dir.
        marked = [{"dir": "olisar", "client_id": "111", "owner": "abc"}, {"dir": "olisar-b", "client_id": "222"}]
        self.assertEqual(remote.choose_app_dir(marked, client_id="999", own="olisar-x", owner="abc"), "olisar")

    def test_pick_volume(self) -> None:
        names = ["olisar_olisar-data", "olisar-b_olisar-data", "other"]
        self.assertEqual(remote.pick_volume(names, "olisar"), "olisar_olisar-data")
        self.assertEqual(remote.pick_volume(names, "olisar-b"), "olisar-b_olisar-data")
        self.assertEqual(remote.pick_volume(names, "olisar-c"), "")
        # A lone oddly-named volume is only ever the original install's.
        self.assertEqual(remote.pick_volume(["legacy_olisar-data"], "olisar"), "legacy_olisar-data")
        self.assertEqual(remote.pick_volume(["legacy_olisar-data"], "olisar-b"), "")

    def test_app_dir_names_are_shell_safe(self) -> None:
        for bad in ("olisar-../x", "olisar;rm", "OLISAR", "olisar-", "../olisar"):
            self.assertFalse(remote.valid_app_dir(bad), bad)
        self.assertEqual(remote.app_dir_of_name("olisar-$(x)"), "olisar")


if __name__ == "__main__":
    unittest.main()
