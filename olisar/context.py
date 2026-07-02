"""Assemble the conversation context Gemini sees.

Phase 1 is the recent-window slice: the last N messages in the channel, turned
into a role-tagged transcript. Semantic memory recall, channel summaries, user
personas, and knowledge-base chunks layer in here in Phases 2 and 4.
"""

from __future__ import annotations

from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import Message, UserProfile

RECENT_WINDOW = 12  # messages of history to include

# Appended to the system instruction so the model reads the transcript correctly.
CONTEXT_NOTE = (
    "You are in a Discord conversation. Below is recent history as a transcript: "
    "each person's line is prefixed with their display name, and your own past "
    "replies appear with no prefix. Reply naturally as yourself — do not prefix "
    "your reply with a name, and don't restate the transcript.\n\n"
    "If a line is marked as replying to an earlier message "
    "(e.g. `Wumpus (replying to Olisar: \"…\"): …`), treat that quoted message as "
    "background only. Lean on it when the new message clearly depends on it, but if "
    "the new message stands on its own, just answer it — don't force a connection to "
    "the quoted message or steer back to it."
)

REPLY_SNIPPET_MAX = 300  # how much of the replied-to message to quote inline


async def name_map(session: AsyncSession, author_ids: set[int]) -> dict[int, str]:
    """Map author ids -> display names from their profiles (one query)."""
    if not author_ids:
        return {}
    rows = (
        await session.scalars(
            select(UserProfile).where(UserProfile.user_id.in_(author_ids))
        )
    ).all()
    return {p.user_id: (p.display_name or str(p.user_id)) for p in rows}


async def people_directory(
    session: AsyncSession,
    *,
    channel_id: int,
    current_user_id: int,
    current_display_name: str,
) -> str:
    """A small name -> id directory of recent participants, so Olisar can address
    people by id (e.g. to DM them via the send_dm tool)."""
    recent = (
        await session.scalars(
            select(Message.author_id)
            .where(Message.channel_id == channel_id, Message.author_is_bot == False)  # noqa: E712
            .order_by(Message.created_at.desc())
            .limit(40)
        )
    ).all()
    ids = {current_user_id, *recent}
    names = await name_map(session, ids)
    names.setdefault(current_user_id, current_display_name or str(current_user_id))
    entries = ", ".join(f"{names.get(i, str(i))} (id {i})" for i in ids)
    return (
        "People directory (display name -> id) for this conversation: "
        + entries
        + f". You are talking to {names.get(current_user_id, current_display_name)} "
        f"(id {current_user_id})."
    )


def _reply_tag(reply_to: tuple[str, str] | None) -> str:
    """The inline `` (replying to Author: "…")`` marker for a reply, or ''.

    Whitespace is collapsed and the quote is clipped so a long replied-to message
    stays a brief, non-dominating aside rather than crowding out the new question."""
    if not reply_to:
        return ""
    author, text = reply_to
    snippet = " ".join((text or "").split())
    if not snippet:
        return ""
    if len(snippet) > REPLY_SNIPPET_MAX:
        snippet = snippet[: REPLY_SNIPPET_MAX - 1].rstrip() + "…"
    return f' (replying to {author}: "{snippet}")'


def _append(contents: list, role: str, text: str) -> None:
    """Append text, merging into the previous turn if it has the same role
    (Gemini prefers alternating roles)."""
    if contents and contents[-1].role == role:
        contents[-1].parts.append(types.Part(text=text))
    else:
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))


async def build_contents(
    session: AsyncSession,
    *,
    channel_id: int,
    current_message_id: int,
    bot_user_id: int,
    current_display_name: str,
    current_text: str,
    current_images: list[tuple[bytes, str, str]] | None = None,
    reply_to: tuple[str, str] | None = None,
    recent_window: int | None = None,
) -> tuple[list, set[int]]:
    """Return Gemini `contents` (recent history + new message) and the set of
    Discord message ids included, so semantic recall can skip duplicates.

    ``current_images`` (``(data, mime)`` pairs) are attached to the new message's
    turn as inline image parts, so the model literally sees what was posted.

    ``reply_to`` is ``(author, text)`` of the message the new one replies to; it's
    folded into the new turn as a quoted prefix so the model is *aware* of the reply
    target. The judgement of whether it matters is left to the model (CONTEXT_NOTE).

    ``recent_window`` overrides how many recent messages to include (the per-guild
    ``context_message_limit``); ``None`` falls back to the default. Clamped to a sane
    range so a bad config value can't blow up (or empty) the context."""
    window = max(1, min(recent_window or RECENT_WINDOW, 100))
    rows = (
        await session.scalars(
            select(Message)
            .where(
                Message.channel_id == channel_id,
                Message.message_id != current_message_id,
            )
            .order_by(Message.created_at.desc())
            .limit(window)
        )
    ).all()
    rows = list(reversed(rows))

    names = await name_map(session, {m.author_id for m in rows if not m.author_is_bot})

    contents: list = []
    for m in rows:
        if not m.content.strip():
            continue
        if m.author_is_bot or m.author_id == bot_user_id:
            _append(contents, "model", m.content)
        else:
            speaker = names.get(m.author_id, str(m.author_id))
            _append(contents, "user", f"{speaker}: {m.content}")

    _append(contents, "user", f"{current_display_name}{_reply_tag(reply_to)}: {current_text}")
    # Attach the new message's images to that same user turn (it's contents[-1]). A note
    # (e.g. for a GIF flattened to its first frame) rides just after its image so the model
    # knows what it's actually looking at.
    for data, mime, note in current_images or []:
        contents[-1].parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=data)))
        if note:
            contents[-1].parts.append(types.Part(text=f"(The image just above is {note}.)"))

    recent_ids = {m.message_id for m in rows}
    return contents, recent_ids
