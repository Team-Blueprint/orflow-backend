import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import _require_tenant
from app.core.exceptions import EntityNotFoundError, ErrorResponse
from app.db.database import get_async_db
from app.payment_methods.schemas import (
    PaymentMethodCreate,
    PaymentMethodRead,
    PaymentMethodUpdate,
)
from app.payment_methods.service import PaymentMethodService

router = APIRouter(
    prefix="/payment-methods",
    tags=["payment_methods"],
    dependencies=[Depends(_require_tenant)],
)

@router.post(
    "/create", 
    response_model=PaymentMethodRead, 
    status_code=201,
    summary="Create a payment method",
    description="Registers a new payment method (like a tokenized card) for a customer."
)
async def create_payment_method(
    payload: PaymentMethodCreate,
    db: AsyncSession = Depends(get_async_db),
):
    service = PaymentMethodService(db)
    return await service.create(**payload.model_dump())

@router.get(
    "/customer/{customer_id}", 
    response_model=list[PaymentMethodRead],
    summary="List customer payment methods",
    description="Lists all payment methods registered to a specific customer."
)
async def list_payment_methods_for_customer(
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = PaymentMethodService(db)
    return await service.list_for_customer(customer_id)

@router.get(
    "/{payment_method_id}/get", 
    response_model=PaymentMethodRead,
    summary="Get a payment method",
    description="Fetches a specific payment method by ID.",
    responses={
        404: {"model": ErrorResponse, "description": "Payment method not found."}
    }
)
async def get_payment_method(
    payment_method_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = PaymentMethodService(db)
    pm = await service.get(payment_method_id)
    if pm is None:
        raise EntityNotFoundError("PaymentMethod", str(payment_method_id))
    return pm

@router.patch(
    "/{payment_method_id}/update", 
    response_model=PaymentMethodRead,
    summary="Update a payment method",
    description="Partially updates a payment method's details.",
    responses={
        404: {"model": ErrorResponse, "description": "Payment method not found."}
    }
)
async def update_payment_method(
    payment_method_id: uuid.UUID,
    payload: PaymentMethodUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    service = PaymentMethodService(db)
    pm = await service.update(
        payment_method_id,
        **{k: v for k, v in payload.model_dump().items() if v is not None},
    )
    if pm is None:
        raise EntityNotFoundError("PaymentMethod", str(payment_method_id))
    return pm

@router.post(
    "/{payment_method_id}/set-default", 
    response_model=PaymentMethodRead,
    summary="Set default payment method",
    description="Marks a payment method as the default for the customer.",
    responses={
        404: {"model": ErrorResponse, "description": "Payment method not found."}
    }
)
async def set_default_payment_method(
    payment_method_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = PaymentMethodService(db)
    pm = await service.set_default(payment_method_id)
    if pm is None:
        raise EntityNotFoundError("PaymentMethod", str(payment_method_id))
    return pm

@router.delete(
    "/{payment_method_id}/del", 
    status_code=204,
    summary="Delete a payment method",
    description="Deletes a payment method permanently.",
    responses={
        404: {"model": ErrorResponse, "description": "Payment method not found."}
    }
)
async def delete_payment_method(
    payment_method_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = PaymentMethodService(db)
    deleted = await service.delete(payment_method_id)
    if not deleted:
        raise EntityNotFoundError("PaymentMethod", str(payment_method_id))
