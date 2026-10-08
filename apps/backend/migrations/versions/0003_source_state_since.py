"""stream_heads.source_state_since: when the source state last changed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null on existing rows: the start is unknown until the next change.
    op.add_column("stream_heads", sa.Column("source_state_since", sa.TIMESTAMP(timezone=True)))


def downgrade() -> None:
    op.drop_column("stream_heads", "source_state_since")
