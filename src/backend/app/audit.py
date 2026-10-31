from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent


def record_audit(
    session: AsyncSession,
    entity: str,
    entity_id: str | int,
    action: str,
    actor: str = "local-user",
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event. Rows are never updated or deleted (append-only)."""
    event = AuditEvent(
        entity=entity,
        entity_id=str(entity_id),
        action=action,
        actor=actor,
        payload=payload,
    )
    session.add(event)
    return event
