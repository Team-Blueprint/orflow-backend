import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import _require_project
from app.core.exceptions import EntityNotFoundError, ErrorResponse
from app.db.database import get_async_db
from app.plans.models import Plan
from app.projects.models import Project

from app.subscription_pages.models import SubscriptionPage
from app.subscription_pages.schemas import (
    PublicCheckoutRequest,
    PublicCheckoutResponse,
    PublicPageInfo,
    PublicPlanInfo,
    SubscriptionPageCreate,
    SubscriptionPageRead,
    SubscriptionPageUpdate,
    SubscriptionPageWithPlanRead,
)
from app.subscription_pages.service import SubscriptionPageService, public_checkout_flow

router = APIRouter(
    prefix="/subscription-pages",
    tags=["subscription-pages"],
    dependencies=[Depends(_require_project)],
)

public_router = APIRouter(
    prefix="/subscription-pages",
    tags=["subscription-pages"],
)


@router.post(
    "/create",
    response_model=SubscriptionPageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a subscription page",
    description="Creates a shareable subscription page linked to a plan. A unique code is auto-generated for the URL.",
    responses={
        404: {"model": ErrorResponse, "description": "Plan not found."},
    },
)
async def create_subscription_page(
    payload: SubscriptionPageCreate,
    db: AsyncSession = Depends(get_async_db),
):
    plan_result = await db.execute(
        select(Plan).where(Plan.id == payload.plan_id)
    )
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise EntityNotFoundError("Plan", str(payload.plan_id))

    svc = SubscriptionPageService(db)
    return await svc.create_page(payload.plan_id)


@router.get(
    "/list",
    response_model=list[SubscriptionPageWithPlanRead],
    summary="List subscription pages",
    description="Returns all subscription pages for the current tenant and project.",
)
async def list_subscription_pages(
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
):
    svc = SubscriptionPageService(db)
    rows = await svc.list_with_plan(offset=offset, limit=limit)
    return [SubscriptionPageWithPlanRead.from_db(page, plan, project_name) for page, plan, project_name in rows]


@router.get(
    "/{page_id}",
    response_model=SubscriptionPageWithPlanRead,
    summary="Get a subscription page",
    description="Fetches a specific subscription page by ID.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription page not found."},
    },
)
async def get_subscription_page(
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    svc = SubscriptionPageService(db)
    row = await svc.get_with_plan(page_id)
    if not row:
        raise EntityNotFoundError("SubscriptionPage", str(page_id))
    page, plan, project_name = row
    return SubscriptionPageWithPlanRead.from_db(page, plan, project_name)


@router.patch(
    "/{page_id}/update",
    response_model=SubscriptionPageRead,
    summary="Update a subscription page",
    description="Partially updates a subscription page (e.g. change plan or deactivate).",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription page not found."},
    },
)
async def update_subscription_page(
    page_id: uuid.UUID,
    payload: SubscriptionPageUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    svc = SubscriptionPageService(db)
    page = await svc.update(
        page_id, **{k: v for k, v in payload.model_dump().items() if v is not None}
    )
    if not page:
        raise EntityNotFoundError("SubscriptionPage", str(page_id))
    return page


@router.delete(
    "/{page_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a subscription page",
    description="Permanently deletes a subscription page.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription page not found."},
    },
)
async def delete_subscription_page(
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    svc = SubscriptionPageService(db)
    deleted = await svc.delete(page_id)
    if not deleted:
        raise EntityNotFoundError("SubscriptionPage", str(page_id))


@public_router.get(
    "/code/{code}",
    response_model=PublicPageInfo,
    summary="Get plan info by page code (public)",
    description="Public endpoint that returns plan details for a given subscription page code. Used by the checkout frontend to render the page.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription page not found or inactive."},
    },
)
async def get_plan_by_code(
    code: str,
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(SubscriptionPage).where(
            SubscriptionPage.code == code,
            SubscriptionPage.is_active.is_(True),
        )
    )
    page = result.scalar_one_or_none()
    if not page:
        raise EntityNotFoundError("SubscriptionPage", code)

    plan_result = await db.execute(
        select(Plan).where(Plan.id == page.plan_id)
    )
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise EntityNotFoundError("Plan", str(page.plan_id))

    project = None
    if page.project_id:
        project_result = await db.execute(
            select(Project).where(Project.id == page.project_id)
        )
        project = project_result.scalar_one_or_none()

    return PublicPageInfo(
        id=page.id,
        plan_id=plan.id,
        name=plan.name,
        amount=plan.amount,
        currency=plan.currency,
        interval=plan.interval,
        interval_count=plan.interval_count,
        project_name=project.name if project else "Unknown",
        is_test=page.is_test,
    )


@public_router.post(
    "/code/{code}/checkout",
    response_model=PublicCheckoutResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate checkout for a subscription page (public)",
    description="Public endpoint that creates a customer and subscription, then returns a Nomba checkout link for card payment.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription page not found or inactive."},
    },
)
async def checkout_by_code(
    code: str,
    payload: PublicCheckoutRequest,
    db: AsyncSession = Depends(get_async_db),
):
    result = await public_checkout_flow(
        code=code,
        name=payload.name,
        email=payload.email,
        session=db,
    )
    return result
