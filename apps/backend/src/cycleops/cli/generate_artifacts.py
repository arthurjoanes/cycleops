"""generate-artifacts: regenerates the generated zones, or with --check reports any diff.

- packages/contracts/schemas/: JSON Schema (Draft 2020-12) of the contracts;
- packages/contracts/fixtures/sort-keys.json: sortKey order for the frontend test;
- apps/web/public/replay/synthetic/: the demo artifact from fixtures/synthetic/demo.yaml;
- apps/web/public/replay/recorded/: from the recording named in fixtures/recorded/demo.yaml,
  when that file exists and the recording passes acceptance.

.gz files are compared decompressed: deflate output differs between zlib builds.
"""

import argparse
import gzip
import io
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from cycleops.adapters.synthetic import load_config
from cycleops.application.replay import (
    Artifact,
    accepted,
    compile_recording,
    compile_synthetic,
    write,
)
from cycleops.contracts import SCHEMA_MODELS
from cycleops.policy import load_policy

# What this command owns. It deletes and rewrites them.
ZONES = (
    Path("packages/contracts/schemas"),
    Path("packages/contracts/fixtures"),
    Path("apps/web/public/replay"),
)
EDGE_SORT_KEYS = [
    "0|1791360000|S-0001",
    "1|1791360000|S-0001",
    "0|1791359999|zz",
    "0|1791360000|A-1",
    "0|1791360000|A_1",
    "0|1791360000|a",
    "0|0000000001|x",
    "1|0000000000|0",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generate-artifacts", description=__doc__)
    parser.add_argument("--check", action="store_true", help="only compare, exit 1 on diff")
    args = parser.parse_args(argv)
    if not args.check:
        for zone in ZONES:
            shutil.rmtree(zone, ignore_errors=True)
        build(Path("."))
        print("generated: " + ", ".join(str(z) for z in ZONES))
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        build(Path(tmp))
        diffs = compare(Path(tmp), Path("."))
    for diff in diffs:
        print(diff)
    return 1 if diffs else 0


def build(root: Path) -> None:
    contracts = root / "packages/contracts"
    for name, model in SCHEMA_MODELS.items():
        schema = TypeAdapter(model).json_schema(mode="serialization", by_alias=True)
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
        _write_json(contracts / "schemas" / f"{name}.schema.json", schema)
    synthetic = compile_synthetic(
        load_config(Path("fixtures/synthetic/demo.yaml")), load_policy("synthetic")
    )
    write(synthetic, root / "apps/web/public/replay/synthetic")
    _write_json(contracts / "fixtures" / "sort-keys.json", _sort_keys(synthetic))
    recorded = _recorded()
    if recorded is not None:
        write(recorded, root / "apps/web/public/replay/recorded")


def _recorded() -> Artifact | None:
    demo = Path("fixtures/recorded/demo.yaml")
    if not demo.exists():
        return None
    name = yaml.safe_load(demo.read_text(encoding="utf-8"))["recording"]
    artifact = compile_recording(demo.parent / name, load_policy("oslo"))
    if not accepted(artifact.manifest):
        raise SystemExit(f"{name} does not pass acceptance")
    return artifact


def _sort_keys(artifact: Artifact) -> dict[str, object]:
    keys = {
        s.decision.sort_key
        for e in artifact.events
        for s in e.stations
        if s.decision.sort_key is not None
    }
    ordered = sorted(keys | set(EDGE_SORT_KEYS))
    shuffled = list(ordered)
    random.Random(0).shuffle(shuffled)
    return {"input": shuffled, "sorted": ordered}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode())


def _content(path: Path) -> bytes:
    data = path.read_bytes()
    return gzip.GzipFile(fileobj=io.BytesIO(data)).read() if path.suffix == ".gz" else data


def compare(generated: Path, current: Path) -> list[str]:
    diffs = []
    for zone in ZONES:
        new = {p.relative_to(generated) for p in (generated / zone).rglob("*") if p.is_file()}
        old = {p.relative_to(current) for p in (current / zone).rglob("*") if p.is_file()}
        diffs += [f"missing: {p}" for p in sorted(new - old)]
        diffs += [f"not generated: {p}" for p in sorted(old - new)]
        diffs += [
            f"differs: {p}"
            for p in sorted(new & old)
            if _content(generated / p) != _content(current / p)
        ]
    return diffs


if __name__ == "__main__":
    sys.exit(main())
