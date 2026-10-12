"""Attempt diagnostics shared by PostgreSQL reads and the offline replay compiler."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from cycleops.collection.outcome import TRANSPORT_ERRORS
from cycleops.contracts import (
    SCHEMA_VERSION,
    DataMode,
    FeedAttemptSample,
    FeedDiagnosticCounts,
    FeedDiagnostics,
    FeedDiagnosticSummary,
    FeedOutcomeCount,
    FeedStatusCount,
)

RECENT_ATTEMPTS = 5
REJECTED_PUBLICATIONS = frozenset({"duplicate", "correction", "out_of_order", "future_timestamp"})


def diagnostic_counts(rows: Sequence[Mapping[str, Any]]) -> FeedDiagnosticCounts:
    outcomes = Counter(str(r["outcome"]) for r in rows)
    parsed = [r for r in rows if r["station_count"] is not None and r["valid_count"] is not None]
    records = sum(int(r["station_count"]) for r in parsed) if parsed else None
    valid = sum(int(r["valid_count"]) for r in parsed) if parsed else None
    return FeedDiagnosticCounts(
        attempts=len(rows),
        http_responses=sum(r["http_status"] is not None for r in rows),
        successful_responses=sum(r["http_status"] in (200, 304) for r in rows),
        http_failures=outcomes["http_error"],
        transport_failures=sum(outcomes[code] for code in TRANSPORT_ERRORS),
        contract_failures=outcomes["invalid"] + outcomes["too_large"],
        accepted_publications=outcomes["ok"],
        rejected_publications=sum(outcomes[code] for code in REJECTED_PUBLICATIONS),
        duplicate_publications=outcomes["duplicate"],
        correction_publications=outcomes["correction"],
        out_of_order_publications=outcomes["out_of_order"],
        future_publications=outcomes["future_timestamp"],
        not_modified=outcomes["not_modified"],
        station_records=records,
        valid_station_records=valid,
        invalid_station_records=records - valid
        if records is not None and valid is not None
        else None,
    )


def attempt_sample(row: Mapping[str, Any]) -> FeedAttemptSample:
    return FeedAttemptSample(
        attempt_at=row["attempt_at"],
        received_at=row["received_at"],
        status=row["http_status"],
        outcome=row["outcome"],
        source_updated_at=row["source_updated_at"],
        error_code=row["error_code"],
        station_records=row["station_count"],
        valid_station_records=row["valid_count"],
        duration_ms=row["duration_ms"],
    )


def feed_diagnostics(
    stream_id: str,
    mode: DataMode,
    start: datetime,
    end: datetime,
    rows: Sequence[Mapping[str, Any]],
) -> FeedDiagnostics:
    """Window membership is receivedAt in [start, end]. Empty windows remain empty:
    neither configured cadence nor HTTP 200 supplies a missing observation."""
    selected = sorted(
        (r for r in rows if start <= r["received_at"] <= end),
        key=lambda r: (r["received_at"], r["attempt_at"], r["feed"]),
    )
    per_feed: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in selected:
        per_feed[row["feed"]].append(row)
    feeds = []
    for feed, attempts in sorted(per_feed.items()):
        outcomes = Counter(str(r["outcome"]) for r in attempts)
        statuses = Counter(int(r["http_status"]) for r in attempts if r["http_status"] is not None)
        feeds.append(
            FeedDiagnosticSummary(
                feed=feed,
                counts=diagnostic_counts(attempts),
                outcomes=[
                    FeedOutcomeCount(outcome=o, count=n) for o, n in sorted(outcomes.items())
                ],
                statuses=[FeedStatusCount(status=s, count=n) for s, n in sorted(statuses.items())],
                latest_attempt=attempt_sample(attempts[-1]),
                recent_attempts=[attempt_sample(r) for r in attempts[-RECENT_ATTEMPTS:][::-1]],
            )
        )
    return FeedDiagnostics(
        schema_version=SCHEMA_VERSION,
        stream_id=stream_id,
        mode=mode,
        window_start=start,
        window_end=end,
        observed_start=selected[0]["received_at"] if selected else None,
        observed_end=selected[-1]["received_at"] if selected else None,
        totals=diagnostic_counts(selected),
        feeds=feeds,
    )
