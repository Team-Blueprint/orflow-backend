import uuid
from datetime import datetime

from pydantic import BaseModel

from app.invoices.schemas import InvoiceRead
from app.subscriptions.models import SubscriptionStatus, SubscriptionType


class SubscriptionRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    plan_id: uuid.UUID
    payment_method_id: uuid.UUID | None
    status: SubscriptionStatus
    type: SubscriptionType
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_end: datetime | None
    canceled_at: datetime | None
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubscriptionCreate(BaseModel):
    customer_id: uuid.UUID
    plan_id: uuid.UUID
    payment_method_id: uuid.UUID | None = None
    trial: bool = False


class SubscriptionCreateResponse(BaseModel):
    subscription: SubscriptionRead
    checkout_link: str | None = None
    order_reference: str | None = None


class ChangePlanRequest(BaseModel):
    new_plan_id: uuid.UUID


class ProrationLineItemRead(BaseModel):
    description: str
    amount_minor: int


class ChangePlanResponse(BaseModel):
    subscription: SubscriptionRead
    invoice: InvoiceRead
    line_items: list[ProrationLineItemRead]
    # True when a positive net amount was successfully collected.
    charged: bool
    # Internal PaymentStatus value, "no_charge", or "no_payment_method".
    payment_status: str | None = None

class SubscriptionAuditLogRead(BaseModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    old_status: str | None
    new_status: str | None
    reason: str | None
    actor: str
    created_at: datetime

    model_config = {"from_attributes": True}
