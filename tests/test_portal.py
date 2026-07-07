import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from app.db.database import get_async_db
from main import app
from app.customers.models import Customer
from app.portal.service import hash_pin, issue_portal_token

@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    app.dependency_overrides[get_async_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()

@pytest_asyncio.fixture
async def portal_customer(db_session: AsyncSession) -> Customer:
    from app.tenants.models import Tenant
    # Create tenant first
    tenant = Tenant(
        name="Portal Tenant",
        email="portaltenant@example.com",
        hashed_password="pw"
    )
    db_session.add(tenant)
    await db_session.flush()
    
    customer = Customer(
        tenant_id=tenant.id,
        email="portal@example.com",
        name="Portal User",
        portal_token_slug="test_slug_123",
        portal_pin_hash=hash_pin("123456")
    )
    db_session.add(customer)
    await db_session.commit()
    await db_session.refresh(customer)
    return customer

@pytest.mark.asyncio
async def test_verify_access_success(client: AsyncClient, portal_customer: Customer):
    resp = await client.post("/v1/portal/verify-access", json={
        "token_slug": "test_slug_123",
        "pin": "123456"
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()

@pytest.mark.asyncio
async def test_verify_access_invalid_pin(client: AsyncClient, portal_customer: Customer):
    resp = await client.post("/v1/portal/verify-access", json={
        "token_slug": "test_slug_123",
        "pin": "wrongpin"
    })
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_verify_access_invalid_slug(client: AsyncClient, portal_customer: Customer):
    resp = await client.post("/v1/portal/verify-access", json={
        "token_slug": "wrong_slug",
        "pin": "123456"
    })
    assert resp.status_code == 401

@pytest.mark.asyncio
async def test_update_pin_success(client: AsyncClient, portal_customer: Customer, db_session: AsyncSession):
    token = issue_portal_token(portal_customer.id)
    resp = await client.post("/v1/portal/update-pin", json={
        "current_pin": "123456",
        "new_pin": "654321"
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    
    # Verify new pin works
    resp2 = await client.post("/v1/portal/verify-access", json={
        "token_slug": "test_slug_123",
        "pin": "654321"
    })
    assert resp2.status_code == 200

@pytest.mark.asyncio
async def test_update_pin_wrong_current(client: AsyncClient, portal_customer: Customer):
    token = issue_portal_token(portal_customer.id)
    resp = await client.post("/v1/portal/update-pin", json={
        "current_pin": "wrongpin",
        "new_pin": "654321"
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400

@pytest.mark.asyncio
async def test_update_pin_unauthorized(client: AsyncClient, portal_customer: Customer):
    resp = await client.post("/v1/portal/update-pin", json={
        "current_pin": "123456",
        "new_pin": "654321"
    })
    assert resp.status_code == 401
