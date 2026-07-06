"""Google OAuth integration using authlib."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
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

_STATE_COOKIE = "google_oauth_state"
_STATE_MAX_AGE = 600  # 10 minutes


def _oauth_client() -> _OAuth2Client:
    from authlib.integrations.httpx_client import OAuth2Client

    return OAuth2Client(
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )


def google_login_redirect() -> RedirectResponse:
    """Build a RedirectResponse to Google's consent screen.

    Sets a state cookie for CSRF protection.
    """
    from authlib.common.security import generate_token

    client = _oauth_client()
    state = generate_token()

    authorize_url, _ = client.create_authorization_url(
        url=_GOOGLE_AUTHORIZE_URL,
        state=state,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
        scope=_SCOPE,
    )

    redirect = RedirectResponse(
        url=authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    redirect.set_cookie(
        key=_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE.lower(),
        max_age=_STATE_MAX_AGE,
        path="/v1/auth/google/callback",
    )
    return redirect


def _verify_state(request: Request) -> None:
    """Validate state parameter to prevent CSRF on the callback."""
    expected = request.cookies.get(_STATE_COOKIE)
    actual = request.query_params.get("state")
    if not expected or not actual or expected != actual:
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
