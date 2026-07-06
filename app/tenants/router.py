"""Auth & API key management router.

Public endpoints (no API key or JWT required):
    GET  /auth/google/login      — redirect to Google consent screen
    GET  /auth/google/callback   — Google OAuth callback
    POST /auth/signup
    POST /auth/signin
    POST /auth/refresh
    POST /auth/logout

JWT-protected endpoints (via http-only cookie or Authorization header):
    GET  /auth/me
    GET  /auth/keys/new          — view all 4 key slots anytime
    POST /auth/keys/create       — generate a key for a slot that has no key yet
    POST /auth/keys/regenerate   — replace an existing key (old key stops working immediately)
    POST /auth/keys/revoke       — soft-revoke a key slot
"""
from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_async_db
from app.tenants.schemas import (
    ApiKeyRead,
    AuthMessage,
    CreateKeyRequest,
    KeyActionResponse,
    RegenerateKeyRequest,
    RevokeKeyRequest,
    RevokeKeyResponse,
    SigninRequest,
    SigninResponse,
    SignupRequest,
    SignupResponse,
    TenantRead,
)
from app.tenants.service import TenantService, verify_access_token
from app.tenants.google_service import google_login_redirect, handle_google_callback
from app.core.exceptions import ErrorResponse
from app.core.config import settings
from app.core.cookies import set_auth_cookies, clear_auth_cookies

router = APIRouter(prefix="/auth", tags=["Auth"])

_bearer = HTTPBearer(auto_error=False)


# ── JWT dependency ─────────────────────────────────────────────────────────────

async def _require_jwt(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_async_db),
):
    """FastAPI dependency — validates the JWT and returns the Tenant.

    Prefers the ``Authorization: Bearer <token>`` header when present (for
    backward compatibility with API clients). Falls back to the ``access_token``
    http-only cookie for browser-based dashboard usage.

    Sets ``request.state.jwt_from_cookie`` so CSRF validation knows
    whether the request was authenticated via cookie (and thus needs CSRF).
    """
    if credentials is not None:
        token = credentials.credentials
        request.state.jwt_from_cookie = False
    else:
        token = request.cookies.get("access_token")
        request.state.jwt_from_cookie = token is not None

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing access token cookie or Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        tenant_id = verify_access_token(token)
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


# ── CSRF validation dependency ─────────────────────────────────────────────────

def _require_csrf(request: Request):
    """Validate CSRF token for state-changing cookie-authenticated requests.

    Only applies when the request was authenticated via cookie
    (``request.state.jwt_from_cookie`` is true). If the client used the
    ``Authorization`` header directly, CSRF is skipped because the
    double-submit cookie pattern is not relevant for non-cookie auth.
    """
    if not getattr(request.state, "jwt_from_cookie", False):
        return

    cookie_token = request.cookies.get("csrf_token")
    if cookie_token is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing CSRF token cookie",
        )

    header_token = request.headers.get("X-CSRF-Token")
    if header_token is None or header_token != cookie_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-CSRF-Token header",
        )


# ── Public endpoints ───────────────────────────────────────────────────────────


@router.get(
    "/google/login",
    summary="Google OAuth login",
    description="Redirects the browser to Google's consent screen. A CSRF-protecting state parameter is set as an http-only cookie.",
    responses={
        307: {"description": "Redirect to Google OAuth consent screen."}
    },
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    response_class=RedirectResponse,
)
async def google_login():
    """Redirect to Google's OAuth consent screen."""
    return google_login_redirect()


@router.get(
    "/google/callback",
    summary="Google OAuth callback",
    description="Handles the OAuth callback from Google. Exchanges the code for tokens, creates/links the tenant, sets auth cookies, and redirects to the frontend.",
    responses={
        307: {"description": "Redirect to frontend after successful authentication."},
        403: {"model": ErrorResponse, "description": "Invalid OAuth state (CSRF)."},
    },
    response_class=RedirectResponse,
)
async def google_callback(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """Handle the OAuth callback from Google and redirect to frontend."""
    tenant, tokens = await handle_google_callback(request, db)
    redirect = RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/google/callback",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    set_auth_cookies(redirect, tokens["access_token"], tokens["refresh_token"])
    redirect.delete_cookie(
        key="google_oauth_state",
        path="/v1/auth/google/callback",
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE.lower(),
    )
    return redirect


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tenant",
    description="Creates the tenant account with email and password. Tokens are set as http-only cookies.",
    responses={
        409: {"model": ErrorResponse, "description": "Email already registered."}
    }
)
async def signup(
    payload: SignupRequest,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
):
    """Register a new tenant.

    Creates the tenant account with email + password and sets
    access/refresh/csrf cookies on the response.
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

    set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])

    return SignupResponse(
        tenant=TenantRead.model_validate(tenant),
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )


@router.post(
    "/signin",
    response_model=SigninResponse,
    summary="Authenticate tenant",
    description="Authenticate with email and password. Tokens are set as http-only cookies.",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials."}
    }
)
async def signin(
    payload: SigninRequest,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
):
    """Authenticate with email + password; set auth cookies."""
    service = TenantService(db)
    try:
        tenant, tokens = await service.signin(
            email=payload.email,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        )

    set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])

    return SigninResponse(
        tenant=TenantRead.model_validate(tenant),
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )


@router.post(
    "/refresh",
    summary="Refresh access token",
    description="Exchange the refresh token cookie for a new access and refresh token pair. Tokens are set as http-only cookies.",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired refresh token."}
    }
)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
):
    """Exchange the refresh token cookie for a new access + refresh token pair."""
    refresh_token_value = request.cookies.get("refresh_token")
    if refresh_token_value is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token cookie",
        )

    service = TenantService(db)
    try:
        tokens = await service.refresh(refresh_token_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        )

    set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"])

    return {"message": "Tokens refreshed"}


@router.post(
    "/logout",
    summary="Logout",
    description="Clears all auth cookies to log the tenant out.",
)
async def logout(
    request: Request,
    response: Response,
):
    """Clear all auth cookies."""
    _require_csrf(request)
    clear_auth_cookies(response)
    return AuthMessage(message="Logged out")


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
    request: Request,
    tenant=Depends(_require_jwt),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a key for a slot that has never been issued.

    Returns the new key value. The key can be retrieved again anytime via
    ``GET /auth/keys/new`` — it is never hidden after creation.

    Returns ``409`` if the slot already has a key (use ``/auth/keys/regenerate`` instead).
    """
    _require_csrf(request)
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
    request: Request,
    tenant=Depends(_require_jwt),
    db: AsyncSession = Depends(get_async_db),
):
    """Replace an existing API key with a new value.

    The old key stops working **immediately** — there is no grace period.
    The new key can be retrieved again anytime via ``GET /auth/keys/new``.
    """
    _require_csrf(request)
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
    request: Request,
    tenant=Depends(_require_jwt),
    db: AsyncSession = Depends(get_async_db),
):
    """Soft-revoke one API key slot.

    The key value is retained in the database; only its active flag is set
    to ``False``.  Use ``/auth/keys/regenerate`` to get a working key again.
    """
    _require_csrf(request)
    service = TenantService(db)
    try:
        await service.revoke_key(tenant.id, payload.key_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return RevokeKeyResponse(key_type=payload.key_type)
