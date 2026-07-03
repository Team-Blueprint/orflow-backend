"""Audit-log writing helper.

Kept deliberately small and commit-free: the calling transition function owns
the transaction so the status change and its audit row commit atomically.
``tenant_id`` is passed in explicitly (read off the entity), so this works in
the background worker where there is no request context var.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEntityType, AuditLog


def record_transition(
    session: AsyncSession,
    *,
    entity_type: AuditEntityType,
    entity_id: uuid.UUID,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    old_status: str | None,
    new_status: str | None,
    reason: str | None = None,
    actor: str = "system",
) -> AuditLog:
    """Stage an ``AuditLog`` row on the session (no commit)."""
    entry = AuditLog(
        tenant_id=tenant_id,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        old_status=old_status,
        new_status=new_status,
        reason=reason,
        actor=actor,
    )
    session.add(entry)
    return entry
