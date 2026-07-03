import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.customers.schemas import CustomerCreate, CustomerRead, CustomerUpdate
from app.customers.service import CustomerService
from app.db.database import get_async_db
from app.core.deps import _require_project
from app.core.exceptions import EntityNotFoundError, ErrorResponse

router = APIRouter(prefix="/customers", tags=["customers"], dependencies=[Depends(_require_project)])

@router.post(
    "/create", 
    response_model=CustomerRead, 
    status_code=201,
    summary="Create a new customer",
    description="Registers a new customer for the tenant. Customers represent the entities that hold subscriptions and payment methods."
)
async def create_customer(
    payload: CustomerCreate,
    db: AsyncSession = Depends(get_async_db),
):
    service = CustomerService(db)
    return await service.create(**payload.model_dump())

@router.get(
    "/all", 
    response_model=list[CustomerRead],
    summary="List all customers",
    description="Returns a paginated list of all customers for the current tenant."
)
async def list_customers(
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
):
    service = CustomerService(db)
    return await service.list(offset=offset, limit=limit)

@router.get(
    "/{customer_id}", 
    response_model=CustomerRead,
    summary="Get a customer",
    description="Fetches a specific customer by ID.",
    responses={
        404: {"model": ErrorResponse, "description": "Customer not found."}
    }
)
async def get_customer(
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = CustomerService(db)
    customer = await service.get(customer_id)
    if customer is None:
        raise EntityNotFoundError("Customer", str(customer_id))
    return customer

@router.patch(
    "/{customer_id}/update", 
    response_model=CustomerRead,
    summary="Update a customer",
    description="Partially updates a customer's information.",
    responses={
        404: {"model": ErrorResponse, "description": "Customer not found."}
    }
)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    service = CustomerService(db)
    customer = await service.update(
        customer_id, **{k: v for k, v in payload.model_dump().items() if v is not None}
    )
    if customer is None:
        raise EntityNotFoundError("Customer", str(customer_id))
    return customer

@router.delete(
    "/{customer_id}/del", 
    status_code=204,
    summary="Delete a customer",
    description="Deletes a customer permanently.",
    responses={
        404: {"model": ErrorResponse, "description": "Customer not found."}
    }
)
async def delete_customer(
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    service = CustomerService(db)
    deleted = await service.delete(customer_id)
    if not deleted:
        raise EntityNotFoundError("Customer", str(customer_id))
