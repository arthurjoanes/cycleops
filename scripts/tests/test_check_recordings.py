"""Exercise the recording budget as the CI and local checks invoke it."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-recordings.py"
LIMIT_BYTES = 5 * 1024 * 1024


class RecordingBudgetTests(unittest.TestCase):
    def test_manifest_counts_toward_the_recording_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            nested = directory / "dated"
            nested.mkdir()
            recording = nested / "record.ndjson.gz"
            # Each file fits separately; the pair crosses the budget by one byte.
            with recording.open("wb") as output:
                output.truncate(LIMIT_BYTES - 1)
            manifest = nested / "record.manifest.json"
            manifest.write_bytes(b"{}")
            result = self.run_check(directory)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("5242881 bytes > 5242880", result.stdout)

            # A pair exactly at the limit is valid.
            with recording.open("r+b") as output:
                output.truncate(LIMIT_BYTES - 2)
            result = self.run_check(directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_recording_cannot_skip_the_manifest_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "record.ndjson.gz").write_bytes(b"recording")
            result = self.run_check(directory)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("missing manifest", result.stdout)

    def test_orphan_manifest_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "record.manifest.json").write_bytes(b"{}")
            result = self.run_check(directory)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("missing recording", result.stdout)

    @staticmethod
    def run_check(directory: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
