"""Keep stored copies of a message in step with Discord edits and deletions.

Discord is the source of truth: if someone edits or deletes a message, the version
Olisar holds — in conversational memory (``message``, the live context window), the
search index (``search_message``), and context-channel snapshots
(``channel_context_item``) — should follow. Without this, the bot can quote text a
user already removed, and ``search_messages`` can surface deleted content forever.

``apply_delete`` also prunes the message's vector (the same discipline as
``forget_user``); the FTS ``AFTER DELETE``/``AFTER UPDATE`` triggers re-sync the
keyword index automatically when ``search_message`` rows change.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import ChannelContextItem, Message, SearchMessage
from olisar.memory.vectors import delete_embedding

log = logging.getLogger("olisar.revisions")


async def apply_edit(session: AsyncSession, *, message_id: int, content: str) -> None:
    """Overwrite the stored content of a message everywhere it's kept. ``content``
    is the already-rendered new text (see bot.content.message_text). An empty
    string is ignored, so partial gateway payloads can't blank a row."""
    content = (content or "").strip()
    if not content:
        return
    for model in (Message, SearchMessage, ChannelContextItem):
        await session.execute(
            update(model).where(model.message_id == message_id).values(content=content)
        )


async def apply_delete(session: AsyncSession, *, message_ids: list[int]) -> None:
    """Remove deleted messages from memory, the search index, and context
    snapshots, pruning conversational-message vectors along the way."""
    if not message_ids:
        return
    # Prune embeddings for any conversational rows before deleting them.
    local_ids = (
        await session.scalars(
            select(Message.id).where(Message.message_id.in_(message_ids))
        )
    ).all()
    for mid in local_ids:
        await delete_embedding(session, "message_embedding", mid)

    for model in (Message, SearchMessage, ChannelContextItem):
        await session.execute(
            delete(model).where(model.message_id.in_(message_ids))
        )
    log.info("removed %d deleted message(s) from memory + index", len(message_ids))
