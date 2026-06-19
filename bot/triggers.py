"""Detect whether (and how) a message is addressed to Olisar.

The four engagement paths from the plan: @mention, reply-to-Olisar, addressed by
name ("olisar, ..."), and DMs. The slash-command path is handled separately by
the /ask command.
"""

from __future__ import annotations

import re

import discord


def _addressed_by_name(content: str, names: list[str]) -> bool:
    """Whether the message addresses Olisar by name.

    Matches a configured name as a standalone word ANYWHERE in the message, so
    members address Olisar however feels natural — "olisar, help", "hey olisar",
    and "does olisar know?" all count. Word boundaries keep "polarisar" from
    matching, and the check is case-insensitive.
    """
    text = content.lower()
    for name in names:
        if re.search(rf"\b{re.escape(name.lower())}\b", text):
            return True
    return False


def detect_trigger(
    message: discord.Message,
    bot_user: discord.ClientUser,
    name_triggers: list[str],
    is_dm: bool,
) -> str | None:
    """Return the trigger type ('dm'|'mention'|'reply'|'name') or None."""
    if is_dm:
        return "dm"

    if bot_user in message.mentions:
        return "mention"

    ref = message.reference
    if ref is not None:
        resolved = ref.resolved
        if isinstance(resolved, discord.Message) and resolved.author.id == bot_user.id:
            return "reply"
        cached = getattr(ref, "cached_message", None)
        if cached is not None and cached.author.id == bot_user.id:
            return "reply"

    if message.content and _addressed_by_name(message.content, name_triggers):
        return "name"

    return None
