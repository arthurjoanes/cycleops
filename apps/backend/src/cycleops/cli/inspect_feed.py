"""inspect-feed: one GET per feed (5 requests), saved as a recording, plus a report.

With --recording, prints the same report over saved recordings, without network.
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from itertools import pairwise
from math import floor
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from cycleops.adapters.gbfs import (
    INFO_FIELDS,
    STATUS_FIELDS,
    ContractError,
    InfoPublication,
    StatusPublication,
    parse_station_information,
    parse_station_status,
    parse_system_information,
)
from cycleops.collection.client import (
    DEFAULT_URLS,
    FEEDS,
    ClientIdentifierError,
    FeedClient,
    client_identifier,
)
from cycleops.collection.outcome import classify_attempt
from cycleops.collection.recording import RecordingWriter, parse_iso, read_recording, recording_line
from cycleops.policy import ProviderConfig, load_provider

Line = dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inspect-feed", description=__doc__)
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        help="total deadline per request (default: requestDeadlineSeconds of the provider)",
    )
    parser.add_argument(
        "--out", type=Path, help="default: fixtures/recorded/inspect-<UTC>.ndjson.gz"
    )
    parser.add_argument(
        "--recording", type=Path, nargs="+", help="report over these recordings, no network"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    provider = load_provider()
    if args.recording:
        lines = [line for path in args.recording for line in read_recording(path)]
    else:
        try:
            identifier = client_identifier()
        except ClientIdentifierError as exc:
            print(exc, file=sys.stderr)
            return 2
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = args.out or Path("fixtures/recorded") / f"inspect-{stamp}.ndjson.gz"
        deadline = args.deadline_seconds or provider.collection.request_deadline_seconds
        lines = asyncio.run(collect(provider, identifier, deadline, out))
        print(f"saved {out}\n")
    print("\n".join(report(lines, provider)))
    return 0


async def collect(
    provider: ProviderConfig,
    identifier: str,
    deadline: float,
    out: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Line]:
    # No rediscovery or retry here: exactly one request per feed.
    urls = dict(DEFAULT_URLS)
    lines: list[Line] = []
    client = FeedClient(
        identifier, provider.collection, deadline_seconds=deadline, transport=transport
    )
    try:
        with RecordingWriter(out, identifier, deadline) as writer:
            for feed in FEEDS:
                attempt = await client.fetch(feed, urls[feed])
                outcome = classify_attempt(attempt, provider.station_report_semantics)
                if feed == "gbfs" and outcome.ok and isinstance(outcome.publication, dict):
                    urls.update(outcome.publication)
                line = recording_line(attempt, outcome)
                writer.write(line)
                lines.append(line)
    finally:
        await client.aclose()
    return lines


def percentile(values: Iterable[float], p: float) -> float | None:
    """Linear interpolation between closest ranks."""
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * p / 100
    f = floor(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _dist(values: list[float], unit: str = "") -> str:
    if not values:
        return "no data"
    p = [percentile(values, q) for q in (50, 95, 99)]
    return (
        f"n={len(values)} min={min(values):.1f}{unit} p50={p[0]:.1f}{unit} "
        f"p95={p[1]:.1f}{unit} p99={p[2]:.1f}{unit} max={max(values):.1f}{unit}"
    )


def _seconds(a: str, b: str) -> float:
    return (parse_iso(a) - parse_iso(b)).total_seconds()


def report(lines: list[Line], provider: ProviderConfig) -> list[str]:
    if not lines:
        return ["no lines"]
    oslo = ZoneInfo(provider.timezone)
    start, end = parse_iso(lines[0]["requestedAt"]), parse_iso(lines[-1]["receivedAt"])
    by_feed: dict[str, list[Line]] = defaultdict(list)
    for line in lines:
        by_feed[line["feed"]].append(line)
    out = [
        f"# Feed report, {len(lines)} requests",
        "",
        f"Period: {start:%Y-%m-%d %H:%M:%S} to {end:%H:%M:%S} UTC "
        f"({start.astimezone(oslo):%Y-%m-%d %H:%M} to {end.astimezone(oslo):%H:%M} Oslo)",
        "",
        "## Requests per feed",
        "",
        "| feed | n | status | errors | version | ttl | content-encoding | duration ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for feed in FEEDS:
        fl = by_feed.get(feed, [])
        if not fl:
            continue
        bodies = [x["body"] for x in fl if isinstance(x["body"], dict)]
        out.append(
            f"| {feed} | {len(fl)} | {_counts(x['status'] for x in fl)} "
            f"| {_counts(x['error'] for x in fl if x['error'])} "
            f"| {_counts(b.get('version') for b in bodies)} "
            f"| {_counts(b.get('ttl') for b in bodies)} "
            f"| {_counts(x['headers'].get('content-encoding', 'none') for x in fl)} "
            f"| {_dist([x['durationMs'] for x in fl])} |"
        )
    status_lines, pubs = _status_publications(by_feed.get("station_status", []))
    info = _latest_info(by_feed.get("station_information", []))
    out += ["", "## Timing (station_status)", ""]
    durations = [x["durationMs"] / 1000 for x in by_feed.get("station_status", [])]
    timeouts = sum(1 for x in by_feed.get("station_status", []) if x["error"] == "timeout")
    out.append(f"- download: {_dist(durations, 's')}; timeouts {timeouts}/{len(durations)}")
    if pubs:
        updated = sorted({p.envelope.last_updated for p in pubs})
        steps = [(b - a).total_seconds() for a, b in pairwise(updated)]
        out.append(f"- last_updated steps: {_counts(int(s) for s in steps) or 'one publication'}")
        ages = [
            _seconds(x["receivedAt"], _iso_of(p)) for x, p in zip(status_lines, pubs, strict=True)
        ]
        out.append(f"- age at receipt (receivedAt - last_updated): {_dist(ages, 's')}")
        upper = min(
            _seconds(x["requestedAt"], _iso_of(p)) for x, p in zip(status_lines, pubs, strict=True)
        )
        late = [
            _seconds(x["requestedAt"], _iso_of(p)) - p.envelope.ttl
            for x, p in zip(status_lines, pubs, strict=True)
        ]
        late_pos = [v for v in late if v > 0]
        out.append(
            f"- availability delay, upper bound min(requestedAt - last_updated): {upper:.1f}s"
        )
        out.append(
            "- availability delay, lower bound max(requestedAt - (last_updated + ttl)) over "
            f"responses still carrying the previous publication: "
            f"{f'{max(late_pos):.1f}s ({len(late_pos)} responses)' if late_pos else 'none seen'}"
        )
    skews = []
    for x in lines:
        if date := x["headers"].get("date"):
            skews.append((parsedate_to_datetime(date) - parse_iso(x["receivedAt"])).total_seconds())
    out.append(f"- Date header - receivedAt (1 s resolution): {_dist(skews, 's')}")
    out += _last_reported_section(pubs)
    out += _fields_section(by_feed, pubs, info)
    out += _etag_section(by_feed)
    out += _system_section(by_feed.get("system_information", []), provider)
    return out


def _counts(values: Iterable[Any]) -> str:
    return ", ".join(f"{k}: {n}" for k, n in sorted(Counter(values).items(), key=str))


def _iso_of(pub: StatusPublication) -> str:
    return pub.envelope.last_updated.isoformat()


def _status_publications(lines: list[Line]) -> tuple[list[Line], list[StatusPublication]]:
    kept: list[Line] = []
    pubs: list[StatusPublication] = []
    for line in lines:
        if line["status"] != 200 or not isinstance(line["body"], dict):
            continue
        try:
            pubs.append(parse_station_status(line["body"]))
        except ContractError:
            continue
        kept.append(line)
    return kept, pubs


def _latest_info(lines: list[Line]) -> InfoPublication | None:
    for line in reversed(lines):
        if line["status"] == 200 and isinstance(line["body"], dict):
            try:
                return parse_station_information(line["body"])
            except ContractError:
                continue
    return None


def _last_reported_section(pubs: list[StatusPublication]) -> list[str]:
    out = ["", "## last_reported", ""]
    if not pubs:
        return [*out, "no valid station_status"]
    out.append(
        f"- distinct values per publication: {_counts(p.distinct_last_reported for p in pubs)}"
    )
    offsets = [
        (s.reported_at - p.envelope.last_updated).total_seconds()
        for p in pubs
        for s in p.stations
        if s.reported_at is not None
    ]
    out.append(f"- last_reported - last_updated: {_dist(offsets, 's')}")
    distinct: dict[datetime, StatusPublication] = {}
    for p in pubs:
        distinct.setdefault(p.envelope.last_updated, p)
    ordered = [distinct[k] for k in sorted(distinct)]
    pairs: Counter[str] = Counter()
    changes = 0
    for prev, cur in pairwise(ordered):
        before = {s.station_id: s for s in prev.stations}
        for s in cur.stations:
            b = before.get(s.station_id)
            if b is None:
                continue
            counts = (b.bikes_available, b.docks_available, b.vehicle_types) != (
                s.bikes_available,
                s.docks_available,
                s.vehicle_types,
            )
            changes += counts
            reported = b.reported_at != s.reported_at
            label = "changed" if reported else "same"
            pairs[f"counts {'changed' if counts else 'same'}, last_reported {label}"] += 1
    out.append(
        f"- per station between consecutive publications: {_counts(pairs.elements()) or 'n/a'}"
    )
    if len(ordered) > 1:
        span = (
            ordered[-1].envelope.last_updated - ordered[0].envelope.last_updated
        ).total_seconds()
        rate = changes / (span / 3600) if span else 0
        out.append(
            f"- count changes: {changes} station changes over {len(ordered)} publications "
            f"in {span:.0f}s ({rate:.0f}/h)"
        )
    last = pubs[-1]
    st = last.stations
    out.append(
        f"- last publication: {len(st)} valid of {last.record_count}, "
        f"invalid {len(last.invalid_station_ids)}, "
        f"0 bikes {sum(s.bikes_available == 0 for s in st)}, "
        f"0 docks {sum(s.docks_available == 0 for s in st)}, "
        f"not installed {sum(not s.is_installed for s in st)}, "
        f"not renting {sum(not s.is_renting for s in st)}, "
        f"not returning {sum(not s.is_returning for s in st)}, "
        f"quality {_counts(s.quality for s in st)}"
    )
    return out


def _fields_section(
    by_feed: dict[str, list[Line]], pubs: list[StatusPublication], info: InfoPublication | None
) -> list[str]:
    out = ["", "## Fields", ""]
    for feed, known in (("station_status", STATUS_FIELDS), ("station_information", INFO_FIELDS)):
        bodies = [x["body"] for x in by_feed.get(feed, []) if isinstance(x["body"], dict)]
        if not bodies:
            continue
        records = bodies[-1].get("data", {}).get("stations", [])
        present: Counter[str] = Counter(k for r in records if isinstance(r, dict) for k in r)
        if feed == "station_information":
            present.pop("station_area", None)  # removed before recording
        out.append(
            f"- {feed} fields (records with it, of {len(records)}): {_counts(present.elements())}"
        )
        out.append(f"  - outside GBFS 2.3: {sorted(set(present) - known) or 'none'}")
        out.append(f"  - GBFS 2.3 fields absent: {sorted(known - set(present) - {'station_area'})}")
    if pubs and info:
        status = {s.station_id: s for s in pubs[-1].stations}
        catalog = {s.station_id: s for s in info.stations}
        out.append(
            f"- ids: status {len(status)}, catalog {len(catalog)}, "
            f"only in status {len(status.keys() - catalog.keys())}, "
            f"only in catalog {len(catalog.keys() - status.keys())}"
        )
        gaps = [
            c.capacity - (s.bikes_available + s.docks_available)
            for sid, s in status.items()
            if (c := catalog.get(sid)) and c.capacity is not None and s.docks_available is not None
        ]
        out.append(
            f"- capacity - (bikes + docks): zero {sum(g == 0 for g in gaps)}, positive "
            f"{sum(g > 0 for g in gaps)}, negative {sum(g < 0 for g in gaps)}; "
            f"{_dist([float(g) for g in gaps])}"
        )
        out.append(
            "- valet signals: docks missing "
            f"{sum(s.docks_available is None for s in status.values())}, "
            f"bikes + docks > capacity {sum(g < 0 for g in gaps)}, "
            f"is_valet_station present {sum(c.is_valet is not None for c in catalog.values())}, "
            f"true {sum(bool(c.is_valet) for c in catalog.values())}, "
            f"is_virtual true {sum(c.is_virtual for c in catalog.values())}, "
            f"capacity flags {_counts(f for c in catalog.values() for f in c.flags) or 'none'}"
        )
    return out


def _etag_section(by_feed: dict[str, list[Line]]) -> list[str]:
    out = ["", "## ETag", ""]
    for feed in ("station_information", "station_status"):
        fl = [x for x in by_feed.get(feed, []) if x["status"] == 200]
        if not fl:
            continue
        etags = {x["headers"].get("etag") for x in fl}
        bodies = {x["bodySha256"] for x in fl}
        updated = {x["body"].get("last_updated") for x in fl if isinstance(x["body"], dict)}
        out.append(
            f"- {feed}: {len(fl)} responses, {len(etags)} distinct ETags, "
            f"{len(bodies)} distinct bodies, {len(updated)} distinct last_updated"
        )
    return out


def _system_section(lines: list[Line], provider: ProviderConfig) -> list[str]:
    out = ["", "## system_information", ""]
    for line in reversed(lines):
        if isinstance(line["body"], dict):
            try:
                system = parse_system_information(line["body"])
            except ContractError as exc:
                return [*out, f"invalid: {exc}"]
            data = line["body"].get("data", {})
            out.append(f"- system_id {system.system_id}, name {system.name!r}")
            out.append(f"- operator {system.operator!r}")
            out.append(
                f"- timezone {system.timezone} (provider file: {provider.timezone}, "
                f"{'match' if system.timezone == provider.timezone else 'MISMATCH'})"
            )
            out.append(f"- fields: {', '.join(sorted(data))}")
            return out
    return [*out, "not fetched"]
