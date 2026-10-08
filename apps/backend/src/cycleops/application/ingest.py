"""The worker's write side: one transaction per entry, on the lock connection.

1. lock the stream_heads row (FOR UPDATE) before reading any stream state;
2. read the state (the cached one, if the head still matches it);
3. run step();
4. write the state that changed and the attempt's feed_publications row;
5. per event: event_log with sequence = head + 1 and committed_at = clock_timestamp(),
   its station_observations, and pg_notify with the cursor;
6. commit. Any error rolls back and drops the cached state; the next call reloads it.
"""

import hashlib
import logging
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops.adapters.gbfs import InfoPublication, StatusPublication
from cycleops.adapters.postgres.connect import NOTIFY_CHANNEL
from cycleops.adapters.postgres.state import StateWriter
from cycleops.adapters.postgres.tables import (
    feed_publications,
    station_observations,
    stream_heads,
)
from cycleops.canonical import sha256_hex
from cycleops.collection.client import Attempt
from cycleops.collection.outcome import Outcome
from cycleops.contracts import DataMode, StationBatchEvent, StationState
from cycleops.domain.engine import new_stream, step
from cycleops.domain.model import ClockTick, Input, PublicationReceived, StreamState
from cycleops.policy import Policy

log = logging.getLogger(__name__)

ERROR_CODE_MAX = 200

# committed_at and the payload's committedAt are the same instant, cut to the contract's
# millisecond precision, in one statement. Colons in the format are escaped for text().
_APPEND = text(
    r"""
    WITH t AS (SELECT date_trunc('milliseconds', clock_timestamp()) AS at)
    INSERT INTO event_log (stream_id, sequence, event_type, payload, committed_at)
    SELECT :stream_id, :sequence, 'station_batch',
           jsonb_set(
               CAST(:payload AS jsonb), '{committedAt}',
               to_jsonb(to_char(t.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24\:MI\:SS.MS"Z"'))
           ),
           t.at
    FROM t
    RETURNING committed_at
    """
)


class StreamMismatch(Exception):
    """The database holds a stream of another mode, system or seed."""


class Ingestor:
    """One stream's processor: the cached StreamState and what was last persisted."""

    def __init__(
        self, policy: Policy, stream_id: str, created_at: datetime, breaker_failures: int = 0
    ) -> None:
        self.policy = policy
        self.stream_id = stream_id
        # The synthetic source anchors its scenario window here.
        self.created_at = created_at
        # Collector guard state stored by the last run, to restore on boot.
        self.breaker_failures = breaker_failures
        # None: invalid, reloaded from the database on the next call.
        self.state: StreamState | None = None
        self._writer = StateWriter(policy.priority.source_stale_after_seconds)

    async def process(
        self,
        conn: AsyncConnection,
        entry: Input | None,
        attempt: Attempt | None = None,
        outcome: Outcome | None = None,
        breaker_failures: int | None = None,
    ) -> list[StationBatchEvent]:
        """One entry in one transaction. `entry` is None for an attempt that makes no
        engine input (discovery feeds, 304). Returns the committed events, with the
        committedAt the database gave them. `conn` must have no transaction open."""
        try:
            async with conn.begin():
                return await self._process(conn, entry, attempt, outcome, breaker_failures)
        except BaseException:
            self.state = None
            raise

    async def _process(
        self,
        conn: AsyncConnection,
        entry: Input | None,
        attempt: Attempt | None,
        outcome: Outcome | None,
        breaker_failures: int | None,
    ) -> list[StationBatchEvent]:
        head = (
            await conn.execute(
                select(stream_heads.c.head_sequence, stream_heads.c.clock_now)
                .where(stream_heads.c.stream_id == self.stream_id)
                .with_for_update()
            )
        ).one()
        state = self.state
        if state is None or (state.head_sequence, state.clock_now) != tuple(head):
            state = self.state = await self._writer.load(conn, self.stream_id)
        events = step(state, entry, self.policy)[1] if entry is not None else []
        await self._writer.save(conn, state, events)

        extra: dict[str, Any] = {}
        if isinstance(entry, ClockTick):
            # The tick's own instant, so the API's stale rule and the engine after a restart
            # put the start of stale at the same instant.
            extra["worker_seen_at"] = state.last_tick_at
        if breaker_failures is not None:
            extra["breaker_failures"] = breaker_failures
            self.breaker_failures = breaker_failures
        if extra:
            await conn.execute(
                update(stream_heads).where(stream_heads.c.stream_id == self.stream_id).values(extra)
            )
        if attempt is not None and outcome is not None:
            # duplicate, correction, out_of_order and future_timestamp are the engine's call.
            code = state.last_outcome if isinstance(entry, PublicationReceived) else None
            await conn.execute(
                insert(feed_publications).values(
                    publication_row(self.stream_id, attempt, outcome, code)
                )
            )
        return [await _append(conn, event) for event in events]


async def create_or_load_stream(
    conn: AsyncConnection, system_id: str, mode: DataMode, seed: int | None, policy: Policy
) -> Ingestor:
    """The active stream (the most recently created) if it is of this mode, system and
    seed; a new one when there is none. Run with the collector lock held."""
    async with conn.begin():
        head = (
            await conn.execute(
                select(
                    stream_heads.c.stream_id,
                    stream_heads.c.system_id,
                    stream_heads.c.mode,
                    stream_heads.c.seed,
                    stream_heads.c.created_at,
                    stream_heads.c.breaker_failures,
                )
                .order_by(stream_heads.c.created_at.desc(), stream_heads.c.stream_id.desc())
                .limit(1)
            )
        ).first()
        if head is not None and (head.system_id, head.mode, head.seed) != (system_id, mode, seed):
            raise StreamMismatch(
                f"the database holds stream {head.stream_id} (mode {head.mode}, system "
                f"{head.system_id}, seed {head.seed}); this worker runs mode {mode}, system "
                f"{system_id}, seed {seed}. Use another compose project name or down -v."
            )
        if head is not None:
            ingestor = Ingestor(policy, head.stream_id, head.created_at, head.breaker_failures)
            ingestor.state = await ingestor._writer.load(conn, head.stream_id)
            return ingestor
        stream_id = f"{system_id}:{secrets.token_hex(4)}"
        state = new_stream(system_id, mode, stream_id, seed)
        writer = StateWriter(policy.priority.source_stale_after_seconds)
        await writer.save(conn, state, [])
        created_at = (
            await conn.execute(
                update(stream_heads)
                .where(stream_heads.c.stream_id == stream_id)
                .values(worker_seen_at=func.now())
                .returning(stream_heads.c.created_at)
            )
        ).scalar_one()
        ingestor = Ingestor(policy, stream_id, created_at)
        ingestor.state, ingestor._writer = state, writer
        log.info("stream created", extra={"streamId": stream_id, "mode": mode})
        return ingestor


def publication_row(
    stream_id: str, attempt: Attempt, outcome: Outcome, engine_outcome: str | None = None
) -> dict[str, Any]:
    pub = outcome.publication
    body = attempt.body_bytes
    station_count = valid_count = distinct = None
    if isinstance(pub, StatusPublication):
        station_count, valid_count = pub.record_count, len(pub.stations)
        distinct = pub.distinct_last_reported
    elif isinstance(pub, InfoPublication):
        station_count, valid_count = len(pub.stations) + pub.invalid_count, len(pub.stations)
    error = (outcome.detail or outcome.code) if outcome.error_class else None
    return {
        "stream_id": stream_id,
        "attempt_at": attempt.requested_at,
        "feed": attempt.feed,
        "http_status": attempt.status,
        "outcome": engine_outcome or outcome.code,
        "source_updated_at": outcome.envelope.last_updated if outcome.envelope else None,
        "received_at": attempt.received_at,
        "bytes": len(body) if body is not None else None,
        "duration_ms": attempt.duration_ms,
        "payload_sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
        "station_count": station_count,
        "valid_count": valid_count,
        "distinct_last_reported": distinct,
        "error_code": error[:ERROR_CODE_MAX] if error else None,
    }


async def _append(conn: AsyncConnection, event: StationBatchEvent) -> StationBatchEvent:
    at = (
        await conn.execute(
            _APPEND,
            {
                "stream_id": event.stream_id,
                "sequence": event.sequence,
                "payload": event.model_dump_json(),
            },
        )
    ).scalar_one()
    if event.stations:
        await conn.execute(
            insert(station_observations), [_observation(event, s, at) for s in event.stations]
        )
    await conn.execute(
        text("SELECT pg_notify(:channel, :cursor)"),
        {"channel": NOTIFY_CHANNEL, "cursor": event.event_id},
    )
    return event.model_copy(update={"committed_at": at})


def _observation(event: StationBatchEvent, station: StationState, at: datetime) -> dict[str, Any]:
    a, d = station.availability, station.decision
    types = [(v.vehicle_type_id, v.count) for v in a.vehicle_types]
    # The station's normalized record, without last_updated and last_reported.
    record: list[object] = [a.station_id, a.is_installed, a.is_renting, a.is_returning]
    record += [a.bikes_available, a.docks_available, types]
    return {
        "stream_id": event.stream_id,
        "station_id": a.station_id,
        "sequence": event.sequence,
        "source_updated_at": a.source_updated_at,
        "committed_at": at,
        "bikes_available": a.bikes_available,
        "docks_available": a.docks_available,
        "vehicle_types": [{"vehicleTypeId": t, "count": c} for t, c in types],
        "quality": a.quality,
        "quality_flags": a.quality_flags,
        "primary_condition": d.primary_condition,
        "conditions": d.conditions,
        "content_hash": sha256_hex(record),
    }
