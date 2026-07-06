"""add_google_sub_to_tenants

Revision ID: 4a5b6c7d8e9f
Revises: 4f31383fdeda
Create Date: 2026-07-06 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4a5b6c7d8e9f"
down_revision: Union[str, Sequence[str], None] = "4f31383fdeda"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.add_column(sa.Column("google_sub", sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint("uq_tenants_google_sub", ["google_sub"])


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_constraint("uq_tenants_google_sub", type_="unique")
        batch_op.drop_column("google_sub")
