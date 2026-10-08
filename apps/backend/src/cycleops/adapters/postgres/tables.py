"""The schema, as SQLAlchemy Core tables. migrations/versions/ mirrors it; a test compares.

Instants are timestamptz. The `engine` jsonb columns hold the step() fields that no query
reads; the plain columns are what the API and the history read.
"""

from typing import Any

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    Double,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

FINAL_STATES_SQL = "('resolved', 'closed_without_recovery', 'discarded')"


def _instant(name: str, nullable: bool = True) -> Column[Any]:
    return Column(name, TIMESTAMP(timezone=True), nullable=nullable)


def _text(name: str, nullable: bool = True) -> Column[Any]:
    return Column(name, Text, nullable=nullable)


metadata = MetaData()

# One row per stream; the active stream is the most recently created one.
stream_heads = Table(
    "stream_heads",
    metadata,
    Column("stream_id", Text, primary_key=True),
    _text("system_id", False),
    _text("mode", False),
    _text("generation", False),
    Column("seed", BigInteger),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("head_sequence", BigInteger, nullable=False),
    _text("catalog_version", False),
    _text("policy_version"),
    _instant("clock_now"),
    _text("source_state", False),
    _instant("last_source_updated_at"),
    _text("last_content_hash"),
    Column("fresh_again_count", Integer, nullable=False),
    # From the policy, so the API applies the stale rule without loading it.
    Column("source_stale_after_seconds", Integer, nullable=False),
    _instant("worker_seen_at"),
    _instant("last_attempt_at"),
    _text("last_outcome"),
    Column("coverage_valid", Integer, nullable=False),
    Column("coverage_total", Integer, nullable=False),
    Column("breaker_failures", Integer, nullable=False, server_default=text("0")),
    _instant("breaker_open_until"),
    _instant("quarantined_since"),
    Column("quarantined_feeds", ARRAY(Text), nullable=False),
    Column("ingestion_suspended", Boolean, nullable=False),
    Column("catalog_fetch_pending", Boolean, nullable=False),
    Column("engine", JSONB, nullable=False),
    # When source_state last changed; the API reads it for FeedHealth.stateSince.
    _instant("source_state_since"),
)

stations = Table(
    "stations",
    metadata,
    _text("system_id", False),
    _text("station_id", False),
    _text("name", False),
    Column("lat", Double, nullable=False),
    Column("lon", Double, nullable=False),
    Column("capacity", Integer),
    Column("is_virtual", Boolean, nullable=False),
    Column("is_valet_station", Boolean),
    _text("catalog_version", False),
    _instant("first_seen_at", False),
    _instant("last_seen_in_catalog_at", False),
    _instant("removed_at"),
    Column("info", JSONB, nullable=False),
    Column("engine", JSONB, nullable=False),
    PrimaryKeyConstraint("system_id", "station_id"),
)

station_current = Table(
    "station_current",
    metadata,
    _text("system_id", False),
    _text("station_id", False),
    _text("stream_id", False),
    Column("bikes_available", Integer),
    Column("docks_available", Integer),
    Column("vehicle_types", JSONB, nullable=False),
    Column("is_installed", Boolean),
    Column("is_renting", Boolean),
    Column("is_returning", Boolean),
    _instant("source_updated_at"),
    _instant("station_reported_at"),
    _text("quality", False),
    Column("quality_flags", ARRAY(Text), nullable=False),
    # The contract objects as last published: what the snapshot returns.
    Column("decision", JSONB),
    Column("availability", JSONB),
    Column("last_sequence", BigInteger),
    Column("engine", JSONB, nullable=False),
    PrimaryKeyConstraint("system_id", "station_id"),
)

# One row per station included in an event. committed_at is the event's: the retention
# clock and the instant of the change (source_updated_at stays put while the source is
# stale, and is null for a station never seen in the status).
station_observations = Table(
    "station_observations",
    metadata,
    _text("stream_id", False),
    _text("station_id", False),
    Column("sequence", BigInteger, nullable=False),
    _instant("source_updated_at"),
    _instant("committed_at", False),
    Column("bikes_available", Integer),
    Column("docks_available", Integer),
    Column("vehicle_types", JSONB, nullable=False),
    _text("quality", False),
    Column("quality_flags", ARRAY(Text), nullable=False),
    _text("primary_condition", False),
    Column("conditions", ARRAY(Text), nullable=False),
    _text("content_hash", False),
    PrimaryKeyConstraint("stream_id", "station_id", "sequence"),
    Index(
        "ix_station_observations_history",
        "stream_id",
        "station_id",
        "source_updated_at",
    ),
    Index("ix_station_observations_committed_at", "committed_at"),
)

station_incidents = Table(
    "station_incidents",
    metadata,
    Column("id", Text, primary_key=True),
    _text("system_id", False),
    _text("station_id", False),
    _text("stream_id", False),
    _text("kind", False),
    _text("state", False),
    _text("suspended_from"),
    _text("unverifiable_reason"),
    Column("at_opening", Boolean, nullable=False),
    _instant("first_observed_at", False),
    _instant("observed_since", False),
    _instant("confirmed_at"),
    _instant("recovering_since"),
    _instant("closed_at"),
    _text("close_reason"),
    Column("observations", JSONB, nullable=False),
    _text("reason_text", False),
    Column("facts", JSONB),
    _text("policy_version", False),
    # At most one open incident per station and kind.
    Index(
        "ux_station_incidents_open",
        "system_id",
        "station_id",
        "kind",
        unique=True,
        postgresql_where=text(f"state NOT IN {FINAL_STATES_SQL}"),
    ),
    Index("ix_station_incidents_station", "stream_id", "station_id"),
)

# One row per attempt; attempt_at is requestedAt.
feed_publications = Table(
    "feed_publications",
    metadata,
    _text("stream_id", False),
    _instant("attempt_at", False),
    _text("feed", False),
    Column("http_status", Integer),
    _text("outcome", False),
    _instant("source_updated_at"),
    _instant("received_at", False),
    Column("bytes", Integer),
    Column("duration_ms", Integer, nullable=False),
    _text("payload_sha256"),
    Column("station_count", Integer),
    Column("valid_count", Integer),
    Column("distinct_last_reported", Integer),
    _text("error_code"),
    PrimaryKeyConstraint("stream_id", "attempt_at", "feed"),
)

event_log = Table(
    "event_log",
    metadata,
    _text("stream_id", False),
    Column("sequence", BigInteger, nullable=False),
    _text("event_type", False),
    Column("payload", JSONB, nullable=False),
    _instant("committed_at", False),
    PrimaryKeyConstraint("stream_id", "sequence"),
    Index("ix_event_log_committed_at", "committed_at"),
)
