from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.customers.models import Customer
from app.invoices.models import Invoice, InvoiceStatus
from app.plans.models import Plan
from app.subscriptions.models import Subscription, SubscriptionStatus


class AnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_subscribers(
        self, tenant_id: UUID, project_id: UUID, is_test: bool
    ) -> int:
        query = (
            select(func.count(func.distinct(Subscription.id)))
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(
                Subscription.tenant_id == tenant_id,
                Subscription.is_test == is_test,
                Subscription.status == SubscriptionStatus.active,
                Plan.project_id == project_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def get_total_customers(
        self, tenant_id: UUID, project_id: UUID, is_test: bool
    ) -> int:
        query = (
            select(func.count(func.distinct(Customer.id)))
            .where(
                Customer.tenant_id == tenant_id,
                Customer.is_test == is_test,
                Customer.project_id == project_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def get_total_volume(
        self, tenant_id: UUID, project_id: UUID, is_test: bool, since_date: date
    ) -> int:
        query = (
            select(func.coalesce(func.sum(Invoice.amount_due), 0))
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(
                Invoice.tenant_id == tenant_id,
                Invoice.is_test == is_test,
                Invoice.status == InvoiceStatus.paid,
                Customer.project_id == project_id,
                Invoice.paid_at >= since_date,
            )
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def get_revenue_chart(
        self, tenant_id: UUID, project_id: UUID, is_test: bool, since_date: date
    ) -> list[dict]:
        query = (
            select(
                func.date(Invoice.paid_at).label("date"),
                func.coalesce(func.sum(Invoice.amount_due), 0).label("amount"),
            )
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(
                Invoice.tenant_id == tenant_id,
                Invoice.is_test == is_test,
                Invoice.status == InvoiceStatus.paid,
                Customer.project_id == project_id,
                Invoice.paid_at >= since_date,
            )
            .group_by(func.date(Invoice.paid_at))
            .order_by(func.date(Invoice.paid_at))
        )
        result = await self.db.execute(query)
        return [{"date": str(row.date), "amount": row.amount} for row in result.all()]

    async def get_currency(
        self, tenant_id: UUID, project_id: UUID, is_test: bool
    ) -> str:
        query = (
            select(Invoice.currency)
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(
                Invoice.tenant_id == tenant_id,
                Invoice.is_test == is_test,
                Invoice.status == InvoiceStatus.paid,
                Customer.project_id == project_id,
            )
            .order_by(Invoice.paid_at.desc())
            .limit(1)
        )
        result = await self.db.execute(query)
        currency = result.scalar()
        return currency or "NGN"
