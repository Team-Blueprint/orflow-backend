import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.projects.service import ProjectService
from app.core.exceptions import EntityNotFoundError, ErrorResponse
from app.core.deps import _require_tenant

router = APIRouter(prefix="/projects", tags=["Projects"], dependencies=[Depends(_require_tenant)])


@router.post(
    "/create",
    response_model=ProjectRead,
    status_code=201,
    summary="Create a new project",
    description="Creates a new project for the authenticated tenant.",
)
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_async_db),
):
    service = ProjectService(db)
    return await service.create(name=payload.name, description=payload.description)


@router.get(
    "/list",
    response_model=list[ProjectRead],
    summary="List all projects",
    description="Returns all projects for the authenticated tenant.",
)
async def list_projects(
    db: AsyncSession = Depends(get_async_db),
):
    service = ProjectService(db)
    return await service.list()


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Get a project",
    description="Fetches a specific project by ID.",
    responses={404: {"model": ErrorResponse, "description": "Project not found."}},
)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = ProjectService(db)
    project = await service.get(project_id)
    if project is None:
        raise EntityNotFoundError("Project", str(project_id))
    return project


@router.patch(
    "/{project_id}/update",
    response_model=ProjectRead,
    summary="Update a project",
    description="Partially updates a project's name or description.",
    responses={404: {"model": ErrorResponse, "description": "Project not found."}},
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    service = ProjectService(db)
    project = await service.update(
        project_id, **{k: v for k, v in payload.model_dump().items() if v is not None}
    )
    if project is None:
        raise EntityNotFoundError("Project", str(project_id))
    return project


@router.delete(
    "/{project_id}/del",
    status_code=204,
    summary="Delete a project",
    description="Deletes a project and all its resources.",
    responses={404: {"model": ErrorResponse, "description": "Project not found."}},
)
async def delete_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = ProjectService(db)
    deleted = await service.delete(project_id)
    if not deleted:
        raise EntityNotFoundError("Project", str(project_id))
