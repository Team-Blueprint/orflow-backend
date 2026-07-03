from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

class NombaWebhookTokenizedCardData(BaseModel):
    tokenKey: str | None = None
    cardType: str | None = None
    tokenExpiryYear: str | None = None
    tokenExpiryMonth: str | None = None
    cardPan: str | None = None

class NombaWebhookData(BaseModel):
    order: dict | None = None
    transaction: dict | None = None
    merchant: dict | None = None
    tokenizedCardData: NombaWebhookTokenizedCardData | None = None

class NombaWebhookPayload(BaseModel):
    event_type: str
    requestId: str | None = None
    data: NombaWebhookData


import uuid
from datetime import datetime

class WebhookEndpointCreate(BaseModel):
    url: str
    description: Optional[str] = None

class WebhookEndpointRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    url: str
    secret: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class OutboundWebhookEventRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    event_type: str
    payload: dict
    status: str
    created_at: datetime

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WebhookDeliveryAttemptRead(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    status_code: Optional[int]
    response_body: Optional[str]
    attempt_number: int
    created_at: datetime

    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class EventCatalogEntry(BaseModel):
    type: str = Field(..., description="The event type string, e.g., 'subscription.activated'")
    description: str = Field(..., description="A description of when this event is fired.")

class EventCatalogRead(BaseModel):
    events: list[EventCatalogEntry] = Field(..., description="List of all available webhook events.")
