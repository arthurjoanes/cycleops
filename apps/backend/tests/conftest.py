import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from cycleops.policy import ProviderConfig, load_provider

REPO = Path(__file__).resolve().parents[3]
POLICY_DIR = REPO / "policy"
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Real HTTPX transports refuse anything outside loopback, before any connection.
    Tests talk to the feed through httpx.MockTransport only."""
    real_async = httpx.AsyncHTTPTransport.handle_async_request
    real_sync = httpx.HTTPTransport.handle_request

    def check(request: httpx.Request) -> None:
        if request.url.host not in LOOPBACK:
            raise RuntimeError(f"external network blocked in tests: {request.url.host}")

    async def guarded_async(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        check(request)
        return await real_async(self, request)

    def guarded_sync(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        check(request)
        return real_sync(self, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_sync)
    yield


@pytest.fixture
def provider() -> ProviderConfig:
    return load_provider(directory=POLICY_DIR)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.t = start

    def now(self) -> datetime:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)
        await asyncio.sleep(0)


# Hand-built GBFS 2.3 payloads for unit tests. Real payloads are in fixtures/recorded/.
T0 = 1_791_360_000  # 2026-10-07T08:00:00Z


def station(**fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "station_id": "100",
        "is_installed": True,
        "is_renting": True,
        "is_returning": True,
        "num_bikes_available": 3,
        "num_docks_available": 5,
        "num_vehicles_available": 3,
        "vehicle_types_available": [
            {"vehicle_type_id": "bike", "count": 2},
            {"vehicle_type_id": "ebike", "count": 1},
        ],
        "last_reported": T0 + 4,
    }
    record.update(fields)
    return {k: v for k, v in record.items() if v is not None}


def status_body(
    stations: list[Any] | None = None,
    last_updated: int = T0,
    version: str = "2.3",
    ttl: int = 15,
) -> dict[str, Any]:
    return {
        "last_updated": last_updated,
        "ttl": ttl,
        "version": version,
        "data": {"stations": [station()] if stations is None else stations},
    }


def info_body(stations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    default = {
        "station_id": "100",
        "name": "Bjørvika",
        "lat": 59.9,
        "lon": 10.75,
        "capacity": 12,
        "is_virtual_station": False,
        "station_area": {"type": "MultiPolygon", "coordinates": [[[[10.7, 59.9]]]]},
        "rental_uris": {"android": "x", "ios": "y"},
    }
    return {
        "last_updated": T0,
        "ttl": 15,
        "version": "2.3",
        "data": {"stations": [default] if stations is None else stations},
    }


def discovery_body(urls: dict[str, str]) -> dict[str, Any]:
    feeds = [{"name": name, "url": url} for name, url in urls.items()]
    return {"last_updated": T0, "ttl": 15, "version": "2.3", "data": {"nb": {"feeds": feeds}}}
