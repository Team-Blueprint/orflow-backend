import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from main import app
from app.core.context import current_tenant_id
from app.db.database import get_async_db
from app.providers.deps import get_payment_provider
from app.tenants.models import Tenant
from app.customers.models import Customer
from app.plans.models import Plan, PlanInterval
from app.subscriptions.models import Subscription, SubscriptionStatus

# We need a mock provider that implements initiate_checkout
from app.providers.base import PaymentProviderAdapter, CheckoutSession, ChargeResult, TransferResult, TransactionStatus

class DummyProvider(PaymentProviderAdapter):
    async def initiate_checkout(self, **kwargs) -> CheckoutSession:
        return CheckoutSession(
            checkout_link="https://checkout.test",
            order_reference="test-order-ref"
        )
    async def charge_tokenized_card(self, **kwargs) -> ChargeResult:
        pass
    async def transfer(self, **kwargs) -> TransferResult:
        pass
    async def verify_transaction(self, **kwargs) -> TransactionStatus:
        pass

@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession):
    # Override dependencies
    app.dependency_overrides[get_async_db] = lambda: db_session
    app.dependency_overrides[get_payment_provider] = lambda: DummyProvider()
    
    # We bypass middleware auth by manually setting the context var, 
    # but middleware runs first and checks X-API-Key against its own session.
    # To bypass easily for router tests, we can just patch current_tenant_id inside the test, 
    # but the middleware will still fail if no key.
    # Actually, we can just insert a tenant into the test DB and use its key.
    keys = Tenant.generate_all_keys()
    tenant = Tenant(
        name="Test Tenant",
        email="router_test@example.com",
        hashed_password="$2b$12$placeholder",
        **keys,
    )
    _test_key = tenant.sk_test
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    
    # Patch middleware's AsyncSessionLocal to use our test session
    from app.core import middleware as app_middleware
    class DummySessionLocal:
        async def __aenter__(self): return db_session
        async def __aexit__(self, exc_type, exc_val, exc_tb): pass
        def __call__(self): return self

    original_session_local = app_middleware.AsyncSessionLocal
    app_middleware.AsyncSessionLocal = DummySessionLocal
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"X-API-Key": _test_key})
        yield client, tenant
        
    app.dependency_overrides.clear()
    app_middleware.AsyncSessionLocal = original_session_local

@pytest.mark.asyncio
async def test_create_subscription_immediate_charge(api_client, db_session: AsyncSession):
    client, tenant = api_client
    
    # Create customer and plan
    customer = Customer(tenant_id=tenant.id, email="test@test.com", name="Test")
    plan = Plan(tenant_id=tenant.id, name="Pro", amount=1000, currency="USD", interval=PlanInterval.month)
    db_session.add(customer)
    db_session.add(plan)
    await db_session.commit()

    response = await client.post("/v1/subscriptions/create", json={
        "customer_id": str(customer.id),
        "plan_id": str(plan.id),
    })

    assert response.status_code == 201
    data = response.json()
    assert data["subscription"]["status"] == "incomplete"
    assert data["checkout_link"] == "https://checkout.test"
    
    # Verify DB
    result = await db_session.execute(select(Subscription).where(Subscription.id == uuid.UUID(data["subscription"]["id"])))
    sub = result.scalar_one_or_none()
    assert sub is not None
    assert sub.status == SubscriptionStatus.incomplete
