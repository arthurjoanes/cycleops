import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import T0, FakeClock, discovery_body, status_body

from cycleops.adapters.gbfs import Envelope
from cycleops.collection.client import DEFAULT_URLS, Attempt, FeedClient
from cycleops.collection.collector import Collector
from cycleops.collection.guard import FeedGuard, next_status_at, poll_interval_seconds
from cycleops.collection.outcome import Outcome
from cycleops.policy import ProviderConfig

START = datetime(2026, 10, 7, 8, 0, 3, tzinfo=UTC)
Handler = Callable[[httpx.Request], httpx.Response]


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def make_collector(
    provider: ProviderConfig, handler: Handler, clock: FakeClock | None = None
) -> tuple[Collector, list[tuple[str, datetime]], list[Outcome]]:
    clock = clock or FakeClock(START)
    client = FeedClient(
        "test-cycleops", provider.collection, transport=httpx.MockTransport(handler), clock=clock
    )
    requests: list[tuple[str, datetime]] = []
    outcomes: list[Outcome] = []

    def on_attempt(attempt: Attempt, outcome: Outcome) -> None:
        requests.append((attempt.feed, attempt.requested_at))
        outcomes.append(outcome)

    collector = Collector(client, provider, on_attempt, clock=clock, sleep=clock.sleep)
    return collector, requests, outcomes


def run_status_loop(provider: ProviderConfig, handler: Handler, seconds: float) -> list[datetime]:
    clock = FakeClock(START)
    collector, requests, _ = make_collector(provider, handler, clock)
    asyncio.run(collector.run(until=at(seconds), feeds=["station_status"]))
    return [t for feed, t in requests if feed == "station_status"]


def test_poll_interval_never_below_the_floor(provider: ProviderConfig) -> None:
    config = provider.collection
    assert poll_interval_seconds(config, None) == 15
    assert poll_interval_seconds(config, 0) == 15
    assert poll_interval_seconds(config, 5) == 15
    assert poll_interval_seconds(config, 30) == 30


def test_next_status_at_formula(provider: ProviderConfig) -> None:
    config = provider.collection
    published = at(-3)  # 08:00:00
    last = Envelope("2.3", published, 15)
    # max(start + 15, last_updated + ttl + margin) = max(08:00:18, 08:00:25)
    assert next_status_at(at(0), last, config) == at(22)
    # a late fetch: the poll interval wins
    assert next_status_at(at(30), last, config) == at(45)
    # no publication yet: poll interval only
    assert next_status_at(at(0), None, config) == at(15)
    # ttl 0: the floor still applies
    assert next_status_at(at(0), Envelope("2.3", published, 0), config) == at(15)
    # last_updated an hour in the future does not push polling out
    assert next_status_at(at(0), Envelope("2.3", at(3600), 15), config) == at(40)


def test_status_loop_follows_the_publications(provider: ProviderConfig) -> None:
    clock = FakeClock(START)

    def handler(request: httpx.Request) -> httpx.Response:
        # A publication every 15 s, each available 2 s after its last_updated.
        available = clock.now() - timedelta(seconds=2)
        last_updated = int(available.timestamp()) // 15 * 15
        return httpx.Response(200, json=status_body(last_updated=last_updated))

    collector, requests, _ = make_collector(provider, handler, clock)
    asyncio.run(collector.run(until=at(75), feeds=["station_status"]))
    # 08:00:03, then last_updated + ttl + margin: 08:00:25, :40, :55, 08:01:10
    assert [round((t - START).total_seconds()) for _, t in requests] == [0, 22, 37, 52, 67]


def test_one_request_in_flight_per_feed(provider: ProviderConfig) -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json=status_body())

    client = FeedClient(
        "test-cycleops", provider.collection, transport=httpx.MockTransport(handler)
    )
    collector = Collector(client, provider, lambda a, o: None)

    async def go() -> None:
        await asyncio.gather(*(collector.fetch("station_status") for _ in range(5)))

    asyncio.run(go())
    assert peak == 1


def test_breaker_opens_after_three_transport_failures_then_half_open(
    provider: ProviderConfig,
) -> None:
    times = run_status_loop(provider, lambda r: httpx.Response(503), seconds=200)
    seconds = [round((t - START).total_seconds()) for t in times]
    # 3 failures 15 s apart, open for 60 s, one half-open trial that fails, open again
    assert seconds == [0, 15, 30, 90, 150]


def test_breaker_state_machine(provider: ProviderConfig) -> None:
    guard = FeedGuard(provider.collection, use_breaker=True)
    fail = Outcome("timeout", "transport", None)
    ok = Outcome("ok", None, 200)
    for i in range(2):
        assert guard.record("station_status", fail, at(i)) is None
    assert guard.breaker_state(at(2)) == "closed"
    assert guard.record("station_status", fail, at(2)) == "breaker_opened"
    assert guard.breaker_state(at(30)) == "open"
    assert guard.next_allowed_at(at(17)) == at(62)
    assert guard.breaker_state(at(62)) == "half_open"
    assert guard.record("station_status", fail, at(62)) == "breaker_opened"
    assert guard.breaker_open_until == at(122)
    guard.record("station_status", ok, at(122))
    assert guard.breaker_state(at(122)) == "closed" and guard.failures == 0


def test_other_feeds_have_no_breaker(provider: ProviderConfig) -> None:
    guard = FeedGuard(provider.collection)
    for i in range(5):
        guard.record("station_information", Outcome("timeout", "transport", None), at(i))
    assert guard.breaker_open_until is None


def test_contract_error_quarantines_and_a_valid_probe_ends_it(provider: ProviderConfig) -> None:
    bodies = iter([status_body(version="3.0"), status_body(version="3.0")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("gbfs.json"):
            return httpx.Response(200, json=discovery_body(DEFAULT_URLS))
        return httpx.Response(200, json=next(bodies, status_body()))

    clock = FakeClock(START)
    collector, requests, _ = make_collector(provider, handler, clock)
    asyncio.run(collector.run(until=at(700), feeds=["station_status"]))
    status = [(t - START).total_seconds() for f, t in requests if f == "station_status"]
    # invalid at 0 -> probe at 300 (still invalid) -> probe at 600 is valid, back to cadence
    assert [round(s) for s in status[:3]] == [0, 300, 600]
    assert round(status[3]) == 615
    # the first contract error also refreshes gbfs.json, once
    assert [f for f, _ in requests].count("gbfs") == 1
    assert collector.guards["station_status"].quarantined_since is None


def test_404_rediscovers_then_quarantines(provider: ProviderConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("gbfs.json"):
            return httpx.Response(200, json=discovery_body(DEFAULT_URLS))
        return httpx.Response(404)

    collector, requests, _ = make_collector(provider, handler)
    asyncio.run(collector.fetch("station_status"))
    assert [f for f, _ in requests] == ["station_status", "gbfs", "station_status"]
    guard = collector.guards["station_status"]
    assert guard.quarantined_since is not None and guard.fingerprint is not None


def test_404_then_found_after_rediscovery(provider: ProviderConfig) -> None:
    responses = iter([httpx.Response(404), httpx.Response(200, json=status_body())])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("gbfs.json"):
            return httpx.Response(200, json=discovery_body(DEFAULT_URLS))
        return next(responses)

    collector, _, _ = make_collector(provider, handler)
    outcome = asyncio.run(collector.fetch("station_status"))
    assert outcome.ok
    assert collector.guards["station_status"].quarantined_since is None


def test_discovered_url_outside_allowlist_is_a_contract_error(provider: ProviderConfig) -> None:
    evil = dict(DEFAULT_URLS, station_status="https://evil.example/station_status.json")
    collector, _, _ = make_collector(
        provider, lambda r: httpx.Response(200, json=discovery_body(evil))
    )
    outcome = asyncio.run(collector.fetch("gbfs"))
    assert (outcome.code, outcome.error_class) == ("invalid", "contract")
    assert collector.urls == DEFAULT_URLS
    assert collector.guards["gbfs"].quarantined_since is not None


def test_retry_after_pushes_the_next_request(provider: ProviderConfig) -> None:
    responses = iter([httpx.Response(429, headers={"retry-after": "90"})])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, httpx.Response(200, json=status_body(last_updated=T0)))

    times = run_status_loop(provider, handler, seconds=100)
    assert [round((t - START).total_seconds()) for t in times] == [0, 90]


@pytest.mark.parametrize("feed", ["station_information", "system_information"])
def test_slow_feeds_follow_their_refresh(provider: ProviderConfig, feed: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    clock = FakeClock(START)
    collector, requests, _ = make_collector(provider, handler, clock)
    asyncio.run(collector.run(until=at(1300), feeds=[feed]))
    gap = provider.collection.catalog_refresh_seconds if feed == "station_information" else 3600
    expected = [s for s in (0, gap, 2 * gap) if s < 1300]
    assert [round((t - START).total_seconds()) for _, t in requests] == expected
