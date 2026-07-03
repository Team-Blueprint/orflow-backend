"""
Seed script — creates sample tenants and plans for local manual testing.

Usage:
    uv run python scripts/seed.py
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.tenants.models import Tenant
from app.customers.models import Customer
from app.plans.models import Plan, PlanInterval, PlanStatus


TENANTS = [
    {"name": "Acme Corp", "api_key": f"sk_{uuid.uuid4().hex[:24]}"},
    {"name": "Globex Inc", "api_key": f"sk_{uuid.uuid4().hex[:24]}"},
]

PLANS_TEMPLATE = [
    {
        "name": "Starter",
        "amount": 999,       # $9.99
        "currency": "USD",
        "interval": PlanInterval.monthly,
        "interval_count": 1,
        "trial_period_days": 14,
    },
    {
        "name": "Pro",
        "amount": 2999,      # $29.99
        "currency": "USD",
        "interval": PlanInterval.monthly,
        "interval_count": 1,
        "trial_period_days": None,
    },
    {
        "name": "Enterprise Annual",
        "amount": 99900,     # $999.00
        "currency": "USD",
        "interval": PlanInterval.yearly,
        "interval_count": 1,
        "trial_period_days": None,
    },
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        print("\n=== Seeding tenants and plans ===\n")

        for tenant_data in TENANTS:
            tenant = Tenant(
                id=uuid.uuid4(),
                name=tenant_data["name"],
                api_key=tenant_data["api_key"],
                is_active=True,
            )
            session.add(tenant)
            await session.flush()   # get tenant.id before creating plans

            for plan_data in PLANS_TEMPLATE:
                plan = Plan(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    status=PlanStatus.active,
                    **plan_data,
                )
                session.add(plan)

            print(f"Tenant : {tenant.name}")
            print(f"API Key: {tenant.api_key}\n")

        await session.commit()
        print("=== Seed complete ===\n")


if __name__ == "__main__":
    asyncio.run(seed())
