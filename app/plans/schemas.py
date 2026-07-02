import uuid
from datetime import datetime

from pydantic import BaseModel

from app.plans.models import PlanInterval, PlanStatus


class PlanCreate(BaseModel):
    name: str
    amount: int  # in smallest currency unit (e.g. cents)
    currency: str
    interval: PlanInterval
    interval_count: int = 1
    trial_period_days: int | None = None
    installments_count: int | None = None


class PlanUpdate(BaseModel):
    name: str | None = None
    status: PlanStatus | None = None
    trial_period_days: int | None = None
    installments_count: int | None = None


class PlanRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    amount: int
    currency: str
    interval: PlanInterval
    interval_count: int
    trial_period_days: int | None
    installments_count: int | None
    status: PlanStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
