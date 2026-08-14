"""Customizable slash-command reply text.

Each command renders its confirmation from a template that admins can override
(stored in ``guild_config.command_messages``; falls back to the defaults here).
Templates use ``{placeholder}`` fields — the available placeholders per key are
listed in ``PLACEHOLDERS`` so the dashboard can show them. Rendering is crash-safe:
unknown placeholders become empty, and a malformed custom template falls back to
the default.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import GuildConfig

DEFAULT_COMMAND_MESSAGES: dict[str, str] = {
    "ping": "pong — {latency} ms",
    "watch": "I'll read and remember this channel now.",
    "unwatch": "I'll leave this channel alone.",
    "channel_status": "This channel's mode is **{mode}**.",
    "learn_url": "queued **{url}** — i'll read it shortly.",
    "learn_site": "queued crawl of **{url}** (depth {depth}, up to {max_pages} pages).",
    "learn_doc": "queued **{filename}** — i'll read it shortly.",
    "forget_me": (
        "done — deleted {messages} messages and {facts} remembered facts, "
        "and cleared your profile."
    ),
    "forget_me_optout": "i'll stop recording your messages from now on.",
    "dm_indexing": (
        "DM saving & indexing is now **{state}**. When on, I remember and search our DMs "
        "like a channel; when off, I don't store or index them (use `/forget-me` to also "
        "delete what's already saved)."
    ),
    "proactive": (
        "proactive chiming is now **{state}** (level: **{level}**). i'll only "
        "speak up in channels i can talk in, with cooldowns so i don't flood."
    ),
    # Fixed conversational messages Olisar falls back to (not tied to a slash
    # command). Surfaced here so admins can tune their voice like any other reply.
    "rate_limit": "i'm a bit rate-limited right now — give me a minute and try again?",
    "blank_fallback": "…my mind just went blank there. mind rephrasing?",
    "access_denied": "sorry — you don't have access to me here.",
    "privacy": (
        "**How Olisar handles your data**\n"
        "In channels an admin has enabled, I store messages so I can follow a conversation "
        "and remember the community, and I build a short profile of you from them so I can "
        "chat more naturally. This server's admins have also turned on server-wide message "
        "search, so I keep a searchable index of messages across channels — including text "
        "from embeds and a short description of posted images — to answer questions like "
        "\"what's the server's X account?\". If status & voice awareness is on, I can check "
        "your current Discord status, activity, or voice channel when someone asks, but I "
        "read that live and never store it. I store and index our DMs too, unless you turn "
        "that off. I never share your DMs or private content publicly.\n\n"
        "• `/forget-me` — delete everything I've stored about you, including the search "
        "index and DMs.\n"
        "• `/forget-me stop_remembering:true` — delete it **and** stop recording you.\n"
        "• `/dm-indexing enabled:false` — stop saving and indexing just your DMs.\n"
        "{portal}"
    ),
    # Appended to /privacy only where the operator has opened the member portal. This is the
    # portal's discovery path: nobody finds a web page nothing links to, and the command that
    # exists to explain data handling is the honest place to mention it.
    "privacy_portal": (
        "\nYou can also see and delete all of it yourself at {url} — sign in with Discord."
    ),
}

# Placeholders available to each template (for the dashboard editor's hints).
PLACEHOLDERS: dict[str, list[str]] = {
    "ping": ["latency"],
    "channel_status": ["mode"],
    "learn_url": ["url"],
    "learn_site": ["url", "depth", "max_pages"],
    "learn_doc": ["filename"],
    "forget_me": ["messages", "facts"],
    "dm_indexing": ["state"],
    "proactive": ["state", "level"],
    # Empty unless this server has opened the member portal — an operator editing the
    # privacy text should know the slot exists and that it can render as nothing.
    "privacy": ["portal"],
    "privacy_portal": ["url"],
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # unknown placeholder -> empty
        return ""


def render_message(custom: dict | None, key: str, **kwargs) -> str:
    template = (custom or {}).get(key) or DEFAULT_COMMAND_MESSAGES.get(key, "")
    try:
        return template.format_map(_SafeDict(**kwargs))
    except Exception:
        default = DEFAULT_COMMAND_MESSAGES.get(key, "")
        try:
            return default.format_map(_SafeDict(**kwargs))
        except Exception:
            return default


async def get_command_messages(session: AsyncSession, guild_id: int) -> dict:
    config = await session.get(GuildConfig, guild_id)
    return (config.command_messages if config and config.command_messages else {})
