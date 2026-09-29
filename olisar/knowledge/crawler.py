"""Polite website crawler for the knowledge base.

Same-domain breadth-first crawl with a depth and page cap, honoring robots.txt,
sending a clear User-Agent, and pausing between requests. Main content is
extracted with trafilatura (strips nav/boilerplate); links come from BeautifulSoup.

A source a member added from chat is crawled ``public_only``: every request, redirects
included, has to go to a host that resolves only to public addresses, and connects to the
address that was checked (see :mod:`olisar.netguard`). Olisar runs inside someone's
network, and without that a member could have it read a router's admin page or a cloud
metadata endpoint into the knowledge base and then ask about it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup

from olisar import netguard
from olisar.netguard import NonPublicHost

log = logging.getLogger("olisar.knowledge.crawler")

USER_AGENT = "OlisarBot/1.0 (Discord community knowledge crawler)"
TIMEOUT = 15.0
DELAY_SECONDS = 0.5
MAX_REDIRECTS = 10
# TIMEOUT bounds each wait on the server, not a whole response, so a server sending a byte
# every few seconds could hold a crawl forever. A request, redirects included, gets this long.
REQUEST_DEADLINE = 20.0
# A page is held in memory to be parsed, so no more than this is read of one: 10 MB, the
# largest document the knowledge base takes as an upload. The rest is never downloaded.
MAX_PAGE_BYTES = 10 * 1024 * 1024
# RFC 9309 has crawlers read at least the first 500 KiB of a robots.txt; that's all we read.
MAX_ROBOTS_BYTES = 500 * 1024


async def _resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to."""
    return await netguard.lookup(host)


async def non_public_reason(url: str) -> str:
    """Why ``url`` mustn't be fetched for a ``public_only`` source, or "" when its host
    resolves only to public addresses. The addresses are what's checked, not the name: a
    public-looking name can point anywhere. This answers whoever is adding the source; a
    crawl still resolves, checks and pins every request it makes."""
    host = urlparse(url).hostname
    if not host:
        return "it has no host"
    try:
        netguard.public_address(host, await _resolve(host))
    except NonPublicHost as exc:
        return str(exc)
    return ""


class _Fetcher:
    """The GETs of one crawl.

    Redirects are followed here, a hop at a time, rather than by httpx. For a
    ``public_only`` crawl each hop's host is resolved and checked before it's contacted,
    and the connection goes to the address that was checked, so neither a redirect nor a
    name that answers differently the second time it's looked up (DNS rebinding) can point
    the crawler at the LAN. A client per host lasts the whole crawl, so a site's pages
    share connections and the site is resolved once."""

    def __init__(self, *, public_only: bool) -> None:
        self._public_only = public_only
        self._clients: dict[str, httpx.AsyncClient] = {}

    async def __aenter__(self) -> _Fetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for client in self._clients.values():
            await client.aclose()

    async def _client(self, url: httpx.URL) -> httpx.AsyncClient:
        host = url.raw_host.decode("ascii") if self._public_only else ""
        client = self._clients.get(host)
        if client is None:
            pinned = {}
            if self._public_only:
                address = await netguard.resolve_public(host)
                pinned["transport"] = netguard.PinnedTransport(host, address)
            client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, **pinned
            )
            self._clients[host] = client
        return client

    async def get_text(self, url: str, *, limit: int, html_only: bool = False) -> str | None:
        """``url``'s body as text, or None unless the answer is a 200 (and, with
        ``html_only``, HTML). Only the first ``limit`` bytes are read, and the request has
        REQUEST_DEADLINE to finish, redirects included (TimeoutError past it)."""
        target = httpx.URL(url)
        async with asyncio.timeout(REQUEST_DEADLINE):
            for _ in range(MAX_REDIRECTS + 1):
                client = await self._client(target)
                async with client.stream("GET", target) as resp:
                    if resp.next_request is not None:
                        target = resp.next_request.url
                        continue
                    if resp.status_code != 200:
                        return None
                    if html_only and "text/html" not in resp.headers.get("content-type", ""):
                        return None
                    body = await _read(resp, limit)
                    return body.decode(resp.encoding or "utf-8", errors="replace")
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=resp.request)


async def _read(resp: httpx.Response, limit: int) -> bytearray:
    body = bytearray()
    async for chunk in resp.aiter_bytes():
        body += chunk[: limit - len(body)]
        if len(body) >= limit:
            log.info("read only the first %d bytes of %s", limit, resp.url)
            break
    return body


@dataclass
class Page:
    url: str
    title: str | None
    text: str


async def _load_robots(fetcher: _Fetcher, start_url: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        text = await fetcher.get_text(urljoin(start_url, "/robots.txt"), limit=MAX_ROBOTS_BYTES)
        rp.parse(text.splitlines() if text is not None else [])
    except Exception:
        rp.parse([])  # no robots reachable -> allow
    return rp


def _extract_links(html: str, base_url: str, root_netloc: str) -> set[str]:
    out: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("mailto:", "javascript:", "tel:", "#")):
            continue
        full = urldefrag(urljoin(base_url, href))[0]
        parsed = urlparse(full)
        if parsed.scheme in ("http", "https") and parsed.netloc == root_netloc:
            out.add(full)
    return out


def _title_of(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None


def _parse(
    html: str, url: str, root_netloc: str, *, links: bool
) -> tuple[Page | None, set[str]]:
    """The page's main text and title (None when there's no text), and the same-site links
    on it when ``links``. A big page takes a while to parse, so the crawl runs this in a
    worker thread rather than on the event loop the bot and the API share."""
    text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    page = Page(url=url, title=_title_of(html), text=text) if text.strip() else None
    return page, _extract_links(html, url, root_netloc) if links else set()


async def crawl(
    start_url: str, *, max_depth: int = 1, max_pages: int = 25, public_only: bool = False
) -> list[Page]:
    root_netloc = urlparse(start_url).netloc
    if not root_netloc:
        raise ValueError(f"invalid URL: {start_url}")
    if public_only:
        # Checked up front as well, so a refused start page is the source's error rather
        # than an empty read.
        reason = await non_public_reason(start_url)
        if reason:
            raise NonPublicHost(f"not read: {reason}")

    pages: list[Page] = []
    seen: set[str] = set()

    async with _Fetcher(public_only=public_only) as fetcher:
        robots = await _load_robots(fetcher, start_url)
        queue: list[tuple[str, int]] = [(start_url, 0)]

        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if not robots.can_fetch(USER_AGENT, url):
                log.info("robots.txt disallows %s", url)
                continue
            try:
                html = await fetcher.get_text(url, limit=MAX_PAGE_BYTES, html_only=True)
            except Exception:
                continue
            if html is None:
                continue

            page, links = await asyncio.to_thread(
                _parse, html, url, root_netloc, links=depth < max_depth
            )
            if page is not None:
                pages.append(page)
            for link in links:
                if link not in seen:
                    queue.append((link, depth + 1))
            await asyncio.sleep(DELAY_SECONDS)

    return pages


async def fetch_page(url: str, *, public_only: bool = False) -> Page | None:
    """Fetch and extract a single page (KB source type 'url')."""
    pages = await crawl(url, max_depth=0, max_pages=1, public_only=public_only)
    return pages[0] if pages else None
