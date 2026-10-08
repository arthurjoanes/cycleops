"""Read side for the API. Every call opens and ends its own short transaction, so the
connection passed in must have none open."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops.adapters.postgres.tables import (
    event_log,
    station_current,
    station_incidents,
    station_observations,
    stations,
    stream_heads,
)
from cycleops.contracts import (
    SCHEMA_VERSION,
    CatalogStation,
    Coverage,
    DataMode,
    FeedHealth,
    HistoryPoint,
    IncidentItem,
    Interval,
    Snapshot,
    SourceState,
    StationHistory,
    StationState,
)
from cycleops.domain.model import FINAL_STATES

_h = stream_heads.c
_HEAD = (
    select(
        _h.stream_id,
        _h.system_id,
        _h.mode,
        _h.seed,
        _h.head_sequence,
        _h.catalog_version,
        _h.source_state,
        _h.source_state_since,
        _h.last_source_updated_at,
        _h.worker_seen_at,
        _h.source_stale_after_seconds,
        _h.last_attempt_at,
        _h.last_outcome,
        _h.coverage_valid,
        _h.coverage_total,
        _h.breaker_open_until,
        _h.quarantined_feeds,
        func.now().label("server_time"),
    )
    .order_by(_h.created_at.desc(), _h.stream_id.desc())
    .limit(1)
)


@dataclass(frozen=True)
class Head:
    stream_id: str
    system_id: str
    mode: DataMode
    seed: int | None
    head_sequence: int
    catalog_version: str
    feed_health: FeedHealth
    # The database's now(), the instant the stale rule was applied at.
    server_time: datetime

    @property
    def cursor(self) -> str:
        return f"{self.stream_id}@{self.head_sequence}"


def api_source_state(
    stored: SourceState,
    stored_since: datetime | None,
    last_source_updated_at: datetime | None,
    worker_seen_at: datetime | None,
    stale_after_seconds: int,
    now: datetime,
) -> tuple[SourceState, datetime | None]:
    """The API's stale rule, for heartbeat, snapshot and feed-health alike: the state the
    worker stored and its start, turned into stale only from fresh or late, when the last
    publication or the worker's last tick is older than sourceStaleAfterSeconds (worker
    stopped). Stale then starts when the older of the two crossed that limit."""
    if stored not in ("fresh", "late"):
        return stored, stored_since
    if last_source_updated_at is None or worker_seen_at is None:
        return "stale", None
    crossed = min(last_source_updated_at, worker_seen_at) + timedelta(seconds=stale_after_seconds)
    return ("stale", crossed) if now > crossed else (stored, stored_since)


def _head(row: Any) -> Head:
    now = row.server_time
    breaker = row.breaker_open_until
    state, since = api_source_state(
        row.source_state,
        row.source_state_since,
        row.last_source_updated_at,
        row.worker_seen_at,
        row.source_stale_after_seconds,
        now,
    )
    return Head(
        stream_id=row.stream_id,
        system_id=row.system_id,
        mode=row.mode,
        seed=row.seed,
        head_sequence=row.head_sequence,
        catalog_version=row.catalog_version,
        feed_health=FeedHealth(
            state=state,
            state_since=since,
            last_source_updated_at=row.last_source_updated_at,
            last_attempt_at=row.last_attempt_at,
            last_outcome=row.last_outcome,
            coverage=Coverage(valid=row.coverage_valid, total=row.coverage_total),
            breaker_open_until=breaker if breaker is not None and breaker > now else None,
            quarantined_feeds=sorted(row.quarantined_feeds),
        ),
        server_time=now,
    )


@asynccontextmanager
async def _snapshot_read(conn: AsyncConnection) -> AsyncIterator[None]:
    """Several reads that must see one instant. The isolation is set before the first
    query, which is what takes the transaction's snapshot."""
    async with conn.begin():
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        yield


async def _active(conn: AsyncConnection) -> Head | None:
    row = (await conn.execute(_HEAD)).first()
    return _head(row) if row is not None else None


async def head(conn: AsyncConnection) -> Head | None:
    """The active stream (the most recently created), with the stale rule applied."""
    async with conn.begin():
        return await _active(conn)


async def active_stream(conn: AsyncConnection) -> str | None:
    found = await head(conn)
    return found.stream_id if found else None


async def feed_health(conn: AsyncConnection) -> FeedHealth | None:
    found = await head(conn)
    return found.feed_health if found else None


async def snapshot(conn: AsyncConnection) -> Snapshot | None:
    """None while no stream exists. Only open incidents: the ones that reached a final
    state went out once, in the event of their transition."""
    async with _snapshot_read(conn):
        found = await _active(conn)
        if found is None:
            return None
        catalog = (
            await conn.execute(
                select(
                    stations.c.station_id,
                    stations.c.name,
                    stations.c.lat,
                    stations.c.lon,
                    stations.c.capacity,
                    stations.c.is_virtual,
                )
                .where(stations.c.system_id == found.system_id, stations.c.removed_at.is_(None))
                .order_by(stations.c.station_id)
            )
        ).all()
        current = (
            await conn.execute(
                select(station_current.c.availability, station_current.c.decision)
                .where(
                    station_current.c.stream_id == found.stream_id,
                    station_current.c.decision.is_not(None),
                )
                .order_by(station_current.c.station_id)
            )
        ).all()
    return Snapshot(
        schema_version=SCHEMA_VERSION,
        stream_id=found.stream_id,
        mode=found.mode,
        system_id=found.system_id,
        seed=found.seed,
        cursor=found.cursor,
        event_schema_version=SCHEMA_VERSION,
        catalog_version=found.catalog_version,
        feed_health=found.feed_health,
        catalog=[CatalogStation.model_validate(r._asdict()) for r in catalog],
        stations=[
            StationState.model_validate(
                {
                    "availability": r.availability,
                    "decision": {
                        **r.decision,
                        "incidents": [
                            i for i in r.decision["incidents"] if i["state"] not in FINAL_STATES
                        ],
                    },
                }
            )
            for r in current
        ],
    )


async def history(
    conn: AsyncConnection, stream_id: str, station_id: str, start: datetime, end: datetime
) -> StationHistory | None:
    """Changes of one station in [start, end], by commit instant, plus the last change
    before start (the values in effect when the window opens). None when the stream or
    the station is unknown. Gap and closed intervals come from the conditions each change
    carries: the engine puts the source state into every station's conditions. An
    interval still open at the last change in the window has end None."""
    o = station_observations.c
    async with _snapshot_read(conn):
        known = (
            await conn.execute(
                select(stations.c.station_id)
                .select_from(
                    stations.join(stream_heads, stream_heads.c.system_id == stations.c.system_id)
                )
                .where(stream_heads.c.stream_id == stream_id, stations.c.station_id == station_id)
            )
        ).first()
        if known is None:
            return None
        columns = select(
            o.sequence,
            o.source_updated_at,
            o.committed_at,
            o.bikes_available,
            o.docks_available,
            o.quality,
            o.primary_condition,
            o.conditions,
        ).where(o.stream_id == stream_id, o.station_id == station_id)
        before = (
            await conn.execute(
                columns.where(o.committed_at < start).order_by(o.sequence.desc()).limit(1)
            )
        ).all()
        inside = (
            await conn.execute(
                columns.where(o.committed_at >= start, o.committed_at <= end).order_by(o.sequence)
            )
        ).all()
        i = station_incidents.c
        incidents = (
            await conn.execute(
                select(station_incidents)
                .where(
                    i.stream_id == stream_id,
                    i.station_id == station_id,
                    i.first_observed_at <= end,
                    or_(i.closed_at.is_(None), i.closed_at >= start),
                )
                .order_by(i.first_observed_at, i.id)
            )
        ).all()
    rows = [*before, *inside]
    gaps, closed = _intervals(rows, start)
    return StationHistory(
        stream_id=stream_id,
        station_id=station_id,
        window_start=start,
        window_end=end,
        points=[
            HistoryPoint(
                source_updated_at=r.source_updated_at,
                bikes_available=r.bikes_available,
                docks_available=r.docks_available,
                quality=r.quality,
                primary_condition=r.primary_condition,
                at=r.committed_at,
            )
            for r in rows
            if r.source_updated_at is not None
        ],
        incidents=[
            IncidentItem(
                id=r.id,
                kind=r.kind,
                state=r.state,
                suspended_from=r.suspended_from if r.state == "unverifiable" else None,
                first_observed_at=r.first_observed_at,
                observed_since=r.observed_since,
                confirmed_at=r.confirmed_at,
                close_reason=r.close_reason,
            )
            for r in incidents
        ],
        gaps=gaps,
        closed=closed,
    )


def _intervals(rows: Sequence[Any], start: datetime) -> tuple[list[Interval], list[Interval]]:
    """Gap: unverifiable (source unknown, stale or quarantined, or the station's own
    reason). Closed: outside service hours, which wins over a gap."""
    out: dict[str, list[Interval]] = {"gap": [], "closed": []}
    kind: Literal["gap", "closed"] | None = None
    since = start
    for r in rows:
        now: Literal["gap", "closed"] | None = (
            "closed"
            if "outside_service_hours" in r.conditions
            else "gap"
            if "unverifiable" in r.conditions
            else None
        )
        if now == kind:
            continue
        at = max(r.committed_at, start)
        if kind is not None:
            out[kind].append(Interval(start=since, end=at))
        kind, since = now, at
    if kind is not None:
        out[kind].append(Interval(start=since, end=None))
    return out["gap"], out["closed"]


async def events_after(
    conn: AsyncConnection, stream_id: str, after_sequence: int, limit: int = 100
) -> list[dict[str, Any]]:
    """Event payloads with sequence > after_sequence, in order. A first sequence other
    than after_sequence + 1 means the cursor fell out of the retention."""
    async with conn.begin():
        rows = await conn.execute(
            select(event_log.c.payload)
            .where(event_log.c.stream_id == stream_id, event_log.c.sequence > after_sequence)
            .order_by(event_log.c.sequence)
            .limit(limit)
        )
        return [r.payload for r in rows]
