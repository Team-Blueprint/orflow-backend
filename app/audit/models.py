import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class AuditEntityType(str, enum.Enum):
    subscription = "subscription"
    invoice = "invoice"


class AuditLog(Base):
    """
    Immutable record of a single state-machine transition.

    Every call to ``transition_subscription`` / ``transition_invoice`` writes one
    row here. This is the trail that demonstrates state-machine completeness
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    entity_type: Mapped[AuditEntityType] = mapped_column(Enum(AuditEntityType, native_enum=False), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), nullable=False, index=True)

    old_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Who triggered the transition: "system", "worker", "tenant", "customer", etc.
    actor: Mapped[str] = mapped_column(String(100), default="system", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
