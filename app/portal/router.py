from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.customers.models import Customer
from app.portal.schemas import VerifyAccessRequest, VerifyAccessResponse, UpdatePinRequest
from app.portal.service import PortalService, verify_pin, issue_portal_token, hash_pin
from app.portal.deps import get_current_portal_customer

router = APIRouter(prefix="/v1/portal", tags=["Portal"])

@router.post("/verify-access", response_model=VerifyAccessResponse)
async def verify_access(
    payload: VerifyAccessRequest,
    db: AsyncSession = Depends(get_async_db)
):
    service = PortalService(db)
    customer = await service.get_customer_by_token_slug(payload.token_slug)
    
    if not customer or not customer.portal_pin_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access credentials"
        )
        
    if not verify_pin(payload.pin, customer.portal_pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access credentials"
        )
        
    token = issue_portal_token(customer.id)
    return VerifyAccessResponse(access_token=token)


@router.post("/update-pin", status_code=status.HTTP_204_NO_CONTENT)
async def update_pin(
    payload: UpdatePinRequest,
    db: AsyncSession = Depends(get_async_db),
    customer: Customer = Depends(get_current_portal_customer)
):
    if not customer.portal_pin_hash or not verify_pin(payload.current_pin, customer.portal_pin_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current PIN is incorrect"
        )
        
    customer.portal_pin_hash = hash_pin(payload.new_pin)
    await db.commit()
    return None
