import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import POLICY_DIR, REPO
from sqlalchemy import select, text

from cycleops import aio
from cycleops.adapters.gbfs import StationInfo, StationStatus
from cycleops.adapters.postgres.connect import create_engine
from cycleops.adapters.postgres.state import StateWriter
from cycleops.adapters.postgres.tables import event_log, station_incidents
from cycleops.adapters.synthetic import SyntheticFeed, collect, load_config
from cycleops.application import replay
from cycleops.application.ingest import Ingestor, create_or_load_stream
from cycleops.contracts import DecisionFacts
from cycleops.domain.engine import new_stream, step
from cycleops.domain.model import Incident, Input, Station, StreamState
from cycleops.policy import Policy, load_policy
from integration.conftest import Database

DEMO = REPO / "fixtures/synthetic/demo.yaml"
T = datetime(2026, 10, 7, 8, 0, 0, 123456, tzinfo=UTC)
STREAM = "synthetic-oslo:0badcafe"


def s(seconds: float) -> datetime:
    return T + timedelta(seconds=seconds)


def status(station_id: str, bikes: int) -> StationStatus:
    return StationStatus(
        station_id=station_id,
        is_installed=True,
        is_renting=True,
        is_returning=False,
        bikes_available=bikes,
        docks_available=None,
        vehicle_types=(("bike", bikes), ("ebike", 0)),
        reported_at=s(-3),
        quality="suspect",
        quality_flags=("vehicle_types_mismatch",),
    )


def info(station_id: str) -> StationInfo:
    return StationInfo(station_id, "Bjørvika", 59.9081234, 10.7512345, None, False, True, ("x",))


FACTS = DecisionFacts(
    bikes_available=0,
    docks_available=None,
    capacity_used=12,
    recovery_min_units=2,
    publications_used=3,
    span_seconds=45,
    source_updated_at=s(1.5),
)


def full_state() -> StreamState:
    """Every field of StreamState, Station and Incident away from its default somewhere,
    with microseconds in the instants."""
    unverifiable = Incident(
        id="a" * 16,
        kind="return_unavailable",
        state="unverifiable",
        first_observed_at=s(0),
        observed_since=s(1),
        policy_version="1.0.0+abcdef01",
        at_opening=True,
        confirmed_at=s(2),
        suspended_from="recovering",
        unverifiable_reason="source_stale",
        reason_text="dados vencidos: fonte vencida",
        facts=FACTS,
    )
    candidate = Incident(
        id="b" * 16,
        kind="pickup_unavailable",
        state="candidate",
        first_observed_at=s(3),
        observed_since=s(3),
        policy_version="1.0.0+abcdef01",
        observations=[s(3), s(18.25)],
    )
    resolved = Incident(
        id="c" * 16,
        kind="pickup_unavailable",
        state="resolved",
        first_observed_at=s(-300),
        observed_since=s(-300),
        policy_version="1.0.0+abcdef01",
        confirmed_at=s(-270),
        close_reason="contrary_observation",
        closed_at=s(-10),
        observations=[s(-80), s(-20)],
        reason_text="resolvido",
        facts=FACTS,
    )
    key = (
        (True, True, False, 0, None, (("bike", 0), ("ebike", 0))),
        "suspect",
        ("vehicle_types_mismatch",),
        ("unverifiable", "pickup_unavailable"),
        "source_stale",
        ("SOURCE_STALE",),
        (("a" * 16, "unverifiable", "recovering"),),
        "0|1791360000|S-0001",
        "1.0.0+abcdef01",
    )
    first = Station(
        station_id="S-0001",
        info=info("S-0001"),
        first_seen_at=s(-600),
        last_seen_in_catalog_at=s(-5),
        missing_catalog_fetches=2,
        tracked_since=s(-590),
        status=status("S-0001", 0),
        status_updated_at=s(-1),
        presence="invalid",
        # Return first: the dict order is part of what must come back.
        incidents={"return_unavailable": unverifiable, "pickup_unavailable": candidate},
        finished=[resolved],
        seen_without={"return_unavailable"},
        reason_text="sem bicicletas, em confirmação",
        facts=FACTS,
        published_key=key,
    )
    second = Station(
        station_id="S-0002",
        info=info("S-0002"),
        first_seen_at=s(-600),
        last_seen_in_catalog_at=s(-5),
    )
    return StreamState(
        system_id="synthetic-oslo",
        mode="synthetic",
        stream_id=STREAM,
        seed=42,
        policy_version="1.0.0+abcdef01",
        head_sequence=7,
        clock_now=s(20.5),
        last_tick_at=s(20),
        service_checked_at=s(20.5),
        in_service=False,
        source_state="stale",
        source_state_since=s(3),
        latched="stale",
        fresh_again_count=1,
        awaiting_since=s(4),
        opened_at=s(5),
        last_source_updated_at=s(-1),
        first_accepted_at=s(-590),
        last_content_hash="d" * 64,
        last_attempt_at=s(19.75),
        last_outcome="timeout",
        breaker_open_until=s(80),
        quarantined_since=s(6),
        quarantine_ended_at=s(4),
        quarantined_feeds={"station_information", "vehicle_types"},
        ingestion_suspended=True,
        catalog_version="e" * 16,
        catalog_fetch_pending=True,
        catalog_fetch_requested_for="f" * 16,
        unknown_ids=frozenset({"X-1", "X-2"}),
        unknown_records={"X-1": status("X-1", 4)},
        removed_ids={"S-0009"},
        stations={"S-0001": first, "S-0002": second},
        last_event_feed_state="fresh",
    )


def assert_every_field_set(instances: list[Any]) -> None:
    """A field added to the model later fails here until the round trip covers it."""
    for field in dataclasses.fields(instances[0]):
        if field.default is not dataclasses.MISSING:
            default = field.default
        elif field.default_factory is not dataclasses.MISSING:
            default = field.default_factory()
        else:
            continue
        assert any(getattr(i, field.name) != default for i in instances), field.name


async def _save(url: str, state: StreamState, writer: StateWriter) -> None:
    engine = create_engine(url)
    async with engine.connect() as conn, conn.begin():
        await writer.save(conn, state, [])
    await engine.dispose()


async def _load(url: str, stream_id: str) -> StreamState:
    engine = create_engine(url)
    async with engine.connect() as conn, conn.begin():
        state = await StateWriter(60).load(conn, stream_id)
    await engine.dispose()
    return state


async def _xmins(url: str) -> dict[str, int]:
    engine = create_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT 'head', xmin::text FROM stream_heads "
                "UNION ALL SELECT 'station:' || station_id, xmin::text FROM stations "
                "UNION ALL SELECT 'current:' || station_id, xmin::text FROM station_current "
                "UNION ALL SELECT 'incident:' || id, xmin::text FROM station_incidents"
            )
        )
        found = {name: int(xmin) for name, xmin in rows}
    await engine.dispose()
    return found


def test_every_field_survives_save_and_load(clean_database: Database) -> None:
    state = full_state()
    stations = list(state.stations.values())
    assert_every_field_set([state])
    assert_every_field_set(stations)
    assert_every_field_set([i for st in stations for i in [*st.incidents.values(), *st.finished]])

    aio.run(_save(clean_database.worker_url, state, StateWriter(60)))
    loaded = aio.run(_load(clean_database.worker_url, STREAM))

    assert loaded == state
    assert list(loaded.stations["S-0001"].incidents) == ["return_unavailable", "pickup_unavailable"]


def test_save_writes_only_what_changed(clean_database: Database) -> None:
    url = clean_database.worker_url
    state = full_state()
    writer = StateWriter(60)
    aio.run(_save(url, state, writer))
    before = aio.run(_xmins(url))

    aio.run(_save(url, state, writer))
    assert aio.run(_xmins(url)) == before

    second = state.stations["S-0002"]
    second.status, second.status_updated_at, second.presence = status("S-0002", 5), s(30), "valid"
    aio.run(_save(url, state, writer))
    after = aio.run(_xmins(url))
    changed = {name for name in after if after[name] != before[name]}
    # The head carries the coverage, which counts S-0002 as valid now.
    assert changed == {"current:S-0002", "head"}


def test_an_incident_finished_and_published_in_one_step_gets_its_final_row(
    clean_database: Database,
) -> None:
    """step() moves it to a final state, the event goes out and clears `finished`: the
    state no longer holds it, but its row must say how it ended."""
    url = clean_database.worker_url
    state = full_state()
    writer = StateWriter(60)
    aio.run(_save(url, state, writer))

    first = state.stations["S-0001"]
    candidate = first.incidents.pop("pickup_unavailable")
    candidate.state, candidate.close_reason, candidate.closed_at = (
        "discarded",
        "contrary_observation",
        s(33),
    )
    aio.run(_save(url, state, writer))

    async def row() -> Any:
        engine = create_engine(url)
        async with engine.connect() as conn:
            found = (
                await conn.execute(
                    select(station_incidents).where(station_incidents.c.id == candidate.id)
                )
            ).one()
        await engine.dispose()
        return found

    found = aio.run(row())
    assert (found.state, found.close_reason, found.closed_at) == (
        "discarded",
        "contrary_observation",
        s(33),
    )
    assert "pickup_unavailable" not in aio.run(_load(url, STREAM)).stations["S-0001"].incidents


def demo(monkeypatch: pytest.MonkeyPatch) -> tuple[Policy, list[Input]]:
    """The demo's inputs in the order replay.run feeds them to step()."""
    config = load_config(DEMO)
    policy = load_policy("synthetic", POLICY_DIR).with_service_hours(config.service_hours)
    inputs: list[Input] = []

    def spy(state: StreamState, entry: Input, p: Policy) -> Any:
        inputs.append(entry)
        return step(state, entry, p)

    monkeypatch.setattr(replay, "step", spy)
    state = new_stream(policy.provider.system_id, "synthetic", STREAM, config.seed)
    attempts = collect(SyntheticFeed(config), policy)
    replay.run(replay.entries(attempts, policy), policy, state, config.end)
    return policy, inputs


def without_committed_at(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k != "committedAt"}


def test_restart_before_every_step_gives_the_same_events(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo saved after every step and reloaded from the database before the next one
    publishes exactly what the uninterrupted in-memory run publishes."""
    policy, inputs = demo(monkeypatch)
    seed = load_config(DEMO).seed

    async def go() -> tuple[str, StreamState, list[dict[str, Any]], list[dict[str, Any]]]:
        engine = create_engine(clean_database.worker_url)
        async with engine.connect() as conn:
            first = await create_or_load_stream(conn, "synthetic-oslo", "synthetic", seed, policy)
            returned = []
            for entry in inputs:
                # A fresh processor per step: nothing survives but the database.
                ingestor = Ingestor(policy, first.stream_id, first.created_at)
                returned += await ingestor.process(conn, entry)
            async with conn.begin():
                final = await StateWriter(60).load(conn, first.stream_id)
                stored = (
                    await conn.execute(select(event_log.c.payload).order_by(event_log.c.sequence))
                ).scalars()
                payloads = list(stored)
        await engine.dispose()
        return first.stream_id, final, [e.model_dump(mode="json") for e in returned], payloads

    stream_id, final, returned, stored = aio.run(go())

    memory = new_stream(policy.provider.system_id, "synthetic", stream_id, seed)
    expected = [e for entry in inputs for e in step(memory, entry, policy)[1]]
    assert len(expected) > 100
    assert any(
        item.state == "resolved"
        for e in expected
        for station in e.stations
        for item in station.decision.incidents
    )
    wanted = [without_committed_at(e.model_dump(mode="json")) for e in expected]
    assert [without_committed_at(p) for p in returned] == wanted
    assert [without_committed_at(p) for p in stored] == wanted
    assert final == memory
