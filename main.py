import logging
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
from fastapi import FastAPI, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.middleware import TenantAuthMiddleware
from app.core.request_id import RequestIDMiddleware
from app.core.rate_limit import RateLimitMiddleware
from app.core.idempotency import IdempotencyMiddleware
from app.db.database import engine
from app.providers.deps import close_payment_providers, init_payment_providers
#alembic config
import app.tenants.models
import app.customers.models
import app.plans.models
import app.payment_methods.models
import app.subscriptions.models
import app.invoices.models
import app.audit.models
import app.webhooks.models
import app.reconciliation.models
import app.projects.models
import app.subscription_pages.models
from app.tenants.router import router as tenants_router
from app.customers.router import router as customers_router
from app.plans.router import router as plans_router
from app.payment_methods.router import router as payment_methods_router
from app.subscriptions.router import router as subscriptions_router
from app.webhooks.router import router as webhooks_router
from app.webhooks.outbound_router import router as outbound_webhooks_router
from app.reconciliation.router import router as reconciliation_router
from app.projects.router import router as projects_router
from app.analytics.router import router as analytics_router
from app.subscription_pages.router import router as subscription_pages_router
from app.subscription_pages.router import public_router as subscription_pages_public_router
from app.core.exceptions import EntityNotFoundError, InvalidStateTransition
from fastapi.responses import JSONResponse
from fastapi import Request, HTTPException

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_payment_providers()
    yield
    await close_payment_providers()
    await engine.dispose()


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="orflow (Subscription Engine)",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(api_key_header)],
)


def _parse_cors_origins(origins_str: str) -> list[str]:
    """Parse comma-separated CORS origins string into a list."""
    return [origin.strip() for origin in origins_str.split(",") if origin.strip()]


app.add_middleware(IdempotencyMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TenantAuthMiddleware)
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(settings.CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Project-ID", "X-API-Key"],
)

app.include_router(tenants_router, prefix="/v1")
app.include_router(customers_router, prefix="/v1")
app.include_router(plans_router, prefix="/v1")
app.include_router(payment_methods_router, prefix="/v1")
app.include_router(subscriptions_router, prefix="/v1")
app.include_router(webhooks_router, prefix="/v1")
app.include_router(outbound_webhooks_router, prefix="/v1")
app.include_router(reconciliation_router, prefix="/v1")
app.include_router(projects_router, prefix="/v1")
app.include_router(analytics_router, prefix="/v1")
app.include_router(subscription_pages_router, prefix="/v1")
app.include_router(subscription_pages_public_router, prefix="/v1")

@app.exception_handler(EntityNotFoundError)
async def entity_not_found_handler(request: Request, exc: EntityNotFoundError):
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": "not_found",
                "message": str(exc),
                "details": {"entity": exc.entity_name, "id": exc.entity_id}
            }
        }
    )

@app.exception_handler(InvalidStateTransition)
async def invalid_state_transition_handler(request: Request, exc: InvalidStateTransition):
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "invalid_state_transition",
                "message": str(exc),
                "details": {
                    "entity": exc.entity,
                    "from_status": exc.from_status,
                    "to_status": exc.to_status
                }
            }
        }
    )

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "bad_request",
                "message": str(exc),
                "details": {}
            }
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Try to map common status codes to codes
    code = "error"
    if exc.status_code == 404:
        code = "not_found"
    elif exc.status_code == 401:
        code = "unauthorized"
    elif exc.status_code == 403:
        code = "forbidden"
    elif exc.status_code == 409:
        code = "conflict"
    elif exc.status_code == 400:
        code = "bad_request"
        
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": code,
                "message": exc.detail,
                "details": {}
            }
        },
        headers=exc.headers
    )


@app.get("/", tags=["health"])
async def health_check():
    return {"status": "ok"}