"""Hand-built attempts for the integration tests, and a worker-side stream to feed them to.

Two catalog stations ("100", "200", capacity 10), publications every 15 s from T0,
synthetic policy (always open).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import POLICY_DIR, T0, info_body, station, status_body
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops.adapters.gbfs import InfoPublication, StatusPublication
from cycleops.adapters.postgres.connect import create_engine
from cycleops.application.ingest import Ingestor, create_or_load_stream
from cycleops.collection.client import Attempt
from cycleops.collection.outcome import Outcome, classify_attempt
from cycleops.domain.model import CatalogReceived, ClockTick, PublicationReceived
from cycleops.policy import Policy, load_policy

POLICY: Policy = load_policy("synthetic", POLICY_DIR)
SYSTEM = POLICY.provider.system_id
SEED = 1


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(T0, UTC) + timedelta(seconds=seconds)


def attempt(feed: str, seconds: float, body: dict[str, Any] | None) -> Attempt:
    raw = json.dumps(body).encode() if body is not None else None
    return Attempt(
        feed=feed,
        url=f"synthetic:{feed}",
        requested_at=at(seconds),
        received_at=at(seconds) + timedelta(milliseconds=300),
        duration_ms=300,
        status=200 if body is not None else None,
        headers={},
        body_bytes=raw,
        body_json=body,
        error=None if body is not None else "timeout",
    )


def classify(a: Attempt) -> Outcome:
    skew = timedelta(seconds=POLICY.priority.clock_skew_tolerance_seconds)
    return classify_attempt(a, "feed_generation", a.received_at + skew)


def catalog(seconds: float = 0) -> tuple[CatalogReceived, Attempt, Outcome]:
    a = attempt("station_information", seconds, info_body([info("100"), info("200")]))
    outcome = classify(a)
    assert isinstance(outcome.publication, InfoPublication)
    return CatalogReceived(a.received_at, outcome.publication), a, outcome


def publication(
    k: int, bikes: dict[str, int], seconds: float | None = None
) -> tuple[PublicationReceived, Attempt, Outcome]:
    """Generation k: last_updated T0 + 15 k, requested 3 s later unless `seconds` says."""
    updated = T0 + 15 * k
    records = [
        station(
            station_id=sid,
            num_bikes_available=n,
            last_reported=updated,
            vehicle_types_available=None,
        )
        for sid, n in bikes.items()
    ]
    a = attempt(
        "station_status",
        15 * k + 3 if seconds is None else seconds,
        status_body(records, last_updated=updated),
    )
    outcome = classify(a)
    assert isinstance(outcome.publication, StatusPublication)
    return PublicationReceived(a.received_at, outcome.publication), a, outcome


def tick(seconds: float, suspended: bool = False) -> ClockTick:
    return ClockTick(at(seconds), ingestion_suspended=suspended)


def info(station_id: str) -> dict[str, Any]:
    return {
        "station_id": station_id,
        "name": f"Estação {station_id}",
        "lat": 59.9,
        "lon": 10.75,
        "capacity": 10,
        "is_virtual_station": False,
    }


@asynccontextmanager
async def worker(
    url: str, policy: Policy = POLICY
) -> AsyncIterator[tuple[AsyncConnection, Ingestor]]:
    """A connection (no transaction open) and the stream's processor on it."""
    engine = create_engine(url)
    try:
        async with engine.connect() as conn:
            yield conn, await create_or_load_stream(conn, SYSTEM, "synthetic", SEED, policy)
    finally:
        await engine.dispose()
