"""Initial schema, grants and default privileges.

Revision ID: 0001
Revises:
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "stream_heads",
    "stations",
    "station_current",
    "station_observations",
    "station_incidents",
    "feed_publications",
    "event_log",
)
TSTZ = sa.TIMESTAMP(timezone=True)
TEXTS = postgresql.ARRAY(sa.Text())
FINAL_STATES = "('resolved', 'closed_without_recovery', 'discarded')"


def upgrade() -> None:
    op.create_table(
        "stream_heads",
        sa.Column("stream_id", sa.Text(), primary_key=True),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("generation", sa.Text(), nullable=False),
        sa.Column("seed", sa.BigInteger()),
        sa.Column("created_at", TSTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("head_sequence", sa.BigInteger(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text()),
        sa.Column("clock_now", TSTZ),
        sa.Column("source_state", sa.Text(), nullable=False),
        sa.Column("last_source_updated_at", TSTZ),
        sa.Column("last_content_hash", sa.Text()),
        sa.Column("fresh_again_count", sa.Integer(), nullable=False),
        sa.Column("source_stale_after_seconds", sa.Integer(), nullable=False),
        sa.Column("worker_seen_at", TSTZ),
        sa.Column("last_attempt_at", TSTZ),
        sa.Column("last_outcome", sa.Text()),
        sa.Column("coverage_valid", sa.Integer(), nullable=False),
        sa.Column("coverage_total", sa.Integer(), nullable=False),
        sa.Column("breaker_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("breaker_open_until", TSTZ),
        sa.Column("quarantined_since", TSTZ),
        sa.Column("quarantined_feeds", TEXTS, nullable=False),
        sa.Column("ingestion_suspended", sa.Boolean(), nullable=False),
        sa.Column("catalog_fetch_pending", sa.Boolean(), nullable=False),
        sa.Column("engine", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "stations",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("capacity", sa.Integer()),
        sa.Column("is_virtual", sa.Boolean(), nullable=False),
        sa.Column("is_valet_station", sa.Boolean()),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("first_seen_at", TSTZ, nullable=False),
        sa.Column("last_seen_in_catalog_at", TSTZ, nullable=False),
        sa.Column("removed_at", TSTZ),
        sa.Column("info", postgresql.JSONB(), nullable=False),
        sa.Column("engine", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("system_id", "station_id"),
    )
    op.create_table(
        "station_current",
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("stream_id", sa.Text(), nullable=False),
        sa.Column("bikes_available", sa.Integer()),
        sa.Column("docks_available", sa.Integer()),
        sa.Column("vehicle_types", postgresql.JSONB(), nullable=False),
        sa.Column("is_installed", sa.Boolean()),
        sa.Column("is_renting", sa.Boolean()),
        sa.Column("is_returning", sa.Boolean()),
        sa.Column("source_updated_at", TSTZ),
        sa.Column("station_reported_at", TSTZ),
        sa.Column("quality", sa.Text(), nullable=False),
        sa.Column("quality_flags", TEXTS, nullable=False),
        sa.Column("decision", postgresql.JSONB()),
        sa.Column("availability", postgresql.JSONB()),
        sa.Column("last_sequence", sa.BigInteger()),
        sa.Column("engine", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("system_id", "station_id"),
    )
    op.create_table(
        "station_observations",
        sa.Column("stream_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_updated_at", TSTZ),
        sa.Column("committed_at", TSTZ, nullable=False),
        sa.Column("bikes_available", sa.Integer()),
        sa.Column("docks_available", sa.Integer()),
        sa.Column("vehicle_types", postgresql.JSONB(), nullable=False),
        sa.Column("quality", sa.Text(), nullable=False),
        sa.Column("quality_flags", TEXTS, nullable=False),
        sa.Column("primary_condition", sa.Text(), nullable=False),
        sa.Column("conditions", TEXTS, nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("stream_id", "station_id", "sequence"),
    )
    op.create_index(
        "ix_station_observations_history",
        "station_observations",
        ["stream_id", "station_id", "source_updated_at"],
    )
    op.create_index(
        "ix_station_observations_committed_at", "station_observations", ["committed_at"]
    )
    op.create_table(
        "station_incidents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("system_id", sa.Text(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("stream_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("suspended_from", sa.Text()),
        sa.Column("unverifiable_reason", sa.Text()),
        sa.Column("at_opening", sa.Boolean(), nullable=False),
        sa.Column("first_observed_at", TSTZ, nullable=False),
        sa.Column("observed_since", TSTZ, nullable=False),
        sa.Column("confirmed_at", TSTZ),
        sa.Column("recovering_since", TSTZ),
        sa.Column("closed_at", TSTZ),
        sa.Column("close_reason", sa.Text()),
        sa.Column("observations", postgresql.JSONB(), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("facts", postgresql.JSONB()),
        sa.Column("policy_version", sa.Text(), nullable=False),
    )
    op.create_index(
        "ux_station_incidents_open",
        "station_incidents",
        ["system_id", "station_id", "kind"],
        unique=True,
        postgresql_where=sa.text(f"state NOT IN {FINAL_STATES}"),
    )
    op.create_index(
        "ix_station_incidents_station", "station_incidents", ["stream_id", "station_id"]
    )
    op.create_table(
        "feed_publications",
        sa.Column("stream_id", sa.Text(), nullable=False),
        sa.Column("attempt_at", TSTZ, nullable=False),
        sa.Column("feed", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("source_updated_at", TSTZ),
        sa.Column("received_at", TSTZ, nullable=False),
        sa.Column("bytes", sa.Integer()),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.Text()),
        sa.Column("station_count", sa.Integer()),
        sa.Column("valid_count", sa.Integer()),
        sa.Column("distinct_last_reported", sa.Integer()),
        sa.Column("error_code", sa.Text()),
        sa.PrimaryKeyConstraint("stream_id", "attempt_at", "feed"),
    )
    op.create_table(
        "event_log",
        sa.Column("stream_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("committed_at", TSTZ, nullable=False),
        sa.PrimaryKeyConstraint("stream_id", "sequence"),
    )
    op.create_index("ix_event_log_committed_at", "event_log", ["committed_at"])

    # The worker writes the domain tables, the API only reads them. Tables created later
    # by cycleops_migrator get the same grants.
    tables = ", ".join(TABLES)
    op.execute("GRANT USAGE ON SCHEMA public TO cycleops_worker, cycleops_api")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO cycleops_worker")
    op.execute(f"GRANT SELECT ON {tables} TO cycleops_api")
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE cycleops_migrator IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cycleops_worker"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE cycleops_migrator IN SCHEMA public "
        "GRANT SELECT ON TABLES TO cycleops_api"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE cycleops_migrator IN SCHEMA public "
        "REVOKE ALL ON TABLES FROM cycleops_worker, cycleops_api"
    )
    for table in reversed(TABLES):
        op.drop_table(table)
