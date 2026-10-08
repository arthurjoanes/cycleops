import re
from typing import Any

import psycopg
import pytest
from conftest import discovery_body
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops import aio
from cycleops.adapters.postgres.connect import connect_lock, create_engine, try_lock, unlock
from cycleops.adapters.postgres.tables import (
    event_log,
    feed_publications,
    station_observations,
    stream_heads,
)
from cycleops.application import ingest
from cycleops.application.ingest import StreamMismatch, create_or_load_stream
from cycleops.collection.client import DEFAULT_URLS
from cycleops.contracts import DataMode, iso
from cycleops.domain.engine import step
from cycleops.domain.model import PublicationFailed
from integration.conftest import Database
from integration.feed import (
    POLICY,
    SEED,
    SYSTEM,
    attempt,
    catalog,
    classify,
    publication,
    tick,
    worker,
)


async def counts(conn: AsyncConnection) -> tuple[int, int, int]:
    """(head_sequence, event_log rows, feed_publications rows), then ends the read."""
    row = (
        await conn.execute(
            select(
                select(stream_heads.c.head_sequence).scalar_subquery(),
                select(func.count()).select_from(event_log).scalar_subquery(),
                select(func.count()).select_from(feed_publications).scalar_subquery(),
            )
        )
    ).one()
    await conn.commit()
    return row[0], row[1], row[2]


def test_state_event_and_attempt_are_one_commit(clean_database: Database) -> None:
    async def go() -> tuple[Any, ...]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            entry, a, outcome = publication(1, {"100": 3, "200": 0})
            [event] = await ingestor.process(conn, entry, a, outcome)
            xmins = (
                await conn.execute(
                    text(
                        "SELECT 'head', xmin::text FROM stream_heads "
                        "UNION ALL SELECT 'event', xmin::text FROM event_log WHERE sequence = :s "
                        "UNION ALL SELECT 'attempt', xmin::text FROM feed_publications "
                        "WHERE feed = 'station_status' "
                        "UNION ALL SELECT 'current', xmin::text FROM station_current "
                        "WHERE last_sequence = :s "
                        "UNION ALL SELECT 'observation', xmin::text FROM station_observations "
                        "WHERE sequence = :s"
                    ),
                    {"s": event.sequence},
                )
            ).all()
            stored = (
                await conn.execute(
                    select(event_log.c.payload, event_log.c.committed_at).where(
                        event_log.c.sequence == event.sequence
                    )
                )
            ).one()
            row = (
                await conn.execute(
                    select(feed_publications).where(feed_publications.c.feed == "station_status")
                )
            ).one()
            return event, a, xmins, stored, row

    event, a, xmins, stored, row = aio.run(go())
    assert sorted(name for name, _ in xmins) == [
        "attempt",
        "current",
        "current",
        "event",
        "head",
        "observation",
        "observation",
    ]
    assert len({xmin for _, xmin in xmins}) == 1
    # committed_at and the payload's committedAt are the same instant, and the caller
    # gets it back.
    assert stored.payload["committedAt"] == iso(stored.committed_at) == iso(event.committed_at)
    assert stored.committed_at == event.committed_at
    assert event.sequence == 2 and stored.payload["eventId"] == f"{event.stream_id}@2"
    assert (row.outcome, row.http_status, row.station_count, row.valid_count) == ("ok", 200, 2, 2)
    assert row.distinct_last_reported == 1
    assert row.bytes == len(a.body_bytes or b"") and row.payload_sha256 is not None
    assert row.source_updated_at == event.source_updated_at
    assert (row.attempt_at, row.received_at) == (a.requested_at, a.received_at)


def test_an_error_rolls_everything_back_and_the_next_entry_reloads(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ingest._append

    async def append_then_fail(conn: AsyncConnection, event: Any) -> Any:
        await real(conn, event)
        raise RuntimeError("injected")

    async def go() -> None:
        listener = await psycopg.AsyncConnection.connect(clean_database.api_url, autocommit=True)
        await listener.execute("LISTEN cycleops_events")
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            assert await counts(conn) == (1, 1, 1)
            entry, a, outcome = publication(1, {"100": 3, "200": 0})

            monkeypatch.setattr(ingest, "_append", append_then_fail)
            with pytest.raises(RuntimeError):
                await ingestor.process(conn, entry, a, outcome)
            assert ingestor.state is None
            assert await counts(conn) == (1, 1, 1)

            monkeypatch.setattr(ingest, "_append", real)
            [event] = await ingestor.process(conn, entry, a, outcome)
            assert event.sequence == 2
            assert await counts(conn) == (2, 2, 2)
        # Only committed events are announced.
        got = [n.payload async for n in listener.notifies(timeout=2, stop_after=3)]
        await listener.close()
        assert got == [f"{event.stream_id}@1", f"{event.stream_id}@2"]

    aio.run(go())


def test_a_tick_after_a_publication_gets_its_own_observation(clean_database: Database) -> None:
    """Same station, same source_updated_at, another sequence: no key collision."""

    async def go() -> list[Any]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 3, "200": 0})[:1])
            # 65 s after last_updated: the source is stale, every station's condition moves.
            [event] = await ingestor.process(conn, tick(80))
            assert event.feed_health.state == "stale"
            rows = (
                await conn.execute(
                    select(
                        station_observations.c.sequence,
                        station_observations.c.source_updated_at,
                        station_observations.c.conditions,
                    )
                    .where(station_observations.c.station_id == "100")
                    .order_by(station_observations.c.sequence)
                )
            ).all()
            return list(rows)

    rows = aio.run(go())
    assert [r.sequence for r in rows] == [1, 2, 3]
    assert rows[1].source_updated_at == rows[2].source_updated_at is not None
    assert "unverifiable" not in rows[1].conditions
    assert "unverifiable" in rows[2].conditions


def test_killing_the_lock_connection_mid_entry_ends_the_leadership_without_writing(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def go() -> None:
        lock = await connect_lock(clean_database.worker_url)
        assert await try_lock(lock)
        pid, *keepalives = (
            await lock.execute(
                text(
                    "SELECT pg_backend_pid(), current_setting('tcp_keepalives_idle'), "
                    "current_setting('tcp_keepalives_interval'), "
                    "current_setting('tcp_keepalives_count'), current_setting('tcp_user_timeout')"
                )
            )
        ).one()
        await lock.commit()
        # Server side, so PostgreSQL drops a frozen worker's session and frees the lock.
        assert keepalives == ["10", "5", "3", "20000"]
        raw = await lock.get_raw_connection()
        client = raw.driver_connection.info.get_parameters()  # type: ignore[union-attr]
        assert {k: client[k] for k in ("keepalives_idle", "tcp_user_timeout")} == {
            "keepalives_idle": "10",
            "tcp_user_timeout": "20000",
        }
        ingestor = await create_or_load_stream(lock, SYSTEM, "synthetic", SEED, POLICY)
        await ingestor.process(lock, *catalog())

        def kill_then_step(*args: Any) -> Any:
            # The fetch is done and the transaction holds the head row: the server drops
            # the session now.
            with psycopg.connect(clean_database.superuser_url, autocommit=True) as admin:
                admin.execute("SELECT pg_terminate_backend(%s, 5000)", [pid])
            return step(*args)

        monkeypatch.setattr(ingest, "step", kill_then_step)
        with pytest.raises(DBAPIError) as raised:
            await ingestor.process(lock, *publication(1, {"100": 3, "200": 0}))
        # The signal the worker reads to drop the leadership and reconnect.
        assert raised.value.connection_invalidated
        assert ingestor.state is None
        await lock.close()

        engine = create_engine(clean_database.worker_url)
        async with engine.connect() as conn:
            assert await counts(conn) == (1, 1, 1)
            # The lock went with the session: the next leader takes it.
            assert await try_lock(conn)
            await unlock(conn)
        await engine.dispose()

    aio.run(go())


def test_the_stream_is_created_once_and_another_mode_system_or_seed_is_refused(
    clean_database: Database,
) -> None:
    async def go() -> None:
        engine = create_engine(clean_database.worker_url)
        async with engine.connect() as conn:
            first = await create_or_load_stream(conn, SYSTEM, "synthetic", SEED, POLICY)
            again = await create_or_load_stream(conn, SYSTEM, "synthetic", SEED, POLICY)
            assert re.fullmatch(rf"{SYSTEM}:[0-9a-f]{{8}}", first.stream_id)
            assert (again.stream_id, again.created_at) == (first.stream_id, first.created_at)
            assert again.state == first.state
            seen = (await conn.execute(select(stream_heads.c.worker_seen_at))).scalar_one()
            await conn.commit()
            assert seen is not None
            others: list[tuple[str, DataMode, int | None]] = [
                (SYSTEM, "live", None),
                ("oslobysykkel", "synthetic", SEED),
                (SYSTEM, "synthetic", SEED + 1),
            ]
            for system, mode, seed in others:
                with pytest.raises(StreamMismatch):
                    await create_or_load_stream(conn, system, mode, seed, POLICY)
        await engine.dispose()

    aio.run(go())


def test_attempts_without_events_ticks_and_breaker_failures(clean_database: Database) -> None:
    async def go() -> None:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 3, "200": 0}))
            # Same publication again: the engine calls it a duplicate, no event.
            assert await ingestor.process(conn, *publication(1, {"100": 3, "200": 0}, 25)) == []
            # A discovery feed makes no engine input; only its attempt is written.
            urls = {"station_status": DEFAULT_URLS["station_status"]}
            gbfs = attempt("gbfs", 26, discovery_body(urls))
            assert await ingestor.process(conn, None, gbfs, classify(gbfs)) == []
            failed = attempt("station_status", 30, None)
            outcome = classify(failed)
            entry = PublicationFailed(
                failed.received_at, "station_status", outcome.code, "transport", None, None
            )
            assert await ingestor.process(conn, entry, failed, outcome, breaker_failures=1) == []
            rows = (
                await conn.execute(
                    select(
                        feed_publications.c.feed,
                        feed_publications.c.outcome,
                        feed_publications.c.error_code,
                    ).order_by(feed_publications.c.attempt_at)
                )
            ).all()
            await conn.execute(
                stream_heads.update().values(worker_seen_at=text("now() - interval '1 hour'"))
            )
            await conn.commit()
            await ingestor.process(conn, tick(35))
            head = (
                await conn.execute(
                    select(
                        stream_heads.c.breaker_failures,
                        stream_heads.c.worker_seen_at,
                    )
                )
            ).one()
            await conn.commit()
            reopened = await create_or_load_stream(conn, SYSTEM, "synthetic", SEED, POLICY)

        assert [tuple(r) for r in rows] == [
            ("station_information", "ok", None),
            ("station_status", "ok", None),
            ("station_status", "duplicate", None),
            ("gbfs", "ok", None),
            ("station_status", "timeout", "timeout"),
        ]
        assert head[0] == 1 and reopened.breaker_failures == 1
        assert head[1] == tick(35).at
        assert reopened.state is not None and reopened.state.last_outcome == "timeout"

    aio.run(go())
