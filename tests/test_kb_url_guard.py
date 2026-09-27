"""Knowledge sources added from chat are only read from public addresses.

Run:  uv run python -m unittest tests.test_kb_url_guard -v

Olisar runs inside someone's network. ``kb_add_page`` and ``kb_add_site`` took any http(s)
URL a member gave in chat, and the crawler fetched whatever it pointed at, so a member could
pull a router's admin page, a NAS, or a cloud metadata endpoint into the knowledge base and
ask about it. The address a host resolves to is what's checked, when the source is added
and again on every request its crawls make, redirects included. Nothing here touches the
network: name resolution is stubbed and HTTP goes through httpx's MockTransport.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from olisar.db.models import KBSourceType
from olisar.knowledge import crawler, ingest

ADDRESSES = {
    "public.example": ["93.184.216.34"],
    "internal.example": ["10.0.0.5"],
}


def _resolve(host: str) -> list[str]:
    return ADDRESSES[host]


def _site(request: httpx.Request) -> httpx.Response:
    """A public page that redirects to a private one."""
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    if request.url.host == "public.example":
        return httpx.Response(302, headers={"location": "http://internal.example/admin"})
    html = "<html><title>router</title><body><p>" + "admin password is hunter2. " * 20
    return httpx.Response(200, headers={"content-type": "text/html"}, text=html + "</p></body></html>")


class _Stubbed(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        real = httpx.AsyncClient
        transport = httpx.MockTransport(_site)
        for p in (
            patch.object(crawler, "_resolve", new=AsyncMock(side_effect=_resolve)),
            patch.object(crawler.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw)),
            patch.object(crawler, "DELAY_SECONDS", 0),
        ):
            p.start()
            self.addCleanup(p.stop)


class ResolvedAddresses(_Stubbed):
    async def test_public_is_fine_and_private_is_not(self):
        self.assertEqual(await crawler.non_public_reason("https://public.example/x"), "")
        self.assertIn("private", await crawler.non_public_reason("http://internal.example/"))

    async def test_a_name_that_does_not_resolve_is_refused(self):
        crawler._resolve.side_effect = OSError("nodename nor servname provided")
        self.assertIn("doesn't resolve", await crawler.non_public_reason("https://nope.example"))


class Crawling(_Stubbed):
    async def test_a_redirect_onto_the_network_is_not_followed(self):
        pages = await crawler.crawl("https://public.example/", max_depth=0, public_only=True)
        self.assertEqual(pages, [])

    async def test_a_private_start_page_is_the_sources_error(self):
        with self.assertRaises(crawler.NonPublicHost):
            await crawler.fetch_page("http://internal.example/admin", public_only=True)

    async def test_an_operators_own_source_is_read_as_before(self):
        """Sources from the console may be on the operator's own network."""
        pages = await crawler.crawl("https://public.example/", max_depth=0)
        self.assertEqual(len(pages), 1)
        self.assertIn("hunter2", pages[0].text)

    async def test_ingest_passes_the_flag_through(self):
        for stype, fn in ((KBSourceType.url, "fetch_page"), (KBSourceType.website, "crawl")):
            with self.subTest(stype=stype), patch.object(
                ingest, fn, new=AsyncMock(return_value=[] if fn == "crawl" else None)
            ) as spy:
                await ingest._gather(stype, "https://public.example/", 1, 5, public_only=True)
            self.assertIs(spy.await_args.kwargs["public_only"], True)


if __name__ == "__main__":
    unittest.main()
