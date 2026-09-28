"""Coverage for the What's new cards and the check the release workflow runs on them.

Run:  uv run python -m unittest tests.test_whats_new -v

A stable release can't be tagged without its card (scripts/whats_new.py check, in the
release workflow's version-check job), so the check has to pass a good card, stop a missing
or broken one with a reason, and leave betas alone. The cards already in web/src/whats-new/
are checked here too, so a broken one fails before anyone tags on it.
"""

from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import whats_new as wn


def riff(chunk: bytes, payload: bytes) -> bytes:
    body = b"WEBP" + chunk + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def card(**over) -> dict:
    return {"alt": "v2.1: a poster", "points": ["One thing.", "Another."], **over}


class WebpSizeTests(unittest.TestCase):
    def test_the_shipped_banner(self) -> None:
        with (wn.CARDS / "2.0.webp").open("rb") as fh:
            self.assertEqual(wn.webp_size(fh.read(30)), (1280, 720))

    def test_lossless(self) -> None:
        bits = (1280 - 1) | ((720 - 1) << 14)
        self.assertEqual(wn.webp_size(riff(b"VP8L", b"\x2f" + struct.pack("<I", bits) + b"\0" * 8)), (1280, 720))

    def test_extended(self) -> None:
        payload = b"\0\0\0\0" + (1279).to_bytes(3, "little") + (719).to_bytes(3, "little")
        self.assertEqual(wn.webp_size(riff(b"VP8X", payload)), (1280, 720))

    def test_not_a_webp(self) -> None:
        self.assertIsNone(wn.webp_size(b"\x89PNG\r\n\x1a\n" + b"\0" * 22))
        self.assertIsNone(wn.webp_size(b"RIFF"))


class CardRuleTests(unittest.TestCase):
    def test_a_good_card(self) -> None:
        self.assertEqual(wn.card_problems(card()), [])
        self.assertEqual(wn.card_problems(card(points=["One.", "Two.", "Three."])), [])

    def test_two_or_three_points(self) -> None:
        self.assertIn("has 1 point; the card takes 2 or 3", wn.card_problems(card(points=["One."])))
        self.assertIn("has 4 points; the card takes 2 or 3", wn.card_problems(card(points=["a", "b", "c", "d"])))

    def test_a_point_too_long_for_the_card(self) -> None:
        [issue] = wn.card_problems(card(points=["x" * 81, "Short."]))
        self.assertIn("81 characters", issue)

    def test_alt_text_is_required(self) -> None:
        self.assertTrue(wn.card_problems(card(alt="  ")))
        self.assertTrue(wn.card_problems({"points": ["a", "b"]}))

    def test_a_misspelled_key_is_caught(self) -> None:
        [issue] = wn.card_problems(card(point="typo"))
        self.assertIn("point", issue)


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patch = mock.patch.object(wn, "CARDS", self.dir)
        patch.start()
        self.addCleanup(patch.stop)

    def run_check(self, tag: str) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            wn.check(tag)
        return out.getvalue()

    def write(self, version: str, data: dict, size: tuple[int, int] = (1280, 720)) -> None:
        (self.dir / f"{version}.json").write_text(json.dumps(data), encoding="utf-8")
        from PIL import Image
        Image.new("RGB", size).save(self.dir / f"{version}.webp", "WEBP")

    def test_a_stable_tag_without_a_card_stops_the_release(self) -> None:
        with self.assertRaises(SystemExit) as stop, redirect_stdout(io.StringIO()):
            wn.check("v2.1")
        self.assertIn("uv run scripts/whats_new.py add 2.1", str(stop.exception.code))

    def test_a_stable_tag_with_its_card_passes(self) -> None:
        self.write("2.1", card())
        self.assertIn("✓ 2.1", self.run_check("v2.1"))

    def test_a_beta_needs_no_card(self) -> None:
        self.assertIn("is a beta", self.run_check("v2.1.beta-1"))

    def test_a_banner_the_wrong_size_is_caught(self) -> None:
        self.write("2.1", card(), size=(1920, 1080))
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()) as out:
            wn.check("v2.1")
        self.assertIn("1920×1080", out.getvalue())

    def test_a_card_without_its_banner_is_caught(self) -> None:
        (self.dir / "2.1.json").write_text(json.dumps(card()), encoding="utf-8")
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            wn.check("")


class AddTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patch = mock.patch.object(wn, "CARDS", self.dir / "cards")
        patch.start()
        self.addCleanup(patch.stop)
        from PIL import Image
        self.poster = self.dir / "poster.png"
        Image.new("RGB", (3840, 2160), (2, 2, 3)).save(self.poster)

    def add(self, version: str, **kw) -> None:
        args = Namespace(version=version, poster=None, alt=None, point=None)
        vars(args).update(kw)
        with redirect_stdout(io.StringIO()):
            wn.add(args)

    def test_a_poster_becomes_a_card_the_release_accepts(self) -> None:
        self.add("2.1", poster=str(self.poster), alt="A poster", point=["One.", "Two."])
        self.assertEqual(wn.problems("2.1"), [])
        with (wn.CARDS / "2.1.webp").open("rb") as fh:
            self.assertEqual(wn.webp_size(fh.read(30)), (1280, 720))

    def test_points_alone_keep_the_banner_and_alt(self) -> None:
        self.add("2.1", poster=str(self.poster), alt="A poster", point=["One.", "Two."])
        self.add("v2.1", point=["New one.", "New two.", "New three."])
        saved = json.loads((wn.CARDS / "2.1.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, {"alt": "A poster", "points": ["New one.", "New two.", "New three."]})
        self.assertTrue((wn.CARDS / "2.1.webp").exists())

    def test_a_beta_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.add("2.1.beta-1", poster=str(self.poster), alt="A", point=["a", "b"])

    def test_a_poster_that_isnt_16_9_is_refused(self) -> None:
        from PIL import Image
        square = self.dir / "square.png"
        Image.new("RGB", (2000, 2000)).save(square)
        with self.assertRaises(SystemExit):
            self.add("2.1", poster=str(square), alt="A", point=["a", "b"])
        self.assertFalse((wn.CARDS / "2.1.json").exists())


class ShippedCardsTests(unittest.TestCase):
    def test_every_card_in_the_repo_passes(self) -> None:
        with redirect_stdout(io.StringIO()):
            wn.check("")  # exits on a broken card


if __name__ == "__main__":
    unittest.main()
