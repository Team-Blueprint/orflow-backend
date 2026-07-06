"""Update PlanInterval enum values (day/week/month/year → daily/weekly/monthly/quarterly/yearly/annually/biannually)

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("plans", "interval", type_=sa.String(10))
    op.execute("UPDATE plans SET interval = 'daily' WHERE interval = 'day'")
    op.execute("UPDATE plans SET interval = 'weekly' WHERE interval = 'week'")
    op.execute("UPDATE plans SET interval = 'monthly' WHERE interval = 'month'")
    op.execute("UPDATE plans SET interval = 'yearly' WHERE interval = 'year'")


def downgrade() -> None:
    op.execute("UPDATE plans SET interval = 'day' WHERE interval = 'daily'")
    op.execute("UPDATE plans SET interval = 'week' WHERE interval = 'weekly'")
    op.execute("UPDATE plans SET interval = 'month' WHERE interval = 'monthly'")
    op.execute("UPDATE plans SET interval = 'year' WHERE interval = 'yearly'")
    op.alter_column("plans", "interval", type_=sa.String(5))
