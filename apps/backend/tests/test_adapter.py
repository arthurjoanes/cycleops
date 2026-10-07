from datetime import timedelta
from pathlib import Path

import pytest
from conftest import REPO, T0, info_body, station, status_body

from cycleops.adapters.gbfs import (
    ContractError,
    parse_discovery,
    parse_station_information,
    parse_station_status,
    parse_system_information,
    to_utc,
)
from cycleops.collection.outcome import classify
from cycleops.collection.recording import parse_iso, read_recording

RECORDED = sorted((REPO / "fixtures" / "recorded").glob("*.ndjson.gz"))


def test_unknown_version_rejects_the_publication() -> None:
    with pytest.raises(ContractError, match="version"):
        parse_station_status(status_body(version="3.0"))


@pytest.mark.parametrize(
    "body",
    [
        {"version": "2.3", "ttl": 15, "data": {"stations": []}},
        {"version": "2.3", "last_updated": "1791360000", "ttl": 15, "data": {"stations": []}},
        {"version": "2.3", "last_updated": T0, "ttl": 15, "data": {}},
        status_body(last_updated=0),
        status_body(last_updated=-1),
        status_body(last_updated=10**12),
        status_body(ttl=10**12),
        [],
    ],
)
def test_broken_envelope_rejects_the_publication(body: object) -> None:
    with pytest.raises(ContractError):
        parse_station_status(body)


def test_no_valid_record_rejects_the_publication() -> None:
    with pytest.raises(ContractError, match="without valid records"):
        parse_station_status(status_body([station(num_bikes_available=-1)]))


def test_extra_field_is_ignored_and_counted() -> None:
    pub = parse_station_status(status_body([station(), station(station_id="101")]))
    assert pub.extra_fields == {"num_vehicles_available": 2}
    assert len(pub.stations) == 2


def test_missing_docks_is_unknown_not_zero() -> None:
    pub = parse_station_status(status_body([station(num_docks_available=None)]))
    assert pub.stations[0].docks_available is None


@pytest.mark.parametrize(
    "bad",
    [
        {"num_bikes_available": -1},
        {"num_docks_available": -2},
        {"num_bikes_disabled": -1},
        {"num_bikes_available": "3"},
        {"is_renting": 1},
        {"is_installed": None},
        {"vehicle_types_available": [{"vehicle_type_id": "bike", "count": -1}]},
    ],
)
def test_bad_record_is_dropped_and_the_rest_goes_on(bad: dict[str, object]) -> None:
    pub = parse_station_status(status_body([station(station_id="1", **bad), station()]))
    assert [s.station_id for s in pub.stations] == ["100"]
    assert pub.record_count == 2
    assert pub.invalid_station_ids == ("1",)


def test_duplicate_station_id_keeps_the_first() -> None:
    pub = parse_station_status(status_body([station(), station(num_bikes_available=0)]))
    assert [s.bikes_available for s in pub.stations] == [3]
    assert pub.invalid_station_ids == ("100",)


def test_last_reported_after_last_updated_is_valid() -> None:
    pub = parse_station_status(
        status_body([station(last_reported=T0 + 13)]), future_after=to_utc(T0 + 15 + 30)
    )
    assert pub.stations[0].quality == "valid"
    assert pub.stations[0].reported_at == to_utc(T0 + 13)


def test_last_reported_in_the_future_is_suspect() -> None:
    received = to_utc(T0 + 15)
    pub = parse_station_status(
        status_body([station(last_reported=T0 + 120)]),
        future_after=received + timedelta(seconds=30),
    )
    assert pub.stations[0].quality == "suspect"
    assert "last_reported_in_future" in pub.stations[0].quality_flags


def test_missing_last_reported_depends_on_semantics() -> None:
    body = status_body([station(last_reported=None), station(station_id="101")])
    generation = parse_station_status(body, "feed_generation")
    assert generation.stations[0].quality == "unknown"
    assert generation.stations[0].reported_at is None
    per_station = parse_station_status(body, "per_station")
    assert [s.station_id for s in per_station.stations] == ["101"]
    assert per_station.invalid_station_ids == ("100",)


@pytest.mark.parametrize(
    ("value", "quality", "flag"),
    [
        (0, "unknown", "last_reported_missing"),
        (-5, "unknown", "last_reported_missing"),
        ((T0 + 4) * 1000, "suspect", "last_reported_in_future"),  # milliseconds
    ],
)
def test_odd_last_reported_marks_the_station_without_dropping_it(
    value: int, quality: str, flag: str
) -> None:
    pub = parse_station_status(status_body([station(last_reported=value)]))
    assert pub.stations[0].quality == quality
    assert pub.stations[0].quality_flags == (flag,)
    assert pub.stations[0].reported_at is None
    with pytest.raises(ContractError):
        parse_station_status(status_body([station(last_reported=0)]), "per_station")


def test_records_that_are_not_objects_are_dropped() -> None:
    pub = parse_station_status(status_body([5, None, [1], station(station_id=7), station()]))
    assert [s.station_id for s in pub.stations] == ["100"]
    assert pub.record_count == 5
    assert pub.invalid_station_ids == ()


def test_vehicle_type_counts_that_do_not_add_up_are_suspect() -> None:
    types = [{"vehicle_type_id": "bike", "count": 1}]
    pub = parse_station_status(status_body([station(vehicle_types_available=types)]))
    assert pub.stations[0].quality == "suspect"
    assert pub.stations[0].vehicle_types == (("bike", 1),)


def test_distinct_last_reported() -> None:
    pub = parse_station_status(
        status_body([station(), station(station_id="2"), station(station_id="3", last_reported=T0)])
    )
    assert pub.distinct_last_reported == 2


def test_station_information_drops_area_and_bad_capacity() -> None:
    body = info_body(
        [
            info_body()["data"]["stations"][0],
            {"station_id": "101", "name": "A", "lat": 59.9, "lon": 10.7, "capacity": 0},
            {"station_id": "102", "name": "B", "lat": 59.9, "lon": 10.7},
            {"station_id": "103", "name": "C", "lat": 99.0, "lon": 10.7},
        ]
    )
    pub = parse_station_information(body)
    by_id = {s.station_id: s for s in pub.stations}
    assert by_id["100"].capacity == 12 and by_id["100"].name == "Bjørvika"
    assert not hasattr(by_id["100"], "station_area")
    assert (by_id["101"].capacity, by_id["101"].flags) == (None, ("capacity_invalid",))
    assert (by_id["102"].capacity, by_id["102"].flags) == (None, ("capacity_missing",))
    assert pub.invalid_count == 1
    assert pub.extra_fields == {}


def test_discovery_prefers_nb() -> None:
    body = {
        "last_updated": T0,
        "ttl": 15,
        "version": "2.3",
        "data": {
            "en": {"feeds": [{"name": "station_status", "url": "https://x/en.json"}]},
            "nb": {"feeds": [{"name": "station_status", "url": "https://x/nb.json"}]},
        },
    }
    assert parse_discovery(body)[1] == {"station_status": "https://x/nb.json"}


# Contract tests over the lines recorded from the real feed (fixtures/recorded/).


def test_there_are_real_recordings() -> None:
    assert RECORDED, "fixtures/recorded/ should hold at least one recording of the real feed"


@pytest.mark.parametrize("path", RECORDED, ids=lambda p: Path(p).name)
def test_recorded_lines_pass_the_adapter(path: Path) -> None:
    lines = list(read_recording(path))
    assert lines
    for line in lines:
        assert line["recordingSchema"] == 1
        if line["status"] != 200:
            continue
        outcome = classify(
            line["feed"],
            line["status"],
            line["error"],
            line["body"],
            line["headers"],
            parse_iso(line["receivedAt"]),
            "feed_generation",
        )
        assert outcome.ok, (line["feed"], outcome.detail)
        assert line["body"]["version"] == "2.3"
        if line["feed"] == "station_status":
            pub = parse_station_status(line["body"])
            assert len(pub.stations) == pub.record_count > 200
            assert pub.extra_fields.get("num_vehicles_available") == pub.record_count
        if line["feed"] == "station_information":
            stations = line["body"]["data"]["stations"]
            assert all("station_area" not in s for s in stations)
            assert len(parse_station_information(line["body"]).stations) > 200
        if line["feed"] == "system_information":
            system = parse_system_information(line["body"])
            assert system.timezone == "Europe/Oslo"
