import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException

from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_async_db
from app.core.context import current_tenant_id, current_key_type
from app.webhooks import outbound_service
from app.webhooks.schemas import (
    WebhookEndpointCreate,
    WebhookEndpointRead,
    OutboundWebhookEventRead,
    WebhookDeliveryAttemptRead,
    EventCatalogRead
)
from app.core.exceptions import EntityNotFoundError, ErrorResponse

router = APIRouter(prefix="/webhooks", tags=["Outbound Webhooks"])

def require_tenant() -> uuid.UUID:
    tenant_id = current_tenant_id.get()
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Enforce sk_* key usage for endpoint management
    key_type = current_key_type.get()
    if not key_type or not key_type.startswith("sk_"):
        raise HTTPException(
            status_code=403, 
            detail="Secret key (sk_*) required for this endpoint"
        )
    return tenant_id

@router.post("/endpoints/add", response_model=WebhookEndpointRead, status_code=201)
async def create_endpoint(
    endpoint_in: WebhookEndpointCreate,
    tenant_id: uuid.UUID = Depends(require_tenant),
    session: AsyncSession = Depends(get_async_db)
):
    """Register a new webhook endpoint.
    
    Creates a new destination URL that will receive HTTP POST requests for outbound events.
    """
    return await outbound_service.create_webhook_endpoint(session, tenant_id, endpoint_in)

@router.get("/endpoints/all", response_model=List[WebhookEndpointRead])
async def list_endpoints(
    tenant_id: uuid.UUID = Depends(require_tenant),
    session: AsyncSession = Depends(get_async_db)
):
    """List all registered webhook endpoints.
    
    Returns the list of endpoints configured to receive events for the current tenant.
    """
    return await outbound_service.get_webhook_endpoints(session, tenant_id)

@router.delete("/endpoints/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(require_tenant),
    session: AsyncSession = Depends(get_async_db)
):
    """Delete a webhook endpoint.
    
    Permanently removes the endpoint. No further events will be sent to it.
    """
    success = await outbound_service.delete_webhook_endpoint(session, tenant_id, endpoint_id)
    if not success:
        raise EntityNotFoundError("WebhookEndpoint", str(endpoint_id))

@router.get("/events/all", response_model=List[OutboundWebhookEventRead])
async def list_events(
    skip: int = 0,
    limit: int = 50,
    tenant_id: uuid.UUID = Depends(require_tenant),
    session: AsyncSession = Depends(get_async_db)
):
    """List recent outbound webhook events.
    
    Returns a history of webhook events triggered by the tenant's activity.
    """
    return await outbound_service.get_outbound_events(session, tenant_id, skip, limit)

@router.get("/events/{event_id}/deliveries", response_model=List[WebhookDeliveryAttemptRead])
async def list_delivery_attempts(
    event_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(require_tenant),
    session: AsyncSession = Depends(get_async_db)
):
    """List delivery attempts for a specific event.
    
    Shows the history of HTTP requests made to deliver this event, including status codes and responses.
    """
    return await outbound_service.get_delivery_attempts(session, tenant_id, event_id)

@router.get("/events/catalog", response_model=EventCatalogRead, summary="Webhook Event Catalog", description="Returns a schema outlining all available webhook events that can be emitted by the platform.")
async def get_event_catalog():
    """Get the webhook event catalog."""
    return EventCatalogRead(
        events=[
            {"type": "subscription.activated", "description": "Fired when a subscription becomes active."},
            {"type": "subscription.paused", "description": "Fired when a subscription is paused."},
            {"type": "subscription.canceled", "description": "Fired when a subscription is canceled."},
            {"type": "subscription.trial_ending", "description": "Fired when a trial period ends."},
            {"type": "invoice.paid", "description": "Fired when an invoice is successfully paid."},
            {"type": "invoice.payment_failed", "description": "Fired when an invoice payment fails."},
            {"type": "payment_method.attached", "description": "Fired when a new payment method is attached to a customer."}
        ]
    )
