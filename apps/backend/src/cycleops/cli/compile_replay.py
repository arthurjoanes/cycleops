"""compile-replay: builds the replay artifact from a recording or from the synthetic source.

A recording is written only when it passes acceptance: at least one incident goes from
confirmed to resolved inside it.
"""

import argparse
import sys
from pathlib import Path

from cycleops.adapters.synthetic import load_config
from cycleops.application.replay import accepted, compile_recording, compile_synthetic, write
from cycleops.policy import load_policy

REPLAY_DIR = Path("apps/web/public/replay")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compile-replay", description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--recording", type=Path, help="a recorded .ndjson.gz")
    source.add_argument("--synthetic", type=Path, help="a synthetic config, e.g. demo.yaml")
    parser.add_argument("--out", type=Path, help="default: apps/web/public/replay/<mode>/")
    args = parser.parse_args(argv)
    if args.recording:
        artifact = compile_recording(args.recording, load_policy("oslo"))
        out = args.out or REPLAY_DIR / "recorded"
    else:
        artifact = compile_synthetic(load_config(args.synthetic), load_policy("synthetic"))
        out = args.out or REPLAY_DIR / "synthetic"
    manifest = artifact.manifest
    print(
        f"{manifest.mode}: {manifest.event_count} events, {len(manifest.incidents)} incidents "
        f"confirmed, {sum(i.resolved_sequence is not None for i in manifest.incidents)} resolved"
    )
    # Acceptance is for recordings; a synthetic scenario may well have nothing resolved.
    if args.recording and not accepted(manifest):
        print("not accepted: no incident went from confirmed to resolved", file=sys.stderr)
        return 3
    write(artifact, out)
    print(f"written to {out}")
    return 0
