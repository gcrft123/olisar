"""In-memory ring buffer of recent log lines, so the dashboard can show live logs.

The runtime logs to stdout (and, in dev, a tee'd file), but the packaged app has no
console. We attach a bounded handler to the root logger that keeps the last few
thousand formatted lines in memory; the settings page reads them over the API. It's
capped, so memory stays flat, and it never touches disk.

Heartbeats are dropped on the way in — see ``_Denoise``. Only this handler filters;
stdout still gets every line, so ``docker compose logs`` (and the dev tee) remain the
complete record when the buffer isn't enough.
"""

from __future__ import annotations

import logging
import re
from collections import deque

# A few thousand lines is plenty for "what just happened" without growing unbounded.
# With the heartbeats filtered out this is weeks of a quiet bot rather than ~2 hours.
_BUFFER: deque[str] = deque(maxlen=4000)

# Trailing status code of a uvicorn access line:
#   127.0.0.1:54610 - "GET /api/settings/desktop HTTP/1.1" 200
_ACCESS_STATUS = re.compile(r'"\s+(\d{3})\s*$')


class _Denoise(logging.Filter):
    """Keep polling chatter out of the operator's log view.

    The desktop app polls the API every few seconds and, in server mode, SSHes the VM
    once a minute. Both are logged at INFO, and between them they *were* the buffer: of
    the last 4000 lines, 2185 were ``uvicorn.access`` and 1815 ``asyncssh``, leaving room
    for about 100 minutes and not one bot event. An incident from that morning had already
    scrolled out before anyone could look at it.

    So heartbeats are dropped and problems are kept: an access line survives if it carries
    a 4xx/5xx, and SSH survives at WARNING or above. Everything else is untouched.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        if name == "uvicorn.access":
            if record.levelno > logging.INFO:
                return True
            m = _ACCESS_STATUS.search(record.getMessage())
            # Unparseable means the format changed — keep it rather than silently swallow.
            return m is None or int(m.group(1)) >= 400
        if name.split(".", 1)[0] == "asyncssh":
            return record.levelno >= logging.WARNING
        return True


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
    handler.addFilter(_Denoise())  # this handler only — stdout keeps everything
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
