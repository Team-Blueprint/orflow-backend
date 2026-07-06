import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid
from app.db.base import Base


class PlanInterval(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    quarterly = "quarterly"
    yearly = "yearly"
    annually = "annually"
    biannually = "biannually"


class PlanStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class Plan(Base):
    """
    A billing plan (product + pricing) offered by a tenant.
    """

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(native_uuid=False), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Amount stored in the smallest currency unit (e.g. cents for USD)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    interval: Mapped[PlanInterval] = mapped_column(Enum(PlanInterval, native_enum=False), nullable=False)
    interval_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    trial_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    installments_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[PlanStatus] = mapped_column(Enum(PlanStatus, native_enum=False), default=PlanStatus.active, nullable=False)
 
    api_rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False,)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False,)
