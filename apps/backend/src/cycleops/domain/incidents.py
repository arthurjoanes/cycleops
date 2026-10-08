"""Incident state machine, one per station and kind, and the text of each transition."""

import hashlib
import math
from datetime import datetime, timedelta

from cycleops.adapters.gbfs import StationStatus
from cycleops.contracts import CloseReason, DecisionFacts, IncidentKind, IncidentState
from cycleops.contracts import UnverifiableReason as Reason
from cycleops.domain.model import FINAL_STATES, Incident, Station
from cycleops.policy import Policy

LABEL: dict[IncidentKind, str] = {
    "pickup_unavailable": "sem bicicletas",
    "return_unavailable": "sem vagas",
}
UNITS: dict[IncidentKind, str] = {"pickup_unavailable": "bicicletas", "return_unavailable": "vagas"}
REASON_TEXT: dict[Reason, str] = {
    "source_unknown": "fonte sem dado",
    "source_stale": "fonte vencida",
    "source_quarantined": "fonte em quarentena",
    "missing_in_status": "estação ausente do status",
    "invalid_record": "registro inválido no status",
    "report_stale": "relato da estação vencido",
}
CAUSE_TEXT: dict[CloseReason, str] = {
    "contrary_observation": "observação sem a condição",
    "source_stale": "fonte vencida",
    "station_stale": "dados da estação vencidos",
    "flag_disabled": "operação desabilitada",
    "uninstalled": "estação desinstalada",
    "return_capacity_unknown": "vagas não informadas pelo feed",
    "removed_from_catalog": "estação removida do cadastro",
    "service_closed": "sistema fechado",
}
SOURCE_REASONS: frozenset[Reason] = frozenset(
    {"source_unknown", "source_stale", "source_quarantined"}
)


def incident_id(system_id: str, station_id: str, kind: IncidentKind, opened: datetime) -> str:
    key = f"{system_id}|{station_id}|{kind}|{int(opened.timestamp())}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def available(kind: IncidentKind, status: StationStatus) -> int | None:
    return status.bikes_available if kind == "pickup_unavailable" else status.docks_available


def has_condition(kind: IncidentKind, status: StationStatus) -> bool:
    if kind == "pickup_unavailable":
        return status.is_installed and status.is_renting and status.bikes_available == 0
    return status.is_installed and status.is_returning and status.docks_available == 0


def closing_cause(kind: IncidentKind, status: StationStatus) -> CloseReason | None:
    if not status.is_installed:
        return "uninstalled"
    enabled = status.is_renting if kind == "pickup_unavailable" else status.is_returning
    if not enabled:
        return "flag_disabled"
    if kind == "return_unavailable" and status.docks_available is None:
        return "return_capacity_unknown"
    return None


def recovery_min_units(status: StationStatus, capacity: int | None, policy: Policy) -> int:
    p = policy.priority
    if capacity is None:
        return 1
    if status.docks_available is None:
        base = capacity
    else:
        base = min(capacity, status.bikes_available + status.docks_available)
    # round() keeps 0.1 * 30 from becoming 3.0000000000000004 and rounding up to 4.
    wanted = math.ceil(round(p.recovery_min_fraction * base, 9))
    return max(p.recovery_min_floor, min(p.recovery_min_cap, wanted))


def _facts(st: Station, inc: Incident, at: datetime | None, units: int | None) -> DecisionFacts:
    status = st.status
    span = inc.observations[-1] - inc.observations[0] if inc.observations else timedelta(0)
    return DecisionFacts(
        bikes_available=status.bikes_available if status else None,
        docks_available=status.docks_available if status else None,
        capacity_used=st.info.capacity,
        recovery_min_units=units,
        publications_used=len(inc.observations),
        span_seconds=int(span.total_seconds()),
        source_updated_at=at,
    )


def _move(
    st: Station,
    inc: Incident,
    state: IncidentState,
    text: str,
    at: datetime | None,
    now: datetime,
    units: int | None = None,
    close: CloseReason | None = None,
) -> None:
    inc.state = state
    inc.reason_text = text
    inc.facts = _facts(st, inc, at, units)
    st.reason_text, st.facts = inc.reason_text, inc.facts
    if state in FINAL_STATES:
        inc.close_reason = close
        inc.closed_at = now
        st.incidents.pop(inc.kind, None)
        st.finished.append(inc)


def observe(
    st: Station,
    kind: IncidentKind,
    at: datetime,
    now: datetime,
    policy: Policy,
    system_id: str,
    opened_at: datetime | None,
) -> None:
    """One observation: an eligible publication with a valid record for the station, with the
    source fresh or late and the station not stale. `at` is its sourceUpdatedAt."""
    status = st.status
    assert status is not None
    inc = st.incidents.get(kind)
    condition = has_condition(kind, status)
    if inc is None:
        if condition:
            inc = Incident(
                id=incident_id(system_id, st.station_id, kind, at),
                kind=kind,
                state="candidate",
                first_observed_at=at,
                observed_since=at,
                policy_version=policy.version,
                # Empty or full at opening: no observation without the condition since then.
                at_opening=opened_at is not None and kind not in st.seen_without,
                observations=[at],
            )
            st.incidents[kind] = inc
            _move(st, inc, "candidate", f"{LABEL[kind]}, em confirmação", at, now)
        return

    units = recovery_min_units(status, st.info.capacity, policy)
    count = available(kind, status)
    # The closing cause comes before any other transition, even one that would resolve.
    cause = closing_cause(kind, status)
    if inc.state == "candidate":
        if cause:
            _move(st, inc, "discarded", f"descartado: {CAUSE_TEXT[cause]}", at, now, close=cause)
        elif not condition:
            text = f"descartado: {CAUSE_TEXT['contrary_observation']}"
            _move(st, inc, "discarded", text, at, now, close="contrary_observation")
        else:
            inc.observations.append(at)
            span = inc.observations[-1] - inc.observations[0]
            if len(inc.observations) >= 2 and span >= _seconds(
                policy.priority.confirm_min_span_seconds
            ):
                inc.confirmed_at = inc.confirmed_at or at
                text = (
                    f"{LABEL[kind]}, confirmado com {len(inc.observations)} publicações "
                    f"em {int(span.total_seconds())} s"
                )
                _move(st, inc, "confirmed", text, at, now, units)
        return

    if cause and inc.state in ("confirmed", "recovering", "unverifiable"):
        text = f"encerrado sem recuperação: {CAUSE_TEXT[cause]}"
        _move(st, inc, "closed_without_recovery", text, at, now, units, close=cause)
        return
    recovered = count is not None and count >= units
    if inc.state == "unverifiable":
        # Back with data: a new stretch starts at this observation.
        inc.observed_since = at
        inc.suspended_from = None
        inc.unverifiable_reason = None
        if recovered:
            inc.observations = [at]
            _move(st, inc, "recovering", _recovering_text(kind, count, units), at, now, units)
        else:
            inc.observations = []
            _move(st, inc, "confirmed", f"{LABEL[kind]}, dados de volta", at, now, units)
    elif inc.state == "confirmed":
        # Between 1 and recoveryMinUnits - 1 it stays confirmed.
        if recovered:
            inc.observations = [at]
            _move(st, inc, "recovering", _recovering_text(kind, count, units), at, now, units)
    elif inc.state == "recovering":
        if not recovered:
            inc.observations = []
            _move(st, inc, "confirmed", f"{LABEL[kind]} de novo", at, now, units)
            return
        inc.observations.append(at)
        span = inc.observations[-1] - inc.observations[0]
        if len(inc.observations) >= 2 and span >= _seconds(
            policy.priority.recovery_min_span_seconds
        ):
            text = (
                f"resolvido: {count} {UNITS[kind]} em {len(inc.observations)} publicações "
                f"por {int(span.total_seconds())} s"
            )
            _move(st, inc, "resolved", text, at, now, units)


def suspend(st: Station, reason: Reason, at: datetime | None, now: datetime) -> None:
    """The source or the station went stale: candidates are discarded, confirmed and
    recovering incidents wait in unverifiable."""
    for inc in list(st.incidents.values()):
        if inc.state == "candidate":
            cause: CloseReason = "source_stale" if reason in SOURCE_REASONS else "station_stale"
            _move(st, inc, "discarded", f"descartado: {CAUSE_TEXT[cause]}", at, now, close=cause)
        elif inc.state in ("confirmed", "recovering"):
            inc.suspended_from = inc.state
            inc.unverifiable_reason = reason
            inc.observations = []
            _move(st, inc, "unverifiable", f"dados vencidos: {REASON_TEXT[reason]}", at, now)
        elif inc.state == "unverifiable" and inc.unverifiable_reason != reason:
            inc.unverifiable_reason = reason
            inc.reason_text = st.reason_text = f"dados vencidos: {REASON_TEXT[reason]}"


def close_all(st: Station, cause: CloseReason, at: datetime | None, now: datetime) -> None:
    """service_closed and removed_from_catalog: they also close unverifiable incidents."""
    for inc in list(st.incidents.values()):
        if inc.state == "candidate":
            _move(st, inc, "discarded", f"descartado: {CAUSE_TEXT[cause]}", at, now, close=cause)
        else:
            text = f"encerrado sem recuperação: {CAUSE_TEXT[cause]}"
            _move(st, inc, "closed_without_recovery", text, at, now, close=cause)


def _recovering_text(kind: IncidentKind, count: int | None, units: int) -> str:
    return f"recuperando: {count} {UNITS[kind]}, mínimo {units}"


def _seconds(value: int) -> timedelta:
    return timedelta(seconds=value)
