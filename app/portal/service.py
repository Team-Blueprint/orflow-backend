import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import settings
from app.core.exceptions import EntityNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.customers.models import Customer


def hash_pin(plain: str) -> str:
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_pin(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except ValueError:
        return False


def issue_portal_token(customer_id: UUID) -> str:
    """Return a short-lived access token for the portal session."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(customer_id),
        "type": "portal_access",
        "iat": now,
        "exp": now + timedelta(minutes=30),  # 30-minute session
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_portal_token(token: str) -> UUID:
    """Decode and validate a portal access token."""
    payload = jwt.decode(
        token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
    )
    if payload.get("type") != "portal_access":
        raise jwt.InvalidTokenError("Not a portal access token")
    return UUID(payload["sub"])


class PortalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_customer_by_token_slug(self, token_slug: str) -> Customer | None:
        result = await self.session.execute(
            select(Customer).where(Customer.portal_token_slug == token_slug)
        )
        return result.scalar_one_or_none()
