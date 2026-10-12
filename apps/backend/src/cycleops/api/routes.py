"""REST handlers: snapshot, history, feed-health and the health checks. app.py registers
them (GET only). Each request borrows a pooled connection for one short read."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import HTTPException, Path, Query, Request, Response
from pydantic import AwareDatetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from cycleops.adapters.postgres import queries
from cycleops.contracts import Contract

DEFAULT_WINDOW = timedelta(hours=6)
MAX_WINDOW = timedelta(hours=24)
NO_STREAM = "no stream yet: the worker has not started one"


def _engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def _json(model: Contract) -> Response:
    return Response(model.model_dump_json(), media_type="application/json")


async def live() -> dict[str, str]:
    return {"status": "ok"}


async def ready(request: Request) -> dict[str, str]:
    """The process answers and reaches PostgreSQL. The GBFS source does not count."""
    async with _engine(request).connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


async def snapshot(request: Request) -> Response:
    async with _engine(request).connect() as conn:
        found = await queries.snapshot(conn)
    if found is None:
        raise HTTPException(503, NO_STREAM)
    return _json(found)


async def history(
    request: Request,
    station_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> Response:
    """Default: the last 6 h up to now. At most 24 h."""
    end = end or datetime.now(UTC)
    start = start or end - DEFAULT_WINDOW
    if not start < end <= start + MAX_WINDOW:
        raise HTTPException(422, "from must be before to, at most 24 h apart")
    async with _engine(request).connect() as conn:
        stream_id = await queries.active_stream(conn)
        if stream_id is None:
            raise HTTPException(503, NO_STREAM)
        found = await queries.history(conn, stream_id, station_id, start, end)
    if found is None:
        raise HTTPException(404, "unknown station")
    return _json(found)


async def feed_health(request: Request) -> Response:
    async with _engine(request).connect() as conn:
        found = await queries.feed_health(conn)
    if found is None:
        raise HTTPException(503, NO_STREAM)
    return _json(found)


async def feed_diagnostics(
    request: Request,
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> Response:
    end = end or datetime.now(UTC)
    start = start or end - DEFAULT_WINDOW
    if not start < end <= start + MAX_WINDOW:
        raise HTTPException(422, "from must be before to, at most 24 h apart")
    async with _engine(request).connect() as conn:
        found = await queries.feed_diagnostics(conn, start, end)
    if found is None:
        raise HTTPException(503, NO_STREAM)
    return _json(found)
