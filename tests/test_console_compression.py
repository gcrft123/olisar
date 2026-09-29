"""The console's scripts and styles go out compressed to a browser that accepts it.

Run:  uv run python -m unittest tests.test_console_compression -v

Nothing was compressed, so a remote admin or portal member downloaded the 1.1 MB main script
instead of about 300 KB, and the extension editor's 9.8 MB instead of about 2 MB. The web build
now writes a Brotli and a gzip copy beside each text file (web/scripts/compress.mjs), and
``ConsoleFiles`` sends the best one the browser accepts, or gzips a file with no copy once and
keeps it. JSON from the API stays uncompressed, so BREACH has nothing to read secrets through.
"""

from __future__ import annotations

import gzip
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi import FastAPI

from olisar.runtime import console_files
from olisar.runtime.console_files import ConsoleFiles

SCRIPT = ("export function f%d(a,b){return a+b}\n" * 3000) % tuple(range(3000))
ALL = "gzip, deflate, br, zstd"


class CompressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dist = Path(tmp.name)
        assets = self.dist / "assets"
        assets.mkdir()
        (self.dist / "index.html").write_text("<!doctype html><div id=root></div>")
        (assets / "index-abc.js").write_text(SCRIPT)
        (assets / "index-abc.js.br").write_bytes(b"brotli copy from the build")
        (assets / "plain-def.js").write_text(SCRIPT)  # a build that wrote no copies
        (assets / "tiny-123.js").write_text("export const x = 1")
        (assets / "logo-456.png").write_bytes(b"\x89PNG" + b"\0" * 4000)
        console_files._gzipped.clear()
        self.addCleanup(console_files._gzipped.clear)
        app = FastAPI()
        app.mount("/", ConsoleFiles(directory=str(self.dist), html=True), name="spa")
        self.app = app

    async def get(self, path: str, **headers) -> tuple[httpx.Response, bytes]:
        """The response and its body exactly as sent (not decoded)."""
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            async with c.stream("GET", path, headers=headers) as r:
                raw = b"".join([chunk async for chunk in r.aiter_raw()])
        return r, raw

    async def test_the_builds_brotli_copy_is_sent_as_it_is(self) -> None:
        r, raw = await self.get("/assets/index-abc.js", **{"accept-encoding": ALL})
        self.assertEqual(r.headers["content-encoding"], "br")
        self.assertEqual(raw, b"brotli copy from the build")
        self.assertIn("javascript", r.headers["content-type"])
        self.assertEqual(r.headers["vary"], "Accept-Encoding")
        self.assertIn("immutable", r.headers["cache-control"])

    async def test_without_a_copy_it_is_gzipped_once_and_kept(self) -> None:
        with mock.patch.object(console_files, "_gzip_file", wraps=console_files._gzip_file) as gz:
            for _ in range(3):
                r, raw = await self.get("/assets/plain-def.js", **{"accept-encoding": ALL})
                self.assertEqual(r.headers["content-encoding"], "gzip")
                self.assertEqual(gzip.decompress(raw).decode(), SCRIPT)
                self.assertEqual(r.headers["vary"], "Accept-Encoding")
        self.assertEqual(gz.call_count, 1)

    async def test_a_changed_file_is_gzipped_again(self) -> None:
        await self.get("/assets/plain-def.js", **{"accept-encoding": "gzip"})
        path = self.dist / "assets" / "plain-def.js"
        path.write_text(SCRIPT + "export const y = 2\n")
        _, raw = await self.get("/assets/plain-def.js", **{"accept-encoding": "gzip"})
        self.assertTrue(gzip.decompress(raw).decode().endswith("export const y = 2\n"))

    async def test_a_copy_older_than_its_file_is_ignored(self) -> None:
        old = (self.dist / "assets" / "index-abc.js").stat().st_mtime - 60
        os.utime(self.dist / "assets" / "index-abc.js.br", (old, old))
        r, raw = await self.get("/assets/index-abc.js", **{"accept-encoding": ALL})
        self.assertEqual(r.headers["content-encoding"], "gzip")
        self.assertEqual(gzip.decompress(raw).decode(), SCRIPT)

    async def test_nothing_is_compressed_for_a_browser_that_does_not_ask(self) -> None:
        for accept in ("", "identity", "gzip;q=0, br;q=0"):
            r, raw = await self.get("/assets/index-abc.js", **{"accept-encoding": accept})
            self.assertNotIn("content-encoding", r.headers, accept)
            self.assertEqual(raw.decode(), SCRIPT)
            self.assertEqual(r.headers["vary"], "Accept-Encoding")

    async def test_ranges_small_files_and_images_go_out_as_they_are(self) -> None:
        r, raw = await self.get("/assets/index-abc.js", **{"accept-encoding": ALL,
                                                           "range": "bytes=0-5"})
        self.assertEqual(r.status_code, 206)
        self.assertNotIn("content-encoding", r.headers)
        self.assertEqual(raw.decode(), SCRIPT[:6])
        for path in ("/assets/tiny-123.js", "/assets/logo-456.png"):
            r, _ = await self.get(path, **{"accept-encoding": ALL})
            self.assertNotIn("content-encoding", r.headers, path)

    async def test_a_browser_holding_the_compressed_copy_gets_a_304(self) -> None:
        for path in ("/assets/index-abc.js", "/assets/plain-def.js"):
            r, _ = await self.get(path, **{"accept-encoding": ALL})
            again, _ = await self.get(path, **{"accept-encoding": ALL,
                                               "if-none-match": r.headers["etag"]})
            self.assertEqual(again.status_code, 304, path)
            self.assertEqual(again.headers["vary"], "Accept-Encoding")


class ApiStaysUncompressedTests(unittest.TestCase):
    def test_json_from_the_api_is_not_compressed(self) -> None:
        import api.main as main
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "index.html").write_text("<!doctype html>")
            with mock.patch.object(main, "web_dist_dir", return_value=dist):
                client = TestClient(main.create_app(), base_url="http://127.0.0.1:8000")
            r = client.get("/api/health", headers={"accept-encoding": ALL})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("content-encoding", r.headers)


if __name__ == "__main__":
    unittest.main()
