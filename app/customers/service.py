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

    async def get_by_email(self, email: str) -> Customer | None:

        result = await self.session.execute(
            self._base_query().where(Customer.email == email)
        )
        return result.scalar_one_or_none()
