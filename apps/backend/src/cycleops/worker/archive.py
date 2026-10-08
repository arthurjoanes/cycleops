"""Raw archive of the live source: every attempt as a recording line (recordingSchema 1),
in hourly NDJSON.gz files, kept 48 h and at most 500 MB.

The hour of a file is the receivedAt hour of its lines. A restart within the hour opens a
new file (raw-<hour>-1.ndjson.gz): appending to a file a crash left without its gzip
trailer would make everything after it unreadable.
"""

import gzip
import json
import logging
import os
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from cycleops.collection.recording import parse_iso, read_recording

log = logging.getLogger(__name__)

RETENTION = timedelta(hours=48)
MAX_BYTES = 500 * 1024 * 1024
STAMP = "%Y%m%dT%H%M%SZ"


def archive_dir() -> Path:
    return Path(os.environ.get("CYCLEOPS_ARCHIVE_DIR") or "/data/archive")


def _hour(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def file_hour(path: Path) -> datetime | None:
    """raw-20261008T100000Z.ndjson.gz and raw-20261008T100000Z-1.ndjson.gz -> 10:00 UTC."""
    try:
        return datetime.strptime(path.name[4:20], STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


class ArchiveWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path: Path | None = None
        self._file: IO[str] | None = None
        self._hour: datetime | None = None

    def write(self, line: dict[str, Any], received_at: datetime) -> None:
        hour = _hour(received_at)
        # Only forward: a line a few ms older than the current hour (another feed's
        # response) stays in the current file. read_archive filters by receivedAt.
        if self._file is None or self._hour is None or hour > self._hour:
            self._open(hour)
        assert self._file is not None
        self._file.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
        # Flushed per line: a crash loses at most the line being written.
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _open(self, hour: datetime) -> None:
        self.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = hour.strftime(STAMP)
        path, n = self.directory / f"raw-{stamp}.ndjson.gz", 0
        while path.exists():
            n += 1
            path = self.directory / f"raw-{stamp}-{n}.ndjson.gz"
        # Stays open across writes; close() closes it.
        self._file = gzip.open(path, "wt", encoding="utf-8", newline="\n")  # noqa: SIM115
        self.path, self._hour = path, hour
        for removed in prune(self.directory, hour, keep=path):
            log.info("raw archive file removed", extra={"file": removed.name})


def prune(
    directory: Path,
    now: datetime,
    keep: Path | None = None,
    retention: timedelta = RETENTION,
    max_bytes: int = MAX_BYTES,
) -> list[Path]:
    """Deletes files whose last write is older than `retention`, then the oldest ones
    while the total is above `max_bytes`. Never `keep` (the file being written)."""
    files = sorted(
        ((p, p.stat()) for p in directory.glob("raw-*.ndjson.gz")), key=lambda f: f[1].st_mtime
    )
    total = sum(st.st_size for _, st in files)
    removed = []
    for path, st in files:
        written = datetime.fromtimestamp(st.st_mtime, UTC)
        if path != keep and (now - written > retention or total > max_bytes):
            path.unlink()
            total -= st.st_size
            removed.append(path)
    return removed


def read_archive(directory: Path, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
    """Lines with receivedAt in [start, end), file by file in time order. A file cut short
    (the hour still being written, or a crash) gives the lines it has."""
    files = []
    for path in directory.glob("raw-*.ndjson.gz"):
        hour = file_hour(path)
        # A line late by a few ms can sit in the next hour's file, hence `<= end`.
        if hour is not None and _hour(start) <= hour <= end:
            files.append((hour, path.stat().st_mtime, path))
    for _, _, path in sorted(files):
        try:
            for line in read_recording(path):
                if start <= parse_iso(line["receivedAt"]) < end:
                    yield line
        except (EOFError, OSError, zlib.error, ValueError) as exc:
            log.warning("raw archive file ends early: %s (%s)", path.name, type(exc).__name__)
