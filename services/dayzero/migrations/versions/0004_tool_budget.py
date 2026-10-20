"""Persist tool reservations across retries without charging the same operation twice."""
from alembic import op
revision = '0004'
down_revision = '0003'


def upgrade():
    op.execute("ALTER TABLE jobs ADD COLUMN tool_operations jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade():
    raise RuntimeError('Tool reservation history must not be discarded')
