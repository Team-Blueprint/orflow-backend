import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class DiscrepancyType(str, enum.Enum):
    missing_in_ours = "missing_in_ours"
    missing_in_nomba = "missing_in_nomba"
    status_mismatch = "status_mismatch"
    amount_mismatch = "amount_mismatch"


class ReconciliationDiscrepancy(Base):
    __tablename__ = "reconciliation_discrepancies"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(native_uuid=False), nullable=False, index=True,
    )

    nomba_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    nomba_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nomba_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nomba_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    merchant_tx_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )

    payment_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("payment_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(native_uuid=False), ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    our_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    our_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)

    discrepancy_type: Mapped[DiscrepancyType] = mapped_column(
        Enum(DiscrepancyType, native_enum=False), nullable=False,
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolved: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
