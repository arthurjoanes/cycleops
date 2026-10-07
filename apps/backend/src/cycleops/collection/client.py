"""HTTP client for the Oslo Bysykkel GBFS feeds.

Only allowlisted HTTPS URLs, no redirects, no proxy from the environment, a total
deadline per request and a 2 MiB cap on the decoded body. Every call returns an
Attempt, including failures; it never raises on network or HTTP errors.
"""

import asyncio
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from cycleops.clock import Clock, SystemClock
from cycleops.policy import CollectionConfig

HOST = "gbfs.urbansharing.com"
PATH_PREFIX = "/oslobysykkel.no/"
FEEDS = ("gbfs", "system_information", "vehicle_types", "station_information", "station_status")
DEFAULT_URLS = {feed: f"https://{HOST}{PATH_PREFIX}{feed}.json" for feed in FEEDS}
MAX_BODY_BYTES = 2 * 1024 * 1024
RECORDED_HEADERS = (
    "date",
    "etag",
    "last-modified",
    "content-type",
    "content-encoding",
    "content-length",
    "retry-after",
)
IDENTIFIER_ENV = "CYCLEOPS_CLIENT_IDENTIFIER"
IDENTIFIER_RULE = (
    "Oslo Bysykkel requires a Client-Identifier header on every request, in the form "
    "<organization>-<application> (https://oslobysykkel.no/en/open-data/realtime). "
    f"Set {IDENTIFIER_ENV}, for example yourname-cycleops."
)


class ClientIdentifierError(ValueError):
    pass


class DisallowedUrlError(ValueError):
    pass


def validate_identifier(value: str) -> str:
    value = value.strip()
    # "<seu-nome>-cycleops" is the .env.example placeholder.
    if not value or "<" in value or ">" in value:
        raise ClientIdentifierError(IDENTIFIER_RULE)
    return value


def client_identifier(environ: Mapping[str, str] = os.environ) -> str:
    return validate_identifier(environ.get(IDENTIFIER_ENV, ""))


def check_url(feed: str, url: str) -> None:
    """Raises DisallowedUrlError unless url is https://HOST/oslobysykkel.no/<feed>.json."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise DisallowedUrlError(f"{feed}: invalid URL") from exc
    if (
        feed not in FEEDS
        or parsed.scheme != "https"
        or parsed.host != HOST
        or parsed.port not in (None, 443)
        or parsed.userinfo
        or parsed.query
        or parsed.fragment
        or parsed.path != f"{PATH_PREFIX}{feed}.json"
    ):
        raise DisallowedUrlError(f"{feed}: URL outside the allowlist: {url}")


@dataclass(frozen=True)
class Attempt:
    feed: str
    url: str
    requested_at: datetime
    received_at: datetime
    duration_ms: int
    status: int | None
    headers: dict[str, str]
    # Decoded body, only when it was read in full.
    body_bytes: bytes | None
    # Parsed JSON without station_area; None when there is no body or it is not JSON.
    body_json: Any
    # Set when the body could not be read: timeout, network_error, truncated, too_large.
    error: str | None


def strip_station_area(body: Any) -> Any:
    """station_area is most of station_information and is never used."""
    stations = body.get("data", {}).get("stations") if isinstance(body, dict) else None
    if isinstance(stations, list):
        for station in stations:
            if isinstance(station, dict):
                station.pop("station_area", None)
    return body


class FeedClient:
    def __init__(
        self,
        identifier: str,
        config: CollectionConfig,
        *,
        deadline_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        t = config.http_timeouts
        self._deadline = deadline_seconds or config.request_deadline_seconds
        self._clock = clock or SystemClock()
        self._http = httpx.AsyncClient(
            headers={"Client-Identifier": validate_identifier(identifier)},
            timeout=httpx.Timeout(connect=t.connect, read=t.read, write=t.write, pool=t.pool),
            follow_redirects=False,
            trust_env=False,
            verify=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def fetch(self, feed: str, url: str) -> Attempt:
        check_url(feed, url)
        requested_at = self._clock.now()
        started = time.monotonic()
        status: int | None = None
        headers: dict[str, str] = {}
        body: bytes | None = None
        error: str | None = None
        try:
            # httpx's read timeout applies per chunk, so a slow trickle never trips it.
            # The deadline bounds the whole request.
            async with asyncio.timeout(self._deadline), self._http.stream("GET", url) as resp:
                status = resp.status_code
                headers = {k: v for k in RECORDED_HEADERS if (v := resp.headers.get(k))}
                body, error = await _read_capped(resp)
        except TimeoutError, httpx.TimeoutException:
            error = "timeout"
        except httpx.TransportError:
            error = "network_error"
        duration_ms = round((time.monotonic() - started) * 1000)
        body_json: Any = None
        if body is not None:
            try:
                body_json = strip_station_area(json.loads(body))
            except ValueError:  # JSONDecodeError and UnicodeDecodeError
                body_json = None
        return Attempt(
            feed=feed,
            url=url,
            requested_at=requested_at,
            received_at=requested_at + timedelta(milliseconds=duration_ms),
            duration_ms=duration_ms,
            status=status,
            headers=headers,
            body_bytes=body,
            body_json=body_json,
            error=error,
        )


async def _read_capped(resp: httpx.Response) -> tuple[bytes | None, str | None]:
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                return None, "too_large"
            chunks.append(chunk)
    except httpx.TimeoutException:
        raise
    except httpx.TransportError, httpx.DecodingError:
        # Headers arrived but the body broke off (peer closed early, bad gzip stream).
        return None, "truncated"
    return b"".join(chunks), None
