"""Preserve legacy jobs/snapshots; new sessions use three independent agents."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0005'
down_revision = '0004'


def upgrade():
    op.add_column('sessions', sa.Column('contract', JSONB, nullable=True))
    op.add_column('jobs', sa.Column('agent_id', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('role_version', sa.String(), nullable=False, server_default='agents-v1'))
    # Historical collaboration checkpoints remain legacy; never clone them to two people.
    op.execute("UPDATE jobs SET agent_id=flow, role_version='legacy-v1'")
    op.alter_column('jobs', 'agent_id', nullable=False)
    op.drop_constraint('jobs_session_id_flow_sequence_key', 'jobs', type_='unique')
    op.create_unique_constraint('jobs_session_id_agent_id_sequence_key', 'jobs', ['session_id', 'agent_id', 'sequence'])
    op.create_table('agent_memories',
        sa.Column('session_id', sa.String(), sa.ForeignKey('sessions.id'), primary_key=True),
        sa.Column('agent_id', sa.String(), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('data', JSONB, nullable=False))
    op.create_table('tasks',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('session_id', sa.String(), sa.ForeignKey('sessions.id'), nullable=False),
        sa.Column('title', sa.String(), nullable=False), sa.Column('owner', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('part_id', sa.String(), sa.ForeignKey('parts.id')),
        sa.Column('source_id', sa.String(), sa.ForeignKey('records.id')),
        sa.Column('note', sa.Text(), nullable=False), sa.Column('due_elapsed', sa.Float()),
        sa.Column('version', sa.Integer(), nullable=False))
    op.create_index('ix_tasks_session_id', 'tasks', ['session_id'])


def downgrade():
    raise RuntimeError('Agent and task provenance must not be discarded')
