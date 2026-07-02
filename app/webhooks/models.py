import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base
from app.providers.base import PaymentStatus, FailureReason


class WebhookEvent(Base):
    """
    Logs incoming webhooks to ensure idempotency.
    """

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class PaymentAttempt(Base):
    """
    Logs every attempt to charge a customer, whether sync or async.
    """

    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus, native_enum=False), nullable=False)
    failure_reason: Mapped[FailureReason | None] = mapped_column(Enum(FailureReason, native_enum=False), nullable=True)

    # Dunning (Section 8): the original charge is attempt 0; each scheduled
    # retry is a separate row with an incrementing number and ``is_retry=True``.
    attempt_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_retry: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    invoice: Mapped["Invoice"] = relationship("Invoice", lazy="joined")


class WebhookEndpoint(Base):
    """
    Stores tenant endpoints and signing secrets for outbound webhooks.
    """
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


import enum
from sqlalchemy import JSON

class OutboundEventStatus(str, enum.Enum):
    pending = "pending"
    successful = "successful"
    failed = "failed"


class OutboundWebhookEvent(Base):
    """
    Queues outbound webhook events before delivery.
    """
    __tablename__ = "outbound_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    
    status: Mapped[OutboundEventStatus] = mapped_column(Enum(OutboundEventStatus, native_enum=False), default=OutboundEventStatus.pending, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class WebhookDeliveryAttempt(Base):
    """
    Logs every attempt to deliver an outbound webhook event.
    """
    __tablename__ = "webhook_delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("outbound_webhook_events.id", ondelete="CASCADE"), nullable=False, index=True)
    
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
