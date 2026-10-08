"""step(state, input, policy) -> (state, events).

The same code runs in the worker, in compile-replay and in the tests. No wall clock, no
random ids: every instant comes from the input. step changes `state` in place and returns
it; a caller that needs the previous state keeps a copy (the worker reloads it from the
database after a rollback).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from cycleops.adapters.gbfs import StatusPublication
from cycleops.canonical import sha256_hex
from cycleops.contracts import (
    SCHEMA_VERSION,
    CatalogStation,
    Condition,
    Coverage,
    DataMode,
    EventCause,
    FeedHealth,
    IncidentItem,
    PriorityDecision,
    Quality,
    SourceState,
    StationAvailability,
    StationBatchEvent,
    StationState,
    UnverifiableReason,
    VehicleTypeCount,
)
from cycleops.domain import incidents
from cycleops.domain.model import (
    KINDS,
    CatalogReceived,
    ClockTick,
    Incident,
    Input,
    PolicyChanged,
    PublicationFailed,
    PublicationReceived,
    Station,
    StreamState,
    instant_of,
)
from cycleops.domain.service_hours import boundaries, in_service
from cycleops.policy import Policy

log = logging.getLogger(__name__)

SOURCE_REASON: dict[SourceState, UnverifiableReason] = {
    "unknown": "source_unknown",
    "stale": "source_stale",
    "quarantined": "source_quarantined",
}
REMOVAL_MIN_FETCHES = 3
REMOVAL_MIN_AGE = timedelta(minutes=30)


def new_stream(
    system_id: str, mode: DataMode, stream_id: str, seed: int | None = None
) -> StreamState:
    state = StreamState(system_id=system_id, mode=mode, stream_id=stream_id, seed=seed)
    state.catalog_version = catalog_version(state)
    return state


def step(
    state: StreamState, entry: Input, policy: Policy
) -> tuple[StreamState, list[StationBatchEvent]]:
    instant = instant_of(entry)
    now = instant if state.clock_now is None else max(instant, state.clock_now)
    state.clock_now = now
    if state.policy_version is None:
        # First boot records the version without PolicyChanged.
        state.policy_version = policy.version
    if state.service_checked_at is None:
        state.service_checked_at = now
        # Before the first accepted publication the source is unknown; no 60 s wait here,
        # that rule is for openings.
        state.in_service = in_service(now, policy.provider.service_hours)

    if isinstance(entry, PublicationFailed):
        _failed(state, entry)
        return state, []
    if isinstance(entry, PublicationReceived):
        observed = _receive(state, entry.publication, entry.received_at, now, policy)
        if observed is None:
            return state, []
        return state, _evaluate(state, now, policy, "publication", entry.received_at, observed)
    if isinstance(entry, CatalogReceived):
        before = state.catalog_version
        removed = _reconcile(state, entry, now)
        # A valid response ends this feed's quarantine, like station_status in _receive;
        # the next tick brings the set from the collection module again.
        state.quarantined_feeds.discard("station_information")
        changed = bool(removed) or state.catalog_version != before
        return state, _evaluate(
            state, now, policy, "catalog", entry.received_at, force=changed, removed=removed
        )
    if isinstance(entry, ClockTick):
        # Collection state that only the source knows comes in on the tick.
        state.ingestion_suspended = entry.ingestion_suspended
        if entry.ingestion_suspended:
            state.last_outcome = "db_quota_exceeded"
        state.quarantined_feeds = set(entry.quarantined_feeds)
        return state, _evaluate(state, now, policy, "clock")
    # PolicyChanged: open incidents keep their state and follow the new rules from here.
    state.policy_version = entry.policy_version
    for st in state.stations.values():
        for inc in st.incidents.values():
            inc.policy_version = entry.policy_version
    return state, _evaluate(state, now, policy, "policy", force=True)


def policy_change(state: StreamState, policy: Policy, at: datetime) -> PolicyChanged | None:
    """What the worker sends on boot: nothing on the first boot or when nothing changed."""
    if state.policy_version is None or state.policy_version == policy.version:
        return None
    return PolicyChanged(at, policy.version)


def _failed(state: StreamState, entry: PublicationFailed) -> None:
    if entry.feed != "station_status":
        # Other feeds never change the source state; their quarantine comes on the tick.
        return
    state.last_attempt_at = entry.received_at
    state.last_outcome = entry.outcome
    opened = entry.breaker_open_until and entry.breaker_open_until != state.breaker_open_until
    if opened or entry.error_class == "contract":
        state.fresh_again_count = 0
    state.breaker_open_until = entry.breaker_open_until
    state.quarantined_since = entry.quarantined_since
    # The state change shows up on the next tick.


def content_hash(publication: StatusPublication) -> str:
    """Normalized records without last_updated and last_reported."""
    return sha256_hex(
        {
            "stations": sorted(
                (
                    s.station_id,
                    s.is_installed,
                    s.is_renting,
                    s.is_returning,
                    s.bikes_available,
                    s.docks_available,
                    s.vehicle_types,
                )
                for s in publication.stations
            ),
            "invalid": sorted(publication.invalid_station_ids),
        }
    )


def _receive(
    state: StreamState,
    pub: StatusPublication,
    received_at: datetime,
    now: datetime,
    policy: Policy,
) -> set[str] | None:
    """Classifies the publication. Returns the observed station ids when accepted."""
    state.last_attempt_at = received_at
    # Any valid response closes the breaker and ends the quarantine in the collection module.
    state.breaker_open_until = None
    state.quarantined_since = None
    updated = pub.envelope.last_updated
    last = state.last_source_updated_at
    digest = content_hash(pub)
    skew = timedelta(seconds=policy.priority.clock_skew_tolerance_seconds)
    if last is None or updated > last:
        outcome = "future_timestamp" if updated > now + skew else "ok"
    elif updated == last:
        outcome = "duplicate" if digest == state.last_content_hash else "correction"
    else:
        outcome = "out_of_order"
    state.last_outcome = outcome
    if outcome == "future_timestamp":
        log.warning("last_updated %s is ahead of the clock (%s); refused", updated, now)
    if outcome == "correction":
        changed = [
            s.station_id
            for s in pub.stations
            if (st := state.stations.get(s.station_id)) and st.status and st.status != s
        ]
        log.warning("correction at last_updated %s: %s", updated, changed[:20])
    if outcome != "ok":
        return None

    state.last_source_updated_at = updated
    state.first_accepted_at = state.first_accepted_at or updated
    state.last_content_hash = digest
    if state.latched:
        state.fresh_again_count += 1
    eligible = in_service(now, policy.provider.service_hours)
    per_station = policy.provider.station_report_semantics == "per_station"
    records = {s.station_id: s for s in pub.stations}
    invalid = set(pub.invalid_station_ids)
    observed: set[str] = set()
    for sid, st in state.stations.items():
        record = records.get(sid)
        if record is None:
            st.presence = "invalid" if sid in invalid else "missing"
            continue
        previous = st.status.reported_at if st.status else None
        advanced = previous is None or (
            record.reported_at is not None and record.reported_at > previous
        )
        if eligible and (advanced or not per_station):
            observed.add(sid)
        st.status, st.status_updated_at, st.presence = record, updated, "valid"
    state.unknown_ids = frozenset((records.keys() | invalid) - state.stations.keys())
    state.unknown_records = {sid: records[sid] for sid in state.unknown_ids if sid in records}
    if (
        state.unknown_ids - state.removed_ids
        and state.catalog_fetch_requested_for != state.catalog_version
    ):
        # At most one catalog fetch while catalogVersion stays the same.
        state.catalog_fetch_pending = True
        state.catalog_fetch_requested_for = state.catalog_version
    return observed


def _reconcile(state: StreamState, entry: CatalogReceived, now: datetime) -> dict[str, Station]:
    """Returns the stations removed now; they go out once more, with the incidents the
    removal closed, in the catalog event that lists them in removedStationIds."""
    seen = {s.station_id: s for s in entry.catalog.stations}
    for sid, info in seen.items():
        st = state.stations.get(sid)
        if st is None:
            record = state.unknown_records.pop(sid, None)
            st = state.stations[sid] = Station(
                station_id=sid,
                info=info,
                first_seen_at=now,
                last_seen_in_catalog_at=now,
                tracked_since=state.last_source_updated_at,
                status=record,
                status_updated_at=state.last_source_updated_at if record else None,
                presence="valid"
                if record
                else "invalid"
                if sid in state.unknown_ids
                else "missing",
            )
            state.removed_ids.discard(sid)
        st.info = info
        st.last_seen_in_catalog_at = now
        st.missing_catalog_fetches = 0
    removed: dict[str, Station] = {}
    for sid, st in list(state.stations.items()):
        if sid in seen:
            continue
        st.missing_catalog_fetches += 1
        # Missing from every fetch since it was last seen, at least 3 of them over 30 min.
        if (
            st.missing_catalog_fetches >= REMOVAL_MIN_FETCHES
            and now - st.last_seen_in_catalog_at >= REMOVAL_MIN_AGE
        ):
            incidents.close_all(st, "removed_from_catalog", state.last_source_updated_at, now)
            del state.stations[sid]
            state.removed_ids.add(sid)
            removed[sid] = st
            if st.presence != "missing":
                # Still in the status: now an id without a catalog entry.
                state.unknown_ids |= {sid}
                if st.presence == "valid" and st.status:
                    state.unknown_records[sid] = st.status
    state.unknown_ids -= state.stations.keys()
    # The fetch happened, whatever it brought.
    state.catalog_fetch_pending = False
    version = catalog_version(state)
    state.catalog_version = version
    return removed


def catalog_stations(state: StreamState) -> list[CatalogStation]:
    return [
        CatalogStation(
            station_id=sid,
            name=st.info.name,
            lat=st.info.lat,
            lon=st.info.lon,
            capacity=st.info.capacity,
            is_virtual=st.info.is_virtual,
        )
        for sid, st in sorted(state.stations.items())
    ]


def catalog_version(state: StreamState) -> str:
    """Hash of the reconciled catalog (last values seen, removed stations out)."""
    fields = [
        (
            sid,
            st.info.name,
            st.info.lat,
            st.info.lon,
            st.info.capacity,
            st.info.is_virtual,
            st.info.is_valet,
        )
        for sid, st in sorted(state.stations.items())
    ]
    return sha256_hex(fields)[:16]


def _evaluate(
    state: StreamState,
    now: datetime,
    policy: Policy,
    cause: EventCause,
    received_at: datetime | None = None,
    observed: set[str] | None = None,
    force: bool = False,
    removed: dict[str, Station] | None = None,
) -> list[StationBatchEvent]:
    # 1. service hours
    _service_boundaries(state, now, policy)
    # 2. source state, with the current input. The first eligible publication after the
    # start or an opening sets it by age, without hysteresis.
    if observed is not None and state.in_service:
        state.awaiting_since = None
    _source_state(state, now, policy)
    usable = state.source_state in ("fresh", "late")
    changed: list[tuple[Station, StationState, tuple[object, ...]]] = []
    for sid, st in sorted((state.stations | (removed or {})).items()):
        # Kinds observed without their condition since the opening, whatever the source
        # state: this is what EMPTY_AT_OPENING and FULL_AT_OPENING look at.
        if observed and sid in observed and st.status:
            st.seen_without.update(k for k in KINDS if not incidents.has_condition(k, st.status))
        # 3. station freshness
        reason = _station_stale(st, state, policy)
        # 5. incidents (4, the conditions, only read the record)
        stale_by = reason or SOURCE_REASON.get(state.source_state)
        if stale_by:
            incidents.suspend(st, stale_by, state.last_source_updated_at, now)
        elif usable and observed and sid in observed and state.last_source_updated_at:
            for kind in KINDS:
                incidents.observe(
                    st,
                    kind,
                    state.last_source_updated_at,
                    now,
                    policy,
                    state.system_id,
                    state.opened_at,
                )
        # 6. decision. The key leaves out timestamps and the finished incidents, which go
        # out once, in the event of their transition.
        view = _view(st, state, reason)
        key = _change_key(st, view, state)
        if key != st.published_key or st.finished:
            changed.append((st, _station_state(st, state, policy, view), key))

    feed = _feed_health(state, now)
    if cause == "clock" and not changed and feed.state == state.last_event_feed_state:
        return []
    if cause == "catalog" and not force:
        return []
    state.head_sequence += 1
    for st, _, key in changed:
        st.published_key = key
        st.finished.clear()
    state.last_event_feed_state = feed.state
    return [
        StationBatchEvent(
            type="station_batch",
            schema_version=SCHEMA_VERSION,
            event_id=f"{state.stream_id}@{state.head_sequence}",
            stream_id=state.stream_id,
            sequence=state.head_sequence,
            mode=state.mode,
            cause=cause,
            catalog_version=state.catalog_version,
            source_updated_at=state.last_source_updated_at,
            received_at=received_at,
            committed_at=now,
            stations=[station_state for _, station_state, _ in changed],
            removed_station_ids=sorted(removed or {}),
            feed_health=feed,
        )
    ]


def _service_boundaries(state: StreamState, now: datetime, policy: Policy) -> None:
    assert state.service_checked_at is not None
    for at, kind in boundaries(state.service_checked_at, now, policy.provider.service_hours):
        if kind == "close":
            state.in_service = False
            for st in state.stations.values():
                incidents.close_all(st, "service_closed", state.last_source_updated_at, at)
        else:
            # Publications from before the opening do not count toward the age. The wait for
            # the first eligible one counts from when the opening is processed: a worker
            # back at 05:30 reopens in unknown, not stale.
            state.in_service = True
            state.awaiting_since = now
            state.opened_at = at
            state.latched = None
            state.fresh_again_count = 0
            for st in state.stations.values():
                st.seen_without.clear()
    state.service_checked_at = now
    state.in_service = in_service(now, policy.provider.service_hours)


def _source_state(state: StreamState, now: datetime, policy: Policy) -> None:
    p = policy.priority
    stale_after = timedelta(seconds=p.source_stale_after_seconds)
    age = now - state.last_source_updated_at if state.last_source_updated_at else None
    if age is not None and age > stale_after:
        state.fresh_again_count = 0
    breaker_open = state.breaker_open_until is not None and now < state.breaker_open_until
    waited = now - state.awaiting_since if state.awaiting_since is not None else None
    raw: SourceState
    if state.quarantined_since:
        raw = "quarantined"
    elif not state.in_service:
        raw = "closed"
    elif (
        breaker_open
        or state.ingestion_suspended
        or (waited is not None and waited > stale_after)
        or (waited is None and age is not None and age > stale_after)
    ):
        raw = "stale"
    elif waited is not None or age is None:
        raw = "unknown"
    elif age > timedelta(seconds=p.source_late_after_seconds):
        raw = "late"
    else:
        raw = "fresh"

    if raw in ("stale", "quarantined"):
        state.latched = "stale" if raw == "stale" else "quarantined"
        state.source_state = raw
    elif raw == "closed" or state.latched is None:
        state.source_state = raw
    elif state.fresh_again_count >= p.fresh_again_after_publications:
        # Hysteresis done: out by age.
        state.latched = None
        state.fresh_again_count = 0
        state.source_state = raw
    else:
        state.source_state = state.latched


def _station_stale(st: Station, state: StreamState, policy: Policy) -> UnverifiableReason | None:
    """Only a new accepted publication moves this clock; without one the source goes stale."""
    last = state.last_source_updated_at
    if last is None:
        return None
    baseline = st.status_updated_at or st.tracked_since or state.first_accepted_at
    assert baseline is not None
    if last - baseline > timedelta(seconds=policy.priority.source_stale_after_seconds):
        return "invalid_record" if st.presence == "invalid" else "missing_in_status"
    if policy.provider.station_report_semantics == "per_station" and st.status:
        reported = st.status.reported_at
        limit = timedelta(seconds=policy.priority.station_stale_after_seconds)
        if reported is not None and last - reported > limit:
            return "report_stale"
    return None


def _conditions(
    st: Station, state: StreamState, station_reason: UnverifiableReason | None
) -> tuple[list[Condition], UnverifiableReason | None]:
    conditions: list[Condition] = []
    status = st.status
    if state.source_state == "closed":
        conditions.append("outside_service_hours")
    if status and not status.is_installed:
        conditions.append("not_installed")
    # The station's own reason wins over the source's.
    reason = station_reason or SOURCE_REASON.get(state.source_state)
    if status is None and reason is None:
        reason = "missing_in_status"
    if reason:
        conditions.append("unverifiable")
    if status:
        if not status.is_renting:
            conditions.append("pickup_disabled")
        if not status.is_returning:
            conditions.append("return_disabled")
        for kind in KINDS:
            if incidents.has_condition(kind, status):
                conditions.append(kind)
        if status.docks_available is None:
            conditions.append("return_capacity_unknown")
    return conditions or ["ok"], reason


@dataclass(frozen=True)
class _View:
    conditions: list[Condition]
    reason: UnverifiableReason | None
    open_incidents: list[Incident]
    sort_key: str | None
    key_incident: Incident | None
    codes: list[str]
    quality: Quality
    flags: list[str]


def _view(st: Station, state: StreamState, station_reason: UnverifiableReason | None) -> _View:
    conditions, reason = _conditions(st, state, station_reason)
    open_incidents = sorted(st.incidents.values(), key=lambda i: i.kind)
    key, key_incident = _sort_key(st.station_id, open_incidents)
    flags = list(st.status.quality_flags) if st.status else []
    if st.presence != "valid":
        flags.append("invalid_record" if st.presence == "invalid" else "missing_in_status")
    return _View(
        conditions=conditions,
        reason=reason if "unverifiable" in conditions else None,
        open_incidents=open_incidents,
        sort_key=key,
        key_incident=key_incident,
        codes=_reason_codes(st, state, open_incidents, conditions, reason),
        quality=_availability_quality(st),
        flags=flags,
    )


def _station_state(st: Station, state: StreamState, policy: Policy, view: _View) -> StationState:
    status = st.status
    text, facts = (
        (view.key_incident.reason_text, view.key_incident.facts)
        if view.key_incident
        else (st.reason_text, st.facts)
    )
    shown = view.open_incidents + sorted(st.finished, key=lambda i: (i.kind, i.id))
    availability = StationAvailability(
        station_id=st.station_id,
        is_installed=status.is_installed if status else None,
        is_renting=status.is_renting if status else None,
        is_returning=status.is_returning if status else None,
        bikes_available=status.bikes_available if status else None,
        docks_available=status.docks_available if status else None,
        vehicle_types=[
            VehicleTypeCount(vehicle_type_id=t, count=c)
            for t, c in (status.vehicle_types if status else ())
        ],
        source_updated_at=st.status_updated_at,
        station_reported_at=status.reported_at if status else None,
        quality=view.quality,
        quality_flags=view.flags,
    )
    decision = PriorityDecision(
        station_id=st.station_id,
        conditions=view.conditions,
        primary_condition=view.conditions[0],
        unverifiable_reason=view.reason,
        reason_codes=view.codes,
        reason_text=text,
        facts=facts,
        quality=view.quality,
        incidents=[_item(i) for i in shown],
        policy_version=state.policy_version or policy.version,
        sort_key=view.sort_key,
    )
    return StationState(availability=availability, decision=decision)


def _sort_key(
    station_id: str, open_incidents: list[Incident]
) -> tuple[str | None, Incident | None]:
    ranked: list[tuple[int, datetime, Incident]] = []
    for inc in open_incidents:
        if inc.state in ("confirmed", "recovering"):
            ranked.append((0, inc.first_observed_at, inc))
        elif inc.state == "candidate":
            ranked.append((1, inc.first_observed_at, inc))
        elif inc.unverifiable_reason in incidents.SOURCE_REASONS:
            # Stale source: same tier and key as before, shown dimmed.
            tier = 0 if inc.suspended_from in ("confirmed", "recovering") else 1
            ranked.append((tier, inc.first_observed_at, inc))
        # Unverifiable for the station's own reason: out of the queue (data review).
    if not ranked:
        return None, None
    tier, first, inc = min(ranked, key=lambda r: (r[0], r[1]))
    return f"{tier}|{int(first.timestamp()):010d}|{station_id}", inc


def _reason_codes(
    st: Station,
    state: StreamState,
    open_incidents: list[Incident],
    conditions: list[Condition],
    reason: UnverifiableReason | None,
) -> list[str]:
    codes: list[str] = []
    for inc in open_incidents:
        full = inc.kind == "return_unavailable"
        if inc.state == "candidate":
            codes.append("CANDIDATE_FULL" if full else "CANDIDATE_EMPTY")
        elif inc.state == "confirmed":
            codes.append("CONFIRMED_FULL" if full else "CONFIRMED_EMPTY")
        elif inc.state == "recovering":
            codes.append("RECOVERING")
        if inc.at_opening:
            codes.append("FULL_AT_OPENING" if full else "EMPTY_AT_OPENING")
    if state.source_state == "closed":
        codes.append("OUTSIDE_SERVICE_HOURS")
    if reason:
        codes.append(reason.upper())
    if st.info.capacity is None:
        codes.append("CAPACITY_UNKNOWN")
    for condition in (
        "not_installed",
        "pickup_disabled",
        "return_disabled",
        "return_capacity_unknown",
    ):
        if condition in conditions:
            codes.append(condition.upper())
    return list(dict.fromkeys(codes))


def _item(inc: Incident) -> IncidentItem:
    return IncidentItem(
        id=inc.id,
        kind=inc.kind,
        state=inc.state,
        suspended_from=inc.suspended_from if inc.state == "unverifiable" else None,
        first_observed_at=inc.first_observed_at,
        observed_since=inc.observed_since,
        confirmed_at=inc.confirmed_at,
        close_reason=inc.close_reason,
    )


def _availability_quality(st: Station) -> Quality:
    if st.presence == "invalid":
        return "invalid"
    return st.status.quality if st.status else "unknown"


def _change_key(st: Station, view: _View, state: StreamState) -> tuple[object, ...]:
    """What counts as a relevant change: counts, flags, quality, condition or decision.
    Timestamps alone do not."""
    status = st.status
    return (
        (
            status.is_installed,
            status.is_renting,
            status.is_returning,
            status.bikes_available,
            status.docks_available,
            status.vehicle_types,
        )
        if status
        else None,
        view.quality,
        tuple(view.flags),
        tuple(view.conditions),
        view.reason,
        tuple(view.codes),
        tuple((i.id, i.state, i.suspended_from) for i in view.open_incidents),
        view.sort_key,
        state.policy_version,
    )


def _feed_health(state: StreamState, now: datetime) -> FeedHealth:
    breaker = state.breaker_open_until
    valid = sum(1 for st in state.stations.values() if st.presence == "valid")
    return FeedHealth(
        state=state.source_state,
        last_source_updated_at=state.last_source_updated_at,
        last_attempt_at=state.last_attempt_at,
        last_outcome=state.last_outcome,
        coverage=Coverage(valid=valid, total=len(state.stations) + len(state.unknown_ids)),
        breaker_open_until=breaker if breaker and breaker > now else None,
        quarantined_feeds=sorted(state.quarantined_feeds),
    )


def state_hash(state: StreamState) -> str:
    """Engine state for golden tests and determinism checks. Leaves out last_updated,
    last_reported, receivedAt and tick instants; incident instants stay in."""
    return sha256_hex(
        {
            "source": state.source_state,
            "latched": state.latched,
            "freshAgain": state.fresh_again_count,
            "catalogVersion": state.catalog_version,
            "policyVersion": state.policy_version,
            "contentHash": state.last_content_hash,
            "breakerOpen": state.breaker_open_until is not None,
            "quarantined": state.quarantined_since is not None,
            "stations": [
                {
                    "id": sid,
                    "presence": st.presence,
                    "status": (
                        [
                            st.status.is_installed,
                            st.status.is_renting,
                            st.status.is_returning,
                            st.status.bikes_available,
                            st.status.docks_available,
                            st.status.vehicle_types,
                            st.status.quality,
                        ]
                        if st.status
                        else None
                    ),
                    "incidents": [
                        [
                            i.id,
                            i.kind,
                            i.state,
                            i.first_observed_at,
                            i.observed_since,
                            i.confirmed_at,
                            i.suspended_from,
                            i.unverifiable_reason,
                            i.at_opening,
                            i.observations,
                        ]
                        for i in sorted(st.incidents.values(), key=lambda i: i.kind)
                    ],
                }
                for sid, st in sorted(state.stations.items())
            ],
        }
    )
