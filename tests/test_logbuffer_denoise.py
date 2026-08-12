"""The dashboard's log buffer keeps events and drops heartbeats.

Run:  uv run python -m unittest tests.test_logbuffer_denoise -v

The buffer used to be all heartbeat: the app's API polling and the once-a-minute SSH to
the VM filled all 4000 lines in well under two hours, so a morning incident had already
scrolled out by the time anyone looked. Failures still have to survive.
"""

from __future__ import annotations

import logging
import unittest

from olisar import logbuffer


def _record(name: str, msg: str, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


class DenoiseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.f = logbuffer._Denoise()

    def keeps(self, rec: logging.LogRecord) -> bool:
        return self.f.filter(rec)

    # ── the noise that crowded the buffer out ────────────────────────────────
    def test_successful_access_polling_is_dropped(self):
        for path in ("/api/health", "/api/bot/status", "/api/tunnel/status", "/api/settings/desktop"):
            with self.subTest(path=path):
                line = f'127.0.0.1:54610 - "GET {path} HTTP/1.1" 200'
                self.assertFalse(self.keeps(_record("uvicorn.access", line)))

    def test_redirects_are_dropped_too(self):
        line = '127.0.0.1:54610 - "GET /api/health HTTP/1.1" 304'
        self.assertFalse(self.keeps(_record("uvicorn.access", line)))

    def test_routine_ssh_chatter_is_dropped(self):
        for msg in (
            "Opening SSH connection to 147.224.196.6, port 22",
            "[conn=2341] Auth for user ubuntu succeeded",
            "[conn=2341] Connection closed",
        ):
            with self.subTest(msg=msg):
                self.assertFalse(self.keeps(_record("asyncssh", msg)))
                self.assertFalse(self.keeps(_record("asyncssh.connection", msg)))

    # ── what must still get through ──────────────────────────────────────────
    def test_failed_requests_are_kept(self):
        for code in ("400", "401", "404", "500", "502"):
            with self.subTest(code=code):
                line = f'127.0.0.1:54610 - "POST /api/server/update HTTP/1.1" {code}'
                self.assertTrue(self.keeps(_record("uvicorn.access", line)))

    def test_ssh_problems_are_kept(self):
        rec = _record("asyncssh", "Auth failed for user ubuntu", logging.WARNING)
        self.assertTrue(self.keeps(rec))
        self.assertTrue(self.keeps(_record("asyncssh", "connection lost", logging.ERROR)))

    def test_access_lines_above_info_are_kept(self):
        rec = _record("uvicorn.access", "something unusual", logging.WARNING)
        self.assertTrue(self.keeps(rec))

    def test_unparseable_access_line_is_kept(self):
        """A format change must not silently swallow the access log."""
        self.assertTrue(self.keeps(_record("uvicorn.access", "GET /api/health -> ok")))

    def test_bot_activity_is_untouched(self):
        for name in ("olisar.conversation", "olisar.tools", "olisar.pipeline", "httpx", "uvicorn.error"):
            with self.subTest(name=name):
                self.assertTrue(self.keeps(_record(name, "trigger=dm from someone")))

    def test_a_logger_merely_starting_with_asyncssh_is_not_swept_up(self):
        """Name matching is per-component, so `asyncsshfoo` is a different logger."""
        self.assertTrue(self.keeps(_record("asyncsshfoo", "hello")))


class RingHandlerTests(unittest.TestCase):
    def test_handler_applies_the_filter_and_keeps_events(self):
        logbuffer._BUFFER.clear()
        handler = logbuffer.RingHandler()
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        handler.addFilter(logbuffer._Denoise())

        noise = _record("uvicorn.access", '127.0.0.1:1 - "GET /api/health HTTP/1.1" 200')
        event = _record("olisar.tools", "tool call: sc_ship_lookup(name='Starlancer TAC')")
        for rec in (noise, event):
            if handler.filter(rec):
                handler.emit(rec)

        lines = logbuffer.tail(50)
        self.assertEqual(len(lines), 1)
        self.assertIn("sc_ship_lookup", lines[0])
        logbuffer._BUFFER.clear()


if __name__ == "__main__":
    unittest.main()
