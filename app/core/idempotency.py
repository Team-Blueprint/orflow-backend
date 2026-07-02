"""IdempotencyMiddleware — dedup for POST and PATCH requests.

Header: ``Idempotency-Key: <client-generated UUID>``

Redis is accessed via a module-level ``_get_redis()`` factory so tests can
replace ``app.core.idempotency._redis_client`` without needing to patch
instance attributes.
"""
import json

import redis.asyncio as aioredis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.context import current_tenant_id
from app.core.middleware import _is_exempt

_IDEMPOTENT_METHODS = {"POST", "PATCH"}


_redis_client: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL, encoding="utf-8", decode_responses=True
        )
    return _redis_client


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path):
            return await call_next(request)

        if request.method not in _IDEMPOTENT_METHODS:
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return await call_next(request)

        tenant_id = current_tenant_id.get()
        if tenant_id is None:
            return await call_next(request)

        redis = _get_redis()
        redis_key = f"idempotency:{tenant_id}:{idempotency_key}"

        cached_raw = await redis.get(redis_key)
        if cached_raw is not None:
            cached = json.loads(cached_raw)
            return Response(
                content=cached["body"],
                status_code=cached["status_code"],
                media_type="application/json",
                headers={"X-Idempotency-Replayed": "true"},
            )

        response = await call_next(request)

        body_bytes = b""
        async for chunk in response.body_iterator:
            body_bytes += chunk

        body_str = body_bytes.decode("utf-8", errors="replace")

        if response.status_code < 500:
            payload = json.dumps(
                {"status_code": response.status_code, "body": body_str}
            )
            await redis.setex(redis_key, settings.IDEMPOTENCY_TTL_SECONDS, payload)

        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
