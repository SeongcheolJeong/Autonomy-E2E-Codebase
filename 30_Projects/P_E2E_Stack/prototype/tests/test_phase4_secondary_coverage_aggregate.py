from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_release_summary_artifact import (
    summarize_phase4_primary_coverage,
    summarize_phase4_secondary_coverage,
)


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


class Phase4SecondaryCoverageAggregateTests(unittest.TestCase):
    def test_primary_coverage_summary_aggregates_eligible_manifests(self) -> None:
        summary = summarize_phase4_primary_coverage(
            [
                {
                    "batch_id": "BATCH_002",
                    "phase4_reference_primary_total_coverage_ratio": 0.25,
                    "phase4_reference_primary_module_coverage": {
                        "adp": 0.25,
                        "copilot": 0.6,
                    },
                },
                {
                    "batch_id": "BATCH_001",
                    "phase4_reference_primary_total_coverage_ratio": 0.75,
                    "phase4_reference_primary_module_coverage": {
                        "adp": 0.75,
                    },
                },
                {
                    "batch_id": "BATCH_003",
                    "phase4_reference_primary_total_coverage_ratio": 0.0,
                    "phase4_reference_primary_module_coverage": {},
                },
            ]
        )
        self.assertEqual(summary.get("pipeline_manifest_count"), 3)
        self.assertEqual(summary.get("evaluated_manifest_count"), 2)
        self.assertEqual(summary.get("lowest_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_coverage_ratio", 0.0)), 0.25)
        self.assertAlmostEqual(float(summary.get("max_coverage_ratio", 0.0)), 0.75)
        self.assertAlmostEqual(float(summary.get("avg_coverage_ratio", 0.0)), 0.50)
        module_summary = summary.get("module_coverage_summary", {})
        self.assertAlmostEqual(
            float(module_summary.get("adp", {}).get("min_coverage_ratio", 0.0)),
            0.25,
        )
        self.assertAlmostEqual(
            float(module_summary.get("adp", {}).get("avg_coverage_ratio", 0.0)),
            0.50,
        )
        self.assertAlmostEqual(
            float(module_summary.get("adp", {}).get("max_coverage_ratio", 0.0)),
            0.75,
        )
        self.assertEqual(module_summary.get("adp", {}).get("lowest_batch_id"), "BATCH_002")
        self.assertEqual(module_summary.get("adp", {}).get("highest_batch_id"), "BATCH_001")
        self.assertAlmostEqual(
            float(module_summary.get("copilot", {}).get("min_coverage_ratio", 0.0)),
            0.60,
        )
        self.assertEqual(int(module_summary.get("copilot", {}).get("sample_count", 0)), 1)

    def test_primary_coverage_summary_returns_na_when_no_eligible_rows(self) -> None:
        summary = summarize_phase4_primary_coverage(
            [
                {
                    "batch_id": "BATCH_NONE",
                    "phase4_reference_primary_total_coverage_ratio": 0.0,
                    "phase4_reference_primary_module_coverage": {},
                }
            ]
        )
        self.assertEqual(summary.get("pipeline_manifest_count"), 1)
        self.assertEqual(summary.get("evaluated_manifest_count"), 0)
        self.assertIsNone(summary.get("min_coverage_ratio"))
        self.assertIsNone(summary.get("avg_coverage_ratio"))
        self.assertIsNone(summary.get("max_coverage_ratio"))
        self.assertEqual(summary.get("lowest_batch_id"), "")
        self.assertEqual(summary.get("highest_batch_id"), "")
        self.assertEqual(summary.get("module_coverage_summary"), {})

    def test_secondary_coverage_summary_aggregates_eligible_manifests(self) -> None:
        summary = summarize_phase4_secondary_coverage(
            [
                {
                    "batch_id": "BATCH_002",
                    "phase4_reference_secondary_total_coverage_ratio": 0.25,
                    "phase4_reference_secondary_module_count": 2,
                    "phase4_reference_secondary_module_coverage": {
                        "adp": 0.25,
                        "copilot": 0.6,
                    },
                },
                {
                    "batch_id": "BATCH_001",
                    "phase4_reference_secondary_total_coverage_ratio": 0.75,
                    "phase4_reference_secondary_module_count": 1,
                    "phase4_reference_secondary_module_coverage": {
                        "adp": 0.75,
                    },
                },
                {
                    "batch_id": "BATCH_003",
                    "phase4_reference_secondary_total_coverage_ratio": 0.99,
                    "phase4_reference_secondary_module_count": 0,
                    "phase4_reference_secondary_module_coverage": {
                        "adp": 0.99,
                    },
                },
            ]
        )
        self.assertEqual(summary.get("pipeline_manifest_count"), 3)
        self.assertEqual(summary.get("evaluated_manifest_count"), 2)
        self.assertEqual(summary.get("lowest_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_coverage_ratio", 0.0)), 0.25)
        self.assertAlmostEqual(float(summary.get("max_coverage_ratio", 0.0)), 0.75)
        self.assertAlmostEqual(float(summary.get("avg_coverage_ratio", 0.0)), 0.50)
        self.assertEqual(int(summary.get("lowest_batch_secondary_module_count", 0)), 2)
        by_min_modules = summary.get("secondary_coverage_by_min_modules", {})
        self.assertEqual(int(by_min_modules.get("1", {}).get("evaluated_manifest_count", 0)), 2)
        self.assertAlmostEqual(float(by_min_modules.get("1", {}).get("min_coverage_ratio", 0.0)), 0.25)
        self.assertEqual(by_min_modules.get("1", {}).get("lowest_batch_id"), "BATCH_002")
        self.assertEqual(int(by_min_modules.get("1", {}).get("lowest_batch_secondary_module_count", 0)), 2)
        self.assertEqual(int(by_min_modules.get("2", {}).get("evaluated_manifest_count", 0)), 1)
        self.assertAlmostEqual(float(by_min_modules.get("2", {}).get("min_coverage_ratio", 0.0)), 0.25)
        self.assertEqual(by_min_modules.get("2", {}).get("lowest_batch_id"), "BATCH_002")
        self.assertEqual(int(by_min_modules.get("2", {}).get("lowest_batch_secondary_module_count", 0)), 2)
        module_summary = summary.get("module_coverage_summary", {})
        self.assertAlmostEqual(
            float(module_summary.get("adp", {}).get("min_coverage_ratio", 0.0)),
            0.25,
        )
        self.assertAlmostEqual(
            float(module_summary.get("adp", {}).get("avg_coverage_ratio", 0.0)),
            0.50,
        )
        self.assertAlmostEqual(
            float(module_summary.get("adp", {}).get("max_coverage_ratio", 0.0)),
            0.75,
        )
        self.assertEqual(module_summary.get("adp", {}).get("lowest_batch_id"), "BATCH_002")
        self.assertEqual(module_summary.get("adp", {}).get("highest_batch_id"), "BATCH_001")
        self.assertAlmostEqual(
            float(module_summary.get("copilot", {}).get("min_coverage_ratio", 0.0)),
            0.60,
        )
        self.assertEqual(int(module_summary.get("copilot", {}).get("sample_count", 0)), 1)

    def test_secondary_coverage_summary_returns_na_when_no_eligible_rows(self) -> None:
        summary = summarize_phase4_secondary_coverage(
            [
                {
                    "batch_id": "BATCH_NONE",
                    "phase4_reference_secondary_total_coverage_ratio": 0.1,
                    "phase4_reference_secondary_module_count": 0,
                }
            ]
        )
        self.assertEqual(summary.get("pipeline_manifest_count"), 1)
        self.assertEqual(summary.get("evaluated_manifest_count"), 0)
        self.assertIsNone(summary.get("min_coverage_ratio"))
        self.assertIsNone(summary.get("avg_coverage_ratio"))
        self.assertIsNone(summary.get("max_coverage_ratio"))
        self.assertEqual(summary.get("lowest_batch_id"), "")
        self.assertEqual(summary.get("highest_batch_id"), "")
        self.assertIsNone(summary.get("lowest_batch_secondary_module_count"))
        self.assertEqual(summary.get("secondary_coverage_by_min_modules"), {})
        self.assertEqual(summary.get("module_coverage_summary"), {})

    def test_markdown_renderer_renders_phase4_secondary_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            summary_json.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE4_AGG_2026_0001",
                        "summary_count": 1,
                        "sds_versions": ["sds_v1"],
                        "final_result_counts": {"PASS": 1},
                        "pipeline_manifest_count": 2,
                        "pipeline_overall_counts": {"PASS": 2},
                        "pipeline_trend_counts": {"PASS": 2},
                        "pipeline_manifests": [],
                        "timing_ms": {"total": 100},
                        "phase4_primary_coverage_summary": {
                            "evaluated_manifest_count": 2,
                            "pipeline_manifest_count": 2,
                            "min_coverage_ratio": 0.25,
                            "avg_coverage_ratio": 0.50,
                            "max_coverage_ratio": 0.75,
                            "lowest_batch_id": "BATCH_LOW",
                            "highest_batch_id": "BATCH_HIGH",
                            "module_coverage_summary": {
                                "adp": {
                                    "sample_count": 2,
                                    "min_coverage_ratio": 0.25,
                                    "avg_coverage_ratio": 0.50,
                                    "max_coverage_ratio": 0.75,
                                    "lowest_batch_id": "BATCH_LOW",
                                    "highest_batch_id": "BATCH_HIGH",
                                },
                                "copilot": {
                                    "sample_count": 1,
                                    "min_coverage_ratio": 0.60,
                                    "avg_coverage_ratio": 0.60,
                                    "max_coverage_ratio": 0.60,
                                    "lowest_batch_id": "BATCH_LOW",
                                    "highest_batch_id": "BATCH_LOW",
                                },
                            },
                        },
                        "phase4_secondary_coverage_summary": {
                            "evaluated_manifest_count": 2,
                            "pipeline_manifest_count": 2,
                            "min_coverage_ratio": 0.25,
                            "avg_coverage_ratio": 0.50,
                            "max_coverage_ratio": 0.75,
                            "lowest_batch_id": "BATCH_LOW",
                            "highest_batch_id": "BATCH_HIGH",
                            "module_coverage_summary": {
                                "adp": {
                                    "sample_count": 2,
                                    "min_coverage_ratio": 0.25,
                                    "avg_coverage_ratio": 0.50,
                                    "max_coverage_ratio": 0.75,
                                    "lowest_batch_id": "BATCH_LOW",
                                    "highest_batch_id": "BATCH_HIGH",
                                },
                                "copilot": {
                                    "sample_count": 1,
                                    "min_coverage_ratio": 0.60,
                                    "avg_coverage_ratio": 0.60,
                                    "max_coverage_ratio": 0.60,
                                    "lowest_batch_id": "BATCH_LOW",
                                    "highest_batch_id": "BATCH_LOW",
                                },
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            proc = run_script(
                PROTOTYPE_DIR / "render_release_summary_markdown.py",
                "--summary-json",
                str(summary_json),
                "--title",
                "Summary",
            )
            self.assertIn(
                "- phase4_primary_coverage: `evaluated=2, min=0.250 (BATCH_LOW), avg=0.500, max=0.750 (BATCH_HIGH)`",
                proc.stdout,
            )
            self.assertIn(
                "- phase4_primary_module_coverage: `adp:min=0.250 (BATCH_LOW), avg=0.500, max=0.750 (BATCH_HIGH); copilot:min=0.600 (BATCH_LOW), avg=0.600, max=0.600 (BATCH_LOW)`",
                proc.stdout,
            )
            self.assertIn(
                "- phase4_secondary_coverage: `evaluated=2, min=0.250 (BATCH_LOW), avg=0.500, max=0.750 (BATCH_HIGH)`",
                proc.stdout,
            )
            self.assertIn(
                "- phase4_secondary_module_coverage: `adp:min=0.250 (BATCH_LOW), avg=0.500, max=0.750 (BATCH_HIGH); copilot:min=0.600 (BATCH_LOW), avg=0.600, max=0.600 (BATCH_LOW)`",
                proc.stdout,
            )


if __name__ == "__main__":
    unittest.main()
