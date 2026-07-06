import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field



class SignupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8)


class SigninRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    """Returned on signin and token refresh."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"



class TenantRead(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    google_sub: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SignupResponse(BaseModel):
    """Returned at signup — tenant profile + access token.

    The access_token is returned in the body for convenience (e.g. Swagger,
    Postman). The frontend should rely on the http-only cookies instead.
    API keys are NOT returned here. The user creates them explicitly via
    ``POST /auth/keys/create`` from the dashboard whenever they need them.
    """
    tenant: TenantRead
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SigninResponse(BaseModel):
    """Returned at signin — tenant profile + access token.

    The access_token is returned in the body for convenience (e.g. Swagger,
    Postman). The frontend should rely on the http-only cookies instead.
    """
    tenant: TenantRead
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthMessage(BaseModel):
    """Simple message response for auth operations."""
    message: str



class ApiKeyRead(BaseModel):
    pk_test: str | None
    sk_test: str | None
    pk_live: str | None
    sk_live: str | None

    pk_test_active: bool
    sk_test_active: bool
    pk_live_active: bool
    sk_live_active: bool

    model_config = {"from_attributes": True}



KeyType = Literal["pk_test", "sk_test", "pk_live", "sk_live"]


class CreateKeyRequest(BaseModel):
    key_type: KeyType


class RegenerateKeyRequest(BaseModel):
    key_type: KeyType


class RevokeKeyRequest(BaseModel):
    key_type: KeyType


class KeyActionResponse(BaseModel):
    key_type: str
    value: str
    active: bool = True


class RevokeKeyResponse(BaseModel):
    key_type: str
    revoked: bool = True
