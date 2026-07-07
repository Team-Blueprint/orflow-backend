import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import AnalyticsService
from app.core.context import current_is_test, current_tenant_id
from app.core.deps import _require_tenant
from app.db.database import get_async_db
from app.projects.models import Project

router = APIRouter(
    prefix="/projects",
    tags=["Analytics"],
    dependencies=[Depends(_require_tenant)],
)


@router.get(
    "/{project_id}/analytics",
    summary="Project analytics overview",
    description="Returns aggregated summary metrics and daily revenue chart for a project.",
)
async def get_project_analytics(
    project_id: uuid.UUID,
    days: int = Query(30, ge=1, le=365, description="Number of days for revenue data"),
    db: AsyncSession = Depends(get_async_db),
):
    tenant_id = current_tenant_id.get()

    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    is_test = current_is_test.get()
    since_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    service = AnalyticsService(db)
    total_volume = await service.get_total_volume(
        tenant_id, project_id, is_test, since_date
    )
    active_subscribers = await service.get_active_subscribers(
        tenant_id, project_id, is_test
    )
    total_customers = await service.get_total_customers(
        tenant_id, project_id, is_test
    )
    revenue_chart = await service.get_revenue_chart(
        tenant_id, project_id, is_test, since_date
    )
    currency = await service.get_currency(tenant_id, project_id, is_test)

    return {
        "summary": {
            "total_volume": total_volume,
            "active_subscribers": active_subscribers,
            "total_customers": total_customers,
            "currency": currency,
        },
        "revenue_chart": revenue_chart,
    }
