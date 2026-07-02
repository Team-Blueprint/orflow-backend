"""Tests for RateLimitMiddleware.

Redis is mocked so tests run without a live Redis instance.
All tests patch both the DB dependency and middleware AsyncSessionLocal.
"""
from __future__ import annotations

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
        return self  # AsyncSessionLocal() returns self
    async def __aenter__(self) -> AsyncSession:
        return self._s
    async def __aexit__(self, *_) -> None:
        pass


@pytest_asyncio.fixture
async def rl_client(db_session: AsyncSession):
    app.dependency_overrides[get_async_db] = lambda: db_session
    from app.core import middleware as mw
    original = mw.AsyncSessionLocal
    mw.AsyncSessionLocal = _SessionProxy(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    mw.AsyncSessionLocal = original


def _make_redis_mock(count: int):
    """Return a mock Redis that always reports *count* for INCR."""
    mock = AsyncMock()
    mock.incr = AsyncMock(return_value=count)
    mock.expire = AsyncMock(return_value=True)
    mock.get = AsyncMock(return_value="60")   # cached limit = 60
    mock.setex = AsyncMock(return_value=True)
    return mock


async def _signup_and_create_key(client, email: str) -> str:
    signup = await client.post(
        "/v1/auth/signup",
        json={"name": "RLTest", "email": email, "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]
    create = await client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return create.json()["value"]


@pytest.mark.asyncio
async def test_request_under_limit_passes(rl_client):
    sk_test = await _signup_and_create_key(rl_client, "rl@example.com")

    with patch("app.core.rate_limit._redis_client", new=_make_redis_mock(count=1)):
        resp = await rl_client.get("/v1/customers/all", headers={"X-API-Key": sk_test})

    assert resp.status_code == 200
    assert "x-ratelimit-limit" in resp.headers


@pytest.mark.asyncio
async def test_request_over_limit_returns_429(rl_client):
    sk_test = await _signup_and_create_key(rl_client, "rl2@example.com")

    with patch(
        "app.core.rate_limit._redis_client",
        new=_make_redis_mock(count=61),  # over default limit of 60
    ):
        resp = await rl_client.get("/v1/customers/all", headers={"X-API-Key": sk_test})

    assert resp.status_code == 429
    assert "retry-after" in resp.headers


@pytest.mark.asyncio
async def test_health_path_bypasses_rate_limit(rl_client):
    """The health check at / must never be rate-limited."""
    mock_redis = _make_redis_mock(count=9999)
    with patch("app.core.rate_limit._redis_client", new=mock_redis):
        resp = await rl_client.get("/")

    assert resp.status_code == 200
    mock_redis.incr.assert_not_called()


@pytest.mark.asyncio
async def test_auth_path_bypasses_rate_limit(rl_client):
    """Auth endpoints must never be rate-limited."""
    mock_redis = _make_redis_mock(count=9999)
    with patch("app.core.rate_limit._redis_client", new=mock_redis):
        resp = await rl_client.post(
            "/v1/auth/signup",
            json={"name": "BypassTest", "email": "bypass@example.com", "password": "pass1234"},
        )

    assert resp.status_code == 201
    mock_redis.incr.assert_not_called()
