"""Knowledge sources are only read from public addresses.

Run:  uv run python -m unittest tests.test_kb_url_guard -v

Olisar runs inside someone's network. ``kb_add_page`` and ``kb_add_site`` took any http(s)
URL a member gave in chat, and the crawler fetched whatever it pointed at, so a member could
pull a router's admin page, a NAS, or a cloud metadata endpoint into the knowledge base and
ask about it. The address a host resolves to is what's checked, when the source is added
and again on every request its crawls make, redirects included.

Checking wasn't enough on its own: the crawler checked a name, then httpx looked it up again
to connect, so a name that answered with a public address for the check and 127.0.0.1 for
the connection (DNS rebinding) still reached the LAN. Each request now connects to the
address that was checked.

Only chat-added sources were guarded, though. ``/olisar learn-url`` and ``learn-site``, the
console, extension seeds and the Star Citizen seed queued sources that were read from
anywhere, and any server's admin can use the first two. Every source is public-only now,
including rows stored before this that say otherwise.

The crawls here run against a real HTTP server on loopback, with name resolution stubbed so
a made-up public name points at it, and the database is a temp SQLite file; nothing leaves
the machine.
"""

from __future__ import annotations

import contextlib
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar import netguard
from olisar.db.models import Base, KBSource, KBSourceType, KBStatus
from olisar.knowledge import crawler, ingest, sources

PUBLIC = "public.test"  # passes as public; its connections land on the loopback test server

# What each name resolves to when a source is added (the up-front check).
ADDRESSES = {
    PUBLIC: ["93.184.216.34"],
    "internal.test": ["10.0.0.5"],
    "rebind.test": ["93.184.216.34"],
}
# What DNS answers afterwards, when a crawl looks a name up to connect. rebind.test has
# changed its answer to the loopback test server.
AT_CONNECT = {PUBLIC: "127.0.0.1", "internal.test": "10.0.0.5", "rebind.test": "127.0.0.1"}

ADMIN_PAGE = "<html><title>router</title><body><p>" + "admin password is hunter2. " * 20
WIKI_PAGE = "<html><title>wiki</title><body><p>" + "the public wiki says hello. " * 20

_real_getaddrinfo = socket.getaddrinfo
_real_resolve_public = netguard.resolve_public


async def _resolve(host: str) -> list[str]:
    return ADDRESSES.get(host, [host])


def _getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, (bytes, bytearray)) else host
    return _real_getaddrinfo(AT_CONNECT.get(name, host), *args, **kwargs)


async def _resolve_public(host: str) -> str:
    # public.test stands in for a public host, so it passes the check it would fail as
    # loopback. Every other name, 127.0.0.1 included, goes through the real one.
    return "127.0.0.1" if host == PUBLIC else await _real_resolve_public(host)


class ResolvedAddresses(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        p = patch.object(crawler, "_resolve", new=AsyncMock(side_effect=_resolve))
        p.start()
        self.addCleanup(p.stop)

    async def test_public_is_fine_and_private_is_not(self):
        self.assertEqual(await crawler.non_public_reason("https://public.test/x"), "")
        self.assertIn("private", await crawler.non_public_reason("http://internal.test/"))

    async def test_a_name_that_does_not_resolve_is_refused(self):
        crawler._resolve.side_effect = None
        crawler._resolve.return_value = []
        self.assertIn("doesn't resolve", await crawler.non_public_reason("https://nope.test"))

    async def test_ipv4_hidden_in_ipv6_is_judged_by_the_ipv4(self):
        for url in ("http://[64:ff9b::a9fe:a9fe]/", "http://[64:ff9b::7f00:1]/",
                    "http://[::127.0.0.1]/", "http://[::ffff:10.0.0.1]/"):
            with self.subTest(url=url):
                self.assertIn("private", await crawler.non_public_reason(url))


class _Site(unittest.IsolatedAsyncioTestCase):
    """The loopback server, reachable as public.test, and the stubbed resolution."""

    async def asyncSetUp(self):
        self.hits: list[str] = []
        self.robots: str | None = None

        async def handler(request: web.Request) -> web.StreamResponse:
            self.hits.append(request.path)
            self.user_agent = request.headers.get("user-agent", "")
            if request.path == "/robots.txt":
                if self.robots is None:
                    raise web.HTTPNotFound()
                return web.Response(text=self.robots)
            if request.path == "/to-loopback":
                raise web.HTTPFound(f"http://127.0.0.1:{self.port}/admin")
            if request.path == "/to-internal":
                raise web.HTTPFound(f"http://internal.test:{self.port}/admin")
            if request.path == "/hop":
                raise web.HTTPFound(f"http://{PUBLIC}:{self.port}/wiki")
            page = ADMIN_PAGE if request.path == "/admin" else WIKI_PAGE
            return web.Response(text=page + "</p></body></html>", content_type="text/html")

        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

        for p in (
            patch.object(crawler, "_resolve", new=AsyncMock(side_effect=_resolve)),
            patch("socket.getaddrinfo", _getaddrinfo),
            patch.object(netguard, "resolve_public", _resolve_public),
            patch.object(crawler, "DELAY_SECONDS", 0),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.runner.cleanup()

    def url(self, path: str, host: str = PUBLIC) -> str:
        return f"http://{host}:{self.port}{path}"


class Crawling(_Site):
    async def test_a_public_page_is_read(self):
        pages = await crawler.crawl(self.url("/wiki"), max_depth=0, public_only=True)
        self.assertEqual(len(pages), 1)
        self.assertIn("public wiki", pages[0].text)
        self.assertEqual(self.hits, ["/robots.txt", "/wiki"])
        self.assertEqual(self.user_agent, crawler.USER_AGENT)

    async def test_a_redirect_onto_the_network_is_never_sent(self):
        for path in ("/to-loopback", "/to-internal"):
            with self.subTest(path=path):
                self.hits.clear()
                pages = await crawler.crawl(self.url(path), max_depth=0, public_only=True)
                self.assertEqual(pages, [])
                self.assertEqual(self.hits, ["/robots.txt", path])

    async def test_a_redirect_between_public_pages_is_followed(self):
        pages = await crawler.crawl(self.url("/hop"), max_depth=0, public_only=True)
        self.assertEqual(len(pages), 1)
        self.assertEqual(self.hits, ["/robots.txt", "/hop", "/wiki"])

    async def test_a_name_that_changes_its_answer_reads_nothing(self):
        """DNS rebinding: rebind.test is public when it's checked, loopback when it's
        connected to. The connection goes to the address that was checked or nowhere."""
        page = await crawler.fetch_page(self.url("/admin", "rebind.test"), public_only=True)
        self.assertIsNone(page)
        self.assertEqual(self.hits, [])

    async def test_a_private_start_page_is_the_sources_error(self):
        with self.assertRaisesRegex(crawler.NonPublicHost, "private or local address"):
            await crawler.fetch_page(self.url("/admin", "internal.test"), public_only=True)
        self.assertEqual(self.hits, [])

    async def test_robots_txt_is_still_honored(self):
        self.robots = "User-agent: *\nDisallow: /admin\n"
        pages = await crawler.crawl(self.url("/admin"), max_depth=0, public_only=True)
        self.assertEqual(pages, [])
        self.assertEqual(self.hits, ["/robots.txt"])


class EverySource(_Site):
    """Ingestion, end to end: a queued source is claimed, crawled and settled."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp.name) / 'kb.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        p = patch.object(ingest, "session_scope", self.scope)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.engine.dispose()
        await super().asyncTearDown()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()

    async def ingest(self, uri: str, **row) -> KBSource:
        async with self.scope() as s:
            s.add(KBSource(guild_id=1, type=KBSourceType.url, uri=uri, **row))
        self.assertTrue(await ingest.process_pending_sources())
        async with self.Session() as s:
            return (await s.scalars(select(KBSource))).one()

    async def test_a_source_stored_as_readable_from_anywhere_is_still_public_only(self):
        """A row from before this, e.g. one the console added, that says public_only False."""
        src = await self.ingest(self.url("/admin", "127.0.0.1"), public_only=False)
        self.assertIs(src.status, KBStatus.error)
        self.assertEqual(src.error, "not read: 127.0.0.1 points at a private or local address")
        self.assertEqual(self.hits, [])

    async def test_a_redirect_onto_the_network_reads_nothing(self):
        src = await self.ingest(self.url("/to-loopback"), public_only=False)
        self.assertIs(src.status, KBStatus.error)
        self.assertEqual(self.hits, ["/robots.txt", "/to-loopback"])

    async def test_a_public_source_is_read(self):
        src = await self.ingest(self.url("/wiki"))
        self.assertIs(src.status, KBStatus.ready)

    async def test_new_sources_are_stored_public_only(self):
        """learn-url, learn-site, extension seeds and the Star Citizen seed build the row
        without naming the flag, and the console goes through new_source."""
        async with self.scope() as s:
            s.add(KBSource(guild_id=1, type=KBSourceType.website, uri="https://a.example"))
            s.add(sources.new_source(guild_id=1, type="url", uri="https://b.example",
                                     crawl_depth=0, max_pages=1, refresh_hours=0, added_by=None))
        async with self.Session() as s:
            flags = (await s.scalars(select(KBSource.public_only))).all()
        self.assertEqual(flags, [True, True])


if __name__ == "__main__":
    unittest.main()
