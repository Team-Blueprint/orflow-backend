import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.providers.base import PaymentProviderAdapter
from app.providers.deps import get_payment_provider
from app.subscriptions.schemas import (
    ChangePlanRequest,
    ChangePlanResponse,
    SubscriptionCreate,
    SubscriptionCreateResponse,
    SubscriptionRead,
    SubscriptionAuditLogRead,
)
from app.subscriptions.service import SubscriptionService

from app.core.exceptions import EntityNotFoundError, ErrorResponse



router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

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
    provider: PaymentProviderAdapter = Depends(get_payment_provider),
):
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
    provider: PaymentProviderAdapter = Depends(get_payment_provider),
):
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
    from app.subscriptions.models import Subscription

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

