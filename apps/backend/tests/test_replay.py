import gzip
import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from conftest import POLICY_DIR, REPO

from cycleops.adapters.synthetic import (
    ScenarioName,
    SyntheticConfig,
    SyntheticFeed,
    collect,
)
from cycleops.application.replay import (
    Artifact,
    accepted,
    attempt_from_line,
    compile_recording,
    compile_synthetic,
    entries,
    run,
)
from cycleops.cli import compile_replay, generate_artifacts
from cycleops.collection.outcome import classify_attempt
from cycleops.collection.recording import RecordingWriter, read_recording, recording_line
from cycleops.contracts import CatalogVersion, ReplayCatalog, StationBatchEvent
from cycleops.domain.engine import new_stream
from cycleops.domain.model import PublicationFailed
from cycleops.policy import ServiceHours, load_policy

SAMPLE = REPO / "fixtures/recorded/sample-20261007T032452Z.ndjson.gz"
OSLO_HOURS = ServiceHours(
    opens="05:00",
    closes="01:00",
    timezone="Europe/Oslo",
    source="test",
    checked_at=date(2026, 10, 6),
)


def config(
    scenario: ScenarioName,
    start: str = "2026-10-07T10:00:00+02:00",
    minutes: int = 20,
    hours: ServiceHours | None = None,
) -> SyntheticConfig:
    begin = datetime.fromisoformat(start)
    return SyntheticConfig(
        seed=1,
        stations=10,
        cadence_seconds=15,
        change_fraction=0,
        start=begin,
        end=begin + timedelta(minutes=minutes),
        service_hours=hours or "always",
        scenarios=[scenario],
    )


def compiled(cfg: SyntheticConfig) -> Artifact:
    return compile_synthetic(cfg, load_policy("synthetic", POLICY_DIR))


def states(events: Iterable[StationBatchEvent]) -> list[str]:
    """Source states in order, consecutive repeats collapsed."""
    out: list[str] = []
    for e in events:
        if not out or out[-1] != e.feed_health.state:
            out.append(e.feed_health.state)
    return out


def incident_states(events: Iterable[StationBatchEvent], station_id: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for e in events:
        for s in e.stations:
            if s.decision.station_id == station_id:
                out += [
                    (i.id, i.state) for i in s.decision.incidents if i.kind == "pickup_unavailable"
                ]
    return out


def test_incident_and_recovery() -> None:
    artifact = compiled(config("incident_recovery"))
    assert [
        (i.station_id, i.resolved_sequence is not None) for i in artifact.manifest.incidents
    ] == [("S-0001", True)]
    assert accepted(artifact.manifest)


def test_relapse_keeps_the_incident_id() -> None:
    seen = incident_states(compiled(config("relapse")).events, "S-0002")
    assert len({i for i, _ in seen}) == 1
    assert [s for _, s in seen] == [
        "candidate",
        "confirmed",
        "recovering",
        "confirmed",
        "recovering",
        "resolved",
    ]


def test_frozen_source_goes_stale_and_comes_back() -> None:
    artifact = compiled(config("frozen_source"))
    assert states(artifact.events) == ["unknown", "fresh", "late", "stale", "fresh"]


def test_timeouts_open_the_breaker() -> None:
    cfg = config("timeout_breaker")
    artifact = compiled(cfg)
    assert states(artifact.events) == ["unknown", "fresh", "late", "stale", "fresh"]
    stale = next(e for e in artifact.events if e.feed_health.state == "stale")
    assert stale.feed_health.last_outcome == "timeout"
    # Three timeouts open the breaker for 60 s, and nothing is requested meanwhile.
    policy = load_policy("synthetic", POLICY_DIR)
    attempts = [a for a in collect(SyntheticFeed(cfg), policy) if a.feed == "station_status"]
    opened = next(
        e
        for _, e, _ in entries(attempts, policy)
        if isinstance(e, PublicationFailed) and e.breaker_open_until
    )
    assert opened.breaker_open_until == opened.received_at + timedelta(seconds=60)
    after = next(a for a in attempts if a.requested_at > opened.received_at)
    assert after.requested_at >= opened.breaker_open_until


def test_contract_error_quarantines() -> None:
    artifact = compiled(config("contract_quarantine"))
    assert states(artifact.events) == ["unknown", "fresh", "quarantined", "fresh"]
    quarantined = [e for e in artifact.events if e.feed_health.state == "quarantined"]
    # One probe every 300 s: the quarantine lasts at least that long.
    span = quarantined[-1].committed_at - quarantined[0].committed_at
    assert span >= timedelta(seconds=280)


def test_catalog_removal() -> None:
    artifact = compiled(config("catalog_removal", minutes=35))
    removals = [e for e in artifact.events if e.removed_station_ids]
    assert [e.removed_station_ids for e in removals] == [["S-0003"]]
    assert removals[0].cause == "catalog"
    assert len(artifact.manifest.incidents) == 0
    assert {v.catalog_version for v in json_catalog(artifact)} == {
        e.catalog_version for e in artifact.events
    }


def test_opening_at_five() -> None:
    artifact = compiled(config("opening", "2026-10-07T04:50:00+02:00", 25, OSLO_HOURS))
    assert states(artifact.events) == ["closed", "unknown", "fresh"]
    last = [s for e in artifact.events for s in e.stations if s.decision.station_id == "S-0004"][-1]
    assert "EMPTY_AT_OPENING" in last.decision.reason_codes
    assert "CONFIRMED_EMPTY" in last.decision.reason_codes


def test_nightly_closing() -> None:
    artifact = compiled(config("closing", "2026-10-07T00:50:00+02:00", 20, OSLO_HOURS))
    seen = incident_states(artifact.events, "S-0005")
    assert [s for _, s in seen][-2:] == ["confirmed", "closed_without_recovery"]
    closing = next(e for e in artifact.events if e.feed_health.state == "closed")
    assert closing.cause == "clock"
    assert closing.committed_at == datetime.fromisoformat("2026-10-07T01:00:00+02:00")


def json_catalog(artifact: Artifact) -> list[CatalogVersion]:
    return list(ReplayCatalog.model_validate_json(artifact.catalog_json).versions)


def test_demo_artifact_has_a_resolved_incident_and_the_closing() -> None:
    manifest = json.loads(
        (REPO / "apps/web/public/replay/synthetic/manifest.json").read_text("utf-8")
    )
    assert manifest["mode"] == "synthetic" and manifest["seed"] == 42
    assert any(i["resolvedSequence"] is not None for i in manifest["incidents"])
    lines = gzip.decompress(
        (REPO / "apps/web/public/replay/synthetic/events.ndjson.gz").read_bytes()
    )
    events = [StationBatchEvent.model_validate_json(line) for line in lines.splitlines()]
    assert len(events) == manifest["eventCount"]
    closing = next(e for e in events if e.feed_health.state == "closed")
    assert closing.committed_at == datetime.fromisoformat("2026-10-06T01:00:00+02:00")
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))


def test_recording_compiles_the_same_twice() -> None:
    policy = load_policy("oslo", POLICY_DIR)
    first, second = compile_recording(SAMPLE, policy), compile_recording(SAMPLE, policy)
    assert first.manifest == second.manifest
    assert first.events_ndjson == second.events_ndjson


def test_short_sample_does_not_pass_acceptance() -> None:
    artifact = compile_recording(SAMPLE, load_policy("oslo", POLICY_DIR))
    assert artifact.manifest.incidents and not accepted(artifact.manifest)
    assert artifact.manifest.stream_id.startswith("oslobysykkel:")


def test_recording_with_a_resolution_passes_acceptance(tmp_path: Path) -> None:
    cfg = config("incident_recovery")
    policy = load_policy("synthetic", POLICY_DIR)
    path = tmp_path / "synthetic-recording.ndjson.gz"
    with RecordingWriter(path, "test-cycleops", 12) as writer:
        for attempt in collect(SyntheticFeed(cfg), policy):
            writer.write(recording_line(attempt, classify_attempt(attempt, "feed_generation")))
    artifact = compile_recording(path, load_policy("oslo", POLICY_DIR))
    assert accepted(artifact.manifest)
    incident = artifact.manifest.incidents[0]
    assert incident.confirmed_offset_seconds < (incident.resolved_offset_seconds or 0)


def test_compile_replay_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)
    assert compile_replay.main(["--recording", str(SAMPLE), "--out", str(tmp_path / "r")]) == 3
    assert not (tmp_path / "r").exists()


def test_generated_files_are_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)
    generate_artifacts.build(tmp_path)
    assert generate_artifacts.compare(tmp_path, REPO) == []


def test_generated_text_has_no_carriage_return() -> None:
    for zone in generate_artifacts.ZONES:
        files = [p for p in (REPO / zone).rglob("*") if p.is_file()]
        assert files
        for path in files:
            data = path.read_bytes()
            assert b"\r" not in (gzip.decompress(data) if path.suffix == ".gz" else data), path


def test_sort_key_fixture_is_in_ascii_order() -> None:
    fixture = json.loads((REPO / "packages/contracts/fixtures/sort-keys.json").read_text("utf-8"))
    assert fixture["sorted"] == sorted(fixture["input"]) and len(fixture["input"]) > 20
    assert fixture["sorted"].index("0|1791360000|A-1") < fixture["sorted"].index("0|1791360000|A_1")


def test_quarantine_of_another_feed_ends_with_a_good_probe() -> None:
    policy = load_policy("oslo", POLICY_DIR)
    attempts = [attempt_from_line(line) for line in read_recording(SAMPLE)]
    good = next(a for a in attempts if a.feed == "system_information")
    start = attempts[0].received_at
    bad = replace(good, status=403, body_json=None, received_at=start + timedelta(seconds=60))
    probe = replace(good, received_at=start + timedelta(seconds=380))
    system_id = policy.provider.system_id
    state = new_stream(system_id, "recorded_replay", f"{system_id}:test")
    events, _ = run(entries([*attempts, bad, probe], policy), policy, state)
    flagged = [e.feed_health.quarantined_feeds for e in events]
    assert ["system_information"] in flagged
    assert flagged[-1] == []


def test_synthetic_stream_id_ignores_how_instants_are_written() -> None:
    cfg = config("incident_recovery", minutes=5)
    utc = cfg.model_copy(
        update={"start": cfg.start.astimezone(UTC), "end": cfg.end.astimezone(UTC)}
    )
    assert compiled(cfg).manifest.stream_id == compiled(utc).manifest.stream_id


def test_compile_replay_writes_a_synthetic_scenario_without_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO)
    cfg_path = tmp_path / "cfg.yaml"
    cfg = config("frozen_source").model_dump(by_alias=True, mode="json")
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    assert compile_replay.main(["--synthetic", str(cfg_path), "--out", str(tmp_path / "s")]) == 0
    assert (tmp_path / "s/manifest.json").exists()


def test_synthetic_stations_sit_on_real_positions() -> None:
    info = next(line for line in read_recording(SAMPLE) if line["feed"] == "station_information")
    real = [(s["lat"], s["lon"]) for s in info["body"]["data"]["stations"]]
    cfg = config("incident_recovery").model_copy(update={"stations": 267})
    for lat, lon in SyntheticFeed(cfg).position:
        # 0.0001 degree is about 11 m: rounding only.
        assert min(abs(lat - a) + abs(lon - b) for a, b in real) < 0.0001


def test_schemas_require_the_version_fields() -> None:
    schemas = REPO / "packages/contracts/schemas"
    event = json.loads((schemas / "station-batch-event.schema.json").read_text("utf-8"))
    assert {"type", "schemaVersion", "eventId", "feedHealth"} <= set(event["required"])
    snapshot = json.loads((schemas / "snapshot.schema.json").read_text("utf-8"))
    assert {"schemaVersion", "eventSchemaVersion", "cursor"} <= set(snapshot["required"])
    manifest = json.loads((schemas / "replay-manifest.schema.json").read_text("utf-8"))
    assert {"schemaVersion", "eventSchemaVersion"} <= set(manifest["required"])
