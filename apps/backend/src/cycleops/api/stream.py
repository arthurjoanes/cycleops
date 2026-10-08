"""GET /api/v1/stream/stations: the SSE connection protocol.

1. The cursor comes from Last-Event-ID, else ?after=, with its syntax and size checked.
2. The connection registers with the distributor, which buffers what the tailer hands out.
3. H = head_sequence is read in a short session.
4. Unknown stream, another generation or a cursor past H: resync_required and close.
   Always with 200: EventSource hides the status and stops reconnecting on 4xx.
5. More than MAX_CATCH_UP events behind, or out of the retention: resync_required.
6. Catch-up from event_log up to H, one short session per page.
7. The buffer above H, dropping what was already written; then live.

Heartbeats come from the tailer's head. When the tailer has not read the database for
HEAD_MAX_AGE_SECONDS, its feedState can no longer be vouched for: the stream ends without a
message, like on any database error, and the browser reconnects.

No database session outlives a read. Control messages never carry id: (an empty one would
reset the browser's lastEventId).
"""

import asyncio
import logging
import math
import re
import statistics
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Request
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncEngine
from sse_starlette import EventSourceResponse, ServerSentEvent

from cycleops.adapters.postgres import queries
from cycleops.api.tailer import PAGE, POLL_SECONDS, Client, End, Frame, Hub, Tailer, frame
from cycleops.contracts import Contract, Heartbeat, ResyncRequired, StreamClosed

log = logging.getLogger("cycleops.api.stream")

PATH = "/api/v1/stream/stations"
# Roughly 10 minutes of events; further behind, a snapshot is cheaper than the catch-up.
MAX_CATCH_UP = 40
# The browser's reconnection delay after stream_closed (shutdown).
RETRY_MS = 2000
# Under uvicorn's graceful timeout (10 s): after it, sse-starlette cancels the response.
SHUTDOWN_GRACE_SECONDS = 5
# Connection errors end the response without a message; EventSource reconnects by itself.
DB_DOWN = (OperationalError, InterfaceError, PoolTimeout)
# The tailer polls every second. A heartbeat names a head at most this old: the stale rule
# ran at its poll, so feedState lags the threshold by this much at most.
HEAD_MAX_AGE_SECONDS = 3 * POLL_SECONDS

# "<system_id>:<8 hex>@<sequence>": at most 92 characters, under the contract's 128.
_CURSOR = re.compile(
    r"(?P<stream>[A-Za-z0-9_-]{1,64}:[0-9a-f]{8})@(?P<sequence>0|[1-9][0-9]{0,17})"
)
MAX_CURSOR = 128

Reason = End | Literal["cursor_expired", "cursor_ahead", "gap"]


class TailerBehind(Exception):
    """The tailer's head is too old for a heartbeat."""


@dataclass(frozen=True)
class Cursor:
    stream_id: str
    sequence: int


def parse_cursor(value: str) -> Cursor | None:
    match = _CURSOR.fullmatch(value) if len(value) <= MAX_CURSOR else None
    return Cursor(match["stream"], int(match["sequence"])) if match else None


def message(event: str, payload: Contract, retry: int | None = None) -> bytes:
    """A control message (heartbeat, resync_required, stream_closed): never an id:."""
    return ServerSentEvent(payload.model_dump_json(), event=event, retry=retry, sep="\n").encode()


class Latency:
    """Commit-to-write delay of the station_batch messages written, the last 10 000."""

    def __init__(self, size: int = 10_000) -> None:
        self.samples: deque[float] = deque(maxlen=size)

    def add(self, committed_at: datetime) -> None:
        # committedAt is the database clock; same host in Compose.
        self.samples.append((datetime.now(UTC) - committed_at).total_seconds() * 1000)

    def summary(self) -> dict[str, float] | None:
        if not self.samples:
            return None
        cuts = statistics.quantiles(self.samples, n=100, method="inclusive")
        return {
            "count": len(self.samples),
            "p50": round(cuts[49], 1),
            "p95": round(cuts[94], 1),
            "max": round(max(self.samples), 1),
        }


async def report_latency(latency: Latency, every: float = 60) -> None:
    while True:
        await asyncio.sleep(every)
        summary = latency.summary()
        if summary is not None:
            log.info("commit to write", extra={"latency_commit_to_write_ms": summary})


async def stream_stations(request: Request) -> EventSourceResponse:
    session = _Session(request)
    raw = request.headers.get("last-event-id") or request.query_params.get("after")
    ip = request.client.host if request.client else "unknown"
    return EventSourceResponse(
        session.run(raw, ip),
        headers={"Cache-Control": "no-store, no-transform"},
        # Heartbeats are ours (named, with data); no comment pings.
        ping=0,
        sep="\n",
        shutdown_grace_period=SHUTDOWN_GRACE_SECONDS,
    )


class _Session:
    """One connection. `last` is the client's position: its cursor, then each event
    written. It is also the lastSequence of resync_required (None: unknown)."""

    def __init__(self, request: Request) -> None:
        state = request.app.state
        self.hub: Hub = state.hub
        self.tailer: Tailer = state.tailer
        self.engine: AsyncEngine = state.engine
        self.latency: Latency = state.latency
        self.heartbeat_seconds: float = state.settings.heartbeat_seconds
        # time.monotonic() once this connection has read the head (step 3).
        self.read_at = math.inf
        self.stream_id: str | None = None
        self.last: int | None = None
        self.end: Reason | None = None
        self.count = 0
        # Read by the access log when the response ends.
        self.fields: dict[str, Any] = {}
        request.state.log = self.fields

    async def run(self, raw: str | None, ip: str) -> AsyncIterator[bytes]:
        client: Client | None = None
        try:
            cursor = parse_cursor(raw) if raw else None
            if raw and cursor is None:
                # Checked before registering: a malformed request takes no slot and
                # supersedes nobody.
                self.end = "unknown_stream"
            else:
                client = self.hub.register(ip)
                async for data in self._protocol(cursor, client):
                    yield data
            if self.end is not None:
                yield self._control(self.end)
        except DB_DOWN as exc:
            log.warning("stream ended, database unavailable", extra={"error": type(exc).__name__})
            self.end = None
            self.fields["error"] = "database_unavailable"
        except TailerBehind:
            log.warning("stream ended, the tailer has not read the database")
            self.fields["error"] = "tailer_behind"
        finally:
            if client is not None:
                self.hub.unregister(client)
            self.fields.update(
                streamId=self.stream_id,
                sequence=self.last,
                count=self.count,
                outcome=self.end or self.fields.get("error", "disconnected"),
            )

    def _control(self, end: Reason) -> bytes:
        match end:
            case "superseded" | "capacity" | "shutdown":
                retry = RETRY_MS if end == "shutdown" else None
                return message("stream_closed", StreamClosed(reason=end), retry)
            case _:
                return message(
                    "resync_required", ResyncRequired(reason=end, last_sequence=self.last)
                )

    async def _protocol(self, cursor: Cursor | None, client: Client) -> AsyncIterator[bytes]:
        if client.end is not None:
            self.end = client.end
            return
        async with self.engine.connect() as conn:
            head = await queries.head(conn)
        self.read_at = time.monotonic()
        if head is None or (cursor is not None and cursor.stream_id != head.stream_id):
            self.end = "unknown_stream"
            return
        self.stream_id = client.stream_id = head.stream_id
        self.last = cursor.sequence if cursor is not None else head.head_sequence
        if self.last > head.head_sequence:
            self.end = "cursor_ahead"
            return
        async for data in self._fill(head.head_sequence):
            yield data
        if self.end is None:
            async for data in self._live(client):
                yield data

    async def _fill(self, upto: int) -> AsyncIterator[bytes]:
        """Events (last, upto] from event_log: the catch-up, and any gap in what the
        tailer handed out (it started later, or switched streams)."""
        assert self.stream_id is not None and self.last is not None
        if upto - self.last > MAX_CATCH_UP:
            self.end = "cursor_expired"
            return
        while self.last < upto:
            async with self.engine.connect() as conn:
                rows = await queries.events_after(
                    conn, self.stream_id, self.last, limit=min(PAGE, upto - self.last)
                )
            if not rows or rows[0]["sequence"] != self.last + 1:
                self.end = "cursor_expired"
                return
            for f in map(frame, rows):
                if f.data is None:
                    self.end = "gap"
                    return
                yield f.data
                self._written(f, live=False)

    async def _live(self, client: Client) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        beat = loop.time() + self.heartbeat_seconds
        while True:
            while client.frames and client.end is None:
                f = client.take()
                assert self.last is not None
                if f.stream_id != self.stream_id or f.sequence <= self.last:
                    continue
                if f.sequence > self.last + 1:
                    async for data in self._fill(f.sequence - 1):
                        yield data
                    if self.end is not None:
                        return
                if f.data is None:
                    self.end = "gap"
                    return
                yield f.data
                self._written(f, live=True)
            if client.end is not None:
                self.end = client.end
                return
            # No await between the empty check and the wait: no wake-up is lost.
            client.wake.clear()
            try:
                async with asyncio.timeout_at(beat):
                    await client.wake.wait()
            except TimeoutError:
                beat = loop.time() + self.heartbeat_seconds
                head = self._tailer_head()
                if self.end is not None:
                    return
                if head is None:
                    continue
                assert self.last is not None
                if head.head_sequence > self.last and not client.frames:
                    # The tailer started at a head this connection had not reached (its
                    # first poll, or a new stream), so no event brings what lies between:
                    # read it before a heartbeat names that head.
                    async for data in self._fill(head.head_sequence):
                        yield data
                    if self.end is not None:
                        return
                yield self._heartbeat(head)

    def _tailer_head(self) -> queries.Head | None:
        """The tailer's head when it is on this connection's stream, else None."""
        polled_at = self.tailer.polled_at
        if time.monotonic() - polled_at > HEAD_MAX_AGE_SECONDS:
            raise TailerBehind
        head = self.tailer.head
        if head is None or head.stream_id == self.stream_id:
            return head
        # Another stream is unknown_stream only when the tailer read it after this
        # connection read its own. Before that, the tailer is the one behind (a reset
        # while it reconnects) and the heartbeat waits for it.
        if polled_at > self.read_at:
            self.end = "unknown_stream"
        return None

    def _heartbeat(self, head: queries.Head) -> bytes:
        return message(
            "heartbeat",
            Heartbeat(
                server_time=datetime.now(UTC),
                stream_id=head.stream_id,
                head_sequence=head.head_sequence,
                last_source_updated_at=head.feed_health.last_source_updated_at,
                feed_state=head.feed_health.state,
                state_since=head.feed_health.state_since,
            ),
        )

    def _written(self, f: Frame, live: bool) -> None:
        # Runs when the generator resumes, i.e. after the message went to the socket.
        self.last = f.sequence
        self.count += 1
        # Catch-up events are late by design; only live delivery is measured.
        if live:
            self.latency.add(f.committed_at)
