"""Synthetic GBFS 2.3 source with a seed.

It answers like the Oslo feed and goes through the same adapter, collection rules and
engine. Ids are S-0001 onward and names "Estação sintética 001" onward, never Oslo's.
The station counts follow a seeded random walk; scenarios script some stations and inject
faults at fixed offsets from the start.
"""

import json
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from cycleops.collection.client import Attempt
from cycleops.collection.guard import FeedGuard, next_status_at
from cycleops.collection.outcome import classify_attempt
from cycleops.policy import Policy, ServiceHours

# Positions of the real stations (station_information of 2026-10-07, 4 decimals). The
# synthetic stations take them in a seeded order, so they sit where real stations are and
# never in the fjord. Past 267 stations the extra ones get a jitter of about 35 m.
POSITIONS: list[tuple[float, float]] = [
    (lat, lon)
    for lat, lon in json.loads(
        (files("cycleops.adapters") / "oslo_positions.json").read_text(encoding="utf-8")
    )["positions"]
]
EXTRA_JITTER = (0.0003, 0.0006)
GENERATION_DELAY = timedelta(seconds=3)
RESPONSE_TIME = timedelta(milliseconds=300)

ScenarioName = Literal[
    "incident_recovery",
    "relapse",
    "frozen_source",
    "timeout_breaker",
    "contract_quarantine",
    "catalog_removal",
    "opening",
    "closing",
]
FaultKind = Literal["timeout", "contract", "frozen"]


class SyntheticConfig(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, extra="forbid", frozen=True
    )

    seed: int
    stations: Annotated[int, Field(gt=4, le=10_000)]
    cadence_seconds: Annotated[int, Field(gt=0)]
    change_fraction: Annotated[float, Field(ge=0, le=1)]
    start: AwareDatetime
    end: AwareDatetime
    service_hours: ServiceHours | Literal["always"] = "always"
    scenarios: list[ScenarioName] = []


def load_config(path: Path) -> SyntheticConfig:
    with path.open(encoding="utf-8") as f:
        return SyntheticConfig.model_validate(yaml.safe_load(f))


@dataclass(frozen=True)
class Window:
    start: float
    end: float | None
    value: int


class SyntheticFeed:
    def __init__(self, config: SyntheticConfig) -> None:
        self.config = config
        rng = random.Random(config.seed)
        n = config.stations
        self.ids = [f"S-{i + 1:04d}" for i in range(n)]
        self.capacity = [rng.randint(10, 30) for _ in range(n)]
        order = rng.sample(POSITIONS, len(POSITIONS))
        self.position = [
            order[i]
            if i < len(order)
            else (
                order[i % len(order)][0] + rng.uniform(-EXTRA_JITTER[0], EXTRA_JITTER[0]),
                order[i % len(order)][1] + rng.uniform(-EXTRA_JITTER[1], EXTRA_JITTER[1]),
            )
            for i in range(n)
        ]
        initial = [rng.randint(2, cap - 2) for cap in self.capacity]
        self.cadence = timedelta(seconds=config.cadence_seconds)
        epoch = int(config.start.timestamp())
        self.origin = config.start - timedelta(seconds=epoch % config.cadence_seconds)
        self.scripts: dict[int, list[Window]] = {}
        self.faults: list[tuple[str, FaultKind, datetime, datetime]] = []
        self.removed_at: dict[int, datetime] = {}
        for name in config.scenarios:
            SCENARIOS[name](self)
        # Counts per generation, precomputed so the answer never depends on request times.
        generations = math.ceil((config.end - self.origin) / self.cadence) + 1
        self.bikes: list[list[int]] = []
        current = initial
        for k in range(generations):
            if k:
                current = list(current)
                for i in range(n):
                    if rng.random() < config.change_fraction and i not in self.scripts:
                        delta = rng.choice((-2, -1, 1, 2))
                        current[i] = min(self.capacity[i], max(0, current[i] + delta))
            row = list(current)
            offset = (self.origin + k * self.cadence - config.start).total_seconds()
            for i, windows in self.scripts.items():
                row[i] = next(
                    (
                        w.value
                        for w in windows
                        if w.start <= offset and (w.end is None or offset < w.end)
                    ),
                    initial[i],
                )
            self.bikes.append(row)

    # Scenario building blocks, with offsets in seconds from config.start.
    def script(self, station: int, windows: list[tuple[float, float | None, int]]) -> None:
        self.scripts[station] = [Window(*w) for w in windows]

    def fault(self, feed: str, kind: FaultKind, start: float, end: float) -> None:
        begin = self.config.start + timedelta(seconds=start)
        self.faults.append((feed, kind, begin, self.config.start + timedelta(seconds=end)))

    def remove(self, station: int, at: float) -> None:
        self.removed_at[station] = self.config.start + timedelta(seconds=at)

    def half(self, station: int) -> int:
        return self.capacity[station] // 2

    def _active(self, at: datetime) -> list[int]:
        return [
            i
            for i in range(len(self.ids))
            if not (i in self.removed_at and at >= self.removed_at[i])
        ]

    def _fault(self, feed: str, at: datetime) -> FaultKind | None:
        return next((k for f, k, a, b in self.faults if f == feed and a <= at < b), None)

    def _generation(self, at: datetime) -> int:
        k = int((at - GENERATION_DELAY - self.origin) / self.cadence)
        return max(0, min(k, len(self.bikes) - 1))

    def status_body(self, k: int, at: datetime, version: str = "2.3") -> dict[str, Any]:
        updated = self.origin + k * self.cadence
        reported = int((updated + GENERATION_DELAY).timestamp())
        stations = []
        for i in self._active(at):
            bikes = self.bikes[k][i]
            stations.append(
                {
                    "station_id": self.ids[i],
                    "is_installed": True,
                    "is_renting": True,
                    "is_returning": True,
                    "num_bikes_available": bikes,
                    "num_docks_available": self.capacity[i] - bikes,
                    "vehicle_types_available": [{"vehicle_type_id": "bike", "count": bikes}],
                    # Same last_reported for every station, like Oslo.
                    "last_reported": reported,
                }
            )
        return _envelope(updated, self.config.cadence_seconds, version, {"stations": stations})

    def information_body(self, at: datetime) -> dict[str, Any]:
        updated = self.origin + self._generation(at) * self.cadence
        stations = [
            {
                "station_id": self.ids[i],
                "name": f"Estação sintética {i + 1:03d}",
                "lat": round(self.position[i][0], 6),
                "lon": round(self.position[i][1], 6),
                "capacity": self.capacity[i],
                "is_virtual_station": False,
            }
            for i in self._active(at)
        ]
        return _envelope(updated, self.config.cadence_seconds, "2.3", {"stations": stations})

    def attempt(self, feed: str, at: datetime, deadline: float) -> Attempt:
        fault = self._fault(feed, at)
        if fault == "timeout":
            return _attempt(feed, at, timedelta(seconds=deadline), None, None, "timeout")
        if feed == "station_information":
            return _attempt(feed, at, RESPONSE_TIME, 200, self.information_body(at), None)
        k = self._generation(at)
        if fault == "frozen":
            k = self._generation(
                next(a for f, kind, a, _ in self.faults if kind == "frozen" and a <= at)
            )
        version = "3.0" if fault == "contract" else "2.3"
        return _attempt(feed, at, RESPONSE_TIME, 200, self.status_body(k, at, version), None)


def _envelope(updated: datetime, ttl: int, version: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"last_updated": int(updated.timestamp()), "ttl": ttl, "version": version, "data": data}


def _attempt(
    feed: str,
    at: datetime,
    duration: timedelta,
    status: int | None,
    body: dict[str, Any] | None,
    error: str | None,
) -> Attempt:
    return Attempt(
        feed=feed,
        url=f"synthetic:{feed}",
        requested_at=at,
        received_at=at + duration,
        duration_ms=int(duration.total_seconds() * 1000),
        status=status,
        headers={},
        body_bytes=json.dumps(body).encode() if body is not None else None,
        body_json=body,
        error=error,
    )


def collect(feed: SyntheticFeed, policy: Policy) -> list[Attempt]:
    """Polls the synthetic source with the collector's rules (cadence formula, breaker,
    quarantine) on a virtual clock, one request in flight per feed."""
    config = policy.provider.collection
    semantics = policy.provider.station_report_semantics
    deadline = config.request_deadline_seconds
    start, end = feed.config.start, feed.config.end
    due = {"station_information": start, "station_status": start + timedelta(seconds=1)}
    guards = {name: FeedGuard(config, use_breaker=name == "station_status") for name in due}
    last_ok = None
    attempts: list[Attempt] = []
    while True:
        name = min(due, key=lambda f: due[f])
        at = due[name]
        if at >= end:
            return attempts
        attempt = feed.attempt(name, at, deadline)
        outcome = classify_attempt(attempt, semantics)
        guards[name].record(name, outcome, attempt.received_at)
        attempts.append(attempt)
        if name == "station_status":
            if outcome.ok:
                last_ok = outcome.envelope
            cadence = next_status_at(at, last_ok, config)
            # One request in flight: the next one never starts before this one ends.
            cadence = max(cadence, attempt.received_at)
        else:
            cadence = at + timedelta(seconds=config.catalog_refresh_seconds)
        due[name] = guards[name].next_allowed_at(cadence)


def _incident_recovery(feed: SyntheticFeed) -> None:
    # S-0001 empties for 4 min, then gets half its capacity back and keeps it.
    feed.script(0, [(120, 360, 0), (360, None, feed.half(0))])


def _relapse(feed: SyntheticFeed) -> None:
    # S-0002 empties, gets 4 bikes for 30 s (too short to resolve), empties again, recovers.
    feed.script(1, [(480, 720, 0), (720, 750, 4), (750, 960, 0), (960, None, feed.half(1))])


def _frozen_source(feed: SyntheticFeed) -> None:
    feed.fault("station_status", "frozen", 300, 420)


def _timeout_breaker(feed: SyntheticFeed) -> None:
    feed.fault("station_status", "timeout", 300, 390)


def _contract_quarantine(feed: SyntheticFeed) -> None:
    feed.fault("station_status", "contract", 300, 330)


def _catalog_removal(feed: SyntheticFeed) -> None:
    feed.remove(2, 300)


def _opening(feed: SyntheticFeed) -> None:
    # S-0004 is empty through the night and at 05:00.
    feed.script(3, [(0, None, 0)])


def _closing(feed: SyntheticFeed) -> None:
    # S-0005 is empty before 01:00 and stays empty.
    feed.script(4, [(0, None, 0)])


SCENARIOS: dict[ScenarioName, Callable[[SyntheticFeed], None]] = {
    "incident_recovery": _incident_recovery,
    "relapse": _relapse,
    "frozen_source": _frozen_source,
    "timeout_breaker": _timeout_breaker,
    "contract_quarantine": _contract_quarantine,
    "catalog_removal": _catalog_removal,
    "opening": _opening,
    "closing": _closing,
}
