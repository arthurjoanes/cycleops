"""The single consumer of the queue, the ticker that feeds it ticks, and the purge loop
that decides whether ingestion is suspended."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from cycleops.adapters.postgres.maintenance import database_size_mb, purge
from cycleops.application.ingest import Ingestor
from cycleops.application.replay import grid_ceil
from cycleops.clock import Clock, SystemClock
from cycleops.domain.model import ClockTick
from cycleops.worker.sources import Queue
from cycleops.worker.watchdog import Watchdog

log = logging.getLogger(__name__)

PURGE_EVERY_SECONDS = 600


@dataclass
class Quota:
    """Set by the purge loop; the ticker carries it to the engine and the source stops
    fetching while it holds."""

    exceeded: bool = False


class Processor:
    def __init__(
        self,
        conn: AsyncConnection,
        ingestor: Ingestor,
        queue: Queue,
        watchdog: Watchdog,
        request_catalog: Callable[[], None],
    ) -> None:
        assert ingestor.state is not None
        self._conn = conn
        self._ingestor = ingestor
        self._queue = queue
        self._watchdog = watchdog
        self._request_catalog = request_catalog
        # Ticks carry the quarantined feeds as the last attempt left them, as in
        # compile-replay; until the first attempt, as the stream stored them.
        self._quarantined = frozenset(ingestor.state.quarantined_feeds)
        self._catalog_pending = ingestor.state.catalog_fetch_pending

    async def run(self) -> None:
        """One transaction per item, on the lock connection. Errors are not caught here: a
        database error ends the leadership in main, anything else ends the process."""
        while True:
            self._watchdog.progress("processor")
            try:
                async with asyncio.timeout(self._watchdog.report_every):
                    item = await self._queue.get()
            except TimeoutError:
                continue
            started = time.monotonic()
            if isinstance(item, ClockTick):
                tick = replace(item, quarantined_feeds=self._quarantined)
                events = await self._ingestor.process(self._conn, tick)
            else:
                self._quarantined = item.quarantined_feeds
                events = await self._ingestor.process(
                    self._conn, item.input, item.attempt, item.outcome, item.breaker_failures
                )
            duration_ms = round((time.monotonic() - started) * 1000)
            for event in events:
                log.info(
                    "event committed",
                    extra={
                        "eventId": event.event_id,
                        "sequence": event.sequence,
                        "cause": event.cause,
                        "count": len(event.stations),
                        "feedState": event.feed_health.state,
                        "outcome": event.feed_health.last_outcome,
                        "duration_ms": duration_ms,
                    },
                )
            state = self._ingestor.state
            pending = state is not None and state.catalog_fetch_pending
            # The engine asks once per catalogVersion; the source fetches on the rising edge.
            if pending and not self._catalog_pending:
                self._request_catalog()
            self._catalog_pending = pending


async def ticker(
    queue: Queue,
    quota: Quota,
    watchdog: Watchdog,
    interval_seconds: int,
    clock: Clock | None = None,
) -> None:
    """A ClockTick on every multiple of the interval counted from the epoch, enqueued when
    its instant arrives."""
    clock = clock or SystemClock()
    interval = timedelta(seconds=interval_seconds)
    at = grid_ceil(clock.now(), interval)
    while True:
        wait = (at - clock.now()).total_seconds()
        await watchdog.sleep("ticker", wait)
        if wait > 0:
            continue
        # Late by more than an interval (blocked loop, host asleep): only the latest grid
        # point goes out, not a burst of every missed one.
        at = max(at, grid_ceil(clock.now() - interval, interval))
        queue.put_nowait(ClockTick(at, ingestion_suspended=quota.exceeded))
        at += interval


async def maintain(
    engine: AsyncEngine, quota: Quota, limit_mb: float, progress: Callable[[], None] | None = None
) -> None:
    """One purge cycle, then the database size against the quota. `progress` keeps the
    watchdog informed between batches."""
    async with engine.connect() as conn:
        deleted = await purge(conn, progress=progress)
        size = await database_size_mb(conn)
    exceeded = size > limit_mb
    fields = {"deleted": deleted, "size_mb": round(size, 1), "quota_mb": limit_mb}
    if exceeded and not quota.exceeded:
        log.error("database over quota, ingestion suspended", extra=fields)
    elif quota.exceeded and not exceeded:
        log.info("database back under quota, ingestion resumed", extra=fields)
    else:
        log.info("purge done", extra=fields)
    quota.exceeded = exceeded


async def maintenance(
    engine: AsyncEngine, quota: Quota, limit_mb: float, watchdog: Watchdog
) -> None:
    while True:
        await watchdog.sleep("purge", PURGE_EVERY_SECONDS)
        await maintain(engine, quota, limit_mb, lambda: watchdog.progress("purge"))
