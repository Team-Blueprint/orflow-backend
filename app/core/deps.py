"""Shared FastAPI dependencies for authentication and project scoping."""

import uuid

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_project_id, current_tenant_id
from app.db.database import get_async_db
from app.projects.models import Project
from app.tenants.service import verify_access_token

_bearer = HTTPBearer(auto_error=False)


async def _require_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_async_db),
):
    """Authenticate tenant from API key context or JWT.

    Checks if ``current_tenant_id`` is already set (by TenantAuthMiddleware
    for API-key-authenticated requests). Falls back to JWT from the
    ``Authorization`` header or ``access_token`` cookie for dashboard users.
    """
    if request.method == "OPTIONS":
        return None

    tid = current_tenant_id.get()
    if tid is not None:
        return tid

    token = None
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get("access_token")

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication",
        )

    try:
        tenant_id = verify_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )

    current_tenant_id.set(tenant_id)
    return tenant_id


async def _require_project(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    tenant_id: uuid.UUID = Depends(_require_tenant),
    x_project_id: str | None = Header(None, alias="X-Project-ID"),
):
    """Validate X-Project-ID header and set current_project_id context var.

    Must be used on all project-scoped resource endpoints.
    The project must belong to the authenticated tenant.
    """
    if request.method == "OPTIONS":
        return None

    project_id_str = x_project_id
    if project_id_str is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Project-ID header",
        )

    try:
        project_id = uuid.UUID(project_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-Project-ID header (must be a valid UUID)",
        )

    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    token = current_project_id.set(project_id)
    request.state.project_id = project_id
    return project
