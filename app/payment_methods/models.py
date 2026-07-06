import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class PaymentMethodType(str, enum.Enum):
    card = "card"
    bank_transfer = "bank_transfer"
    wallet = "wallet"


class PaymentMethodStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    detached = "detached"


class PaymentMethod(Base):
    """
    A tokenized payment instrument belonging to a customer.
    Raw card data is never stored — only provider tokens.
    """

    __tablename__ = "payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    __project_scoped__ = False
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[PaymentMethodType] = mapped_column(Enum(PaymentMethodType, native_enum=False), nullable=False)
    status: Mapped[PaymentMethodStatus] = mapped_column(Enum(PaymentMethodStatus, native_enum=False), default=PaymentMethodStatus.active, nullable=False,)

    # Opaque token from the payment provider — never raw card numbers
    provider_token: Mapped[str] = mapped_column(String(512), nullable=False)
    last_four: Mapped[str | None] = mapped_column(String(4), nullable=True)
    expiry_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expiry_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False,)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),onupdate=lambda: datetime.now(timezone.utc),nullable=False,)
