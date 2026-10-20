"""Use a 45-second renewable lease, preserving the last known heartbeat."""
from alembic import op
revision = '0003'
down_revision = '0002'


def upgrade():
    # Existing 180s leases encode last_heartbeat = lease_until - 180.
    # Convert to last_heartbeat + 45; session/assessment clocks are untouched.
    op.execute("UPDATE jobs SET lease_until=lease_until-135 WHERE status='running' AND lease_until>0")


def downgrade():
    raise RuntimeError('Lease policy rollback requires draining active workers')
