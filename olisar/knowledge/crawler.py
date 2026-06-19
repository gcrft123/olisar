"""Polite website crawler for the knowledge base.

Same-domain breadth-first crawl with a depth and page cap, honoring robots.txt,
sending a clear User-Agent, and pausing between requests. Main content is
extracted with trafilatura (strips nav/boilerplate); links come from BeautifulSoup.
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

log = logging.getLogger("olisar.knowledge.crawler")

USER_AGENT = "OlisarBot/1.0 (Discord community knowledge crawler)"
TIMEOUT = 15.0
DELAY_SECONDS = 0.5


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


async def crawl(start_url: str, *, max_depth: int = 1, max_pages: int = 25) -> list[Page]:
    root_netloc = urlparse(start_url).netloc
    if not root_netloc:
        raise ValueError(f"invalid URL: {start_url}")

    pages: list[Page] = []
    seen: set[str] = set()
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(
        headers=headers, timeout=TIMEOUT, follow_redirects=True
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


async def fetch_page(url: str) -> Page | None:
    """Fetch and extract a single page (KB source type 'url')."""
    pages = await crawl(url, max_depth=0, max_pages=1)
    return pages[0] if pages else None
