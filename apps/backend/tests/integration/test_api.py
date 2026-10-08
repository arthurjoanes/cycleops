"""The API against a real PostgreSQL: uvicorn serving the app in the test's loop, the worker's
ingest producing the events, httpx reading the SSE streams over loopback."""

import asyncio
import json
import logging
import math
import socket
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops import aio
from cycleops.adapters.postgres import queries
from cycleops.adapters.postgres.connect import create_engine
from cycleops.adapters.postgres.tables import metadata
from cycleops.api import stream, tailer
from cycleops.api.__main__ import Protocol, Server
from cycleops.api.app import Settings, create_app
from cycleops.api.tailer import Frame, Hub, Tailer, frame
from cycleops.application.ingest import Ingestor
from cycleops.contracts import iso
from cycleops.jsonlog import JsonFormatter
from integration.conftest import Database, admin
from integration.feed import catalog, publication, worker

STREAM_PATH = "/api/v1/stream/stations"
# Seconds any single wait in these tests may take.
WAIT = 10


@asynccontextmanager
async def serving(
    settings: Settings, hub: Hub | None = None
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """The app under uvicorn on a free loopback port. Proxy headers are trusted from
    loopback (uvicorn's default), so X-Forwarded-For picks the client IP here."""
    app = create_app(settings)
    if hub is not None:
        app.state.hub = hub
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, http=Protocol, log_config=None, access_log=False
    )
    server = Server(config, app.state.shutdown)
    app.state.server = server
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(10):
        while not server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as http:
            yield app, http
    finally:
        app.state.shutdown.set()
        server.should_exit = True
        await task


class Sse:
    """Messages of one SSE response, as {field: value}."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self._lines = response.aiter_lines()

    async def next(self) -> dict[str, str] | None:
        """The next message; None when the server ended the response."""
        fields: dict[str, str] = {}
        async with asyncio.timeout(WAIT):
            async for line in self._lines:
                if line:
                    name, _, value = line.partition(":")
                    fields[name] = value.removeprefix(" ")
                elif fields:
                    return fields
        return None

    async def batches(self, count: int) -> list[int]:
        """Sequences of the next `count` station_batch messages, skipping heartbeats."""
        out: list[int] = []
        while len(out) < count:
            m = await self.next()
            assert m is not None, out
            if m["event"] == "heartbeat":
                continue
            assert m["event"] == "station_batch", m
            data = json.loads(m["data"])
            assert m["id"] == data["eventId"] == f"{data['streamId']}@{data['sequence']}"
            out.append(data["sequence"])
        return out

    async def control(self) -> tuple[str, dict[str, Any]]:
        """The control message that ends the response, then the end itself."""
        m = await self.next()
        assert m is not None and "id" not in m, m
        assert await self.next() is None
        return m["event"], json.loads(m["data"])


@asynccontextmanager
async def opened(
    http: httpx.AsyncClient, cursor: str | None = None, **headers: str
) -> AsyncIterator[Sse]:
    params = {"after": cursor} if cursor else {}
    async with http.stream("GET", STREAM_PATH, params=params, headers=headers) as response:
        assert response.status_code == 200
        yield Sse(response)


async def until(check: Callable[[], bool]) -> None:
    for _ in range(int(WAIT / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def live(app: FastAPI, count: int) -> Callable[[], bool]:
    """The hub holds `count` connections past the head read (step 3)."""
    return lambda: sum(c.stream_id is not None for c in app.state.hub.clients) == count


async def publish(conn: AsyncConnection, ingestor: Ingestor, ks: range) -> None:
    for k in ks:
        await ingestor.process(conn, *publication(k, {"100": k % 4, "200": 3}))


async def head_cursor(http: httpx.AsyncClient) -> str:
    r = await http.get("/api/v1/stations/snapshot")
    assert r.status_code == 200, r.text
    cursor: str = r.json()["cursor"]
    return cursor


def test_two_tailers_receive_every_event(clean_database: Database) -> None:
    async def go() -> list[list[int]]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            hubs = [Hub(4, 100), Hub(4, 100)]
            clients = [hub.register("127.0.0.1") for hub in hubs]
            tailers = [Tailer(clean_database.api_url, hub) for hub in hubs]
            tasks = [asyncio.create_task(t.run()) for t in tailers]
            await until(lambda: all(t.head is not None for t in tailers))
            await publish(conn, ingestor, range(1, 8))
            await until(lambda: all(len(c.frames) == 7 for c in clients))
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return [[c.take().sequence for _ in range(7)] for c in clients]

    assert aio.run(go()) == [list(range(2, 9))] * 2


def test_resync_rules_and_catch_up(clean_database: Database) -> None:
    async def go() -> None:
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(Settings(clean_database.api_url, sse_per_ip=50)) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            await publish(conn, ingestor, range(1, 42))
            cursor = await head_cursor(http)
            stream_id, head = cursor.split("@")
            assert head == "42"

            # 41 behind: past the catch-up limit.
            async with opened(http, f"{stream_id}@1") as sse:
                assert await sse.control() == (
                    "resync_required",
                    {"reason": "cursor_expired", "lastSequence": 1},
                )
            # 40 behind: catch-up from event_log, then live.
            async with opened(http, f"{stream_id}@2") as sse:
                assert await sse.batches(40) == list(range(3, 43))
                await publish(conn, ingestor, range(42, 43))
                assert await sse.batches(1) == [43]
            # Only the live event is a latency sample; the catch-up is late by design.
            assert len(app.state.latency.samples) == 1
            # Last-Event-ID wins over after.
            async with opened(
                http, f"{stream_id}@1", **{"Last-Event-ID": f"{stream_id}@41"}
            ) as sse:
                assert await sse.batches(2) == [42, 43]
            async with opened(http, f"{stream_id}@44") as sse:
                assert await sse.control() == (
                    "resync_required",
                    {"reason": "cursor_ahead", "lastSequence": 44},
                )
            system, generation = stream_id.split(":")
            other = f"{(int(generation, 16) + 1) % 2**32:08x}"
            for unknown in (f"{system}:{other}@43", f"oslobysykkel:{generation}@43"):
                async with opened(http, unknown) as sse:
                    assert await sse.control() == (
                        "resync_required",
                        {"reason": "unknown_stream", "lastSequence": None},
                    )
            # Out of the retention: the purge took what the cursor needs.
            await purge_events(clean_database, upto=20)
            async with opened(http, f"{stream_id}@10") as sse:
                assert await sse.control() == (
                    "resync_required",
                    {"reason": "cursor_expired", "lastSequence": 10},
                )

    aio.run(go())


async def purge_events(db: Database, upto: int) -> None:
    engine = create_engine(db.superuser_url)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM event_log WHERE sequence <= :s"), {"s": upto})
    await engine.dispose()


def test_buffer_above_head_and_gaps_are_written_once(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hub here has no tailer: the test plays it, so the order is fixed."""
    gate = asyncio.Event()

    async def gated_head(conn: AsyncConnection) -> queries.Head | None:
        await gate.wait()
        return await queries.head(conn)

    monkeypatch.setattr(
        stream, "queries", SimpleNamespace(head=gated_head, events_after=queries.events_after)
    )

    async def go() -> list[int]:
        settings = Settings(clean_database.api_url)
        hub = Hub(settings.sse_per_ip, settings.sse_per_instance)
        engine = create_engine(clean_database.api_url)

        async def handed_out(*sequences: int) -> None:
            async with engine.connect() as reader:
                stream_id = await queries.active_stream(reader)
                assert stream_id is not None
                for s in sequences:
                    rows = await queries.events_after(reader, stream_id, s - 1, limit=1)
                    hub.publish([frame(rows[0])])

        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(settings) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            await publish(conn, ingestor, range(1, 3))
            cursor = await head_cursor(http)
            app.state.hub = hub
            async with opened(http, cursor) as sse:
                # Registered, the head not read yet: 4 and 5 commit and reach the buffer.
                await until(lambda: len(hub.clients) == 1)
                await publish(conn, ingestor, range(3, 5))
                await handed_out(4, 5)
                gate.set()
                # H is 5: the catch-up writes 4 and 5, the buffered copies are dropped.
                got = await sse.batches(2)
                await until(lambda: not hub.clients[0].frames)
                # 8 is handed out before 6 and 7: those come from event_log.
                await publish(conn, ingestor, range(5, 8))
                await handed_out(8)
                got += await sse.batches(3)
                # Late copies are dropped: the next message written is 9.
                await publish(conn, ingestor, range(8, 9))
                await handed_out(6, 7, 8, 9)
                got += await sse.batches(1)
        await engine.dispose()
        return got

    assert aio.run(go()) == [4, 5, 6, 7, 8, 9]


def test_events_the_tailer_started_past_arrive_by_the_next_heartbeat(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection reads H, the worker commits H + 1, and only then does the tailer read
    the stream for the first time: it starts at H + 1 and hands out nothing. The connection
    reads that event at its next heartbeat, without waiting for another commit."""
    hold, held, release = False, asyncio.Event(), asyncio.Event()

    async def tailer_head(conn: AsyncConnection) -> queries.Head | None:
        if hold:
            held.set()
            await release.wait()
        return await queries.head(conn)

    monkeypatch.setattr(
        tailer, "queries", SimpleNamespace(head=tailer_head, events_after=queries.events_after)
    )

    async def go() -> list[int]:
        nonlocal hold
        # The tailer is held for well under HEAD_MAX_AGE_SECONDS.
        async with serving(Settings(clean_database.api_url, heartbeat_seconds=2)) as (app, http):
            # The tailer has read the empty database; its next poll waits.
            await until(lambda: math.isfinite(app.state.tailer.polled_at))
            hold = True
            async with asyncio.timeout(WAIT):
                await held.wait()
            async with (
                worker(clean_database.worker_url) as (conn, ingestor),
                opened(http, await first_cursor(http, conn, ingestor)) as sse,
            ):
                await until(live(app, 1))
                await publish(conn, ingestor, range(1, 2))
                release.set()
                async with asyncio.timeout(WAIT):
                    return await sse.batches(1)

    assert aio.run(go()) == [2]


async def first_cursor(http: httpx.AsyncClient, conn: AsyncConnection, ingestor: Ingestor) -> str:
    await ingestor.process(conn, *catalog())
    return await head_cursor(http)


def test_a_new_stream_sends_open_connections_to_resync(clean_database: Database) -> None:
    async def go() -> tuple[str, dict[str, Any]]:
        async with serving(Settings(clean_database.api_url)) as (app, http):
            async with worker(clean_database.worker_url) as (conn, ingestor):
                await ingestor.process(conn, *catalog())
            async with opened(http, await head_cursor(http)) as sse:
                await until(live(app, 1))
                # Reset: the database is emptied and the worker starts another stream.
                tables = sql.SQL(", ").join(sql.Identifier(t) for t in metadata.tables)
                await admin(clean_database.superuser_url, sql.SQL("TRUNCATE {}").format(tables))
                async with worker(clean_database.worker_url):
                    return await sse.control()

    assert aio.run(go()) == ("resync_required", {"reason": "unknown_stream", "lastSequence": 1})


def test_queue_overflow_ends_the_stream(clean_database: Database) -> None:
    async def go() -> tuple[str, dict[str, Any]]:
        settings = Settings(clean_database.api_url)
        # No room at all: the first event the tailer hands out overflows the queue.
        hub = Hub(settings.sse_per_ip, settings.sse_per_instance, max_messages=0)
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(settings, hub) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            async with opened(http, await head_cursor(http)) as sse:
                await until(live(app, 1))
                await publish(conn, ingestor, range(1, 2))
                return await sse.control()

    assert aio.run(go()) == ("resync_required", {"reason": "queue_overflow", "lastSequence": 1})


@contextmanager
def not_reading(http: httpx.AsyncClient, cursor: str) -> Iterator[tuple[str, int]]:
    """A stream opened from a raw socket with a small window that is never read: the
    server's writes back up. Yields the socket's own address."""
    port = http.base_url.port
    assert port is not None
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.connect(("127.0.0.1", port))
        sock.sendall(f"GET {STREAM_PATH}?after={cursor} HTTP/1.1\r\nHost: api\r\n\r\n".encode())
        yield sock.getsockname()


def connection(app: FastAPI, client: tuple[str, int]) -> Protocol | None:
    """The server side of a connection, while the server holds it."""
    found = [c for c in app.state.server.server_state.connections if c.client == client]
    return found[0] if found else None


def big(stream_id: str, sequence: int, size: int = 2**20) -> Frame:
    return Frame(stream_id, sequence, datetime.now(UTC), b"x" * size)


def test_a_client_that_stops_reading_is_reset(
    clean_database: Database, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its writes stay blocked: the write timeout resets the connection, which ends the
    response. Until then the connection keeps its place in the hub, closed or not."""
    caplog.set_level(logging.INFO, logger="cycleops.api.access")
    monkeypatch.setattr(Protocol, "write_timeout", 0.5)

    async def go() -> None:
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(Settings(clean_database.api_url)) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            cursor = await head_cursor(http)
            stream_id, head = cursor.split("@")
            hub: Hub = app.state.hub
            with not_reading(http, cursor) as local:
                await until(live(app, 1))
                client = hub.clients[0]
                sequence = int(head)
                while client in hub.clients and client.end is None:
                    sequence += 1
                    hub.publish([big(stream_id, sequence)])
                    await asyncio.sleep(0.02)
                await until(lambda: not hub.clients)
                await until(
                    lambda: any(line["path"] == STREAM_PATH for line in access_lines(caplog))
                )
                # The socket is gone too, with what was still buffered for it.
                await until(lambda: connection(app, local) is None)

    aio.run(go())


def test_the_last_write_of_a_stream_is_bounded_too(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write fills the buffer, then the stream ends without another message (here the
    database fails while it fills a gap): the connection leaves the hub while the response's
    closing write waits for the buffer to drain. The write timeout resets it all the same."""

    async def down(*_: Any, **__: Any) -> None:
        raise OperationalError("SELECT", None, ConnectionError())

    monkeypatch.setattr(stream, "queries", SimpleNamespace(head=queries.head, events_after=down))
    monkeypatch.setattr(Protocol, "write_timeout", 2)

    async def go() -> None:
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(Settings(clean_database.api_url)) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            cursor = await head_cursor(http)
            stream_id, head = cursor.split("@")
            hub: Hub = app.state.hub
            with not_reading(http, cursor) as local:
                await until(live(app, 1))
                client = hub.clients[0]
                sequence = int(head)
                # One frame at a time, each written before the next: when the buffer passes
                # the high-water mark the stream is waiting for events, not on a write.
                while not (side := connection(app, local)) or not side.flow.write_paused:
                    sequence += 1
                    hub.publish([big(stream_id, sequence)])
                    await until(lambda: not client.frames)
                hub.publish([big(stream_id, sequence + 2)])
                await until(lambda: not hub.clients)
                assert connection(app, local) is not None
                await until(lambda: connection(app, local) is None)

    aio.run(go())


def test_a_few_unsent_bytes_are_bounded_too(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A few KiB wait in the buffer, well under asyncio's usual 64 KiB high-water mark, when
    a newer connection from the same IP supersedes the stream. The close that follows its
    end would wait for a drain that never comes; the write timeout resets it all the same."""
    monkeypatch.setattr(Protocol, "write_timeout", 0.5)

    async def go() -> None:
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(Settings(clean_database.api_url, sse_per_ip=1)) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            cursor = await head_cursor(http)
            stream_id, head = cursor.split("@")
            hub: Hub = app.state.hub
            with not_reading(http, cursor) as local:
                await until(live(app, 1))
                client = hub.clients[0]
                sequence = int(head)
                while (
                    not (side := connection(app, local))
                    or not side.transport.get_write_buffer_size()
                ):
                    sequence += 1
                    hub.publish([big(stream_id, sequence, 4096)])
                    await until(lambda: not client.frames)
                async with opened(http, cursor):
                    await until(lambda: client.end == "superseded")
                    await until(lambda: connection(app, local) is None)

    aio.run(go())


def test_streams_above_the_pool_leave_the_snapshot_answering(clean_database: Database) -> None:
    """15 streams against a pool of 5 + 5 with pool_timeout 2 s: no stream holds a
    connection, so the snapshot still answers well within that."""
    streams = 15

    async def go() -> tuple[int, float, int]:
        settings = Settings(clean_database.api_url, sse_per_ip=50, pool_timeout=2)
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(settings) as (app, http),
            AsyncExitStack() as stack,
        ):
            await ingestor.process(conn, *catalog())
            cursor = await head_cursor(http)
            sses = [await stack.enter_async_context(opened(http, cursor)) for _ in range(streams)]
            await until(live(app, streams))
            await publish(conn, ingestor, range(1, 2))
            for sse in sses:
                assert await sse.batches(1) == [2]
            checked_out = app.state.engine.pool.checkedout()
            started = time.perf_counter()
            r = await http.get("/api/v1/stations/snapshot")
            return r.status_code, time.perf_counter() - started, checked_out

    status, elapsed, checked_out = aio.run(go())
    assert (status, checked_out) == (200, 0)
    assert elapsed < 2


def test_sse_limits_per_ip_and_per_instance(clean_database: Database) -> None:
    async def go() -> None:
        settings = Settings(clean_database.api_url, sse_per_ip=2, sse_per_instance=3)
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(settings) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            cursor = await head_cursor(http)
            a, b = {"X-Forwarded-For": "10.1.1.1"}, {"X-Forwarded-For": "10.1.1.2"}
            async with (
                opened(http, cursor, **a) as a1,
                opened(http, cursor, **a) as a2,
                opened(http, cursor, **b) as b1,
            ):
                await until(live(app, 3))
                # The instance is full: the newcomer from another IP is refused.
                async with opened(http, cursor, **{"X-Forwarded-For": "10.1.1.3"}) as c1:
                    assert await c1.control() == ("stream_closed", {"reason": "capacity"})
                # A third from 10.1.1.1 takes the place of its oldest.
                async with opened(http, cursor, **a) as a3:
                    assert await a1.control() == ("stream_closed", {"reason": "superseded"})
                    await until(live(app, 3))
                    await publish(conn, ingestor, range(1, 2))
                    for sse in (a2, b1, a3):
                        assert await sse.batches(1) == [2]

    aio.run(go())


def test_worker_stopped_is_stale_in_heartbeat_snapshot_and_feed_health(
    clean_database: Database,
) -> None:
    async def states(app: FastAPI, http: httpx.AsyncClient, sse: Sse) -> list[tuple[str, Any]]:
        # A heartbeat written after the tailer read the database again; the ones before it
        # wait in the socket buffer.
        changed = time.monotonic()
        await until(lambda: app.state.tailer.polled_at > changed)
        polled = datetime.now(UTC)
        while True:
            m = await sse.next()
            assert m is not None and m["event"] == "heartbeat" and "id" not in m, m
            beat = json.loads(m["data"])
            if datetime.fromisoformat(beat["serverTime"]) >= polled:
                break
        snapshot = (await http.get("/api/v1/stations/snapshot")).json()
        health = (await http.get("/api/v1/feed-health")).json()
        return [
            (beat["feedState"], beat.get("stateSince")),
            (snapshot["feedHealth"]["state"], snapshot["feedHealth"].get("stateSince")),
            (health["state"], health.get("stateSince")),
        ]

    async def go() -> tuple[list[tuple[str, Any]], list[tuple[str, Any]], datetime, datetime]:
        settings = Settings(clean_database.api_url, heartbeat_seconds=1)
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(settings) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            await publish(conn, ingestor, range(1, 2))
            # The feed's instants are from 2026-10-07; only the stored state matters here.
            since = (
                await conn.execute(
                    text(
                        "UPDATE stream_heads SET last_source_updated_at = now(), "
                        "worker_seen_at = now() RETURNING source_state_since"
                    )
                )
            ).scalar_one()
            await conn.commit()
            async with opened(http, await head_cursor(http)) as sse:
                running = await states(app, http, sse)
                # The worker stops ticking: worker_seen_at ages past sourceStaleAfterSeconds.
                seen = (
                    await conn.execute(
                        text(
                            "UPDATE stream_heads SET worker_seen_at = now() - interval '61 seconds'"
                            " RETURNING worker_seen_at"
                        )
                    )
                ).scalar_one()
                await conn.commit()
                stopped = await states(app, http, sse)
        return running, stopped, since, seen

    running, stopped, since, seen = aio.run(go())
    assert running == [("fresh", iso(since))] * 3
    # Only the API's rule makes it stale, from when the last tick crossed the limit.
    assert stopped == [("stale", iso(seen + timedelta(seconds=60)))] * 3


def test_a_tailer_that_cannot_read_ends_the_streams_without_a_message(
    clean_database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tailer hangs on the database. Heartbeats repeat the head it read last for a few
    poll intervals at most, however long the heartbeat interval, then the streams end with no
    message. A connection on a stream the tailer has not read yet (a reset meanwhile) is not
    sent to resync."""
    hung, held = False, asyncio.Event()

    async def tailer_head(conn: AsyncConnection) -> queries.Head | None:
        if hung:
            held.set()
            await asyncio.Event().wait()
        return await queries.head(conn)

    monkeypatch.setattr(
        tailer, "queries", SimpleNamespace(head=tailer_head, events_after=queries.events_after)
    )

    async def messages(sse: Sse) -> list[tuple[float, dict[str, str]]]:
        """Each message with time.monotonic() when it was read."""
        out = []
        while (m := await sse.next()) is not None:
            out.append((time.monotonic(), m))
        return out

    async def go() -> tuple[
        str, float, list[tuple[float, dict[str, str]]], list[tuple[float, dict[str, str]]]
    ]:
        nonlocal hung
        async with serving(Settings(clean_database.api_url, heartbeat_seconds=2)) as (app, http):
            async with worker(clean_database.worker_url) as (conn, ingestor):
                old = await first_cursor(http, conn, ingestor)
            async with opened(http, old) as a:
                await until(live(app, 1))
                m = await a.next()
                assert m is not None and m["event"] == "heartbeat", m
                hung = True
                async with asyncio.timeout(WAIT):
                    await held.wait()
                on_old = asyncio.create_task(messages(a))
                tables = sql.SQL(", ").join(sql.Identifier(t) for t in metadata.tables)
                await admin(clean_database.superuser_url, sql.SQL("TRUNCATE {}").format(tables))
                async with worker(clean_database.worker_url):
                    pass
                async with opened(http, await head_cursor(http)) as b:
                    async with asyncio.timeout(WAIT):
                        old_messages, on_new = await asyncio.gather(on_old, messages(b))
            polled_at: float = app.state.tailer.polled_at
        return old.split("@")[0], polled_at, old_messages, on_new

    old_stream, polled_at, on_old, on_new = aio.run(go())
    assert {m["event"] for _, m in on_old} <= {"heartbeat"}
    assert {json.loads(m["data"])["streamId"] for _, m in on_old} <= {old_stream}
    # Each read within half a second of being written: none names a head past the limit.
    assert all(at - polled_at < stream.HEAD_MAX_AGE_SECONDS + 0.5 for at, _ in on_old), [
        at - polled_at for at, _ in on_old
    ]
    assert on_new == []


def test_no_response_has_cors_headers(clean_database: Database) -> None:
    async def go() -> list[httpx.Headers]:
        origin = {"Origin": "https://elsewhere.example"}
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(Settings(clean_database.api_url)) as (_, http),
        ):
            await ingestor.process(conn, *catalog())
            out = []
            for path in (
                "/api/v1/stations/snapshot",
                "/api/v1/stations/100/history",
                "/api/v1/stations/999/history",
                "/api/v1/feed-health",
                "/health/live",
                "/health/ready",
                "/api/v1/nope",
            ):
                r = await http.get(path, headers=origin)
                assert r.status_code in (200, 404), (path, r.text)
                out.append(r.headers)
            preflight = {**origin, "Access-Control-Request-Method": "GET"}
            r = await http.options("/api/v1/stations/snapshot", headers=preflight)
            assert r.status_code == 405
            out.append(r.headers)
            async with http.stream("GET", STREAM_PATH, headers=origin) as r:
                out.append(r.headers)
            return out

    for headers in aio.run(go()):
        assert not [h for h in headers if h.lower().startswith("access-control-")], headers


def test_rest_routes(clean_database: Database) -> None:
    async def go() -> dict[str, Any]:
        async with serving(Settings(clean_database.api_url)) as (_, http):
            # No stream yet.
            for path in ("/api/v1/stations/snapshot", "/api/v1/feed-health"):
                r = await http.get(path)
                assert (r.status_code, r.json()["code"]) == (503, "unavailable")
            async with worker(clean_database.worker_url) as (conn, ingestor):
                await ingestor.process(conn, *catalog())
                await publish(conn, ingestor, range(1, 3))
            snapshot = (await http.get("/api/v1/stations/snapshot")).json()
            health = (await http.get("/api/v1/feed-health")).json()
            history = await http.get("/api/v1/stations/100/history")
            window = {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T06:00:00Z"}
            dated = await http.get("/api/v1/stations/100/history", params=window)
            unknown = await http.get("/api/v1/stations/999/history")
            ready = await http.get("/health/ready")
            return {
                "snapshot": snapshot,
                "health": health,
                "history": (history.status_code, history.json()),
                "dated": (dated.status_code, dated.json()),
                "unknown": (unknown.status_code, unknown.json()["code"]),
                "ready": (ready.status_code, ready.json()),
            }

    out = aio.run(go())
    snapshot = out["snapshot"]
    assert snapshot["cursor"] == f"{snapshot['streamId']}@3" and snapshot["mode"] == "synthetic"
    assert [s["availability"]["stationId"] for s in snapshot["stations"]] == ["100", "200"]
    assert out["health"] == snapshot["feedHealth"]
    status, history = out["history"]
    # Default window: the last 6 h of commits, which holds both publications.
    assert status == 200 and history["streamId"] == snapshot["streamId"]
    assert [p["bikesAvailable"] for p in history["points"]] == [1, 2]
    status, dated = out["dated"]
    assert status == 200 and dated["points"] == []
    assert out["unknown"] == (404, "not_found")
    assert out["ready"] == (200, {"status": "ok"})


def test_json_access_log_has_the_fields_and_no_payload(
    clean_database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="cycleops.api.access")

    async def go() -> str:
        async with (
            worker(clean_database.worker_url) as (conn, ingestor),
            serving(Settings(clean_database.api_url)) as (app, http),
        ):
            await ingestor.process(conn, *catalog())
            r = await http.get("/api/v1/stations/snapshot", headers={"X-Request-ID": "req-1"})
            cursor = r.json()["cursor"]
            async with opened(http, cursor, **{"X-Request-ID": "req-2"}) as sse:
                await until(live(app, 1))
                await publish(conn, ingestor, range(1, 3))
                assert await sse.batches(2) == [2, 3]
            await until(lambda: len(access_lines(caplog)) == 2)
            stream_id: str = cursor.split("@")[0]
            return stream_id

    stream_id = aio.run(go())
    snapshot, sse = access_lines(caplog)
    assert set(snapshot) == {
        "time",
        "level",
        "logger",
        "message",
        "requestId",
        "method",
        "path",
        "status",
        "duration_ms",
    }
    assert (snapshot["requestId"], snapshot["path"], snapshot["status"]) == (
        "req-1",
        "/api/v1/stations/snapshot",
        200,
    )
    assert (sse["requestId"], sse["path"], sse["status"]) == ("req-2", STREAM_PATH, 200)
    assert (sse["streamId"], sse["sequence"], sse["count"]) == (stream_id, 3, 2)
    assert sse["outcome"] == "disconnected" and sse["duration_ms"] > 0
    for line in (snapshot, sse):
        text_line = json.dumps(line, ensure_ascii=False)
        assert "Estação" not in text_line and "availability" not in text_line
        assert "after=" not in text_line


def access_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    formatter = JsonFormatter()
    return [
        json.loads(formatter.format(r)) for r in caplog.records if r.name == "cycleops.api.access"
    ]
