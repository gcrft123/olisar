"""Assemble the conversation context Gemini sees.

Phase 1 is the recent-window slice: the last N messages in the channel, turned
into a role-tagged transcript. Semantic memory recall, channel summaries, user
personas, and knowledge-base chunks layer in here in Phases 2 and 4.
"""

from __future__ import annotations

from datetime import datetime, timezone

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
    "the quoted message or steer back to it. Several conversations often run at once in "
    "one channel, so consecutive lines are frequently unrelated: use those markers to "
    "follow the strand you're actually in rather than assuming each line answers the "
    "one above it.\n\n"
    "A line like `— 3 hours later —` is a gap in the conversation, not something anyone "
    "said. Treat what came before it as finished: a channel picking up after a long "
    "silence is a fresh start, not a thread to resume. A line prefixed `Name (bot):` is "
    "another bot in the channel, not a person and not you."
)

REPLY_SNIPPET_MAX = 300  # how much of the replied-to message to quote inline
HISTORY_SNIPPET_MAX = 120  # …and how much for an older line, which matters less
CHANNEL_TOPIC_MAX = 300  # how much of a channel topic to pass through
GAP_SECONDS = 900  # a silence longer than this is marked in the transcript


def channel_note(name: str, topic: str = "") -> str:
    """Tell the model which room it's standing in, or '' when there isn't one (a DM).

    The same member writes very differently in #help than in #off-topic, and Olisar
    couldn't tell them apart: the channel *directory* in the system prompt names every
    channel so it can post to them, but nothing ever said which one the conversation is
    happening in. Without that there's no register to match — every room got the same voice.
    """
    name = (name or "").lstrip("#").strip()
    if not name:
        return ""
    note = f"You're talking in #{name}."
    topic = " ".join((topic or "").split())[:CHANNEL_TOPIC_MAX]
    if topic:
        note += f' The channel topic is: "{topic}".'
    return note + (
        " Pitch your register to the room: help/support/dev channels want clear, complete "
        "answers (code blocks where they fit), while general/off-topic/meme channels want "
        "short, loose and chatty. When in doubt, match how the last few messages are written."
    )


def is_own_message(message: Message) -> bool:
    """Whether a stored row is one of Olisar's own replies.

    A bot row carries the sender's name and Olisar's deliberately doesn't (see
    ``Message.author_name``), so this holds without the gateway's user id — which the
    summarizer, the recall block and the proactivity scan don't have to hand."""
    return bool(message.author_is_bot) and not message.author_name


def speaker_name(message: Message, names: dict[int, str], *, own: str = "Olisar") -> str:
    """Who a stored message is from, for a rendered transcript.

    A nameless bot row is Olisar's — which is what every transcript needs to stop
    labelling the server's other bots as itself. Humans prefer their current profile
    name and fall back to the name they had when they wrote it."""
    if message.author_is_bot:
        return message.author_name or own
    return names.get(message.author_id) or message.author_name or str(message.author_id)


def _aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; every one we store is UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _elapsed(seconds: float) -> str:
    """A rough, human way to say how long a silence lasted."""
    if seconds >= 172800:
        return f"{int(seconds // 86400)} days later"
    if seconds >= 86400:
        return "a day later"
    if seconds >= 7200:
        return f"{int(seconds // 3600)} hours later"
    if seconds >= 3600:
        return "an hour later"
    return f"{max(1, int(seconds // 60))} minutes later"


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


def _reply_tag(reply_to: tuple[str, str] | None, limit: int = REPLY_SNIPPET_MAX) -> str:
    """The inline `` (replying to Author: "…")`` marker for a reply, or ''.

    Whitespace is collapsed and the quote is clipped so a long replied-to message
    stays a brief, non-dominating aside rather than crowding out the new question."""
    if not reply_to:
        return ""
    author, text = reply_to
    snippet = " ".join((text or "").split())
    if not snippet:
        return ""
    if len(snippet) > limit:
        snippet = snippet[: limit - 1].rstrip() + "…"
    return f' (replying to {author}: "{snippet}")'


async def _reply_targets(
    session: AsyncSession, rows: list[Message], names: dict[int, str]
) -> dict[int, tuple[str, str]]:
    """Map ``message_id -> (author, text)`` for everything the given rows reply to.

    Every message already records what it replied to, and nothing ever read it: the
    transcript arrived as a flat list of lines, so two or three interleaved conversations
    looked like one and Olisar answered whichever strand happened to be adjacent. Targets
    inside the window are free; the ones that have scrolled out cost one extra query,
    because a reply pointing at a message you can't see is the case that misleads most.
    """
    known = {m.message_id: m for m in rows}
    wanted = {m.reply_to_message_id for m in rows if m.reply_to_message_id}
    missing = wanted - set(known)
    if missing:
        older = (
            await session.scalars(select(Message).where(Message.message_id.in_(missing)))
        ).all()
        extra_names = await name_map(
            session, {m.author_id for m in older if not m.author_is_bot}
        )
        names = {**extra_names, **names}
        known.update({m.message_id: m for m in older})
    return {
        mid: (speaker_name(msg, names), msg.content)
        for mid, msg in known.items()
        if mid in wanted
    }


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
    range so a bad config value can't blow up (or empty) the context.

    History lines carry the same ``(replying to …)`` marker as the new message, and a
    silence longer than ``GAP_SECONDS`` is marked ``— 3 hours later —``. Both are there
    so the transcript reads as what a channel actually is: several strands at once, with
    holes in it — not one continuous conversation where every line answers the last."""
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
    targets = await _reply_targets(session, rows, names)

    contents: list = []
    previous: datetime | None = None
    for m in rows:
        if not m.content.strip():
            continue
        mine = m.author_id == bot_user_id or is_own_message(m)
        # A silence is part of the conversation's shape — without it a burst from this
        # morning and one from ten seconds ago read identically, and Olisar answers a
        # dead thread as if it were live. Only marked ahead of someone else's message:
        # on its own turn the marker would just be teaching it to narrate the clock.
        gap = ""
        if previous is not None and not mine:
            elapsed = (_aware(m.created_at) - previous).total_seconds()
            if elapsed >= GAP_SECONDS:
                gap = f"— {_elapsed(elapsed)} —\n"
        previous = _aware(m.created_at)
        if mine:
            _append(contents, "model", m.content)
            continue
        speaker = speaker_name(m, names)
        if m.author_is_bot:
            speaker += " (bot)"
        tag = _reply_tag(targets.get(m.reply_to_message_id or 0), HISTORY_SNIPPET_MAX)
        _append(contents, "user", f"{gap}{speaker}{tag}: {m.content}")

    # The message being answered is "now", so a gap before it says whether the visible
    # history is a live conversation or one that ended hours ago.
    gap = ""
    if previous is not None:
        elapsed = (datetime.now(timezone.utc) - previous).total_seconds()
        if elapsed >= GAP_SECONDS:
            gap = f"— {_elapsed(elapsed)} —\n"
    _append(
        contents, "user", f"{gap}{current_display_name}{_reply_tag(reply_to)}: {current_text}"
    )
    # Attach the new message's images to that same user turn (it's contents[-1]). A note
    # (e.g. for a GIF flattened to its first frame) rides just after its image so the model
    # knows what it's actually looking at.
    for data, mime, note in current_images or []:
        contents[-1].parts.append(types.Part(inline_data=types.Blob(mime_type=mime, data=data)))
        if note:
            contents[-1].parts.append(types.Part(text=f"(The image just above is {note}.)"))

    recent_ids = {m.message_id for m in rows}
    return contents, recent_ids
