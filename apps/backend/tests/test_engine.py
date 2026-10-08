# The tests assert a state, act, then assert another state of the same attribute.
# mypy: disable-error-code="comparison-overlap"
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from conftest import POLICY_DIR, T0, info_body, station, status_body

from cycleops.adapters.gbfs import ReportSemantics, parse_station_information, parse_station_status
from cycleops.contracts import IncidentKind, StationBatchEvent, StationState
from cycleops.domain.engine import new_stream, policy_change, state_hash, step
from cycleops.domain.model import (
    CatalogReceived,
    ClockTick,
    Incident,
    PolicyChanged,
    PublicationFailed,
    PublicationReceived,
)
from cycleops.domain.service_hours import Hours
from cycleops.policy import Policy, ServiceHours, load_priority_policy, load_provider

START = datetime.fromtimestamp(T0, UTC)  # 10:00 in Oslo
OSLO = ZoneInfo("Europe/Oslo")
OSLO_HOURS = ServiceHours(
    opens="05:00",
    closes="01:00",
    timezone="Europe/Oslo",
    source="test",
    checked_at=date(2026, 10, 6),
)
PICKUP: IncidentKind = "pickup_unavailable"
RETURN: IncidentKind = "return_unavailable"


def oslo(hhmm: str, day: int = 7) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=OSLO).astimezone(UTC)


class Feed:
    """Drives the engine with publications whose file is `age` seconds old at receipt.
    Times are seconds from `start`."""

    def __init__(
        self,
        ids: tuple[str, ...] = ("A", "B"),
        capacity: int = 10,
        hours: Hours = "always",
        semantics: ReportSemantics = "feed_generation",
        start: datetime = START,
    ) -> None:
        provider = load_provider("synthetic", POLICY_DIR).model_copy(
            update={"service_hours": hours, "station_report_semantics": semantics}
        )
        self.policy = Policy(load_priority_policy(POLICY_DIR), provider)
        self.semantics = semantics
        self.start = start
        self.capacity = capacity
        self.state = new_stream("test", "synthetic", "test:00000000")
        self.events: list[StationBatchEvent] = []
        self.catalog(0, ids)

    def t(self, seconds: float) -> datetime:
        return self.start + timedelta(seconds=seconds)

    def apply(self, entry: Any) -> list[StationBatchEvent]:
        events = step(self.state, entry, self.policy)[1]
        self.events += events
        return events

    def catalog(
        self, at: float, ids: tuple[str, ...], capacity: int | None = None
    ) -> list[StationBatchEvent]:
        cap = self.capacity if capacity is None else capacity
        records = [
            {"station_id": i, "name": f"Station {i}", "lat": 59.9, "lon": 10.7, "capacity": cap}
            for i in ids
        ]
        return self.apply(
            CatalogReceived(self.t(at), parse_station_information(info_body(records)))
        )

    def publish(
        self,
        at: float,
        bikes: dict[str, int],
        updated: float | None = None,
        reported: float | None = None,
        **fields: dict[str, Any],
    ) -> str | None:
        updated = at - 10 if updated is None else updated
        epoch = int(self.t(updated).timestamp())
        records = []
        for sid, count in bikes.items():
            record = station(
                station_id=sid,
                num_bikes_available=count,
                num_docks_available=self.capacity - count,
                vehicle_types_available=None,
                last_reported=epoch + 4 if reported is None else int(self.t(reported).timestamp()),
            )
            record.update(fields.get(sid, {}))
            records.append({k: v for k, v in record.items() if v is not None})
        publication = parse_station_status(
            status_body(records, last_updated=epoch), self.semantics, self.t(at + 30)
        )
        self.apply(PublicationReceived(self.t(at), publication))
        return self.state.last_outcome

    def tick(self, at: float) -> list[StationBatchEvent]:
        return self.apply(ClockTick(self.t(at)))

    def incident(self, sid: str, kind: IncidentKind = PICKUP) -> Incident | None:
        return self.state.stations[sid].incidents.get(kind)

    def shown(self, sid: str) -> StationState:
        """The station as the last event that included it shows it."""
        for event in reversed(self.events):
            for s in event.stations:
                if s.decision.station_id == sid:
                    return s
        raise AssertionError(f"{sid} never went out")


def confirmed_feed(capacity: int = 10, **kwargs: Any) -> Feed:
    feed = Feed(capacity=capacity, **kwargs)
    for at in (10, 25, 40):  # sourceUpdatedAt 0, 15, 30
        feed.publish(at, {"A": 0, "B": 5})
    inc = feed.incident("A")
    assert inc is not None and inc.state == "confirmed"
    return feed


# Publications


def test_unknown_return_capacity_is_not_zero() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 10}, A={"num_docks_available": None})
    a, b = feed.shown("A").decision, feed.shown("B").decision
    assert "return_capacity_unknown" in a.conditions and "return_unavailable" not in a.conditions
    assert "return_unavailable" in b.conditions
    assert feed.incident("A", RETURN) is None and feed.incident("B", RETURN) is not None


def test_invalid_record_goes_unverifiable_after_the_stale_limit() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5})
    for at in range(25, 100, 15):
        feed.publish(at, {"A": -1, "B": 5})
    shown = feed.shown("A")
    assert shown.availability.quality == "invalid"
    assert shown.decision.unverifiable_reason == "invalid_record"


def test_future_last_updated_is_refused_without_changing_state() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5})
    before, count = state_hash(feed.state), len(feed.events)
    assert feed.publish(25, {"A": 0, "B": 5}, updated=25 + 31) == "future_timestamp"
    assert state_hash(feed.state) == before and len(feed.events) == count
    assert feed.state.last_source_updated_at == feed.t(0)


def test_same_last_updated_with_other_content_is_a_correction() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5})
    before, count = state_hash(feed.state), len(feed.events)
    assert feed.publish(25, {"A": 3, "B": 5}, updated=0) == "duplicate"
    assert feed.publish(30, {"A": 0, "B": 5}, updated=0) == "correction"
    assert state_hash(feed.state) == before and len(feed.events) == count
    assert feed.incident("A") is None


def test_older_last_updated_is_out_of_order() -> None:
    feed = Feed()
    feed.publish(25, {"A": 3, "B": 5}, updated=15)
    assert feed.publish(30, {"A": 0, "B": 5}, updated=0) == "out_of_order"
    assert feed.incident("A") is None


def test_accepted_publication_always_makes_an_event() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5})
    count = len(feed.events)
    feed.publish(25, {"A": 3, "B": 5})
    assert len(feed.events) == count + 1 and feed.events[-1].stations == []


# Source state


def test_late_then_stale() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5})
    feed.tick(35)
    assert feed.state.source_state == "fresh"
    feed.tick(45)
    assert feed.state.source_state == "late"
    feed.tick(65)
    assert feed.state.source_state == "stale"
    assert feed.events[-1].cause == "clock" and feed.events[-1].feed_health.state == "stale"


def test_stale_source_suspends_and_resumes_with_a_new_stretch() -> None:
    feed = confirmed_feed()
    for at in range(45, 125, 5):
        feed.tick(at)  # no publication: age reaches 90 s at 120
    inc = feed.incident("A")
    assert inc is not None and inc.state == "unverifiable" and inc.suspended_from == "confirmed"
    assert feed.shown("A").decision.sort_key is not None  # same tier, shown dimmed
    feed.publish(130, {"A": 0, "B": 5})
    assert feed.state.source_state == "stale"  # one of two publications
    assert inc.state == "unverifiable"
    feed.publish(145, {"A": 0, "B": 5})
    assert feed.state.source_state == "fresh"
    assert inc.state == "confirmed" and inc.observed_since == feed.t(135)
    assert inc.first_observed_at == feed.t(0) and inc.confirmed_at == feed.t(30)


def test_short_gap_keeps_state_and_stretch() -> None:
    feed = confirmed_feed()
    feed.tick(75)  # last sourceUpdatedAt 30: age 45 s, late but not stale
    assert feed.state.source_state == "late"
    feed.publish(80, {"A": 0, "B": 5})
    inc = feed.incident("A")
    assert inc is not None and inc.state == "confirmed" and inc.observed_since == feed.t(0)


def test_open_breaker_makes_the_source_stale_on_the_next_tick() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5})
    entry = PublicationFailed(
        feed.t(20), "station_status", "timeout", "transport", feed.t(80), None
    )
    assert feed.apply(entry) == []
    feed.tick(25)
    assert feed.state.source_state == "stale"
    assert feed.events[-1].feed_health.breaker_open_until == feed.t(80)


def test_quarantine_needs_two_publications_to_clear() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5})
    feed.apply(
        PublicationFailed(feed.t(25), "station_status", "invalid", "contract", None, feed.t(25))
    )
    feed.tick(30)
    assert feed.state.source_state == "quarantined"
    feed.publish(330, {"A": 3, "B": 5})
    assert feed.state.source_state == "quarantined"
    feed.publish(345, {"A": 3, "B": 5})
    assert feed.state.source_state == "fresh"


# Service hours


def test_empty_station_at_night_is_not_an_incident() -> None:
    feed = Feed(hours=OSLO_HOURS, start=oslo("02:00"))
    for at in range(10, 200, 15):
        feed.publish(at, {"A": 0, "B": 5})
    assert feed.state.source_state == "closed"
    assert feed.incident("A") is None
    assert feed.shown("A").decision.primary_condition == "outside_service_hours"


def test_incident_open_at_closing_closes_with_service_closed() -> None:
    feed = confirmed_feed(hours=OSLO_HOURS, start=oslo("00:58"))
    inc = feed.incident("A")
    feed.tick(120)  # 01:00
    assert feed.incident("A") is None and inc is not None
    assert (inc.state, inc.close_reason) == ("closed_without_recovery", "service_closed")
    event = feed.events[-1]
    assert event.cause == "clock" and event.feed_health.state == "closed"
    item = next(s for s in event.stations if s.decision.station_id == "A").decision.incidents[0]
    assert item.state == "closed_without_recovery"


@pytest.mark.parametrize("frozen", [False, True])
def test_opening_is_stale_only_with_a_frozen_feed(frozen: bool) -> None:
    feed = Feed(hours=OSLO_HOURS, start=oslo("04:58"))
    states = []
    for at in range(10, 360, 5):
        if at % 15 == 10:
            feed.publish(at, {"A": 3, "B": 5}, updated=0 if frozen and at > 130 else at - 10)
        feed.tick(at)
        states.append(feed.state.source_state)
    assert states[0] == "closed" and "unknown" in states
    assert ("stale" in states) is frozen


def test_empty_at_opening() -> None:
    feed = Feed(hours=OSLO_HOURS, start=oslo("04:58"))
    for at in range(10, 300, 15):
        feed.publish(at, {"A": 0, "B": 5 if at < 200 else 0})
    a, b = feed.shown("A").decision, feed.shown("B").decision
    assert "EMPTY_AT_OPENING" in a.reason_codes and "CONFIRMED_EMPTY" in a.reason_codes
    assert "EMPTY_AT_OPENING" not in b.reason_codes


def test_worker_stopped_overnight_closes_and_reopens_unknown() -> None:
    feed = confirmed_feed(hours=OSLO_HOURS, start=oslo("00:49"))
    inc = feed.incident("A")
    feed.tick((oslo("05:30") - feed.start).total_seconds())
    assert inc is not None and inc.close_reason == "service_closed"
    assert feed.state.source_state == "unknown"


# Incidents


def test_three_empty_publications_confirm_on_the_third() -> None:
    feed = Feed()
    feed.publish(10, {"A": 0, "B": 5})
    feed.publish(25, {"A": 0, "B": 5})
    inc = feed.incident("A")
    assert inc is not None and inc.state == "candidate"
    feed.publish(40, {"A": 0, "B": 5})
    assert inc.state == "confirmed" and inc.confirmed_at == feed.t(30)
    assert (
        feed.shown("A").decision.reason_text
        == "sem bicicletas, confirmado com 3 publicações em 30 s"
    )


def test_zero_one_zero_discards_the_candidate() -> None:
    feed = Feed()
    feed.publish(10, {"A": 0, "B": 5})
    first = feed.incident("A")
    feed.publish(25, {"A": 1, "B": 5})
    assert first is not None and first.close_reason == "contrary_observation"
    feed.publish(40, {"A": 0, "B": 5})
    second = feed.incident("A")
    assert second is not None and second.id != first.id and second.state == "candidate"


def test_relapse_keeps_the_same_incident() -> None:
    feed = confirmed_feed()
    inc = feed.incident("A")
    feed.publish(55, {"A": 3, "B": 5})
    assert inc is not None and inc.state == "recovering"
    feed.publish(85, {"A": 0, "B": 5})
    assert feed.incident("A") is inc and inc.state == "confirmed"
    assert inc.confirmed_at == feed.t(30)


def test_resolution_needs_observations_covering_60_s() -> None:
    feed = confirmed_feed()
    inc = feed.incident("A")
    for at in (55, 70, 85, 100):  # sourceUpdatedAt 45 to 90: 45 s
        feed.publish(at, {"A": 5, "B": 5})
        assert inc is not None and inc.state == "recovering"
    feed.publish(115, {"A": 5, "B": 5})  # 105: 60 s
    assert inc is not None and inc.state == "resolved" and feed.incident("A") is None


def test_below_recovery_min_units_stays_confirmed() -> None:
    feed = confirmed_feed(capacity=30)  # 10% of 30 is 3
    inc = feed.incident("A")
    for count in (1, 2):
        feed.publish(55 + 15 * count, {"A": count, "B": 5})
        assert inc is not None and inc.state == "confirmed"
    feed.publish(100, {"A": 3, "B": 5})
    assert inc is not None and inc.state == "recovering"


@pytest.mark.parametrize(
    ("count", "expected"), [(0, "confirmed"), (1, "confirmed"), (3, "recovering")]
)
def test_back_from_unverifiable(count: int, expected: str) -> None:
    feed = confirmed_feed(capacity=30)
    feed.tick(110)
    inc = feed.incident("A")
    assert inc is not None and inc.state == "unverifiable"
    feed.publish(115, {"A": count, "B": 5})
    feed.publish(130, {"A": count, "B": 5})
    assert inc.state == expected and inc.observed_since == feed.t(120)


@pytest.mark.parametrize("from_state", ["confirmed", "recovering", "about_to_resolve"])
def test_disabled_renting_closes_without_recovery(from_state: str) -> None:
    feed = confirmed_feed()
    inc = feed.incident("A")
    at = 55
    if from_state != "confirmed":
        for at in (55, 70, 85, 100)[: 1 if from_state == "recovering" else 4]:
            feed.publish(at, {"A": 5, "B": 5})
        at += 15
    assert inc is not None and inc.state == (
        "confirmed" if from_state == "confirmed" else "recovering"
    )
    bikes = 0 if from_state == "confirmed" else 5
    feed.publish(at, {"A": bikes, "B": 5}, A={"is_renting": False})
    assert (inc.state, inc.close_reason) == ("closed_without_recovery", "flag_disabled")


def test_missing_docks_closes_a_return_incident() -> None:
    feed = Feed()
    for at in (10, 25, 40):
        feed.publish(at, {"A": 10, "B": 5})
    inc = feed.incident("A", RETURN)
    assert inc is not None and inc.state == "confirmed"
    feed.publish(55, {"A": 9, "B": 5}, A={"num_docks_available": None})
    assert (inc.state, inc.close_reason) == ("closed_without_recovery", "return_capacity_unknown")


def test_station_missing_from_status_goes_unverifiable() -> None:
    feed = Feed()
    feed.publish(10, {"A": 0, "B": 5})
    candidate = feed.incident("A")
    for at in range(25, 100, 15):
        feed.publish(at, {"B": 5})
        assert candidate is not None and candidate.state != "confirmed"
    assert candidate is not None and candidate.close_reason == "station_stale"
    decision = feed.shown("A").decision
    assert decision.unverifiable_reason == "missing_in_status" and decision.sort_key is None


def test_confirmed_station_missing_from_status_leaves_the_queue() -> None:
    feed = confirmed_feed()
    for at in range(55, 130, 15):
        feed.publish(at, {"B": 5})
    inc = feed.incident("A")
    assert inc is not None and (inc.state, inc.unverifiable_reason) == (
        "unverifiable",
        "missing_in_status",
    )
    assert feed.shown("A").decision.sort_key is None


@pytest.mark.parametrize("capacity", [1, 0])
def test_small_or_missing_capacity_resolves(capacity: int) -> None:
    feed = Feed(capacity=capacity)
    feed.catalog(1, ("A", "B"), capacity=capacity)
    for at in (10, 25, 40):
        feed.publish(
            at, {"A": 0, "B": 0}, A={"num_docks_available": 1}, B={"num_docks_available": 1}
        )
    inc = feed.incident("A")
    assert inc is not None and inc.state == "confirmed"
    for at in range(55, 140, 15):
        feed.publish(
            at, {"A": 1, "B": 0}, A={"num_docks_available": 0}, B={"num_docks_available": 1}
        )
    assert inc.state == "resolved"
    if capacity == 0:
        assert "CAPACITY_UNKNOWN" in feed.shown("A").decision.reason_codes


def test_per_station_needs_last_reported_to_advance() -> None:
    feed = Feed(semantics="per_station")
    for at in (10, 25, 40, 55):
        feed.publish(at, {"A": 0, "B": 5}, reported=0)
    inc = feed.incident("A")
    assert inc is not None and inc.state == "candidate"
    for at in range(70, 170, 15):
        feed.publish(at, {"A": 0, "B": 5}, reported=0)
    assert feed.shown("A").decision.unverifiable_reason == "report_stale"


def test_two_incidents_at_one_station() -> None:
    feed = Feed()
    for at in (10, 25, 40):
        feed.publish(at, {"A": 0, "B": 5}, A={"num_docks_available": 0})
    decision = feed.shown("A").decision
    assert [i.kind for i in decision.incidents] == [PICKUP, RETURN]
    assert decision.reason_codes[:2] == ["CONFIRMED_EMPTY", "CONFIRMED_FULL"]


# Order, catalog and policy


def test_sort_key_order_and_first_observed_at_through_unverifiable() -> None:
    feed = Feed(ids=("A", "B", "C"))
    feed.publish(10, {"A": 0, "B": 5, "C": 5})
    feed.publish(25, {"A": 0, "B": 0, "C": 5})
    feed.publish(40, {"A": 0, "B": 0, "C": 5})
    feed.publish(55, {"A": 0, "B": 0, "C": 0})
    keys = {sid: feed.shown(sid).decision.sort_key for sid in "ABC"}
    base = int(START.timestamp())
    assert keys == {
        "A": f"0|{base:010d}|A",
        "B": f"0|{base + 15:010d}|B",
        "C": f"1|{base + 45:010d}|C",
    }
    assert sorted(keys, key=lambda s: keys[s] or "") == ["A", "B", "C"]
    for at in range(60, 130, 5):
        feed.tick(at)
    assert feed.shown("A").decision.sort_key == keys["A"]


def test_unknown_station_triggers_one_catalog_fetch() -> None:
    feed = Feed()
    feed.publish(10, {"A": 3, "B": 5, "Z": 1})
    assert feed.state.catalog_fetch_pending and feed.events[-1].feed_health.coverage.total == 3
    feed.state.catalog_fetch_pending = False  # the worker fetched it
    feed.publish(25, {"A": 3, "B": 5, "Z": 1})
    assert not feed.state.catalog_fetch_pending
    feed.catalog(30, ("A", "B", "Z"))
    assert "Z" in feed.state.stations


@pytest.mark.parametrize(
    ("fetches", "removed"),
    [((600, 1200, 1700), False), ((1200, 1800), False), ((600, 1200, 1800), True)],
)
def test_catalog_removal_needs_3_fetches_over_30_min(
    fetches: tuple[int, ...], removed: bool
) -> None:
    feed = Feed()
    events = []
    for at in fetches:
        events += feed.catalog(at, ("A",))
    assert ("B" not in feed.state.stations) is removed
    assert any(e.removed_station_ids == ["B"] for e in events) is removed


def test_policy_version_ignores_crlf(tmp_path: Any) -> None:
    for name in ("priority-policy.yaml", "provider-oslo.yaml"):
        text = (POLICY_DIR / name).read_text(encoding="utf-8")
        (tmp_path / name).write_bytes(text.replace("\n", "\r\n").encode())
    crlf = Policy(load_priority_policy(tmp_path), load_provider("oslo", tmp_path))
    lf = Policy(load_priority_policy(POLICY_DIR), load_provider("oslo", POLICY_DIR))
    assert crlf.version == lf.version
    assert lf.version.startswith("1.0.0+") and len(lf.version) == len("1.0.0+") + 8


def test_policy_changed_only_when_the_version_changes() -> None:
    feed = confirmed_feed()
    assert policy_change(feed.state, feed.policy, feed.t(50)) is None
    other = feed.policy.with_service_hours(OSLO_HOURS)
    entry = policy_change(feed.state, other, feed.t(50))
    assert entry == PolicyChanged(feed.t(50), other.version)
    events = feed.apply(entry)
    assert events[0].cause == "policy"
    assert {s.decision.policy_version for s in events[0].stations} == {other.version}
    fresh = new_stream("test", "synthetic", "test:1")
    assert policy_change(fresh, other, feed.t(50)) is None


def test_golden_state_hash_and_determinism() -> None:
    def run() -> tuple[str, str]:
        feed = confirmed_feed()
        for at in (55, 70, 85, 100, 115):
            feed.publish(at, {"A": 5, "B": 0})
        feed.tick(200)
        return state_hash(feed.state), "".join(e.model_dump_json() for e in feed.events)

    first, second = run(), run()
    assert first == second
    assert first[0] == GOLDEN_STATE_HASH


def test_clock_never_goes_back() -> None:
    feed = Feed()
    feed.publish(30, {"A": 3, "B": 5})
    feed.tick(20)
    assert feed.state.clock_now == feed.t(30)
    feed.publish(25, {"A": 0, "B": 5}, updated=24)
    assert feed.events[-1].committed_at == feed.t(30)


GOLDEN_STATE_HASH = "d7244be79b4ce32d89f986c24a35903c557199bec51db2fe13663c7970a21919"
