"""Breaker, quarantine and poll cadence for one feed.

Pure state machines: instants come in as arguments and nothing reads the wall clock, so
the worker, the CLIs and compile-replay (over recorded attempts) share the same rules.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from cycleops.adapters.gbfs import Envelope
from cycleops.collection.outcome import Outcome
from cycleops.policy import CollectionConfig

GuardEvent = Literal["breaker_opened", "rediscover", "quarantined", "quarantine_ended"]


@dataclass
class FeedGuard:
    config: CollectionConfig
    # Only station_status has a breaker.
    use_breaker: bool = False
    failures: int = 0
    breaker_open_until: datetime | None = None
    quarantined_since: datetime | None = None
    fingerprint: str | None = None
    retry_after_until: datetime | None = None
    last_attempt_at: datetime | None = None
    # A first 404 asks for rediscovery through gbfs.json; a second one in a row quarantines.
    rediscovering: bool = False

    def breaker_state(self, now: datetime) -> Literal["closed", "open", "half_open"]:
        if self.breaker_open_until is None:
            return "closed"
        return "open" if now < self.breaker_open_until else "half_open"

    def record(self, feed: str, outcome: Outcome, at: datetime) -> GuardEvent | None:
        """Apply one attempt; `at` is its receivedAt."""
        self.last_attempt_at = at
        self.retry_after_until = outcome.retry_after_until
        if outcome.error_class == "transport":
            if not self.use_breaker:
                return None
            self.failures += 1
            # breaker_open_until still set means this was the half-open trial.
            if self.breaker_open_until is not None or self.failures >= self.config.breaker_failures:
                self.breaker_open_until = at + timedelta(seconds=self.config.breaker_open_seconds)
                return "breaker_opened"
            return None
        # Any response that is not a transport failure means the transport works.
        self.failures = 0
        self.breaker_open_until = None
        if outcome.error_class is None:
            self.rediscovering = False
            ended = self.quarantined_since is not None and outcome.ok
            if ended:
                self.quarantined_since = None
                self.fingerprint = None
            return "quarantine_ended" if ended else None
        if outcome.status == 404 and not self.rediscovering and self.quarantined_since is None:
            self.rediscovering = True
            return "rediscover"
        self.rediscovering = False
        self.fingerprint = outcome.fingerprint(feed)
        if self.quarantined_since is None:
            self.quarantined_since = at
            return "quarantined"
        return None

    def next_allowed_at(self, due: datetime) -> datetime:
        """Push a due instant back for quarantine probes, an open breaker or Retry-After."""
        if self.quarantined_since is not None and self.last_attempt_at is not None:
            # In quarantine the probe cadence replaces the normal one.
            due = self.last_attempt_at + timedelta(seconds=self.config.quarantine_probe_seconds)
        if self.breaker_open_until is not None:
            due = max(due, self.breaker_open_until)
        if self.retry_after_until is not None:
            due = max(due, self.retry_after_until)
        return due


def poll_interval_seconds(config: CollectionConfig, ttl: int | None) -> int:
    # ttl 0 or missing falls back to the floor: a zero TTL never means a tight loop.
    return max(config.poll_interval_floor_seconds, ttl or 0)


def next_status_at(
    previous_start: datetime, last: Envelope | None, config: CollectionConfig
) -> datetime:
    """max(previous start + poll interval, last_updated + ttl + publication margin)."""
    ttl = last.ttl if last is not None else None
    due = previous_start + timedelta(seconds=poll_interval_seconds(config, ttl))
    if last is not None:
        # A last_updated in the future (operator clock ahead, bad file) must not push
        # polling out: a real one is never later than the previous start + deadline.
        updated = min(last.last_updated, due)
        due = max(due, updated + timedelta(seconds=last.ttl + config.publication_margin_seconds))
    return due
