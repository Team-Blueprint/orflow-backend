import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.plans.schemas import PlanCreate, PlanRead, PlanUpdate
from app.plans.service import PlanService
from app.core.exceptions import EntityNotFoundError, ErrorResponse

router = APIRouter(prefix="/plans", tags=["plans"])

@router.post(
    "/create", 
    response_model=PlanRead, 
    status_code=201,
    summary="Create a new plan",
    description="Creates a new billing plan defining pricing, interval, and features."
)
async def create_plan(
    payload: PlanCreate,
    db: AsyncSession = Depends(get_async_db),
):
    service = PlanService(db)
    data = payload.model_dump()
    # Convert major-unit input (e.g. naira) to minor-unit storage (kobo)
    data["amount"] = int(Decimal(str(data["amount"])) * Decimal(100))
    return await service.create(**data)

@router.get(
    "/list", 
    response_model=list[PlanRead],
    summary="List all plans",
    description="Returns a paginated list of all plans for the current tenant."
)
async def list_plans(
    active_only: bool = False,
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
):
    service = PlanService(db)
    if active_only:
        return await service.list_active()
    return await service.list(offset=offset, limit=limit)

@router.get(
    "/{plan_id}", 
    response_model=PlanRead,
    summary="Get a plan",
    description="Fetches a specific plan by ID.",
    responses={
        404: {"model": ErrorResponse, "description": "Plan not found."}
    }
)
async def get_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = PlanService(db)
    plan = await service.get(plan_id)
    if plan is None:
        raise EntityNotFoundError("Plan", str(plan_id))
    return plan

@router.patch(
    "/{plan_id}/update", 
    response_model=PlanRead,
    summary="Update a plan",
    description="Partially updates a plan's information.",
    responses={
        404: {"model": ErrorResponse, "description": "Plan not found."}
    }
)
async def update_plan(
    plan_id: uuid.UUID,
    payload: PlanUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    service = PlanService(db)
    plan = await service.update(
        plan_id, **{k: v for k, v in payload.model_dump().items() if v is not None}
    )
    if plan is None:
        raise EntityNotFoundError("Plan", str(plan_id))
    return plan

@router.post(
    "/{plan_id}/archive", 
    response_model=PlanRead,
    summary="Archive a plan",
    description="Archives a plan so it can no longer be subscribed to.",
    responses={
        404: {"model": ErrorResponse, "description": "Plan not found."}
    }
)
async def archive_plan(
    plan_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = PlanService(db)
    plan = await service.archive(plan_id)
    if plan is None:
        raise EntityNotFoundError("Plan", str(plan_id))
    return plan
