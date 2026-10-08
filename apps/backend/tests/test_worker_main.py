import asyncio
import itertools
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import POLICY_DIR
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection
from test_worker_sources import START, VirtualTime, stream

from cycleops.application.ingest import Ingestor
from cycleops.collection.client import Attempt
from cycleops.collection.outcome import Outcome
from cycleops.contracts import StationBatchEvent
from cycleops.domain.model import ClockTick, Input
from cycleops.policy import load_policy
from cycleops.worker import main
from cycleops.worker.main import ConfigError, Settings
from cycleops.worker.processor import Processor, Quota, ticker
from cycleops.worker.sources import Queue, Work
from cycleops.worker.watchdog import Watchdog


def test_settings_defaults() -> None:
    settings = Settings.from_env({"DATABASE_URL": "postgresql://w@db/cycleops"})
    assert settings.source == "synthetic"
    assert (settings.seed, settings.scenario, settings.quota_mb) == (42, None, 1024)
    assert settings.heartbeat_file == Path("/tmp/cycleops-worker.heartbeat")
    live = Settings.from_env(
        {
            "DATABASE_URL": "postgresql://w@db/cycleops",
            "CYCLEOPS_SOURCE": "live",
            "CYCLEOPS_SYNTHETIC_SEED": "",
            "CYCLEOPS_DB_QUOTA_MB": "200",
        }
    )
    assert (live.source, live.seed, live.quota_mb) == ("live", None, 200)
    scenario = Settings.from_env(
        {
            "DATABASE_URL": "postgresql://w@db/cycleops",
            "CYCLEOPS_SYNTHETIC_SEED": "7",
            "CYCLEOPS_SYNTHETIC_SCENARIO": "contract_quarantine",
        }
    )
    assert (scenario.seed, scenario.scenario) == (7, "contract_quarantine")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CYCLEOPS_SOURCE": "replay"},
        {"CYCLEOPS_SYNTHETIC_SCENARIO": "relapse"},
        {"CYCLEOPS_SOURCE": "live", "CYCLEOPS_SYNTHETIC_SCENARIO": "contract_quarantine"},
        {"CYCLEOPS_SYNTHETIC_SEED": "x"},
        {"CYCLEOPS_DB_QUOTA_MB": "0"},
    ],
)
def test_settings_refused(env: dict[str, str]) -> None:
    base = {"DATABASE_URL": "postgresql://w@db/cycleops"} if env else {}
    with pytest.raises(ConfigError):
        Settings.from_env(base | env)


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"DATABASE_URL": "postgresql://w@127.0.0.1:1/x", "CYCLEOPS_SOURCE": "live"},
    ],
)
def test_the_process_exits_2_on_configuration(env: dict[str, str], tmp_path: Path) -> None:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("CYCLEOPS_", "DATABASE"))}
    clean |= {"CYCLEOPS_POLICY_DIR": str(POLICY_DIR), "CYCLEOPS_CLIENT_IDENTIFIER": "<x>"}
    done = subprocess.run(
        [sys.executable, "-m", "cycleops.worker"],
        env=clean | env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 2
    assert '"level": "ERROR"' in done.stderr and "worker not started" in done.stderr


def run_ticker(minutes: float, quota: Quota, jump: datetime | None = None) -> list[ClockTick]:
    clock = VirtualTime(START + timedelta(seconds=3.2))

    async def go() -> list[ClockTick]:
        queue: Queue = asyncio.Queue()
        watchdog = Watchdog(Path("unused"), 5, sleep=clock.sleep)

        async def stall() -> None:
            if jump is not None:
                await clock.sleep((jump - clock.now()).total_seconds())
                quota.exceeded = True
                # The clock leaps 37 s while the ticker sleeps.
                clock.t += timedelta(seconds=37)

        async def body() -> None:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(ticker(queue, quota, watchdog, 5, clock))
                tg.create_task(stall())

        await clock.run(body(), START + timedelta(minutes=minutes))
        ticks = []
        while not queue.empty():
            item = queue.get_nowait()
            assert isinstance(item, ClockTick)
            ticks.append(item)
        return ticks

    return asyncio.run(go())


def test_ticks_fall_on_the_epoch_grid_and_carry_the_quota() -> None:
    ticks = run_ticker(1, Quota())
    assert [t.at for t in ticks] == [START + timedelta(seconds=s) for s in range(5, 61, 5)]
    assert not any(t.ingestion_suspended for t in ticks)
    assert all(t.quarantined_feeds == frozenset() for t in ticks)


def test_after_a_stall_the_ticker_skips_to_the_latest_grid_point() -> None:
    ticks = run_ticker(1, Quota(), jump=START + timedelta(seconds=11))
    instants = [(t.at - START).total_seconds() for t in ticks]
    # 5, 10, then the clock is at 48 s: one tick at 45, then the grid again.
    assert instants[:5] == [5, 10, 45, 50, 55]
    assert [t.ingestion_suspended for t in ticks[:3]] == [False, False, True]


class FakeIngestor:
    def __init__(self) -> None:
        self.state = stream()
        self.calls: list[tuple[Input | None, int | None]] = []
        self.four = asyncio.Event()

    async def process(
        self,
        conn: AsyncConnection,
        entry: Input | None,
        attempt: Attempt | None = None,
        outcome: Outcome | None = None,
        breaker_failures: int | None = None,
    ) -> list[StationBatchEvent]:
        self.calls.append((entry, breaker_failures))
        # The engine raises the flag on the second call and keeps it up.
        self.state.catalog_fetch_pending = len(self.calls) >= 2
        if len(self.calls) == 4:
            self.four.set()
        return []


def test_processor_stamps_ticks_and_asks_for_the_catalog_once(tmp_path: Path) -> None:
    fake = FakeIngestor()
    fake.state.quarantined_feeds = {"vehicle_types"}
    requests: list[None] = []
    at = datetime(2026, 10, 8, tzinfo=UTC)
    work = Work(
        cast(Attempt, None),
        cast(Outcome, None),
        None,
        frozenset({"gbfs"}),
        2,
    )

    async def go() -> None:
        queue: Queue = asyncio.Queue()
        for item in (ClockTick(at), work, ClockTick(at), ClockTick(at)):
            queue.put_nowait(item)
        processor = Processor(
            cast(AsyncConnection, None),
            cast(Ingestor, fake),
            queue,
            Watchdog(tmp_path / "hb", 0.01),
            lambda: requests.append(None),
        )
        task = asyncio.create_task(processor.run())
        await asyncio.wait_for(fake.four.wait(), 5)
        task.cancel()

    asyncio.run(go())
    entries: list[Any] = [entry for entry, _ in fake.calls]
    # Before any attempt, ticks carry the feeds the stream stored; then the last attempt's.
    assert entries[0].quarantined_feeds == {"vehicle_types"}
    assert entries[1] is None and fake.calls[1][1] == 2
    assert entries[2].quarantined_feeds == entries[3].quarantined_feeds == {"gbfs"}
    assert len(requests) == 1


class Done(Exception):
    pass


def test_leadership_terms_back_off_until_one_runs_long_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A term that keeps failing (a full disk) never becomes a tight loop on the database."""
    clock = VirtualTime(START)
    # How long each term runs before a database error ends it.
    lengths = iter([0, 0, 0, 0, 0, 0, 0, 120, 0, 0])
    began: list[datetime] = []

    class Lock:
        async def close(self) -> None:
            pass

    async def acquire(url: str, watchdog: Watchdog) -> Lock:
        return Lock()

    async def lead(*args: Any) -> None:
        began.append(clock.now())
        seconds = next(lengths, None)
        if seconds is None:
            raise Done
        await clock.sleep(seconds)
        raise OperationalError("INSERT", {}, Exception("could not extend file"))

    monkeypatch.setattr(main, "_acquire", acquire)
    monkeypatch.setattr(main, "_lead", lead)
    settings = Settings.from_env({"DATABASE_URL": "postgresql://w@127.0.0.1:1/x"})
    watchdog = Watchdog(Path("unused"), 5, sleep=clock.sleep)
    policy = load_policy("synthetic", POLICY_DIR)

    async def go() -> None:
        work = main.run(settings, policy, None, watchdog, main._Context(), clock=clock)
        await clock.run(work, START + timedelta(hours=1))

    with pytest.raises(Done):
        asyncio.run(go())
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(began)]
    # 1 s doubling up to 30 s; a term of 120 s starts over at 1 s.
    assert gaps == [1, 2, 4, 8, 16, 30, 30, 121, 2, 4]
