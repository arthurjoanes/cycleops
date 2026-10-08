"""The worker's two sources. Each one polls with the collection module's rules and pushes a
Work item per attempt; the processor turns it into one transaction.

LiveSource is the Collector over the real feed. SyntheticSource answers from SyntheticFeed
on the wall clock, with the same cadence formula as compile-replay's collect().
"""

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

import httpx

from cycleops.adapters.gbfs import Envelope
from cycleops.adapters.synthetic import ScenarioName, SyntheticConfig, SyntheticFeed
from cycleops.application.replay import attempt_from_line, attempt_input, engine_outcome
from cycleops.clock import Clock, SystemClock
from cycleops.collection.client import Attempt, FeedClient
from cycleops.collection.collector import Collector
from cycleops.collection.guard import FeedGuard, GuardEvent, next_status_at
from cycleops.collection.outcome import RETRY_AFTER_MAX, Outcome, classify_attempt
from cycleops.collection.recording import recording_line
from cycleops.domain.model import ClockTick, Input, StreamState
from cycleops.policy import Policy
from cycleops.worker.archive import ArchiveWriter, read_archive
from cycleops.worker.watchdog import Watchdog

log = logging.getLogger(__name__)

NAME = "source"
# The synthetic feed's parameters are the demo artifact's (fixtures/synthetic/demo.yaml);
# only the window differs. The continuous loop runs only these two scenarios.
SYNTHETIC_STATIONS = 267
SYNTHETIC_CADENCE_SECONDS = 15
SYNTHETIC_CHANGE_FRACTION = 0.05
LOOP_SCENARIOS: list[ScenarioName] = ["incident_recovery", "relapse"]
WINDOW = timedelta(hours=24)
# contract_quarantine in the worker: station_status breaks this long after the start and
# stays broken until the worker stops.
CONTRACT_AFTER = timedelta(seconds=20)


@dataclass(frozen=True)
class Work:
    """One attempt for the processor: what goes to feed_publications, the engine input
    (None for discovery feeds and 304), the feeds other than station_status in quarantine
    after it, and the breaker failure count to persist (station_status only)."""

    attempt: Attempt
    outcome: Outcome
    input: Input | None
    quarantined_feeds: frozenset[str]
    breaker_failures: int | None


# The processor's queue: attempts from the source and ticks from the ticker, in the order of
# their instants (both enqueue when their instant arrives on the wall clock).
Queue = asyncio.Queue[Work | ClockTick]


def work_item(attempt: Attempt, guards: dict[str, FeedGuard], policy: Policy) -> Work:
    """Same mapping as compile-replay's entries(); the guard already recorded the attempt.
    Classified again with the skew tolerance, which the collector does not pass."""
    outcome = engine_outcome(attempt, policy)
    entry, quarantined = attempt_input(attempt, outcome, guards)
    failures = guards[attempt.feed].failures if attempt.feed == "station_status" else None
    return Work(attempt, outcome, entry, quarantined, failures)


def restore_guards(guards: dict[str, FeedGuard], state: StreamState, breaker_failures: int) -> None:
    """Breaker and quarantine as the last run stored them, so a crash loop never turns
    into a burst of requests."""
    status = guards["station_status"]
    status.failures = breaker_failures
    status.breaker_open_until = state.breaker_open_until
    status.quarantined_since = state.quarantined_since
    status.last_attempt_at = state.last_attempt_at
    # Only the names of the other feeds in quarantine are stored: their next probe counts
    # from the last instant the stream processed.
    for name in state.quarantined_feeds & guards.keys():
        guards[name].quarantined_since = guards[name].last_attempt_at = state.clock_now


def restore_attempts(
    collector: Collector,
    archived: Iterable[dict[str, Any]],
    started: Mapping[str, datetime],
    policy: Policy,
) -> None:
    """The previous process's last request per feed, so that after a restart each feed
    waits its normal cadence from it and its Retry-After still holds. `archived` are raw
    archive lines in time order: every request sent, committed or not, with its headers.
    `started` is the last attempt_at per feed in feed_publications, for the requests the
    archive missed."""
    last = {line["feed"]: line for line in archived if line["feed"] in collector.guards}
    for feed, line in last.items():
        attempt = attempt_from_line(line)
        outcome = classify_attempt(attempt, policy.provider.station_report_semantics)
        collector.last_start[feed] = attempt.requested_at
        collector.guards[feed].retry_after_until = outcome.retry_after_until
        if outcome.ok:
            # station_status: the next poll also waits for last_updated + ttl + margin.
            collector.last_ok[feed] = outcome
    for feed, at in started.items():
        if feed in collector.guards:
            collector.last_start[feed] = max(at, collector.last_start.get(feed, at))


class LiveSource:
    def __init__(
        self,
        policy: Policy,
        identifier: str,
        state: StreamState,
        breaker_failures: int,
        queue: Queue,
        suspended: Callable[[], bool],
        watchdog: Watchdog,
        archive: ArchiveWriter | None,
        *,
        started: Mapping[str, datetime] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        """One per process: run() again in a new leadership term goes on with the same
        guards and cadence. `started`: see restore_attempts."""
        clock = clock or SystemClock()
        self._policy = policy
        self._queue = queue
        self._suspended = suspended
        self._watchdog = watchdog
        self._archive = archive
        self._clock = clock
        self._client = FeedClient(
            identifier, policy.provider.collection, transport=transport, clock=clock
        )
        self.collector = Collector(
            self._client,
            policy.provider,
            self._on_attempt,
            clock=clock,
            sleep=partial(watchdog.sleep, NAME),
            gate=self._gate,
        )
        restore_guards(self.collector.guards, state, breaker_failures)
        now = clock.now()
        # The longest wait between two requests to one feed.
        lookback = max(
            timedelta(seconds=policy.provider.collection.discovery_refresh_seconds),
            RETRY_AFTER_MAX,
        )
        archived = read_archive(archive.directory, now - lookback, now) if archive else ()
        restore_attempts(self.collector, archived, started or {}, policy)
        self._catalog_wanted = asyncio.Event()

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.collector.run())
            tg.create_task(self._catalog_on_request())

    async def aclose(self) -> None:
        await self._client.aclose()

    def request_catalog(self) -> None:
        """One extra station_information fetch: the status brought unknown station ids."""
        self._catalog_wanted.set()

    async def _catalog_on_request(self) -> None:
        while True:
            await self._catalog_wanted.wait()
            self._catalog_wanted.clear()
            await self._gate()
            now = self._clock.now()
            if self.collector.guards["station_information"].next_allowed_at(now) <= now:
                await self.collector.fetch("station_information")

    async def _gate(self) -> None:
        """No request while ingestion is suspended (database over quota)."""
        while self._suspended():
            await self._watchdog.sleep(NAME, self._watchdog.report_every)

    def _on_attempt(self, attempt: Attempt, outcome: Outcome) -> None:
        self._watchdog.progress(NAME)
        if self._archive is not None:
            try:
                self._archive.write(recording_line(attempt, outcome), attempt.received_at)
            except OSError:
                # The archive is a copy for reprocessing; a full disk must not stop ingestion.
                log.exception("raw archive write failed", extra={"feed": attempt.feed})
        self._queue.put_nowait(work_item(attempt, self.collector.guards, self._policy))


class SyntheticSource:
    FEEDS = ("station_information", "station_status")

    def __init__(
        self,
        policy: Policy,
        seed: int,
        anchor: datetime,
        state: StreamState,
        breaker_failures: int,
        queue: Queue,
        suspended: Callable[[], bool],
        watchdog: Watchdog,
        *,
        scenario: ScenarioName | None = None,
        started_at: datetime | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._policy = policy
        self._seed = seed
        # The stream's created_at: a restart continues the same timeline.
        self._anchor = anchor
        self._queue = queue
        self._suspended = suspended
        self._watchdog = watchdog
        config = policy.provider.collection
        self.guards = {
            name: FeedGuard(config, use_breaker=name == "station_status") for name in self.FEEDS
        }
        restore_guards(self.guards, state, breaker_failures)
        now = self._clock.now()
        self._due = {
            "station_information": self.guards["station_information"].next_allowed_at(now),
            "station_status": self.guards["station_status"].next_allowed_at(
                now + timedelta(seconds=1)
            ),
        }
        self._contract_from = (
            (started_at or now) + CONTRACT_AFTER if scenario == "contract_quarantine" else None
        )
        self._last_ok: Envelope | None = None
        self._window: tuple[int, SyntheticFeed] | None = None

    def request_catalog(self) -> None:
        self._due["station_information"] = self._clock.now()

    def feed_at(self, at: datetime) -> SyntheticFeed:
        """The 24 h window holding `at`, counted from the anchor. Same stations every day; the
        counts start over from the seed's at each window."""
        day = (at - self._anchor) // WINDOW
        if self._window is None or self._window[0] != day:
            start = self._anchor + day * WINDOW
            feed = SyntheticFeed(
                SyntheticConfig(
                    seed=self._seed,
                    day=day,
                    stations=SYNTHETIC_STATIONS,
                    cadence_seconds=SYNTHETIC_CADENCE_SECONDS,
                    change_fraction=SYNTHETIC_CHANGE_FRACTION,
                    start=start,
                    end=start + WINDOW,
                    scenarios=LOOP_SCENARIOS,
                )
            )
            if self._contract_from is not None:
                begin = (self._contract_from - start).total_seconds()
                feed.fault("station_status", "contract", begin, 2 * WINDOW.total_seconds())
            self._window = (day, feed)
        return self._window[1]

    async def run(self) -> None:
        config = self._policy.provider.collection
        semantics = self._policy.provider.station_report_semantics
        step = self._watchdog.report_every
        while True:
            self._watchdog.progress(NAME)
            name = min(self._due, key=self._due.__getitem__)
            wait = (self._due[name] - self._clock.now()).total_seconds()
            if wait > 0 or self._suspended():
                # Short naps, so request_catalog() and the quota are seen within a tick.
                await self._watchdog.sleep(NAME, min(wait, step) if wait > 0 else step)
                continue
            at = self._clock.now()
            attempt = self.feed_at(at).attempt(name, at, config.request_deadline_seconds)
            # The answer arrives after the synthetic response time, on the wall clock, so
            # the queue keeps the order of the instants.
            await self._watchdog.sleep(
                NAME, (attempt.received_at - self._clock.now()).total_seconds()
            )
            outcome = classify_attempt(attempt, semantics)
            guard = self.guards[name]
            _log(attempt, outcome, guard, guard.record(name, outcome, attempt.received_at))
            self._queue.put_nowait(work_item(attempt, self.guards, self._policy))
            if name == "station_status":
                if outcome.ok:
                    self._last_ok = outcome.envelope
                cadence = max(next_status_at(at, self._last_ok, config), attempt.received_at)
            else:
                cadence = at + timedelta(seconds=config.catalog_refresh_seconds)
            self._due[name] = guard.next_allowed_at(cadence)


def _log(attempt: Attempt, outcome: Outcome, guard: FeedGuard, event: GuardEvent | None) -> None:
    """The Collector's log lines, for the attempts that do not go through it."""
    feed = attempt.feed
    fields = {
        "feed": feed,
        "outcome": outcome.code,
        "status": outcome.status,
        "duration_ms": attempt.duration_ms,
    }
    if outcome.error_class == "contract":
        log.error(
            "%s quarantined: %s (fingerprint %s)",
            feed,
            outcome.detail or outcome.code,
            guard.fingerprint,
            extra=fields | {"fingerprint": guard.fingerprint},
        )
    elif event == "breaker_opened":
        log.warning("%s breaker open until %s", feed, guard.breaker_open_until, extra=fields)
    elif event == "quarantine_ended":
        log.info("%s quarantine ended", feed, extra=fields)
    elif not outcome.ok:
        log.warning("%s attempt failed: %s", feed, outcome.detail or outcome.code, extra=fields)
    else:
        log.debug("%s ok", feed, extra=fields)
