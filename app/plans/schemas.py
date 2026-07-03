import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

from app.plans.models import PlanInterval, PlanStatus


class PlanCreate(BaseModel):
    name: str
    amount: Decimal  # in major currency unit (e.g. 10.00 for $10 or ₦10)
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
    amount: Decimal  # in major currency unit (e.g. 10.00 for $10 or ₦10)
    currency: str
    interval: PlanInterval
    interval_count: int
    trial_period_days: int | None
    installments_count: int | None
    status: PlanStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("amount", mode="before")
    @classmethod
    def convert_to_major(cls, v):
        """Convert stored minor-unit int (kobo/cents) to major-unit Decimal."""
        if isinstance(v, int):
            return Decimal(v) / Decimal(100)
        return v
