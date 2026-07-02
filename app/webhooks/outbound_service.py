from typing import Sequence
import uuid
import secrets
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.webhooks.models import WebhookEndpoint, OutboundWebhookEvent, WebhookDeliveryAttempt
from app.webhooks.schemas import WebhookEndpointCreate

async def create_webhook_endpoint(
    session: AsyncSession, tenant_id: uuid.UUID, endpoint_in: WebhookEndpointCreate
) -> WebhookEndpoint:
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id,
        url=endpoint_in.url,
        secret=secrets.token_hex(32),
        description=endpoint_in.description,
        is_active=True
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)
    return endpoint

async def get_webhook_endpoints(
    session: AsyncSession, tenant_id: uuid.UUID
) -> Sequence[WebhookEndpoint]:
    result = await session.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id)
    )
    return result.scalars().all()

async def delete_webhook_endpoint(
    session: AsyncSession, tenant_id: uuid.UUID, endpoint_id: uuid.UUID
) -> bool:
    result = await session.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.id == endpoint_id
        )
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        return False
    await session.delete(endpoint)
    await session.commit()
    return True

async def get_outbound_events(
    session: AsyncSession, tenant_id: uuid.UUID, skip: int = 0, limit: int = 50
) -> Sequence[OutboundWebhookEvent]:
    result = await session.execute(
        select(OutboundWebhookEvent)
        .where(OutboundWebhookEvent.tenant_id == tenant_id)
        .order_by(OutboundWebhookEvent.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()

async def get_delivery_attempts(
    session: AsyncSession, tenant_id: uuid.UUID, event_id: uuid.UUID
) -> Sequence[WebhookDeliveryAttempt]:
    # Ensure event belongs to tenant
    event_result = await session.execute(
        select(OutboundWebhookEvent).where(
            OutboundWebhookEvent.id == event_id,
            OutboundWebhookEvent.tenant_id == tenant_id
        )
    )
    event = event_result.scalar_one_or_none()
    if not event:
        return []
        
    result = await session.execute(
        select(WebhookDeliveryAttempt)
        .where(WebhookDeliveryAttempt.event_id == event_id)
        .order_by(WebhookDeliveryAttempt.created_at.desc())
    )
    return result.scalars().all()
