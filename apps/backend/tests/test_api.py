"""API pieces that need no database: cursor syntax, distributor limits and queues, SSE
encoding, settings, route inventory, error format, rate limit, early stream endings.

Requests go through httpx.ASGITransport, which buffers the whole response: fine for the
streams here, which all end before any database read. A dead database URL keeps the
lifespan honest (the tailer just retries)."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.routing import APIRoute

from cycleops import aio
from cycleops.api import __main__ as api_main
from cycleops.api import routes
from cycleops.api.app import Settings, create_app
from cycleops.api.stream import Latency, message, parse_cursor, report_latency
from cycleops.api.tailer import MAX_EVENT_BYTES, Frame, Hub, frame
from cycleops.contracts import StreamClosed

# Nothing listens there. connect_timeout: on Windows a refused connect is retried, not failed.
DEAD = "postgresql://cycleops_api:x@127.0.0.1:1/cycleops?connect_timeout=1"
STREAM = "synthetic-oslo:0a1b2c3d"


def payload(sequence: int, **extra: Any) -> dict[str, Any]:
    return {
        "type": "station_batch",
        "eventId": f"{STREAM}@{sequence}",
        "streamId": STREAM,
        "sequence": sequence,
        "committedAt": "2026-10-08T10:00:00.000Z",
        "stations": [],
        **extra,
    }


def frames(*sequences: int, size: int = 0) -> list[Frame]:
    return [
        Frame(STREAM, s, datetime(2026, 10, 8, tzinfo=UTC), b"x" * size if size else b"x")
        for s in sequences
    ]


# Cursor


def test_cursor_syntax() -> None:
    c = parse_cursor(f"{STREAM}@0")
    assert c is not None and (c.stream_id, c.sequence) == (STREAM, 0)
    found = parse_cursor(f"{STREAM}@123456789012345678")
    assert found is not None and found.sequence == 123456789012345678
    for bad in [
        "",
        STREAM,
        f"{STREAM}@",
        f"{STREAM}@-1",
        f"{STREAM}@01",
        f"{STREAM}@1 ",
        f"{STREAM}@1234567890123456789",  # past bigint
        "synthetic-oslo:0A1B2C3D@1",  # the generation is lowercase hex
        "synthetic-oslo:0a1b2c3@1",
        "synthetic oslo:0a1b2c3d@1",
        f"{'s' * 65}:0a1b2c3d@1",
        f"{STREAM}@1\nid: x",
        "x" * 129,
    ]:
        assert parse_cursor(bad) is None, bad


# Distributor


def test_per_ip_limit_supersedes_the_oldest_of_that_ip() -> None:
    hub = Hub(per_ip=2, per_instance=10)
    a1, a2, b1 = hub.register("a"), hub.register("a"), hub.register("b")
    assert (a1.end, a2.end, b1.end) == (None, None, None)
    a3 = hub.register("a")
    assert (a1.end, a2.end, a3.end, b1.end) == ("superseded", None, None, None)
    assert a1.wake.is_set() and hub.clients == [a1, a2, b1, a3]
    hub.unregister(a1)
    assert hub.register("a").end is None and a2.end == "superseded"


def test_per_instance_limit_refuses_the_new_connection() -> None:
    hub = Hub(per_ip=5, per_instance=2)
    first, second = hub.register("a"), hub.register("b")
    third = hub.register("c")
    assert third.end == "capacity" and third not in hub.clients
    assert (first.end, second.end) == (None, None)
    # Over its own IP limit a connection replaces its oldest, so it is never refused.
    hub = Hub(per_ip=1, per_instance=1)
    old = hub.register("a")
    new = hub.register("a")
    assert (old.end, new.end) == ("superseded", None)
    hub.unregister(old)
    hub.unregister(new)
    assert hub.register("b").end is None


def test_queue_over_200_messages_overflows() -> None:
    hub = Hub(per_ip=4, per_instance=10)
    slow, fast = hub.register("a"), hub.register("b")
    hub.publish(frames(*range(1, 201)))
    while fast.frames:
        fast.take()
    assert slow.end is None and len(slow.frames) == 200 and slow.wake.is_set()
    hub.publish(frames(201))
    assert slow.end == "queue_overflow" and not slow.frames
    assert fast.end is None and [f.sequence for f in fast.frames] == [201]


def test_rejected_replacement_keeps_the_current_connection_open() -> None:
    hub = Hub(per_ip=1, per_instance=1)
    draining = hub.register("a")
    current = hub.register("a")
    assert draining.end == "superseded" and current.end is None

    rejected = hub.register("a")

    assert rejected.end == "capacity" and rejected not in hub.clients
    assert current.end is None
    hub.publish(frames(1))
    assert [item.sequence for item in current.frames] == [1]


def test_queue_over_4_mib_overflows() -> None:
    hub = Hub(per_ip=4, per_instance=10)
    client = hub.register("a")
    hub.publish(frames(1, 2, size=2 * 1024 * 1024))
    assert client.end is None and client.size == 4 * 1024 * 1024
    client.take()
    assert client.size == 2 * 1024 * 1024
    hub.publish(frames(3, 4, size=1024 * 1024 + 1))
    assert client.end == "queue_overflow"


def test_publish_and_switch_follow_the_stream() -> None:
    hub = Hub(per_ip=4, per_instance=10)
    pending, here, elsewhere = hub.register("a"), hub.register("a"), hub.register("a")
    here.stream_id, elsewhere.stream_id = STREAM, "synthetic-oslo:ffffffff"
    hub.publish(frames(1))
    assert [len(c.frames) for c in (pending, here, elsewhere)] == [1, 1, 0]
    hub.switch(STREAM)
    assert (pending.end, here.end, elsewhere.end) == (None, None, "unknown_stream")


def test_a_closed_connection_counts_until_its_response_ends() -> None:
    """A client that stopped reading is closed while its response is stuck on a write,
    until the write timeout resets it: meanwhile it still takes a place in the instance."""
    hub = Hub(per_ip=1, per_instance=2, max_messages=1)
    stuck = hub.register("a")
    stuck.stream_id = STREAM
    hub.publish(frames(1, 2))
    assert stuck.end == "queue_overflow" and stuck in hub.clients
    # Nothing more reaches it, and the reason it will send stays the first one.
    hub.publish(frames(3))
    hub.switch("synthetic-oslo:ffffffff")
    assert not stuck.frames and stuck.end == "queue_overflow"
    assert hub.register("b").end is None
    assert hub.register("c").end == "capacity"
    assert hub.register("a").end == "capacity" and stuck.end == "queue_overflow"
    hub.unregister(stuck)
    assert hub.register("c").end is None


def test_close_all_ends_every_connection_and_refuses_new_ones() -> None:
    hub = Hub(per_ip=4, per_instance=10)
    client = hub.register("a")
    hub.close_all()
    assert client.end == "shutdown" and hub.clients == [client]
    assert hub.register("b").end == "shutdown" and hub.clients == [client]


# SSE encoding


def test_station_batch_frame_carries_the_cursor_as_id() -> None:
    f = frame(payload(7))
    assert (f.stream_id, f.sequence) == (STREAM, 7)
    assert f.committed_at == datetime(2026, 10, 8, 10, tzinfo=UTC)
    assert f.data is not None
    lines = f.data.decode().split("\n")
    assert lines[:2] == [f"id: {STREAM}@7", "event: station_batch"]
    assert json.loads(lines[2].removeprefix("data: ")) == payload(7)
    assert lines[3:] == ["", ""]


def test_event_over_2_mib_is_not_written() -> None:
    assert frame(payload(1, pad="x" * (MAX_EVENT_BYTES - 500))).data is not None
    big = frame(payload(1, pad="x" * MAX_EVENT_BYTES))
    assert big.data is None and big.size == 0


def test_control_messages_never_carry_an_id() -> None:
    closed = message("stream_closed", StreamClosed(reason="shutdown"), retry=2000).decode()
    assert closed == 'event: stream_closed\ndata: {"reason":"shutdown"}\nretry: 2000\n\n'
    assert "id:" not in closed


def test_latency_summary() -> None:
    latency = Latency(size=100)
    assert latency.summary() is None
    now = datetime.now(UTC)
    for ms in range(1, 201):
        latency.samples.append(float(ms))
    latency.add(now - timedelta(seconds=10))
    summary = latency.summary()
    assert summary is not None
    assert summary["count"] == 100 and summary["max"] >= 10_000
    assert 140 < summary["p50"] < 160 and summary["p95"] > 195


def test_latency_summary_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="cycleops.api.stream")
    latency = Latency()
    latency.samples.extend([5.0, 7.0])

    async def go() -> None:
        task = asyncio.create_task(report_latency(latency, every=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    aio.run(go())
    record = next(r for r in caplog.records if r.getMessage() == "commit to write")
    assert vars(record)["latency_commit_to_write_ms"]["count"] == 2


# Settings and entry point


def test_settings_from_env() -> None:
    settings = Settings.from_env({"DATABASE_URL": DEAD})
    assert (settings.sse_per_ip, settings.sse_per_instance, settings.rest_per_minute_per_ip) == (
        4,
        100,
        60,
    )
    custom = Settings.from_env(
        {
            "DATABASE_URL": DEAD,
            "CYCLEOPS_SSE_PER_IP": "9",
            "CYCLEOPS_SSE_PER_INSTANCE": "7",
            "CYCLEOPS_REST_PER_MINUTE_PER_IP": "5",
        }
    )
    assert (custom.sse_per_ip, custom.sse_per_instance, custom.rest_per_minute_per_ip) == (9, 7, 5)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings.from_env({})
    for bad in ("0", "-1", "four", "1.5"):
        with pytest.raises(ValueError, match="CYCLEOPS_SSE_PER_IP"):
            Settings.from_env({"DATABASE_URL": DEAD, "CYCLEOPS_SSE_PER_IP": bad})


def test_main_refuses_bad_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert api_main.main([]) == 2
    monkeypatch.setenv("DATABASE_URL", DEAD)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    assert api_main.main([]) == 2


# Routes and HTTP behaviour


def test_route_inventory_is_read_only() -> None:
    app = create_app(Settings(DEAD))
    routes = {(r.path, frozenset(r.methods or ())) for r in app.routes if isinstance(r, APIRoute)}
    assert routes == {
        ("/health/live", frozenset({"GET"})),
        ("/health/ready", frozenset({"GET"})),
        ("/api/v1/stations/snapshot", frozenset({"GET"})),
        ("/api/v1/stations/{station_id}/history", frozenset({"GET"})),
        ("/api/v1/feed-health", frozenset({"GET"})),
        ("/api/v1/feed-diagnostics", frozenset({"GET"})),
        ("/api/v1/stream/stations", frozenset({"GET"})),
    }
    assert all(methods <= {"GET", "HEAD"} for _, methods in routes)
    # Nothing but those: no docs, no OpenAPI, no websocket or mounts.
    assert len(app.routes) == len(routes)


@asynccontextmanager
async def client(
    app: FastAPI, ip: str = "127.0.0.1", lifespan: bool = False
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app, client=(ip, 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
        if not lifespan:
            yield http
            return
        async with app.router.lifespan_context(app):
            yield http


def run(go: Callable[[], Any]) -> Any:
    return aio.run(go())


def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body: dict[str, Any] = response.json()
    assert set(body) == {"code", "message", "requestId", "details"}
    assert body["code"] == code and body["requestId"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text
    return body


def test_error_format_and_request_id() -> None:
    async def go() -> None:
        async with client(create_app(Settings(DEAD))) as http:
            r = await http.get("/api/v1/nope", headers={"X-Request-ID": "abc-123"})
            assert assert_error(r, 404, "not_found")["requestId"] == "abc-123"
            assert r.headers["cache-control"] == "no-store"
            # A request id that could break a log line is replaced.
            r = await http.get("/api/v1/nope", headers={"X-Request-ID": "a b\tc"})
            assert len(assert_error(r, 404, "not_found")["requestId"]) == 32
            r = await http.post("/api/v1/stations/snapshot")
            assert_error(r, 405, "method_not_allowed")
            assert r.headers["allow"] == "GET"
            r = await http.get("/api/v1/stations/bad!id/history")
            details = assert_error(r, 422, "invalid_request")["details"]
            assert details[0]["loc"] == ["path", "station_id"] and "input" not in details[0]
            r = await http.get("/api/v1/stations/100/history", params={"from": "2026-10-08T10:00"})
            assert_error(r, 422, "invalid_request")
            # Over 24 h, and from after to: refused before any database read.
            window = {"from": "2026-10-07T10:00:00Z", "to": "2026-10-08T10:00:01Z"}
            r = await http.get("/api/v1/stations/100/history", params=window)
            assert_error(r, 422, "invalid_request")
            r = await http.get("/api/v1/feed-diagnostics", params=window)
            assert_error(r, 422, "invalid_request")
            window = {"from": "2026-10-08T10:00:00Z", "to": "2026-10-08T09:00:00Z"}
            r = await http.get("/api/v1/stations/100/history", params=window)
            assert_error(r, 422, "invalid_request")
            r = await http.get("/api/v1/feed-diagnostics", params=window)
            assert_error(r, 422, "invalid_request")
            r = await http.get("/api/v1/feed-diagnostics", params={"from": "2026-10-08T10:00"})
            assert_error(r, 422, "invalid_request")

    run(go)


@pytest.mark.parametrize("path", ["/api/v1/stations/100/history", "/api/v1/feed-diagnostics"])
@pytest.mark.parametrize(
    ("window", "status"),
    [
        ({"to": "0001-01-01T00:00:00Z"}, 422),
        ({"from": "9999-12-31T00:00:00Z", "to": "9999-12-31T01:00:00Z"}, 503),
    ],
)
def test_date_boundaries_do_not_overflow(
    monkeypatch: pytest.MonkeyPatch, path: str, window: dict[str, str], status: int
) -> None:
    def unavailable(request: Request) -> None:
        raise HTTPException(503, "test database unavailable")

    monkeypatch.setattr(routes, "_engine", unavailable)

    async def go() -> None:
        async with client(create_app(Settings(DEAD))) as http:
            response = await http.get(path, params=window)
            assert_error(response, status, "invalid_request" if status == 422 else "unavailable")

    run(go)


def test_unhandled_error_is_a_500_without_trace() -> None:
    app = create_app(Settings(DEAD))

    @app.get("/api/v1/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail")

    async def go() -> None:
        async with client(app) as http:
            r = await http.get("/api/v1/boom")
            body = assert_error(r, 500, "internal_error")
            assert "secret" not in r.text and body["details"] is None

    run(go)


def test_rest_rate_limit_per_ip() -> None:
    # The window is a fixed minute: stay clear of its boundary.
    if time.monotonic() % 60 > 55:
        time.sleep(6)
    app = create_app(Settings(DEAD, rest_per_minute_per_ip=3))

    async def go() -> None:
        async with client(app, "10.0.0.1") as a, client(app, "10.0.0.2") as b:
            for _ in range(3):
                assert (await a.get("/api/v1/nope")).status_code == 404
            r = await a.get("/api/v1/nope")
            assert_error(r, 429, "rate_limited")
            assert 1 <= int(r.headers["retry-after"]) <= 60
            # Another IP has its own count; health checks and the stream are not REST.
            assert (await b.get("/api/v1/nope")).status_code == 404
            assert (await a.get("/health/live")).status_code == 200

    run(go)


def test_database_down_is_a_503() -> None:
    async def go() -> None:
        async with client(create_app(Settings(DEAD)), lifespan=True) as http:
            for path in (
                "/health/ready",
                "/api/v1/stations/snapshot",
                "/api/v1/feed-health",
                "/api/v1/feed-diagnostics",
            ):
                assert_error(await http.get(path), 503, "database_unavailable")
            assert (await http.get("/health/live")).json() == {"status": "ok"}

    run(go)


def sse(body: str) -> list[dict[str, str]]:
    out = []
    for block in body.split("\n\n"):
        if block:
            out.append(dict(line.split(": ", 1) for line in block.split("\n")))
    return out


def test_stream_ends_early_with_200_and_a_control_message() -> None:
    app = create_app(Settings(DEAD, sse_per_ip=2, sse_per_instance=2))

    async def go() -> None:
        async with client(app, lifespan=True) as http:
            # A malformed cursor: resync, never a 4xx (EventSource would stop for good).
            r = await http.get("/api/v1/stream/stations", params={"after": "nope"})
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            assert r.headers["cache-control"] == "no-store, no-transform"
            assert r.headers["x-accel-buffering"] == "no"
            assert sse(r.text) == [
                {
                    "event": "resync_required",
                    "data": '{"reason":"unknown_stream","lastSequence":null}',
                }
            ]
            # Validated before registering: the IP's open streams are not superseded.
            hub: Hub = app.state.hub
            mine = [hub.register("127.0.0.1") for _ in range(app.state.settings.sse_per_ip)]
            r = await http.get("/api/v1/stream/stations", params={"after": "x" * 200})
            assert sse(r.text)[0]["event"] == "resync_required"
            assert all(c.end is None for c in mine)
            for c in mine:
                hub.unregister(c)
            # Last-Event-ID wins over ?after=: a valid after does not rescue a bad header.
            r = await http.get(
                "/api/v1/stream/stations",
                params={"after": f"{STREAM}@1"},
                headers={"Last-Event-ID": "bad"},
            )
            assert sse(r.text)[0]["event"] == "resync_required"
            # Instance full: the new connection is the one refused.
            taken = [hub.register("10.9.9.1"), hub.register("10.9.9.2")]
            r = await http.get("/api/v1/stream/stations")
            assert sse(r.text) == [{"event": "stream_closed", "data": '{"reason":"capacity"}'}]
            assert all(c.end is None for c in taken)
            # Shutdown: what is open and what arrives later get stream_closed with retry.
            app.state.shutdown.set()
            for _ in range(100):
                if hub.closing:
                    break
                await asyncio.sleep(0.01)
            assert all(c.end == "shutdown" for c in taken)
            r = await http.get("/api/v1/stream/stations")
            assert sse(r.text) == [
                {"event": "stream_closed", "data": '{"reason":"shutdown"}', "retry": "2000"}
            ]

    run(go)
