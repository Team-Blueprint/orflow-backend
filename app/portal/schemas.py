import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ── Auth ──────────────────────────────────────────────────────────────────────

class PortalVerifyRequest(BaseModel):
    token_slug: str
    pin: str


class PortalVerifyResponse(BaseModel):
    portal_session_token: str


class PortalUpdatePinRequest(BaseModel):
    current_pin: str
    new_pin: str


# ── Subscription view ─────────────────────────────────────────────────────────

class PortalSubscriptionRead(BaseModel):
    subscription_id: uuid.UUID
    plan_name: str
    status: str
    amount: int
    currency: str
    next_charge_date: Optional[datetime]
    card_last4: Optional[str]
    card_brand: Optional[str]


# ── Payment history ───────────────────────────────────────────────────────────

class PortalPaymentRead(BaseModel):
    date: datetime
    amount: int
    currency: str
    status: str


# ── Update card ───────────────────────────────────────────────────────────────

class PortalUpdateCardRequest(BaseModel):
    payment_token: str


class CreateUpdateCardCheckoutResponse(BaseModel):
    checkout_link: str
    order_reference: str


class ConfirmUpdateCardRequest(BaseModel):
    order_reference: str


class ConfirmUpdateCardResponse(BaseModel):
    success: bool


# ── Public access lookup ──────────────────────────────────────────────────────

class PortalAccessRead(BaseModel):
    name: str


# ── Verify checkout (callback redirect) ───────────────────────────────────────

class VerifyCheckoutResponse(BaseModel):
    success: bool
    status: str  # "paid" | "open" | "failed"
    subscription_id: uuid.UUID | None = None
    portal_token_slug: str | None = None
    customer_name: str | None = None
