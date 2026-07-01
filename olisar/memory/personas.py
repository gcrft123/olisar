"""Per-user persona synthesis.

Builds and **refines** a short characterization of a member from their own message
history so Olisar can talk to each person naturally. The automatic path regenerates
when their ``messages_since_persona`` counter crosses the configured threshold; a
manual path (the dashboard "Create impression" button) can build on demand and, if
the conversation memory is thin, reach into the all-channel search index for more of
the member's messages. Each refresh *updates* the existing summary rather than
overwriting it from scratch. Strictly gated on opt-out, and deliberately conservative
(no speculation / sensitive inference).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.db.models import Message, SearchMessage, UserProfile, utcnow
from olisar.gemini.client import get_gemini
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.memory.channels import resource_reference

log = logging.getLogger("olisar.personas")

PERSONA_SYSTEM = (
    "You maintain a brief, respectful profile of a Discord member so a friendly bot "
    "can talk to them naturally. You're given the existing profile (if any), the "
    "member's recent messages, their server roles, and a server reference (channels "
    "like #roles-list / #rules that explain what roles and terms mean).\n\n"
    "UPDATE the existing profile: keep details that are still true, add anything new "
    "the recent messages reveal, and only drop or correct something the new messages "
    "clearly contradict. In 3-6 sentences capture their interests, expertise, "
    "communication style, and stable, clearly stated facts (timezone, projects, "
    "preferences). When a role they hold is explained in the reference, fold in what "
    "it signifies — status, responsibilities, standing — and tie it to this member. "
    "Use ONLY the existing profile, messages, roles, and reference; do not speculate, "
    "infer sensitive attributes, or include one-off remarks. Output ONLY the updated "
    "profile — neutral and factual, no preamble."
)
HISTORY_LIMIT = 60   # messages pulled to (re)build a persona
MIN_MESSAGES = 8     # automatic build needs this much signal
MANUAL_MIN = 3       # the manual button is more eager (it can use the index)


async def _gather_messages(
    session: AsyncSession, guild_id: int, user_id: int, *, use_index: bool
) -> list[str]:
    """The member's most recent message texts (oldest-first), up to HISTORY_LIMIT.
    Primary source is conversation memory (memory/both channels); when ``use_index``
    is set and that's short of the limit, top up from the all-channel search index."""
    rows = (
        await session.scalars(
            select(Message)
            .where(
                Message.guild_id == guild_id,
                Message.author_id == user_id,
                Message.author_is_bot == False,  # noqa: E712
            )
            .order_by(Message.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
    ).all()
    picked: dict[int, str] = {m.message_id: m.content for m in rows if m.content.strip()}

    if use_index and len(picked) < HISTORY_LIMIT:
        extra = (
            await session.scalars(
                select(SearchMessage)
                .where(
                    SearchMessage.guild_id == guild_id,
                    SearchMessage.author_id == user_id,
                )
                .order_by(SearchMessage.created_at.desc())
                .limit(HISTORY_LIMIT)
            )
        ).all()
        for s in extra:
            if len(picked) >= HISTORY_LIMIT:
                break
            if s.message_id not in picked and s.content.strip():
                picked[s.message_id] = s.content

    # Discord message ids are time-ordered, so sorting by id gives chronological order
    # without juggling tz-aware/naive datetimes from two tables.
    return [picked[mid] for mid in sorted(picked)][:HISTORY_LIMIT]


async def _synthesize(
    session: AsyncSession, profile: UserProfile, contents: list[str], *, min_messages: int
) -> str | None:
    """Refine ``profile.persona_summary`` from ``contents`` (the member's messages).
    Returns the new summary, or None if there's too little signal / the model balks."""
    if len(contents) < min_messages:
        return None
    name = profile.display_name or str(profile.user_id)
    transcript = "\n".join(f"- {c}" for c in contents)
    role_names = ", ".join(r.get("name", "") for r in (profile.roles or []) if r.get("name"))
    reference = await resource_reference(session, profile.guild_id)

    parts = [f"Member: {name}"]
    if profile.persona_summary:
        parts.append(f"Existing profile:\n{profile.persona_summary}")
    if role_names:
        parts.append(f"Their server roles: {role_names}")
    if reference:
        parts.append(f"Server reference (what roles/terms mean):\n{reference}")
    parts.append(f"Their recent messages:\n{transcript}")

    try:
        result = await get_gemini().generate(
            contents=["\n\n".join(parts)],
            system_instruction=PERSONA_SYSTEM,
            model=settings.gemini_lite_model,
            temperature=0.3,
            max_output_tokens=320,
            source="persona",
        )
    except RateLimitExceeded:
        return None
    except Exception:
        log.exception("user persona generation failed")
        return None

    text = (result.text or "").strip()
    if not text:
        return None
    profile.persona_summary = text
    profile.persona_updated_at = utcnow()
    profile.messages_since_persona = 0
    return text


async def maybe_build_user_persona(
    session: AsyncSession, *, guild_id: int, user_id: int, threshold: int
) -> bool:
    """Automatic refresh, once a member's message counter crosses ``threshold``."""
    profile = await session.scalar(
        select(UserProfile).where(
            UserProfile.user_id == user_id, UserProfile.guild_id == guild_id
        )
    )
    if profile is None or profile.memory_opt_out:
        return False
    if profile.messages_since_persona < threshold:
        return False

    contents = await _gather_messages(session, guild_id, user_id, use_index=False)
    if len(contents) < MIN_MESSAGES:
        profile.messages_since_persona = 0  # wait for more signal; don't re-check each tick
        return False
    text = await _synthesize(session, profile, contents, min_messages=MIN_MESSAGES)
    if text:
        log.info("refreshed persona for user %s from %d messages", user_id, len(contents))
    return bool(text)


async def build_persona_now(
    session: AsyncSession, *, guild_id: int, user_id: int
) -> dict:
    """Manual build (dashboard button): refine the impression from up to the last 60
    of the member's messages, reaching into the all-channel index when conversation
    memory is thin. Returns ``{ok, impression?, messages?, error?}``."""
    profile = await session.scalar(
        select(UserProfile).where(
            UserProfile.user_id == user_id, UserProfile.guild_id == guild_id
        )
    )
    if profile is None:
        return {"ok": False, "error": "Olisar has no profile for that member yet."}
    if profile.memory_opt_out:
        return {"ok": False, "error": "That member has opted out of being remembered."}

    contents = await _gather_messages(session, guild_id, user_id, use_index=True)
    if len(contents) < MANUAL_MIN:
        return {"ok": False, "error": f"Not enough of their messages to go on ({len(contents)} found)."}
    text = await _synthesize(session, profile, contents, min_messages=MANUAL_MIN)
    if not text:
        return {"ok": False, "error": "The model was busy — try again in a moment."}
    log.info("manually built persona for user %s from %d messages", user_id, len(contents))
    return {"ok": True, "impression": text, "messages": len(contents)}
