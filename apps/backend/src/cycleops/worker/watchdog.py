"""Progress registry, heartbeat file and the stall exit.

Every critical loop reports progress on each iteration and sleeps through `sleep()`, which
reports at least every `report_every` seconds, so waiting on the breaker, a quarantine
probe or the database counts as alive. The check runs in a thread, not a task: a loop
blocked in a synchronous call stops the event loop and every task with it.
"""

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)

STALL_SECONDS = 60.0
HEARTBEAT_SECONDS = 5.0
EXIT_CODE = 3


class Watchdog:
    def __init__(
        self,
        heartbeat: Path,
        report_every: float,
        *,
        stall_seconds: float = STALL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        exit: Callable[[int], object] = os._exit,
    ) -> None:
        self.heartbeat = heartbeat
        self.report_every = report_every
        self._stall = stall_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._exit = exit
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def progress(self, name: str) -> None:
        with self._lock:
            self._seen[name] = self._monotonic()

    def reset(self) -> None:
        """Forgets every loop. Called when the set of running loops changes (leadership
        won or lost), so a loop that ended is not taken for a stalled one."""
        with self._lock:
            self._seen.clear()

    async def sleep(self, name: str, seconds: float) -> None:
        """Sleeps in pieces of at most `report_every` seconds, reporting before each."""
        while True:
            self.progress(name)
            if seconds <= 0:
                return
            piece = min(seconds, self.report_every)
            await self._sleep(piece)
            seconds -= piece

    def check(self) -> list[str]:
        """The loops without progress for `stall_seconds`. With none, touches the heartbeat
        file (the container healthcheck reads its mtime)."""
        now = self._monotonic()
        with self._lock:
            stalled = sorted(name for name, at in self._seen.items() if now - at > self._stall)
        if not stalled:
            try:
                self.heartbeat.touch()
            except OSError as exc:
                log.warning("heartbeat file not written: %s", exc)
        return stalled

    def start(self) -> None:
        threading.Thread(target=self._run, name="watchdog", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while True:
            stalled = self.check()
            if stalled:
                log.critical(
                    "no progress for %.0f s in %s, exiting",
                    self._stall,
                    ", ".join(stalled),
                    extra={"loops": stalled},
                )
                # os._exit: the event loop may be the thing that is stuck, so nothing that
                # needs it (cancellation, finally blocks) can be relied on.
                self._exit(EXIT_CODE)
                return
            if self._stop.wait(HEARTBEAT_SECONDS):
                return
