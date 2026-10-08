"""The worker: one source, one processor, the ticker, the purge loop and the watchdog.

Leadership is pg_try_advisory_lock on a dedicated connection; the processor writes every
entry on that connection. A database error (a lost connection, a full disk) ends the
term: the loops stop, and the worker waits, retakes the lock and reloads the stream from
the database. The wait doubles while terms keep ending early. The source, the queue and
the quota flag outlive the term, so a new term goes on with the same cadence per feed.

Exit codes: 0 stopped (SIGTERM), 1 unhandled error, 2 configuration or a database that
holds another stream, 3 watchdog (a loop stalled).
"""

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from cycleops import aio, jsonlog
from cycleops.adapters.postgres.connect import connect_lock, create_engine, try_lock
from cycleops.adapters.postgres.tables import feed_publications
from cycleops.adapters.synthetic import ScenarioName
from cycleops.application.ingest import StreamMismatch, create_or_load_stream
from cycleops.clock import Clock, SystemClock
from cycleops.collection.client import ClientIdentifierError, client_identifier
from cycleops.domain.engine import policy_change
from cycleops.policy import Policy, load_policy
from cycleops.worker.archive import ArchiveWriter, archive_dir
from cycleops.worker.processor import Processor, Quota, maintain, maintenance, ticker
from cycleops.worker.sources import LiveSource, Queue, SyntheticSource
from cycleops.worker.watchdog import Watchdog

log = logging.getLogger("cycleops.worker")

SourceName = Literal["live", "synthetic"]
# Only this scenario runs in the worker (the phase 3 E2E); the others are compile-replay's.
WORKER_SCENARIOS: tuple[ScenarioName, ...] = ("contract_quarantine",)
OTHER_LEADER_RETRY_SECONDS = 5.0
BACKOFF_SECONDS = (1.0, 30.0)
# A term that lasted this long resets the wait before the next one.
STABLE_TERM_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 10.0
# The lock connection plus 2 for the purge loop.
POOL_SIZE = 2
# A disconnect shows up as one of these (the lock connection or the pool).
CONNECTION_ERRORS = (OperationalError, InterfaceError)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    source: SourceName
    seed: int | None
    scenario: ScenarioName | None
    quota_mb: float
    archive_dir: Path
    heartbeat_file: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> Settings:
        def get(name: str) -> str | None:
            # compose passes unset variables as empty strings from an env file.
            return environ.get(name) or None

        url = get("DATABASE_URL")
        if url is None:
            raise ConfigError("DATABASE_URL is not set")
        source = get("CYCLEOPS_SOURCE") or "synthetic"
        if source not in get_args(SourceName):
            raise ConfigError(f"CYCLEOPS_SOURCE must be live or synthetic, not {source!r}")
        scenario = get("CYCLEOPS_SYNTHETIC_SCENARIO")
        if scenario is not None and (source != "synthetic" or scenario not in WORKER_SCENARIOS):
            raise ConfigError(
                "CYCLEOPS_SYNTHETIC_SCENARIO accepts only contract_quarantine, "
                "with the synthetic source"
            )
        try:
            seed = int(get("CYCLEOPS_SYNTHETIC_SEED") or 42) if source == "synthetic" else None
            quota = float(get("CYCLEOPS_DB_QUOTA_MB") or 1024)
        except ValueError as exc:
            raise ConfigError(f"CYCLEOPS_SYNTHETIC_SEED / CYCLEOPS_DB_QUOTA_MB: {exc}") from exc
        if quota <= 0:
            raise ConfigError("CYCLEOPS_DB_QUOTA_MB must be above 0")
        return cls(
            database_url=url,
            source="live" if source == "live" else "synthetic",
            seed=seed,
            scenario="contract_quarantine" if scenario else None,
            quota_mb=quota,
            archive_dir=archive_dir(),
            heartbeat_file=Path(get("CYCLEOPS_HEARTBEAT_FILE") or "/tmp/cycleops-worker.heartbeat"),
        )


class _Context(logging.Filter):
    """Adds provider, mode and, once known, streamId to every log line of the worker."""

    def __init__(self, **fields: object) -> None:
        super().__init__()
        self.fields = fields

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in self.fields.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


def main(transport: httpx.AsyncBaseTransport | None = None) -> int:
    """`transport` replaces the network for the live source in tests."""
    jsonlog.setup()
    # One line per request from httpx would repeat the collector's own log line.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
        policy = load_policy("oslo" if settings.source == "live" else "synthetic")
        identifier = client_identifier() if settings.source == "live" else None
    except (ConfigError, ClientIdentifierError, ValidationError, OSError) as exc:
        log.error("worker not started: %s", exc)
        return 2
    context = _Context(provider=policy.provider.system_id, mode=settings.source)
    for handler in logging.getLogger().handlers:
        handler.addFilter(context)
    watchdog = Watchdog(settings.heartbeat_file, policy.priority.tick_interval_seconds)
    watchdog.start()
    try:
        aio.run(_serve(settings, policy, identifier, watchdog, context, transport))
    except StreamMismatch as exc:
        log.error("worker not started: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        log.exception("worker stopped by an unhandled error")
        return 1
    finally:
        watchdog.stop()
    return 0


async def _serve(
    settings: Settings,
    policy: Policy,
    identifier: str | None,
    watchdog: Watchdog,
    context: _Context,
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    task = asyncio.current_task()
    if sys.platform != "win32" and task is not None:
        # docker stop: cancel, so the archive file gets its gzip trailer and the lock
        # connection closes cleanly.
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await run(settings, policy, identifier, watchdog, context, transport)
    except asyncio.CancelledError:
        log.info("worker stopped")


@dataclass
class _Kept:
    """What outlives a leadership term. Items left in the queue come after the last
    committed one, so the next term processes them."""

    queue: Queue = field(default_factory=asyncio.Queue)
    quota: Quota = field(default_factory=Quota)
    source: LiveSource | SyntheticSource | None = None


async def run(
    settings: Settings,
    policy: Policy,
    identifier: str | None,
    watchdog: Watchdog,
    context: _Context,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock | None = None,
) -> None:
    clock = clock or SystemClock()
    started_at = clock.now()
    engine = create_engine(settings.database_url, pool_size=POOL_SIZE, max_overflow=0)
    archive = ArchiveWriter(settings.archive_dir) if settings.source == "live" else None
    kept = _Kept()
    wait = 0.0
    try:
        while True:
            watchdog.reset()
            await watchdog.sleep("leader", wait)
            lock = await _acquire(settings.database_url, watchdog)
            began = clock.now()
            try:
                await _lead(
                    settings,
                    policy,
                    identifier,
                    lock,
                    engine,
                    archive,
                    watchdog,
                    context,
                    started_at,
                    transport,
                    kept,
                )
            except* CONNECTION_ERRORS as group:
                # Retaking the lock at once would turn a lasting error (a full disk) into a
                # tight loop on the database: the wait doubles until a term runs long enough.
                stable = (clock.now() - began).total_seconds() >= STABLE_TERM_SECONDS
                low, high = BACKOFF_SECONDS
                wait = low if stable else min(max(2 * wait, low), high)
                log.warning(
                    "database error, leadership ended, next try in %.0f s: %s",
                    wait,
                    "; ".join(_first_line(e) for e in group.exceptions),
                )
            finally:
                with suppress(Exception):
                    await lock.close()
    finally:
        if archive is not None:
            archive.close()
        if isinstance(kept.source, LiveSource):
            await kept.source.aclose()
        await engine.dispose()


async def _acquire(url: str, watchdog: Watchdog) -> AsyncConnection:
    """Waits for the collector lock: every 5 s while another worker holds it, with
    exponential backoff while the database is unreachable."""
    delay, waiting_logged = BACKOFF_SECONDS[0], False
    while True:
        watchdog.progress("leader")
        conn: AsyncConnection | None = None
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                conn = await connect_lock(url)
                acquired = await try_lock(conn)
            if acquired:
                log.info("leadership acquired")
                return conn
            if not waiting_logged:
                log.info("another worker holds the collector lock, waiting")
                waiting_logged = True
            wait, delay = OTHER_LEADER_RETRY_SECONDS, BACKOFF_SECONDS[0]
        except (*CONNECTION_ERRORS, OSError, TimeoutError) as exc:
            log.warning("database unavailable, retrying in %.0f s: %s", delay, _first_line(exc))
            wait, delay = delay, min(delay * 2, BACKOFF_SECONDS[1])
        if conn is not None:
            with suppress(Exception):
                await conn.close()
        await watchdog.sleep("leader", wait)


async def _lead(
    settings: Settings,
    policy: Policy,
    identifier: str | None,
    lock: AsyncConnection,
    engine: AsyncEngine,
    archive: ArchiveWriter | None,
    watchdog: Watchdog,
    context: _Context,
    started_at: datetime,
    transport: httpx.AsyncBaseTransport | None,
    kept: _Kept,
) -> None:
    """One leadership term. Returns only by an exception."""
    ingestor = await create_or_load_stream(
        lock, policy.provider.system_id, settings.source, settings.seed, policy
    )
    context.fields["streamId"] = ingestor.stream_id
    state = ingestor.state
    assert state is not None
    change = policy_change(state, policy, SystemClock().now())
    if change is not None:
        log.info("policy changed", extra={"policyVersion": change.policy_version})
        await ingestor.process(lock, change)
    quota = kept.quota
    # The quota is known before the source starts: no fetch while the database is full.
    await maintain(engine, quota, settings.quota_mb, lambda: watchdog.progress("leader"))

    state = ingestor.state
    assert state is not None
    if kept.source is None and settings.source == "live":
        assert identifier is not None
        kept.source = LiveSource(
            policy,
            identifier,
            state,
            ingestor.breaker_failures,
            kept.queue,
            lambda: quota.exceeded,
            watchdog,
            archive,
            started=await last_attempts(lock, ingestor.stream_id),
            transport=transport,
        )
    elif kept.source is None:
        assert settings.seed is not None
        kept.source = SyntheticSource(
            policy,
            settings.seed,
            ingestor.created_at,
            state,
            ingestor.breaker_failures,
            kept.queue,
            lambda: quota.exceeded,
            watchdog,
            scenario=settings.scenario,
            started_at=started_at,
        )
    source = kept.source
    processor = Processor(lock, ingestor, kept.queue, watchdog, source.request_catalog)
    # From here these loops report; the leader wait does not any more.
    watchdog.reset()
    log.info("worker running", extra={"policyVersion": policy.version})
    async with asyncio.TaskGroup() as tg:
        tg.create_task(source.run())
        tg.create_task(processor.run())
        tg.create_task(ticker(kept.queue, quota, watchdog, policy.priority.tick_interval_seconds))
        tg.create_task(maintenance(engine, quota, settings.quota_mb, watchdog))


async def last_attempts(conn: AsyncConnection, stream_id: str) -> dict[str, datetime]:
    """The last attempt_at per feed of the stream: where each feed's cadence starts after a
    restart, when the raw archive lacks it."""
    column = feed_publications.c
    async with conn.begin():
        rows = await conn.execute(
            select(column.feed, func.max(column.attempt_at))
            .where(column.stream_id == stream_id)
            .group_by(column.feed)
        )
        return {feed: at for feed, at in rows}


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text.splitlines()[0] if text else ''}"
