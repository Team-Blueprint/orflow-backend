from sqlalchemy.ext.asyncio import AsyncSession
from app.db.repository import BaseRepository
from app.plans.models import Plan, PlanStatus


class PlanService(BaseRepository[Plan]):
    """
    All queries are automatically scoped to the current tenant
    via BaseRepository's tenant isolation logic.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Plan, session)

    async def list_active(self) -> list[Plan]:
        from sqlalchemy import select

        result = await self.session.execute(
            self._base_query().where(Plan.status == PlanStatus.active)
        )
        return list(result.scalars().all())

    async def archive(self, plan_id) -> Plan | None:
        return await self.update(plan_id, status=PlanStatus.archived)
