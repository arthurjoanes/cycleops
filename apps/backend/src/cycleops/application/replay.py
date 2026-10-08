"""compile-replay: the adapter and the engine on a virtual clock, over a recording or the
synthetic source, written as the artifact the browser plays.

Output: events.ndjson.gz, catalog.json and manifest.json. Same input, same content.
"""

import gzip
import hashlib
import io
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cycleops.adapters.gbfs import ADAPTER_VERSION, GBFS_VERSION, InfoPublication, StatusPublication
from cycleops.adapters.synthetic import SyntheticConfig, SyntheticFeed, collect
from cycleops.canonical import HASH_VERSION, sha256_hex
from cycleops.collection.client import FEEDS, Attempt
from cycleops.collection.guard import FeedGuard
from cycleops.collection.outcome import TRANSPORT_ERRORS, classify_attempt
from cycleops.collection.recording import parse_iso, read_recording
from cycleops.contracts import (
    CatalogStation,
    CatalogVersion,
    ManifestIncident,
    ReplayCatalog,
    ReplayManifest,
    StationBatchEvent,
)
from cycleops.domain.engine import catalog_stations, new_stream, step
from cycleops.domain.model import (
    CatalogReceived,
    ClockTick,
    Input,
    PublicationFailed,
    PublicationReceived,
    StreamState,
    instant_of,
)
from cycleops.policy import Policy

# ponytail: over the cap compile-replay stops; cutting the window around the manifest
# offsets comes when a real recording needs it.
MAX_EVENTS_GZ_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Artifact:
    events: list[StationBatchEvent]
    events_ndjson: bytes
    catalog_json: bytes
    manifest: ReplayManifest


def attempt_from_line(line: dict[str, Any]) -> Attempt:
    error = line["error"] if line["error"] in TRANSPORT_ERRORS | {"too_large"} else None
    return Attempt(
        feed=line["feed"],
        url=line["url"],
        requested_at=parse_iso(line["requestedAt"]),
        received_at=parse_iso(line["receivedAt"]),
        duration_ms=line["durationMs"],
        status=line["status"],
        headers=line["headers"],
        body_bytes=None,
        body_json=line["body"],
        error=error,
    )


def entries(attempts: Iterable[Attempt], policy: Policy) -> Iterator[Input]:
    """Attempts in receivedAt order, as engine inputs. Breaker and quarantine are the
    collection module's, run here over the attempts."""
    collection = policy.provider.collection
    guards = {feed: FeedGuard(collection, use_breaker=feed == "station_status") for feed in FEEDS}
    skew = timedelta(seconds=policy.priority.clock_skew_tolerance_seconds)
    semantics = policy.provider.station_report_semantics
    for attempt in sorted(attempts, key=lambda a: a.received_at):
        outcome = classify_attempt(attempt, semantics, attempt.received_at + skew)
        guard = guards[attempt.feed]
        guard.record(attempt.feed, outcome, attempt.received_at)
        publication = outcome.publication
        if outcome.ok and isinstance(publication, StatusPublication):
            yield PublicationReceived(attempt.received_at, publication)
        elif outcome.ok and isinstance(publication, InfoPublication):
            yield CatalogReceived(attempt.received_at, publication)
        elif outcome.error_class is not None:
            yield PublicationFailed(
                attempt.received_at,
                attempt.feed,
                outcome.code,
                outcome.error_class,
                guard.breaker_open_until,
                guard.quarantined_since,
            )


def run(
    inputs: Iterable[Input], policy: Policy, state: StreamState, end: datetime | None = None
) -> tuple[list[StationBatchEvent], dict[str, list[CatalogStation]]]:
    """Runs the engine with ticks on the epoch grid between inputs. On a tie the input goes
    first. Returns the events and every catalog version seen."""
    tick = timedelta(seconds=policy.priority.tick_interval_seconds)
    events: list[StationBatchEvent] = []
    catalogs: dict[str, list[CatalogStation]] = {}
    next_tick: datetime | None = None

    def apply(entry: Input) -> None:
        events.extend(step(state, entry, policy)[1])
        if state.catalog_version not in catalogs:
            catalogs[state.catalog_version] = catalog_stations(state)

    for entry in inputs:
        at = instant_of(entry)
        if next_tick is None:
            next_tick = _grid_ceil(at, tick)
        while next_tick < at:
            apply(ClockTick(next_tick))
            next_tick += tick
        apply(entry)
    while end is not None and next_tick is not None and next_tick <= end:
        apply(ClockTick(next_tick))
        next_tick += tick
    return events, catalogs


def compile_recording(path: Path, policy: Policy) -> Artifact:
    recording_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    lines = list(read_recording(path))
    attempts = [attempt_from_line(line) for line in lines]
    start = min(a.requested_at for a in attempts)
    end = max(a.received_at for a in attempts)
    system_id = policy.provider.system_id
    state = new_stream(system_id, "recorded_replay", f"{system_id}:{recording_hash[:8]}")
    events, catalogs = run(entries(attempts, policy), policy, state, end)
    return _artifact(events, catalogs, policy, "recorded_replay", recording_hash, start, end)


def compile_synthetic(config: SyntheticConfig, policy: Policy) -> Artifact:
    policy = policy.with_service_hours(config.service_hours)
    # The demo's "recording hash" is the canonical hash of its configuration.
    config_hash = sha256_hex(config.model_dump(by_alias=True, mode="json"))
    system_id = policy.provider.system_id
    state = new_stream(system_id, "synthetic", f"{system_id}:{config_hash[:8]}", config.seed)
    attempts = collect(SyntheticFeed(config), policy)
    events, catalogs = run(entries(attempts, policy), policy, state, config.end)
    parameters = config.model_dump(by_alias=True, mode="json")
    return _artifact(
        events,
        catalogs,
        policy,
        "synthetic",
        config_hash,
        config.start,
        config.end,
        config.seed,
        parameters,
    )


def _artifact(
    events: list[StationBatchEvent],
    catalogs: dict[str, list[CatalogStation]],
    policy: Policy,
    mode: str,
    recording_hash: str,
    start: datetime,
    end: datetime,
    seed: int | None = None,
    parameters: dict[str, Any] | None = None,
) -> Artifact:
    if not events:
        raise ValueError("no events: the input has no accepted publication")
    ndjson = b"".join(e.model_dump_json().encode() + b"\n" for e in events)
    used = list(dict.fromkeys(e.catalog_version for e in events))
    catalog = ReplayCatalog(
        system_id=policy.provider.system_id,
        versions=[CatalogVersion(catalog_version=v, stations=catalogs[v]) for v in used],
    )
    catalog_json = (
        json.dumps(catalog.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, indent=2)
        + "\n"
    ).encode()
    tz = ZoneInfo(policy.provider.timezone)
    manifest = ReplayManifest(
        mode=mode,
        stream_id=events[0].stream_id,
        system_id=policy.provider.system_id,
        timezone=policy.provider.timezone,
        period_start=start,
        period_end=end,
        period_start_local=start.astimezone(tz).isoformat(timespec="seconds"),
        period_end_local=end.astimezone(tz).isoformat(timespec="seconds"),
        recording_hash=recording_hash,
        events_sha256=hashlib.sha256(ndjson).hexdigest(),
        catalog_sha256=hashlib.sha256(catalog_json).hexdigest(),
        gbfs_version=GBFS_VERSION,
        adapter_version=ADAPTER_VERSION,
        engine_version=version("cycleops"),
        policy_version=policy.version,
        hash_version=HASH_VERSION,
        seed=seed,
        parameters=parameters,
        incidents=_incidents(events),
        event_count=len(events),
    )
    return Artifact(events, ndjson, catalog_json, manifest)


def _incidents(events: list[StationBatchEvent]) -> list[ManifestIncident]:
    """Incidents that reached confirmed, with the event of the first confirmation and of
    the resolution. Offsets are virtual committedAt minus the first event's."""
    origin = events[0].committed_at
    confirmed: dict[str, tuple[str, str, StationBatchEvent]] = {}
    resolved: dict[str, StationBatchEvent] = {}
    for event in events:
        for station in event.stations:
            for item in station.decision.incidents:
                if item.state == "confirmed" and item.id not in confirmed:
                    confirmed[item.id] = (item.kind, station.decision.station_id, event)
                elif item.state == "resolved" and item.id in confirmed:
                    resolved[item.id] = event
    result = []
    for incident_id, (kind, station_id, event) in confirmed.items():
        done = resolved.get(incident_id)
        result.append(
            ManifestIncident(
                incident_id=incident_id,
                kind=kind,
                station_id=station_id,
                confirmed_sequence=event.sequence,
                resolved_sequence=done.sequence if done else None,
                confirmed_offset_seconds=_offset(event.committed_at, origin),
                resolved_offset_seconds=_offset(done.committed_at, origin) if done else None,
            )
        )
    return result


def accepted(manifest: ReplayManifest) -> bool:
    """Acceptance of a recording: at least one incident goes from confirmed to resolved."""
    return any(i.resolved_sequence is not None for i in manifest.incidents)


def write(artifact: Artifact, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    # filename='' and mtime=0: no name or time in the gzip header.
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as gz:
        gz.write(artifact.events_ndjson)
    if buffer.tell() > MAX_EVENTS_GZ_BYTES:
        raise ValueError(f"events.ndjson.gz is {buffer.tell()} bytes, over the 2 MB cap")
    (out / "events.ndjson.gz").write_bytes(buffer.getvalue())
    (out / "catalog.json").write_bytes(artifact.catalog_json)
    manifest = artifact.manifest.model_dump(mode="json")
    (out / "manifest.json").write_bytes(
        (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
    )


def _grid_ceil(at: datetime, tick: timedelta) -> datetime:
    """First multiple of the tick, counted from the epoch, at or after `at`."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    micro = timedelta(microseconds=1)
    span, size = (at - epoch) // micro, tick // micro
    return epoch + -(-span // size) * size * micro


def _offset(at: datetime, origin: datetime) -> float:
    return round((at - origin).total_seconds(), 3)
