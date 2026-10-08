from typing import Any

from sqlalchemy import select, text

from cycleops import aio
from cycleops.adapters.postgres import queries
from cycleops.adapters.postgres.connect import create_engine
from cycleops.adapters.postgres.maintenance import RETENTION, database_size_mb, purge
from cycleops.adapters.postgres.tables import stream_heads
from cycleops.worker.processor import Quota, maintain
from integration.conftest import Database
from integration.feed import catalog, publication, tick, worker

# Per table: rows past the retention, rows inside it.
ROWS = """
INSERT INTO event_log (stream_id, sequence, event_type, payload, committed_at)
SELECT 's', n, 'station_batch', '{}',
       now() - CASE WHEN n <= 5 THEN interval '25 hours' ELSE interval '23 hours' END
FROM generate_series(1, 8) n;

INSERT INTO station_observations (stream_id, station_id, sequence, committed_at,
    vehicle_types, quality, quality_flags, primary_condition, conditions, content_hash)
SELECT 's', 'S-1', n,
       now() - CASE WHEN n <= 5 THEN interval '8 days' ELSE interval '6 days' END,
       '[]', 'valid', '{}', 'ok', '{ok}', 'h'
FROM generate_series(1, 7) n;

INSERT INTO feed_publications (stream_id, attempt_at, feed, outcome, received_at, duration_ms)
SELECT 's', now() - interval '1 day' * d, 'station_status', 'ok', now(), 1
FROM unnest(ARRAY[8, 9, 10, 6]) d;

INSERT INTO station_incidents (id, system_id, station_id, stream_id, kind, state,
    at_opening, first_observed_at, observed_since, closed_at, observations, reason_text,
    policy_version)
SELECT id, 'sys', 'S-' || id, 's', 'pickup_unavailable', state, false,
       now() - interval '30 days', now() - interval '30 days', closed, '[]', '', '1.0.0'
FROM (VALUES
    ('a', 'resolved', now() - interval '8 days'),
    ('b', 'discarded', now() - interval '9 days'),
    ('c', 'closed_without_recovery', now() - interval '6 days'),
    -- Open for 30 days: never purged.
    ('d', 'confirmed', NULL::timestamptz)
) AS v(id, state, closed);
"""


def test_purge_deletes_past_the_retention_in_batches(clean_database: Database) -> None:
    async def go() -> tuple[dict[str, int], dict[str, int]]:
        engine = create_engine(clean_database.worker_url)
        async with engine.connect() as conn:
            for statement in ROWS.split(";\n"):
                if statement.strip():
                    await conn.execute(text(statement))
            await conn.commit()
            deleted = await purge(conn, batch=2)
            left = {}
            for table in RETENTION:
                left[table] = (
                    await conn.execute(text(f"SELECT count(*) FROM {table}"))
                ).scalar_one()
            await conn.commit()
        await engine.dispose()
        return deleted, left

    deleted, left = aio.run(go())
    assert deleted == {
        "event_log": 5,
        "station_observations": 5,
        "feed_publications": 3,
        "station_incidents": 2,
    }
    assert left == {
        "event_log": 3,
        "station_observations": 2,
        "feed_publications": 1,
        "station_incidents": 2,
    }


def test_database_over_the_quota_suspends_ingestion_until_it_is_back_under(
    clean_database: Database,
) -> None:
    """What the worker's purge loop does: measure, compare with CYCLEOPS_DB_QUOTA_MB, and
    let the ticks carry the result."""

    async def go() -> list[Any]:
        async def suspended() -> bool:
            value = (await conn.execute(select(stream_heads.c.ingestion_suspended))).scalar_one()
            await conn.commit()
            return bool(value)

        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 3, "200": 3}))
            size = await database_size_mb(conn)
            [event] = await ingestor.process(conn, tick(20, size > 1.0))
            health = await queries.feed_health(conn)
            assert health is not None
            over = (await suspended(), event.feed_health.state, health.last_outcome)
            # Back under the quota: the flag clears now, the source leaves stale after
            # freshAgainAfterPublications accepted publications.
            assert await ingestor.process(conn, tick(25, size > 10_000.0)) == []
            under = await suspended()
            await ingestor.process(conn, *publication(2, {"100": 3, "200": 3}))
            [event] = await ingestor.process(conn, *publication(3, {"100": 3, "200": 3}))
            return [size, over, under, event.feed_health.state]

    size, over, under, after = aio.run(go())
    assert size > 1
    assert over == (True, "stale", "db_quota_exceeded")
    assert under is False and after == "fresh"


# About 25 MB of event_log rows past the retention, then one inside it at the end of the
# heap, so VACUUM cannot just cut the file.
FILL = """
INSERT INTO event_log (stream_id, sequence, event_type, payload, committed_at)
SELECT 's', n, 'station_batch', jsonb_build_object('pad', repeat(md5(n::text), 12)),
       now() - interval '25 hours'
FROM generate_series(1, 60000) n;

INSERT INTO event_log VALUES ('s', 60001, 'station_batch', '{}', now())
"""


def test_the_purge_brings_the_measured_size_back_under_the_quota(
    clean_database: Database,
) -> None:
    async def go() -> tuple[float, float, float, bool]:
        engine = create_engine(clean_database.worker_url)
        try:
            async with engine.connect() as conn:
                empty = await database_size_mb(conn)
                for statement in FILL.split(";\n"):
                    await conn.execute(text(statement))
                await conn.commit()
                full = await database_size_mb(conn)
            quota = Quota(exceeded=True)
            await maintain(engine, quota, empty + 10)
            async with engine.connect() as conn:
                after = await database_size_mb(conn)
        finally:
            await engine.dispose()
        return empty, full, after, quota.exceeded

    empty, full, after, exceeded = aio.run(go())
    assert full > empty + 20
    assert after < empty + 10
    assert exceeded is False
