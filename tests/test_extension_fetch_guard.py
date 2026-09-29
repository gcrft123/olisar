"""An extension's ``host.fetch`` stays on the public internet, redirects included.

Run:  uv run python -m unittest tests.test_extension_fetch_guard -v

A public host that answers 307 to ``http://127.0.0.1:8723/...`` used to carry the request,
method and body included, to the operator's own loopback routes. These run a real HTTP server
on loopback and let a made-up public name point at it, so what's checked is the connection
the fetch actually makes: each hop's address is vetted and pinned, and a hop that isn't
public is never sent.
"""

from __future__ import annotations

import unittest
from unittest import mock

import httpx
from aiohttp import web

from olisar import netguard
from olisar.sandbox import capabilities
from olisar.sandbox.capabilities import Invocation, dispatch

PUBLIC = "public.test"  # stands in for a public host; resolves to the loopback test server


class AddressTests(unittest.TestCase):
    def test_only_globally_routable_addresses_are_public(self) -> None:
        for address in ("93.184.216.34", "2606:4700::1", "1.1.1.1"):
            self.assertTrue(netguard.is_public_address(address), address)
        for address in (
            "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0",
            "100.64.0.1", "100.100.100.100",       # Tailscale's shared range and its service
            "::1", "fe80::1%en0", "fd7a:115c:a1e0::1", "ff02::1",
            "::ffff:127.0.0.1",                    # IPv4-mapped
            "::127.0.0.1",                         # IPv4-compatible
            "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",  # NAT64 of loopback and metadata
            "2002:7f00:1::",                       # 6to4 of loopback
            "224.0.0.1", "not an address", "",
        ):
            self.assertFalse(netguard.is_public_address(address), address)


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.hits: list[str] = []

        async def handler(request: web.Request) -> web.Response:
            self.hits.append(f"{request.method} {request.path} host={request.host}")
            if request.path == "/to-loopback":
                raise web.HTTPTemporaryRedirect(f"http://127.0.0.1:{self.port}/api/bots")
            if request.path == "/to-metadata":
                raise web.HTTPFound("http://169.254.169.254/latest/meta-data/")
            if request.path == "/hop":
                resp = web.HTTPFound(f"http://{PUBLIC}:{self.port}/landed")
                resp.set_cookie("hop", "1")
                raise resp
            if request.path == "/loop":
                raise web.HTTPFound(f"http://{PUBLIC}:{self.port}/loop")
            return web.json_response({
                "path": request.path, "ua": request.headers.get("user-agent", ""),
                "cookie": request.headers.get("cookie", ""), "body": await request.text(),
            })

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

        # The made-up public name resolves to the test server, and passes as public. Every
        # other name, 127.0.0.1 included, goes through the real check.
        real_resolve = netguard.resolve_public

        async def resolve(host: str) -> str:
            return "127.0.0.1" if host == PUBLIC else await real_resolve(host)

        patcher = mock.patch.object(netguard, "resolve_public", resolve)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()

    async def fetch(self, path: str, init: dict | None = None) -> dict:
        inv = Invocation(ext_key="x", permissions={"fetch"}, guild_id=1)
        return await dispatch(inv, "fetch", "request", [f"http://{PUBLIC}:{self.port}{path}", init or {}])

    async def test_a_public_host_is_fetched_over_the_vetted_address(self) -> None:
        out = await self.fetch("/hello", {"method": "POST", "body": "hi"})
        self.assertEqual(out["status"], 200)
        self.assertIn("python-httpx", out["body"])  # the client's default headers still go
        self.assertIn('"body": "hi"', out["body"])
        self.assertEqual(self.hits, [f"POST /hello host={PUBLIC}:{self.port}"])

    async def test_a_redirect_to_loopback_is_never_sent(self) -> None:
        with self.assertRaisesRegex(ValueError, "isn't allowed"):
            await self.fetch("/to-loopback", {"method": "POST", "body": '{"on": false}'})
        self.assertEqual(self.hits, [f"POST /to-loopback host={PUBLIC}:{self.port}"])

    async def test_a_redirect_to_cloud_metadata_is_never_sent(self) -> None:
        with self.assertRaisesRegex(ValueError, "isn't allowed"):
            await self.fetch("/to-metadata")

    async def test_redirects_between_public_hosts_are_followed(self) -> None:
        out = await self.fetch("/hop", {"method": "POST", "body": "x"})
        self.assertEqual(out["status"], 200)
        self.assertIn('"path": "/landed"', out["body"])
        self.assertIn("hop=1", out["body"])  # a cookie set on the way carries on
        self.assertEqual(len(self.hits), 2)
        self.assertTrue(self.hits[1].startswith("GET /landed"))  # 302 turns a POST into a GET

    async def test_redirect_loops_stop(self) -> None:
        with self.assertRaisesRegex(ValueError, "too many redirects"):
            await self.fetch("/loop")
        self.assertEqual(len(self.hits), capabilities._FETCH_MAX_REDIRECTS + 1)

    async def test_the_pinned_connection_ignores_what_the_name_resolves_to_later(self) -> None:
        """DNS rebinding: the name is resolved once, checked, and the connection goes to that
        address. The transport never resolves it again."""
        transport = netguard.PinnedTransport("rebinding.test", "127.0.0.1")
        async with httpx.AsyncClient(transport=transport) as c:
            r = await c.get(f"http://rebinding.test:{self.port}/pinned")
        self.assertEqual(r.status_code, 200)
        with self.assertRaises(httpx.ConnectError):
            async with httpx.AsyncClient(transport=netguard.PinnedTransport("rebinding.test", "127.0.0.1")) as c:
                await c.get(f"http://another.test:{self.port}/")


if __name__ == "__main__":
    unittest.main()
