"""Inputs and state of the engine. Plain dataclasses, no I/O."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from cycleops.adapters.gbfs import InfoPublication, StationInfo, StationStatus, StatusPublication
from cycleops.contracts import (
    CloseReason,
    DataMode,
    DecisionFacts,
    IncidentKind,
    IncidentState,
    SourceState,
    UnverifiableReason,
)

KINDS: tuple[IncidentKind, ...] = ("pickup_unavailable", "return_unavailable")
FINAL_STATES: frozenset[IncidentState] = frozenset(
    {"resolved", "closed_without_recovery", "discarded"}
)


@dataclass(frozen=True)
class PublicationReceived:
    """A station_status response that passed the adapter."""

    received_at: datetime
    publication: StatusPublication


@dataclass(frozen=True)
class PublicationFailed:
    """A failed attempt. Breaker and quarantine come from the collection module; the
    engine only reads them."""

    received_at: datetime
    feed: str
    outcome: str
    error_class: Literal["transport", "contract"]
    breaker_open_until: datetime | None
    quarantined_since: datetime | None


@dataclass(frozen=True)
class CatalogReceived:
    received_at: datetime
    catalog: InfoPublication


@dataclass(frozen=True)
class ClockTick:
    at: datetime
    ingestion_suspended: bool = False


@dataclass(frozen=True)
class PolicyChanged:
    at: datetime
    policy_version: str


Input = PublicationReceived | PublicationFailed | CatalogReceived | ClockTick | PolicyChanged


def instant_of(entry: Input) -> datetime:
    return entry.at if isinstance(entry, ClockTick | PolicyChanged) else entry.received_at


@dataclass
class Incident:
    id: str
    kind: IncidentKind
    state: IncidentState
    first_observed_at: datetime
    observed_since: datetime
    policy_version: str
    at_opening: bool = False
    confirmed_at: datetime | None = None
    suspended_from: IncidentState | None = None
    unverifiable_reason: UnverifiableReason | None = None
    close_reason: CloseReason | None = None
    closed_at: datetime | None = None
    # sourceUpdatedAt of the observations the current rule counts: with the condition while
    # candidate, at or above recoveryMinUnits while recovering.
    observations: list[datetime] = field(default_factory=list)
    reason_text: str = ""
    facts: DecisionFacts | None = None


@dataclass
class Station:
    station_id: str
    info: StationInfo
    first_seen_at: datetime
    last_seen_in_catalog_at: datetime
    missing_catalog_fetches: int = 0
    # sourceUpdatedAt of the last accepted publication when the station started being tracked.
    tracked_since: datetime | None = None
    # Last valid status record and the sourceUpdatedAt of its publication.
    status: StationStatus | None = None
    status_updated_at: datetime | None = None
    # How the station came in the last accepted publication.
    presence: Literal["valid", "missing", "invalid"] = "missing"
    incidents: dict[IncidentKind, Incident] = field(default_factory=dict)
    # Incidents that reached a final state and still have to go out in an event.
    finished: list[Incident] = field(default_factory=list)
    # Kinds observed without their condition since the last opening.
    seen_without: set[IncidentKind] = field(default_factory=set)
    # Text and facts of the station's most recent incident transition.
    reason_text: str | None = None
    facts: DecisionFacts | None = None
    # Change key of what went out in the last event that included the station.
    published_key: tuple[object, ...] | None = None


@dataclass
class StreamState:
    """Everything step() needs. In the worker this is stream_heads plus the station tables."""

    system_id: str
    mode: DataMode
    stream_id: str
    seed: int | None = None
    policy_version: str | None = None
    head_sequence: int = 0
    clock_now: datetime | None = None
    # Service boundaries are applied up to this instant.
    service_checked_at: datetime | None = None
    in_service: bool = True
    source_state: SourceState = "unknown"
    # Set while leaving stale or quarantined: the state holds until freshAgainAfterPublications.
    latched: Literal["stale", "quarantined"] | None = None
    fresh_again_count: int = 0
    # Waiting for the first eligible publication since this instant (start or opening).
    awaiting_since: datetime | None = None
    # Last opening this stream crossed; None when it started already open.
    opened_at: datetime | None = None
    last_source_updated_at: datetime | None = None
    first_accepted_at: datetime | None = None
    last_content_hash: str | None = None
    last_attempt_at: datetime | None = None
    last_outcome: str | None = None
    breaker_open_until: datetime | None = None
    quarantined_since: datetime | None = None
    quarantined_feeds: set[str] = field(default_factory=set)
    ingestion_suspended: bool = False
    catalog_version: str = ""
    catalog_fetch_pending: bool = False
    catalog_fetch_requested_for: str | None = None
    # Ids in the last accepted status that are not in the catalog.
    unknown_ids: frozenset[str] = frozenset()
    removed_ids: set[str] = field(default_factory=set)
    stations: dict[str, Station] = field(default_factory=dict)
    last_event_feed_state: SourceState | None = None
