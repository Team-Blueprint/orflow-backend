import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.providers.base import FailureReason


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    open = "open"
    paid = "paid"
    void = "void"
    uncollectible = "uncollectible"


class Invoice(Base):
    """
    A single billing document for a subscription period (or a one-off charge).

    ``status`` must never be written directly — use
    ``app.invoices.state_machine.transition_invoice``.
    """

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    # Nullable: standalone invoices need not belong to a subscription.
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True, index=True)

    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus, native_enum=False), default=InvoiceStatus.draft, nullable=False)

    # Amount stored in the smallest currency unit (e.g. kobo for NGN).
    amount_due: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Dunning / failed-payment recovery (Section 8) ---
    # When set, a retry is scheduled: the billing/dunning worker re-attempts the
    # charge once ``next_retry_at <= now``. Cleared on recovery or exhaustion.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # The FailureReason that *opened* the dunning cycle. It anchors the whole
    # retry schedule (see app.dunning.policy) even if later attempts decline for
    # a different reason, and flags reasons that need a new payment method.
    dunning_failure_reason: Mapped[FailureReason | None] = mapped_column(
        Enum(FailureReason, native_enum=False), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class InvoiceLineItem(Base):
    """
    An explicit, auditable line on an invoice (Section 10: proration).

    A proration invoice carries two of these — a negative credit for the unused
    old plan and a positive charge for the remaining new plan — so the change is
    always readable, never a silent balance adjustment. ``amount_minor`` is
    signed (negative = credit, positive = charge), in the smallest currency unit.
    """

    __tablename__ = "invoice_line_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)

    description: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
