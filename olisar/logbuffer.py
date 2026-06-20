"""In-memory ring buffer of recent log lines, so the dashboard can show live logs.

The runtime logs to stdout (and, in dev, a tee'd file), but the packaged app has no
console. We attach a bounded handler to the root logger that keeps the last few
thousand formatted lines in memory; the settings page reads them over the API. It's
capped, so memory stays flat, and it never touches disk.
"""

from __future__ import annotations

import logging
from collections import deque

# A few thousand lines is plenty for "what just happened" without growing unbounded.
_BUFFER: deque[str] = deque(maxlen=4000)


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _BUFFER.append(self.format(record))
        except Exception:  # never let logging crash the app
            self.handleError(record)


def install(fmt: str, datefmt: str, level: int = logging.INFO) -> None:
    """Attach the ring handler to the root logger (idempotent)."""
    root = logging.getLogger()
    if any(isinstance(h, RingHandler) for h in root.handlers):
        return
    handler = RingHandler()
    handler.setFormatter(logging.Formatter(fmt, datefmt))
    handler.setLevel(level)
    root.addHandler(handler)


def tail(limit: int = 500, *, contains: str | None = None) -> list[str]:
    """The most recent log lines (oldest first), optionally filtered to lines whose
    text contains ``contains`` (used for the remote-access view)."""
    lines = list(_BUFFER)
    if contains:
        lines = [ln for ln in lines if contains in ln]
    if limit and limit > 0:
        lines = lines[-limit:]
    return lines
