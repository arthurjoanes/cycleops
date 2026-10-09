"""The worker as a real process against a real PostgreSQL, plus the collection CLIs' lock.

Each worker runs `python -c` with the bootstrap below: the test can patch a module (a
stalled or failing processor, a shorter watchdog) and hand the live source an
httpx.MockTransport, so nothing reaches the network.
"""

import asyncio
import itertools
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, LiteralString

import psycopg
import pytest
import yaml
from conftest import POLICY_DIR, REPO
from psycopg.rows import dict_row
from test_worker_archive import H10, write

from cycleops import aio
from cycleops.adapters.postgres.connect import LOCK_KEY, create_engine
from cycleops.cli import inspect_feed, record_feed
from cycleops.cli.record_feed import LockLost, with_collector_lock
from cycleops.collection.client import FEEDS
from cycleops.collection.recording import iso, read_recording
from cycleops.worker.archive import read_archive
from cycleops.worker.processor import Quota, maintain
from integration.conftest import Database

BOOT = """
import asyncio, functools, httpx
from cycleops.application import ingest
from cycleops.domain.model import ClockTick
from cycleops.worker import main
transport = None
{code}
raise SystemExit(main.main(transport))
"""

HOUR = timedelta(hours=1)
# A live feed that never works: station_information is refused (contract, quarantine),
# everything else answers 503 (transport; station_status opens the breaker).
BROKEN_FEED = """
def handler(request):
    if request.url.path.endswith("/station_information.json"):
        return httpx.Response(400)
    return httpx.Response(503)
transport = httpx.MockTransport(handler)
"""
# A live feed that answers station_status, station_information and gbfs.
OK_FEED = f"""
import sys
sys.path.insert(0, {str(Path(__file__).parents[1])!r})
from conftest import discovery_body, info_body, status_body
from cycleops.collection.client import DEFAULT_URLS
BODIES = {{
    "gbfs": discovery_body(DEFAULT_URLS),
    "station_information": info_body(),
    "station_status": status_body(),
}}
def handler(request):
    feed = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
    return httpx.Response(200, json=BODIES[feed]) if feed in BODIES else httpx.Response(503)
transport = httpx.MockTransport(handler)
"""


class Worker:
    def __init__(self, proc: subprocess.Popen[bytes], log: Path) -> None:
        self.proc = proc
        self.log = log

    def lines(self) -> list[dict[str, Any]]:
        text = self.log.read_text(encoding="utf-8", errors="replace")
        return [json.loads(x) for x in text.splitlines() if x.startswith("{")]

    def messages(self) -> list[str]:
        return [line["message"] for line in self.lines()]

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(30)


@pytest.fixture
def start(clean_database: Database, tmp_path: Path) -> Iterator[Callable[..., Worker]]:
    workers: list[Worker] = []

    def start(code: str = "", **env: str) -> Worker:
        environ = {
            k: v for k, v in os.environ.items() if not k.startswith(("CYCLEOPS_", "DATABASE_URL"))
        }
        environ |= {
            "DATABASE_URL": clean_database.worker_url,
            "CYCLEOPS_POLICY_DIR": str(POLICY_DIR),
            "CYCLEOPS_HEARTBEAT_FILE": str(tmp_path / "heartbeat"),
            "CYCLEOPS_ARCHIVE_DIR": str(tmp_path / "archive"),
            "PYTHONUNBUFFERED": "1",
        }
        log = tmp_path / f"worker-{len(workers)}.log"
        with log.open("wb") as out:
            proc = subprocess.Popen(
                [sys.executable, "-c", BOOT.format(code=code)],
                env=environ | env,
                cwd=tmp_path,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        workers.append(Worker(proc, log))
        return workers[-1]

    try:
        yield start
    finally:
        # Never leave a worker writing into the next test's database.
        for worker in workers:
            worker.stop()


def query(db: Database, sql: LiteralString, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.superuser_url, autocommit=True) as conn:
        return conn.execute(sql, params).fetchall()


def wait_for(check: Callable[[], Any], timeout: float, what: str) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out after {timeout} s waiting for {what}")


def head(db: Database) -> dict[str, Any] | None:
    with psycopg.connect(db.superuser_url, autocommit=True, row_factory=dict_row) as conn:
        return conn.execute("SELECT * FROM stream_heads").fetchone()


def lock_pid(db: Database) -> int | None:
    rows = query(
        db,
        "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted AND database = "
        "(SELECT oid FROM pg_database WHERE datname = current_database())",
    )
    return rows[0][0] if rows else None


def test_collection_clis_refuse_to_run_next_to_a_leader(
    clean_database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("DATABASE_URL", clean_database.worker_url)
    monkeypatch.setenv("CYCLEOPS_CLIENT_IDENTIFIER", "test-cycleops")
    archive = tmp_path / "archive"
    write(archive, [H10 + timedelta(minutes=m) for m in (0, 20, 40)])
    monkeypatch.setenv("CYCLEOPS_ARCHIVE_DIR", str(archive))
    with psycopg.connect(clean_database.worker_url, autocommit=True) as leader:
        leader.execute("SELECT pg_advisory_lock(%s)", [LOCK_KEY])
        recorded = tmp_path / "live.ndjson.gz"
        assert record_feed.main(["--minutes", "1", "--out", str(recorded)]) == 4
        assert inspect_feed.main(["--out", str(tmp_path / "inspect.ndjson.gz")]) == 4
        assert "another collector holds the lock" in capsys.readouterr().err
        assert not recorded.exists()

        # --from-archive never calls the feed and never asks for the lock.
        out = tmp_path / "export.ndjson.gz"
        args = ["--from", "2026-10-08T10:10:00Z", "--to", "2026-10-08T11:00:00Z"]
        assert record_feed.main(["--from-archive", *args, "--out", str(out)]) == 0
        assert len(list(read_recording(out))) == 2


def test_a_cli_that_loses_its_lock_session_stops_collecting(
    clean_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = clean_database
    monkeypatch.setattr(record_feed, "LOCK_CHECK_SECONDS", 0.2)
    cancelled: list[bool] = []

    async def work() -> None:
        try:
            # Cancellation may arrive before the terminating query returns to this loop.
            # Observe it during all of the work, including that executor await.
            pid = await asyncio.to_thread(lock_pid, db)
            await asyncio.to_thread(query, db, "SELECT pg_terminate_backend(%s, 5000)", pid)
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    started = time.monotonic()
    with pytest.raises(LockLost):
        aio.run(with_collector_lock(db.worker_url, work))
    assert cancelled and time.monotonic() - started < 10
    assert record_feed.lock_refused(LockLost()) == record_feed.LOCK_LOST_EXIT
    assert "collector lock ended" in capsys.readouterr().err


def test_the_cli_holds_the_lock_while_it_collects(clean_database: Database) -> None:
    def taken_elsewhere() -> bool:
        with psycopg.connect(clean_database.worker_url, autocommit=True) as other:
            row = other.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY]).fetchone()
            return not (row and row[0])

    async def work() -> bool:
        return taken_elsewhere()

    assert aio.run(with_collector_lock(clean_database.worker_url, work)) is True
    assert taken_elsewhere() is False


def test_synthetic_worker_commits_notifies_and_comes_back_after_losing_the_lock(
    clean_database: Database, start: Callable[..., Worker]
) -> None:
    db = clean_database
    with psycopg.connect(db.superuser_url, autocommit=True) as listener:
        listener.execute("LISTEN cycleops_events")
        worker = start()
        cursors = [n.payload for n in listener.notifies(timeout=30, stop_after=2)]
    stream = wait_for(lambda: head(db), 5, "the stream")
    assert cursors == [f"{stream['stream_id']}@1", f"{stream['stream_id']}@2"]
    rows = query(db, "SELECT sequence, payload, committed_at FROM event_log ORDER BY sequence")
    assert [r[0] for r in rows[:2]] == [1, 2]
    for _, payload, committed_at in rows:
        assert payload["committedAt"] == committed_at.astimezone(UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    assert rows[0][1]["cause"] == "catalog" and rows[1][1]["cause"] == "publication"

    # The server drops the lock connection: the worker reconnects, retakes the lock and
    # goes on with the same stream.
    pid = wait_for(lambda: lock_pid(db), 5, "the advisory lock")
    query(db, "SELECT pg_terminate_backend(%s, 5000)", pid)
    wait_for(lambda: (lock_pid(db) or pid) != pid, 20, "a new lock session")
    seen = head(db)
    assert seen is not None
    wait_for(
        lambda: (h := head(db)) and h["worker_seen_at"] > seen["worker_seen_at"],
        15,
        "a tick after the reconnection",
    )
    assert worker.proc.poll() is None
    assert len(query(db, "SELECT 1 FROM stream_heads")) == 1
    messages = worker.messages()
    assert messages.count("leadership acquired") == 2
    assert any(m.startswith("database error, leadership ended") for m in messages)
    sequences = [r[0] for r in query(db, "SELECT sequence FROM event_log ORDER BY sequence")]
    assert sequences == list(range(1, len(sequences) + 1))
    # The source outlives the term: the catalog (every 10 min) was not fetched again.
    feeds = query(db, "SELECT feed FROM feed_publications WHERE feed = 'station_information'")
    assert len(feeds) == 1


def test_a_worker_waits_while_another_holds_the_lock(
    clean_database: Database, start: Callable[..., Worker]
) -> None:
    db = clean_database
    leader = psycopg.connect(db.worker_url, autocommit=True)
    try:
        leader.execute("SELECT pg_advisory_lock(%s)", [LOCK_KEY])
        worker = start()
        wait_for(
            lambda: "another worker holds the collector lock, waiting" in worker.messages(),
            20,
            "the worker to see the other leader",
        )
        time.sleep(3)
        assert head(db) is None
    finally:
        leader.close()
    wait_for(lambda: head(db), 15, "the worker to take over")
    assert worker.proc.poll() is None


def scaled_policy(tmp_path: Path) -> Path:
    """The Oslo provider with short collection waits: breaker and quarantine probe 20 s."""
    directory = tmp_path / "policy"
    directory.mkdir()
    (directory / "priority-policy.yaml").write_bytes(
        (POLICY_DIR / "priority-policy.yaml").read_bytes()
    )
    provider = yaml.safe_load((POLICY_DIR / "provider-oslo.yaml").read_text("utf-8"))
    provider["collection"] |= {
        "pollIntervalFloorSeconds": 3,
        "requestDeadlineSeconds": 2,
        "publicationMarginSeconds": 0,
        "breakerOpenSeconds": 20,
        "quarantineProbeSeconds": 20,
    }
    (directory / "provider-oslo.yaml").write_text(yaml.safe_dump(provider), "utf-8")
    return directory


def test_a_live_source_that_only_fails_never_stops_the_worker_nor_trips_the_watchdog(
    clean_database: Database, start: Callable[..., Worker], tmp_path: Path
) -> None:
    db = clean_database
    # Watchdog at 8 s: every wait below (breaker 20 s, probe 20 s, discovery 1 h) is longer,
    # so only waits that report progress keep the process alive.
    code = BROKEN_FEED + "main.Watchdog = functools.partial(main.Watchdog, stall_seconds=8)"
    worker = start(
        code,
        CYCLEOPS_SOURCE="live",
        CYCLEOPS_CLIENT_IDENTIFIER="test-cycleops",
        CYCLEOPS_POLICY_DIR=str(scaled_policy(tmp_path)),
    )
    time.sleep(40)
    assert worker.proc.poll() is None, worker.log.read_text("utf-8")
    beat = tmp_path / "heartbeat"
    assert time.time() - beat.stat().st_mtime < 10
    worker.stop()

    state = head(db)
    assert state is not None
    assert state["source_state"] == "stale"
    assert state["breaker_failures"] >= 3
    assert state["quarantined_feeds"] == ["station_information"]
    attempts = query(
        db,
        "SELECT feed, outcome, error_code, attempt_at FROM feed_publications ORDER BY attempt_at",
    )
    status = [a for a in attempts if a[0] == "station_status"]
    assert all(a[1] == "http_error" for a in status)
    # 3 polls 3 s apart open the breaker; then one trial per 20 s: never a tight loop.
    assert 4 <= len(status) <= 6
    assert all(b[3] - a[3] >= timedelta(seconds=19) for a, b in itertools.pairwise(status[2:]))
    info = [a for a in attempts if a[0] == "station_information"]
    assert info[0][1:3] == ("http_error", "status 400")
    assert 2 <= len(info) <= 3

    # Every attempt is in the raw archive too (the archive is written first).
    now = datetime.now(UTC)
    archived = {x["requestedAt"] for x in read_archive(tmp_path / "archive", now - HOUR, now)}
    assert {iso(a[3]) for a in attempts} <= archived
    lines = worker.lines()
    assert any(x["level"] == "WARNING" and "breaker open" in x["message"] for x in lines)
    quarantined = [
        x for x in lines if x["level"] == "ERROR" and x.get("feed") == "station_information"
    ]
    assert quarantined and re.fullmatch(r"[0-9a-f]{16}", quarantined[0]["fingerprint"])


def test_neither_a_new_term_nor_a_restart_polls_a_feed_early(
    clean_database: Database, start: Callable[..., Worker], tmp_path: Path
) -> None:
    db = clean_database
    env = {"CYCLEOPS_SOURCE": "live", "CYCLEOPS_CLIENT_IDENTIFIER": "test-cycleops"}

    def attempts(feed: str) -> list[datetime]:
        rows = query(
            db,
            "SELECT attempt_at FROM feed_publications WHERE feed = %s ORDER BY attempt_at",
            feed,
        )
        return [r[0] for r in rows]

    worker = start(OK_FEED, **env)
    wait_for(lambda: all(attempts(f) for f in FEEDS), 30, "one attempt per feed")
    # A new term: the server drops the lock connection.
    pid = wait_for(lambda: lock_pid(db), 5, "the advisory lock")
    query(db, "SELECT pg_terminate_backend(%s, 5000)", pid)
    wait_for(lambda: worker.messages().count("worker running") == 2, 30, "a new term")
    time.sleep(3)
    worker.stop()

    # A restart with an empty raw archive: the cadence comes from feed_publications.
    wait_for(lambda: lock_pid(db) is None, 10, "the first worker's session to end")
    last = attempts("station_status")[-1]
    again = start(OK_FEED, **env, CYCLEOPS_ARCHIVE_DIR=str(tmp_path / "archive-2"))
    wait_for(lambda: attempts("station_status")[-1] > last, 40, "a poll after the restart")
    again.stop()

    for feed in ("gbfs", "system_information", "vehicle_types"):
        assert len(attempts(feed)) == 1, feed
    status = attempts("station_status")
    assert all(b - a >= timedelta(seconds=15) for a, b in itertools.pairwise(status)), status


def test_the_watchdog_ends_a_worker_whose_processor_stalls(
    clean_database: Database, start: Callable[..., Worker]
) -> None:
    code = """
real = ingest.Ingestor.process
async def stuck(self, conn, entry, *args, **kwargs):
    if isinstance(entry, ClockTick):
        await asyncio.sleep(3600)
    return await real(self, conn, entry, *args, **kwargs)
ingest.Ingestor.process = stuck
main.Watchdog = functools.partial(main.Watchdog, stall_seconds=7)
"""
    worker = start(code)
    assert worker.proc.wait(40) == 3
    [line] = [x for x in worker.lines() if x["level"] == "CRITICAL"]
    assert line["loops"] == ["processor"]


def test_an_exception_in_the_processor_ends_the_process_with_a_non_zero_code(
    clean_database: Database, start: Callable[..., Worker]
) -> None:
    code = """
async def boom(self, conn, entry, *args, **kwargs):
    raise RuntimeError("injected")
ingest.Ingestor.process = boom
"""
    worker = start(code)
    assert worker.proc.wait(40) == 1
    [line] = [x for x in worker.lines() if x["level"] == "ERROR"]
    assert line["message"] == "worker stopped by an unhandled error"
    assert "RuntimeError: injected" in line["traceback"]


def test_contract_scenario_quarantines_station_status_and_logs_it(
    clean_database: Database, start: Callable[..., Worker]
) -> None:
    db = clean_database
    worker = start(CYCLEOPS_SYNTHETIC_SCENARIO="contract_quarantine")
    wait_for(lambda: (h := head(db)) and h["source_state"] == "quarantined", 60, "the quarantine")
    [(outcome, error)] = query(
        db,
        "SELECT outcome, error_code FROM feed_publications "
        "WHERE feed = 'station_status' AND outcome NOT IN ('ok', 'duplicate')",
    )
    assert outcome == "invalid" and "version '3.0'" in error
    [(payload,)] = query(db, "SELECT payload FROM event_log ORDER BY sequence DESC LIMIT 1")
    assert payload["feedHealth"]["state"] == "quarantined"
    worker.stop()

    lines = worker.lines()
    assert lines
    for line in lines:
        assert {"time", "level", "logger", "message", "provider", "mode"} <= line.keys()
        assert (line["provider"], line["mode"]) == ("synthetic-oslo", "synthetic")
    stream = head(db)
    assert stream is not None
    events = [x for x in lines if x["message"] == "event committed"]
    assert events
    for event in events:
        assert event["streamId"] == stream["stream_id"]
        assert event["eventId"] == f"{stream['stream_id']}@{event['sequence']}"
        assert {"cause", "count", "outcome", "duration_ms", "feedState"} <= event.keys()
    [contract] = [x for x in lines if x["level"] == "ERROR"]
    assert contract["feed"] == "station_status" and contract["outcome"] == "invalid"
    assert re.fullmatch(r"[0-9a-f]{16}", contract["fingerprint"])
    # No payload in the logs: no station ids, no feed fields.
    text = worker.log.read_text("utf-8")
    assert "S-0001" not in text and "num_bikes_available" not in text
    assert max(len(x) for x in text.splitlines()) < 2000


def test_a_database_over_quota_suspends_ingestion(
    clean_database: Database, start: Callable[..., Worker]
) -> None:
    db = clean_database
    worker = start(CYCLEOPS_DB_QUOTA_MB="0.001")
    state = wait_for(
        lambda: (h := head(db)) and h["ingestion_suspended"] and h, 30, "the suspension"
    )
    assert (state["last_outcome"], state["source_state"]) == ("db_quota_exceeded", "stale")
    time.sleep(5)
    # The quota is checked before the source starts: not one fetch.
    assert query(db, "SELECT count(*) FROM feed_publications") == [(0,)]
    assert worker.proc.poll() is None
    assert "database over quota, ingestion suspended" in worker.messages()


def test_the_purge_cycle_sets_and_clears_the_quota(clean_database: Database) -> None:
    async def go() -> list[bool]:
        engine = create_engine(clean_database.worker_url)
        quota = Quota()
        seen = []
        try:
            for limit in (0.001, 1_000_000):
                await maintain(engine, quota, limit)
                seen.append(quota.exceeded)
        finally:
            await engine.dispose()
        return seen

    assert aio.run(go()) == [True, False]
