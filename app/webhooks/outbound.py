"""Outbound webhook event seam (engine -> tenants).

This owns the full delivery pipeline (``WebhookEndpoint`` + signed,
retried delivery). Until that lands, this module is the single, stable seam
that the rest of the engine calls whenever a tenant-visible event occurs — the
dunning flow  in particular enqueues an event at every stage
transition. Today it just logs; wiring real delivery later means changing only
this function, not its many callers.

Event catalog 
    subscription.created, subscription.activated, subscription.past_due,
    subscription.unpaid, subscription.canceled, subscription.completed,
    invoice.paid, invoice.payment_failed, invoice.voided
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_project_id

logger = logging.getLogger(__name__)


async def enqueue_webhook_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """Queue an outbound webhook event for ``tenant_id``.

    it records intent so callers can be written
    against the final interface. ``session`` is accepted now so the eventual
    implementation can persist a queued ``WebhookEvent`` row in the caller's
    transaction without a signature change.
    """
    logger.info(
        "Outbound webhook queued: %s tenant=%s payload=%s",
        event_type,
        tenant_id,
        payload or {},
    )
    
    from app.webhooks.models import OutboundWebhookEvent, OutboundEventStatus
    from app.worker.tasks import enqueue_webhook_delivery
    
    event = OutboundWebhookEvent(
        tenant_id=tenant_id,
        project_id=current_project_id.get(),
        event_type=event_type,
        payload=payload or {},
        status=OutboundEventStatus.pending
    )
    session.add(event)
    await session.flush() # flush to get the UUID
    
    # In real app, you might use arq directly. We enqueue it using the tasks wrapper.
    await enqueue_webhook_delivery(event.id)
