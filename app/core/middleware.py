"""TenantAuthMiddleware — resolves tenant from one of four API key columns.

Reads ``X-API-Key`` from the request header, searches all active key slots
(pk_test, sk_test, pk_live, sk_live) on the Tenant table, and stores both
the tenant ID and the matching key type in request-scoped context vars.


"""
from sqlalchemy import or_, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.context import current_key_type, current_tenant_id
from app.db.database import AsyncSessionLocal

# Paths that require no API key — auth endpoints, docs, health, inbound webhooks.
# Update these whenever a new public path is added or the /v1 prefix changes.
_EXEMPT_PREFIXES = (
    "/v1/auth/",
    "/v1/webhooks/inbound/",
    "/docs",
    "/redoc",
    "/openapi.json",
)
_EXEMPT_EXACT = {"/"}


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """
    Reads ``X-API-Key`` header, resolves it to a Tenant record across all
    four key columns (active only), and stores the tenant's ID and key type
    in context vars for the duration of the request.

    Returns 401 if the key is missing, not found, or belongs to an
    inactive tenant / revoked key slot.
    """

    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing X-API-Key header"},
            )

        # Late import avoids circular dependency at module load time.
        from app.tenants.models import Tenant

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Tenant).where(
                    Tenant.is_active.is_(True),
                    or_(
                        (Tenant.pk_test == api_key) & Tenant.pk_test_active.is_(True),
                        (Tenant.sk_test == api_key) & Tenant.sk_test_active.is_(True),
                        (Tenant.pk_live == api_key) & Tenant.pk_live_active.is_(True),
                        (Tenant.sk_live == api_key) & Tenant.sk_live_active.is_(True),
                    ),
                )
            )
            tenant = result.scalar_one_or_none()

        if tenant is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or revoked API key"},
            )

        # Determine which slot matched so downstream code can enforce
        # live/test mode restrictions if needed.
        matched_key_type: str | None = None
        for slot in ("pk_test", "sk_test", "pk_live", "sk_live"):
            if getattr(tenant, slot) == api_key:
                matched_key_type = slot
                break

        t_token = current_tenant_id.set(tenant.id)
        k_token = current_key_type.set(matched_key_type)
        try:
            response = await call_next(request)
        finally:
            current_tenant_id.reset(t_token)
            current_key_type.reset(k_token)

        return response
