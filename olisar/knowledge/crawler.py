"""Polite website crawler for the knowledge base.

Same-domain breadth-first crawl with a depth and page cap, honoring robots.txt,
sending a clear User-Agent, and pausing between requests. Main content is
extracted with trafilatura (strips nav/boilerplate); links come from BeautifulSoup.

A source a member added from chat is crawled ``public_only``: every request, redirects
included, has to go to a host that resolves only to public addresses (see
:func:`non_public_reason`). Olisar runs inside someone's network, and without that a
member could have it read a router's admin page or a cloud metadata endpoint into the
knowledge base and then ask about it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup

log = logging.getLogger("olisar.knowledge.crawler")

USER_AGENT = "OlisarBot/1.0 (Discord community knowledge crawler)"
TIMEOUT = 15.0
DELAY_SECONDS = 0.5


class NonPublicHost(ValueError):
    """A ``public_only`` crawl was pointed at a host that isn't on the public internet."""


async def _resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])  # drop an IPv6 zone
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global rules out loopback, private, link-local, shared (CGNAT), reserved and
    # unspecified ranges; multicast isn't a web server either.
    return ip.is_global and not ip.is_multicast


async def non_public_reason(url: str) -> str:
    """Why ``url`` mustn't be fetched for a ``public_only`` source, or "" when its host
    resolves only to public addresses. The addresses are what's checked, not the name: a
    public-looking name can point anywhere, and one private address among several is
    enough to refuse, since there's no telling which one the connection would use."""
    host = urlparse(url).hostname
    if not host:
        return "it has no host"
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await _resolve(host)
        except (OSError, UnicodeError):
            return f"{host} doesn't resolve"
    for address in addresses:
        try:
            public = _is_public(address)
        except ValueError:
            public = False
        if not public:
            return f"{host} points at a private or local address"
    return "" if addresses else f"{host} doesn't resolve"


async def _refuse_non_public(request: httpx.Request) -> None:
    """httpx request hook for a ``public_only`` crawl. Runs for every request the client
    makes, redirects included, so a public page can't bounce the crawler onto the LAN."""
    reason = await non_public_reason(str(request.url))
    if reason:
        raise NonPublicHost(reason)


@dataclass
class Page:
    url: str
    title: str | None
    text: str


async def _load_robots(client: httpx.AsyncClient, start_url: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        resp = await client.get(urljoin(start_url, "/robots.txt"))
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
    hooks = {}
    if public_only:
        # Checked up front as well, so a refused start page is the source's error rather
        # than an empty read.
        reason = await non_public_reason(start_url)
        if reason:
            raise NonPublicHost(f"not read: {reason}")
        hooks = {"request": [_refuse_non_public]}

    pages: list[Page] = []
    seen: set[str] = set()
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(
        headers=headers, timeout=TIMEOUT, follow_redirects=True, event_hooks=hooks
    ) as client:
        robots = await _load_robots(client, start_url)
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
                resp = await client.get(url)
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
