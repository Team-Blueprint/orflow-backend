"""Per-request context variables.

These are set by middleware early in the request lifecycle and consumed by
downstream code (repositories, services, logging) without passing values
through every function signature.
"""
from contextvars import ContextVar
from uuid import UUID

# Holds the authenticated tenant's ID for the lifetime of a request.
# Set by TenantAuthMiddleware; consumed by BaseRepository to auto-filter queries.
current_tenant_id: ContextVar[UUID | None] = ContextVar(
    "current_tenant_id", default=None
)

# Identifies which API key slot was used to authenticate this request.
# Values: "pk_test" | "sk_test" | "pk_live" | "sk_live" | None
# Useful for enforcing live/test mode restrictions per-endpoint in the future.
current_key_type: ContextVar[str | None] = ContextVar(
    "current_key_type", default=None
)

# Holds the active project's ID for the duration of a request.
# Set by _require_project dependency; consumed by BaseRepository to auto-filter queries.
current_project_id: ContextVar[UUID | None] = ContextVar(
    "current_project_id", default=None
)

# Unique ID for this request — used in logs and echoed in X-Request-ID header.
# Set by RequestIDMiddleware before any other processing.
current_request_id: ContextVar[str | None] = ContextVar(
    "current_request_id", default=None
)
