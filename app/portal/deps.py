from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.core.context import current_tenant_id
from app.db.database import get_async_db
from app.customers.models import Customer
from app.portal.service import decode_portal_session_token

_bearer = HTTPBearer(auto_error=False)

async def get_current_portal_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_async_db),
) -> Customer:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication",
        )
        
    try:
        payload = decode_portal_session_token(credentials.credentials)
        customer_id = payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Portal session expired",
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        )
        
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer not found",
        )
        
    # Crucially, set the context tenant_id so tenant-isolated repositories work
    current_tenant_id.set(customer.tenant_id)
    return customer
