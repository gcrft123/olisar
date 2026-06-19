"""Audit log helper — records who changed what from the dashboard."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    actor: object,
    action: str,
    target_type: str | None = None,
    target_id: object | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor=str(actor),
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            before=before,
            after=after,
            ip=ip,
        )
    )
