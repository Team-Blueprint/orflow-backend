"""Shared domain exceptions.

These are deliberately framework-agnostic — they carry structured data, not HTTP
concerns. API ergonomics maps them to the standardized error
response shape; until then callers can catch and inspect them directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

class ErrorDetail(BaseModel):
    code: str = Field(..., description="A snake_case error code for programmatic handling.")
    message: str = Field(..., description="A human-readable error message.")
    details: dict | None = Field(default=None, description="Optional extra details about the error.")

class ErrorResponse(BaseModel):
    error: ErrorDetail

class EntityNotFoundError(Exception):
    """Raised when an entity is not found in the database."""
    def __init__(self, entity_name: str, entity_id: str | None = None) -> None:
        self.entity_name = entity_name
        self.entity_id = entity_id
        msg = f"{entity_name} not found"
        if entity_id:
            msg += f": {entity_id}"
        super().__init__(msg)

class InvalidStateTransition(Exception):
    """Raised when code attempts a status change the state machine forbids.

    All status mutations flow through ``transition_subscription`` /
    ``transition_invoice`` — this is the single error those functions raise so an
    illegal move can never be silently applied.
    """

    def __init__(self, entity: str, from_status: object, to_status: object) -> None:
        self.entity = entity
        # Normalize enum members to their value for readable messages.
        self.from_status = getattr(from_status, "value", from_status)
        self.to_status = getattr(to_status, "value", to_status)
        super().__init__(
            f"Cannot transition {entity} from {self.from_status!r} to {self.to_status!r}"
        )
