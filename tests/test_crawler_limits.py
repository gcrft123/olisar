"""A knowledge-base crawl reads a bounded amount of each response, in bounded time.

Run:  uv run python -m unittest tests.test_crawler_limits -v

The crawler read every response whole into memory, and httpx's timeout only bounds each wait
on the server, so a page someone added could stream gigabytes into the bot's memory, or send a
byte every few seconds and hold the ingest worker forever. robots.txt was read the same way.
Now a page is read up to a cap (and not at all unless it's HTML), robots.txt up to the
500 KiB RFC 9309 asks crawlers to read, and a request, redirects included, has a deadline.

Responses come from httpx's MockTransport with bodies generated as they're read, so what's
measured is how much the crawler pulls. These run the public-only path, the one every source
takes; nothing touches the network.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from olisar import netguard
from olisar.knowledge import crawler

SITE = "http://public.test"
CHUNK = 64 * 1024
MB = 1024 * 1024


def _page(sentence: bytes) -> bytes:
    return b"<html><body><p>" + sentence * 100 + b"</p></body></html>"


class _Site(unittest.IsolatedAsyncioTestCase):
    """A made-up public site. ``routes`` maps a path to a function returning its response;
    ``pulled`` is how many body bytes of each path the crawler read."""

    async def asyncSetUp(self):
        self.routes: dict = {"/robots.txt": lambda: httpx.Response(404)}
        self.pulled: dict[str, int] = {}
        self.hits: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            self.hits.append(request.url.path)
            route = self.routes.get(request.url.path, lambda: httpx.Response(404))
            return route()

        transport = httpx.MockTransport(handle)
        for p in (
            patch.object(crawler, "_resolve", new=AsyncMock(return_value=["93.184.216.34"])),
            patch.object(netguard, "resolve_public", new=AsyncMock(return_value="93.184.216.34")),
            patch.object(netguard, "PinnedTransport", lambda host, address: transport),
            patch.object(crawler, "DELAY_SECONDS", 0),
        ):
            p.start()
            self.addCleanup(p.stop)

    def body(self, path: str, chunk: bytes, total: int, *, every: float = 0.0):
        """``total`` bytes of ``chunk`` repeated, produced only as they're read."""

        async def produce():
            sent = 0
            while sent < total:
                if every:
                    await asyncio.sleep(every)
                yield chunk
                sent += len(chunk)
                self.pulled[path] = sent

        return produce()

    def serve(self, path: str, content_type: str, chunk: bytes, total: int, **kw):
        self.routes[path] = lambda: httpx.Response(
            200, headers={"content-type": content_type}, content=self.body(path, chunk, total, **kw)
        )


class BodySize(_Site):
    async def test_a_huge_page_is_read_only_up_to_the_cap(self):
        paragraph = b"<p>" + b"all work and no play makes a long page. " * 1600 + b"</p>\n"
        self.serve("/big", "text/html", paragraph, 30 * MB)
        with patch.object(crawler, "MAX_PAGE_BYTES", 256 * 1024):
            page = await crawler.fetch_page(f"{SITE}/big", public_only=True)
        self.assertLessEqual(self.pulled["/big"], 256 * 1024 + len(paragraph))
        self.assertIsNotNone(page)  # what was read is still used
        self.assertLessEqual(len(page.text), 256 * 1024)

    async def test_a_response_that_is_not_html_is_never_downloaded(self):
        self.serve("/blob", "application/octet-stream", b"A" * CHUNK, 30 * MB)
        self.assertIsNone(await crawler.fetch_page(f"{SITE}/blob", public_only=True))
        self.assertNotIn("/blob", self.pulled)

    async def test_robots_txt_is_read_up_to_500_kib(self):
        rules = b"User-agent: *\nDisallow: /private\n"
        self.routes["/robots.txt"] = lambda: httpx.Response(
            200, headers={"content-type": "text/plain"},
            content=self._robots(rules, b"# " + b"x" * (CHUNK - 3) + b"\n", 20 * MB),
        )
        self.serve("/private", "text/html", _page(b"the secret plans are here. "), 1)
        self.assertIsNone(await crawler.fetch_page(f"{SITE}/private", public_only=True))
        self.assertNotIn("/private", self.hits)  # the rules at the top still count
        self.assertLessEqual(self.pulled["/robots.txt"], crawler.MAX_ROBOTS_BYTES + CHUNK)

    def _robots(self, head: bytes, filler: bytes, total: int):
        async def produce():
            yield head
            sent = len(head)
            while sent < total:
                yield filler
                sent += len(filler)
                self.pulled["/robots.txt"] = sent

        return produce()

    async def test_a_redirects_body_is_not_read(self):
        self.routes["/moved"] = lambda: httpx.Response(
            302, headers={"location": "/landed"}, content=self.body("/moved", b"A" * CHUNK, 30 * MB)
        )
        self.serve("/landed", "text/html", _page(b"you made it here. "), 1)
        page = await crawler.fetch_page(f"{SITE}/moved", public_only=True)
        self.assertIn("you made it here", page.text)
        self.assertNotIn("/moved", self.pulled)


class Time(_Site):
    async def test_a_slow_drip_is_cut_off_at_the_deadline(self):
        # A byte every 50 ms never trips the 15 s read timeout, and would take 20 s here.
        self.serve("/robots.txt", "text/plain", b"#", 400, every=0.05)
        self.serve("/slow", "text/html", b"<", 400, every=0.05)
        started = time.monotonic()
        with patch.object(crawler, "REQUEST_DEADLINE", 0.3):
            page = await crawler.fetch_page(f"{SITE}/slow", public_only=True)
        self.assertIsNone(page)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertLess(self.pulled["/slow"], 400)

    async def test_a_redirect_loop_stops(self):
        self.routes["/loop"] = lambda: httpx.Response(302, headers={"location": "/loop"})
        self.assertIsNone(await crawler.fetch_page(f"{SITE}/loop", public_only=True))
        self.assertEqual(self.hits.count("/loop"), crawler.MAX_REDIRECTS + 1)


if __name__ == "__main__":
    unittest.main()
