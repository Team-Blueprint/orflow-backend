"""API key management: email/password auth + 4 typed keys on tenants + per-plan rate limit on plans.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Tenants table:
        - Drop old single api_key column
        - Add email (unique), hashed_password
        - Add pk_test, sk_test, pk_live, sk_live (unique, nullable)
        - Add pk_test_active, sk_test_active, pk_live_active, sk_live_active (boolean, not null)

    Plans table:
        - Add api_rate_limit_per_minute (integer, default 60, not null)
    """

    # SQLite doesn't support DROP COLUMN well in older versions, so we use
    # batch mode (Alembic's compatibility wrapper for SQLite).
    with op.batch_alter_table("tenants") as batch_op:
        # Remove old single key
        batch_op.drop_column("api_key")

        # Login identity
        batch_op.add_column(
            sa.Column("email", sa.String(length=255), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("hashed_password", sa.String(length=255), nullable=False, server_default="")
        )

        # Four API key slots (plaintext, not hashed)
        batch_op.add_column(
            sa.Column("pk_test", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("sk_test", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pk_live", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("sk_live", sa.String(length=64), nullable=True)
        )

        # Per-key active flags
        batch_op.add_column(
            sa.Column("pk_test_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("sk_test_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("pk_live_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("sk_live_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )

        # Unique constraints on key slots (NULL values are not compared for uniqueness)
        batch_op.create_unique_constraint("uq_tenants_email", ["email"])
        batch_op.create_unique_constraint("uq_tenants_pk_test", ["pk_test"])
        batch_op.create_unique_constraint("uq_tenants_sk_test", ["sk_test"])
        batch_op.create_unique_constraint("uq_tenants_pk_live", ["pk_live"])
        batch_op.create_unique_constraint("uq_tenants_sk_live", ["sk_live"])

    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(
            sa.Column(
                "api_rate_limit_per_minute",
                sa.Integer(),
                nullable=False,
                server_default="60",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("api_rate_limit_per_minute")

    with op.batch_alter_table("tenants") as batch_op:
        batch_op.drop_constraint("uq_tenants_sk_live", type_="unique")
        batch_op.drop_constraint("uq_tenants_pk_live", type_="unique")
        batch_op.drop_constraint("uq_tenants_sk_test", type_="unique")
        batch_op.drop_constraint("uq_tenants_pk_test", type_="unique")
        batch_op.drop_constraint("uq_tenants_email", type_="unique")
        batch_op.drop_column("sk_live_active")
        batch_op.drop_column("pk_live_active")
        batch_op.drop_column("sk_test_active")
        batch_op.drop_column("pk_test_active")
        batch_op.drop_column("sk_live")
        batch_op.drop_column("pk_live")
        batch_op.drop_column("sk_test")
        batch_op.drop_column("pk_test")
        batch_op.drop_column("hashed_password")
        batch_op.drop_column("email")
        batch_op.add_column(
            sa.Column("api_key", sa.String(length=255), nullable=False, server_default="")
        )
        batch_op.create_unique_constraint("uq_tenants_api_key", ["api_key"])
