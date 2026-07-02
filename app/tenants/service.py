"""Tenant auth and API key management service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import bcrypt
import jwt
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.tenants.models import Tenant

KeyType = Literal["pk_test", "sk_test", "pk_live", "sk_live"]



def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())



def _issue_token_pair(tenant_id: UUID) -> dict[str, str]:
    """Return a fresh access + refresh token pair for *tenant_id*."""
    now = datetime.now(timezone.utc)

    access_payload = {
        "sub": str(tenant_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    refresh_payload = {
        "sub": str(tenant_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS),
    }
    return {
        "access_token": jwt.encode(
            access_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
        ),
        "refresh_token": jwt.encode(
            refresh_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
        ),
        "token_type": "bearer",
    }


def verify_access_token(token: str) -> UUID:
    """Decode and validate an access token; return the tenant UUID.

    Raises ``jwt.InvalidTokenError`` (or a subclass) if the token is expired,
    tampered with, or of the wrong type.
    """
    payload = jwt.decode(
        token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return UUID(payload["sub"])


def verify_refresh_token(token: str) -> UUID:
    """Decode and validate a refresh token; return the tenant UUID."""
    payload = jwt.decode(
        token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
    )
    if payload.get("type") != "refresh":
        raise jwt.InvalidTokenError("Not a refresh token")
    return UUID(payload["sub"])



class TenantService:
    """
    Tenant-level operations.

    Tenant has no ``tenant_id`` FK so it does NOT use BaseRepository —
    queries are unrestricted by design.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session


    async def signup(self, name: str, email: str, password: str) -> tuple[Tenant, dict]:

        existing = await self._get_by_email(email)
        if existing is not None:
            raise ValueError("Email already registered")

        hashed_password = _hash_password(password)

        tenant = Tenant(
            name=name,
            email=email,
            hashed_password=hashed_password,
        )
        self.session.add(tenant)
        await self.session.commit()
        await self.session.refresh(tenant)

        tokens = _issue_token_pair(tenant.id)
        return tenant, tokens

    async def signin(self, email: str, password: str) -> tuple[Tenant, dict]:
        """Verify credentials; return the tenant and a fresh token pair."""
        tenant = await self._get_by_email(email)
        if tenant is None or not _verify_password(password, tenant.hashed_password):
            raise ValueError("Invalid email or password")
        if not tenant.is_active:
            raise ValueError("Tenant account is inactive")

        tokens = _issue_token_pair(tenant.id)
        return tenant, tokens

    async def refresh(self, refresh_token: str) -> dict:
        """Validate a refresh token and issue a new token pair."""
        try:
            tenant_id = verify_refresh_token(refresh_token)
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"Invalid refresh token: {exc}") from exc

        tenant = await self._get_by_id(tenant_id)
        if tenant is None or not tenant.is_active:
            raise ValueError("Tenant not found or inactive")

        return _issue_token_pair(tenant.id)

    # ── API key management ────────────────────────────────────────────────────

    async def get_by_api_key(self, key: str) -> Tenant | None:
        """Resolve an API key to its tenant.

        Checks all four key columns; only returns tenants whose specific key
        slot is active.  Returns ``None`` if not found or key is revoked.
        """
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.is_active.is_(True),
                or_(
                    (Tenant.pk_test == key) & Tenant.pk_test_active.is_(True),
                    (Tenant.sk_test == key) & Tenant.sk_test_active.is_(True),
                    (Tenant.pk_live == key) & Tenant.pk_live_active.is_(True),
                    (Tenant.sk_live == key) & Tenant.sk_live_active.is_(True),
                ),
            )
        )
        return result.scalar_one_or_none()

    async def create_key(self, tenant_id: UUID, key_type: KeyType) -> tuple[Tenant, str]:
        """Generate and store the first key for *key_type*.

        Raises ``ValueError`` if a key for this slot already exists — use
        ``regenerate_key`` to replace an existing key.

        Returns the updated tenant and the new key value.
        """
        tenant = await self._get_by_id(tenant_id)
        if tenant is None:
            raise ValueError("Tenant not found")
        if getattr(tenant, key_type) is not None:
            raise ValueError(
                f"Key '{key_type}' already exists — use /auth/keys/regenerate to replace it"
            )

        new_key = Tenant.generate_key(key_type)
        setattr(tenant, key_type, new_key)
        setattr(tenant, f"{key_type}_active", True)
        await self.session.commit()
        await self.session.refresh(tenant)
        return tenant, new_key

    async def regenerate_key(self, tenant_id: UUID, key_type: KeyType) -> tuple[Tenant, str]:
        """Generate a new value for *key_type* and persist it.

        Old key is overwritten immediately — there is no grace period.
        Returns the updated tenant and the new key value.
        """
        tenant = await self._get_by_id(tenant_id)
        if tenant is None:
            raise ValueError("Tenant not found")

        new_key = Tenant.generate_key(key_type)
        setattr(tenant, key_type, new_key)
        setattr(tenant, f"{key_type}_active", True)  # re-activate if was revoked
        await self.session.commit()
        await self.session.refresh(tenant)
        return tenant, new_key

    async def revoke_key(self, tenant_id: UUID, key_type: KeyType) -> Tenant:
        """Soft-revoke *key_type* by setting its active flag to False."""
        tenant = await self._get_by_id(tenant_id)
        if tenant is None:
            raise ValueError("Tenant not found")

        setattr(tenant, f"{key_type}_active", False)
        await self.session.commit()
        await self.session.refresh(tenant)
        return tenant

    async def get_tenant(self, tenant_id: UUID) -> Tenant | None:
        return await self._get_by_id(tenant_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_by_email(self, email: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.email == email)
        )
        return result.scalar_one_or_none()

    async def _get_by_id(self, tenant_id: UUID) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return result.scalar_one_or_none()
