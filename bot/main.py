"""Bot entrypoint.

Run with:  uv run python -m bot.main
"""

from __future__ import annotations

import asyncio
import logging

from bot.client import OlisarBot
from olisar.config import settings


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("discord").setLevel(logging.WARNING)  # quiet the gateway chatter


async def _run() -> None:
    bot = OlisarBot()
    async with bot:
        await bot.start(settings.discord_token)


def main() -> None:
    _setup_logging()
    if not settings.discord_token:
        raise SystemExit(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
