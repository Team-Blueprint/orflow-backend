import uuid
import base64
import hmac
import hashlib
import json
import pytest
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from main import app
from app.db.database import get_async_db
from app.core.config import settings
from app.tenants.models import Tenant
from app.customers.models import Customer
from app.invoices.models import Invoice, InvoiceStatus
from app.projects.models import Project
from app.webhooks.models import WebhookEvent, PaymentAttempt
from app.providers.base import PaymentStatus


def _safe(val):
    if val is None:
        return ""
    s = str(val)
    if s == "null":
        return ""
    return s


def generate_signature(payload: dict, secret: str, timestamp: str) -> str:
    data = payload.get("data") or {}
    merchant = data.get("merchant") or {}
    transaction = data.get("transaction") or {}

    event_type = _safe(payload.get("event_type"))
    request_id = _safe(payload.get("requestId"))
    user_id = _safe(merchant.get("userId"))
    wallet_id = _safe(merchant.get("walletId"))
    transaction_id = _safe(transaction.get("transactionId"))
    transaction_type = _safe(transaction.get("type"))
    transaction_time = _safe(transaction.get("time"))
    response_code = _safe(transaction.get("responseCode"))

    hashing_payload = (
        f"{event_type}:{request_id}:{user_id}:{wallet_id}:"
        f"{transaction_id}:{transaction_type}:{transaction_time}:"
        f"{response_code}:{timestamp}"
    )

    secret_bytes = secret.encode("utf-8")
    digest = hmac.new(secret_bytes, hashing_payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _make_tenant() -> Tenant:
    """Build a Tenant with all required fields for the new schema."""
    keys = Tenant.generate_all_keys()
    return Tenant(
        name="WebhookTest",
        email=f"wh_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="$2b$12$placeholder",  # not used in webhook tests
        **keys,
    )


@pytest.mark.asyncio
async def test_nomba_webhook(db_session: AsyncSession):
    settings.NOMBA_WEBHOOK_SECRET = "test_secret"

    app.dependency_overrides[get_async_db] = lambda: db_session

    tenant = _make_tenant()
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)

    project = Project(tenant_id=tenant.id, name="Test")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="wh@test.com", name="Test")
    db_session.add(customer)
    await db_session.commit()

    invoice = Invoice(
        tenant_id=tenant.id,
        project_id=project.id,
        customer_id=customer.id,
        status=InvoiceStatus.open,
        amount_due=1000,
        currency="USD"
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "event_type": "payment_success",
            "requestId": "req_123",
            "data": {
                "order": {
                    "orderReference": str(invoice.id),
                    "amount": 1000.00,
                    "currency": "USD",
                    "paymentMethod": "card_payment",
                    "cardLast4Digits": "8038",
                    "cardType": "Visa",
                },
                "transaction": {
                    "transactionId": "WEB-ONLINE_C-abc123",
                    "type": "online_checkout",
                    "transactionAmount": 1000.00,
                    "fee": 10.0,
                    "time": "2026-01-01T00:00:00Z",
                },
                "merchant": {
                    "userId": str(tenant.id),
                },
            }
        }
        payload_bytes = json.dumps(payload).encode('utf-8')
        nomba_timestamp = "2026-01-01T00:00:00Z"
        sig = generate_signature(payload, settings.NOMBA_WEBHOOK_SECRET, nomba_timestamp)

        # Test Invalid Signature
        response = await client.post(
            "/v1/webhooks/inbound/nomba",
            content=payload_bytes,
            headers={
                "nomba-signature": "invalid",
                "nomba-timestamp": nomba_timestamp,
            }
        )
        assert response.status_code == 401

        # Test Valid Signature
        response = await client.post(
            "/v1/webhooks/inbound/nomba",
            content=payload_bytes,
            headers={
                "nomba-signature": sig,
                "nomba-timestamp": nomba_timestamp,
                "nomba-event-id": "evt_123",
                "Content-Type": "application/json",
            }
        )
        assert response.status_code == 200

        # Verify invoice is paid
        await db_session.refresh(invoice)
        assert invoice.status == InvoiceStatus.paid

        # Verify PaymentAttempt
        stmt = select(PaymentAttempt).where(PaymentAttempt.invoice_id == invoice.id)
        result = await db_session.execute(stmt)
        attempt = result.scalar_one()
        assert attempt.status == PaymentStatus.success

        # Test Idempotency — replaying the same event must not fail
        response = await client.post(
            "/v1/webhooks/inbound/nomba",
            content=payload_bytes,
            headers={
                "nomba-signature": sig,
                "nomba-timestamp": nomba_timestamp,
                "nomba-event-id": "evt_123",
                "Content-Type": "application/json",
            }
        )
        assert response.status_code == 200

    app.dependency_overrides.clear()
