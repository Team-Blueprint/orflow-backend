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
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    mw.AsyncSessionLocal = original


def _make_redis_mock(cached_value=None):
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=cached_value)
    mock.setex = AsyncMock(return_value=True)
    return mock


async def _signup_and_create_key(client, email: str, key_type: str = "sk_test") -> str:
    """Helper: sign up a tenant and create one API key, return key value."""
    signup = await client.post(
        "/v1/auth/signup",
        json={"name": "IdemTest", "email": email, "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]
    create = await client.post(
        "/v1/auth/keys/create",
        json={"key_type": key_type},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return create.json()["value"]


@pytest.mark.asyncio
async def test_first_request_with_idempotency_key_passes_through(idem_client):
    sk_test = await _signup_and_create_key(idem_client, "idem@example.com")

    redis_mock = _make_redis_mock(cached_value=None)
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.post(
            "/v1/customers/all",
            json={"email": "cust@example.com", "name": "Customer"},
            headers={
                "X-API-Key": sk_test,
                "Idempotency-Key": "unique-key-abc-123",
            },
        )

    # Handler ran — response should be non-cached
    assert "x-idempotency-replayed" not in resp.headers
    # Redis setex should have been called to cache the result
    redis_mock.setex.assert_called_once()


@pytest.mark.asyncio
async def test_replayed_request_returns_cached_response(idem_client):
    """Second request with same key returns cached response without hitting handler."""
    sk_test = await _signup_and_create_key(idem_client, "idem2@example.com")

    cached_body = json.dumps({"id": "abc", "email": "cust@example.com"})
    cached_payload = json.dumps({"status_code": 201, "body": cached_body})

    redis_mock = _make_redis_mock(cached_value=cached_payload)
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.post(
            "/v1/customers/all",
            json={"email": "cust@example.com", "name": "Customer"},
            headers={
                "X-API-Key": sk_test,
                "Idempotency-Key": "already-seen-key",
            },
        )

    assert resp.status_code == 201
    assert resp.headers.get("x-idempotency-replayed") == "true"
    # Handler was NOT called — setex should not have been called again
    redis_mock.setex.assert_not_called()


@pytest.mark.asyncio
async def test_get_request_bypasses_idempotency(idem_client):
    """GET requests must never be subject to idempotency checks."""
    sk_test = await _signup_and_create_key(idem_client, "idemget@example.com")

    redis_mock = _make_redis_mock()
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.get(
            "/v1/customers/all",
            headers={
                "X-API-Key": sk_test,
                "Idempotency-Key": "should-be-ignored",
            },
        )

    assert resp.status_code == 200
    redis_mock.get.assert_not_called()


@pytest.mark.asyncio
async def test_missing_idempotency_key_passes_through(idem_client):
    """POST without Idempotency-Key must pass through silently (permissive mode)."""
    sk_test = await _signup_and_create_key(idem_client, "idemnokey@example.com")

    redis_mock = _make_redis_mock()
    with patch("app.core.idempotency._redis_client", new=redis_mock):
        resp = await idem_client.post(
            "/v1/customers/all",
            json={"email": "nokey@example.com", "name": "NoKey"},
            headers={"X-API-Key": sk_test},
            # No Idempotency-Key header
        )

    # Should proceed normally — Redis never consulted
    redis_mock.get.assert_not_called()
    redis_mock.setex.assert_not_called()
