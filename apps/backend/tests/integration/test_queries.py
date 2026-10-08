from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops import aio
from cycleops.adapters.postgres import queries
from cycleops.adapters.postgres.connect import create_engine
from cycleops.adapters.postgres.tables import event_log, station_current
from cycleops.contracts import FeedHealth, Interval, Snapshot
from cycleops.policy import ServiceHours
from integration.conftest import Database
from integration.feed import POLICY, at, catalog, publication, tick, worker


async def read(url: str, query: Any, *args: Any) -> Any:
    engine = create_engine(url)
    async with engine.connect() as conn:
        result = await query(conn, *args)
    await engine.dispose()
    return result


def test_snapshot_is_one_instant_even_with_a_commit_between_its_reads(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def go() -> tuple[Snapshot | None, Snapshot | None]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 5, "200": 5}))

            engine = create_engine(clean_database.api_url)
            async with engine.connect() as reader:
                real = AsyncConnection.execute
                calls = 0

                async def spy(self: AsyncConnection, *args: Any, **kwargs: Any) -> Any:
                    nonlocal calls
                    result = await real(self, *args, **kwargs)
                    if self is reader:
                        calls += 1
                        if calls == 2:
                            # The head is read (after SET TRANSACTION): the worker commits
                            # sequence 3 before the stations are read.
                            await ingestor.process(conn, *publication(2, {"100": 0, "200": 5}))
                    return result

                monkeypatch.setattr(AsyncConnection, "execute", spy)
                during = await queries.snapshot(reader)
                monkeypatch.setattr(AsyncConnection, "execute", real)
                after = await queries.snapshot(reader)
            await engine.dispose()
        return during, after

    during, after = aio.run(go())
    assert during is not None and after is not None
    stream = during.stream_id
    bikes = {s.availability.station_id: s.availability.bikes_available for s in during.stations}
    assert (during.cursor, bikes) == (f"{stream}@2", {"100": 5, "200": 5})
    bikes = {s.availability.station_id: s.availability.bikes_available for s in after.stations}
    assert (after.cursor, bikes) == (f"{stream}@3", {"100": 0, "200": 5})


def test_snapshot_contents(clean_database: Database) -> None:
    async def go() -> tuple[Snapshot | None, list[Any]]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            assert await queries.snapshot(conn) is not None
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 3, "200": 0}))
            # 200 gets bikes: its candidate is discarded and goes out once, in this event.
            await ingestor.process(conn, *publication(2, {"100": 0, "200": 3}))
            stored = (
                await conn.execute(
                    select(station_current.c.station_id, station_current.c.decision).order_by(
                        station_current.c.station_id
                    )
                )
            ).all()
            await conn.commit()
            return await queries.snapshot(conn), list(stored)

    snap, stored = aio.run(go())
    assert snap is not None
    assert (snap.mode, snap.system_id, snap.seed) == ("synthetic", "synthetic-oslo", 1)
    assert snap.cursor == f"{snap.stream_id}@3" and snap.catalog_version
    assert [c.station_id for c in snap.catalog] == ["100", "200"]
    assert snap.catalog[0].name == "Estação 100" and snap.catalog[0].capacity == 10
    # The worker stored fresh; T0 is in the past, so the API's stale rule turns it stale.
    assert snap.feed_health.state == "stale" and snap.feed_health.coverage.total == 2
    incidents = {s.decision.station_id: s.decision.incidents for s in snap.stations}
    assert [(i.kind, i.state) for i in incidents["100"]] == [("pickup_unavailable", "candidate")]
    assert incidents["200"] == []
    assert [i["state"] for i in dict(stored)["200"]["incidents"]] == ["discarded"]


def test_api_stale_rule() -> None:
    now = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
    minute = timedelta(seconds=60)
    since = now - timedelta(hours=1)
    recent, old = now - timedelta(seconds=30), now - timedelta(seconds=61)
    older = now - timedelta(seconds=90)
    rule = queries.api_source_state
    assert rule("fresh", since, recent, recent, 60, now) == ("fresh", since)
    assert rule("late", since, recent, recent, 60, now) == ("late", since)
    # The worker stopped ticking, or the last publication is too old: stale since the
    # older of the two crossed the limit.
    assert rule("fresh", since, recent, old, 60, now) == ("stale", old + minute)
    assert rule("late", since, old, recent, 60, now) == ("stale", old + minute)
    assert rule("fresh", since, older, old, 60, now) == ("stale", older + minute)
    assert rule("fresh", since, None, recent, 60, now) == ("stale", None)
    for stored in ("unknown", "stale", "quarantined", "closed"):
        assert rule(stored, since, old, old, 60, now) == (stored, since)


def test_head_feed_health_and_snapshot_apply_the_stale_rule(clean_database: Database) -> None:
    async def go() -> list[tuple[str, datetime | None, datetime | None]]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            [event] = await ingestor.process(conn, *publication(1, {"100": 3, "200": 3}))
            # The test's instants are from T0 (2026-10-07); only the stored state counts
            # here, so bring last_updated and the tick to now.
            await conn.execute(
                text(
                    "UPDATE stream_heads SET last_source_updated_at = now(), worker_seen_at = now()"
                )
            )
            await conn.commit()
            states: list[tuple[str, datetime | None, datetime | None]] = []
            for seen in ("now()", "now() - interval '61 seconds'"):
                at = (
                    await conn.execute(
                        text(f"UPDATE stream_heads SET worker_seen_at = {seen} RETURNING {seen}")
                    )
                ).scalar_one()
                await conn.commit()
                head = await queries.head(conn)
                health = await queries.feed_health(conn)
                snap = await queries.snapshot(conn)
                assert head is not None and health is not None and snap is not None
                assert head.feed_health == health == snap.feed_health
                assert await queries.active_stream(conn) == head.stream_id == snap.stream_id
                states.append((health.state, health.state_since, at))
            assert event.feed_health.state_since is not None
            assert states[0][1] == event.feed_health.state_since
            return states

    [running, stopped] = aio.run(go())
    assert running[0] == "fresh"
    # Stale since the worker's last tick crossed sourceStaleAfterSeconds.
    assert stopped[0] == "stale" and stopped[2] is not None
    assert stopped[1] == stopped[2] + timedelta(seconds=60)


def test_a_snapshot_during_stale_has_its_start(clean_database: Database) -> None:
    async def go() -> tuple[Any, Snapshot | None]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 3, "200": 0}))
            [event] = await ingestor.process(conn, tick(80))
            await ingestor.process(conn, tick(85))
            return event, await queries.snapshot(conn)

    event, snap = aio.run(go())
    assert snap is not None
    assert event.feed_health.state == snap.feed_health.state == "stale"
    # Stale by age starts when it crossed the limit (last_updated T0 + 15, plus 60 s), not
    # at the tick that saw it.
    assert snap.feed_health.state_since == event.feed_health.state_since == at(15 + 60)


def test_the_restart_keeps_the_stale_start_the_api_showed(clean_database: Database) -> None:
    async def go() -> tuple[FeedHealth | None, FeedHealth | None]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 3, "200": 3}))
            await ingestor.process(conn, tick(20))
            # last_updated T0 + 30, after the last tick; then the worker stops. The test's
            # instants are from T0 (2026-10-07), long past for the API's now().
            await ingestor.process(conn, *publication(2, {"100": 3, "200": 3}))
            during = await queries.feed_health(conn)
            await ingestor.process(conn, tick(1800))
            return during, await queries.feed_health(conn)

    during, after = aio.run(go())
    assert during is not None and after is not None
    # Stale from the tick at 20 plus 60 s, during the outage and after the restart.
    assert (during.state, during.state_since) == (after.state, after.state_since)
    assert (after.state, after.state_since) == ("stale", at(80))


def test_history_points_incidents_and_gaps(clean_database: Database) -> None:
    async def go() -> tuple[Any, list[datetime], Any, Any]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            committed: list[Any] = []
            entries: list[tuple[Any, ...]] = [
                catalog(),
                publication(1, {"100": 3, "200": 0}),
                publication(2, {"100": 3, "200": 0}),
                publication(3, {"100": 3, "200": 0}),
                (tick(110),),
                publication(8, {"100": 3, "200": 0}),
                publication(9, {"100": 3, "200": 0}),
            ]
            for entry in entries:
                events = await ingestor.process(conn, *entry)
                committed.append(events[0].committed_at if events else None)
            now = (await conn.execute(text("SELECT now()"))).scalar_one()
            await conn.commit()
            window = (now - timedelta(hours=1), now + timedelta(minutes=1))
            found = await queries.history(conn, ingestor.stream_id, "200", *window)
            unknown = await queries.history(conn, ingestor.stream_id, "999", *window)
            inside = committed[4] + timedelta(milliseconds=1)
            narrow = await queries.history(conn, ingestor.stream_id, "200", inside, now)
            return found, committed, unknown, narrow

    found, committed, unknown, narrow = aio.run(go())
    assert unknown is None and found is not None
    catalog_at, first, _, confirmed, stale, still_stale, fresh = committed
    # Unknown source before the first publication, then stale from the tick until the
    # hysteresis ends at the second publication after it.
    assert found.gaps == [
        Interval(start=catalog_at, end=first),
        Interval(start=stale, end=fresh),
    ]
    assert found.closed == []
    assert [(p.bikes_available, p.primary_condition) for p in found.points] == [
        (0, "pickup_unavailable"),
        (0, "pickup_unavailable"),
        (0, "unverifiable"),
        (0, "pickup_unavailable"),
    ]
    # Each point at its commit, where the gaps are cut. The stale one keeps the
    # sourceUpdatedAt of the point before it.
    assert [p.at for p in found.points] == [first, confirmed, stale, fresh]
    assert found.points[2].source_updated_at == found.points[1].source_updated_at
    assert [(i.kind, i.state) for i in found.incidents] == [("pickup_unavailable", "confirmed")]
    assert confirmed is not None and still_stale is not None
    # A window that opens inside the gap starts with the change in effect then.
    assert narrow is not None
    assert narrow.gaps == [Interval(start=stale + timedelta(milliseconds=1), end=fresh)]
    assert narrow.points[0].primary_condition == "unverifiable"


def test_history_closed_interval(clean_database: Database) -> None:
    hours = ServiceHours(
        opens="05:00",
        closes="10:01",
        timezone="Europe/Oslo",
        source="test",
        checked_at=date(2026, 10, 7),
    )

    async def go() -> tuple[Any, list[datetime]]:
        async with worker(clean_database.worker_url, POLICY.with_service_hours(hours)) as (
            conn,
            ingestor,
        ):
            committed = []
            # T0 is 10:00 in Oslo; the tick at 10:01:05 closes the system.
            entries: list[tuple[Any, ...]] = [
                catalog(),
                publication(1, {"100": 3, "200": 0}),
                (tick(65),),
            ]
            for entry in entries:
                [event] = await ingestor.process(conn, *entry)
                committed.append(event.committed_at)
            now = (await conn.execute(text("SELECT now()"))).scalar_one()
            await conn.commit()
            found = await queries.history(
                conn, ingestor.stream_id, "100", now - timedelta(hours=1), now
            )
            return found, committed

    found, committed = aio.run(go())
    assert found is not None
    assert found.closed == [Interval(start=committed[2], end=None)]
    assert found.gaps == [Interval(start=committed[0], end=committed[1])]


def test_events_after(clean_database: Database) -> None:
    async def go() -> tuple[str, list[Any], list[Any], list[Any], list[Any]]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 3, "200": 0}))
            await ingestor.process(conn, *publication(2, {"100": 0, "200": 0}))
            stored = (
                await conn.execute(select(event_log.c.payload).order_by(event_log.c.sequence))
            ).scalars()
            stored_list = list(stored)
            await conn.commit()
        api = clean_database.api_url
        stream = ingestor.stream_id
        return (
            stream,
            stored_list,
            await read(api, queries.events_after, stream, 0),
            await read(api, queries.events_after, stream, 1, 1),
            await read(api, queries.events_after, stream, 3),
        )

    stream, stored, everything, one, none = aio.run(go())
    assert everything == stored and [e["sequence"] for e in everything] == [1, 2, 3]
    assert [e["eventId"] for e in one] == [f"{stream}@2"]
    assert none == []
