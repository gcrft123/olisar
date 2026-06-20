"""On-join welcome messages — a toggleable extension.

When on, a new member triggers a short welcome generated from the server's persona
*plus* a custom prompt you write (e.g. "roast {user} on their username"), posted to a
channel you pick. The Discord event is handled in bot/cogs/welcome.py; this module
just puts the feature in the catalog. Per-guild config lives in
``ExtensionState.settings``: ``{"channel_id": "...", "prompt": "..."}``.
"""

from __future__ import annotations

from olisar.extensions.base import Extension, register

WELCOME_KEY = "welcome"


def register_welcome() -> None:
    register(
        Extension(
            key=WELCOME_KEY,
            name="Welcome messages",
            description=(
                "Greet new members in a channel you pick — in Olisar's voice, shaped "
                "by a custom prompt you write (use {user} for the new member). Set the "
                "channel and prompt on the Welcome panel."
            ),
            category="Automation",
            default_enabled=False,
        )
    )
