"""Knowledge-base parsing runs in a worker thread, not on the bot's event loop.

Run:  uv run python -m unittest tests.test_kb_parsing_off_loop -v

The ingest worker shares the event loop that serves Discord and the API. pypdf and
python-docx (uploaded documents) and trafilatura and BeautifulSoup (crawled pages) ran right
on it, so parsing a big PDF froze the bot and the console for the whole parse, most of a
second for a 1.3 MB manual. They run in a worker thread now. The documents here are built in
a temp directory, which also checks that the installed pypdf and python-docx still read
them, and pages come from httpx's MockTransport; nothing touches the network.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import docx
import httpx

from olisar import netguard
from olisar.db.models import KBSourceType
from olisar.knowledge import crawler, extract, ingest

FACT = "The hangar keys are kept in the blue locker next to the cargo lift."


def _pdf(text: str) -> bytes:
    """A one-page PDF that says ``text`` in Helvetica."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref,
    )
    return bytes(out)


class _Threads:
    """Wraps a callable to note which thread each call ran on."""

    def __init__(self, fn):
        self.fn = fn
        self.threads: list[int] = []

    def __call__(self, *args, **kwargs):
        self.threads.append(threading.get_ident())
        return self.fn(*args, **kwargs)


class Documents(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    async def gather(self, path: Path) -> tuple[list[dict], list[int]]:
        spy = _Threads(extract.extract_document)
        with patch.object(ingest, "extract_document", spy):
            records = await ingest._gather(KBSourceType.doc, str(path), 0, 1)
        return records, spy.threads

    async def test_a_pdf_is_read_off_the_event_loop(self):
        path = self.dir / "handbook.pdf"
        path.write_bytes(_pdf(FACT))
        records, threads = await self.gather(path)
        self.assertIn("blue locker", records[0]["text"])
        self.assertEqual(records[0]["title"], "handbook.pdf")
        self.assertNotIn(threading.get_ident(), threads)
        self.assertEqual(len(threads), 1)

    async def test_a_docx_is_read_off_the_event_loop(self):
        path = self.dir / "handbook.docx"
        document = docx.Document()
        document.add_paragraph(FACT)
        document.save(path)
        records, threads = await self.gather(path)
        self.assertIn("blue locker", records[0]["text"])
        self.assertNotIn(threading.get_ident(), threads)
        self.assertEqual(len(threads), 1)


class Pages(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            other = "/two" if request.url.path == "/" else "/"
            html = (f"<html><title>{request.url.path}</title><body><p>{FACT * 5}</p>"
                    f'<a href="{other}">next</a></body></html>')
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

        transport = httpx.MockTransport(handle)
        self.extract = _Threads(crawler.trafilatura.extract)
        self.soup = _Threads(crawler.BeautifulSoup)
        for p in (
            patch.object(crawler, "_resolve", new=AsyncMock(return_value=["93.184.216.34"])),
            patch.object(netguard, "resolve_public", new=AsyncMock(return_value="93.184.216.34")),
            patch.object(netguard, "PinnedTransport", lambda host, address: transport),
            patch.object(crawler, "DELAY_SECONDS", 0),
            patch.object(crawler.trafilatura, "extract", self.extract),
            patch.object(crawler, "BeautifulSoup", self.soup),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def test_pages_are_parsed_off_the_event_loop(self):
        pages = await crawler.crawl("http://public.test/", max_depth=1, public_only=True)
        self.assertEqual([p.title for p in pages], ["/", "/two"])  # the link was followed
        self.assertIn("blue locker", pages[0].text)
        self.assertEqual(len(self.extract.threads), 2)
        self.assertTrue(self.soup.threads)
        self.assertNotIn(threading.get_ident(), self.extract.threads + self.soup.threads)


if __name__ == "__main__":
    unittest.main()
