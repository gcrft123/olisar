"""Apply the bot's profile bio — i.e. its public "About Me".

A bot's visible About Me is sourced from its **Application Description**, which a bot
can set on itself with ``PATCH /applications/@me`` using its own token. It is a single
**bot-wide** property (not per-guild), so Olisar drives it from the home/target guild's
persona. Called on startup (``on_ready``) and whenever that persona is saved from the
dashboard, so the live profile tracks the configured bio without a manual copy-paste.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("olisar.bio")

# Discord caps the application description at 400 characters.
BIO_MAX = 400
_ENDPOINT = "https://discord.com/api/v10/applications/@me"


async def apply_bot_bio(token: str, description: str) -> bool:
    """Set the bot application's description (its profile About Me). Returns True on
    success. Best-effort: any failure is logged and swallowed so it never blocks a
    save or startup. ``description`` is trimmed to Discord's 400-char limit; an empty
    string clears the bio (so emptying the field in the dashboard removes it)."""
    if not token:
        return False
    desc = (description or "").strip()[:BIO_MAX]
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(_ENDPOINT, headers=headers, json={"description": desc}) as resp:
                if resp.status >= 400:
                    body = (await resp.text())[:300]
                    log.warning("bot bio update failed (HTTP %s): %s", resp.status, body)
                    return False
    except Exception:
        log.exception("bot bio update errored")
        return False
    log.info("applied bot profile bio (%d chars)", len(desc))
    return True
