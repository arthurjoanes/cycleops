"""Retention and database size, for the worker's purge loop."""

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops.adapters.postgres.tables import FINAL_STATES_SQL

BATCH = 5000
# Table -> what is past the retention. Open incidents never go.
RETENTION = {
    "event_log": "committed_at < now() - interval '24 hours'",
    "station_observations": "committed_at < now() - interval '7 days'",
    "feed_publications": "attempt_at < now() - interval '7 days'",
    "station_incidents": f"state IN {FINAL_STATES_SQL} AND closed_at < now() - interval '7 days'",
}


async def purge(
    conn: AsyncConnection, batch: int = BATCH, progress: Callable[[], None] | None = None
) -> dict[str, int]:
    """Deletes in batches, one short transaction each, so the processor never waits long
    on these rows, then vacuums the tables. Returns the rows deleted per table. `conn` must
    have no transaction open. `progress` is called after every batch (the worker's
    watchdog)."""
    deleted: dict[str, int] = {}
    for table, past in RETENTION.items():
        # Constants only in the SQL text; the batch size is a parameter.
        stmt = text(
            f"DELETE FROM {table} WHERE ctid = ANY(ARRAY("
            f"SELECT ctid FROM {table} WHERE {past} LIMIT :n))"
        )
        total = 0
        while True:
            async with conn.begin():
                count = (await conn.execute(stmt, {"n": batch})).rowcount
            total += count
            if progress is not None:
                progress()
            if count < batch:
                break
        deleted[table] = total
    # A DELETE leaves the files at their peak size: the oldest rows sit at the start of
    # these tables, so the heap is rarely cut at the end, and B-tree files never shrink.
    # VACUUM puts the freed pages in the free space map, where new rows reuse them and
    # database_size_mb() stops counting them. It cannot run inside a transaction.
    await conn.execution_options(isolation_level="AUTOCOMMIT")
    async with conn.begin():
        await conn.execute(text(f"VACUUM {', '.join(RETENTION)}"))
    await conn.execution_options(isolation_level=conn.default_isolation_level)
    return deleted


# The files' size minus the free space VACUUM recorded in them: what the data takes, which
# is what the quota is about. Index pages emptied by a purge show up as free one VACUUM
# later, once no transaction can still reach them.
USED = text(
    "SELECT pg_database_size(current_database()) - coalesce(sum(f.avail), 0) "
    "FROM pg_class c CROSS JOIN LATERAL public.pg_freespace(c.oid) f "
    "WHERE c.relnamespace IN ('public'::regnamespace, 'pg_toast'::regnamespace) "
    "AND c.relkind IN ('r', 'i', 't') AND NOT c.relisshared"
)


async def database_size_mb(conn: AsyncConnection) -> float:
    async with conn.begin():
        size = (await conn.execute(USED)).scalar_one()
    return float(size) / (1024 * 1024)
