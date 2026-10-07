import asyncio
import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from conftest import REPO, discovery_body, info_body, status_body

from cycleops.cli import inspect_feed, record_feed
from cycleops.collection.client import DEFAULT_URLS, Attempt
from cycleops.collection.outcome import Outcome
from cycleops.collection.recording import (
    BODY_TEXT_LIMIT,
    RecordingWriter,
    read_recording,
    recording_line,
)
from cycleops.policy import ProviderConfig

REQUESTED = datetime(2026, 10, 7, 8, 0, 3, 123000, tzinfo=UTC)
LINE_KEYS = [
    "recordingSchema",
    "feed",
    "url",
    "requestedAt",
    "receivedAt",
    "durationMs",
    "status",
    "headers",
    "bodySha256",
    "body",
    "bodyText",
    "error",
]


def attempt(**fields: object) -> Attempt:
    base: dict[str, object] = {
        "feed": "station_status",
        "url": DEFAULT_URLS["station_status"],
        "requested_at": REQUESTED,
        "received_at": REQUESTED,
        "duration_ms": 0,
        "status": 200,
        "headers": {},
        "body_bytes": None,
        "body_json": None,
        "error": None,
    }
    base.update(fields)
    return Attempt(**base)  # type: ignore[arg-type]


def test_line_for_a_good_response() -> None:
    raw = json.dumps(info_body()).encode()
    body = info_body()
    del body["data"]["stations"][0]["station_area"]
    line = recording_line(
        attempt(feed="station_information", body_bytes=raw, body_json=body, duration_ms=250),
        Outcome("ok", None, 200),
    )
    assert list(line) == LINE_KEYS
    assert line["requestedAt"] == "2026-10-07T08:00:03.123Z"
    # hash of the decoded bytes as received, station_area included
    assert line["bodySha256"] == hashlib.sha256(raw).hexdigest()
    assert "station_area" not in line["body"]["data"]["stations"][0]
    assert (line["bodyText"], line["error"]) == (None, None)


def test_line_for_a_body_that_is_not_json() -> None:
    raw = b"<html>" + b"x" * (2 * BODY_TEXT_LIMIT)
    line = recording_line(
        attempt(status=502, body_bytes=raw), Outcome("http_error", "transport", 502)
    )
    assert line["body"] is None
    assert len(line["bodyText"]) == BODY_TEXT_LIMIT
    assert line["error"] == "http_error"


def test_line_for_a_timeout() -> None:
    line = recording_line(
        attempt(status=None, error="timeout"), Outcome("timeout", "transport", None)
    )
    assert (line["body"], line["bodyText"], line["bodySha256"]) == (None, None, None)
    assert line["error"] == "timeout"


def test_writer_manifest_and_reader(tmp_path: Path) -> None:
    out = tmp_path / "rec.ndjson.gz"
    ok = recording_line(
        attempt(body_bytes=b"{}", body_json=status_body()), Outcome("ok", None, 200)
    )
    bad = recording_line(attempt(error="truncated"), Outcome("truncated", "transport", 200))
    with RecordingWriter(out, "test-cycleops", 30) as writer:
        writer.write(ok)
        writer.write(bad)
    assert list(read_recording(out)) == [ok, bad]
    manifest = json.loads((tmp_path / "rec.manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert (manifest["requests"], manifest["failures"], manifest["truncations"]) == (2, 1, 1)
    assert manifest["gbfsVersion"] == ["2.3"]
    assert manifest["clientIdentifier"] == "test-cycleops"
    assert b"\r" not in gzip.decompress(out.read_bytes())


def feed_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("gbfs.json"):
        return httpx.Response(200, json=discovery_body(DEFAULT_URLS))
    if path.endswith("station_status.json"):
        return httpx.Response(200, json=status_body())
    if path.endswith("station_information.json"):
        return httpx.Response(200, json=info_body())
    body = status_body()
    body["data"] = {
        "system_id": "oslobysykkel",
        "language": "nb",
        "name": "Oslo Bysykkel",
        "timezone": "Europe/Oslo",
        "vehicle_types": [{"vehicle_type_id": "bike"}],
    }
    return httpx.Response(200, json=body)


def test_inspect_feed_makes_one_request_per_feed(provider: ProviderConfig, tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path.rsplit("/", 1)[-1])
        return feed_handler(request)

    out = tmp_path / "inspect.ndjson.gz"
    lines = asyncio.run(
        inspect_feed.collect(provider, "test-cycleops", 30, out, httpx.MockTransport(handler))
    )
    assert requests == [f"{feed}.json" for feed in DEFAULT_URLS]
    assert [line["error"] for line in lines] == [None] * 5
    assert list(read_recording(out)) == lines
    report = "\n".join(inspect_feed.report(lines, provider))
    assert "Feed report, 5 requests" in report
    assert "outside GBFS 2.3: ['num_vehicles_available']" in report


def test_record_feed_records_every_attempt(provider: ProviderConfig, tmp_path: Path) -> None:
    out = tmp_path / "sample.ndjson.gz"
    manifest = asyncio.run(
        record_feed.record(
            provider, "test-cycleops", 30, 0.01, out, httpx.MockTransport(feed_handler)
        )
    )
    lines = list(read_recording(out))
    assert sorted(line["feed"] for line in lines) == sorted(DEFAULT_URLS)
    assert manifest["requests"] == 5 and manifest["failures"] == 0
    assert (tmp_path / "sample.manifest.json").exists()


@pytest.mark.parametrize("cli", [inspect_feed.main, record_feed.main])
def test_clis_refuse_to_start_without_identifier(
    cli: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("CYCLEOPS_CLIENT_IDENTIFIER", "<seu-nome>-cycleops")
    args = ["--minutes", "1"] if cli is record_feed.main else []
    assert callable(cli)
    assert cli(args) == 2
    assert "Client-Identifier" in capsys.readouterr().err


def test_inspect_feed_reports_over_a_recording_without_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recordings = sorted((REPO / "fixtures" / "recorded").glob("inspect-*.ndjson.gz"))
    assert recordings
    monkeypatch.chdir(REPO)
    monkeypatch.delenv("CYCLEOPS_CLIENT_IDENTIFIER", raising=False)
    assert inspect_feed.main(["--recording", str(recordings[0])]) == 0
    assert "## last_reported" in capsys.readouterr().out
