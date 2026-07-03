import uuid
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.tenants.models import Tenant
from app.customers.models import Customer
from app.plans.models import Plan, PlanInterval
from app.payment_methods.models import PaymentMethod, PaymentMethodType
from app.projects.models import Project
from app.subscriptions.models import Subscription, SubscriptionStatus, SubscriptionType
from app.invoices.models import Invoice, InvoiceStatus
from app.worker.billing_cycle import process_billing_cycle

from app.providers.base import PaymentProviderAdapter, ChargeResult, PaymentStatus

class DummyChargeProvider(PaymentProviderAdapter):
    async def initiate_checkout(self, **kwargs): pass
    async def charge_tokenized_card(self, **kwargs) -> ChargeResult:
        return ChargeResult(status=PaymentStatus.success)
    async def transfer(self, **kwargs): pass
    async def verify_transaction(self, **kwargs): pass

class DummyFailedChargeProvider(DummyChargeProvider):
    async def charge_tokenized_card(self, **kwargs) -> ChargeResult:
        from app.providers.base import FailureReason
        return ChargeResult(status=PaymentStatus.failed, failure_reason=FailureReason.card_declined)

@pytest.mark.asyncio
async def test_billing_cycle_success(db_session: AsyncSession):
    import app.providers.deps as deps
    deps._provider = DummyChargeProvider()
    
    import app.worker.billing_cycle as bc
    class DummySessionLocal:
        async def __aenter__(self): return db_session
        async def __aexit__(self, exc_type, exc_val, exc_tb): pass
        def __call__(self): return self
    original_session_local = bc.AsyncSessionLocal
    bc.AsyncSessionLocal = DummySessionLocal
    
    tenant = Tenant(name="Test", sk_test="key_test_1", email="t1@test.com", hashed_password="pass")
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    
    project = Project(tenant_id=tenant.id, name="Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    
    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="test@test.com", name="Test")
    plan = Plan(tenant_id=tenant.id, project_id=project.id, name="Pro", amount=1000, currency="USD", interval=PlanInterval.monthly)
    db_session.add_all([customer, plan])
    await db_session.commit()
    
    pm = PaymentMethod(tenant_id=tenant.id, project_id=project.id, customer_id=customer.id, type=PaymentMethodType.card, provider_token="tok")
    db_session.add(pm)
    await db_session.commit()
    
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id=tenant.id,
        project_id=project.id,
        customer_id=customer.id,
        plan_id=plan.id,
        payment_method_id=pm.id,
        status=SubscriptionStatus.active,
        type=SubscriptionType.recurring,
        current_period_start=now - timedelta(days=30),
        current_period_end=now - timedelta(seconds=1)
    )
    db_session.add(sub)
    await db_session.commit()
    
    await process_billing_cycle({})
    
    await db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.active
    assert sub.current_period_end.replace(tzinfo=timezone.utc) > now
    
    stmt = select(Invoice).where(Invoice.subscription_id == sub.id)
    result = await db_session.execute(stmt)
    invoice = result.scalar_one()
    assert invoice.status == InvoiceStatus.paid
    assert invoice.amount_due == 1000
    
    bc.AsyncSessionLocal = original_session_local
    deps._provider = None

@pytest.mark.asyncio
async def test_billing_cycle_failure(db_session: AsyncSession):
    import app.providers.deps as deps
    deps._provider = DummyFailedChargeProvider()
    
    import app.worker.billing_cycle as bc
    class DummySessionLocal:
        async def __aenter__(self): return db_session
        async def __aexit__(self, exc_type, exc_val, exc_tb): pass
        def __call__(self): return self
    original_session_local = bc.AsyncSessionLocal
    bc.AsyncSessionLocal = DummySessionLocal
    
    tenant = Tenant(name="Test", sk_test="key_test_2", email="t2@test.com", hashed_password="pass")
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    
    project = Project(tenant_id=tenant.id, name="Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    
    customer = Customer(tenant_id=tenant.id, project_id=project.id, email="test2@test.com", name="Test2")
    plan = Plan(tenant_id=tenant.id, project_id=project.id, name="Pro", amount=1000, currency="USD", interval=PlanInterval.monthly)
    db_session.add_all([customer, plan])
    await db_session.commit()
    
    pm = PaymentMethod(tenant_id=tenant.id, project_id=project.id, customer_id=customer.id, type=PaymentMethodType.card, provider_token="tok")
    db_session.add(pm)
    await db_session.commit()
    
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id=tenant.id,
        project_id=project.id,
        customer_id=customer.id,
        plan_id=plan.id,
        payment_method_id=pm.id,
        status=SubscriptionStatus.active,
        type=SubscriptionType.recurring,
        current_period_start=now - timedelta(days=30),
        current_period_end=now - timedelta(seconds=1)
    )
    db_session.add(sub)
    await db_session.commit()
    
    await process_billing_cycle({})
    
    await db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.past_due
    assert sub.current_period_end.replace(tzinfo=timezone.utc) < now
    
    stmt = select(Invoice).where(Invoice.subscription_id == sub.id)
    result = await db_session.execute(stmt)
    invoice = result.scalar_one()
    assert invoice.status == InvoiceStatus.open
    
    bc.AsyncSessionLocal = original_session_local
    deps._provider = None
