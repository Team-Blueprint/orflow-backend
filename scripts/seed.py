"""
Seed script for the Orflow evaluation environment.

Inserts a deterministic, realistic dataset:
  - 1 merchant tenant (Adebayo Olumide) with a project and 5 plans
  - 15–20 paid invoices spread over the last 3 months for revenue chart
  - 1–2 failed payment attempts followed by success to demonstrate dunning
  - 3 demo customer subscribers with subscriptions and payment methods

Run:
    python -m scripts.seed
or:
    .venv/bin/python -m scripts.seed

Safe to re-run: existing records are detected by email / slug and skipped.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.tenants.models import Tenant
from app.projects.models import Project
from app.plans.models import Plan, PlanInterval, PlanStatus
from app.customers.models import Customer
from app.subscriptions.models import Subscription, SubscriptionStatus, SubscriptionType
from app.payment_methods.models import PaymentMethod, PaymentMethodType, PaymentMethodStatus
from app.invoices.models import Invoice, InvoiceStatus
from app.webhooks.models import PaymentAttempt
from app.providers.base import PaymentStatus, FailureReason


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days_ago(n: int) -> datetime:
    return _now() - timedelta(days=n)


def _days_from_now(n: int) -> datetime:
    return _now() + timedelta(days=n)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


async def _get_or_none(session: AsyncSession, model, **filters):
    conditions = [getattr(model, k) == v for k, v in filters.items()]
    result = await session.execute(select(model).where(*conditions))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# 1. Tenant
# ---------------------------------------------------------------------------

async def seed_tenant(session: AsyncSession) -> Tenant:
    existing = await _get_or_none(session, Tenant, email="adebayo.olumide.biz@gmail.com")
    if existing:
        print("  [skip] Tenant already exists")
        return existing

    keys = Tenant.generate_all_keys()
    tenant = Tenant(
        name="Adebayo Olumide",
        email="adebayo.olumide.biz@gmail.com",
        hashed_password=_hash("hackathon123"),
        is_active=True,
        **keys,
    )
    session.add(tenant)
    await session.flush()
    print(f"  [ok]   Tenant created: {tenant.id}")
    print(f"         pk_test : {tenant.pk_test}")
    print(f"         sk_test : {tenant.sk_test}")
    print(f"         pk_live : {tenant.pk_live}")
    print(f"         sk_live : {tenant.sk_live}")
    return tenant


# ---------------------------------------------------------------------------
# 2. Project
# ---------------------------------------------------------------------------

async def seed_project(session: AsyncSession, tenant: Tenant) -> Project:
    existing = await _get_or_none(session, Project, tenant_id=tenant.id, name="Adebayo's Digital Services")
    if existing:
        print("  [skip] Project already exists")
        return existing

    project = Project(
        tenant_id=tenant.id,
        name="Adebayo's Digital Services",
        description="Main project for Adebayo's digital service offerings",
        default_callback_url="https://orflow.vercel.app/portal/callback",
    )
    session.add(project)
    await session.flush()
    print(f"  [ok]   Project created: {project.id}")
    return project


# ---------------------------------------------------------------------------
# 3. Plans
# ---------------------------------------------------------------------------

PLAN_DEFS = [
    ("Basic",            250_000,     PlanInterval.monthly,  1),
    ("Pro",              750_000,     PlanInterval.monthly,  1),
    ("Business",       2_500_000,     PlanInterval.monthly,  1),
    ("Enterprise",    10_000_000,     PlanInterval.monthly,  1),
    ("Enterprise Annual", 100_000_000, PlanInterval.yearly,  1),
]


async def seed_plans(session: AsyncSession, tenant: Tenant, project: Project) -> dict[str, Plan]:
    plans: dict[str, Plan] = {}
    for name, amount, interval, interval_count in PLAN_DEFS:
        existing = await _get_or_none(session, Plan, tenant_id=tenant.id, project_id=project.id, name=name)
        if existing:
            print(f"  [skip] Plan '{name}' already exists")
            plans[name] = existing
            continue

        plan = Plan(
            tenant_id=tenant.id,
            project_id=project.id,
            name=name,
            amount=amount,
            currency="NGN",
            interval=interval,
            interval_count=interval_count,
            status=PlanStatus.active,
            is_test=False,
        )
        session.add(plan)
        await session.flush()
        plans[name] = plan
        print(f"  [ok]   Plan '{name}' created: {plan.id}")
    return plans


# ---------------------------------------------------------------------------
# 4. Revenue history invoices (for analytics charts)
# ---------------------------------------------------------------------------

async def seed_revenue_history(
    session: AsyncSession,
    tenant: Tenant,
    project: Project,
    plans: dict[str, Plan],
    customers: list[Customer],
    subscriptions: list[Subscription],
) -> None:
    """
    Create 18 paid invoices spread over the last 3 months.
    Invoices 5 and 6 include failed attempt(s) before success to show dunning.
    Writes directly to the DB (bypasses state machine / email side effects).
    """
    # Clean up existing revenue data for this tenant so the seed is re-runnable
    existing_pa = await session.execute(
        select(PaymentAttempt.id).join(Invoice).where(
            Invoice.tenant_id == tenant.id,
            Invoice.status == InvoiceStatus.paid,
        )
    )
    existing_pa_ids = [row[0] for row in existing_pa.fetchall()]
    if existing_pa_ids:
        await session.execute(delete(PaymentAttempt).where(PaymentAttempt.id.in_(existing_pa_ids)))
    existing_inv = await session.execute(
        select(Invoice.id).where(
            Invoice.tenant_id == tenant.id,
            Invoice.status == InvoiceStatus.paid,
        )
    )
    existing_inv_ids = [row[0] for row in existing_inv.fetchall()]
    if existing_inv_ids:
        await session.execute(delete(Invoice).where(Invoice.id.in_(existing_inv_ids)))
    print("  [clean] Removed existing revenue data")

    # Map customer → subscription for FK
    cust_sub: dict[uuid.UUID, Subscription] = {s.customer_id: s for s in subscriptions}

    # (customer_index, plan_name, days_ago_paid)
    invoice_schedule = [
        # Month 3 ago (≈60–90 days ago)
        (0, "Pro",        88),
        (1, "Basic",      85),
        (2, "Business",   83),
        (0, "Pro",        80),
        (1, "Basic",      75),
        # Month 2 ago (≈30–59 days ago)
        (0, "Pro",        58),
        (1, "Basic",      55),
        (2, "Business",   53),
        (0, "Pro",        50),
        (1, "Basic",      48),
        (2, "Business",   45),
        # Dunning demo: invoice 12 has 2 failed attempts then success
        (0, "Pro",        42),
        # Month 1 ago / recent (0–29 days ago)
        (1, "Basic",      28),
        (2, "Business",   25),
        (0, "Pro",        22),
        (1, "Basic",      18),
        (2, "Business",   14),
        (0, "Pro",        7),
    ]

    dunning_invoice_local_idx = 11  # 0-based index in invoice_schedule above

    for i, (cust_idx, plan_name, days_ago) in enumerate(invoice_schedule):
        customer = customers[cust_idx]
        plan = plans[plan_name]
        sub = cust_sub.get(customer.id)
        paid_at = _days_ago(days_ago)

        invoice = Invoice(
            tenant_id=tenant.id,
            customer_id=customer.id,
            subscription_id=sub.id if sub else None,
            status=InvoiceStatus.paid,
            amount_due=plan.amount,
            currency="NGN",
            period_start=paid_at,
            period_end=paid_at + timedelta(days=30),
            due_date=paid_at,
            paid_at=paid_at,
            attempt_count=1,
            is_test=False,
        )
        session.add(invoice)
        await session.flush()

        if i == dunning_invoice_local_idx:
            # Two failed attempts before the successful one
            for fail_num in range(2):
                fail_attempt = PaymentAttempt(
                    tenant_id=tenant.id,
                    invoice_id=invoice.id,
                    status=PaymentStatus.failed,
                    failure_reason=FailureReason.insufficient_funds,
                    attempt_number=fail_num,
                    is_retry=fail_num > 0,
                    provider_reference=f"demo_fail_{invoice.id}_{fail_num}",
                    error_message="Insufficient funds",
                    is_test=False,
                    created_at=paid_at,
                )
                session.add(fail_attempt)
            invoice.attempt_count = 3

        # Successful attempt
        success_attempt = PaymentAttempt(
            tenant_id=tenant.id,
            invoice_id=invoice.id,
            status=PaymentStatus.success,
            failure_reason=None,
            attempt_number=invoice.attempt_count - 1,
            is_retry=i == dunning_invoice_local_idx,
            provider_reference=f"demo_success_{invoice.id}",
            is_test=False,
            created_at=paid_at,
        )
        session.add(success_attempt)

    await session.flush()
    print(f"  [ok]   {len(invoice_schedule)} revenue invoices + payment attempts created")


# ---------------------------------------------------------------------------
# 5. Demo customers, subscriptions, and payment methods
# ---------------------------------------------------------------------------

async def seed_customer(
    session: AsyncSession,
    tenant: Tenant,
    project: Project,
    *,
    name: str,
    email: str,
    portal_token_slug: str,
    portal_pin: str,
    card_last4: str,
    card_brand: str,
) -> Customer:
    existing = await _get_or_none(session, Customer, tenant_id=tenant.id, email=email)
    if existing:
        print(f"  [skip] Customer '{name}' already exists")
        return existing

    customer = Customer(
        tenant_id=tenant.id,
        project_id=project.id,
        email=email,
        name=name,
        portal_token_slug=portal_token_slug,
        portal_pin_hash=_hash(portal_pin),
        card_last4=card_last4,
        card_brand=card_brand,
        is_test=False,
    )
    session.add(customer)
    await session.flush()
    print(f"  [ok]   Customer '{name}' created: {customer.id}")
    return customer


async def seed_subscription(
    session: AsyncSession,
    tenant: Tenant,
    customer: Customer,
    plan: Plan,
    *,
    status: SubscriptionStatus,
    period_start_days_ago: int,
    period_end_days_from_now: int,
    payment_method_id: uuid.UUID | None = None,
) -> Subscription:
    existing = await _get_or_none(session, Subscription, tenant_id=tenant.id, customer_id=customer.id, plan_id=plan.id)
    if existing:
        print(f"  [skip] Subscription for '{customer.name}' already exists")
        return existing

    sub = Subscription(
        tenant_id=tenant.id,
        customer_id=customer.id,
        plan_id=plan.id,
        payment_method_id=payment_method_id,
        status=status,
        type=SubscriptionType.recurring,
        current_period_start=_days_ago(period_start_days_ago),
        current_period_end=_days_from_now(period_end_days_from_now),
        trial_end=None,
        cancel_at_period_end=False,
        custom_metadata={},
        is_test=False,
    )
    session.add(sub)
    await session.flush()
    print(f"  [ok]   Subscription for '{customer.name}' ({status.value}) created: {sub.id}")
    return sub


async def seed_payment_method(
    session: AsyncSession,
    tenant: Tenant,
    project: Project,
    customer: Customer,
    *,
    provider_token: str,
    last_four: str,
    card_brand: str,
    expiry_month: int,
    expiry_year: int,
    is_default: bool,
) -> PaymentMethod:
    existing = await _get_or_none(session, PaymentMethod, tenant_id=tenant.id, provider_token=provider_token)
    if existing:
        print(f"  [skip] PaymentMethod {last_four} for '{customer.name}' already exists")
        return existing

    pm = PaymentMethod(
        tenant_id=tenant.id,
        project_id=project.id,
        customer_id=customer.id,
        type=PaymentMethodType.card,
        status=PaymentMethodStatus.active,
        provider_token=provider_token,
        last_four=last_four,
        expiry_month=expiry_month,
        expiry_year=expiry_year,
        is_default=is_default,
        is_test=False,
    )
    session.add(pm)
    await session.flush()
    print(f"  [ok]   PaymentMethod *{last_four} for '{customer.name}' created: {pm.id}")
    return pm


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("\n=== Orflow Seed Script ===\n")

    async with AsyncSessionLocal() as session:
        # 1. Tenant
        print("→ Tenant")
        tenant = await seed_tenant(session)

        # 2. Project
        print("→ Project")
        project = await seed_project(session, tenant)

        # 3. Plans
        print("→ Plans")
        plans = await seed_plans(session, tenant, project)

        # ----------------------------------------------------------------
        # Profile A – Idighs Anthony (Active / Pro)
        # ----------------------------------------------------------------
        print("→ Profile A – Idighs Anthony")
        idighs = await seed_customer(
            session, tenant, project,
            name="Idighs Anthony",
            email="idighs.anthony@gmail.com",
            portal_token_slug="demo-idighs-active",
            portal_pin="123456",
            card_last4="4242",
            card_brand="Visa",
        )
        idighs_pm = await seed_payment_method(
            session, tenant, project, idighs,
            provider_token="tok_demo_idighs_visa",
            last_four="4242",
            card_brand="Visa",
            expiry_month=12,
            expiry_year=2027,
            is_default=True,
        )
        idighs_sub = await seed_subscription(
            session, tenant, idighs, plans["Pro"],
            status=SubscriptionStatus.active,
            period_start_days_ago=28,
            period_end_days_from_now=2,
            payment_method_id=idighs_pm.id,
        )

        # ----------------------------------------------------------------
        # Profile B – Jamie Chukwuma (Paused / Basic)
        # ----------------------------------------------------------------
        print("→ Profile B – Jamie Chukwuma")
        jamie = await seed_customer(
            session, tenant, project,
            name="Jamie Chukwuma",
            email="jamie.chukwuma99@gmail.com",
            portal_token_slug="demo-jamie-paused",
            portal_pin="123456",
            card_last4="9876",
            card_brand="Mastercard",
        )
        jamie_pm = await seed_payment_method(
            session, tenant, project, jamie,
            provider_token="tok_demo_jamie_mc",
            last_four="9876",
            card_brand="Mastercard",
            expiry_month=8,
            expiry_year=2026,
            is_default=True,
        )
        jamie_sub = await seed_subscription(
            session, tenant, jamie, plans["Basic"],
            status=SubscriptionStatus.paused,
            period_start_days_ago=58,
            period_end_days_from_now=2,
            payment_method_id=jamie_pm.id,
        )

        # ----------------------------------------------------------------
        # Profile C – Amadi Florence (Active / Business, multi-card)
        # ----------------------------------------------------------------
        print("→ Profile C – Amadi Florence")
        amadi = await seed_customer(
            session, tenant, project,
            name="Amadi Florence",
            email="amadi.florence.f@gmail.com",
            portal_token_slug="demo-amadi-cards",
            portal_pin="123456",
            card_last4="5555",
            card_brand="Visa",
        )
        amadi_pm1 = await seed_payment_method(
            session, tenant, project, amadi,
            provider_token="tok_demo_amadi_visa_1",
            last_four="5555",
            card_brand="Visa",
            expiry_month=12,
            expiry_year=2028,
            is_default=True,
        )
        await seed_payment_method(
            session, tenant, project, amadi,
            provider_token="tok_demo_amadi_mc",
            last_four="4444",
            card_brand="Mastercard",
            expiry_month=12,
            expiry_year=2028,
            is_default=False,
        )
        await seed_payment_method(
            session, tenant, project, amadi,
            provider_token="tok_demo_amadi_visa_2",
            last_four="3333",
            card_brand="Visa",
            expiry_month=12,
            expiry_year=2028,
            is_default=False,
        )
        amadi_sub = await seed_subscription(
            session, tenant, amadi, plans["Business"],
            status=SubscriptionStatus.active,
            period_start_days_ago=28,
            period_end_days_from_now=2,
            payment_method_id=amadi_pm1.id,
        )

        # ----------------------------------------------------------------
        # 4. Revenue history (must run after customers + subscriptions exist)
        # ----------------------------------------------------------------
        print("→ Revenue history")
        await seed_revenue_history(
            session, tenant, project, plans,
            customers=[idighs, jamie, amadi],
            subscriptions=[idighs_sub, jamie_sub, amadi_sub],
        )

        await session.commit()
        print("\n✓ Seed complete.\n")

        print("Demo credentials")
        print("  Merchant login : adebayo.olumide.biz@gmail.com / hackathon123")
        print("  Portal A slug  : demo-idighs-active  PIN: 123456")
        print("  Portal B slug  : demo-jamie-paused   PIN: 123456")
        print("  Portal C slug  : demo-amadi-cards    PIN: 123456")


if __name__ == "__main__":
    asyncio.run(main())
