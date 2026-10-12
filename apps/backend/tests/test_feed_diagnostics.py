from datetime import timedelta
from typing import Any

from integration.feed import at

from cycleops.application.feed_diagnostics import feed_diagnostics


def row(seconds: int, outcome: str = "ok", status: int | None = 200) -> dict[str, Any]:
    return {
        "attempt_at": at(seconds),
        "received_at": at(seconds + 1),
        "feed": "station_status",
        "http_status": status,
        "outcome": outcome,
        "source_updated_at": at(seconds - 3),
        "station_count": None,
        "valid_count": None,
        "duration_ms": 1000,
        "error_code": None,
    }


def test_status_success_and_contract_acceptance_have_different_denominators() -> None:
    rows = [
        row(0),
        row(10, "invalid"),
        row(20, "http_error", 403),
        row(30, "http_error", 503),
        row(40, "timeout", None),
        row(50, "truncated"),
        row(60, "too_large"),
        row(70, "not_modified", 304),
        row(80, "duplicate"),
        row(90, "correction"),
        row(100, "out_of_order"),
        row(110, "future_timestamp"),
    ]
    found = feed_diagnostics("test", "live", at(0), at(120), rows)
    c = found.totals
    assert c.attempts == 12 and c.http_responses == 11 and c.successful_responses == 9
    assert c.http_failures == 2 and c.transport_failures == 2 and c.contract_failures == 2
    assert c.accepted_publications == 1 and c.not_modified == 1
    assert c.rejected_publications == 4
    assert (
        c.duplicate_publications,
        c.correction_publications,
        c.out_of_order_publications,
        c.future_publications,
    ) == (1, 1, 1, 1)
    assert c.station_records is c.valid_station_records is c.invalid_station_records is None
    assert found.feeds[0].latest_attempt.outcome == "future_timestamp"
    assert [s.outcome for s in found.feeds[0].recent_attempts] == [
        "future_timestamp",
        "out_of_order",
        "correction",
        "duplicate",
        "not_modified",
    ]
    assert sum(s.count for s in found.feeds[0].statuses) == c.http_responses
    assert sum(o.count for o in found.feeds[0].outcomes) == c.attempts


def test_station_record_counts_cover_only_parsed_attempts_and_include_repeats() -> None:
    rows = [
        {**row(0), "station_count": 4, "valid_count": 3},
        {**row(10, "duplicate"), "station_count": 4, "valid_count": 3},
        row(20, "invalid"),
        {**row(30), "feed": "station_information", "station_count": 5, "valid_count": 5},
    ]
    found = feed_diagnostics("test", "recorded_replay", at(0), at(40), rows)
    assert (
        found.totals.station_records,
        found.totals.valid_station_records,
        found.totals.invalid_station_records,
    ) == (13, 11, 2)
    by_feed = {f.feed: f for f in found.feeds}
    assert by_feed["station_status"].counts.station_records == 8
    assert by_feed["station_information"].counts.invalid_station_records == 0


def test_membership_uses_received_at_and_empty_windows_do_not_claim_observations() -> None:
    rows = [row(0), row(10), row(20)]
    found = feed_diagnostics("test", "synthetic", at(11), at(21), rows)
    assert found.totals.attempts == 2
    assert (found.observed_start, found.observed_end) == (at(11), at(21))
    empty = feed_diagnostics("test", "synthetic", at(30), at(30) + timedelta(hours=6), rows)
    assert empty.feeds == [] and empty.totals.attempts == 0
    assert empty.observed_start is empty.observed_end is None
    assert empty.totals.station_records is None
