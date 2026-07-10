import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import _require_tenant
from app.core.exceptions import ErrorResponse
from app.db.database import get_async_db
from app.projects.keys_schemas import (
    ProjectApiKeyActionResponse,
    ProjectApiKeyCreateRequest,
    ProjectApiKeyRead,
    ProjectApiKeyRegenerateRequest,
    ProjectApiKeyRevokeRequest,
    ProjectApiKeyRevokeResponse,
)
from app.projects.keys_service import ProjectApiKeyService
from app.projects.models import Project
from app.tenants.router import _require_csrf, _require_jwt


async def _resolve_project(
    project_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(_require_tenant),
    db: AsyncSession = Depends(get_async_db),
) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


router = APIRouter(prefix="/projects/{project_id}/keys", tags=["Project API Keys"])


@router.get(
    "",
    response_model=list[ProjectApiKeyRead],
    summary="List project API keys",
    description="Returns all API keys for the specified project.",
)
async def list_project_keys(
    project_id: uuid.UUID,
    project: Project = Depends(_resolve_project),
    db: AsyncSession = Depends(get_async_db),
):
    service = ProjectApiKeyService(db)
    return await service.list_keys(project_id)


@router.post(
    "/create",
    response_model=ProjectApiKeyActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project API key",
    description="Creates a new API key for a specific key type on the project.",
)
async def create_project_key(
    project_id: uuid.UUID,
    payload: ProjectApiKeyCreateRequest,
    request: Request,
    tenant=Depends(_require_jwt),
    project: Project = Depends(_resolve_project),
    db: AsyncSession = Depends(get_async_db),
):
    _require_csrf(request)
    service = ProjectApiKeyService(db)
    try:
        key, new_value = await service.create_key(
            project_id, tenant.id, payload.key_type, name=payload.name
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return ProjectApiKeyActionResponse(
        key_type=payload.key_type,
        value=new_value,
        name=payload.name,
    )


@router.post(
    "/regenerate",
    response_model=ProjectApiKeyActionResponse,
    summary="Regenerate a project API key",
    description="Replaces an existing project API key with a new value.",
)
async def regenerate_project_key(
    project_id: uuid.UUID,
    payload: ProjectApiKeyRegenerateRequest,
    request: Request,
    tenant=Depends(_require_jwt),
    project: Project = Depends(_resolve_project),
    db: AsyncSession = Depends(get_async_db),
):
    _require_csrf(request)
    service = ProjectApiKeyService(db)
    try:
        key, new_value = await service.regenerate_key(project_id, payload.key_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    return ProjectApiKeyActionResponse(
        key_type=payload.key_type,
        value=new_value,
        name=key.name,
    )


@router.post(
    "/revoke",
    response_model=ProjectApiKeyRevokeResponse,
    summary="Revoke a project API key",
    description="Soft-revokes a project API key by setting its active flag to false.",
)
async def revoke_project_key(
    project_id: uuid.UUID,
    payload: ProjectApiKeyRevokeRequest,
    request: Request,
    tenant=Depends(_require_jwt),
    project: Project = Depends(_resolve_project),
    db: AsyncSession = Depends(get_async_db),
):
    _require_csrf(request)
    service = ProjectApiKeyService(db)
    try:
        await service.revoke_key(project_id, payload.key_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    return ProjectApiKeyRevokeResponse(key_type=payload.key_type)
