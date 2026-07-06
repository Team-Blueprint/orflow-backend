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

logger = logging.getLogger(__name__)


async def enqueue_webhook_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict | None = None,
    is_test: bool = False,
) -> None:
    """Queue an outbound webhook event for ``tenant_id``.

    ``is_test`` mirrors the API key environment that triggered the event.
    Events with ``is_test=True`` are only delivered to endpoints registered
    with a test-mode key; live events go only to live endpoints.
    """
    logger.info(
        "Outbound webhook queued: %s tenant=%s is_test=%s payload=%s",
        event_type,
        tenant_id,
        is_test,
        payload or {},
    )
    
    from app.webhooks.models import OutboundWebhookEvent, OutboundEventStatus
    from app.worker.tasks import enqueue_webhook_delivery
    
    event = OutboundWebhookEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload or {},
        status=OutboundEventStatus.pending,
        is_test=is_test,
    )
    session.add(event)
    await session.flush()  # flush to get the UUID
    
    await enqueue_webhook_delivery(event.id)
