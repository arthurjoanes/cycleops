"""record-feed: records the live feed for a while, one NDJSON line per attempt.

With --from-archive it writes the same recording from the worker's raw archive instead,
without network and without the collector lock.
"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cycleops import aio
from cycleops.adapters.postgres.connect import connect_lock, try_lock
from cycleops.clock import SystemClock
from cycleops.collection.client import (
    IDENTIFIER_ENV,
    ClientIdentifierError,
    FeedClient,
    client_identifier,
)
from cycleops.collection.collector import Collector
from cycleops.collection.recording import RecordingWriter, recording_line
from cycleops.policy import ProviderConfig, load_provider
from cycleops.worker.archive import archive_dir, read_archive

log = logging.getLogger(__name__)

LOCK_HELD_EXIT = 4
LOCK_HELD = (
    "another collector holds the lock (the worker is running). Record from its raw archive "
    "instead: record-feed --from-archive --from <utc> --to <utc> --out <file>"
)
LOCK_LOST_EXIT = 5
LOCK_LOST = (
    "the database session holding the collector lock ended, so a worker may be polling "
    "the feed now: stopped"
)
# How often the lock session is checked while the CLI collects.
LOCK_CHECK_SECONDS = 5.0


class LockHeld(Exception):
    pass


class LockLost(Exception):
    pass


async def with_collector_lock[T](url: str | None, work: Callable[[], Awaitable[T]]) -> T:
    """Runs `work` holding the worker's advisory lock, so a CLI never polls the feed next
    to a running worker. Raises LockHeld when another session has it. Without a URL, runs
    it unchecked.

    The lock lives as long as its session: a SELECT 1 every LOCK_CHECK_SECONDS checks it,
    and if the session is gone, `work` is cancelled and LockLost raised."""
    if url is None:
        return await work()
    conn = await connect_lock(url)
    try:
        if not await try_lock(conn):
            raise LockHeld
        task = asyncio.ensure_future(work())
        try:
            while True:
                await asyncio.wait([task], timeout=LOCK_CHECK_SECONDS)
                if task.done():
                    return task.result()
                try:
                    await conn.execute(text("SELECT 1"))
                    await conn.commit()
                except DBAPIError as exc:
                    raise LockLost from exc
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    finally:
        with suppress(DBAPIError):
            await conn.close()


def lock_refused(exc: LockHeld | LockLost | DBAPIError) -> int:
    """Prints why and returns the exit code: 4 when another session holds the lock, 5 when
    the session holding it ended, 1 when it could not be checked."""
    if isinstance(exc, LockHeld):
        print(LOCK_HELD, file=sys.stderr)
        return LOCK_HELD_EXIT
    if isinstance(exc, LockLost):
        print(LOCK_LOST, file=sys.stderr)
        return LOCK_LOST_EXIT
    print(f"could not check the collector lock: {exc.orig or exc}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="record-feed", description=__doc__)
    parser.add_argument("--minutes", type=float, help="how long to record")
    parser.add_argument("--out", type=Path, help="default: data/recordings/record-<UTC>.ndjson.gz")
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        help="total deadline per request (default: requestDeadlineSeconds of the provider)",
    )
    parser.add_argument(
        "--from-archive",
        action="store_true",
        help="read the worker's raw archive (CYCLEOPS_ARCHIVE_DIR) instead of the feed",
    )
    parser.add_argument("--from", dest="start", type=instant, help="with --from-archive, UTC")
    parser.add_argument(
        "--to", dest="end", type=instant, help="with --from-archive, UTC, exclusive"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # One INFO line per request from httpx is noise over a long recording.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    provider = load_provider()
    if args.from_archive:
        if args.start is None or args.end is None or args.out is None:
            parser.error("--from-archive needs --from, --to and --out")
        if args.start >= args.end:
            parser.error("--from must be before --to")
        manifest = export(provider, args.start, args.end, args.out)
        if manifest is None:
            print(f"no archived attempt between {args.start} and {args.end}", file=sys.stderr)
            return 1
        print(f"{args.out}: {manifest['requests']} requests, {manifest['failures']} failures")
        return 0
    if args.minutes is None:
        parser.error("--minutes is required (or --from-archive)")
    try:
        identifier = client_identifier()
    except ClientIdentifierError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not os.environ.get("DATABASE_URL"):
        # Only inside Compose does the CLI reach the worker's database.
        log.warning("DATABASE_URL is not set: the collector lock was not checked")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out: Path = args.out or Path("data/recordings") / f"record-{stamp}.ndjson.gz"
    deadline: float = args.deadline_seconds or provider.collection.request_deadline_seconds
    url = os.environ.get("DATABASE_URL") or None
    try:
        manifest = aio.run(
            with_collector_lock(
                url, lambda: record(provider, identifier, deadline, args.minutes, out)
            )
        )
    except LockLost as exc:
        code = lock_refused(exc)
        print(f"kept what was recorded in {out}", file=sys.stderr)
        return code
    except (LockHeld, DBAPIError) as exc:
        return lock_refused(exc)
    except KeyboardInterrupt:
        print(f"interrupted, kept what was recorded in {out}", file=sys.stderr)
        return 130
    print(f"{out}: {manifest['requests']} requests, {manifest['failures']} failures")
    return 0


async def record(
    provider: ProviderConfig,
    identifier: str,
    deadline: float,
    minutes: float,
    out: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    client = FeedClient(
        identifier, provider.collection, deadline_seconds=deadline, transport=transport
    )
    with RecordingWriter(out, identifier, deadline) as writer:
        collector = Collector(
            client,
            provider,
            lambda attempt, outcome: writer.write(recording_line(attempt, outcome)),
        )
        until = SystemClock().now() + timedelta(minutes=minutes)
        try:
            await collector.run(until=until)
        finally:
            await client.aclose()
        return writer.close()


def export(
    provider: ProviderConfig, start: datetime, end: datetime, out: Path
) -> dict[str, Any] | None:
    """The archived attempts with receivedAt in [start, end) as a recording with its
    manifest. None, and no file, when there is none."""
    lines = read_archive(archive_dir(), start, end)
    first = next(lines, None)
    if first is None:
        return None
    # The archive does not keep the identifier; the worker's environment has it.
    identifier = os.environ.get(IDENTIFIER_ENV, "").strip() or "unknown"
    deadline = provider.collection.request_deadline_seconds
    with RecordingWriter(out, identifier, deadline) as writer:
        writer.write(first)
        for line in lines:
            writer.write(line)
        return writer.close()


def instant(value: str) -> datetime:
    instant = datetime.fromisoformat(value)
    return instant if instant.tzinfo else instant.replace(tzinfo=UTC)
