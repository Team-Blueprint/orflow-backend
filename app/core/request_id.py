"""RequestIDMiddleware — per-request tracing identifier.

Behaviour:
- If the client sends ``X-Request-ID``, honour it (useful for client-side
  correlation — same ID appears in your logs and in the response header).
- Otherwise generate a fresh ``uuid4``.
- Store the ID in ``current_request_id`` context var so logging/services
  can attach it to structured log lines.
- echo the final ID back in the ``X-Request-ID`` response header.
"""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.context import current_request_id


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        token = current_request_id.set(request_id)
        try:
            response = await call_next(request)
        finally:
            current_request_id.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response
