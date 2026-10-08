"""The tailer, one per API process, and the in-memory distributor it feeds.

The tailer reads event_log from its own cursor on a dedicated LISTEN connection, outside
any transaction, every second and whenever a notification arrives. The notification only
wakes it, so a lost one costs at most a poll interval. The distributor hands each event to
every registered connection's bounded queue and applies the connection limits.
"""

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import psycopg
from sqlalchemy import NullPool
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sse_starlette import ServerSentEvent

from cycleops.adapters.postgres import queries
from cycleops.adapters.postgres.connect import KEEPALIVES, NOTIFY_CHANNEL, async_url

log = logging.getLogger("cycleops.api.tailer")

POLL_SECONDS = 1.0
PAGE = 100
# An event this large points to a configuration error; it is never written to a socket.
MAX_EVENT_BYTES = 2 * 1024 * 1024
# Past either bound a connection gets resync_required (queue_overflow) and closes.
MAX_QUEUE_MESSAGES = 200
MAX_QUEUE_BYTES = 4 * 1024 * 1024

# Why the distributor ended a connection: stream_closed for the first three,
# resync_required for the others.
End = Literal["superseded", "capacity", "shutdown", "queue_overflow", "unknown_stream"]


@dataclass(frozen=True)
class Frame:
    stream_id: str
    sequence: int
    committed_at: datetime
    # The SSE message, encoded once for every connection; None when over MAX_EVENT_BYTES.
    data: bytes | None

    @property
    def size(self) -> int:
        return len(self.data) if self.data is not None else 0


def frame(payload: dict[str, Any]) -> Frame:
    """A station_batch payload from event_log as an SSE message with id: (the cursor)."""
    encoded = ServerSentEvent(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        id=payload["eventId"],
        event="station_batch",
        sep="\n",
    ).encode()
    data = encoded if len(encoded) <= MAX_EVENT_BYTES else None
    if data is None:
        log.error(
            "event over the size limit, not written",
            extra={
                "streamId": payload["streamId"],
                "sequence": payload["sequence"],
                "bytes": len(encoded),
            },
        )
    return Frame(
        payload["streamId"],
        payload["sequence"],
        datetime.fromisoformat(payload["committedAt"]),
        data,
    )


@dataclass(eq=False)
class Client:
    """One SSE connection as the distributor sees it."""

    ip: str
    # Known once the connection has read the head. Until then it buffers frames of any
    # stream and drops the others when it drains.
    stream_id: str | None = None
    frames: deque[Frame] = field(default_factory=deque)
    size: int = 0
    end: End | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def take(self) -> Frame:
        f = self.frames.popleft()
        self.size -= f.size
        return f


class Hub:
    """In-memory distributor and the connection limits of this instance.

    A connection stays in `clients` until its response ends, closed or not: one stuck
    writing to a client that stopped reading keeps its place in the limits until the
    write timeout resets it."""

    def __init__(
        self,
        per_ip: int,
        per_instance: int,
        max_messages: int = MAX_QUEUE_MESSAGES,
        max_bytes: int = MAX_QUEUE_BYTES,
    ) -> None:
        self.per_ip = per_ip
        self.per_instance = per_instance
        self.max_messages = max_messages
        self.max_bytes = max_bytes
        # Registration order: the oldest connection of an IP comes first.
        self.clients: list[Client] = []
        self.closing = False

    def register(self, ip: str) -> Client:
        """A new connection. Over the IP limit the oldest of that IP is superseded; over the
        instance limit the new one is refused (capacity). Either way it is a connection
        with `end` set, answered with 200 and stream_closed."""
        client = Client(ip)
        if self.closing:
            client.end = "shutdown"
            return client
        same = [c for c in self.clients if c.ip == ip and c.end is None]
        replaced = same[: max(0, len(same) + 1 - self.per_ip)]
        for old in replaced:
            self._close(old, "superseded")
        # What it replaces is on its way out: over its own IP limit a connection is never
        # refused.
        if len(self.clients) - len(replaced) >= self.per_instance:
            client.end = "capacity"
            return client
        self.clients.append(client)
        return client

    def unregister(self, client: Client) -> None:
        if client in self.clients:
            self.clients.remove(client)

    def publish(self, frames: list[Frame]) -> None:
        stream_id = frames[0].stream_id
        for client in self.clients:
            if client.end is not None or client.stream_id not in (None, stream_id):
                continue
            client.frames.extend(frames)
            client.size += sum(f.size for f in frames)
            if len(client.frames) > self.max_messages or client.size > self.max_bytes:
                self._close(client, "queue_overflow")
            else:
                client.wake.set()

    def switch(self, stream_id: str) -> None:
        """A new active stream (database reset): connections on another one resync."""
        for client in list(self.clients):
            if client.stream_id not in (None, stream_id):
                self._close(client, "unknown_stream")

    def close_all(self) -> None:
        """Server shutdown: every connection, and any that arrives later, gets
        stream_closed (shutdown)."""
        self.closing = True
        for client in list(self.clients):
            self._close(client, "shutdown")

    def _close(self, client: Client, end: End) -> None:
        # The first reason is the one the connection sends. Whatever was queued is
        # dropped: the client comes back with its cursor.
        if client.end is not None:
            return
        client.end = end
        client.frames.clear()
        client.size = 0
        client.wake.set()


class Tailer:
    def __init__(self, database_url: str, hub: Hub) -> None:
        self._url = database_url
        self._hub = hub
        self.stream_id: str | None = None
        self.last = 0
        # The last head read, with the API's stale rule applied: heartbeats come from it,
        # so they cost no query per connection.
        self.head: queries.Head | None = None
        # time.monotonic() when the poll that read it started; the head is at least that
        # recent. Its age is how long the tailer has not read the database.
        self.polled_at = -math.inf

    async def run(self) -> None:
        """Forever, reconnecting with backoff. Cancel to stop."""
        engine = create_async_engine(
            async_url(self._url),
            poolclass=NullPool,
            isolation_level="AUTOCOMMIT",
            # A connect that never answers would freeze every stream without a log line.
            connect_args=KEEPALIVES | {"connect_timeout": 10},
            hide_parameters=True,
        )
        delay = 1.0
        try:
            while True:
                try:
                    async with engine.connect() as conn:
                        await conn.exec_driver_sql(f"LISTEN {NOTIFY_CHANNEL}")
                        # Autocommit already; this ends SQLAlchemy's autobegun transaction,
                        # so the queries can begin their own.
                        await conn.commit()
                        listener = (await conn.get_raw_connection()).driver_connection
                        assert isinstance(listener, psycopg.AsyncConnection)
                        while True:
                            await self.poll(conn)
                            delay = 1.0
                            async for _ in listener.notifies(timeout=POLL_SECONDS, stop_after=1):
                                pass
                except (DBAPIError, psycopg.Error, OSError) as exc:
                    log.warning(
                        "tailer lost the database, retrying in %.0f s",
                        delay,
                        extra={"error": type(exc).__name__, "detail": str(exc).splitlines()[0]},
                    )
                except Exception:
                    # A bug here would freeze every stream silently; log it and go on.
                    log.exception("tailer failed, retrying in %.0f s", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
        finally:
            await engine.dispose()

    async def poll(self, conn: AsyncConnection) -> None:
        """Hands out the events up to the head read now. Later ones wait for the next
        poll, so a heartbeat never names a head ahead of what was handed out."""
        started = time.monotonic()
        head = await queries.head(conn)
        if head is not None and head.stream_id != self.stream_id:
            # First read, or a new stream: start at its head. Connections fill any gap
            # from event_log themselves, at the next event or at the next heartbeat.
            self.stream_id, self.last = head.stream_id, head.head_sequence
            self._hub.switch(head.stream_id)
        while head is not None and self.last < head.head_sequence:
            rows = await queries.events_after(
                conn, head.stream_id, self.last, limit=min(PAGE, head.head_sequence - self.last)
            )
            if not rows:
                # Purged while the tailer was away; connections see the gap and resync.
                self.last = head.head_sequence
                break
            self._hub.publish([frame(r) for r in rows])
            self.last = rows[-1]["sequence"]
        self.head, self.polled_at = head, started
