import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.repository import BaseRepository
from app.payment_methods.models import PaymentMethod, PaymentMethodStatus


class PaymentMethodService(BaseRepository[PaymentMethod]):
    """
    All queries are automatically scoped to the current tenant
    via BaseRepository's tenant isolation logic.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(PaymentMethod, session)

    async def list_for_customer(self, customer_id: uuid.UUID) -> list[PaymentMethod]:
        result = await self.session.execute(
            self._base_query().where(PaymentMethod.customer_id == customer_id)
        )
        return list(result.scalars().all())

    async def set_default(self, payment_method_id: uuid.UUID) -> PaymentMethod | None:
        """Clears existing default for the customer then sets the new one."""
        pm = await self.get(payment_method_id)
        if pm is None:
            return None

        # Unset any existing default for this customer
        existing = await self.list_for_customer(pm.customer_id)
        for method in existing:
            if method.is_default and method.id != payment_method_id:
                method.is_default = False

        pm.is_default = True
        await self.session.commit()
        await self.session.refresh(pm)
        return pm
