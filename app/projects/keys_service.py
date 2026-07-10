from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.keys_models import ProjectApiKey


KEY_TYPES = ("pk_test", "sk_test", "pk_live", "sk_live")


class ProjectApiKeyService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_keys(self, project_id: UUID) -> list[ProjectApiKey]:
        result = await self.session.execute(
            select(ProjectApiKey)
            .where(ProjectApiKey.project_id == project_id)
            .order_by(ProjectApiKey.key_type)
        )
        return list(result.scalars().all())

    async def get_key(self, project_id: UUID, key_type: str) -> ProjectApiKey | None:
        result = await self.session.execute(
            select(ProjectApiKey).where(
                ProjectApiKey.project_id == project_id,
                ProjectApiKey.key_type == key_type,
            )
        )
        return result.scalar_one_or_none()

    async def create_key(
        self, project_id: UUID, tenant_id: UUID, key_type: str, name: str | None = None
    ) -> tuple[ProjectApiKey, str]:
        existing = await self.get_key(project_id, key_type)
        if existing is not None:
            raise ValueError(f"Key '{key_type}' already exists for this project")

        new_value = ProjectApiKey.generate_key(key_type)
        key = ProjectApiKey(
            project_id=project_id,
            tenant_id=tenant_id,
            key_value=new_value,
            key_type=key_type,
            is_active=True,
            name=name,
        )
        self.session.add(key)
        await self.session.commit()
        await self.session.refresh(key)
        return key, new_value

    async def regenerate_key(
        self, project_id: UUID, key_type: str
    ) -> tuple[ProjectApiKey, str]:
        key = await self.get_key(project_id, key_type)
        if key is None:
            raise ValueError(f"Key '{key_type}' does not exist for this project")

        new_value = ProjectApiKey.generate_key(key_type)
        key.key_value = new_value
        key.is_active = True
        await self.session.commit()
        await self.session.refresh(key)
        return key, new_value

    async def revoke_key(self, project_id: UUID, key_type: str) -> ProjectApiKey:
        key = await self.get_key(project_id, key_type)
        if key is None:
            raise ValueError(f"Key '{key_type}' does not exist for this project")

        key.is_active = False
        await self.session.commit()
        await self.session.refresh(key)
        return key

    async def get_by_key_value(self, key_value: str) -> ProjectApiKey | None:
        result = await self.session.execute(
            select(ProjectApiKey).where(
                ProjectApiKey.key_value == key_value,
                ProjectApiKey.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()
