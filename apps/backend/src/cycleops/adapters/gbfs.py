"""GBFS 2.3 adapter: validates a feed body and normalizes it.

A broken envelope, or a station feed with no valid record, rejects the whole publication
(ContractError). A broken station record only drops that record.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

GBFS_VERSION = "2.3"
# Bump when the normalization changes; it goes into the replay manifest.
ADAPTER_VERSION = "gbfs-2.3/1"

# Field names defined by GBFS 2.3. Anything else is counted as extra and ignored.
STATUS_FIELDS = frozenset(
    {
        "station_id",
        "num_bikes_available",
        "vehicle_types_available",
        "num_bikes_disabled",
        "num_docks_available",
        "vehicle_docks_available",
        "num_docks_disabled",
        "is_installed",
        "is_renting",
        "is_returning",
        "last_reported",
    }
)
INFO_FIELDS = frozenset(
    {
        "station_id",
        "name",
        "short_name",
        "lat",
        "lon",
        "address",
        "cross_street",
        "region_id",
        "post_code",
        "rental_methods",
        "is_virtual_station",
        "station_area",
        "parking_type",
        "parking_hoop",
        "contact_phone",
        "capacity",
        "vehicle_capacity",
        "vehicle_type_capacity",
        "is_valet_station",
        "is_charging_station",
        "rental_uris",
    }
)

ReportSemantics = Literal["feed_generation", "per_station"]
Quality = Literal["valid", "suspect", "unknown"]
Count = Annotated[int, Field(ge=0)]
# POSIX seconds from 1970 to 2100. Outside that, last_updated is a schema failure and
# last_reported counts as missing or future: never a datetime crash, never 0 as 1970.
POSIX_MAX = 4_102_444_800
PosixTime = Annotated[int, Field(gt=0, lt=POSIX_MAX)]


class ContractError(Exception):
    """The publication does not match GBFS 2.3 and is rejected as a whole."""


class _In(BaseModel):
    # Strict: no "5" -> 5 or true -> 1. Extra fields are ignored, never forbidden,
    # because Oslo sends num_vehicles_available.
    model_config = ConfigDict(extra="ignore", strict=True)


class _Envelope(_In):
    version: str
    last_updated: PosixTime
    ttl: Annotated[int, Field(ge=0, le=86_400)]
    data: dict[str, Any]


class _StationList(_In):
    stations: list[Any]


class _VehicleTypeCount(_In):
    vehicle_type_id: str
    count: Count


class _StatusRecord(_In):
    station_id: Annotated[str, Field(min_length=1)]
    is_installed: bool
    is_renting: bool
    is_returning: bool
    num_bikes_available: Count
    num_docks_available: Count | None = None
    num_bikes_disabled: Count | None = None
    num_docks_disabled: Count | None = None
    vehicle_types_available: list[_VehicleTypeCount] | None = None
    # Not range-checked here: a bad last_reported marks the station, it does not drop it.
    last_reported: int | None = None


class _InfoRecord(_In):
    station_id: Annotated[str, Field(min_length=1)]
    name: str
    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]
    capacity: int | None = None
    is_virtual_station: bool = False
    is_valet_station: bool | None = None


class _Feed(_In):
    name: str
    url: str


class _Language(_In):
    feeds: list[_Feed]


class _SystemInformation(_In):
    system_id: str
    language: str
    name: str
    timezone: str
    operator: str | None = None


@dataclass(frozen=True)
class Envelope:
    version: str
    last_updated: datetime
    ttl: int


@dataclass(frozen=True)
class StationStatus:
    station_id: str
    is_installed: bool
    is_renting: bool
    is_returning: bool
    bikes_available: int
    # None: the feed left it out. In GBFS 2.3 that means unlimited returns; here it
    # means unknown return capacity, never zero.
    docks_available: int | None
    vehicle_types: tuple[tuple[str, int], ...]
    reported_at: datetime | None
    quality: Quality
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class StatusPublication:
    envelope: Envelope
    stations: tuple[StationStatus, ...]
    invalid_station_ids: tuple[str, ...]
    record_count: int
    distinct_last_reported: int
    extra_fields: dict[str, int]


@dataclass(frozen=True)
class StationInfo:
    station_id: str
    name: str
    lat: float
    lon: float
    capacity: int | None
    is_virtual: bool
    is_valet: bool | None
    flags: tuple[str, ...]


@dataclass(frozen=True)
class InfoPublication:
    envelope: Envelope
    stations: tuple[StationInfo, ...]
    invalid_count: int
    extra_fields: dict[str, int]


@dataclass(frozen=True)
class SystemInformation:
    envelope: Envelope
    system_id: str
    name: str
    operator: str | None
    timezone: str


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_utc(posix: int) -> datetime:
    return _EPOCH + timedelta(seconds=posix)


def parse_envelope(body: Any) -> tuple[Envelope, dict[str, Any]]:
    try:
        env = _Envelope.model_validate(body)
    except ValidationError as exc:
        raise ContractError(f"envelope: {_summary(exc)}") from exc
    if env.version != GBFS_VERSION:
        raise ContractError(f"version {env.version!r}")
    return Envelope(env.version, to_utc(env.last_updated), env.ttl), env.data


def _station_list(data: dict[str, Any]) -> list[Any]:
    try:
        return _StationList.model_validate(data).stations
    except ValidationError as exc:
        raise ContractError(f"data.stations: {_summary(exc)}") from exc


def _extra_fields(records: Iterable[Any], known: frozenset[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        if isinstance(record, dict):
            counts.update(key for key in record if key not in known)
    return dict(sorted(counts.items()))


def parse_station_status(
    body: Any,
    semantics: ReportSemantics = "feed_generation",
    future_after: datetime | None = None,
) -> StatusPublication:
    """`future_after`: instant past which last_reported counts as clock in the future.
    The domain passes now + skew tolerance; collection only checks the contract."""
    envelope, data = parse_envelope(body)
    raw = _station_list(data)
    stations: list[StationStatus] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for item in raw:
        try:
            rec = _StatusRecord.model_validate(item)
        except ValidationError:
            if isinstance(item, dict) and isinstance(item.get("station_id"), str):
                invalid.append(item["station_id"])
            continue
        if rec.station_id in seen:
            invalid.append(rec.station_id)
            continue
        if (rec.last_reported or 0) <= 0 and semantics == "per_station":
            invalid.append(rec.station_id)
            continue
        seen.add(rec.station_id)
        stations.append(_normalize_status(rec, future_after))
    if not stations:
        raise ContractError("station_status without valid records")
    reported = {s.reported_at for s in stations if s.reported_at is not None}
    return StatusPublication(
        envelope=envelope,
        stations=tuple(stations),
        invalid_station_ids=tuple(invalid),
        record_count=len(raw),
        distinct_last_reported=len(reported),
        extra_fields=_extra_fields(raw, STATUS_FIELDS),
    )


def _normalize_status(rec: _StatusRecord, future_after: datetime | None) -> StationStatus:
    flags: list[str] = []
    raw = rec.last_reported or 0
    reported_at = to_utc(raw) if 0 < raw < POSIX_MAX else None
    if raw <= 0:
        flags.append("last_reported_missing")
    elif reported_at is None or (future_after is not None and reported_at > future_after):
        flags.append("last_reported_in_future")
    types = tuple((v.vehicle_type_id, v.count) for v in rec.vehicle_types_available or ())
    if rec.vehicle_types_available is not None and sum(c for _, c in types) != (
        rec.num_bikes_available
    ):
        flags.append("vehicle_types_mismatch")
    quality: Quality = "valid"
    if flags:
        quality = "unknown" if flags == ["last_reported_missing"] else "suspect"
    return StationStatus(
        station_id=rec.station_id,
        is_installed=rec.is_installed,
        is_renting=rec.is_renting,
        is_returning=rec.is_returning,
        bikes_available=rec.num_bikes_available,
        docks_available=rec.num_docks_available,
        vehicle_types=types,
        reported_at=reported_at,
        quality=quality,
        quality_flags=tuple(flags),
    )


def parse_station_information(body: Any) -> InfoPublication:
    envelope, data = parse_envelope(body)
    raw = _station_list(data)
    stations: list[StationInfo] = []
    seen: set[str] = set()
    invalid = 0
    for item in raw:
        try:
            rec = _InfoRecord.model_validate(item)
        except ValidationError:
            invalid += 1
            continue
        if rec.station_id in seen:
            invalid += 1
            continue
        seen.add(rec.station_id)
        flags: tuple[str, ...] = ()
        capacity = rec.capacity
        if capacity is None:
            flags = ("capacity_missing",)
        elif capacity <= 0:
            capacity, flags = None, ("capacity_invalid",)
        # station_area and rental_uris are dropped here on purpose.
        stations.append(
            StationInfo(
                station_id=rec.station_id,
                name=rec.name,
                lat=rec.lat,
                lon=rec.lon,
                capacity=capacity,
                is_virtual=rec.is_virtual_station,
                is_valet=rec.is_valet_station,
                flags=flags,
            )
        )
    if not stations:
        raise ContractError("station_information without valid records")
    return InfoPublication(envelope, tuple(stations), invalid, _extra_fields(raw, INFO_FIELDS))


def parse_discovery(body: Any) -> tuple[Envelope, dict[str, str]]:
    """Feed name -> URL from gbfs.json. Oslo lists one language (nb)."""
    envelope, data = parse_envelope(body)
    lang = "nb" if "nb" in data else next(iter(data), None)
    if lang is None:
        raise ContractError("gbfs.json without languages")
    try:
        feeds = _Language.model_validate(data[lang]).feeds
    except ValidationError as exc:
        raise ContractError(f"gbfs.json feeds: {_summary(exc)}") from exc
    return envelope, {f.name: f.url for f in feeds}


def parse_system_information(body: Any) -> SystemInformation:
    envelope, data = parse_envelope(body)
    try:
        info = _SystemInformation.model_validate(data)
    except ValidationError as exc:
        raise ContractError(f"system_information: {_summary(exc)}") from exc
    return SystemInformation(envelope, info.system_id, info.name, info.operator, info.timezone)


def parse_vehicle_types(body: Any) -> Envelope:
    envelope, data = parse_envelope(body)
    types = data.get("vehicle_types")
    if not isinstance(types, list) or not all(
        isinstance(t, dict) and isinstance(t.get("vehicle_type_id"), str) for t in types
    ):
        raise ContractError("vehicle_types: missing vehicle_type_id")
    return envelope


def _summary(exc: ValidationError) -> str:
    # Locations and error types only, no input values: stable enough to fingerprint.
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}:{e['type']}" for e in exc.errors()[:5]
    )
