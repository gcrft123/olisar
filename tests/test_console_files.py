"""The console's cache headers: index.html is always revalidated, hashed assets are kept.

Without them, Chromium served a desktop window the pre-update index.html for hours after an
update (see olisar/runtime/console_files.py).
"""

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from olisar.runtime.console_files import ConsoleFiles


class ConsoleFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        (root / "assets").mkdir()
        (root / "index.html").write_text('<script src="/assets/index-abc123.js"></script>')
        (root / "assets" / "index-abc123.js").write_text("console.log(1)")
        (root / "logo.png").write_bytes(b"\x89PNG")
        app = FastAPI()
        app.mount("/", ConsoleFiles(directory=str(root), html=True), name="spa")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_index_is_revalidated_every_time(self) -> None:
        for url in ("/", "/index.html"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["cache-control"], "no-cache", url)

    def test_a_revalidation_that_finds_no_change_says_so_cheaply(self) -> None:
        etag = self.client.get("/").headers["etag"]
        r = self.client.get("/", headers={"If-None-Match": etag})
        self.assertEqual(r.status_code, 304)
        self.assertEqual(r.headers["cache-control"], "no-cache")

    def test_hashed_assets_are_kept_for_good(self) -> None:
        r = self.client.get("/assets/index-abc123.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r.headers["cache-control"])

    def test_other_unhashed_files_are_revalidated_too(self) -> None:
        self.assertEqual(self.client.get("/logo.png").headers["cache-control"], "no-cache")


if __name__ == "__main__":
    unittest.main()
