import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.providers.deps import get_payment_provider_for_mode
from app.core.context import current_is_test
from app.core.deps import _require_project, _require_tenant
from app.core.exceptions import EntityNotFoundError, ErrorResponse
from app.projects.models import Project
from app.subscriptions.schemas import (
    ChangePlanRequest,
    ChangePlanResponse,
    SubscriberRead,
    SubscriptionCreate,
    SubscriptionCreateResponse,
    SubscriptionRead,
    SubscriptionWithPlanRead,
    SubscriptionAuditLogRead,
)
from app.subscriptions.service import SubscriptionService



router = APIRouter(
    prefix="/subscriptions", 
    tags=["subscriptions"],
    dependencies=[Depends(_require_tenant)]
)

@router.post(
    "/create", 
    response_model=SubscriptionCreateResponse, 
    status_code=status.HTTP_201_CREATED,
    summary="Create a new subscription",
    description="Creates a new subscription for a customer to a plan. If the plan has a trial period, it starts in `trialing` state. Otherwise, it initiates a checkout session to collect the first payment, returning `checkout_link` and `order_reference`.",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid payload or missing payment method for instant charge."},
        404: {"model": ErrorResponse, "description": "Customer, Plan, or Payment Method not found."}
    }
)
async def create_subscription(
    payload: SubscriptionCreate,
    session: AsyncSession = Depends(get_async_db),
):
    provider = get_payment_provider_for_mode(current_is_test.get())
    svc = SubscriptionService(session)
    return await svc.create_subscription_flow(payload, provider)

@router.post(
    "/{subscription_id}/cancel", 
    response_model=SubscriptionRead,
    summary="Cancel a subscription",
    description="Transitions a subscription to `canceled` status immediately. Stops all future billing.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription not found."},
        409: {"model": ErrorResponse, "description": "Invalid state transition (e.g., already canceled)."}
    }
)
async def cancel_subscription(
    subscription_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_db),
):
    svc = SubscriptionService(session)
    return await svc.cancel_subscription_flow(subscription_id)

@router.post(
    "/{subscription_id}/pause", 
    response_model=SubscriptionRead,
    summary="Pause a subscription",
    description="Transitions an active subscription to `paused` status. It will not be billed until resumed.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription not found."},
        409: {"model": ErrorResponse, "description": "Invalid state transition."}
    }
)
async def pause_subscription(
    subscription_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_db),
):
    svc = SubscriptionService(session)
    return await svc.pause_subscription_flow(subscription_id)

@router.post(
    "/{subscription_id}/resume", 
    response_model=SubscriptionRead,
    summary="Resume a paused subscription",
    description="Transitions a paused subscription back to `active` status.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription not found."},
        409: {"model": ErrorResponse, "description": "Invalid state transition."}
    }
)
async def resume_subscription(
    subscription_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_db),
):
    svc = SubscriptionService(session)
    return await svc.resume_subscription_flow(subscription_id)


@router.post(
    "/{subscription_id}/change-plan",
    response_model=ChangePlanResponse,
    summary="Change a subscription's plan with proration",
    description=(
        "Switches an active subscription to a new plan, crediting unused time on "
        "the old plan and charging the remaining time on the new plan as explicit "
        "invoice line items. The net amount is charged immediately; a failed "
        "charge falls into the dunning flow."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Cannot change plan (e.g. currency mismatch)."},
        404: {"model": ErrorResponse, "description": "Subscription or Plan not found."},
        409: {"model": ErrorResponse, "description": "Invalid state transition."}
    }
)
async def change_plan(
    subscription_id: uuid.UUID,
    payload: ChangePlanRequest,
    session: AsyncSession = Depends(get_async_db),
):
    provider = get_payment_provider_for_mode(current_is_test.get())
    svc = SubscriptionService(session)
    return await svc.change_plan_flow(subscription_id, payload.new_plan_id, provider)

@router.get(
    "/{subscription_id}/audit-log",
    response_model=list[SubscriptionAuditLogRead],
    summary="Get subscription audit log",
    description="Returns the full history of state transitions for a specific subscription, newest first.",
    responses={
        404: {"model": ErrorResponse, "description": "Subscription not found."}
    }
)
async def get_audit_log(
    subscription_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_db)
):
    from sqlalchemy import select
    from app.audit.models import AuditLog, AuditEntityType

    # verify sub exists
    svc = SubscriptionService(session)
    sub = await svc.get(subscription_id)
    if not sub:
        raise EntityNotFoundError("Subscription", str(subscription_id))

    stmt = (
        select(AuditLog)
        .where(
            AuditLog.entity_id == subscription_id,
            AuditLog.entity_type == AuditEntityType.subscription
        )
        .order_by(AuditLog.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()

@router.get(
    "/list",
    response_model=list[SubscriptionWithPlanRead],
    summary="List subscriptions",
    description="Returns all subscriptions for the current tenant and project. Optionally filter by plan_id.",
    responses={
        400: {"model": ErrorResponse, "description": "Missing X-Project-ID header."}
    }
)
async def list_subscriptions(
    plan_id: uuid.UUID | None = None,
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    _project: Project = Depends(_require_project),
):
    svc = SubscriptionService(db)
    rows = await svc.list_by_project(
        _project.id, plan_id=plan_id, offset=offset, limit=limit
    )
    return [SubscriptionWithPlanRead.from_db(sub, plan) for sub, plan in rows]

@router.get(
    "/subscribers/list",
    response_model=list[SubscriberRead],
    summary="List subscribers",
    description="Returns all customers with subscriptions in the current project. Optionally filter by plan_id.",
    responses={
        400: {"model": ErrorResponse, "description": "Missing X-Project-ID header."}
    }
)
async def list_subscribers(
    plan_id: uuid.UUID | None = None,
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    _project: Project = Depends(_require_project),
):
    svc = SubscriptionService(db)
    customers, subs_map = await svc.list_subscribers_by_project(
        _project.id, plan_id=plan_id, offset=offset, limit=limit
    )
    return [SubscriberRead.from_db(c, subs_map.get(c.id, [])) for c in customers]

