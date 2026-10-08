"""The API's FastAPI app: settings, lifespan (engine, tailer, latency log) and one ASGI
middleware for the request id, the REST rate limit, the JSON access log and the 500 body.

Read only: GET routes, a SELECT-only database role, no CORS, no cookies.
"""

import asyncio
import logging
import math
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cycleops.adapters.postgres.connect import create_engine
from cycleops.api import routes, stream
from cycleops.api.tailer import Hub, Tailer
from cycleops.contracts import FeedHealth, Snapshot, StationHistory

log = logging.getLogger("cycleops.api")
access = logging.getLogger("cycleops.api.access")

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_CODES = {404: "not_found", 405: "method_not_allowed", 422: "invalid_request", 503: "unavailable"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    sse_per_ip: int = 4
    sse_per_instance: int = 100
    rest_per_minute_per_ip: int = 60
    # How long a request waits for a pooled connection before a 503.
    pool_timeout: float = 5
    heartbeat_seconds: float = 15

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        """ValueError names the variable that is missing or invalid."""
        url = env.get("DATABASE_URL")
        if not url:
            raise ValueError("DATABASE_URL is not set")
        return cls(
            database_url=url,
            sse_per_ip=_positive(env, "CYCLEOPS_SSE_PER_IP", cls.sse_per_ip),
            sse_per_instance=_positive(env, "CYCLEOPS_SSE_PER_INSTANCE", cls.sse_per_instance),
            rest_per_minute_per_ip=_positive(
                env, "CYCLEOPS_REST_PER_MINUTE_PER_IP", cls.rest_per_minute_per_ip
            ),
        )


def _positive(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return int(raw)


def create_app(settings: Settings) -> FastAPI:
    """Nothing connects here; the lifespan opens the pool and starts the tailer."""
    # No docs or OpenAPI routes: the contracts are published from Pydantic, and the
    # inventory stays the routes below.
    app = FastAPI(
        title="CycleOps", lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.hub = Hub(settings.sse_per_ip, settings.sse_per_instance)
    app.state.latency = stream.Latency()
    # Set by the server on SIGTERM; open streams send stream_closed and end.
    app.state.shutdown = asyncio.Event()
    # Every route, registered flat so app.routes is the inventory: GET only.
    app.get("/health/live")(routes.live)
    app.get("/health/ready")(routes.ready)
    app.get("/api/v1/stations/snapshot", responses={200: {"model": Snapshot}})(routes.snapshot)
    app.get("/api/v1/stations/{station_id}/history", responses={200: {"model": StationHistory}})(
        routes.history
    )
    app.get("/api/v1/feed-health", responses={200: {"model": FeedHealth}})(routes.feed_health)
    app.get(stream.PATH)(stream.stream_stations)
    app.add_middleware(RequestMiddleware, rest_per_minute=settings.rest_per_minute_per_ip)
    app.add_exception_handler(HTTPException, _http_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _invalid)  # type: ignore[arg-type]
    for exc in stream.DB_DOWN:
        app.add_exception_handler(exc, _database_unavailable)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # 5 + 5 per process; streams only borrow a connection for a read.
    app.state.engine = create_engine(settings.database_url, pool_timeout=settings.pool_timeout)
    app.state.tailer = Tailer(settings.database_url, app.state.hub)
    tasks = [
        asyncio.create_task(app.state.tailer.run()),
        asyncio.create_task(stream.report_latency(app.state.latency)),
        asyncio.create_task(_close_streams_on_exit(app)),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.state.engine.dispose()


async def _close_streams_on_exit(app: FastAPI) -> None:
    await app.state.shutdown.wait()
    app.state.hub.close_all()


def error(
    status: int,
    code: str,
    message: str,
    request_id: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """The error body of every route: no stack trace, ever."""
    return JSONResponse(
        {"code": code, "message": message, "requestId": request_id, "details": details},
        status_code=status,
        headers=headers,
    )


async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error(
        exc.status_code,
        _CODES.get(exc.status_code, f"http_{exc.status_code}"),
        str(exc.detail),
        request.state.request_id,
        headers=exc.headers,
    )


async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Without "input" and "ctx": the request's own values are not echoed back.
    details = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return error(422, "invalid_request", "invalid request", request.state.request_id, details)


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.warning("database unavailable", extra={"error": type(exc).__name__})
    request.state.log = {"error": "database_unavailable"}
    return error(503, "database_unavailable", "database unavailable", request.state.request_id)


class RequestMiddleware:
    """Request id (X-Request-ID, echoed), REST rate limit per IP, JSON access log and the
    500 body. Pure ASGI, so SSE responses pass through unbuffered."""

    def __init__(self, app: ASGIApp, rest_per_minute: int) -> None:
        self.app = app
        self.limit = rest_per_minute
        self.window = 0
        self.counts: dict[str, int] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        status = 0

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers.setdefault("Cache-Control", "no-store")
            await send(message)

        try:
            retry_after = self._limited(scope)
            if retry_after is not None:
                state["log"] = {"error": "rate_limited"}
                response = error(
                    429,
                    "rate_limited",
                    "too many requests",
                    request_id,
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send_with_id)
            else:
                await self.app(scope, receive, send_with_id)
        except Exception:
            if status:
                # The response started (a stream): the server drops the connection.
                raise
            log.exception("unhandled error", extra={"requestId": request_id})
            state["log"] = {"error": "internal_error"}
            await error(500, "internal_error", "internal error", request_id)(
                scope, receive, send_with_id
            )
        finally:
            # Never the query string or a body: only what identifies the request.
            access.info(
                "request",
                extra={
                    "requestId": request_id,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    **state.get("log", {}),
                },
            )

    def _limited(self, scope: Scope) -> int | None:
        """Seconds until the window resets when the IP is over the limit. REST only: the
        stream has its own limits and /health/* is for the container healthcheck."""
        path: str = scope["path"]
        if not path.startswith("/api/") or path == stream.PATH:
            return None
        # ponytail: fixed one-minute window, a burst of up to 2x across a boundary;
        # a sliding log if that ever matters.
        now = time.monotonic()
        window = int(now // 60)
        if window != self.window:
            self.window, self.counts = window, {}
        ip = scope["client"][0] if scope.get("client") else "unknown"
        self.counts[ip] = self.counts.get(ip, 0) + 1
        if self.counts[ip] <= self.limit:
            return None
        return max(1, math.ceil((window + 1) * 60 - now))
