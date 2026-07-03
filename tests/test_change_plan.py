"""Integration tests for the change-plan / proration flow 

``SubscriptionService.change_plan_flow`` end-to-end against a real
in-memory DB: proration invoice + line items, atomic plan swap, immediate
charge, and the dunning fallback on a failed charge.
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
from app.subscriptions.service import SubscriptionService
from app.invoices.models import Invoice, InvoiceStatus, InvoiceLineItem
from app.webhooks.models import PaymentAttempt
from app.providers.base import (
    PaymentProviderAdapter,
    ChargeResult,
    PaymentStatus,
    FailureReason,
)


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
    async def charge_tokenized_card(self, **kwargs) -> ChargeResult:
        return ChargeResult(status=PaymentStatus.failed, failure_reason=FailureReason.card_declined)


async def _seed(session: AsyncSession, *, old_amount=1000, new_amount=3000, api_key="k"):
    tenant = Tenant(name="T", sk_test=api_key, email="t@test.com", hashed_password="pass")
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)

    project = Project(tenant_id=tenant.id, name="Test Project")
    session.add(project)
    await session.commit()
    await session.refresh(project)

    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="c@test.com", name="C")
    old_plan = Plan(tenant_id=tenant.id, project_id=project.id, name="Basic", amount=old_amount, currency="USD", interval=PlanInterval.monthly)
    new_plan = Plan(tenant_id=tenant.id, project_id=project.id, name="Pro", amount=new_amount, currency="USD", interval=PlanInterval.monthly)
    session.add_all([customer, old_plan, new_plan])
    await session.commit()

    pm = PaymentMethod(
        tenant_id=tenant.id, project_id=project.id, customer_id=customer.id, type=PaymentMethodType.card, provider_token="tok"
    )
    session.add(pm)
    await session.commit()

    now = datetime.now(timezone.utc)
    # Half the cycle remaining; +1h buffer keeps the integer-day math stable.
    sub = Subscription(
        tenant_id=tenant.id,
        customer_id=customer.id,
        plan_id=old_plan.id,
        payment_method_id=pm.id,
        status=SubscriptionStatus.active,
        type=SubscriptionType.recurring,
        current_period_start=now - timedelta(days=15),
        current_period_end=now + timedelta(days=15, hours=1),
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return tenant, project, customer, old_plan, new_plan, pm, sub


@pytest.mark.asyncio
async def test_upgrade_charges_net_and_swaps_plan(db_session: AsyncSession):
    tenant, project, _, _, new_plan, _, sub = await _seed(db_session, api_key="cp1")
    token = current_tenant_id.set(tenant.id)
    try:
        svc = SubscriptionService(db_session)
        resp = await svc.change_plan_flow(sub.id, new_plan.id, _SuccessProvider())
    finally:
        current_tenant_id.reset(token)

    # net = (3000 - 1000) * 15/30 = 1000
    assert resp.invoice.amount_due == 1000
    assert resp.invoice.status == InvoiceStatus.paid
    assert resp.charged is True
    assert resp.payment_status == "success"
    assert resp.subscription.plan_id == new_plan.id
    assert len(resp.line_items) == 2

    await db_session.refresh(sub)
    assert sub.plan_id == new_plan.id

    items = (await db_session.execute(
        select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == resp.invoice.id)
    )).scalars().all()
    assert sorted(i.amount_minor for i in items) == [-500, 1500]

    attempts = (await db_session.execute(
        select(PaymentAttempt).where(PaymentAttempt.invoice_id == resp.invoice.id)
    )).scalars().all()
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_downgrade_does_not_charge(db_session: AsyncSession):
    tenant, project, _, _, new_plan, _, sub = await _seed(db_session, old_amount=3000, new_amount=1000, api_key="cp2")
    token = current_tenant_id.set(tenant.id)
    try:
        svc = SubscriptionService(db_session)
        resp = await svc.change_plan_flow(sub.id, new_plan.id, _SuccessProvider())
    finally:
        current_tenant_id.reset(token)

    # net = (1000 - 3000) * 15/30 = -1000  -> nothing collected
    assert resp.invoice.amount_due == -1000
    assert resp.invoice.status == InvoiceStatus.paid
    assert resp.charged is False
    assert resp.payment_status == "no_charge"
    assert resp.subscription.plan_id == new_plan.id

    attempts = (await db_session.execute(
        select(PaymentAttempt).where(PaymentAttempt.invoice_id == resp.invoice.id)
    )).scalars().all()
    assert len(attempts) == 0


@pytest.mark.asyncio
async def test_failed_charge_swaps_plan_and_enters_dunning(db_session: AsyncSession):
    tenant, project, _, _, new_plan, _, sub = await _seed(db_session, api_key="cp3")
    token = current_tenant_id.set(tenant.id)
    try:
        svc = SubscriptionService(db_session)
        resp = await svc.change_plan_flow(sub.id, new_plan.id, _FailProvider())
    finally:
        current_tenant_id.reset(token)

    assert resp.charged is False
    await db_session.refresh(sub)
    # Plan swap is committed even though the charge failed.
    assert sub.plan_id == new_plan.id
    assert sub.status == SubscriptionStatus.past_due

    invoice = (await db_session.execute(
        select(Invoice).where(Invoice.id == resp.invoice.id)
    )).scalar_one()
    assert invoice.status == InvoiceStatus.open
    assert invoice.next_retry_at is not None  # dunning scheduled a retry


@pytest.mark.asyncio
async def test_change_to_same_plan_rejected(db_session: AsyncSession):
    tenant, project, _, old_plan, _, _, sub = await _seed(db_session, api_key="cp4")
    token = current_tenant_id.set(tenant.id)
    try:
        svc = SubscriptionService(db_session)
        with pytest.raises(ValueError, match="already on this plan"):
            await svc.change_plan_flow(sub.id, old_plan.id, _SuccessProvider())
    finally:
        current_tenant_id.reset(token)


@pytest.mark.asyncio
async def test_change_plan_requires_active_subscription(db_session: AsyncSession):
    tenant, project, _, _, new_plan, _, sub = await _seed(db_session, api_key="cp5")
    sub.status = SubscriptionStatus.past_due
    await db_session.commit()

    token = current_tenant_id.set(tenant.id)
    try:
        svc = SubscriptionService(db_session)
        with pytest.raises(ValueError, match="active subscription"):
            await svc.change_plan_flow(sub.id, new_plan.id, _SuccessProvider())
    finally:
        current_tenant_id.reset(token)
