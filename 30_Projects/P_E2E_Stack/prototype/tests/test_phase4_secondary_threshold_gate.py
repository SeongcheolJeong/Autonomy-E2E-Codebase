import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROTOTYPE_DIR = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run_script(script_path: Path, *args: str, expected_rc: int = 0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [PYTHON, str(script_path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != expected_rc:
        raise AssertionError(
            f"Unexpected return code: got {proc.returncode}, expected {expected_rc}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


class Phase4SecondaryThresholdGateTests(unittest.TestCase):
    def _write_reference_map(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                [
                    "# Phase-4 External Reference Map",
                    "",
                    "| Phase-4 Module | Priority | Primary References | Pattern to Extract | Local First Target |",
                    "|---|---|---|---|---|",
                    "| `adp` | P1 | `autowarefoundation/autoware` | primary workflow pattern | target_adp |",
                    "",
                    "## Secondary Module-to-Reference Mapping",
                    "",
                    "| Phase-4 Module | Secondary References | Candidate Patterns to Extract |",
                    "|---|---|---|",
                    "| `adp` | `ApolloAuto/apollo` | moduleized planning control handoff contracts |",
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_reference_index_primary_only(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "phase4_reference_index_schema_version": "phase4_reference_index_v0",
                    "generated_at": "2026-02-28T00:00:00+00:00",
                    "repositories": [
                        {
                            "repository": "autowarefoundation/autoware",
                            "observed_patterns": ["primary workflow pattern"],
                        }
                    ],
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_reference_pattern_scan_fails_when_secondary_coverage_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference_map = tmp_path / "PHASE4_EXTERNAL_REFERENCE_MAP.md"
            reference_index = tmp_path / "PHASE4_REFERENCE_SCAN_INDEX_STUB.json"
            out_path = tmp_path / "phase4_reference_pattern_scan_report.json"
            self._write_reference_map(reference_map)
            self._write_reference_index_primary_only(reference_index)

            proc = run_script(
                PROTOTYPE_DIR / "phase4_reference_pattern_scan_stub.py",
                "--reference-map",
                str(reference_map),
                "--reference-index",
                str(reference_index),
                "--module",
                "adp",
                "--secondary-min-coverage-ratio",
                "1.0",
                "--out",
                str(out_path),
                expected_rc=2,
            )
            self.assertIn(
                "[error] phase4_reference_pattern_scan_stub.py: secondary reference pattern coverage below threshold for module adp:",
                proc.stderr,
            )
            self.assertNotIn("Traceback", proc.stderr)

    def test_reference_pattern_scan_passes_when_secondary_threshold_not_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference_map = tmp_path / "PHASE4_EXTERNAL_REFERENCE_MAP.md"
            reference_index = tmp_path / "PHASE4_REFERENCE_SCAN_INDEX_STUB.json"
            out_path = tmp_path / "phase4_reference_pattern_scan_report.json"
            self._write_reference_map(reference_map)
            self._write_reference_index_primary_only(reference_index)

            proc = run_script(
                PROTOTYPE_DIR / "phase4_reference_pattern_scan_stub.py",
                "--reference-map",
                str(reference_map),
                "--reference-index",
                str(reference_index),
                "--module",
                "adp",
                "--out",
                str(out_path),
            )
            self.assertIn("[ok] module=adp matched=1/1 coverage=1.000", proc.stdout)
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIsNone(payload.get("secondary_min_coverage_ratio"))
            self.assertEqual(payload.get("secondary_total_expected_pattern_count"), 1)
            self.assertEqual(payload.get("secondary_total_matched_pattern_count"), 0)

    def test_run_e2e_rejects_invalid_secondary_min_coverage_ratio_before_batch_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_e2e_pipeline.py",
                "--batch-spec",
                str(tmp_path / "missing_batch_spec.json"),
                "--release-id",
                "REL_PHASE4_SECONDARY_THRESHOLD_001",
                "--sds-version",
                "sds_v1",
                "--phase4-reference-secondary-min-coverage-ratio",
                "1.5",
                expected_rc=1,
            )
            self.assertIn(
                "[error] run_e2e_pipeline.py: phase4-reference-secondary-min-coverage-ratio must be between 0 and 1",
                proc.stderr,
            )
            self.assertNotIn("Traceback", proc.stderr)

    def test_run_ci_pipeline_rejects_invalid_secondary_min_coverage_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch_spec.json"
            batch_spec.write_text("{}\n", encoding="utf-8")
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_pipeline.py",
                "--batch-spec",
                str(batch_spec),
                "--release-id",
                "REL_PHASE4_SECONDARY_THRESHOLD_002",
                "--phase4-reference-secondary-min-coverage-ratio",
                "oops",
                expected_rc=1,
            )
            self.assertIn(
                "[error] run_ci_pipeline.py: phase4-reference-secondary-min-coverage-ratio must be a number, got: oops",
                proc.stderr,
            )
            self.assertNotIn("Traceback", proc.stderr)

    def test_run_ci_pipeline_dry_run_forwards_secondary_min_coverage_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch_spec.json"
            batch_spec.write_text("{}\n", encoding="utf-8")
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_pipeline.py",
                "--batch-spec",
                str(batch_spec),
                "--release-id",
                "REL_PHASE4_SECONDARY_THRESHOLD_003",
                "--phase4-enable-hooks-input",
                "true",
                "--phase4-reference-secondary-min-coverage-ratio",
                "0.5",
                "--dry-run",
            )
            self.assertIn(
                "--phase4-reference-secondary-min-coverage-ratio 0.5",
                proc.stdout,
            )


if __name__ == "__main__":
    unittest.main()
