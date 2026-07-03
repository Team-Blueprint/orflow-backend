import uuid
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient

from app.subscriptions.models import Subscription, SubscriptionStatus
from app.invoices.models import Invoice

class _SessionProxy:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
    def __call__(self):
        return self  
    async def __aenter__(self) -> AsyncSession:
        return self._s
    async def __aexit__(self, *_) -> None:
        pass

from unittest.mock import AsyncMock, patch
import pytest_asyncio
from httpx._transports.asgi import ASGITransport
from app.db.database import get_async_db
from app.providers.deps import get_payment_provider
from app.providers.nomba import NombaProvider
from main import app

@pytest_asyncio.fixture(autouse=True)
def mock_tasks():
    with patch("app.subscriptions.service.schedule_subscription_expiry", new_callable=AsyncMock) as m1, \
         patch("app.worker.tasks.enqueue_webhook_delivery", new_callable=AsyncMock) as m2, \
         patch("app.subscriptions.service.schedule_trial_activation", new_callable=AsyncMock) as m3:
        yield m1, m2, m3

@pytest_asyncio.fixture
async def auth_client(db_session: AsyncSession, provider: NombaProvider, router):
    router.set("POST", "/v1/checkout/order", {"code": "00", "description": "Success", "data": {"checkoutLink": "https://test.nomba.com/pay/123", "orderReference": "ord-123"}})
    router.set("POST", "/v1/checkout/tokenized-card-payment", {"code": "00", "description": "Success", "data": {"status": True}})
    
    app.dependency_overrides[get_async_db] = lambda: db_session
    app.dependency_overrides[get_payment_provider] = lambda: provider
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

@pytest.fixture
async def auth_headers(auth_client: AsyncClient):
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Test", "email": "test_trials@example.com", "password": "pass1234"},
    )
    token = auth_client.cookies.get("access_token")
    
    resp = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}"}
    )
    sk_test = resp.json()["value"]
    return {"X-API-Key": sk_test}

@pytest.fixture
async def customer_and_pm(auth_client: AsyncClient, auth_headers: dict):
    # Create customer
    c_resp = await auth_client.post(
        "/v1/customers/create",
        json={"email": "cust@trials.com", "name": "Trial Cust"},
        headers=auth_headers
    )
    c_resp.raise_for_status()
    customer_id = c_resp.json()["id"]
    
    pm_resp = await auth_client.post(
        "/v1/payment-methods/create",
        json={"customer_id": customer_id, "type": "card", "provider_token": "tok_visa"},
        headers=auth_headers
    )
    pm_resp.raise_for_status()
    pm_id = pm_resp.json()["id"]
    
    return {"customer_id": customer_id, "payment_method_id": pm_id}

@pytest.fixture
async def trial_plan(auth_client: AsyncClient, auth_headers: dict):
    resp = await auth_client.post(
        "/v1/plans/create",
        json={
            "name": "Trial Plan",
            "amount": 10.0, 
            "currency": "USD",
            "interval": "monthly",
            "interval_count": 1,
            "trial_period_days": 14
        },
        headers=auth_headers
    )
    resp.raise_for_status()
    return resp.json()["id"]

@pytest.mark.asyncio
async def test_trial_subscription_lifecycle(
    auth_client: AsyncClient, 
    auth_headers: dict, 
    customer_and_pm: dict, 
    trial_plan: str,
    db_session: AsyncSession
):
    # Subscribe
    sub_resp = await auth_client.post(
        "/v1/subscriptions/create",
        json={
            "customer_id": customer_and_pm["customer_id"],
            "plan_id": trial_plan,
            "payment_method_id": customer_and_pm["payment_method_id"]
        },
        headers=auth_headers
    )
    assert sub_resp.status_code == 201
    sub_id = sub_resp.json()["subscription"]["id"]
    
    # Verify initial status is trialing
    sub = await db_session.get(Subscription, uuid.UUID(sub_id))
    assert sub.status == SubscriptionStatus.trialing
    assert sub.trial_end is not None
    
    # Run trial activation job
    from app.worker.tasks import activate_trial_subscription_job
    with patch("app.worker.tasks.AsyncSessionLocal", _SessionProxy(db_session)):
        await activate_trial_subscription_job({}, sub.id)
    
    # Verify subscription is now active
    await db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.active
    
    # Invoices are NOT generated by the trial activation job directly.
    # The billing cycle worker will pick it up since current_period_end is passed.
    
@pytest.mark.asyncio
async def test_trial_activation_without_payment_method(
    auth_client: AsyncClient, 
    auth_headers: dict, 
    trial_plan: str,
    db_session: AsyncSession
):
    # Create customer without PM
    c_resp = await auth_client.post(
        "/v1/customers/create",
        json={"email": "notrialpm@example.com", "name": "No PM"},
        headers=auth_headers
    )
    customer_id = c_resp.json()["id"]
    
    # Subscribe
    sub_resp = await auth_client.post(
        "/v1/subscriptions/create",
        json={
            "customer_id": customer_id,
            "plan_id": trial_plan,
        },
        headers=auth_headers
    )
    sub_id = sub_resp.json()["subscription"]["id"]
    
    # Verify initial status is trialing
    sub = await db_session.get(Subscription, uuid.UUID(sub_id))
    assert sub.status == SubscriptionStatus.trialing
    
    # Run trial activation job
    from app.worker.tasks import activate_trial_subscription_job
    with patch("app.worker.tasks.AsyncSessionLocal", _SessionProxy(db_session)):
        await activate_trial_subscription_job({}, sub.id)
    
    # Verify subscription is paused
    await db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.paused
