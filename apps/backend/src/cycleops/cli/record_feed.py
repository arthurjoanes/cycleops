"""record-feed: records the live feed for a while, one NDJSON line per attempt."""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from cycleops.clock import SystemClock
from cycleops.collection.client import ClientIdentifierError, FeedClient, client_identifier
from cycleops.collection.collector import Collector
from cycleops.collection.recording import RecordingWriter, recording_line
from cycleops.policy import ProviderConfig, load_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="record-feed", description=__doc__)
    parser.add_argument("--minutes", type=float, required=True, help="how long to record")
    parser.add_argument("--out", type=Path, help="default: data/recordings/record-<UTC>.ndjson.gz")
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        help="total deadline per request (default: requestDeadlineSeconds of the provider)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # One INFO line per request from httpx is noise over a long recording.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    provider = load_provider()
    try:
        identifier = client_identifier()
    except ClientIdentifierError as exc:
        print(exc, file=sys.stderr)
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out: Path = args.out or Path("data/recordings") / f"record-{stamp}.ndjson.gz"
    deadline: float = args.deadline_seconds or provider.collection.request_deadline_seconds
    try:
        manifest = asyncio.run(record(provider, identifier, deadline, args.minutes, out))
    except KeyboardInterrupt:
        print(f"interrupted, kept what was recorded in {out}", file=sys.stderr)
        return 130
    print(f"{out}: {manifest['requests']} requests, {manifest['failures']} failures")
    return 0


async def record(
    provider: ProviderConfig,
    identifier: str,
    deadline: float,
    minutes: float,
    out: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    client = FeedClient(
        identifier, provider.collection, deadline_seconds=deadline, transport=transport
    )
    with RecordingWriter(out, identifier, deadline) as writer:
        collector = Collector(
            client,
            provider,
            lambda attempt, outcome: writer.write(recording_line(attempt, outcome)),
        )
        until = SystemClock().now() + timedelta(minutes=minutes)
        try:
            await collector.run(until=until)
        finally:
            await client.aclose()
        return writer.close()
