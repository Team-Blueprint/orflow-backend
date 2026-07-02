"""Auth & API key management router.

Public endpoints (no API key or JWT required):
    POST /auth/signup
    POST /auth/signin
    POST /auth/refresh

JWT-protected endpoints (Bearer token in Authorization header):
    GET  /auth/me
    GET  /auth/keys/new          — view all 4 key slots anytime
    POST /auth/keys/create       — generate a key for a slot that has no key yet
    POST /auth/keys/regenerate   — replace an existing key (old key stops working immediately)
    POST /auth/keys/revoke       — soft-revoke a key slot
"""
from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_async_db
from app.tenants.schemas import (
    ApiKeyRead,
    CreateKeyRequest,
    KeyActionResponse,
    RefreshRequest,
    RegenerateKeyRequest,
    RevokeKeyRequest,
    RevokeKeyResponse,
    SigninRequest,
    SignupRequest,
    SignupResponse,
    TenantRead,
    TokenPair,
)
from app.tenants.service import TenantService, verify_access_token
from app.core.exceptions import ErrorResponse

router = APIRouter(prefix="/auth", tags=["Auth"])

_bearer = HTTPBearer(auto_error=False)


# ── JWT dependency ─────────────────────────────────────────────────────────────

async def _require_jwt(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_async_db),
):
    """FastAPI dependency — validates the Bearer JWT and returns the Tenant."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        tenant_id = verify_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    service = TenantService(db)
    tenant = await service.get_tenant(tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant not found or inactive",
        )
    return tenant


# ── Public endpoints ───────────────────────────────────────────────────────────

@router.post(
    "/signup", 
    response_model=SignupResponse, 
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tenant",
    description="Creates the tenant account with email and password and returns an access/refresh token pair. If the email is already in use, returns a 409 Conflict.",
    responses={
        409: {"model": ErrorResponse, "description": "Email already registered."}
    }
)
async def signup(
    payload: SignupRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Register a new tenant.

    Creates the tenant account with email + password and returns a token pair.

    """
    service = TenantService(db)
    try:
        tenant, tokens = await service.signup(
            name=payload.name,
            email=payload.email,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return SignupResponse(
        tenant=TenantRead.model_validate(tenant),
        tokens=TokenPair(**tokens),
    )


@router.post(
    "/signin", 
    response_model=TokenPair,
    summary="Authenticate tenant",
    description="Authenticate with email and password to receive an access and refresh token pair.",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials."}
    }
)
async def signin(
    payload: SigninRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Authenticate with email + password; receive an access + refresh token pair."""
    service = TenantService(db)
    try:
        _tenant, tokens = await service.signin(
            email=payload.email,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        )
    return TokenPair(**tokens)


@router.post(
    "/refresh", 
    response_model=TokenPair,
    summary="Refresh access token",
    description="Exchange a valid refresh token for a new access and refresh token pair.",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired refresh token."}
    }
)
async def refresh_token(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    service = TenantService(db)
    try:
        tokens = await service.refresh(payload.refresh_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        )
    return TokenPair(**tokens)


# ── JWT-protected endpoints ────────────────────────────────────────────────────

@router.get(
    "/me", 
    response_model=TenantRead,
    summary="Get current tenant",
    description="Returns the profile of the currently authenticated tenant.",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid access token."}
    }
)
async def get_me(tenant=Depends(_require_jwt)):
    """Return the profile of the currently authenticated tenant."""
    return TenantRead.model_validate(tenant)


@router.get(
    "/keys/new", 
    response_model=ApiKeyRead,
    summary="Get API keys",
    description="Returns all four API key slots and their current values. Slots that have never been created appear as `null`. Keys can be retrieved at any time.",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid access token."}
    }
)
async def get_api_keys(tenant=Depends(_require_jwt)):
    """Return all four API key slots and their current values.

    Slots that have never been created appear as ``null``.
    This endpoint is always available — keys can be retrieved at any time.
    """
    return ApiKeyRead.model_validate(tenant)


@router.post(
    "/keys/create", 
    response_model=KeyActionResponse, 
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
    description="Creates a key for a slot that has never been issued. Returns the new key value. Returns `409` if the slot already has a key.",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid access token."},
        409: {"model": ErrorResponse, "description": "Key already exists for this slot."}
    }
)
async def create_key(
    payload: CreateKeyRequest,
    tenant=Depends(_require_jwt),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a key for a slot that has never been issued.

    Returns the new key value. The key can be retrieved again anytime via
    ``GET /auth/keys/new`` — it is never hidden after creation.

    Returns ``409`` if the slot already has a key (use ``/auth/keys/regenerate`` instead).
    """
    service = TenantService(db)
    try:
        _updated, new_value = await service.create_key(tenant.id, payload.key_type)
    except ValueError as exc:
        status_code = (
            status.HTTP_409_CONFLICT
            if "already exists" in str(exc)
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=status_code, detail=str(exc))

    return KeyActionResponse(key_type=payload.key_type, value=new_value)


@router.post(
    "/keys/regenerate", 
    response_model=KeyActionResponse,
    summary="Regenerate an API key",
    description="Replaces an existing API key with a new value. The old key stops working immediately.",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid access token."},
        404: {"model": ErrorResponse, "description": "Key slot not found or never issued."}
    }
)
async def regenerate_key(
    payload: RegenerateKeyRequest,
    tenant=Depends(_require_jwt),
    db: AsyncSession = Depends(get_async_db),
):
    """Replace an existing API key with a new value.

    The old key stops working **immediately** — there is no grace period.
    The new key can be retrieved again anytime via ``GET /auth/keys/new``.
    """
    service = TenantService(db)
    try:
        _updated, new_value = await service.regenerate_key(tenant.id, payload.key_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return KeyActionResponse(key_type=payload.key_type, value=new_value)


@router.post(
    "/keys/revoke", 
    response_model=RevokeKeyResponse,
    summary="Revoke an API key",
    description="Soft-revokes one API key slot. Its active flag is set to `False`. Use `/auth/keys/regenerate` to get a working key again.",
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid access token."},
        404: {"model": ErrorResponse, "description": "Key slot not found or never issued."}
    }
)
async def revoke_key(
    payload: RevokeKeyRequest,
    tenant=Depends(_require_jwt),
    db: AsyncSession = Depends(get_async_db),
):
    """Soft-revoke one API key slot.

    The key value is retained in the database; only its active flag is set
    to ``False``.  Use ``/auth/keys/regenerate`` to get a working key again.
    """
    service = TenantService(db)
    try:
        await service.revoke_key(tenant.id, payload.key_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return RevokeKeyResponse(key_type=payload.key_type)
