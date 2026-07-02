"""Add invoice_line_items table (proration)

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "invoice_line_items",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("tenant_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("invoice_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_invoice_line_items_invoice_id"), "invoice_line_items", ["invoice_id"], unique=False
    )
    op.create_index(
        op.f("ix_invoice_line_items_tenant_id"), "invoice_line_items", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_invoice_line_items_tenant_id"), table_name="invoice_line_items")
    op.drop_index(op.f("ix_invoice_line_items_invoice_id"), table_name="invoice_line_items")
    op.drop_table("invoice_line_items")
