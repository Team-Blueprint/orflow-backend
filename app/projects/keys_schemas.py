import uuid
from datetime import datetime

from pydantic import BaseModel

from app.tenants.schemas import KeyType


class ProjectApiKeyRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    key_type: str
    is_active: bool
    name: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectApiKeyCreateRequest(BaseModel):
    key_type: KeyType
    name: str | None = None


class ProjectApiKeyRegenerateRequest(BaseModel):
    key_type: KeyType


class ProjectApiKeyRevokeRequest(BaseModel):
    key_type: KeyType


class ProjectApiKeyActionResponse(BaseModel):
    key_type: str
    value: str
    active: bool = True
    name: str | None = None


class ProjectApiKeyRevokeResponse(BaseModel):
    key_type: str
    revoked: bool = True
