import uuid
from datetime import datetime, timezone, timedelta
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.context import current_project_id, current_tenant_id
from app.invoices.models import Invoice, InvoiceStatus
from app.subscriptions.models import Subscription, SubscriptionStatus, SubscriptionType
from httpx._transports.asgi import ASGITransport
import pytest_asyncio
from app.db.database import get_async_db
from app.providers.deps import get_payment_provider
from app.providers.nomba import NombaProvider
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

from unittest.mock import AsyncMock, patch

@pytest_asyncio.fixture(autouse=True)
def mock_tasks():
    with patch("app.subscriptions.service.schedule_subscription_expiry", new_callable=AsyncMock) as m1, \
         patch("app.worker.tasks.enqueue_webhook_delivery", new_callable=AsyncMock) as m2:
        yield m1, m2

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
        json={"name": "Test", "email": "test_install@example.com", "password": "pass1234"},
    )
    token = auth_client.cookies.get("access_token")
    
    resp = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}"}
    )
    sk_test = resp.json()["value"]

    proj_resp = await auth_client.post("/v1/projects/create", json={"name": "Test Project"})
    project_id = proj_resp.json()["id"]
    return {"X-API-Key": sk_test, "X-Project-ID": project_id}

@pytest.fixture
async def customer_and_pm(auth_client: AsyncClient, auth_headers: dict):
    # Create customer
    c_resp = await auth_client.post(
        "/v1/customers/create",
        json={"email": "cust@installments.com", "name": "Installment Cust"},
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
async def installment_plan(auth_client: AsyncClient, auth_headers: dict):
    resp = await auth_client.post(
        "/v1/plans/create",
        json={
            "name": "4 Month Installment",
            "amount": 250.0, # $250
            "currency": "USD",
            "interval": "monthly",
            "interval_count": 1,
            "installments_count": 4
        },
        headers=auth_headers
    )
    resp.raise_for_status()
    return resp.json()["id"]

@pytest.mark.asyncio
async def test_installment_subscription_creation(
    auth_client: AsyncClient, 
    auth_headers: dict, 
    customer_and_pm: dict, 
    installment_plan: str,
    db_session: AsyncSession
):
    # Subscribe
    sub_resp = await auth_client.post(
        "/v1/subscriptions/create",
        json={
            "customer_id": customer_and_pm["customer_id"],
            "plan_id": installment_plan,
            "payment_method_id": customer_and_pm["payment_method_id"]
        },
        headers=auth_headers
    )
    assert sub_resp.status_code == 201
    sub_data = sub_resp.json()["subscription"]
    assert sub_data["type"] == "installment"
    
    # Verify N invoices created
    sub_id = sub_data["id"]
    invoices_res = await db_session.execute(
        select(Invoice).where(Invoice.subscription_id == uuid.UUID(sub_id)).order_by(Invoice.due_date)
    )
    invoices = invoices_res.scalars().all()
    
    assert len(invoices) == 4
    for i, inv in enumerate(invoices):
        assert inv.status == InvoiceStatus.open
        # Verify due dates are staggered
        if i > 0:
            assert invoices[i].due_date > invoices[i-1].due_date

@pytest.mark.asyncio
async def test_process_due_installment_invoices(
    auth_client: AsyncClient, 
    auth_headers: dict, 
    customer_and_pm: dict, 
    installment_plan: str,
    db_session: AsyncSession,
    provider: NombaProvider
):
    # Subscribe
    sub_resp = await auth_client.post(
        "/v1/subscriptions/create",
        json={
            "customer_id": customer_and_pm["customer_id"],
            "plan_id": installment_plan,
            "payment_method_id": customer_and_pm["payment_method_id"]
        },
        headers=auth_headers
    )
    sub_id = sub_resp.json()["subscription"]["id"]
    
    # Fetch invoices
    invoices_res = await db_session.execute(
        select(Invoice).where(Invoice.subscription_id == uuid.UUID(sub_id)).order_by(Invoice.due_date)
    )
    invoices = invoices_res.scalars().all()
    
    # Manually transition subscription to active and mark first invoice as paid
    from app.subscriptions.state_machine import transition_subscription
    sub = await db_session.get(Subscription, uuid.UUID(sub_id))
    token = current_tenant_id.set(sub.tenant_id)
    proj_token = current_project_id.set(sub.project_id)
    try:
        await transition_subscription(db_session, sub, SubscriptionStatus.active)
    finally:
        current_tenant_id.reset(token)
        current_project_id.reset(proj_token)
    
    invoices[0].status = InvoiceStatus.paid
    
    # Shift the second invoice's due_date to past
    invoices[1].due_date = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()
    
    # Run worker
    from app.worker.installments import process_due_installment_invoices
    with patch("app.worker.installments.AsyncSessionLocal", _SessionProxy(db_session)), \
         patch("app.worker.installments.get_payment_provider", return_value=provider):
        await process_due_installment_invoices({})
    
    # Verify second invoice is paid
    await db_session.refresh(invoices[1])
    assert invoices[1].status == InvoiceStatus.paid

@pytest.mark.asyncio
async def test_installment_completion(
    auth_client: AsyncClient, 
    auth_headers: dict, 
    customer_and_pm: dict, 
    installment_plan: str,
    db_session: AsyncSession
):
    # Subscribe
    sub_resp = await auth_client.post(
        "/v1/subscriptions/create",
        json={
            "customer_id": customer_and_pm["customer_id"],
            "plan_id": installment_plan,
            "payment_method_id": customer_and_pm["payment_method_id"]
        },
        headers=auth_headers
    )
    sub_id = uuid.UUID(sub_resp.json()["subscription"]["id"])
    
    # Fetch invoices
    invoices_res = await db_session.execute(
        select(Invoice).where(Invoice.subscription_id == sub_id)
    )
    invoices = invoices_res.scalars().all()
    
    # Transition to active
    from app.subscriptions.state_machine import transition_subscription
    sub = await db_session.get(Subscription, sub_id)
    token = current_tenant_id.set(sub.tenant_id)
    proj_token = current_project_id.set(sub.project_id)
    try:
        await transition_subscription(db_session, sub, SubscriptionStatus.active)
    finally:
        current_tenant_id.reset(token)
        current_project_id.reset(proj_token)
    
    # Pay all but one
    for inv in invoices[:-1]:
        inv.status = InvoiceStatus.paid
    await db_session.commit()
    
    # Use transition_invoice for the last one to trigger completion logic
    from app.invoices.state_machine import transition_invoice
    token = current_tenant_id.set(sub.tenant_id)
    proj_token = current_project_id.set(sub.project_id)
    try:
        await transition_invoice(db_session, invoices[-1], InvoiceStatus.paid)
    finally:
        current_tenant_id.reset(token)
        current_project_id.reset(proj_token)
    
    # Verify subscription is completed
    sub = await db_session.get(Subscription, sub_id)
    assert sub.status == SubscriptionStatus.completed
