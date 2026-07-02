"""Add WebhookEvent and PaymentAttempt models

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb72333ead97'
down_revision: Union[str, Sequence[str], None] = '3c1dca427260'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_webhook_events_event_id"), "webhook_events", ["event_id"], unique=True
    )

    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("tenant_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("invoice_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "success", "pending", "requires_action", "failed", "refunded",
                name="paymentstatus", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "failure_reason",
            sa.Enum(
                "insufficient_funds", "expired_card", "do_not_honor", "card_declined",
                "invalid_payment_method", "requires_action", "generic_decline",
                "processing_error", "unknown",
                name="failurereason", native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_attempts_invoice_id"), "payment_attempts", ["invoice_id"], unique=False
    )
    op.create_index(
        op.f("ix_payment_attempts_tenant_id"), "payment_attempts", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_payment_attempts_tenant_id"), table_name="payment_attempts")
    op.drop_index(op.f("ix_payment_attempts_invoice_id"), table_name="payment_attempts")
    op.drop_table("payment_attempts")
    op.drop_index(op.f("ix_webhook_events_event_id"), table_name="webhook_events")
    op.drop_table("webhook_events")
