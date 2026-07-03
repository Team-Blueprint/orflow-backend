"""Integration tests for the dunning flow .

Mirrors the billing-cycle test harness: a real in-memory DB session, a dummy
payment provider injected via ``app.providers.deps``, and ``AsyncSessionLocal``
monkeypatched onto the worker's session so the cron job runs against the test
DB.
"""

import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_tenant_id
from app.tenants.models import Tenant
from app.customers.models import Customer
from app.plans.models import Plan, PlanInterval
from app.payment_methods.models import PaymentMethod, PaymentMethodType
from app.projects.models import Project
from app.subscriptions.models import Subscription, SubscriptionStatus, SubscriptionType
from app.invoices.models import Invoice, InvoiceStatus
from app.webhooks.models import PaymentAttempt
from app.providers.base import (
    PaymentProviderAdapter,
    ChargeResult,
    PaymentStatus,
    FailureReason,
)
from app.dunning.service import open_or_advance_dunning
from app.dunning.worker import process_dunning_retries, process_unpaid_grace


# --------------------------------------------------------------------------- providers


class _SuccessProvider(PaymentProviderAdapter):
    async def initiate_checkout(self, **kwargs):
        pass

    async def charge_tokenized_card(self, **kwargs) -> ChargeResult:
        return ChargeResult(status=PaymentStatus.success, provider_reference="ref")

    async def transfer(self, **kwargs):
        pass

    async def verify_transaction(self, **kwargs):
        pass


class _FailProvider(_SuccessProvider):
    def __init__(self, reason: FailureReason) -> None:
        self._reason = reason

    async def charge_tokenized_card(self, **kwargs) -> ChargeResult:
        return ChargeResult(status=PaymentStatus.failed, failure_reason=self._reason)


# --------------------------------------------------------------------------- helpers


async def _seed(session: AsyncSession, *, sub_status, period_end, with_token=True, api_key="key"):
    tenant = Tenant(name="Test", sk_test=api_key, email="t@test.com", hashed_password="pass")
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)

    project = Project(tenant_id=tenant.id, name="Test Project")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="c@test.com", name="C")
    plan = Plan(tenant_id=tenant.id, project_id=project.id, name="Pro", amount=1000, currency="USD", interval=PlanInterval.monthly)
    session.add_all([customer, plan])
    await session.commit()

    pm = PaymentMethod(
        tenant_id=tenant.id,
        project_id=project.id,
        customer_id=customer.id,
        type=PaymentMethodType.card,
        provider_token="tok" if with_token else None,
    )
    session.add(pm)
    await session.commit()

    sub = Subscription(
        tenant_id=tenant.id,
        customer_id=customer.id,
        plan_id=plan.id,
        payment_method_id=pm.id,
        status=sub_status,
        type=SubscriptionType.recurring,
        current_period_start=datetime.now(timezone.utc) - timedelta(days=30),
        current_period_end=period_end,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return tenant, project, customer, plan, pm, sub


def _patch_session(monkeypatch, db_session):
    import app.dunning.worker as dw

    class DummySessionLocal:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            pass

        def __call__(self):
            return self

    monkeypatch.setattr(dw, "AsyncSessionLocal", DummySessionLocal())


# --------------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_first_failure_opens_dunning_and_schedules_retry(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    _, project, customer, plan, _, sub = await _seed(
        db_session, sub_status=SubscriptionStatus.active, period_end=now - timedelta(seconds=1), api_key="k1"
    )
    invoice = Invoice(
        tenant_id=sub.tenant_id,
        customer_id=customer.id,
        subscription_id=sub.id,
        status=InvoiceStatus.open,
        amount_due=1000,
        currency="USD",
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    token = current_tenant_id.set(sub.tenant_id)
    try:
        await open_or_advance_dunning(
            db_session,
            invoice=invoice,
            subscription=sub,
            failure_reason=FailureReason.insufficient_funds,
        )
    finally:
        current_tenant_id.reset(token)

    await db_session.refresh(sub)
    await db_session.refresh(invoice)
    assert sub.status == SubscriptionStatus.past_due
    assert invoice.dunning_failure_reason == FailureReason.insufficient_funds
    assert invoice.next_retry_at is not None
    # First retry for insufficient_funds is one day out.
    gap = invoice.next_retry_at.replace(tzinfo=timezone.utc) - now
    assert timedelta(hours=23) < gap < timedelta(hours=25)


@pytest.mark.asyncio
async def test_expired_card_flags_without_retry_or_unpaid(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    _, project, customer, _, _, sub = await _seed(
        db_session, sub_status=SubscriptionStatus.active, period_end=now - timedelta(seconds=1), api_key="k2"
    )
    invoice = Invoice(
        tenant_id=sub.tenant_id,
        customer_id=customer.id,
        subscription_id=sub.id,
        status=InvoiceStatus.open,
        amount_due=1000,
        currency="USD",
    )
    db_session.add(invoice)
    await db_session.commit()

    token = current_tenant_id.set(sub.tenant_id)
    try:
        await open_or_advance_dunning(
            db_session,
            invoice=invoice,
            subscription=sub,
            failure_reason=FailureReason.expired_card,
        )
    finally:
        current_tenant_id.reset(token)

    await db_session.refresh(sub)
    await db_session.refresh(invoice)
    # Flagged for payment-method update: past_due, no retry, NOT revoked yet.
    assert sub.status == SubscriptionStatus.past_due
    assert invoice.next_retry_at is None
    assert invoice.dunning_failure_reason == FailureReason.expired_card


@pytest.mark.asyncio
async def test_dunning_retry_recovers_subscription(db_session: AsyncSession, monkeypatch):
    import app.providers.deps as deps

    deps._provider = _SuccessProvider()
    _patch_session(monkeypatch, db_session)

    now = datetime.now(timezone.utc)
    _, project, customer, _, _, sub = await _seed(
        db_session, sub_status=SubscriptionStatus.past_due, period_end=now - timedelta(seconds=1), api_key="k3"
    )
    invoice = Invoice(
        tenant_id=sub.tenant_id,
        customer_id=customer.id,
        subscription_id=sub.id,
        status=InvoiceStatus.open,
        amount_due=1000,
        currency="USD",
        next_retry_at=now - timedelta(hours=1),
        dunning_failure_reason=FailureReason.insufficient_funds,
    )
    db_session.add(invoice)
    await db_session.commit()

    await process_dunning_retries({})

    await db_session.refresh(sub)
    await db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.paid
    assert invoice.next_retry_at is None
    assert invoice.dunning_failure_reason is None
    assert sub.status == SubscriptionStatus.active
    assert sub.current_period_end.replace(tzinfo=timezone.utc) > now

    attempts = (await db_session.execute(
        select(PaymentAttempt).where(PaymentAttempt.invoice_id == invoice.id)
    )).scalars().all()
    assert any(a.is_retry for a in attempts)

    deps._provider = None


@pytest.mark.asyncio
async def test_dunning_exhaustion_transitions_to_unpaid(db_session: AsyncSession, monkeypatch):
    import app.providers.deps as deps

    # do_not_honor allows exactly one retry; that retry failing exhausts it.
    deps._provider = _FailProvider(FailureReason.do_not_honor)
    _patch_session(monkeypatch, db_session)

    now = datetime.now(timezone.utc)
    _, project, customer, _, _, sub = await _seed(
        db_session, sub_status=SubscriptionStatus.past_due, period_end=now - timedelta(seconds=1), api_key="k4"
    )
    invoice = Invoice(
        tenant_id=sub.tenant_id,
        customer_id=customer.id,
        subscription_id=sub.id,
        status=InvoiceStatus.open,
        amount_due=1000,
        currency="USD",
        next_retry_at=now - timedelta(hours=1),
        dunning_failure_reason=FailureReason.do_not_honor,
    )
    db_session.add(invoice)
    await db_session.commit()

    await process_dunning_retries({})

    await db_session.refresh(sub)
    await db_session.refresh(invoice)
    assert sub.status == SubscriptionStatus.unpaid
    assert invoice.status == InvoiceStatus.open
    assert invoice.next_retry_at is None

    deps._provider = None


@pytest.mark.asyncio
async def test_grace_worker_cancels_long_unpaid(db_session: AsyncSession, monkeypatch):
    _patch_session(monkeypatch, db_session)

    now = datetime.now(timezone.utc)
    _, project, _, _, _, sub = await _seed(
        db_session, sub_status=SubscriptionStatus.unpaid, period_end=now - timedelta(days=40), api_key="k5"
    )
    # Force the unpaid clock well past the default 14-day grace window.
    sub.updated_at = now - timedelta(days=30)
    await db_session.commit()

    await process_unpaid_grace({})

    await db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.canceled
    assert sub.canceled_at is not None
