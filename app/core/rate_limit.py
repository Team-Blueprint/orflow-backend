import time

import redis.asyncio as aioredis
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.context import current_tenant_id
from app.core.middleware import _is_exempt


_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL, encoding="utf-8", decode_responses=True
        )
    return _redis_client


class RateLimitMiddleware(BaseHTTPMiddleware):
    pass

    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path):
            return await call_next(request)

        tenant_id = current_tenant_id.get()
        if tenant_id is None:
            return await call_next(request)

        redis = _get_redis()
        limit = await self._resolve_limit(str(tenant_id), redis)
        minute_bucket = int(time.time()) // 60
        counter_key = f"rate_limit:{tenant_id}:{minute_bucket}"

        count = await redis.incr(counter_key)
        if count == 1:
            await redis.expire(counter_key, 120)

        if count > limit:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": f"Rate limit of {limit} requests/minute exceeded.",
                    }
                },
                headers={"Retry-After": "60"},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        return response

    async def _resolve_limit(self, tenant_id: str, redis: aioredis.Redis) -> int:
        cache_key = f"rate_limit_cfg:{tenant_id}"
        cached = await redis.get(cache_key)
        if cached is not None:
            return int(cached)
        limit = await self._fetch_plan_limit(tenant_id)
        await redis.setex(cache_key, settings.RATE_LIMIT_CACHE_TTL_SECONDS, str(limit))
        return limit

    async def _fetch_plan_limit(self, tenant_id: str) -> int:
        """Query the DB for the tenant's active plan rate limit."""
        # Lazy import to avoid circular deps at module load.
        from app.db.database import AsyncSessionLocal
        from app.plans.models import Plan
        from app.subscriptions.models import Subscription, SubscriptionStatus

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Plan.api_rate_limit_per_minute)
                    .join(
                        Subscription,
                        (Subscription.plan_id == Plan.id)
                        & (Subscription.tenant_id == tenant_id)
                        & (Subscription.status == SubscriptionStatus.active),
                    )
                    .where(Plan.tenant_id == tenant_id)
                    .limit(1)
                )
                row = result.scalar_one_or_none()
                if row is not None:
                    return int(row)
        except Exception:
            
            pass

        return settings.RATE_LIMIT_DEFAULT_PER_MINUTE
