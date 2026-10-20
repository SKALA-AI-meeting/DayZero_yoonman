"""Pin scenario version and share model/time budgets across queue retries."""
from alembic import op
import sqlalchemy as sa
revision = '0002'
down_revision = '0001'


def upgrade():
    op.add_column('sessions', sa.Column('scenario_version',sa.String(),nullable=False,server_default='looplab-v1'))
    op.add_column('jobs',sa.Column('model_calls',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('jobs',sa.Column('execution_started',sa.Float(),nullable=True))


def downgrade():
    raise RuntimeError('Restore a backup rather than discarding execution audit metadata')
