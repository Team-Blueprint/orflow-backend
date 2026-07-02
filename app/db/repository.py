from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_tenant_id
from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """

    Tenant isolation is enforced here — every SELECT automatically
    filters by the tenant ID stored in the request context var.
    No other code is allowed to bypass this by querying the model directly.
    """

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    def _tenant_id(self) -> UUID:
        tid = current_tenant_id.get()
        if tid is None:
            raise RuntimeError(
                "No tenant in context. TenantAuthMiddleware must run first."
            )
        return tid

    def _base_query(self):
        return select(self.model).where(
            self.model.tenant_id == self._tenant_id()  # type: ignore[attr-defined]
        )

    async def get(self, record_id: UUID) -> ModelT | None:
        result = await self.session.execute(
            self._base_query().where(self.model.id == record_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ModelT]:
        result = await self.session.execute(
            self._base_query().offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, **kwargs) -> ModelT:
        kwargs.setdefault("tenant_id", self._tenant_id())
        obj = self.model(**kwargs)
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def update(self, record_id: UUID, **kwargs) -> ModelT | None:
        obj = await self.get(record_id)
        if obj is None:
            return None
        for field, value in kwargs.items():
            setattr(obj, field, value)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def delete(self, record_id: UUID) -> bool:
        obj = await self.get(record_id)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.commit()
        return True
