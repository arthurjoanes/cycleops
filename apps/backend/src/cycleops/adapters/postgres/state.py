"""StreamState <-> rows.

StateWriter.load() reads the whole state of a stream. save() writes, inside the caller's
transaction, only the rows that differ from what this writer last loaded or wrote.
Instants inside jsonb keep full precision (isoformat): a restart must give back exactly
the state it saved, or the next step could publish something else.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Table, and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from cycleops.adapters.gbfs import StationInfo, StationStatus
from cycleops.adapters.postgres.tables import (
    station_current,
    station_incidents,
    stations,
    stream_heads,
)
from cycleops.contracts import DecisionFacts, StationBatchEvent
from cycleops.domain.model import FINAL_STATES, Incident, Station, StreamState

Row = dict[str, Any]
_UNPUBLISHED: Row = {"decision": None, "availability": None, "last_sequence": None}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _instant(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _status_json(status: StationStatus | None) -> Row | None:
    # vars(), not asdict(): no deep copy, and the tuples serialize as JSON lists.
    if status is None:
        return None
    return {**vars(status), "reported_at": _iso(status.reported_at)}


def _status(data: Row) -> StationStatus:
    return StationStatus(
        **{
            **data,
            "vehicle_types": tuple((t, c) for t, c in data["vehicle_types"]),
            "reported_at": _instant(data["reported_at"]),
            "quality_flags": tuple(data["quality_flags"]),
        }
    )


def _facts_json(facts: DecisionFacts | None) -> Row | None:
    if facts is None:
        return None
    data = facts.model_dump(by_alias=False)
    return {**data, "source_updated_at": _iso(facts.source_updated_at)}


def _facts(data: Row | None) -> DecisionFacts | None:
    return DecisionFacts.model_validate(data) if data is not None else None


def _tuples(value: Any) -> Any:
    """The change key is made of tuples only; jsonb gives them back as lists."""
    return tuple(_tuples(v) for v in value) if isinstance(value, list) else value


def _head_row(state: StreamState, stale_after_seconds: int) -> Row:
    return {
        "stream_id": state.stream_id,
        "system_id": state.system_id,
        "mode": state.mode,
        "generation": state.stream_id.rsplit(":", 1)[-1],
        "seed": state.seed,
        "head_sequence": state.head_sequence,
        "catalog_version": state.catalog_version,
        "policy_version": state.policy_version,
        "clock_now": state.clock_now,
        "source_state": state.source_state,
        "source_state_since": state.source_state_since,
        "last_source_updated_at": state.last_source_updated_at,
        "last_content_hash": state.last_content_hash,
        "fresh_again_count": state.fresh_again_count,
        "source_stale_after_seconds": stale_after_seconds,
        "last_attempt_at": state.last_attempt_at,
        "last_outcome": state.last_outcome,
        # The FeedHealth coverage: catalog stations with a valid record over the catalog
        # plus the status ids without a catalog entry.
        "coverage_valid": sum(st.presence == "valid" for st in state.stations.values()),
        "coverage_total": len(state.stations) + len(state.unknown_ids),
        "breaker_open_until": state.breaker_open_until,
        "quarantined_since": state.quarantined_since,
        "quarantined_feeds": sorted(state.quarantined_feeds),
        "ingestion_suspended": state.ingestion_suspended,
        "catalog_fetch_pending": state.catalog_fetch_pending,
        "engine": {
            "service_checked_at": _iso(state.service_checked_at),
            "last_tick_at": _iso(state.last_tick_at),
            "quarantine_ended_at": _iso(state.quarantine_ended_at),
            "in_service": state.in_service,
            "latched": state.latched,
            "awaiting_since": _iso(state.awaiting_since),
            "opened_at": _iso(state.opened_at),
            "first_accepted_at": _iso(state.first_accepted_at),
            "catalog_fetch_requested_for": state.catalog_fetch_requested_for,
            "unknown_ids": sorted(state.unknown_ids),
            "unknown_records": {
                sid: _status_json(record) for sid, record in sorted(state.unknown_records.items())
            },
            "removed_ids": sorted(state.removed_ids),
            "last_event_feed_state": state.last_event_feed_state,
        },
    }


def _station_row(state: StreamState, st: Station) -> Row:
    info = st.info
    return {
        "system_id": state.system_id,
        "station_id": st.station_id,
        "name": info.name,
        "lat": info.lat,
        "lon": info.lon,
        "capacity": info.capacity,
        "is_virtual": info.is_virtual,
        "is_valet_station": info.is_valet,
        "catalog_version": state.catalog_version,
        "first_seen_at": st.first_seen_at,
        "last_seen_in_catalog_at": st.last_seen_in_catalog_at,
        "removed_at": None,
        "info": dict(vars(info)),
        "engine": {
            "missing_catalog_fetches": st.missing_catalog_fetches,
            "tracked_since": _iso(st.tracked_since),
        },
    }


def _current_row(state: StreamState, st: Station, published: Row) -> Row:
    status = st.status
    return {
        "system_id": state.system_id,
        "station_id": st.station_id,
        "stream_id": state.stream_id,
        "bikes_available": status.bikes_available if status else None,
        "docks_available": status.docks_available if status else None,
        "vehicle_types": [list(t) for t in status.vehicle_types] if status else [],
        "is_installed": status.is_installed if status else None,
        "is_renting": status.is_renting if status else None,
        "is_returning": status.is_returning if status else None,
        "source_updated_at": st.status_updated_at,
        "station_reported_at": status.reported_at if status else None,
        "quality": status.quality if status else "unknown",
        "quality_flags": list(status.quality_flags) if status else [],
        **published,
        "engine": {
            "status": _status_json(status),
            "status_updated_at": _iso(st.status_updated_at),
            "presence": st.presence,
            # Ids in dict order: step() walks the open incidents in that order, and the
            # last transition sets the station's reason_text.
            "incidents": [inc.id for inc in st.incidents.values()],
            "finished": [inc.id for inc in st.finished],
            "seen_without": sorted(st.seen_without),
            "reason_text": st.reason_text,
            "facts": _facts_json(st.facts),
            "published_key": st.published_key,
        },
    }


def _incident_row(state: StreamState, station_id: str, inc: Incident) -> Row:
    # While recovering (and once resolved) the observations are the recovery ones.
    recovering = inc.state in ("recovering", "resolved") and inc.observations
    return {
        "id": inc.id,
        "system_id": state.system_id,
        "station_id": station_id,
        "stream_id": state.stream_id,
        "kind": inc.kind,
        "state": inc.state,
        "suspended_from": inc.suspended_from,
        "unverifiable_reason": inc.unverifiable_reason,
        "at_opening": inc.at_opening,
        "first_observed_at": inc.first_observed_at,
        "observed_since": inc.observed_since,
        "confirmed_at": inc.confirmed_at,
        "recovering_since": inc.observations[0] if recovering else None,
        "closed_at": inc.closed_at,
        "close_reason": inc.close_reason,
        "observations": [o.isoformat() for o in inc.observations],
        "reason_text": inc.reason_text,
        "facts": _facts_json(inc.facts),
        "policy_version": inc.policy_version,
    }


def _incident(row: Any) -> Incident:
    return Incident(
        id=row.id,
        kind=row.kind,
        state=row.state,
        first_observed_at=row.first_observed_at,
        observed_since=row.observed_since,
        policy_version=row.policy_version,
        at_opening=row.at_opening,
        confirmed_at=row.confirmed_at,
        suspended_from=row.suspended_from,
        unverifiable_reason=row.unverifiable_reason,
        close_reason=row.close_reason,
        closed_at=row.closed_at,
        observations=[datetime.fromisoformat(o) for o in row.observations],
        reason_text=row.reason_text,
        facts=_facts(row.facts),
    )


async def _upsert(conn: AsyncConnection, table: Table, keys: list[str], rows: list[Row]) -> None:
    if not rows:
        return
    stmt = insert(table)
    stmt = stmt.on_conflict_do_update(
        index_elements=keys,
        set_={c.name: stmt.excluded[c.name] for c in table.columns if c.name in rows[0]},
    )
    await conn.execute(stmt, rows)


class StateWriter:
    def __init__(self, stale_after_seconds: int) -> None:
        self._stale_after = stale_after_seconds
        # None: the stream has no row yet.
        self._head: Row | None = None
        self._stations: dict[str, Row] = {}
        self._current: dict[str, Row] = {}
        # decision, availability and last_sequence as last published, per station.
        self._published: dict[str, Row] = {}
        # id -> (station_id, the Incident object, its row as last written).
        self._incidents: dict[str, tuple[str, Incident, Row]] = {}

    async def load(self, conn: AsyncConnection, stream_id: str) -> StreamState:
        head = (
            (await conn.execute(select(stream_heads).where(stream_heads.c.stream_id == stream_id)))
            .mappings()
            .one()
        )
        e = head["engine"]
        state = StreamState(
            system_id=head["system_id"],
            mode=head["mode"],
            stream_id=stream_id,
            seed=head["seed"],
            policy_version=head["policy_version"],
            head_sequence=head["head_sequence"],
            clock_now=head["clock_now"],
            # Absent in rows saved before it existed.
            last_tick_at=_instant(e.get("last_tick_at")),
            quarantine_ended_at=_instant(e.get("quarantine_ended_at")),
            service_checked_at=_instant(e["service_checked_at"]),
            in_service=e["in_service"],
            source_state=head["source_state"],
            source_state_since=head["source_state_since"],
            latched=e["latched"],
            fresh_again_count=head["fresh_again_count"],
            awaiting_since=_instant(e["awaiting_since"]),
            opened_at=_instant(e["opened_at"]),
            last_source_updated_at=head["last_source_updated_at"],
            first_accepted_at=_instant(e["first_accepted_at"]),
            last_content_hash=head["last_content_hash"],
            last_attempt_at=head["last_attempt_at"],
            last_outcome=head["last_outcome"],
            breaker_open_until=head["breaker_open_until"],
            quarantined_since=head["quarantined_since"],
            quarantined_feeds=set(head["quarantined_feeds"]),
            ingestion_suspended=head["ingestion_suspended"],
            catalog_version=head["catalog_version"],
            catalog_fetch_pending=head["catalog_fetch_pending"],
            catalog_fetch_requested_for=e["catalog_fetch_requested_for"],
            unknown_ids=frozenset(e["unknown_ids"]),
            unknown_records={sid: _status(r) for sid, r in e["unknown_records"].items()},
            removed_ids=set(e["removed_ids"]),
            last_event_feed_state=e["last_event_feed_state"],
        )
        rows = (
            await conn.execute(
                select(
                    stations.c.station_id,
                    stations.c.info,
                    stations.c.first_seen_at,
                    stations.c.last_seen_in_catalog_at,
                    stations.c.engine.label("tracking"),
                    station_current.c.engine,
                    station_current.c.decision,
                    station_current.c.availability,
                    station_current.c.last_sequence,
                )
                .select_from(
                    stations.join(
                        station_current,
                        and_(
                            station_current.c.system_id == stations.c.system_id,
                            station_current.c.station_id == stations.c.station_id,
                        ),
                    )
                )
                .where(
                    stations.c.system_id == state.system_id,
                    stations.c.removed_at.is_(None),
                    station_current.c.stream_id == stream_id,
                )
            )
        ).all()
        pending = [i for r in rows for i in r.engine["finished"]]
        by_id = {
            r.id: _incident(r)
            for r in await conn.execute(
                select(station_incidents).where(
                    station_incidents.c.stream_id == stream_id,
                    or_(
                        station_incidents.c.state.not_in(FINAL_STATES),
                        station_incidents.c.id.in_(pending),
                    ),
                )
            )
        }
        self._published = {}
        for r in rows:
            e = r.engine
            open_incidents = [by_id[i] for i in e["incidents"]]
            state.stations[r.station_id] = Station(
                station_id=r.station_id,
                info=StationInfo(**{**r.info, "flags": tuple(r.info["flags"])}),
                first_seen_at=r.first_seen_at,
                last_seen_in_catalog_at=r.last_seen_in_catalog_at,
                missing_catalog_fetches=r.tracking["missing_catalog_fetches"],
                tracked_since=_instant(r.tracking["tracked_since"]),
                status=_status(e["status"]) if e["status"] is not None else None,
                status_updated_at=_instant(e["status_updated_at"]),
                presence=e["presence"],
                incidents={inc.kind: inc for inc in open_incidents},
                # A pending one past the 7-day purge (worker down that long) is dropped.
                finished=[by_id[i] for i in e["finished"] if i in by_id],
                seen_without=set(e["seen_without"]),
                reason_text=e["reason_text"],
                facts=_facts(e["facts"]),
                published_key=_tuples(e["published_key"]),
            )
            if r.decision is not None:
                self._published[r.station_id] = {
                    "decision": r.decision,
                    "availability": r.availability,
                    "last_sequence": r.last_sequence,
                }
        # Rows rebuilt from the loaded state equal the stored ones, except the head, whose
        # stale_after comes from the policy in use: an empty dict makes the first save
        # write it.
        self._head = {}
        self._stations = {sid: _station_row(state, st) for sid, st in state.stations.items()}
        self._current = {
            sid: _current_row(state, st, self._published.get(sid, _UNPUBLISHED))
            for sid, st in state.stations.items()
        }
        self._incidents = {
            inc.id: (sid, inc, _incident_row(state, sid, inc))
            for sid, st in state.stations.items()
            for inc in [*st.incidents.values(), *st.finished]
        }
        return state

    async def save(
        self, conn: AsyncConnection, state: StreamState, events: list[StationBatchEvent]
    ) -> None:
        for event in events:
            for s in event.stations:
                self._published[s.decision.station_id] = {
                    "decision": s.decision.model_dump(mode="json"),
                    "availability": s.availability.model_dump(mode="json"),
                    "last_sequence": event.sequence,
                }

        head = _head_row(state, self._stale_after)
        if self._head is None:
            await conn.execute(insert(stream_heads).values(head))
        elif head != self._head:
            await conn.execute(
                update(stream_heads).where(stream_heads.c.stream_id == state.stream_id).values(head)
            )
        self._head = head

        station_rows = {sid: _station_row(state, st) for sid, st in state.stations.items()}
        await _upsert(
            conn, stations, ["system_id", "station_id"], _changed(station_rows, self._stations)
        )
        gone = sorted(self._stations.keys() - station_rows.keys())
        if gone:
            # Removed from the catalog: the row stays, marked; the current state goes.
            await conn.execute(
                update(stations)
                .where(stations.c.system_id == state.system_id, stations.c.station_id.in_(gone))
                .values(removed_at=state.clock_now)
            )
            await conn.execute(
                delete(station_current).where(
                    station_current.c.system_id == state.system_id,
                    station_current.c.station_id.in_(gone),
                )
            )
            for sid in gone:
                self._published.pop(sid, None)
        self._stations = station_rows

        current_rows = {
            sid: _current_row(state, st, self._published.get(sid, _UNPUBLISHED))
            for sid, st in state.stations.items()
        }
        await _upsert(
            conn,
            station_current,
            ["system_id", "station_id"],
            _changed(current_rows, self._current),
        )
        self._current = current_rows

        held = {
            inc.id: (sid, inc)
            for sid, st in state.stations.items()
            for inc in [*st.incidents.values(), *st.finished]
        }
        # An incident that reached a final state and went out in this step's event is no
        # longer in the state, but step() changes objects in place: the one kept here
        # holds its final values.
        written = {iid: (sid, inc) for iid, (sid, inc, _) in self._incidents.items()}
        incident_rows = {
            iid: (sid, inc, _incident_row(state, sid, inc))
            for iid, (sid, inc) in (written | held).items()
        }
        await _upsert(
            conn,
            station_incidents,
            ["id"],
            [
                row
                for iid, (_, _, row) in incident_rows.items()
                if iid not in self._incidents or self._incidents[iid][2] != row
            ],
        )
        self._incidents = {iid: v for iid, v in incident_rows.items() if iid in held}


def _changed(rows: dict[str, Row], previous: dict[str, Row]) -> list[Row]:
    return [row for key, row in rows.items() if previous.get(key) != row]
