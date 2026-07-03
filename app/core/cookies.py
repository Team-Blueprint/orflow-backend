"""Cookie utility functions for auth tokens and CSRF protection."""
from __future__ import annotations

import secrets
from typing import Literal

from fastapi import Response
from app.core.config import settings


CookieName = Literal["access_token", "refresh_token", "csrf_token"]


_COOKIE_PATHS: dict[CookieName, str] = {
    "access_token": "/v1",
    "refresh_token": "/v1/auth/refresh",
    "csrf_token": "/",
}


_COOKIE_MAX_AGES: dict[CookieName, int] = {
    "access_token": settings.JWT_EXPIRE_MINUTES * 60,
    "refresh_token": settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60,
    "csrf_token": settings.JWT_EXPIRE_MINUTES * 60,
}


def _get_cookie_options(name: CookieName) -> dict:
    """Get common cookie options for the given cookie name."""
    return {
        "httponly": name != "csrf_token",
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE.lower(),
        "path": _COOKIE_PATHS[name],
        "max_age": _COOKIE_MAX_AGES[name],
    }


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    csrf_token: str | None = None,
) -> None:
    """Set access token, refresh token, and CSRF cookies on the response."""
    if csrf_token is None:
        csrf_token = secrets.token_hex(32)

    access_opts = _get_cookie_options("access_token")
    refresh_opts = _get_cookie_options("refresh_token")
    csrf_opts = _get_cookie_options("csrf_token")

    response.set_cookie(key="access_token", value=access_token, **access_opts)
    response.set_cookie(key="refresh_token", value=refresh_token, **refresh_opts)
    response.set_cookie(key="csrf_token", value=csrf_token, **csrf_opts)


def clear_auth_cookies(response: Response) -> None:
    """Clear all auth cookies."""
    for name in ("access_token", "refresh_token", "csrf_token"):
        opts = _get_cookie_options(name)
        response.delete_cookie(key=name, path=opts["path"], secure=opts["secure"], samesite=opts["samesite"])


def generate_csrf_token() -> str:
    """Generate a new CSRF token."""
    return secrets.token_hex(32)