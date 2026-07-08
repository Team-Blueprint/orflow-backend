import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.customers.models import Customer
from app.db.repository import BaseRepository


class CustomerService(BaseRepository[Customer]):
    """
    All queries are automatically scoped to the current tenant
    via BaseRepository's tenant isolation logic.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Customer, session)

    async def get_by_email(self, email: str, project_id: uuid.UUID | None = None) -> Customer | None:
        query = self._base_query().where(Customer.email == email)
        if project_id is not None:
            query = query.where(Customer.project_id == project_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
