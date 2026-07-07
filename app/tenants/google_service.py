"""Google OAuth integration using authlib."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
import jwt
from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.tenants.service import TenantService

if TYPE_CHECKING:
    from authlib.common.security import generate_token as _generate_token
    from authlib.integrations.httpx_client import OAuth2Client as _OAuth2Client


_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_SCOPE = "openid email profile"

_STATE_MAX_AGE_SECONDS = 600  # 10 minutes


def _oauth_client() -> _OAuth2Client:
    from authlib.integrations.httpx_client import OAuth2Client

    return OAuth2Client(
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )


def google_login_redirect() -> RedirectResponse:
    """Build a RedirectResponse to Google's consent screen.

    Uses a self-contained JWT-signed state instead of a cookie, so the
    state survives cross-domain redirects without depending on the cookie
    domain matching the callback domain.
    """
    from authlib.common.security import generate_token

    client = _oauth_client()
    nonce = generate_token()

    state = jwt.encode(
        {
            "nonce": nonce,
            "exp": datetime.now(timezone.utc) + timedelta(seconds=_STATE_MAX_AGE_SECONDS),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )

    authorize_url, _ = client.create_authorization_url(
        url=_GOOGLE_AUTHORIZE_URL,
        state=state,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
        scope=_SCOPE,
    )

    return RedirectResponse(
        url=authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


def _verify_state(request: Request) -> None:
    """Validate state parameter to prevent CSRF on the callback.

    The state is a signed JWT, so verification is stateless — no cookie
    or server-side storage needed.
    """
    actual = request.query_params.get("state")
    if not actual:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid OAuth state parameter — possible CSRF attack",
        )
    try:
        jwt.decode(
            actual,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OAuth state expired — please try again",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid OAuth state parameter — possible CSRF attack",
        )


async def handle_google_callback(
    request: Request,
    db: AsyncSession,
) -> tuple[dict, dict]:
    """Complete the Google OAuth flow.

    1. Verify the ``state`` parameter (CSRF protection).
    2. Exchange the ``code`` for an access token.
    3. Fetch the user's Google profile.
    4. Create or link a Tenant via ``TenantService.google_auth``.
    5. Return the tenant profile and JWT token pair.
    """
    _verify_state(request)

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'code' query parameter from Google",
        )

    client = _oauth_client()

    try:
        token_data = await asyncio.to_thread(
            client.fetch_token,
            url=_GOOGLE_TOKEN_URL,
            code=code,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google token exchange failed: {exc}",
        )

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No access_token in Google response",
        )

    async with httpx.AsyncClient() as hc:
        userinfo_resp = await hc.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if not userinfo_resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to fetch Google user info",
        )

    google_user = userinfo_resp.json()
    google_sub: str | None = google_user.get("sub")
    email: str | None = google_user.get("email")
    name: str | None = google_user.get("name", email or "Unknown")

    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google profile missing required fields (sub, email)",
        )

    tenant, tokens = await TenantService(db).google_auth(
        google_sub=google_sub,
        email=email,
        name=name,
    )

    return tenant, tokens
