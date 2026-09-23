"""The desktop gateway: every bot in its own process, one console in front.

Run:  uv run python -m unittest tests.test_gateway -v

The gateway sits between the operator's console and every bot, so the thing to prove is that
it's invisible: a bot behind it must see the request it would have seen directly — the same
Host (its OAuth redirect is built from it), no forwarding headers from the operator's own
machine (they'd lock the operator out of the loopback-only routes), every Set-Cookie of its
answer, redirects left for the browser to follow. And that each request reaches the right
bot: the one on screen, or the one a sign-in started on.

The last test is the whole thing for real: a gateway process, two bot processes, a write that
lands in one bot's database only, and bots that exit when the gateway is killed outright.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

REPO = Path(__file__).resolve().parent.parent


def _fake_bot(name: str) -> FastAPI:
    """Stands in for a bot's backend: echoes what it was sent."""
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"ok": True, "bot": name}

    @app.api_route("/api/echo/{rest:path}", methods=["GET", "POST"])
    async def echo(request: Request, rest: str):
        return {
            "bot": name,
            "host": request.headers.get("host"),
            "xff": request.headers.get("x-forwarded-for"),
            "cookie": request.headers.get("cookie"),
            "raw_path": request.scope["raw_path"].decode(),
            "query": request.url.query,
            "body": (await request.body()).decode(),
        }

    @app.get("/auth/login")
    async def login():
        r = RedirectResponse("https://discord.com/oauth2/authorize?x=1", status_code=307)
        r.set_cookie("olisar_oauth_state", "s1")
        r.set_cookie("second", "s2")
        return r

    @app.get("/auth/callback")
    async def callback():
        return JSONResponse({"bot": name})

    return app


class ForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["OLISAR_DATA_DIR"] = self._tmp.name
        self.addCleanup(os.environ.pop, "OLISAR_DATA_DIR", None)
        from olisar.runtime import gateway, profiles

        self.profiles = profiles
        self.second = profiles.create("Second")["id"]
        self.pool = gateway.Pool("http://127.0.0.1:8723")
        for pid, name in (("default", "first"), (self.second, "second")):
            w = gateway.Worker(pid, console_url=self.pool.console_url, token=self.pool.token)
            w.start = lambda: None  # a stand-in: no process behind it
            w.state = "ready"
            w._ready.set()
            w.client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_fake_bot(name)), base_url="http://worker",
            )
            self.pool.workers[pid] = w
        self.app = gateway.create_app(self.pool)

    async def asyncTearDown(self) -> None:
        for w in self.pool.workers.values():
            await w.client.aclose()

    def client(self, peer: str = "127.0.0.1") -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=(peer, 50000)),
            base_url="http://127.0.0.1:8723",
        )

    async def test_the_bot_sees_the_request_it_would_have_seen_directly(self) -> None:
        async with self.client() as c:
            r = await c.post("/api/echo/a%2Fb?x=1&y=2", content=b"payload", headers={"cookie": "olisar_session=abc"})
        body = r.json()
        self.assertEqual(body["bot"], "first")
        self.assertEqual(body["host"], "127.0.0.1:8723")  # the redirect URI is built from this
        self.assertIsNone(body["xff"])                     # the operator stays "local"
        self.assertEqual(body["cookie"], "olisar_session=abc")
        self.assertEqual(body["raw_path"], "/api/echo/a%2Fb")  # an escaped "/" stays one segment
        self.assertEqual(body["query"], "x=1&y=2")
        self.assertEqual(body["body"], "payload")

    async def test_a_visitor_from_elsewhere_is_marked_as_one(self) -> None:
        async with self.client(peer="192.168.1.20") as c:
            r = await c.get("/api/echo/x")
        self.assertEqual(r.json()["xff"], "192.168.1.20")
        async with self.client(peer="192.168.1.20") as c:
            r = await c.get("/api/bots")
        self.assertEqual(r.status_code, 403)

    async def test_redirects_and_every_cookie_come_back_untouched(self) -> None:
        async with self.client() as c:
            r = await c.get("/auth/login")
        self.assertEqual(r.status_code, 307)
        self.assertTrue(r.headers["location"].startswith("https://discord.com/"))
        cookies = r.headers.get_list("set-cookie")
        names = [c.split("=", 1)[0] for c in cookies]
        self.assertIn("olisar_oauth_state", names)
        self.assertIn("second", names)
        self.assertIn("olisar_bot_route", names)

    async def test_the_console_follows_the_bot_on_screen(self) -> None:
        async with self.client() as c:
            self.assertEqual((await c.get("/api/echo/x")).json()["bot"], "first")
            r = await c.post("/api/bots/switch", json={"id": self.second})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual((await c.get("/api/echo/x")).json()["bot"], "second")

    async def test_a_sign_in_finishes_on_the_bot_it_started_on(self) -> None:
        async with self.client() as c:
            await c.post("/api/bots/switch", json={"id": self.second})
            started = await c.get("/auth/login")
            route = next(v for v in started.headers.get_list("set-cookie") if v.startswith("olisar_bot_route="))
            pinned = route.split(";", 1)[0]
            await c.post("/api/bots/switch", json={"id": "default"})  # switched mid-sign-in
            r = await c.get("/auth/callback?code=1", headers={"cookie": pinned})
        self.assertEqual(r.json()["bot"], "second")

    async def test_the_private_routes_are_never_forwarded(self) -> None:
        async with self.client() as c:
            for path in ("/api/instance", "/api/instance/reset"):
                self.assertEqual((await c.post(path)).status_code, 404, path)

    async def test_a_bot_still_starting_answers_503(self) -> None:
        w = self.pool.workers["default"]
        w.state = "starting"
        w._ready.clear()
        async with self.client() as c:
            health = await c.get("/api/health")
        self.assertEqual(health.status_code, 503)

    async def test_a_failed_bot_still_lets_the_window_open(self) -> None:
        w = self.pool.workers["default"]
        w.state = "failed"
        w._ready.clear()
        async with self.client() as c:
            health = await c.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["bot_failed"])

    async def test_another_website_cant_change_anything(self) -> None:
        """Any page the operator has open can POST to 127.0.0.1. The console's own pages are
        always on loopback; nothing else gets to reset a bot or reach one through us."""
        async with self.client() as c:
            evil = {"origin": "https://evil.example"}
            self.assertEqual((await c.post("/api/bots/default/reset", headers=evil)).status_code, 403)
            self.assertEqual((await c.post("/api/echo/x", headers=evil)).status_code, 403)
            self.assertEqual((await c.get("/api/echo/x", headers=evil)).status_code, 200)
            for ok in ("http://127.0.0.1:8723", "http://localhost:5173"):
                r = await c.post("/api/echo/x", headers={"origin": ok})
                self.assertEqual(r.status_code, 200, ok)
            self.assertEqual((await c.post("/api/echo/x")).status_code, 200)  # the desktop shell

    async def test_cannot_delete_the_bot_on_screen_or_the_last_one(self) -> None:
        async with self.client() as c:
            self.assertEqual((await c.delete("/api/bots/default")).status_code, 409)


class WorkerEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["OLISAR_DATA_DIR"] = self._tmp.name
        self.addCleanup(os.environ.pop, "OLISAR_DATA_DIR", None)

    def test_each_bot_gets_its_own_dir_cookies_and_none_of_the_others_secrets(self) -> None:
        from olisar.runtime import gateway, profiles

        other = profiles.create("Other")["id"]
        os.environ["DISCORD_TOKEN"] = "first-bots-token"
        self.addCleanup(os.environ.pop, "DISCORD_TOKEN", None)
        first = gateway.Worker("default", console_url="http://127.0.0.1:8723", token="t")._env()
        second = gateway.Worker(other, console_url="http://127.0.0.1:8723", token="t")._env()

        self.assertEqual(first["OLISAR_DATA_DIR"], self._tmp.name)  # the original never moves
        self.assertEqual(first["OLISAR_COOKIE_SUFFIX"], "")        # its sessions survive
        self.assertEqual(first["DISCORD_TOKEN"], "first-bots-token")
        self.assertNotIn("OLISAR_NO_DOTENV", first)

        self.assertEqual(second["OLISAR_DATA_DIR"], str(Path(self._tmp.name) / "profiles" / other))
        self.assertEqual(second["OLISAR_COOKIE_SUFFIX"], f"_{other}")
        self.assertNotIn("DISCORD_TOKEN", second)
        self.assertEqual(second["OLISAR_NO_DOTENV"], "1")
        self.assertEqual(second["OLISAR_HOME"], self._tmp.name)


class TailscaleHandOverTests(unittest.TestCase):
    """Bots used to share one Tailscale node; now each has its own. Whoever was using it keeps
    it, so that bot's web address — and the OAuth redirect registered for it — survive."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["OLISAR_DATA_DIR"] = self._tmp.name
        self.addCleanup(os.environ.pop, "OLISAR_DATA_DIR", None)
        self.home = Path(self._tmp.name)
        (self.home / "tailscale").mkdir()
        (self.home / "tailscale" / "tailscaled.state").write_text("node")

    def db(self, path: Path, enabled: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as con:
            con.execute("CREATE TABLE app_config (id INTEGER PRIMARY KEY, tunnel_enabled BOOLEAN)")
            con.execute("INSERT INTO app_config VALUES (1, ?)", (1 if enabled else 0,))

    def test_goes_to_the_one_other_bot_that_used_it(self) -> None:
        from olisar.runtime import gateway, profiles

        other = profiles.create("Other")["id"]
        self.db(self.home / "olisar.db", False)
        self.db(profiles.db_path_for(other), True)
        self.assertEqual(gateway.adopt_shared_tailscale(), other)
        self.assertTrue((profiles.data_dir_for(other) / "tailscale" / "tailscaled.state").exists())
        self.assertFalse((self.home / "tailscale").exists())

    def test_stays_with_the_original_bot_when_it_uses_it(self) -> None:
        from olisar.runtime import gateway, profiles

        other = profiles.create("Other")["id"]
        self.db(self.home / "olisar.db", True)
        self.db(profiles.db_path_for(other), True)
        self.assertIsNone(gateway.adopt_shared_tailscale())
        self.assertTrue((self.home / "tailscale").exists())

    def test_leaves_it_when_it_cant_tell_whose(self) -> None:
        from olisar.runtime import gateway, profiles

        for name in ("A", "B"):
            self.db(profiles.db_path_for(profiles.create(name)["id"]), True)
        self.db(self.home / "olisar.db", False)
        self.assertIsNone(gateway.adopt_shared_tailscale())
        self.assertTrue((self.home / "tailscale").exists())


class SupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_many_bots_dont_starve_the_gateway_of_threads(self) -> None:
        """Waiting on a bot's process lasts as long as the bot. With one pool thread per bot
        taken for that, the next spawn or stop would never run."""
        import concurrent.futures

        from olisar.runtime import gateway, profiles

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["OLISAR_DATA_DIR"] = tmp.name
        self.addCleanup(os.environ.pop, "OLISAR_DATA_DIR", None)
        asyncio.get_running_loop().set_default_executor(concurrent.futures.ThreadPoolExecutor(1))
        workers = []
        for i in range(3):
            pid = profiles.create(f"Bot {i}")["id"]
            w = gateway.Worker(pid, console_url="http://127.0.0.1:1", token="t")
            w._command = lambda: [sys.executable, "-c", "import sys; sys.stdin.read()"]
            w.start()
            workers.append(w)
        await asyncio.sleep(1.0)
        self.assertTrue(all(w._proc is not None and w._proc.poll() is None for w in workers))
        await asyncio.wait_for(asyncio.gather(*(w.stop() for w in workers)), 30)
        self.assertTrue(all(w._proc.poll() is not None for w in workers))


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_browser_that_leaves_cancels_the_request_to_the_bot(self) -> None:
        from olisar.runtime.gateway import _await_or_disconnect

        class Gone:
            async def is_disconnected(self) -> bool:
                return True

        task = asyncio.create_task(asyncio.sleep(30))
        self.assertIsNone(await _await_or_disconnect(Gone(), task))
        self.assertTrue(task.cancelled())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"null")


def _workers() -> list[int]:
    out = subprocess.run(["pgrep", "-f", "olisar.runtime --worker"], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


@unittest.skipIf(sys.platform == "win32", "uses pgrep and SIGKILL")
class RealProcessesTest(unittest.TestCase):
    def test_bots_run_side_by_side_and_die_with_the_gateway(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DISCORD_", "OLISAR_"))}
        env.update(OLISAR_DATA_DIR=tmp.name, PYTHONUNBUFFERED="1")
        before = set(_workers())
        gw = subprocess.Popen(
            [sys.executable, "-m", "olisar.runtime", "--gateway", "--port", str(port)],
            cwd=str(REPO), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        self.addCleanup(lambda: gw.poll() is None and gw.kill())

        def wait_for(check, timeout: float = 90.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    if check():
                        return True
                except Exception:  # noqa: BLE001 — not up yet
                    pass
                time.sleep(0.5)
            return False

        self.assertTrue(wait_for(lambda: _get(base + "/api/health")[0] == 200), "gateway never came up")
        _, bot = _get(base + "/api/bots", method="POST", body={"name": "Second"})
        self.assertTrue(wait_for(lambda: all(
            p["state"] == "ready" for p in _get(base + "/api/bots")[1]["profiles"]
        )), "the second bot never came up")
        mine = set(_workers()) - before
        self.assertEqual(len(mine), 2)

        # A write through the console lands in the bot on screen, and only there.
        _get(base + "/api/bots/switch", method="POST", body={"id": bot["id"]}, timeout=30)
        status, _ = _get(base + "/api/setup/keys", method="POST", body={"gemini_api_key": "for-second"})
        self.assertEqual(status, 200)
        second_db = Path(tmp.name) / "profiles" / bot["id"] / "olisar.db"
        first_db = Path(tmp.name) / "olisar.db"
        with sqlite3.connect(second_db) as con:
            self.assertEqual(con.execute("SELECT gemini_api_key FROM app_secret").fetchone()[0], "for-second")
        with sqlite3.connect(first_db) as con:
            rows = con.execute("SELECT gemini_api_key FROM app_secret").fetchall()
        self.assertNotIn(("for-second",), rows)

        # Killed outright — no chance to clean up — its bots notice and leave.
        os.kill(gw.pid, signal.SIGKILL)
        gw.wait(10)
        self.assertTrue(wait_for(lambda: not (set(_workers()) & mine), timeout=30), "bots outlived the gateway")

    def test_closing_its_stdin_stops_the_gateway_and_every_bot(self) -> None:
        """How the desktop shell quits it, on every platform (on Windows a kill is
        TerminateProcess, which would give the gateway no chance to stop its bots)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        port = _free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DISCORD_", "OLISAR_"))}
        env.update(OLISAR_DATA_DIR=tmp.name, OLISAR_PARENT_PIPE="1", PYTHONUNBUFFERED="1")
        before = set(_workers())
        gw = subprocess.Popen(
            [sys.executable, "-m", "olisar.runtime", "--gateway", "--port", str(port)],
            cwd=str(REPO), env=env, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        self.addCleanup(lambda: gw.poll() is None and gw.kill())
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if _get(f"http://127.0.0.1:{port}/api/health")[0] == 200:
                    break
            except Exception:  # noqa: BLE001 — not up yet
                time.sleep(0.5)
        mine = set(_workers()) - before
        self.assertEqual(len(mine), 1)
        gw.stdin.close()
        self.assertEqual(gw.wait(40), 0)  # it waited for its bot, then left on its own
        self.assertFalse(set(_workers()) & mine)


if __name__ == "__main__":
    unittest.main()
