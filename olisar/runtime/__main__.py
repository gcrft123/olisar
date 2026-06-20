"""CLI entry for the unified backend:  python -m olisar.runtime [--host H] [--port N]

Sets the per-user data directory BEFORE importing ``olisar.config`` (an lru_cached
singleton), then runs the API (serving the dashboard) + the bot on one event loop.
This module is the PyInstaller entry point inside the Electron app.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os


_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_LOG_DATEFMT = "%H:%M:%S"


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    logging.getLogger("discord").setLevel(logging.WARNING)  # quiet the gateway chatter
    # Keep the last few thousand lines in memory so the dashboard's Settings → Logs
    # can show them (the packaged app has no console to scroll).
    from olisar import logbuffer

    logbuffer.install(_LOG_FORMAT, _LOG_DATEFMT)


def main() -> None:
    # Must run before anything imports olisar.config / opens the DB.
    from olisar.runtime import paths

    paths.bootstrap_env()
    _setup_logging()

    parser = argparse.ArgumentParser(prog="olisar.runtime")
    parser.add_argument("--host", default=os.environ.get("OLISAR_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("OLISAR_PORT", "8000"))
    )
    args = parser.parse_args()

    from olisar.runtime.server import run

    try:
        asyncio.run(run(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
