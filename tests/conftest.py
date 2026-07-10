"""Shared test fixtures for the Nomba provider adapter.

A ``MockRouter`` wired into an ``httpx.MockTransport`` lets us drive the adapter
without any network access. Every request is recorded, and the OAuth token
endpoint is served automatically so individual tests only stub the business
endpoints they exercise.
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base

# Import every model so Base.metadata is fully populated before create_all.
import app.tenants.models  # noqa: F401,E402
import app.customers.models  # noqa: F401,E402
import app.plans.models  # noqa: F401,E402
import app.payment_methods.models  # noqa: F401,E402
import app.projects.models  # noqa: F401,E402
import app.projects.keys_models  # noqa: F401,E402
import app.subscriptions.models  # noqa: F401,E402
import app.invoices.models  # noqa: F401,E402
import app.audit.models  # noqa: F401,E402
import app.webhooks.models  # noqa: F401,E402

from app.core.config import Settings
from app.providers.nomba import NombaProvider


# ── Redis no-op mocks (autouse) ───────────────────────────────────────────────
# All tests run without a live Redis. Tests that want specific Redis behaviour
# override ``app.core.rate_limit._redis_client`` or
# ``app.core.idempotency._redis_client`` with their own mock inside the test.

from unittest.mock import AsyncMock


def _make_noop_redis():
    """A Redis mock that always reports count=0, cache miss, and succeeds on writes."""
    m = AsyncMock()
    m.incr = AsyncMock(return_value=0)
    m.expire = AsyncMock(return_value=True)
    m.get = AsyncMock(return_value=None)      # cache miss → fall through
    m.setex = AsyncMock(return_value=True)
    return m


@pytest.fixture(autouse=True)
def _mock_rate_limit_redis():
    """Replace the rate-limit Redis client for every test."""
    import app.core.rate_limit as rl
    original = rl._redis_client
    rl._redis_client = _make_noop_redis()
    yield
    rl._redis_client = original


@pytest.fixture(autouse=True)
def _mock_idempotency_redis():
    """Replace the idempotency Redis client for every test."""
    import app.core.idempotency as idem
    original = idem._redis_client
    idem._redis_client = _make_noop_redis()
    yield
    idem._redis_client = original


class MockRouter:
    """Programmable response router for ``httpx.MockTransport``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}
        # Default: hand out a token valid for an hour.
        self.set(
            "POST",
            "/v1/auth/token/issue",
            {
                "code": "00",
                "description": "Success",
                "data": {
                    "businessId": "biz-1",
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "expiresAt": "2999-01-01T00:00:00Z",
                },
            },
        )

    def set(self, method: str, path: str, body: dict, status_code: int = 200) -> None:
        self._routes[(method.upper(), path)] = lambda _req: httpx.Response(
            status_code, json=body
        )

    def set_handler(
        self, method: str, path: str, handler: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        self._routes[(method.upper(), path)] = handler

    def auth_calls(self) -> int:
        return sum(
            1 for r in self.requests if r.url.path == "/v1/auth/token/issue"
        )

    def last_body(self) -> dict:
        return json.loads(self.requests[-1].content)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self._routes.get((request.method.upper(), request.url.path))
        if handler is None:
            return httpx.Response(404, json={"code": "99", "description": "no route"})
        return handler(request)


@pytest.fixture
def router() -> MockRouter:
    return MockRouter()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        NOMBA_BASE_URL="https://api.test.nomba.com",
        NOMBA_CLIENT_ID="client-id",
        NOMBA_CLIENT_SECRET="client-secret",
        NOMBA_ACCOUNT_ID="account-id",
        NOMBA_CALLBACK_URL="https://merchant.test/callback",
    )


@pytest.fixture
async def provider(router: MockRouter, settings: Settings) -> NombaProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    try:
        yield NombaProvider(client, settings)
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """A fresh in-memory SQLite database per test.

    A ``StaticPool`` keeps the single in-memory connection alive across the
    session's checkouts. The state-machine transition functions take an explicit
    ``tenant_id`` off the entity, so no tenant context/middleware is needed here.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session

    await engine.dispose()
