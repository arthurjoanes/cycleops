"""Polling loop shared by record-feed and the worker.

One task per feed, each sequential, plus a lock per feed for the inline rediscovery
after a 404: never more than one request in flight per feed.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta

from cycleops.adapters.gbfs import StatusPublication
from cycleops.clock import Clock, SystemClock
from cycleops.collection.client import DEFAULT_URLS, FEEDS, Attempt, FeedClient
from cycleops.collection.guard import FeedGuard, next_status_at
from cycleops.collection.outcome import Outcome, classify_attempt
from cycleops.policy import ProviderConfig

log = logging.getLogger(__name__)

AttemptHandler = Callable[[Attempt, Outcome], None]
Sleep = Callable[[float], Awaitable[None]]
SEMANTICS_WARNING_INTERVAL = timedelta(hours=1)


class Collector:
    def __init__(
        self,
        client: FeedClient,
        provider: ProviderConfig,
        on_attempt: AttemptHandler,
        *,
        clock: Clock | None = None,
        sleep: Sleep = asyncio.sleep,
        gate: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._provider = provider
        self._config = provider.collection
        self._on_attempt = on_attempt
        self._clock = clock or SystemClock()
        self._sleep = sleep
        self._locks = {feed: asyncio.Lock() for feed in FEEDS}
        self.urls = dict(DEFAULT_URLS)
        self.guards = {
            feed: FeedGuard(self._config, use_breaker=feed == "station_status") for feed in FEEDS
        }
        self.last_ok: dict[str, Outcome] = {}
        # Start of each feed's last scheduled request. On the instance, so a run() started
        # again (a new leadership term) or seeded from a previous process goes on with the
        # same cadence instead of fetching every feed at once.
        self.last_start: dict[str, datetime] = {}
        # Awaited before each scheduled request (the worker's quota).
        self._gate = gate
        self._extra_fields_seen: dict[str, frozenset[str]] = {}
        self._semantics_warned_at: datetime | None = None

    async def run(self, until: datetime | None = None, feeds: Iterable[str] = FEEDS) -> None:
        """Poll until `until` (forever with None)."""
        async with asyncio.TaskGroup() as tg:
            for feed in feeds:
                tg.create_task(self._loop(feed, until))

    async def fetch(self, feed: str) -> Outcome:
        async with self._locks[feed]:
            attempt = await self._client.fetch(feed, self.urls[feed])
            outcome = classify_attempt(attempt, self._provider.station_report_semantics)
            event = self.guards[feed].record(feed, outcome, attempt.received_at)
            if outcome.ok:
                self.last_ok[feed] = outcome
                if feed == "gbfs" and isinstance(outcome.publication, dict):
                    self.urls.update(outcome.publication)
            self._on_attempt(attempt, outcome)
            self._log(attempt, outcome, event)
        if event == "rediscover":
            if feed != "gbfs":
                await self.rediscover()
            return await self.fetch(feed)
        if event == "quarantined" and outcome.status != 404 and feed != "gbfs":
            await self.rediscover()
        return outcome

    async def rediscover(self) -> None:
        now = self._clock.now()
        if self.guards["gbfs"].next_allowed_at(now) <= now:
            await self.fetch("gbfs")

    async def _loop(self, feed: str, until: datetime | None) -> None:
        while True:
            previous = self.last_start.get(feed)
            due = self._clock.now() if previous is None else self._cadence(feed, previous)
            due = self.guards[feed].next_allowed_at(due)
            if until is not None and due >= until:
                return
            delay = (due - self._clock.now()).total_seconds()
            if delay > 0:
                await self._sleep(delay)
            if self._gate is not None:
                # Before the start is taken: after a long wait here the next request still
                # comes a full interval after this one, not right behind it.
                await self._gate()
            self.last_start[feed] = self._clock.now()
            await self.fetch(feed)

    def _cadence(self, feed: str, previous_start: datetime) -> datetime:
        if feed == "station_status":
            last = self.last_ok.get(feed)
            return next_status_at(previous_start, last.envelope if last else None, self._config)
        if feed == "station_information":
            return previous_start + timedelta(seconds=self._config.catalog_refresh_seconds)
        return previous_start + timedelta(seconds=self._config.discovery_refresh_seconds)

    def _log(self, attempt: Attempt, outcome: Outcome, event: str | None) -> None:
        feed = attempt.feed
        fields = {
            "feed": feed,
            "outcome": outcome.code,
            "status": outcome.status,
            "duration_ms": attempt.duration_ms,
        }
        guard = self.guards[feed]
        if event == "quarantined" or (
            guard.quarantined_since and outcome.error_class == "contract"
        ):
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
        publication = outcome.publication
        if isinstance(publication, StatusPublication):
            self._check_publication(feed, publication, attempt.received_at)

    def _check_publication(self, feed: str, pub: StatusPublication, at: datetime) -> None:
        extra = frozenset(pub.extra_fields)
        if extra and extra != self._extra_fields_seen.get(feed):
            log.info("%s fields outside GBFS 2.3, ignored: %s", feed, pub.extra_fields)
        self._extra_fields_seen[feed] = extra
        semantics = self._provider.station_report_semantics
        mismatch = (semantics == "feed_generation" and pub.distinct_last_reported > 1) or (
            semantics == "per_station" and pub.distinct_last_reported == 1 and len(pub.stations) > 1
        )
        warned = self._semantics_warned_at
        if mismatch and (warned is None or at - warned >= SEMANTICS_WARNING_INTERVAL):
            self._semantics_warned_at = at
            log.warning(
                "last_reported has %d distinct values in one publication, configured as %s",
                pub.distinct_last_reported,
                semantics,
            )
