"""Make customer and payment_method project_id nullable

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a83030c0e8c'
down_revision: Union[str, Sequence[str], None] = '801c2f6cc90a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.alter_column('project_id', nullable=True)
        batch_op.drop_constraint('fk_customers_project_id', type_='foreignkey')
        batch_op.create_foreign_key('fk_customers_project_id', 'projects', ['project_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('payment_methods', schema=None) as batch_op:
        batch_op.alter_column('project_id', nullable=True)
        batch_op.drop_constraint('fk_payment_methods_project_id', type_='foreignkey')
        batch_op.create_foreign_key('fk_payment_methods_project_id', 'projects', ['project_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('payment_methods', schema=None) as batch_op:
        batch_op.drop_constraint('fk_payment_methods_project_id', type_='foreignkey')
        batch_op.create_foreign_key('fk_payment_methods_project_id', 'projects', ['project_id'], ['id'], ondelete='CASCADE')
        batch_op.alter_column('project_id', nullable=False)

    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_customers_project_id', type_='foreignkey')
        batch_op.create_foreign_key('fk_customers_project_id', 'projects', ['project_id'], ['id'], ondelete='CASCADE')
        batch_op.alter_column('project_id', nullable=False)
