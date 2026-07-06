import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class SubscriptionStatus(str, enum.Enum):
    incomplete = "incomplete"
    incomplete_expired = "incomplete_expired"
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    unpaid = "unpaid"
    paused = "paused"
    canceled = "canceled"
    defaulted = "defaulted"
    completed = "completed"


class SubscriptionType(str, enum.Enum):
    recurring = "recurring"
    installment = "installment"


class Subscription(Base):
    """
    A customer's ongoing subscription to a plan.

    ``status`` must never be written directly — use
    ``app.subscriptions.state_machine.transition_subscription``.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("plans.id"), nullable=False, index=True)
    payment_method_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey("payment_methods.id", ondelete="SET NULL"), nullable=True)

    status: Mapped[SubscriptionStatus] = mapped_column(Enum(SubscriptionStatus, native_enum=False), default=SubscriptionStatus.incomplete, nullable=False)
    type: Mapped[SubscriptionType] = mapped_column(Enum(SubscriptionType, native_enum=False), default=SubscriptionType.recurring, nullable=False)

    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
