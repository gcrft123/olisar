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
            w._settled.set()
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

    async def test_a_window_showing_another_bot_cant_save_into_this_one(self) -> None:
        """The desktop window is on the first bot when a browser tab switches the console to
        the second. The window's next save must not land in the second bot: it's refused, and
        the answer names the bot now on screen, so the window reloads onto it."""
        async with self.client() as c:
            loaded = await c.get("/api/echo/x")
            shown = loaded.headers["x-olisar-bot"]
            self.assertEqual(shown, "default")
            self.assertEqual((await c.get("/api/bots")).headers["x-olisar-bot"], "default")
            await c.post("/api/bots/switch", json={"id": self.second})  # the other tab

            stale = {"x-olisar-bot": shown}
            r = await c.post("/api/echo/x", content=b"save", headers=stale)
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.headers["x-olisar-bot"], self.second)
            r = await c.post("/api/bots/share-server", json={"from_id": "default"}, headers=stale)
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.headers["x-olisar-bot"], self.second)

            read = await c.get("/api/echo/x", headers=stale)  # reads pass
            self.assertEqual((read.status_code, read.json()["bot"]), (200, "second"))
            mine = await c.post("/api/echo/x", headers={"x-olisar-bot": self.second})
            self.assertEqual((mine.status_code, mine.json()["bot"]), (200, "second"))
            shell = await c.post("/api/echo/x")  # the desktop shell names no bot
            self.assertEqual(shell.status_code, 200)

    async def test_a_sign_in_still_lands_on_the_bot_it_started_on(self) -> None:
        async with self.client() as c:
            started = await c.get("/auth/login", headers={"x-olisar-bot": "default"})
            route = next(v for v in started.headers.get_list("set-cookie") if v.startswith("olisar_bot_route="))
            await c.post("/api/bots/switch", json={"id": self.second})
            r = await c.get("/auth/callback?code=1", headers={
                "cookie": route.split(";", 1)[0], "x-olisar-bot": "default",
            })
        self.assertEqual((r.status_code, r.json()["bot"]), (200, "first"))

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
        """Any page the operator has open can POST to 127.0.0.1. Only the console's own pages,
        served from this address, get to reset a bot or reach one through us — a page on
        another loopback port (some local dev server) is somebody else's."""
        async with self.client() as c:
            evil = {"origin": "https://evil.example"}
            self.assertEqual((await c.post("/api/bots/default/reset", headers=evil)).status_code, 403)
            self.assertEqual((await c.post("/api/echo/x", headers=evil)).status_code, 403)
            self.assertEqual((await c.get("/api/echo/x", headers=evil)).status_code, 200)
            for bad in ("http://localhost:5555", "http://127.0.0.1:5173", "null", "http://127.0.0.1"):
                r = await c.post("/api/bots/default/reset", headers={"origin": bad})
                self.assertEqual(r.status_code, 403, bad)
                r = await c.post("/api/echo/x", headers={"origin": bad})
                self.assertEqual(r.status_code, 403, bad)
            for ok in ("http://127.0.0.1:8723", "http://localhost:8723", "http://[::1]:8723"):
                r = await c.post("/api/echo/x", headers={"origin": ok})
                self.assertEqual(r.status_code, 200, ok)
            # The Vite dev server proxies the console's calls through: the browser addressed
            # it, so the Host it forwards is the page's own.
            vite = {"origin": "http://localhost:5173", "host": "localhost:5173"}
            self.assertEqual((await c.post("/api/echo/x", headers=vite)).status_code, 200)
            self.assertEqual((await c.post("/api/echo/x")).status_code, 200)  # the desktop shell

    async def test_another_local_page_cant_read_the_answers(self) -> None:
        async with self.client() as c:
            other = await c.get("/api/echo/x", headers={"origin": "http://localhost:5555"})
            self.assertNotIn("access-control-allow-origin", other.headers)
            preflight = await c.options("/api/echo/x", headers={
                "origin": "http://localhost:5555", "access-control-request-method": "PUT",
            })
            self.assertEqual(preflight.status_code, 400)
            own = await c.get("/api/echo/x", headers={"origin": "http://localhost:8723"})
            self.assertEqual(own.headers.get("access-control-allow-origin"), "http://localhost:8723")

    async def test_a_page_that_rebinds_its_name_to_loopback_reads_nothing(self) -> None:
        """DNS rebinding: attacker.example resolves to 127.0.0.1 once its page has loaded, so
        the browser counts us as that page's own origin. Only its Host gives it away."""
        async with self.client() as c:
            for path in ("/api/bots", "/api/echo/x", "/api/health"):
                r = await c.get(path, headers={"host": "attacker.example:8723"})
                self.assertEqual(r.status_code, 403, path)
            for host in ("127.0.0.1:8723", "localhost:8723", "[::1]:8723", "localhost"):
                r = await c.get("/api/echo/x", headers={"host": host})
                self.assertEqual(r.status_code, 200, host)
        # A remote visitor names whatever host they reached; they're marked as remote anyway.
        async with self.client(peer="192.168.1.20") as c:
            r = await c.get("/api/echo/x", headers={"host": "olisar.local:8723"})
        self.assertEqual(r.status_code, 200)

    async def test_cannot_delete_the_bot_on_screen_or_the_last_one(self) -> None:
        async with self.client() as c:
            self.assertEqual((await c.delete("/api/bots/default")).status_code, 409)


class BotPortGuardTests(unittest.IsolatedAsyncioTestCase):
    """Each bot also listens on a loopback port of its own, which a web page can find by
    scanning. It refuses the same cross-site writes the gateway does, while the console
    (through the gateway, which passes its Host and Origin along) and a remote visitor on the
    Funnel address, whose page is that address, keep working."""

    def app(self):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        from api.trust import ConsoleGuard

        app = FastAPI()
        app.add_middleware(ConsoleGuard)

        @app.post("/api/server/reconnect")
        async def reconnect():
            return {"ok": True}

        @app.get("/api/settings/logs")
        async def logs(request: Request):
            from api.trust import is_local_request

            return {"local": is_local_request(request)}

        # As the bot serves it: trusting X-Forwarded-* from the Funnel sidecar on loopback.
        return ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")

    async def post(self, headers: dict, base: str = "http://127.0.0.1:49152") -> int:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app(), client=("127.0.0.1", 50000)), base_url=base,
        ) as c:
            return (await c.post("/api/server/reconnect", headers=headers)).status_code

    async def test_a_scanning_page_is_refused(self) -> None:
        for origin in ("https://evil.example", "http://localhost:5555", "null"):
            self.assertEqual(await self.post({"origin": origin}), 403, origin)

    async def test_the_console_through_the_gateway_passes(self) -> None:
        console = {"origin": "http://127.0.0.1:8723", "host": "127.0.0.1:8723"}
        self.assertEqual(await self.post(console), 200)
        self.assertEqual(await self.post({}), 200)  # the gateway's own calls, the desktop shell

    async def test_a_remote_console_on_the_funnel_passes(self) -> None:
        funnel = {
            "origin": "https://olisar.tail1234.ts.net", "host": "olisar.tail1234.ts.net",
            "x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https",
            "x-forwarded-host": "olisar.tail1234.ts.net",
        }
        self.assertEqual(await self.post(funnel), 200)
        # A sidecar that rewrote Host to the backend still reports the public one.
        self.assertEqual(await self.post({**funnel, "host": "127.0.0.1:49152"}), 200)
        self.assertEqual(await self.post({**funnel, "origin": "https://evil.example"}), 403)

    async def get(self, headers: dict) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app(), client=("127.0.0.1", 50000)),
            base_url="http://127.0.0.1:49152",
        ) as c:
            return await c.get("/api/settings/logs", headers=headers)

    async def test_a_rebound_name_reads_nothing(self) -> None:
        self.assertEqual((await self.get({"host": "attacker.example:49152"})).status_code, 403)
        mine = await self.get({})
        self.assertEqual((mine.status_code, mine.json()), (200, {"local": True}))
        # Behind the Funnel: X-Forwarded-For and the public host. Served, as a remote visitor.
        funnel = await self.get({
            "host": "olisar.tail1234.ts.net", "x-forwarded-for": "198.51.100.7",
            "x-forwarded-proto": "https", "x-forwarded-host": "olisar.tail1234.ts.net",
        })
        self.assertEqual((funnel.status_code, funnel.json()), (200, {"local": False}))

    def test_the_bots_own_app_carries_the_guard(self) -> None:
        from api.main import create_app
        from api.trust import ConsoleGuard

        self.assertIn(ConsoleGuard, [m.cls for m in create_app().user_middleware])


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

    def db(self, path: Path, enabled: bool, public_host: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as con:
            con.execute(
                "CREATE TABLE app_config (id INTEGER PRIMARY KEY, tunnel_enabled BOOLEAN, tunnel_hostname TEXT)"
            )
            con.execute("INSERT INTO app_config VALUES (1, ?, ?)", (1 if enabled else 0, public_host))

    def node_named(self, host: str, domain: str = "tail1234.ts.net.") -> None:
        """The shared node's state as tsnet keeps it: base64 values, the prefs under the
        current profile's key."""
        import base64

        def b64(value) -> str:
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            return base64.b64encode(raw).decode()

        (self.home / "tailscale" / "tailscaled.state").write_text(json.dumps({
            "_current-profile": b64(b"profile-ab12"),
            "_profiles": b64({"ab12": {"ID": "ab12", "Key": "profile-ab12",
                                       "NetworkProfile": {"MagicDNSName": domain}}}),
            "profile-ab12": b64({"Hostname": host, "WantRunning": True}),
            "_machinekey": b64(b"privkey:0000"),
        }))

    def three_bots(self) -> tuple[str, str, str]:
        """The original bot with remote access off, and three others that had it on."""
        from olisar.runtime import profiles

        self.db(self.home / "olisar.db", False)
        ids = []
        for name, host in (("A", "abot.tail1234.ts.net"), ("B", "bbot.tail1234.ts.net"),
                           ("C", "cbot-1.tail1234.ts.net")):
            ids.append(profiles.create(name)["id"])
            self.db(profiles.db_path_for(ids[-1]), True, host)
        return tuple(ids)

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

    def test_a_path_with_a_hash_or_question_mark_is_read_where_it_is(self) -> None:
        """Unescaped, "#" ends a SQLite URI's path: the check read a new, empty (and writable)
        database at "Olisar " instead, and the bot's remote access looked off."""
        from pathlib import PureWindowsPath

        from olisar.runtime import gateway

        folder = self.home / "Olisar #2?"
        self.db(folder / "olisar.db", True, "olisar.tail1234.ts.net")
        self.assertEqual(gateway._remote_access(folder / "olisar.db"), (True, "olisar.tail1234.ts.net"))
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["Olisar #2?", "tailscale"])
        with sqlite3.connect(gateway._read_only_uri(folder / "olisar.db"), uri=True) as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("UPDATE app_config SET tunnel_enabled = 0")
        self.assertEqual(
            gateway._read_only_uri(PureWindowsPath(r"C:\Users\Ann\AppData\Roaming\Olisar #2\olisar.db")),
            "file:///C:/Users/Ann/AppData/Roaming/Olisar%20%232/olisar.db?mode=ro",
        )

    def test_of_several_it_goes_to_the_bot_last_on_screen(self) -> None:
        """That bot ran last, so the node carries its name. The others get devices of their
        own; nobody keeping it would give every one of them a new address."""
        from olisar.runtime import gateway, profiles

        a, b, c = self.three_bots()
        self.node_named("cbot")
        self.assertEqual(gateway.adopt_shared_tailscale(last_active=b), b)
        self.assertTrue((profiles.data_dir_for(b) / "tailscale" / "tailscaled.state").exists())
        self.assertIsNone(gateway.adopt_shared_tailscale(last_active=b))  # it's moved once

    def test_else_to_the_bot_whose_address_is_the_nodes_name(self) -> None:
        from olisar.runtime import gateway

        a, b, c = self.three_bots()
        self.node_named("cbot")  # Tailscale called it cbot-1: another device had the name
        self.assertEqual(gateway.adopt_shared_tailscale(last_active="default"), c)

    def test_else_to_the_oldest(self) -> None:
        from olisar.runtime import gateway

        a, b, c = self.three_bots()  # the node's state is unreadable ("node")
        self.assertEqual(gateway.adopt_shared_tailscale(), a)

    def test_a_node_named_for_none_of_them_goes_to_the_oldest(self) -> None:
        from olisar.runtime import gateway

        a, b, c = self.three_bots()
        self.node_named("somebody-else")
        self.assertEqual(gateway.adopt_shared_tailscale(last_active="gone"), a)


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


_FAKE_HELPER = """\
import sys, time
print("OLISAR_FUNNEL_URL=https://fake-node.example.ts.net", flush=True)
time.sleep(600)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A child that has exited but not been waited for is still "there"; ps says it's a zombie.
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(out.strip()) and not out.strip().startswith("Z")


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


@unittest.skipIf(sys.platform == "win32", "uses ps and POSIX signals")
class FunnelHelperTests(unittest.IsolatedAsyncioTestCase):
    """The Tailscale helper is a process of its own, and nothing ties it to the bot's: one that
    outlives its backend keeps the bot's node and public address, serving a dead port."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = str(Path(self._tmp.name) / "tail scale")  # a space, as in "Application Support"
        helper = Path(self._tmp.name) / "olisar-funnel"
        helper.write_text(f"#!{sys.executable}\n{_FAKE_HELPER}")
        helper.chmod(0o755)
        os.environ["OLISAR_FUNNEL"] = str(helper)
        self.addCleanup(os.environ.pop, "OLISAR_FUNNEL", None)

    def orphan(self, state_dir: str) -> subprocess.Popen:
        """A helper as an earlier backend would have left it: running, pid on file."""
        proc = subprocess.Popen(
            [os.environ["OLISAR_FUNNEL"], "--hostname", "x", "--target", "http://127.0.0.1:1",
             "--state", state_dir],
            stdout=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        (Path(state_dir) / "olisar-funnel.pid").write_text(f"{proc.pid}\n")
        time.sleep(0.3)
        return proc

    async def test_a_helper_left_running_is_stopped_before_a_new_one_starts(self) -> None:
        from olisar.runtime.tunnel import PIDFILE, FunnelManager

        stale = self.orphan(self.state_dir)
        funnel = FunnelManager()
        ok, url = await funnel.start("", "x", "http://127.0.0.1:1", self.state_dir)
        self.assertEqual((ok, url), (True, "https://fake-node.example.ts.net"))
        self.assertIsNotNone(stale.wait(10))
        mine = funnel._proc.pid
        self.assertEqual((Path(self.state_dir) / PIDFILE).read_text().strip(), str(mine))
        await funnel.stop()
        self.assertFalse((Path(self.state_dir) / PIDFILE).exists())
        self.assertTrue(_gone(mine))

    def test_only_this_bots_helper_is_stopped(self) -> None:
        """A pid on file that's since gone to some other process is left alone."""
        from olisar.runtime.tunnel import PIDFILE, reap_stale_helper

        other_bot = self.orphan(str(Path(self._tmp.name) / "other"))
        Path(self.state_dir).mkdir(parents=True)
        (Path(self.state_dir) / PIDFILE).write_text(f"{other_bot.pid}\n")
        self.assertIsNone(reap_stale_helper(self.state_dir))
        self.assertIsNone(other_bot.poll())
        self.assertFalse((Path(self.state_dir) / PIDFILE).exists())

    def test_a_worker_past_its_shutdown_deadline_stops_its_helper_first(self) -> None:
        script = (
            "import asyncio, sys\n"
            "from olisar.runtime import server\n"
            "from olisar.runtime.tunnel import FunnelManager\n"
            "async def main():\n"
            "    funnel = FunnelManager()\n"
            f"    await funnel.start('', 'x', 'http://127.0.0.1:1', {self.state_dir!r})\n"
            "    print(funnel._proc.pid, flush=True)\n"
            "    server._exit_now()\n"
            "asyncio.run(main())\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
            cwd=self._tmp.name, env={**os.environ, "PYTHONPATH": str(REPO), "OLISAR_NO_DOTENV": "1"},
        )
        helper = int(out.stdout.split()[0])
        self.assertTrue(_gone(helper), "the helper outlived its backend")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"null")


def _isolated_env(data_dir: str, **extra: str) -> dict[str, str]:
    """An environment for a real gateway that can't reach a real bot: no Discord or Olisar
    settings inherited from the shell, no ``.env`` (config reads one from the working
    directory, and a developer's has a live token in it), and an explicitly empty token."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DISCORD_", "OLISAR_"))}
    env.update(
        OLISAR_DATA_DIR=data_dir, OLISAR_NO_DOTENV="1", DISCORD_TOKEN="",
        PYTHONPATH=os.pathsep.join(filter(None, (str(REPO), env.get("PYTHONPATH")))),
        PYTHONUNBUFFERED="1", **extra,
    )
    return env


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
        env = _isolated_env(tmp.name)
        before = set(_workers())
        gw = subprocess.Popen(
            [sys.executable, "-m", "olisar.runtime", "--gateway", "--port", str(port)],
            cwd=tmp.name, env=env,
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
        env = _isolated_env(tmp.name, OLISAR_PARENT_PIPE="1")
        before = set(_workers())
        gw = subprocess.Popen(
            [sys.executable, "-m", "olisar.runtime", "--gateway", "--port", str(port)],
            cwd=tmp.name, env=env, stdin=subprocess.PIPE,
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
