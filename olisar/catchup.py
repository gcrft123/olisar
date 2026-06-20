"""On-demand 'what did I miss' digest for a single channel.

Reuses the rolling channel summaries plus recent messages and asks the lite model
for a short catch-up. Defaults to *since the asker last posted here*, with an
optional fixed window. Shared by the /catchup command and the catchup tool.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.context import name_map
from olisar.db.models import ChannelSummary, Message
from olisar.gemini.client import get_gemini
from olisar.gemini.rate_limiter import RateLimitExceeded

log = logging.getLogger("olisar.catchup")

CATCHUP_SYSTEM = (
    "You catch a Discord member up on what they missed in one channel. From the "
    "earlier notes and recent messages below, write 3-6 short bullet points: the key "
    "things discussed, questions asked (and whether they were answered), decisions, "
    "plans, and who was involved. Keep names. Skip greetings and small talk. If "
    "almost nothing happened, say so in one line. Output only the bullets, no preamble."
)

DEFAULT_WINDOW_HOURS = 24
MAX_MESSAGES = 120


async def generate_catchup(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    hours: int | None = None,
) -> str:
    """A short digest of recent activity in ``channel_id``. With ``hours`` it covers
    that window; otherwise it covers everything since the user last posted here (or
    the last day if they never have)."""
    now = datetime.now(timezone.utc)
    if hours and hours > 0:
        since = now - timedelta(hours=hours)
        scope = f"the last {hours}h"
    else:
        last = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.channel_id == channel_id, Message.author_id == user_id
            )
        )
        if last is not None:
            since = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            scope = "since you last posted here"
        else:
            since = now - timedelta(hours=DEFAULT_WINDOW_HOURS)
            scope = f"the last {DEFAULT_WINDOW_HOURS}h"

    msgs = [
        m
        for m in reversed(
            (
                await session.scalars(
                    select(Message)
                    .where(Message.channel_id == channel_id, Message.created_at > since)
                    .order_by(Message.created_at.desc())
                    .limit(MAX_MESSAGES)
                )
            ).all()
        )
        if (m.content or "").strip()
    ]
    summaries = (
        await session.scalars(
            select(ChannelSummary)
            .where(
                ChannelSummary.channel_id == channel_id,
                ChannelSummary.created_at > since,
            )
            .order_by(ChannelSummary.created_at.asc())
        )
    ).all()

    if not msgs and not summaries:
        return "You're all caught up — nothing notable here since you last stopped by."

    names = await name_map(session, {m.author_id for m in msgs if not m.author_is_bot})
    lines: list[str] = []
    if summaries:
        lines.append("Earlier notes:")
        lines.extend(s.summary for s in summaries)
    if msgs:
        lines.append("\nRecent messages:")
        for m in msgs:
            who = "Olisar" if m.author_is_bot else names.get(m.author_id, str(m.author_id))
            lines.append(f"{who}: {m.content}")

    try:
        result = await get_gemini().generate(
            contents=["\n".join(lines)],
            system_instruction=CATCHUP_SYSTEM,
            model=settings.gemini_lite_model,
            temperature=0.3,
            max_output_tokens=600,
        )
    except RateLimitExceeded:
        return "I'm rate-limited right now — try the catch-up again in a minute."
    except Exception:
        log.exception("catchup generation failed")
        return "Couldn't put a catch-up together just now — try again shortly."

    text = (result.text or "").strip()
    if not text:
        return "Couldn't put a catch-up together just now — try again shortly."
    return f"Here's what you missed ({scope}):\n{text}"
