"""Tests for IdempotencyMiddleware.

Redis is mocked so tests run without a live Redis instance.
All tests patch both the dependency DB and middleware AsyncSessionLocal
so that test isolation is complete.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from main import app


class _SessionProxy:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
    def __call__(self):
        return self  # AsyncSessionLocal() -> returns self as ctx manager
    async def __aenter__(self) -> AsyncSession:
        return self._s
    async def __aexit__(self, *_) -> None:
        pass


@pytest_asyncio.fixture
async def idem_client(db_session: AsyncSession):
    app.dependency_overrides[get_async_db] = lambda: db_session
    from app.core import middleware as mw
    original = mw.AsyncSessionLocal
    mw.AsyncSessionLocal = _SessionProxy(db_session)
    transport = ASGITransport(app=app)
    from app.core.config import settings
    original_secure = settings.COOKIE_SECURE
    original_samesite = settings.COOKIE_SAMESITE
    settings.COOKIE_SECURE = False
    settings.COOKIE_SAMESITE = "lax"

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    settings.COOKIE_SECURE = original_secure
    settings.COOKIE_SAMESITE = original_samesite
    app.dependency_overrides.clear()
    mw.AsyncSessionLocal = original


def _make_redis_mock(cached_value=None):
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=cached_value)
    mock.setex = AsyncMock(return_value=True)
    return mock


async def _signup_and_create_key(client, email: str, key_type: str = "sk_test") -> str:
    """Helper: sign up a tenant, create one API key, and create a project. Return key value."""
    signup = await client.post(
        "/v1/auth/signup",
        json={"name": "IdemTest", "email": email, "password": "pass1234"},
    )
    access_token = client.cookies.get("access_token")
    create = await client.post(
        "/v1/auth/keys/create",
        json={"key_type": key_type},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return create.json()["value"]


async def _signup_and_create_key_with_project(client, email: str, key_type: str = "sk_test") -> dict:
    """Helper: sign up, create API key, create project, return headers dict."""
    key = await _signup_and_create_key(client, email, key_type)
    proj_resp = await client.post("/v1/projects/create", json={"name": "Test Project"})
    project_id = proj_resp.json()["id"]
    return {"X-API-Key": key, "X-Project-ID": project_id}


@pytest.mark.asyncio
async def test_first_request_with_idempotency_key_passes_through(idem_client):
    headers = await _signup_and_create_key_with_project(idem_client, "idem@example.com")
    headers["Idempotency-Key"] = "unique-key-abc-123"

    redis_mock = _make_redis_mock(cached_value=None)
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.post(
            "/v1/customers/all",
            json={"email": "cust@example.com", "name": "Customer"},
            headers=headers,
        )


@pytest.mark.asyncio
async def test_replayed_request_returns_cached_response(idem_client):
    """Second request with same key returns cached response without hitting handler."""
    headers = await _signup_and_create_key_with_project(idem_client, "idem2@example.com")
    headers["Idempotency-Key"] = "already-seen-key"

    cached_body = json.dumps({"id": "abc", "email": "cust@example.com"})
    cached_payload = json.dumps({"status_code": 201, "body": cached_body})

    redis_mock = _make_redis_mock(cached_value=cached_payload)
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.post(
            "/v1/customers/all",
            json={"email": "cust@example.com", "name": "Customer"},
            headers=headers,
        )

    assert resp.status_code == 201
    assert resp.headers.get("x-idempotency-replayed") == "true"
    # Handler was NOT called — setex should not have been called again
    redis_mock.setex.assert_not_called()


@pytest.mark.asyncio
async def test_get_request_bypasses_idempotency(idem_client):
    """GET requests must never be subject to idempotency checks."""
    headers = await _signup_and_create_key_with_project(idem_client, "idemget@example.com")
    headers["Idempotency-Key"] = "should-be-ignored"

    redis_mock = _make_redis_mock()
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.get(
            "/v1/customers/all",
            headers=headers,
        )

    assert resp.status_code == 200
    redis_mock.get.assert_not_called()


@pytest.mark.asyncio
async def test_missing_idempotency_key_passes_through(idem_client):
    """POST without Idempotency-Key must pass through silently (permissive mode)."""
    headers = await _signup_and_create_key_with_project(idem_client, "idemnokey@example.com")
    # No Idempotency-Key header

    redis_mock = _make_redis_mock()
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.post(
            "/v1/customers/all",
            json={"email": "nokey@example.com", "name": "NoKey"},
            headers=headers,
        )

    # Should proceed normally — Redis never consulted
    redis_mock.get.assert_not_called()
    redis_mock.setex.assert_not_called()
