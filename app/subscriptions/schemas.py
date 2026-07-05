import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

from app.invoices.schemas import InvoiceRead
from app.plans.models import PlanInterval, PlanStatus
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


class PlanBrief(BaseModel):
    id: uuid.UUID
    name: str
    amount: Decimal
    currency: str
    interval: PlanInterval
    interval_count: int
    status: PlanStatus

    model_config = {"from_attributes": True}

    @field_validator("amount", mode="before")
    @classmethod
    def convert_to_major(cls, v):
        if isinstance(v, int):
            return Decimal(v) / Decimal(100)
        return v


class CustomerBrief(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    external_id: str | None

    model_config = {"from_attributes": True}


class SubscriptionWithPlanRead(BaseModel):
    id: uuid.UUID
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
    plan: PlanBrief

    @classmethod
    def from_db(cls, subscription, plan):
        return cls(
            id=subscription.id,
            customer_id=subscription.customer_id,
            plan_id=subscription.plan_id,
            payment_method_id=subscription.payment_method_id,
            status=subscription.status,
            type=subscription.type,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            trial_end=subscription.trial_end,
            canceled_at=subscription.canceled_at,
            cancel_at_period_end=subscription.cancel_at_period_end,
            created_at=subscription.created_at,
            updated_at=subscription.updated_at,
            plan=PlanBrief.model_validate(plan),
        )


class SubscriberRead(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    external_id: str | None
    created_at: datetime
    subscriptions: list[SubscriptionWithPlanRead]

    @classmethod
    def from_db(cls, customer, subscriptions):
        return cls(
            id=customer.id,
            email=customer.email,
            name=customer.name,
            external_id=customer.external_id,
            created_at=customer.created_at,
            subscriptions=[SubscriptionWithPlanRead.from_db(s, p) for s, p in subscriptions],
        )
