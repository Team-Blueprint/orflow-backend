import uuid
from datetime import datetime

from pydantic import BaseModel

from app.invoices.models import InvoiceStatus


class InvoiceRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    subscription_id: uuid.UUID | None
    status: InvoiceStatus
    amount_due: int
    currency: str
    period_start: datetime | None
    period_end: datetime | None
    due_date: datetime | None
    paid_at: datetime | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
