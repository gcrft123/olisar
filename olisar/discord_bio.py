"""Apply the bot's profile bio — i.e. its public "About Me".

A bot's visible About Me is sourced from its **Application Description**, which a bot
can set on itself with ``PATCH /applications/@me`` using its own token. It is a single
**bot-wide** property (not per-guild), so Olisar drives it from the home/target guild's
persona. Called on startup (``on_ready``) and whenever that persona is saved from the
dashboard, so the live profile tracks the configured bio without a manual copy-paste.

A fixed attribution line (:data:`WATERMARK`) is always appended to the operator's bio,
separated by a blank line. It is added here at the Discord boundary only — never stored
in the DB or shown in the dashboard editor — so the operator's saved bio stays clean and
the line can't stack up on re-save. The attribution is included even when the operator's
bio is blank, so every install carries it.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("olisar.bio")

# Discord caps the application description (the public "About Me") at 400 characters.
BIO_MAX = 400

# The forced "Powered by" attribution line. Appended to every bot's About Me below the
# operator's own text. (Empty string disables it.)
WATERMARK = "Powered by Olisar AI — try it free on your server: https://gcrft.s.gy/olisar"

# Blank line between the operator's bio and the attribution line.
_SEP = "\n\n"

# How much of the 400-char budget the operator's own text may use; the rest is reserved
# for the separator + watermark so the attribution always survives the cap.
USER_BIO_MAX = 300

_ENDPOINT = "https://discord.com/api/v10/applications/@me"


def compose_bio(user_text: str) -> str:
    """Combine the operator's bio with the forced :data:`WATERMARK` attribution line.

    The operator's text is capped at :data:`USER_BIO_MAX` and the watermark is appended
    after a blank line. When the operator's text is empty the bio is just the watermark,
    so the attribution is present even with a blank bio. Any watermark already trailing
    the input is stripped first so it can't stack on re-application, and the result is
    clamped to Discord's :data:`BIO_MAX`.
    """
    clean = (user_text or "").strip()
    if WATERMARK and clean.endswith(WATERMARK):
        clean = clean[: -len(WATERMARK)].rstrip()
    clean = clean[:USER_BIO_MAX]
    if not WATERMARK:
        return clean[:BIO_MAX]
    composed = f"{clean}{_SEP}{WATERMARK}" if clean else WATERMARK
    return composed[:BIO_MAX]


async def apply_bot_bio(token: str, description: str) -> bool:
    """Set the bot application's description (its public About Me) to the operator's bio
    plus the forced attribution line. Returns True on success. Best-effort: any failure
    is logged and swallowed so it never blocks a save or startup. The attribution is
    always included, even when ``description`` is blank."""
    if not token:
        return False
    desc = compose_bio(description)
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
    log.info("applied bot profile bio (%d chars, incl. attribution)", len(desc))
    return True
