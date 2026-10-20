"""Pin report inference independently; preserve all historical bindings/snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0006'
down_revision = '0005'


def upgrade():
    op.add_column('sessions', sa.Column('report_binding', JSONB, nullable=True))


def downgrade():
    raise RuntimeError('Report model provenance must not be discarded')
