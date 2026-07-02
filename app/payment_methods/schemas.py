import uuid
from datetime import datetime
from pydantic import BaseModel
from app.payment_methods.models import PaymentMethodStatus, PaymentMethodType


class PaymentMethodCreate(BaseModel):
    customer_id: uuid.UUID
    type: PaymentMethodType
    provider_token: str
    last_four: str | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None
    is_default: bool = False


class PaymentMethodUpdate(BaseModel):
    status: PaymentMethodStatus | None = None
    is_default: bool | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None


class PaymentMethodRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    type: PaymentMethodType
    status: PaymentMethodStatus
    last_four: str | None
    expiry_month: int | None
    expiry_year: int | None
    is_default: bool
    created_at: datetime
    updated_at: datetime

    # provider_token intentionally omitted — never returned to callers

    model_config = {"from_attributes": True}
