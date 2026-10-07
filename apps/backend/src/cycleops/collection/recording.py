"""Recording format (recordingSchema 1): one NDJSON line per attempt, gzip for the file,
with <name>.manifest.json next to it. The worker's raw archive uses the same line."""

import gzip
import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from cycleops.collection.client import Attempt
from cycleops.collection.outcome import Outcome

RECORDING_SCHEMA = 1
BODY_TEXT_LIMIT = 64 * 1024
LICENSE = (
    "Norwegian Licence for Open Government Data (NLOD) 2.0, "
    "https://data.norge.no/nlod/en/2.0, made available by Oslo Bysykkel "
    "(UIP Bauer Media Outdoor Norge AS) at https://oslobysykkel.no/en/open-data"
)


def iso(instant: datetime) -> str:
    return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def recording_line(attempt: Attempt, outcome: Outcome) -> dict[str, Any]:
    body = attempt.body_json
    body_text = None
    if body is None and attempt.body_bytes is not None:
        body_text = attempt.body_bytes[:BODY_TEXT_LIMIT].decode("utf-8", errors="replace")
    sha = hashlib.sha256(attempt.body_bytes).hexdigest() if attempt.body_bytes is not None else None
    return {
        "recordingSchema": RECORDING_SCHEMA,
        "feed": attempt.feed,
        "url": attempt.url,
        "requestedAt": iso(attempt.requested_at),
        "receivedAt": iso(attempt.received_at),
        "durationMs": attempt.duration_ms,
        "status": attempt.status,
        "headers": attempt.headers,
        "bodySha256": sha,
        "body": body,
        "bodyText": body_text,
        "error": None if outcome.code in ("ok", "not_modified") else outcome.code,
    }


class RecordingWriter:
    """Writes lines as they come, flushed so an interrupted run keeps what it got."""

    def __init__(self, path: Path, client_identifier: str, deadline_seconds: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._identifier = client_identifier
        self._deadline = deadline_seconds
        # Stays open across writes; close() or __exit__ closes it.
        self._file = gzip.open(path, "wt", encoding="utf-8", newline="\n")  # noqa: SIM115
        self._urls: set[str] = set()
        self._versions: set[str] = set()
        self._outcomes: Counter[str] = Counter()
        self._started: str | None = None
        self._ended: str | None = None

    def write(self, line: dict[str, Any]) -> None:
        self._file.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._file.flush()
        self._urls.add(line["url"])
        body = line["body"]
        if isinstance(body, dict) and isinstance(body.get("version"), str):
            self._versions.add(body["version"])
        self._outcomes[line["error"] or "ok"] += 1
        self._started = self._started or line["requestedAt"]
        self._ended = line["receivedAt"]

    def close(self) -> dict[str, Any]:
        """Closes the file and writes the manifest. Returns the manifest."""
        self._file.close()
        manifest = {
            "recordingSchema": RECORDING_SCHEMA,
            "file": self.path.name,
            "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "startedAt": self._started,
            "endedAt": self._ended,
            "urls": sorted(self._urls),
            "gbfsVersion": sorted(self._versions),
            "license": LICENSE,
            "requests": sum(self._outcomes.values()),
            "failures": sum(n for code, n in self._outcomes.items() if code != "ok"),
            "truncations": self._outcomes["truncated"] + self._outcomes["too_large"],
            "outcomes": dict(sorted(self._outcomes.items())),
            "clientIdentifier": self._identifier,
            "deadlineSeconds": self._deadline,
        }
        manifest_path = self.path.with_name(self.path.name.removesuffix(".ndjson.gz"))
        manifest_path = manifest_path.with_name(manifest_path.name + ".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return manifest

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._file.closed:
            self.close()


def read_recording(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)
