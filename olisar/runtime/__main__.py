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


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)  # quiet the gateway chatter


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
