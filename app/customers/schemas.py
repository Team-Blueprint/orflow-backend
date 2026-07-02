import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr


class CustomerCreate(BaseModel):
    email: str
    name: str
    external_id: str | None = None


class CustomerUpdate(BaseModel):
    email: str | None = None
    name: str | None = None
    external_id: str | None = None


class CustomerRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    name: str
    external_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
