"""Add reconciliation_discrepancies table

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, list[str], None] = "99a9b5251816"
branch_labels: Union[str, list[str], None] = None
depends_on: Union[str, list[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_discrepancies",
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("tenant_id", sa.Uuid(native_uuid=False), nullable=True),
        sa.Column("run_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("nomba_transaction_id", sa.String(255), nullable=True),
        sa.Column("nomba_status", sa.String(50), nullable=True),
        sa.Column("nomba_amount", sa.Integer, nullable=True),
        sa.Column("nomba_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merchant_tx_ref", sa.String(255), nullable=True),
        sa.Column("payment_attempt_id", sa.Uuid(native_uuid=False), nullable=True),
        sa.Column("invoice_id", sa.Uuid(native_uuid=False), nullable=True),
        sa.Column("our_status", sa.String(50), nullable=True),
        sa.Column("our_amount", sa.Integer, nullable=True),
        sa.Column("discrepancy_type", sa.String(50), nullable=False),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column("resolved", sa.Boolean, default=False, nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_attempt_id"], ["payment_attempts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_reconciliation_discrepancies_tenant_id", "reconciliation_discrepancies", ["tenant_id"])
    op.create_index("ix_reconciliation_discrepancies_run_id", "reconciliation_discrepancies", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_reconciliation_discrepancies_run_id", table_name="reconciliation_discrepancies")
    op.drop_index("ix_reconciliation_discrepancies_tenant_id", table_name="reconciliation_discrepancies")
    op.drop_table("reconciliation_discrepancies")
