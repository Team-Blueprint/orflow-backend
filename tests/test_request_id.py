"""Tests for the RequestIDMiddleware.

- Response always has X-Request-ID header.
- Client-provided X-Request-ID is echoed back unchanged.
- Server-generated ID is a valid UUID.
"""
from __future__ import annotations

import uuid

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
        return self
    async def __aenter__(self) -> AsyncSession:
        return self._s
    async def __aexit__(self, *_) -> None:
        pass


@pytest_asyncio.fixture
async def rid_client(db_session: AsyncSession):
    app.dependency_overrides[get_async_db] = lambda: db_session
    from app.core import middleware as mw
    original = mw.AsyncSessionLocal
    mw.AsyncSessionLocal = _SessionProxy(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    mw.AsyncSessionLocal = original


@pytest.mark.asyncio
async def test_response_always_has_request_id(rid_client):
    resp = await rid_client.get("/")
    assert "x-request-id" in resp.headers
    uuid.UUID(resp.headers["x-request-id"])  # must be a valid UUID


@pytest.mark.asyncio
async def test_client_provided_request_id_is_echoed(rid_client):
    custom_id = str(uuid.uuid4())
    resp = await rid_client.get("/", headers={"X-Request-ID": custom_id})
    assert resp.headers["x-request-id"] == custom_id


@pytest.mark.asyncio
async def test_server_generates_unique_ids(rid_client):
    ids = set()
    for _ in range(5):
        resp = await rid_client.get("/")
        ids.add(resp.headers["x-request-id"])
    assert len(ids) == 5  # all unique
