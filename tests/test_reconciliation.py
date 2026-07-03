import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from main import app
from app.db.database import get_async_db
from app.core.config import settings
from app.core.context import current_tenant_id, current_key_type
from app.tenants.models import Tenant
from app.customers.models import Customer
from app.invoices.models import Invoice, InvoiceStatus
from app.projects.models import Project
from app.webhooks.models import PaymentAttempt
from app.providers.base import PaymentStatus, ProviderError
from app.reconciliation.models import ReconciliationDiscrepancy
from app.reconciliation.service import ReconciliationService


def _make_tenant() -> Tenant:
    keys = Tenant.generate_all_keys()
    return Tenant(
        name="ReconTest",
        email=f"recon_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="$2b$12$placeholder",
        is_active=True,
        **keys,
    )


_RANGE_START = datetime(2026, 6, 30, tzinfo=timezone.utc)


async def _seed_attempt(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    provider_reference: str,
    status: PaymentStatus = PaymentStatus.success,
    amount_due: int = 5000,
    **overrides,
) -> tuple[Invoice, PaymentAttempt]:
    invoice = Invoice(
        tenant_id=tenant_id,
        customer_id=customer_id,
        status=InvoiceStatus.paid,
        amount_due=amount_due,
        currency="NGN",
        created_at=_RANGE_START,
    )
    db.add(invoice)
    await db.flush()

    attempt = PaymentAttempt(
        tenant_id=tenant_id,
        invoice_id=invoice.id,
        status=status,
        provider_reference=provider_reference,
        created_at=_RANGE_START,
        **overrides,
    )
    db.add(attempt)
    await db.flush()
    return invoice, attempt


_TX = {"id": "nomba-1", "merchantTxRef": "ref-1", "status": "SUCCESS", "amount": 50.00, "timeCreated": "2026-06-30T12:00:00Z"}


# ── Service Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_in_ours(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[_TX])

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(db_session, provider, date_from, date_to)

    assert summary.total_nomba == 1
    assert summary.total_ours == 0
    assert summary.matched == 0
    assert summary.discrepancies == 1

    discs = (await db_session.execute(select(ReconciliationDiscrepancy))).scalars().all()
    assert len(discs) == 1
    assert discs[0].discrepancy_type.value == "missing_in_ours"
    assert discs[0].nomba_transaction_id == "nomba-1"


@pytest.mark.asyncio
async def test_missing_in_nomba(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[])

    tenant = _make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    project = Project(tenant_id=tenant.id, name="Test")
    db_session.add(project)
    await db_session.flush()

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="c@t.com", name="C")
    db_session.add(customer)
    await db_session.flush()

    await _seed_attempt(db_session, tenant.id, customer.id, "ref-1")

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(db_session, provider, date_from, date_to)

    assert summary.total_nomba == 0
    assert summary.total_ours == 1
    assert summary.matched == 0
    assert summary.discrepancies == 1

    discs = (await db_session.execute(select(ReconciliationDiscrepancy))).scalars().all()
    assert discs[0].discrepancy_type.value == "missing_in_nomba"


@pytest.mark.asyncio
async def test_status_mismatch(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[
        {**_TX, "id": "nomba-2", "merchantTxRef": "ref-2", "status": "REFUND"},
    ])

    tenant = _make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    project = Project(tenant_id=tenant.id, name="Test")
    db_session.add(project)
    await db_session.flush()

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="c@t.com", name="C")
    db_session.add(customer)
    await db_session.flush()

    await _seed_attempt(db_session, tenant.id, customer.id, "ref-2", status=PaymentStatus.success)

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(db_session, provider, date_from, date_to)

    assert summary.discrepancies == 1
    discs = (await db_session.execute(select(ReconciliationDiscrepancy))).scalars().all()
    assert discs[0].discrepancy_type.value == "status_mismatch"
    assert "REFUND" in (discs[0].details or "")


@pytest.mark.asyncio
async def test_amount_mismatch(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[
        {**_TX, "id": "nomba-3", "merchantTxRef": "ref-3", "amount": 100.00},
    ])

    tenant = _make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    project = Project(tenant_id=tenant.id, name="Test")
    db_session.add(project)
    await db_session.flush()

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="c@t.com", name="C")
    db_session.add(customer)
    await db_session.flush()

    await _seed_attempt(db_session, tenant.id, customer.id, "ref-3", amount_due=5000)

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(db_session, provider, date_from, date_to)

    assert summary.discrepancies == 1
    discs = (await db_session.execute(select(ReconciliationDiscrepancy))).scalars().all()
    assert discs[0].discrepancy_type.value == "amount_mismatch"
    assert "Amount" in (discs[0].details or "")


@pytest.mark.asyncio
async def test_perfect_match_no_discrepancies(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[
        {**_TX, "id": "nomba-4", "merchantTxRef": "ref-4"},
    ])

    tenant = _make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    project = Project(tenant_id=tenant.id, name="Test")
    db_session.add(project)
    await db_session.flush()

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="c@t.com", name="C")
    db_session.add(customer)
    await db_session.flush()

    await _seed_attempt(db_session, tenant.id, customer.id, "ref-4", amount_due=5000)

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(db_session, provider, date_from, date_to)

    assert summary.matched == 1
    assert summary.discrepancies == 0


@pytest.mark.asyncio
async def test_idempotent_re_run(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[
        {**_TX, "id": "nomba-5", "merchantTxRef": "ref-5"},
    ])

    tenant = _make_tenant()
    db_session.add(tenant)
    await db_session.flush()
    project = Project(tenant_id=tenant.id, name="Test")
    db_session.add(project)
    await db_session.flush()

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="c@t.com", name="C")
    db_session.add(customer)
    await db_session.flush()

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary1 = await service.run_reconciliation(db_session, provider, date_from, date_to)
    assert summary1.discrepancies == 1

    summary2 = await service.run_reconciliation(db_session, provider, date_from, date_to)
    assert summary2.discrepancies == 1

    discs = (await db_session.execute(select(ReconciliationDiscrepancy))).scalars().all()
    assert len(discs) == 1


@pytest.mark.asyncio
async def test_tenant_ids_filter(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[
        {"id": "nomba-t1", "merchantTxRef": "ref-t1", "status": "SUCCESS", "amount": 50.00, "timeCreated": "2026-06-30T12:00:00Z"},
        {"id": "nomba-t2", "merchantTxRef": "ref-t2", "status": "SUCCESS", "amount": 50.00, "timeCreated": "2026-06-30T12:00:00Z"},
    ])

    t1 = _make_tenant()
    t2 = _make_tenant()
    db_session.add_all([t1, t2])
    await db_session.flush()
    p1 = Project(tenant_id=t1.id, name="Test1")
    p2 = Project(tenant_id=t2.id, name="Test2")
    db_session.add_all([p1, p2])
    await db_session.flush()

    c1 = Customer(tenant_id=t1.id, project_id=p1.id, email="c1@t.com", name="C1")
    c2 = Customer(tenant_id=t2.id, project_id=p2.id, email="c2@t.com", name="C2")
    db_session.add_all([c1, c2])
    await db_session.flush()

    await _seed_attempt(db_session, t1.id, c1.id, "ref-t1")
    await _seed_attempt(db_session, t2.id, c2.id, "ref-t2")

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(
        db_session, provider, date_from, date_to,
        tenant_ids=[t1.id],
    )

    assert summary.discrepancies == 1
    discs = (await db_session.execute(select(ReconciliationDiscrepancy))).scalars().all()
    assert len(discs) == 1
    assert discs[0].tenant_id is None
    assert discs[0].merchant_tx_ref == "ref-t2"


@pytest.mark.asyncio
async def test_nomba_unreachable(db_session: AsyncSession, provider):
    async def _fail(**kw):
        raise ProviderError("Nomba down")
    provider.fetch_transactions = _fail

    service = ReconciliationService()
    date_from = _RANGE_START
    date_to = datetime(2026, 7, 1, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(db_session, provider, date_from, date_to)
    assert summary.total_nomba == 0
    assert summary.discrepancies == 0


@pytest.mark.asyncio
async def test_empty_date_range(db_session: AsyncSession, provider):
    provider.fetch_transactions = AsyncMock(return_value=[])

    service = ReconciliationService()
    date_from = datetime(2020, 1, 1, tzinfo=timezone.utc)
    date_to = datetime(2020, 1, 2, tzinfo=timezone.utc)

    summary = await service.run_reconciliation(db_session, provider, date_from, date_to)
    assert summary.total_nomba == 0
    assert summary.total_ours == 0
    assert summary.discrepancies == 0


# ── API Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_discrepancies_endpoint(db_session: AsyncSession):
    import app.core.middleware as mw

    app.dependency_overrides[get_async_db] = lambda: db_session
    original_prefixes = mw._EXEMPT_PREFIXES
    mw._EXEMPT_PREFIXES = mw._EXEMPT_PREFIXES + ("/v1/reconciliation/",)
    try:
        tenant = _make_tenant()
        db_session.add(tenant)
        await db_session.flush()

        disc = ReconciliationDiscrepancy(
            run_id=uuid.uuid4(),
            tenant_id=tenant.id,
            nomba_transaction_id="nomba-api-1",
            merchant_tx_ref="ref-api",
            discrepancy_type="missing_in_ours",
            details="Test discrepancy",
        )
        db_session.add(disc)
        await db_session.commit()

        t_token = current_tenant_id.set(tenant.id)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/v1/reconciliation/discrepancies")
        finally:
            current_tenant_id.reset(t_token)

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["nomba_transaction_id"] == "nomba-api-1"
    finally:
        mw._EXEMPT_PREFIXES = original_prefixes
        app.dependency_overrides.pop(get_async_db, None)


@pytest.mark.asyncio
async def test_resolve_discrepancy_endpoint(db_session: AsyncSession):
    import app.core.middleware as mw

    app.dependency_overrides[get_async_db] = lambda: db_session
    original_prefixes = mw._EXEMPT_PREFIXES
    mw._EXEMPT_PREFIXES = mw._EXEMPT_PREFIXES + ("/v1/reconciliation/",)
    try:
        tenant = _make_tenant()
        db_session.add(tenant)
        await db_session.flush()

        disc = ReconciliationDiscrepancy(
            run_id=uuid.uuid4(),
            nomba_transaction_id="nomba-resolve-1",
            merchant_tx_ref="ref-resolve",
            discrepancy_type="status_mismatch",
            details="To be resolved",
        )
        db_session.add(disc)
        await db_session.commit()
        await db_session.refresh(disc)

        t_token = current_tenant_id.set(tenant.id)
        k_token = current_key_type.set("sk_test")
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.patch(
                    f"/v1/reconciliation/discrepancies/{disc.id}/resolve",
                    json={"resolution_note": "Fixed"},
                )
        finally:
            current_tenant_id.reset(t_token)
            current_key_type.reset(k_token)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resolved"] is True
        assert body["resolution_note"] == "Fixed"
    finally:
        mw._EXEMPT_PREFIXES = original_prefixes
        app.dependency_overrides.pop(get_async_db, None)
