import asyncio
import itertools
import os
import threading
from pathlib import Path

import pytest

from cycleops.worker import watchdog as watchdog_module
from cycleops.worker.watchdog import Watchdog


class Ticks:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_heartbeat_while_every_loop_progresses_and_none_after_a_stall(tmp_path: Path) -> None:
    beat = tmp_path / "heartbeat"
    ticks = Ticks()
    dog = Watchdog(beat, 5, monotonic=ticks)
    dog.progress("processor")
    dog.progress("source")
    assert dog.check() == []
    assert beat.exists()

    os.utime(beat, (0, 0))
    ticks.t += 59
    dog.progress("source")
    ticks.t += 2
    # processor last reported 61 s ago: no heartbeat, and it is named.
    assert dog.check() == ["processor"]
    assert beat.stat().st_mtime == 0


def test_reset_forgets_loops_that_ended(tmp_path: Path) -> None:
    ticks = Ticks()
    dog = Watchdog(tmp_path / "heartbeat", 5, monotonic=ticks)
    dog.progress("leader")
    ticks.t += 120
    dog.reset()
    dog.progress("processor")
    assert dog.check() == []


def test_sleep_reports_at_least_every_report_every_seconds(tmp_path: Path) -> None:
    pieces: list[float] = []
    reports: list[float] = []
    ticks = Ticks()

    async def fake_sleep(seconds: float) -> None:
        pieces.append(seconds)
        ticks.t += seconds

    class Spy(Watchdog):
        def progress(self, name: str) -> None:
            reports.append(ticks.t)
            super().progress(name)

    dog = Spy(tmp_path / "heartbeat", 5, sleep=fake_sleep, monotonic=ticks)
    asyncio.run(dog.sleep("source", 300))
    assert sum(pieces) == 300 and max(pieces) <= 5
    gaps = [b - a for a, b in itertools.pairwise(reports)]
    assert max(gaps) <= 5
    assert reports[-1] - reports[0] == 300


def test_the_thread_exits_with_code_3_on_a_stall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watchdog_module, "HEARTBEAT_SECONDS", 0.01)
    exited = threading.Event()
    codes: list[int] = []

    def fake_exit(code: int) -> None:
        codes.append(code)
        exited.set()

    dog = Watchdog(tmp_path / "heartbeat", 5, stall_seconds=0.05, exit=fake_exit)
    dog.progress("processor")
    dog.start()
    assert exited.wait(5)
    dog.stop()
    assert codes == [3]
