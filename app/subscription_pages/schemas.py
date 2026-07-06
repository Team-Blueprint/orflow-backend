import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.plans.models import PlanInterval


class SubscriptionPageCreate(BaseModel):
    plan_id: uuid.UUID


class SubscriptionPageUpdate(BaseModel):
    plan_id: uuid.UUID | None = None
    is_active: bool | None = None


class SubscriptionPageRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    plan_id: uuid.UUID
    code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SubscriptionPageWithPlanRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID | None
    plan_id: uuid.UUID
    code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    plan_name: str
    url: str

    @classmethod
    def from_db(cls, page, plan):
        return cls(
            id=page.id,
            tenant_id=page.tenant_id,
            project_id=page.project_id,
            plan_id=page.plan_id,
            code=page.code,
            is_active=page.is_active,
            created_at=page.created_at,
            updated_at=page.updated_at,
            plan_name=plan.name,
            url=f"/subscribe/{page.code}",
        )


class PublicPlanInfo(BaseModel):
    name: str
    amount: Decimal
    currency: str
    interval: PlanInterval
    interval_count: int

    model_config = {"from_attributes": True}

    @field_validator("amount", mode="before")
    @classmethod
    def convert_to_major(cls, v):
        if isinstance(v, int):
            return Decimal(v) / Decimal(100)
        return v


class PublicPageInfo(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    name: str
    amount: Decimal
    currency: str
    interval: PlanInterval
    interval_count: int
    merchant_name: str

    @field_validator("amount", mode="before")
    @classmethod
    def convert_to_major(cls, v):
        if isinstance(v, int):
            return Decimal(v) / Decimal(100)
        return v


class PublicCheckoutRequest(BaseModel):
    name: str
    email: str


class PublicCheckoutResponse(BaseModel):
    checkout_link: str
    order_reference: str
