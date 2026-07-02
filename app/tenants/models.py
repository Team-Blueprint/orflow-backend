import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


def _generate_key(prefix: str) -> str:
    """Generate a prefixed, 40-hex-char API key."""

    return f"{prefix}_{secrets.token_hex(20)}"


class Tenant(Base):


    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    pk_test: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    sk_test: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    pk_live: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    sk_live: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    pk_test_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sk_test_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    pk_live_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sk_live_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    @staticmethod
    def generate_all_keys() -> dict[str, str]:
        """Return a fresh set of all four API keys. Call once at signup."""
        return {
            "pk_test": _generate_key("pk_test"),
            "sk_test": _generate_key("sk_test"),
            "pk_live": _generate_key("pk_live"),
            "sk_live": _generate_key("sk_live"),
        }

    @staticmethod
    def generate_key(key_type: str) -> str:
        """Return a single fresh key for the given slot (for regeneration)."""
        prefix = key_type  # e.g. "pk_test"
        return _generate_key(prefix)
