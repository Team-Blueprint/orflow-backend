"""Add dunning fields to invoices and payment_attempts

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "cb72333ead97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("payment_attempts") as batch:
        batch.add_column(
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("is_retry", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("invoices") as batch:
        batch.add_column(
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("dunning_failure_reason", sa.String(length=50), nullable=True)
        )
        batch.create_index(
            "ix_invoices_next_retry_at", ["next_retry_at"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("invoices") as batch:
        batch.drop_index("ix_invoices_next_retry_at")
        batch.drop_column("dunning_failure_reason")
        batch.drop_column("next_retry_at")

    with op.batch_alter_table("payment_attempts") as batch:
        batch.drop_column("is_retry")
        batch.drop_column("attempt_number")
