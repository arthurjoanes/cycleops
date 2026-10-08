import gzip
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO, status_body

from cycleops.cli import record_feed
from cycleops.collection.client import Attempt
from cycleops.collection.outcome import classify_attempt
from cycleops.collection.recording import iso, read_recording, recording_line
from cycleops.worker.archive import ArchiveWriter, prune, read_archive

H10 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)


def line(at: datetime, status: int = 200) -> dict[str, Any]:
    body = status_body() if status == 200 else None
    attempt = Attempt(
        feed="station_status",
        url="https://gbfs.urbansharing.com/oslobysykkel.no/station_status.json",
        requested_at=at - timedelta(milliseconds=200),
        received_at=at,
        duration_ms=200,
        status=status,
        headers={},
        body_bytes=json.dumps(body).encode() if body else b"",
        body_json=body,
        error=None,
    )
    return recording_line(attempt, classify_attempt(attempt, "feed_generation"))


def write(directory: Path, instants: list[datetime]) -> None:
    writer = ArchiveWriter(directory)
    for at in instants:
        writer.write(line(at), at)
    writer.close()


def test_one_file_per_received_hour_and_a_new_file_after_a_restart(tmp_path: Path) -> None:
    write(tmp_path, [H10 + timedelta(minutes=m) for m in (0, 30, 59, 61)])
    # Restart within the 11:00 hour: a new file, the first one is never appended to.
    write(tmp_path, [H10 + timedelta(minutes=70)])
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "raw-20261008T100000Z.ndjson.gz",
        "raw-20261008T110000Z-1.ndjson.gz",
        "raw-20261008T110000Z.ndjson.gz",
    ]
    first = list(read_recording(tmp_path / names[0]))
    assert [x["receivedAt"] for x in first] == [
        iso(H10 + timedelta(minutes=m)) for m in (0, 30, 59)
    ]
    assert first[0]["recordingSchema"] == 1


def test_read_archive_filters_by_received_at_in_time_order(tmp_path: Path) -> None:
    write(tmp_path, [H10 + timedelta(minutes=m) for m in (0, 30, 59, 61, 125)])
    write(tmp_path, [H10 + timedelta(minutes=70)])
    got = read_archive(tmp_path, H10 + timedelta(minutes=30), H10 + timedelta(minutes=71))
    assert [x["receivedAt"] for x in got] == [
        iso(H10 + timedelta(minutes=m)) for m in (30, 59, 61, 70)
    ]


def test_read_archive_keeps_the_lines_of_a_file_cut_short(tmp_path: Path) -> None:
    writer = ArchiveWriter(tmp_path)
    for m in (0, 1, 2):
        writer.write(line(H10 + timedelta(minutes=m)), H10 + timedelta(minutes=m))
    # Not closed, as after a crash: no gzip trailer. Copy the bytes as they are on disk.
    assert writer.path is not None
    cut = tmp_path / "copy"
    cut.mkdir()
    (cut / writer.path.name).write_bytes(writer.path.read_bytes())
    writer.close()
    got = list(read_archive(cut, H10, H10 + timedelta(hours=1)))
    assert len(got) == 3


def test_prune_by_age_then_by_size_oldest_first(tmp_path: Path) -> None:
    now = H10 + timedelta(days=3)
    files = {}
    for hours_ago in (50, 47, 30, 1):
        path = tmp_path / f"raw-{(now - timedelta(hours=hours_ago)):%Y%m%dT%H0000Z}.ndjson.gz"
        path.write_bytes(b"x" * 100)
        mtime = (now - timedelta(hours=hours_ago)).timestamp()
        os.utime(path, (mtime, mtime))
        files[hours_ago] = path
    removed = prune(tmp_path, now, keep=files[1], max_bytes=250)
    # 50 h is past 48 h; then 300 bytes > 250 removes the oldest of the rest (47 h).
    assert removed == [files[50], files[47]]
    assert sorted(tmp_path.iterdir()) == sorted([files[30], files[1]])
    # The file being written is never removed, even alone above the cap.
    assert prune(tmp_path, now, keep=files[1], max_bytes=10) == [files[30]]


def test_writer_prunes_when_it_opens_a_file(tmp_path: Path) -> None:
    old = tmp_path / "raw-20261001T000000Z.ndjson.gz"
    old.write_bytes(gzip.compress(b""))
    mtime = (H10 - timedelta(days=7)).timestamp()
    os.utime(old, (mtime, mtime))
    write(tmp_path, [H10])
    assert not old.exists()


def test_record_feed_from_archive_writes_a_recording_with_its_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "archive"
    write(archive, [H10 + timedelta(minutes=m) for m in (0, 15, 45, 75)])
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("CYCLEOPS_ARCHIVE_DIR", str(archive))
    monkeypatch.setenv("CYCLEOPS_CLIENT_IDENTIFIER", "test-cycleops")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    out = tmp_path / "export" / "cut.ndjson.gz"
    args = ["--from-archive", "--from", "2026-10-08T10:10:00Z", "--to", "2026-10-08T11:00:00"]
    assert record_feed.main([*args, "--out", str(out)]) == 0
    lines = list(read_recording(out))
    assert [x["receivedAt"] for x in lines] == [iso(H10 + timedelta(minutes=m)) for m in (15, 45)]
    manifest = json.loads((tmp_path / "export" / "cut.manifest.json").read_text("utf-8"))
    assert manifest["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert manifest["requests"] == 2 and manifest["failures"] == 0
    assert manifest["clientIdentifier"] == "test-cycleops"
    assert "2 requests" in capsys.readouterr().out

    # Nothing in the range: exit 1 and no file.
    empty = tmp_path / "empty.ndjson.gz"
    late = ["--from", "2026-10-09T00:00:00Z", "--to", "2026-10-09T01:00:00Z"]
    assert record_feed.main(["--from-archive", *late, "--out", str(empty)]) == 1
    assert not empty.exists()


@pytest.mark.parametrize(
    "args",
    [
        ["--from-archive", "--from", "2026-10-08T10:00:00Z", "--out", "x.ndjson.gz"],
        [
            "--from-archive",
            "--from",
            "2026-10-08T11:00Z",
            "--to",
            "2026-10-08T10:00Z",
            "--out",
            "x",
        ],
        [],
    ],
)
def test_record_feed_argument_errors(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)
    with pytest.raises(SystemExit) as exited:
        record_feed.main(args)
    assert exited.value.code == 2
