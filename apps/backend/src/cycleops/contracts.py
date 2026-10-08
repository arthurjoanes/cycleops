"""Contracts sent to the browser: events, SSE messages, snapshot, history and replay files.

These models are the source. packages/contracts/ is generated from them and never edited.
Inside schemaVersion 1 only new optional fields may be added.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)
from pydantic.alias_generators import to_camel

SCHEMA_VERSION: Literal[1] = 1


def iso(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


Instant = Annotated[
    AwareDatetime,
    PlainSerializer(iso, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]
DataMode = Literal["live", "recorded_replay", "synthetic"]
EventCause = Literal["publication", "clock", "catalog", "policy"]
SourceState = Literal["unknown", "fresh", "late", "stale", "quarantined", "closed"]
Quality = Literal["valid", "suspect", "invalid", "unknown"]
Condition = Literal[
    "outside_service_hours",
    "not_installed",
    "unverifiable",
    "pickup_disabled",
    "return_disabled",
    "pickup_unavailable",
    "return_unavailable",
    "return_capacity_unknown",
    "ok",
]
UnverifiableReason = Literal[
    "source_unknown",
    "source_stale",
    "source_quarantined",
    "missing_in_status",
    "invalid_record",
    "report_stale",
]
IncidentKind = Literal["pickup_unavailable", "return_unavailable"]
IncidentState = Literal[
    "candidate",
    "confirmed",
    "recovering",
    "unverifiable",
    "resolved",
    "closed_without_recovery",
    "discarded",
]
CloseReason = Literal[
    "contrary_observation",
    "source_stale",
    "station_stale",
    "flag_disabled",
    "uninstalled",
    "return_capacity_unknown",
    "removed_from_catalog",
    "service_closed",
]
StationId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        serialize_by_alias=True,
        frozen=True,
        extra="forbid",
    )


class VehicleTypeCount(Contract):
    vehicle_type_id: str
    count: int


class StationAvailability(Contract):
    station_id: StationId
    is_installed: bool | None
    is_renting: bool | None
    is_returning: bool | None
    bikes_available: int | None
    docks_available: int | None
    vehicle_types: list[VehicleTypeCount]
    source_updated_at: Instant | None
    # With feed_generation this is when the provider generated the file.
    station_reported_at: Instant | None
    quality: Quality
    quality_flags: list[str]


class IncidentItem(Contract):
    id: str
    kind: IncidentKind
    state: IncidentState
    suspended_from: IncidentState | None = None
    first_observed_at: Instant
    observed_since: Instant
    confirmed_at: Instant | None = None
    close_reason: CloseReason | None = None


class DecisionFacts(Contract):
    bikes_available: int | None
    docks_available: int | None
    capacity_used: int | None
    recovery_min_units: int | None
    publications_used: int
    span_seconds: int
    source_updated_at: Instant | None


class PriorityDecision(Contract):
    station_id: StationId
    conditions: list[Condition]
    primary_condition: Condition
    unverifiable_reason: UnverifiableReason | None
    reason_codes: list[str]
    reason_text: str | None
    facts: DecisionFacts | None
    quality: Quality
    # Open incidents, at most one per kind, plus the ones that reached a final state in
    # this transition (only in that event).
    incidents: list[IncidentItem]
    policy_version: str
    # "<tier>|<firstObservedAt, 10-digit epoch>|<stationId>", compared with <.
    sort_key: str | None


class StationState(Contract):
    availability: StationAvailability
    decision: PriorityDecision


class Coverage(Contract):
    valid: int
    total: int


class FeedHealth(Contract):
    state: SourceState
    last_source_updated_at: Instant | None
    last_attempt_at: Instant | None
    last_outcome: str | None
    coverage: Coverage
    breaker_open_until: Instant | None
    quarantined_feeds: list[str] = []


class StationBatchEvent(Contract):
    # No defaults on the constant fields: they must be required in the schema, so a
    # payload without them fails validation in the browser.
    type: Literal["station_batch"]
    schema_version: Literal[1]
    event_id: str
    stream_id: str
    sequence: int
    mode: DataMode
    cause: EventCause
    catalog_version: str
    source_updated_at: Instant | None
    received_at: Instant | None
    committed_at: Instant
    stations: list[StationState]
    removed_station_ids: list[str]
    feed_health: FeedHealth


class Heartbeat(Contract):
    server_time: Instant
    stream_id: str
    head_sequence: int
    last_source_updated_at: Instant | None
    feed_state: SourceState


class ResyncRequired(Contract):
    reason: Literal[
        "cursor_expired",
        "cursor_ahead",
        "unknown_stream",
        "gap",
        "queue_overflow",
        "schema_changed",
    ]
    last_sequence: int | None


class StreamClosed(Contract):
    reason: Literal["superseded", "capacity", "shutdown"]


class CatalogStation(Contract):
    station_id: StationId
    name: str
    lat: float
    lon: float
    capacity: int | None
    is_virtual: bool


class CatalogVersion(Contract):
    catalog_version: str
    stations: list[CatalogStation]


class ReplayCatalog(Contract):
    schema_version: Literal[1]
    system_id: str
    versions: list[CatalogVersion]


class ManifestIncident(Contract):
    incident_id: str
    kind: IncidentKind
    station_id: StationId
    confirmed_sequence: int
    resolved_sequence: int | None
    # Virtual committedAt of the event minus that of the first event of the artifact.
    confirmed_offset_seconds: float
    resolved_offset_seconds: float | None


class ReplayManifest(Contract):
    schema_version: Literal[1]
    mode: Literal["recorded_replay", "synthetic"]
    stream_id: str
    system_id: str
    timezone: str
    period_start: Instant
    period_end: Instant
    period_start_local: str
    period_end_local: str
    recording_hash: str
    events_sha256: str
    catalog_sha256: str
    gbfs_version: str
    adapter_version: str
    engine_version: str
    policy_version: str
    event_schema_version: Literal[1]
    hash_version: int
    seed: int | None
    parameters: dict[str, Any] | None
    incidents: list[ManifestIncident]
    event_count: int


class Snapshot(Contract):
    schema_version: Literal[1]
    stream_id: str
    mode: DataMode
    system_id: str
    seed: int | None
    cursor: str
    event_schema_version: Literal[1]
    catalog_version: str
    feed_health: FeedHealth
    catalog: list[CatalogStation]
    stations: list[StationState]


class HistoryPoint(Contract):
    source_updated_at: Instant
    bikes_available: int | None
    docks_available: int | None
    quality: Quality
    primary_condition: Condition


class Interval(Contract):
    start: Instant
    end: Instant | None


class StationHistory(Contract):
    stream_id: str
    station_id: StationId
    window_start: Instant
    window_end: Instant
    points: list[HistoryPoint]
    incidents: list[IncidentItem]
    gaps: list[Interval]
    closed: list[Interval]


# Generated into packages/contracts/schemas/<name>.schema.json.
SCHEMA_MODELS: dict[str, type[Contract]] = {
    "station-batch-event": StationBatchEvent,
    "heartbeat": Heartbeat,
    "resync-required": ResyncRequired,
    "stream-closed": StreamClosed,
    "snapshot": Snapshot,
    "station-history": StationHistory,
    "replay-catalog": ReplayCatalog,
    "replay-manifest": ReplayManifest,
}
