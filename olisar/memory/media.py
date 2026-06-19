"""Fold a one-time image description into stored message rows.

When the vision model captions an image (live, or during backfill), the caption is
appended to whatever copies of that message we keep — the search index
(``search_message``), conversational memory (``message``), and any context-channel
snapshot (``channel_context_item``) — so the image becomes findable by what's in
it, not just its filename. Idempotent: a row that already carries a description is
left alone, so re-runs and overlapping live/backfill captioning don't stack.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import ChannelContextItem, Message, SearchMessage

# Must match bot/content.IMAGE_DESCRIPTION_PREFIX.
DESCRIPTION_PREFIX = "[image description:"


def description_marker(caption: str) -> str:
    """The text appended to a row's content for a given caption."""
    return f"\n{DESCRIPTION_PREFIX} {caption.strip()}]"


async def store_image_description(
    session: AsyncSession, *, message_id: int, caption: str
) -> None:
    """Append ``caption`` to every stored copy of the message that doesn't already
    have a description. No-op for an empty caption."""
    caption = (caption or "").strip()
    if not caption:
        return
    marker = description_marker(caption)
    for model in (SearchMessage, Message, ChannelContextItem):
        await session.execute(
            update(model)
            .where(
                model.message_id == message_id,
                model.content.not_like(f"%{DESCRIPTION_PREFIX}%"),
            )
            .values(content=model.content + marker)
        )
