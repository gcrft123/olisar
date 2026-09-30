"""A posted GIF can't make the bot decode a huge canvas, or decode anything on its event loop.

Run:  uv run python -m unittest tests.test_gif_decode_limits -v

A GIF declares its canvas size in its first bytes, whatever its length. A 35-byte file can
say 12000x12000, and flattening its first frame for vision converted that whole canvas to
RGBA: about 0.8 s and 800 MB, on the event loop that also runs Discord and the API, for any
image posted in a channel the bot indexes. The canvas is now checked before anything is
decoded, and the decode itself runs in a worker thread.
"""

from __future__ import annotations

import io
import struct
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from PIL import Image

from bot import content


def _gif(width: int, height: int, frame_w: int = 1, frame_h: int = 1) -> bytes:
    """A tiny GIF with a ``width`` x ``height`` canvas and one ``frame_w`` x ``frame_h`` frame."""
    head = b"GIF89a" + struct.pack("<HH", width, height) + bytes([0x80, 0, 0])
    head += b"\x00\x00\x00\xff\xff\xff"
    frame = b"\x2c" + struct.pack("<HHHH", 0, 0, frame_w, frame_h) + b"\x00"
    return head + frame + b"\x02\x02\x44\x01\x00" + b"\x3b"


def _real_gif(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="GIF")
    return buf.getvalue()


class CanvasSize(unittest.TestCase):
    def test_a_huge_declared_canvas_is_refused_before_decoding(self):
        with patch.object(Image.Image, "convert", side_effect=AssertionError("decoded")):
            self.assertIsNone(content._gif_first_frame(_gif(9000, 9000)))

    def test_a_frame_larger_than_its_canvas_is_refused_too(self):
        self.assertIsNone(content._gif_first_frame(_gif(10, 10, 6000, 6000)))

    def test_an_ordinary_gif_still_becomes_a_png(self):
        out = content._gif_first_frame(_real_gif(320, 240))
        self.assertIsNotNone(out)
        png, note = out
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertIn("GIF", note)

    def test_the_limit_is_the_pixel_count(self):
        side = int(content.MAX_GIF_PIXELS ** 0.5) + 1
        self.assertIsNone(content._gif_first_frame(_gif(side, side)))


class OffTheEventLoop(unittest.IsolatedAsyncioTestCase):
    async def test_an_attached_gif_is_decoded_in_a_worker_thread(self):
        loop_thread = threading.get_ident()
        seen: list[int] = []
        real = content._gif_first_frame

        def spy(data):
            seen.append(threading.get_ident())
            return real(data)

        att = NS(content_type="image/gif", size=100, filename="x.gif",
                 read=AsyncMock(return_value=_real_gif(16, 16)))
        message = NS(id=1, attachments=[att], content="", embeds=[])
        with patch.object(content, "_gif_first_frame", spy):
            out = await content.download_images(message)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1], "image/png")
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], loop_thread)

    async def test_a_gif_bomb_attachment_is_skipped(self):
        att = NS(content_type="image/gif", size=35, filename="x.gif",
                 read=AsyncMock(return_value=_gif(9000, 9000)))
        message = NS(id=1, attachments=[att], content="", embeds=[])
        self.assertEqual(await content.download_images(message), [])

    async def test_a_linked_gif_is_decoded_in_a_worker_thread(self):
        loop_thread = threading.get_ident()
        seen: list[int] = []
        real = content._still_from_bytes

        def spy(data, ctype):
            seen.append(threading.get_ident())
            return real(data, ctype)

        gif = _real_gif(16, 16)

        class _Client:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                return NS(content=gif, headers={"content-type": "image/gif"},
                          raise_for_status=lambda: None)

        with patch.object(content, "_still_from_bytes", spy), patch.object(
            content.httpx, "AsyncClient", _Client
        ):
            out = await content._fetch_gif_still("https://media.tenor.com/abc/cat.gif")
        self.assertIsNotNone(out)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], loop_thread)


if __name__ == "__main__":
    unittest.main()
