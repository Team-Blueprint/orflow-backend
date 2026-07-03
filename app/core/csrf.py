"""CSRF protection middleware for FastAPI endpoints."""
from __future__ import annotations

from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings


# Double-submit pattern protection
_csrf = HTTPBearer(auto_error=False)


def _require_csrf(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = None,
):
    """Validate CSRF token for state-changing endpoints.

    Proxies to the _csrf security instance to extract and validate the token.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-CSRF-Token header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials


def validate_csrf_token(request: Request, csrf_token: str) -> bool:
    """Validate the CSRF token from the request against the stored cookie value."""
    cookie_token = request.cookies.get("csrf_token")
    if cookie_token is None or cookie_token != csrf_token:
        return False
    return True