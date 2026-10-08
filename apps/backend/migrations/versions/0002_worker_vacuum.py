"""The worker vacuums the tables it purges.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# The purge's tables. MAINTAIN (PostgreSQL 17+) allows VACUUM without owning the table.
PURGED = "event_log, station_observations, feed_publications, station_incidents"


def upgrade() -> None:
    op.execute(f"GRANT MAINTAIN ON {PURGED} TO cycleops_worker")


def downgrade() -> None:
    op.execute(f"REVOKE MAINTAIN ON {PURGED} FROM cycleops_worker")
