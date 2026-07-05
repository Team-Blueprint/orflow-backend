from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import BaseRepository
from app.invoices.models import Invoice, InvoiceStatus
from app.plans.models import Plan, PlanStatus
from app.subscriptions.models import Subscription


class PlanService(BaseRepository[Plan]):
    """
    All queries are automatically scoped to the current tenant
    via BaseRepository's tenant isolation logic.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Plan, session)

    async def list_active(self) -> list[Plan]:

        result = await self.session.execute(
            self._base_query().where(Plan.status == PlanStatus.active)
        )
        return list(result.scalars().all())

    async def archive(self, plan_id) -> Plan | None:
        return await self.update(plan_id, status=PlanStatus.archived)

    async def list_with_stats(
        self,
        offset: int = 0,
        limit: int = 100,
    ) -> list[tuple[Plan, int, int]]:
        stmt = (
            select(
                Plan,
                func.count(func.distinct(Subscription.id)).label("subscription_count"),
                func.coalesce(func.sum(Invoice.amount_due), 0).label("revenue"),
            )
            .outerjoin(Subscription, Subscription.plan_id == Plan.id)
            .outerjoin(
                Invoice,
                (Invoice.subscription_id == Subscription.id)
                & (Invoice.status == InvoiceStatus.paid),
            )
            .where(Plan.tenant_id == self._tenant_id())
            .group_by(Plan.id)
            .offset(offset)
            .limit(limit)
        )
        project_id = self._project_id()
        if project_id is not None:
            stmt = stmt.where(Plan.project_id == project_id)
        result = await self.session.execute(stmt)
        return list(result.all())

    async def get_with_stats(self, plan_id: UUID) -> tuple[Plan, int, int] | None:
        plan = await self.get(plan_id)
        if not plan:
            return None

        count_stmt = select(func.count(Subscription.id)).where(
            Subscription.plan_id == plan_id,
            Subscription.tenant_id == self._tenant_id(),
        )
        sub_count = (await self.session.execute(count_stmt)).scalar()

        revenue_stmt = (
            select(func.coalesce(func.sum(Invoice.amount_due), 0))
            .select_from(Invoice)
            .join(Subscription, Invoice.subscription_id == Subscription.id)
            .where(
                Subscription.plan_id == plan_id,
                Subscription.tenant_id == self._tenant_id(),
                Invoice.status == InvoiceStatus.paid,
            )
        )
        revenue = (await self.session.execute(revenue_stmt)).scalar()

        return plan, sub_count, revenue
