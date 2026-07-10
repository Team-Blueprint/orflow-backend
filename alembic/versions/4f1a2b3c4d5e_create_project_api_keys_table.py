"""create project_api_keys table

Revision ID: 4f1a2b3c4d5e
Revises: 03e024362baf
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4f1a2b3c4d5e"
down_revision: Union[str, Sequence[str], None] = "03e024362baf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_api_keys",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("key_value", sa.String(length=64), nullable=False),
        sa.Column("key_type", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            ondelete="CASCADE", name="fk_project_api_keys_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            ondelete="CASCADE", name="fk_project_api_keys_tenant_id",
        ),
        sa.UniqueConstraint("key_value", name="uq_project_api_keys_key_value"),
        sa.UniqueConstraint(
            "project_id", "key_type", name="uq_project_api_keys_project_key_type"
        ),
    )
    op.create_index(
        "ix_project_api_keys_project_id", "project_api_keys", ["project_id"]
    )
    op.create_index(
        "ix_project_api_keys_tenant_id", "project_api_keys", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_api_keys_tenant_id", table_name="project_api_keys")
    op.drop_index("ix_project_api_keys_project_id", table_name="project_api_keys")
    op.drop_table("project_api_keys")
