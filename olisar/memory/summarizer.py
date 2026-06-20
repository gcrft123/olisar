"""Size-triggered channel summarization.

When a channel accumulates enough unsummarized tokens, condense the backlog into
a durable `channel_summary` (cheap Flash-Lite call), mark those messages as
summarized (prune-eligible), and reset the counter. Busy channels cross the
threshold sooner, so they summarize more often — automatically.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.context import name_map
from olisar.db.models import ChannelAllowlist, ChannelSummary, Message, utcnow
from olisar.gemini.client import get_gemini
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.memory.writer import estimate_tokens

log = logging.getLogger("olisar.summarizer")

SUMMARY_SYSTEM = (
    "You compress a slice of a Discord channel's history into durable notes. "
    "Write 3-6 terse bullet points capturing key facts, decisions, questions "
    "answered, plans, and who is involved. Keep names. Skip small talk and "
    "greetings. Output only the bullets, no preamble."
)
MIN_MESSAGES = 6


async def maybe_summarize_channel(
    session: AsyncSession, *, guild_id: int, channel_id: int, threshold: int
) -> bool:
    row = await session.scalar(
        select(ChannelAllowlist).where(
            ChannelAllowlist.guild_id == guild_id,
            ChannelAllowlist.channel_id == channel_id,
        )
    )
    if row is None or row.unsummarized_tokens < threshold:
        return False

    msgs = [
        m
        for m in (
            await session.scalars(
                select(Message)
                .where(Message.channel_id == channel_id, Message.summarized == False)  # noqa: E712
                .order_by(Message.created_at.asc())
            )
        ).all()
        if m.content.strip()
    ]
    if len(msgs) < MIN_MESSAGES:
        return False

    names = await name_map(session, {m.author_id for m in msgs if not m.author_is_bot})
    transcript = "\n".join(
        f"{'Olisar' if m.author_is_bot else names.get(m.author_id, str(m.author_id))}: {m.content}"
        for m in msgs
    )

    try:
        result = await get_gemini().generate(
            contents=[transcript],
            system_instruction=SUMMARY_SYSTEM,
            model=settings.gemini_lite_model,
            temperature=0.3,
            max_output_tokens=400,
        )
    except RateLimitExceeded:
        return False  # try again next tick
    except Exception:
        log.exception("channel summary generation failed")
        return False

    summary_text = result.text.strip()
    if not summary_text:
        return False

    session.add(
        ChannelSummary(
            guild_id=guild_id,
            channel_id=channel_id,
            summary=summary_text,
            covers_from_msg=msgs[0].message_id,
            covers_to_msg=msgs[-1].message_id,
            token_count=estimate_tokens(summary_text),
            embedded=False,  # the worker's embed pass picks this up
        )
    )
    for m in msgs:
        m.summarized = True
    row.unsummarized_tokens = 0
    row.last_summary_at = utcnow()
    log.info("summarized %d messages in channel %s", len(msgs), channel_id)
    # Glossary mining now runs on its own, more frequent pass (maintenance.run_glossary).
    return True
