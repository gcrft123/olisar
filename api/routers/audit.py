"""Activity log — what was changed here, and when.

``record_audit`` has been writing every config change and every destructive action to
``audit_log`` for a long time, from ~15 call sites in the admin and extension routers.
Nothing ever read it: the only record an operator saw of clearing a server's memory was a
toast that removed itself after 3.6 seconds. This exposes it.

Scope: the table has no ``guild_id`` column, so entries are install-wide and are reported
as such rather than being filtered into a per-server view they can't honestly support.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from api.auth.deps import require_admin
from olisar.db.engine import session_scope
from olisar.db.models import AdminUser, AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])

# Actions worth a line in the operator's history. `record_audit` also fires on routine
# config writes; those are noise in a "what happened to my data" view, so the destructive
# and identity-changing ones are marked and the rest are still returned but flagged.
DESTRUCTIVE = {
    "clear_memory",
    "clear_key",
    "delete_guild_fact",
    "set_channel_indexing",
}

# Plain-English labels. The stored action names are internal; an operator reading their own
# history should not have to translate `set_channel_indexing`.
LABELS: dict[str, str] = {
    "clear_memory": "Cleared memory",
    "update_persona": "Updated the persona",
    "update_config": "Changed behavior settings",
    "update_proactivity": "Changed proactivity",
    "update_command_messages": "Edited command replies",
    "update_keys": "Updated API keys",
    "clear_key": "Removed an API key",
    "set_channel_mode": "Changed a channel's mode",
    "set_channel_indexing": "Changed a channel's indexing",
    "build_impression": "Rebuilt a member impression",
    "toggle_extension": "Toggled an extension",
    "update_extension_settings": "Changed extension settings",
    "add_guild_fact": "Added a glossary fact",
    "delete_guild_fact": "Deleted a glossary fact",
    "mine_glossary": "Mined the glossary",
    "deep_mine_glossary": "Deep-mined the glossary",
}


@router.get("")
async def list_audit(
    limit: int = Query(100, ge=1, le=500),
    _: AdminUser = Depends(require_admin),
) -> dict:
    """The most recent entries, newest first."""
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
            )
        ).all()

    return {
        "install_wide": True,  # no guild_id on the table; say so rather than imply otherwise
        "entries": [
            {
                "id": r.id,
                "ts": r.ts.isoformat() if r.ts else None,
                "actor": r.actor,
                "action": r.action,
                "label": LABELS.get(r.action, r.action.replace("_", " ").capitalize()),
                "destructive": r.action in DESTRUCTIVE,
                "target_type": r.target_type,
                "target_id": r.target_id,
                # `after` carries the receipt — clear_memory stores its deleted-row counts
                # here, which is exactly the number the operator watched disappear.
                "after": r.after,
            }
            for r in rows
        ],
    }
