#!/usr/bin/env python3
"""Check the 5 MiB budget for each recording and its required manifest together."""

import argparse
from pathlib import Path

LIMIT_BYTES = 5 * 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("fixtures/recorded"))
    directory = parser.parse_args().directory
    if not directory.is_dir():
        print(f"recording directory not found: {directory}")
        return 1

    errors = []
    recordings = sorted(directory.rglob("*.ndjson.gz"))
    for recording in recordings:
        manifest = recording.with_name(recording.name.removesuffix(".ndjson.gz") + ".manifest.json")
        if not manifest.is_file():
            errors.append(f"missing manifest: {manifest}")
            continue
        total = recording.stat().st_size + manifest.stat().st_size
        if total > LIMIT_BYTES:
            errors.append(
                f"recording and manifest above 5 MiB: {recording} + {manifest} "
                f"({total} bytes > {LIMIT_BYTES})"
            )

    for manifest in sorted(directory.rglob("*.manifest.json")):
        recording = manifest.with_name(manifest.name.removesuffix(".manifest.json") + ".ndjson.gz")
        if not recording.is_file():
            errors.append(f"missing recording: {recording}")

    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
