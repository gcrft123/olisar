"""The bot's own name, for the console to call it by.

Operators bring their own Discord application, so the bot members talk to is called
whatever they named it there. The console says that name wherever it means the bot, and
"Olisar" only where it means the product.
"""

from __future__ import annotations

from fastapi import Request

from olisar import discord_app


def bot_name(request: Request) -> str:
    """The bot's name as Discord shows it, or "" when it isn't known yet.

    Read off the in-process client once it has logged in; before that (or while the
    operator has it stopped), from the Discord application if one has been fetched. Never
    waits on Discord: the console asks for this before it paints anything."""
    supervisor = getattr(request.app.state, "bot_supervisor", None)
    bot = getattr(supervisor, "bot", None) if supervisor is not None else None
    user = getattr(bot, "user", None) if bot is not None else None
    if user is not None:
        return user.display_name
    app_bot = (discord_app.cached_application() or {}).get("bot") or {}
    return app_bot.get("global_name") or app_bot.get("username") or ""
