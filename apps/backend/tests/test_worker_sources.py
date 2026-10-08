import asyncio
import heapq
import itertools
import logging
from collections.abc import Callable, Coroutine, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import POLICY_DIR, discovery_body, info_body, station, status_body

from cycleops.adapters.gbfs import StatusPublication
from cycleops.collection.client import DEFAULT_URLS
from cycleops.collection.guard import FeedGuard
from cycleops.collection.recording import read_recording
from cycleops.domain.engine import new_stream
from cycleops.domain.model import (
    CatalogReceived,
    PublicationFailed,
    PublicationReceived,
    StreamState,
)
from cycleops.policy import load_policy
from cycleops.worker.archive import ArchiveWriter
from cycleops.worker.sources import LiveSource, Queue, SyntheticSource, Work, restore_guards
from cycleops.worker.watchdog import Watchdog

SYNTHETIC = load_policy("synthetic", POLICY_DIR)
OSLO = load_policy("oslo", POLICY_DIR)
START = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)


class VirtualTime:
    """A clock whose sleeps wake in instant order: time jumps to the next wake-up once
    every task is waiting, so concurrent loops keep their real relative timing."""

    def __init__(self, start: datetime) -> None:
        self.t = start
        self._sleepers: list[tuple[datetime, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    def now(self) -> datetime:
        return self.t

    async def sleep(self, seconds: float) -> None:
        wake = self.t + timedelta(seconds=max(seconds, 0))
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (wake, next(self._order), future))
        await future

    async def run(self, coro: Coroutine[Any, Any, None], until: datetime) -> None:
        """Runs `coro` up to `until`. An exception it raises fails the test."""
        task = asyncio.create_task(coro)
        while self.t < until and not task.done():
            for _ in range(50):
                await asyncio.sleep(0)
            if not self._sleepers:
                break
            wake, _, future = heapq.heappop(self._sleepers)
            self.t = max(self.t, wake)
            if not future.done():
                future.set_result(None)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def drain(queue: Queue) -> list[Work]:
    items = []
    while not queue.empty():
        item = queue.get_nowait()
        assert isinstance(item, Work)
        items.append(item)
    return items


def stream(**fields: Any) -> StreamState:
    state = new_stream("synthetic-oslo", "synthetic", "synthetic-oslo:test", 42)
    for key, value in fields.items():
        setattr(state, key, value)
    return state


def run_synthetic(
    minutes: float,
    *,
    anchor: datetime = START,
    state: StreamState | None = None,
    suspended: Callable[[], bool] = lambda: False,
    scenario: str | None = None,
) -> tuple[list[Work], SyntheticSource, Watchdog]:
    clock = VirtualTime(START)
    holder: dict[str, Any] = {}

    async def go() -> None:
        queue: Queue = asyncio.Queue()
        watchdog = Watchdog(Path("unused"), 5, sleep=clock.sleep)
        source = SyntheticSource(
            SYNTHETIC,
            42,
            anchor,
            state or stream(),
            0,
            queue,
            suspended,
            watchdog,
            scenario="contract_quarantine" if scenario else None,
            started_at=START,
            clock=clock,
        )
        holder.update(queue=queue, source=source, watchdog=watchdog)
        await clock.run(source.run(), START + timedelta(minutes=minutes))

    asyncio.run(go())
    return drain(holder["queue"]), holder["source"], holder["watchdog"]


def statuses(items: list[Work]) -> list[Work]:
    return [w for w in items if w.attempt.feed == "station_status"]


def publication(work: Work) -> StatusPublication:
    assert isinstance(work.input, PublicationReceived)
    return work.input.publication


def test_synthetic_source_follows_the_collect_cadence_and_runs_the_loop_scenarios() -> None:
    items, source, _ = run_synthetic(20)
    info = [w for w in items if w.attempt.feed == "station_information"]
    status = statuses(items)
    assert [w.attempt.requested_at for w in info] == [START, START + timedelta(minutes=10)]
    assert all(isinstance(w.input, CatalogReceived) for w in info)
    assert status[0].attempt.requested_at == START + timedelta(seconds=1)
    gaps = [
        (b.attempt.requested_at - a.attempt.requested_at).total_seconds()
        for a, b in itertools.pairwise(status)
    ]
    # max(previous start + 15 s, last_updated + ttl 15 + margin 10)
    assert all(15 <= g <= 26 for g in gaps), gaps
    updated = [publication(w).envelope.last_updated for w in status]
    assert updated == sorted(set(updated))
    # incident_recovery: S-0001 is empty from 120 s to 360 s after the anchor, then half full.
    half = source.feed_at(START).capacity[0] // 2
    for work in status:
        offset = (publication(work).envelope.last_updated - START).total_seconds()
        bikes = next(
            s.bikes_available for s in publication(work).stations if s.station_id == "S-0001"
        )
        if 120 <= offset < 360:
            assert bikes == 0
        elif offset >= 360:
            assert bikes == half
    assert all(w.breaker_failures == 0 for w in status)
    assert all(w.breaker_failures is None for w in info)


def test_synthetic_window_rolls_over_every_24_h_on_the_same_seed_and_the_next_day() -> None:
    anchor = START - timedelta(hours=24) + timedelta(seconds=60)
    items, source, _ = run_synthetic(3, anchor=anchor)
    before = source.feed_at(START)
    assert (before.config.seed, before.config.day) == (42, 0)
    after = source.feed_at(START + timedelta(minutes=2))
    config = after.config
    assert (config.seed, config.day, config.start) == (42, 1, anchor + timedelta(hours=24))
    # Same stations every day; only the walk changes.
    assert (after.ids, after.capacity, after.position) == (
        before.ids,
        before.capacity,
        before.position,
    )
    updated = [publication(w).envelope.last_updated for w in statuses(items)]
    # last_updated keeps going up across the boundary.
    assert updated == sorted(set(updated)) and len(updated) >= 7


def test_contract_scenario_breaks_station_status_20_s_after_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="cycleops.worker.sources"):
        items, source, _ = run_synthetic(4, scenario="contract_quarantine")
    status = statuses(items)
    before = [w for w in status if w.attempt.requested_at < START + timedelta(seconds=20)]
    after = [w for w in status if w.attempt.requested_at >= START + timedelta(seconds=20)]
    assert before and all(w.outcome.ok for w in before)
    # One attempt, then the quarantine probe is 300 s away.
    [broken] = after
    failed = broken.input
    assert isinstance(failed, PublicationFailed)
    assert (failed.error_class, failed.outcome) == ("contract", "invalid")
    assert failed.quarantined_since == broken.attempt.received_at
    assert "version" in (broken.outcome.detail or "")
    [record] = caplog.records
    assert record.levelname == "ERROR"
    assert record.__dict__["fingerprint"] == source.guards["station_status"].fingerprint
    assert record.__dict__["feed"] == "station_status"


def test_synthetic_source_does_not_poll_while_ingestion_is_suspended() -> None:
    items, _, watchdog = run_synthetic(3, suspended=lambda: True)
    assert items == []
    # Waiting on the quota still counts as progress.
    assert "source" in watchdog._seen


def test_synthetic_source_resumes_after_the_suspension() -> None:
    holder: list[SyntheticSource] = []

    def suspended() -> bool:
        return holder[0]._clock.now() < START + timedelta(seconds=90) if holder else True

    clock = VirtualTime(START)

    async def go() -> list[Work]:
        queue: Queue = asyncio.Queue()
        watchdog = Watchdog(Path("unused"), 5, sleep=clock.sleep)
        source = SyntheticSource(
            SYNTHETIC, 42, START, stream(), 0, queue, suspended, watchdog, clock=clock
        )
        holder.append(source)
        await clock.run(source.run(), START + timedelta(minutes=3))
        return drain(queue)

    items = asyncio.run(go())
    assert items
    assert min(w.attempt.requested_at for w in items) >= START + timedelta(seconds=90)


def test_restored_quarantine_delays_the_first_probe() -> None:
    last = START - timedelta(seconds=100)
    state = stream(quarantined_since=last, last_attempt_at=last)
    items, _, _ = run_synthetic(6, state=state)
    first = statuses(items)[0]
    # last attempt + quarantineProbeSeconds (300), not at start.
    assert first.attempt.requested_at == last + timedelta(seconds=300)


def test_restore_guards_from_the_stream() -> None:
    config = OSLO.provider.collection
    guards = {
        name: FeedGuard(config, use_breaker=name == "station_status")
        for name in ("gbfs", "station_information", "station_status")
    }
    state = stream(
        breaker_open_until=START + timedelta(seconds=50),
        last_attempt_at=START - timedelta(seconds=10),
        quarantined_feeds={"station_information", "vehicle_types"},
        clock_now=START,
    )
    restore_guards(guards, state, 3)
    status = guards["station_status"]
    assert (status.failures, status.breaker_open_until) == (3, START + timedelta(seconds=50))
    assert status.next_allowed_at(START) == START + timedelta(seconds=50)
    info = guards["station_information"]
    assert info.quarantined_since == START
    assert info.next_allowed_at(START) == START + timedelta(seconds=300)
    assert guards["gbfs"].quarantined_since is None


class Feed:
    """httpx handler for the Oslo URLs: answers per feed, counts requests."""

    def __init__(self, answers: dict[str, Callable[[], httpx.Response]]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, datetime]] = []
        self.clock: VirtualTime | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        feed = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        assert self.clock is not None
        self.requests.append((feed, self.clock.now()))
        return self.answers.get(feed, lambda: httpx.Response(503))()


def run_live(
    feed: Feed,
    minutes: float,
    *,
    start: datetime = START,
    state: StreamState | None = None,
    breaker_failures: int = 0,
    suspended: Callable[[], bool] = lambda: False,
    archive: ArchiveWriter | None = None,
    started: dict[str, datetime] | None = None,
    during: Callable[[LiveSource, VirtualTime], Coroutine[Any, Any, None]] | None = None,
    stops: Sequence[datetime] = (),
) -> list[Work]:
    """Runs one LiveSource for `minutes` from `start`. At each instant in `stops` its run()
    is cancelled and started again, as a new leadership term does."""
    clock = VirtualTime(start)
    feed.clock = clock

    async def go() -> list[Work]:
        queue: Queue = asyncio.Queue()
        watchdog = Watchdog(Path("unused"), 5, sleep=clock.sleep)
        source = LiveSource(
            OSLO,
            "test-cycleops",
            state or stream(),
            breaker_failures,
            queue,
            suspended,
            watchdog,
            archive,
            started=started,
            transport=httpx.MockTransport(feed),
            clock=clock,
        )

        async def body() -> None:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(source.run())
                if during is not None:
                    tg.create_task(during(source, clock))

        for end in (*stops, start + timedelta(minutes=minutes)):
            await clock.run(body(), end)
        await source.aclose()
        return drain(queue)

    return asyncio.run(go())


def test_live_source_through_ten_minutes_of_503_breaks_and_keeps_going() -> None:
    feed = Feed({})
    items = run_live(feed, 10)
    status = statuses(items)
    assert all(
        isinstance(w.input, PublicationFailed) and w.input.error_class == "transport"
        for w in status
    )
    assert [w.breaker_failures for w in status[:4]] == [1, 2, 3, 4]
    third = status[2].input
    assert isinstance(third, PublicationFailed)
    assert third.breaker_open_until == status[2].attempt.received_at + timedelta(seconds=60)
    # After the breaker opens, one half-open trial per breakerOpenSeconds.
    starts = [w.attempt.requested_at for w in status]
    assert all(b - a >= timedelta(seconds=60) for a, b in itertools.pairwise(starts[2:]))
    assert 10 <= len(status) <= 12
    # The discovery feeds went once each, at the start (refresh every hour).
    assert sum(1 for f, _ in feed.requests if f == "gbfs") <= 2


def ok_feed() -> Feed:
    return Feed(
        {
            "gbfs": lambda: httpx.Response(200, json=discovery_body(DEFAULT_URLS)),
            "station_information": lambda: httpx.Response(200, json=info_body()),
            "station_status": lambda: httpx.Response(200, json=status_body([station()])),
        }
    )


def test_live_source_makes_no_request_while_ingestion_is_suspended() -> None:
    feed = ok_feed()
    assert run_live(feed, 3, suspended=lambda: True) == []
    assert feed.requests == []


def test_live_source_respects_the_restored_breaker() -> None:
    feed = ok_feed()
    state = stream(breaker_open_until=START + timedelta(seconds=50))
    items = run_live(feed, 2, state=state, breaker_failures=3)
    first = next(t for f, t in feed.requests if f == "station_status")
    assert first == START + timedelta(seconds=50)
    assert statuses(items)[0].breaker_failures == 0


def test_live_source_fetches_the_catalog_once_more_on_request_and_archives_every_attempt(
    tmp_path: Path,
) -> None:
    feed = ok_feed()
    archive = ArchiveWriter(tmp_path)

    async def ask(source: LiveSource, clock: VirtualTime) -> None:
        await clock.sleep(30)
        source.request_catalog()

    items = run_live(feed, 2, archive=archive, during=ask)
    archive.close()
    info = [t for f, t in feed.requests if f == "station_information"]
    assert info[0] == START and len(info) == 2 and info[1] >= START + timedelta(seconds=30)
    lines = [line for path in sorted(tmp_path.iterdir()) for line in read_recording(path)]
    assert len(lines) == len(items) == len(feed.requests)
    assert {line["feed"] for line in lines} == {
        "gbfs",
        "system_information",
        "vehicle_types",
        "station_information",
        "station_status",
    }
    assert all(line["recordingSchema"] == 1 for line in lines)
    # status_body has last_updated T0 in 2026-10-07: accepted by the adapter, mapped to input.
    assert any(isinstance(w.input, PublicationReceived) for w in statuses(items))


def requested(feed: Feed, name: str) -> list[datetime]:
    return [t for f, t in feed.requests if f == name]


def test_live_source_run_again_goes_on_with_the_same_cadence() -> None:
    feed = ok_feed()
    # Two leadership terms: the second one fetches nothing that is not due.
    run_live(feed, 2, stops=[START + timedelta(seconds=40)])
    for name in ("gbfs", "system_information", "vehicle_types", "station_information"):
        assert requested(feed, name) == [START], name
    status = requested(feed, "station_status")
    assert len(status) >= 7
    assert all(b - a >= timedelta(seconds=15) for a, b in itertools.pairwise(status))


def test_live_source_after_a_quota_suspension_waits_a_full_interval() -> None:
    feed = ok_feed()
    resume = START + timedelta(seconds=120)
    run_live(feed, 4, suspended=lambda: feed.clock is not None and feed.clock.now() < resume)
    status = requested(feed, "station_status")
    assert status[0] == resume
    assert all(b - a >= timedelta(seconds=15) for a, b in itertools.pairwise(status)), status
    assert requested(feed, "gbfs") == [resume]


def test_a_restarted_live_source_waits_from_the_archived_attempts(tmp_path: Path) -> None:
    first = Feed(
        {
            **ok_feed().answers,
            "station_status": lambda: httpx.Response(429, headers={"Retry-After": "120"}),
        }
    )
    archive = ArchiveWriter(tmp_path)
    run_live(first, 0.25, archive=archive)
    archive.close()
    assert {f for f, _ in first.requests} == set(DEFAULT_URLS)

    # The next process starts 20 s later and reads the same archive.
    second = ok_feed()
    archive = ArchiveWriter(tmp_path)
    run_live(second, 4, start=START + timedelta(seconds=20), archive=archive)
    archive.close()
    # Retry-After 120 s from the 429, then the poll interval.
    status = requested(second, "station_status")
    assert status[:2] == [START + timedelta(seconds=120), START + timedelta(seconds=135)]
    assert {f for f, _ in second.requests} == {"station_status"}


def test_a_restarted_live_source_waits_from_feed_publications() -> None:
    feed = ok_feed()
    started = {
        "gbfs": START - timedelta(minutes=10),
        "system_information": START - timedelta(minutes=10),
        "vehicle_types": START - timedelta(minutes=10),
        "station_information": START - timedelta(minutes=1),
        "station_status": START - timedelta(seconds=5),
    }
    run_live(feed, 2, started=started)
    assert requested(feed, "station_status")[0] == START + timedelta(seconds=10)
    assert {f for f, _ in feed.requests} == {"station_status"}
