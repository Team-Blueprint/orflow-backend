from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_tenant_id
from app.projects.models import Project


class ProjectService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_id(self) -> UUID:
        tid = current_tenant_id.get()
        if tid is None:
            raise RuntimeError("No tenant in context.")
        return tid

    async def create(self, name: str, description: str | None = None) -> Project:
        project = Project(tenant_id=self._tenant_id(), name=name, description=description)
        self.session.add(project)
        await self.session.commit()
        await self.session.refresh(project)
        return project

    async def get(self, project_id: UUID) -> Project | None:
        result = await self.session.execute(
            select(Project).where(Project.id == project_id, Project.tenant_id == self._tenant_id())
        )
        return result.scalar_one_or_none()

    async def list(self) -> list[Project]:
        result = await self.session.execute(
            select(Project).where(Project.tenant_id == self._tenant_id()).order_by(Project.created_at)
        )
        return list(result.scalars().all())

    async def update(self, project_id: UUID, **kwargs) -> Project | None:
        project = await self.get(project_id)
        if project is None:
            return None
        for field, value in kwargs.items():
            setattr(project, field, value)
        await self.session.commit()
        await self.session.refresh(project)
        return project

    async def delete(self, project_id: UUID) -> bool:
        project = await self.get(project_id)
        if project is None:
            return False
        await self.session.delete(project)
        await self.session.commit()
        return True
