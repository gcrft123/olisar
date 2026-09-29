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

    async def get(self, url: str) -> httpx.Response:
        target = httpx.URL(url)
        for _ in range(MAX_REDIRECTS + 1):
            resp = await (await self._client(target)).get(target)
            if resp.next_request is None:
                return resp
            target = resp.next_request.url
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=resp.request)


@dataclass
class Page:
    url: str
    title: str | None
    text: str


async def _load_robots(fetcher: _Fetcher, start_url: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        resp = await fetcher.get(urljoin(start_url, "/robots.txt"))
        rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
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
                resp = await fetcher.get(url)
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("content-type", ""):
                continue

            html = resp.text
            text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
            if text.strip():
                pages.append(Page(url=url, title=_title_of(html), text=text))

            if depth < max_depth:
                for link in _extract_links(html, url, root_netloc):
                    if link not in seen:
                        queue.append((link, depth + 1))
            await asyncio.sleep(DELAY_SECONDS)

    return pages


async def fetch_page(url: str, *, public_only: bool = False) -> Page | None:
    """Fetch and extract a single page (KB source type 'url')."""
    pages = await crawl(url, max_depth=0, max_pages=1, public_only=public_only)
    return pages[0] if pages else None
