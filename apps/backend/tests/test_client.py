import asyncio
import gzip
import json
from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import httpx
import pytest
from conftest import status_body

from cycleops.collection.client import (
    DEFAULT_URLS,
    MAX_BODY_BYTES,
    Attempt,
    ClientIdentifierError,
    DisallowedUrlError,
    FeedClient,
    check_url,
    client_identifier,
)
from cycleops.collection.outcome import Outcome, classify_attempt
from cycleops.policy import ProviderConfig

STATUS_URL = DEFAULT_URLS["station_status"]
# A valid publication plus an extra field nested 10 800 levels deep.
DEEP_EXTRA = json.dumps(status_body())[:-1] + ', "x": ' + "[" * 10_800 + "]" * 10_800 + "}"
Handler = Callable[[httpx.Request], httpx.Response]


def fetch(
    provider: ProviderConfig,
    handler: Handler,
    *,
    deadline: float | None = None,
    url: str = STATUS_URL,
) -> tuple[Attempt, Outcome]:
    async def go() -> Attempt:
        client = FeedClient(
            "test-cycleops",
            provider.collection,
            deadline_seconds=deadline,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.fetch("station_status", url)
        finally:
            await client.aclose()

    attempt = asyncio.run(go())
    return attempt, classify_attempt(attempt, provider.station_report_semantics)


def json_response(body: object, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


@pytest.mark.parametrize("feed", list(DEFAULT_URLS))
def test_allowlist_accepts_the_oslo_feeds(feed: str) -> None:
    check_url(feed, DEFAULT_URLS[feed])


@pytest.mark.parametrize(
    "url",
    [
        "http://gbfs.urbansharing.com/oslobysykkel.no/station_status.json",
        "https://gbfs.urbansharing.com.evil.example/oslobysykkel.no/station_status.json",
        "https://evil.example/oslobysykkel.no/station_status.json",
        "https://gbfs.urbansharing.com:8443/oslobysykkel.no/station_status.json",
        "https://user@gbfs.urbansharing.com/oslobysykkel.no/station_status.json",
        "https://gbfs.urbansharing.com/oslobysykkel.no/station_status.json?x=1",
        "https://gbfs.urbansharing.com/bergenbysykkel.no/station_status.json",
        "https://gbfs.urbansharing.com/oslobysykkel.no/station_information.json",
        "not a url",
    ],
)
def test_allowlist_refuses(url: str) -> None:
    with pytest.raises(DisallowedUrlError):
        check_url("station_status", url)


def test_url_outside_allowlist_is_never_requested(provider: ProviderConfig) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return json_response(status_body())

    with pytest.raises(DisallowedUrlError):
        fetch(provider, handler, url="https://evil.example/oslobysykkel.no/station_status.json")
    assert calls == []


def test_ok_sends_client_identifier_and_records_headers(provider: ProviderConfig) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = {"etag": '"abc"', "cache-control": "x"}
        return httpx.Response(200, json=status_body(), headers=headers)

    attempt, outcome = fetch(provider, handler)
    assert seen[0].headers["client-identifier"] == "test-cycleops"
    assert outcome.code == "ok" and outcome.error_class is None
    assert attempt.headers["etag"] == '"abc"'
    assert "cache-control" not in attempt.headers
    assert attempt.received_at == attempt.requested_at + timedelta(milliseconds=attempt.duration_ms)


def test_redirect_is_not_followed(provider: ProviderConfig) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/x.json"})

    _, outcome = fetch(provider, handler)
    assert calls == [STATUS_URL]
    assert (outcome.code, outcome.error_class, outcome.status) == ("http_error", "contract", 302)


def test_body_over_2_mib_is_cut(provider: ProviderConfig) -> None:
    sent: list[int] = []

    async def stream() -> AsyncIterator[bytes]:
        for _ in range(64):  # 16 MiB if read to the end
            sent.append(1)
            yield b" " * (256 * 1024)

    _, outcome = fetch(provider, lambda r: httpx.Response(200, content=stream()))
    assert (outcome.code, outcome.error_class) == ("too_large", "contract")
    assert len(sent) <= MAX_BODY_BYTES // (256 * 1024) + 1


def test_cap_applies_to_decoded_bytes(provider: ProviderConfig) -> None:
    bomb = gzip.compress(b" " * (3 * 1024 * 1024))
    assert len(bomb) < 100_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bomb, headers={"content-encoding": "gzip"})

    _, outcome = fetch(provider, handler)
    assert outcome.code == "too_large"


def test_gzip_body_is_decoded_and_hashed_decoded(provider: ProviderConfig) -> None:
    raw = json.dumps(status_body()).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=gzip.compress(raw), headers={"content-encoding": "gzip"})

    attempt, outcome = fetch(provider, handler)
    assert outcome.ok
    assert attempt.body_bytes == raw
    assert attempt.headers["content-encoding"] == "gzip"


def test_truncated_body_is_a_transport_error(provider: ProviderConfig) -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield b'{"last_updated": 1'
        raise httpx.RemoteProtocolError("peer closed connection without sending complete body")

    attempt, outcome = fetch(provider, lambda r: httpx.Response(200, content=stream()))
    assert (outcome.code, outcome.error_class) == ("truncated", "transport")
    assert attempt.body_bytes is None


def test_read_timeout(provider: ProviderConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _, outcome = fetch(provider, handler)
    assert (outcome.code, outcome.error_class) == ("timeout", "transport")


def test_network_error(provider: ProviderConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _, outcome = fetch(provider, handler)
    assert (outcome.code, outcome.error_class, outcome.status) == (
        "network_error",
        "transport",
        None,
    )


def test_total_deadline_stops_a_slow_trickle(provider: ProviderConfig) -> None:
    # Each chunk arrives well within the read timeout; only the total deadline stops it.
    async def stream() -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(0.02)
            yield b" "

    attempt, outcome = fetch(
        provider, lambda r: httpx.Response(200, content=stream()), deadline=0.3
    )
    assert (outcome.code, outcome.error_class) == ("timeout", "transport")
    assert 250 <= attempt.duration_ms < 2000
    assert attempt.body_bytes is None


@pytest.mark.parametrize(
    ("status", "code", "error_class"),
    [
        (404, "http_error", "contract"),
        (403, "http_error", "contract"),
        (410, "http_error", "contract"),
        (429, "http_error", "transport"),
        (500, "http_error", "transport"),
        (503, "http_error", "transport"),
        (304, "not_modified", None),
    ],
)
def test_status_classification(
    provider: ProviderConfig, status: int, code: str, error_class: str | None
) -> None:
    _, outcome = fetch(provider, lambda r: httpx.Response(status, text="no"))
    assert (outcome.code, outcome.error_class, outcome.status) == (code, error_class, status)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (503, {"data": None, "error": "maintenance"}, ("http_error", "transport")),
        (404, {"data": "not found"}, ("http_error", "contract")),
        (200, status_body() | {"data": None}, ("invalid", "contract")),
        (200, status_body() | {"data": []}, ("invalid", "contract")),
        (200, "[" * 200_000, ("invalid", "contract")),  # RecursionError in json.loads
        # loads copes with this depth but json.dumps in the recording would not
        (200, DEEP_EXTRA, ("invalid", "contract")),
    ],
    ids=[
        "503-data-null",
        "404-data-string",
        "200-data-null",
        "200-data-list",
        "200-deep",
        "200-deep-extra",
    ],
)
def test_malformed_bodies_are_classified_not_raised(
    provider: ProviderConfig, status: int, body: object, expected: tuple[str, str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    _, outcome = fetch(provider, handler)
    assert (outcome.code, outcome.error_class) == expected


@pytest.mark.parametrize("value", [b"\xb2", b"soon", b"-5"])
def test_odd_retry_after_is_ignored(provider: ProviderConfig, value: bytes) -> None:
    headers = [(b"retry-after", value)]
    _, outcome = fetch(provider, lambda r: httpx.Response(429, headers=headers))
    assert (outcome.code, outcome.error_class) == ("http_error", "transport")
    assert outcome.retry_after_until is None


@pytest.mark.parametrize(
    "value",
    [b"999999999", b"99999999999999999999", b"Fri, 31 Dec 9999 23:59:59 GMT"],
)
def test_huge_retry_after_is_capped(provider: ProviderConfig, value: bytes) -> None:
    headers = [(b"retry-after", value)]
    attempt, outcome = fetch(provider, lambda r: httpx.Response(429, headers=headers))
    assert outcome.retry_after_until == attempt.received_at + timedelta(hours=1)


def test_429_respects_retry_after(provider: ProviderConfig) -> None:
    attempt, outcome = fetch(
        provider, lambda r: httpx.Response(429, headers={"retry-after": "120"})
    )
    assert outcome.retry_after_until == attempt.received_at + timedelta(seconds=120)


def test_invalid_json_is_a_contract_error(provider: ProviderConfig) -> None:
    attempt, outcome = fetch(provider, lambda r: httpx.Response(200, text="<html>oops</html>"))
    assert (outcome.code, outcome.error_class) == ("invalid", "contract")
    assert attempt.body_json is None and attempt.body_bytes is not None


def test_unknown_version_is_a_contract_error(provider: ProviderConfig) -> None:
    _, outcome = fetch(provider, lambda r: json_response(status_body(version="3.0")))
    assert (outcome.code, outcome.error_class) == ("invalid", "contract")
    assert outcome.detail is not None and "3.0" in outcome.detail


def test_extra_field_is_accepted(provider: ProviderConfig) -> None:
    _, outcome = fetch(provider, lambda r: json_response(status_body()))
    assert outcome.ok


@pytest.mark.parametrize("value", ["", "   ", "<seu-nome>-cycleops", "<org>-<app>"])
def test_client_identifier_refuses_empty_or_placeholder(
    provider: ProviderConfig, value: str
) -> None:
    with pytest.raises(ClientIdentifierError, match="Client-Identifier"):
        client_identifier({"CYCLEOPS_CLIENT_IDENTIFIER": value})
    with pytest.raises(ClientIdentifierError):
        FeedClient(value, provider.collection, transport=httpx.MockTransport(json_response))


def test_client_identifier_missing() -> None:
    with pytest.raises(ClientIdentifierError):
        client_identifier({})
    assert client_identifier({"CYCLEOPS_CLIENT_IDENTIFIER": " me-cycleops "}) == "me-cycleops"


def test_client_without_injected_transport_fails_before_connecting(
    provider: ProviderConfig,
) -> None:
    async def go() -> None:
        client = FeedClient("test-cycleops", provider.collection)
        try:
            await client.fetch("station_status", STATUS_URL)
        finally:
            await client.aclose()

    with pytest.raises(RuntimeError, match="external network blocked"):
        asyncio.run(go())
