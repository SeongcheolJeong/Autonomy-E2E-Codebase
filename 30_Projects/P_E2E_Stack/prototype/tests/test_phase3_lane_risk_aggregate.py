from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_release_summary_artifact import summarize_phase3_lane_risk


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


class Phase3LaneRiskAggregateTests(unittest.TestCase):
    def test_phase3_lane_risk_summary_aggregates_manifests(self) -> None:
        summary = summarize_phase3_lane_risk(
            [
                {
                    "batch_id": "BATCH_LANE_002",
                    "phase3_lane_risk_summary_run_count": 2,
                    "phase3_lane_risk_min_ttc_same_lane_sec": 2.1,
                    "phase3_lane_risk_min_ttc_adjacent_lane_sec": 2.4,
                    "phase3_lane_risk_min_ttc_any_lane_sec": 2.1,
                    "phase3_lane_risk_ttc_under_3s_same_lane_total": 3,
                    "phase3_lane_risk_ttc_under_3s_adjacent_lane_total": 1,
                    "phase3_lane_risk_same_lane_rows_total": 12,
                    "phase3_lane_risk_adjacent_lane_rows_total": 10,
                    "phase3_lane_risk_other_lane_rows_total": 2,
                    "phase3_lane_risk_gate_result": "hold",
                    "phase3_lane_risk_gate_reason_count": 2,
                    "phase3_lane_risk_gate_min_ttc_same_lane_sec": 2.5,
                    "phase3_lane_risk_gate_min_ttc_adjacent_lane_sec": 2.0,
                    "phase3_lane_risk_gate_min_ttc_any_lane_sec": 1.8,
                    "phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total": 3,
                    "phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total": 2,
                    "phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total": 4,
                },
                {
                    "batch_id": "BATCH_LANE_001",
                    "phase3_lane_risk_summary_run_count": 1,
                    "phase3_lane_risk_min_ttc_same_lane_sec": 2.6,
                    "phase3_lane_risk_min_ttc_adjacent_lane_sec": 2.2,
                    "phase3_lane_risk_min_ttc_any_lane_sec": 2.2,
                    "phase3_lane_risk_ttc_under_3s_same_lane_total": 1,
                    "phase3_lane_risk_ttc_under_3s_adjacent_lane_total": 2,
                    "phase3_lane_risk_same_lane_rows_total": 8,
                    "phase3_lane_risk_adjacent_lane_rows_total": 9,
                    "phase3_lane_risk_other_lane_rows_total": 1,
                    "phase3_lane_risk_gate_result": "pass",
                    "phase3_lane_risk_gate_reason_count": 0,
                    "phase3_lane_risk_gate_min_ttc_same_lane_sec": 2.5,
                    "phase3_lane_risk_gate_min_ttc_adjacent_lane_sec": 2.5,
                    "phase3_lane_risk_gate_min_ttc_any_lane_sec": 1.5,
                    "phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total": 2,
                    "phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total": 1,
                    "phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total": 3,
                },
                {
                    "batch_id": "BATCH_LANE_003",
                    "phase3_lane_risk_summary_run_count": 0,
                    "phase3_lane_risk_gate_result": "n/a",
                },
            ]
        )
        self.assertEqual(int(summary.get("pipeline_manifest_count", 0) or 0), 3)
        self.assertEqual(int(summary.get("evaluated_manifest_count", 0) or 0), 2)
        self.assertEqual(int(summary.get("lane_risk_summary_run_count_total", 0) or 0), 3)
        self.assertEqual(summary.get("gate_result_counts"), {"hold": 1, "pass": 1})
        self.assertEqual(int(summary.get("gate_reason_count_total", 0) or 0), 2)
        self.assertEqual(summary.get("gate_min_ttc_same_lane_sec_counts"), {"2.5": 2})
        self.assertEqual(summary.get("gate_min_ttc_adjacent_lane_sec_counts"), {"2": 1, "2.5": 1})
        self.assertEqual(summary.get("gate_min_ttc_any_lane_sec_counts"), {"1.5": 1, "1.8": 1})
        self.assertEqual(summary.get("gate_max_ttc_under_3s_same_lane_total_counts"), {"2": 1, "3": 1})
        self.assertEqual(summary.get("gate_max_ttc_under_3s_adjacent_lane_total_counts"), {"1": 1, "2": 1})
        self.assertEqual(summary.get("gate_max_ttc_under_3s_any_lane_total_counts"), {"3": 1, "4": 1})
        self.assertAlmostEqual(float(summary.get("min_ttc_same_lane_sec", 0.0) or 0.0), 2.1, places=6)
        self.assertEqual(str(summary.get("lowest_same_lane_batch_id", "")), "BATCH_LANE_002")
        self.assertAlmostEqual(float(summary.get("min_ttc_adjacent_lane_sec", 0.0) or 0.0), 2.2, places=6)
        self.assertEqual(str(summary.get("lowest_adjacent_lane_batch_id", "")), "BATCH_LANE_001")
        self.assertAlmostEqual(float(summary.get("min_ttc_any_lane_sec", 0.0) or 0.0), 2.1, places=6)
        self.assertEqual(str(summary.get("lowest_any_lane_batch_id", "")), "BATCH_LANE_002")
        self.assertEqual(int(summary.get("ttc_under_3s_same_lane_total", 0) or 0), 4)
        self.assertEqual(int(summary.get("ttc_under_3s_adjacent_lane_total", 0) or 0), 3)
        self.assertEqual(int(summary.get("same_lane_rows_total", 0) or 0), 20)
        self.assertEqual(int(summary.get("adjacent_lane_rows_total", 0) or 0), 19)
        self.assertEqual(int(summary.get("other_lane_rows_total", 0) or 0), 3)

    def test_build_summary_artifact_collects_phase3_lane_risk_gate_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifacts_root = tmp_path / "artifacts"
            reports_root = artifacts_root / "reports"
            batch_root = artifacts_root / "batch_lane"
            reports_root.mkdir(parents=True, exist_ok=True)
            batch_root.mkdir(parents=True, exist_ok=True)

            summary_path = reports_root / "REL_PHASE3_LANE_RISK_001_sds_v1.summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_LANE_RISK_001_sds_v1",
                        "sds_version": "sds_v1",
                        "final_result": "PASS",
                        "generated_at": "2026-03-02T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            batch_result_path = batch_root / "batch_result.json"
            batch_result_path.write_text(
                json.dumps(
                    {
                        "batch_id": "BATCH_LANE_001",
                        "lane_risk_batch_summary": {
                            "lane_risk_batch_summary_schema_version": "lane_risk_batch_summary_v0",
                            "lane_risk_summary_run_count": 2,
                            "min_ttc_same_lane_sec": 2.4,
                            "min_ttc_adjacent_lane_sec": 3.1,
                            "min_ttc_any_lane_sec": 2.4,
                            "ttc_under_3s_same_lane_total": 1,
                            "ttc_under_3s_adjacent_lane_total": 2,
                            "same_lane_rows_total": 12,
                            "adjacent_lane_rows_total": 8,
                            "other_lane_rows_total": 3,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            pipeline_manifest_path = batch_root / "pipeline_result.json"
            pipeline_manifest_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_LANE_RISK_001_sds_v1",
                        "batch_id": "BATCH_LANE_001",
                        "batch_result_path": str(batch_result_path),
                        "overall_result": "HOLD",
                        "strict_gate": True,
                        "trend_gate": {"result": "PASS"},
                        "reports": [{"sds_version": "sds_v1"}],
                        "functional_quality_gates": {
                            "phase3_lane_risk_gate": {
                                "result": "HOLD",
                                "reasons": [
                                    "phase3_lane_risk_min_ttc_same_lane_sec 2.400 < min_ttc_same_lane_sec 2.500",
                                ],
                                "details": {
                                    "min_ttc_same_lane_sec": 2.5,
                                    "min_ttc_adjacent_lane_sec": 2.0,
                                    "min_ttc_any_lane_sec": 1.8,
                                    "max_ttc_under_3s_same_lane_total": 2,
                                    "max_ttc_under_3s_adjacent_lane_total": 2,
                                    "max_ttc_under_3s_any_lane_total": 3,
                                },
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            out_text = tmp_path / "summary.txt"
            out_json = tmp_path / "summary.json"
            out_db = tmp_path / "summary.db"
            run_script(
                PROTOTYPE_DIR / "build_release_summary_artifact.py",
                "--release-prefix",
                "REL_PHASE3_LANE_RISK_001",
                "--artifacts-root",
                str(artifacts_root),
                "--out-text",
                str(out_text),
                "--out-json",
                str(out_json),
                "--out-db",
                str(out_db),
                "--python-bin",
                PYTHON,
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            manifests = payload.get("pipeline_manifests", [])
            self.assertEqual(len(manifests), 1)
            row = manifests[0]
            self.assertEqual(str(row.get("phase3_lane_risk_gate_result", "")), "hold")
            self.assertEqual(int(row.get("phase3_lane_risk_gate_reason_count", 0) or 0), 1)
            self.assertAlmostEqual(float(row.get("phase3_lane_risk_gate_min_ttc_same_lane_sec", 0.0) or 0.0), 2.5, places=6)
            self.assertAlmostEqual(float(row.get("phase3_lane_risk_gate_min_ttc_adjacent_lane_sec", 0.0) or 0.0), 2.0, places=6)
            self.assertAlmostEqual(float(row.get("phase3_lane_risk_gate_min_ttc_any_lane_sec", 0.0) or 0.0), 1.8, places=6)
            self.assertEqual(int(row.get("phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total", 0) or 0), 2)
            self.assertEqual(int(row.get("phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total", 0) or 0), 2)
            self.assertEqual(int(row.get("phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total", 0) or 0), 3)
            lane_summary = payload.get("phase3_lane_risk_summary", {})
            self.assertEqual(int(lane_summary.get("evaluated_manifest_count", 0) or 0), 1)
            self.assertEqual(lane_summary.get("gate_result_counts"), {"hold": 1})
            self.assertEqual(int(lane_summary.get("gate_reason_count_total", 0) or 0), 1)
            self.assertEqual(lane_summary.get("gate_min_ttc_same_lane_sec_counts"), {"2.5": 1})
            self.assertEqual(lane_summary.get("gate_min_ttc_adjacent_lane_sec_counts"), {"2": 1})
            self.assertEqual(lane_summary.get("gate_min_ttc_any_lane_sec_counts"), {"1.8": 1})
            self.assertEqual(lane_summary.get("gate_max_ttc_under_3s_same_lane_total_counts"), {"2": 1})
            self.assertEqual(lane_summary.get("gate_max_ttc_under_3s_adjacent_lane_total_counts"), {"2": 1})
            self.assertEqual(lane_summary.get("gate_max_ttc_under_3s_any_lane_total_counts"), {"3": 1})
            self.assertIn(
                "phase3_lane_risk=evaluated:1,runs:2,gate_results:hold:1,gate_reasons_total:1,",
                out_text.read_text(encoding="utf-8"),
            )

    def test_markdown_renderer_renders_phase3_lane_risk_gate_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            summary_json.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_LANE_MD_001",
                        "summary_count": 1,
                        "sds_versions": ["sds_v1"],
                        "final_result_counts": {"PASS": 1},
                        "pipeline_manifest_count": 1,
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "pipeline_manifests": [],
                        "timing_ms": {"total": 100},
                        "phase3_lane_risk_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "lane_risk_summary_run_count_total": 2,
                            "gate_result_counts": {"hold": 1},
                            "gate_reason_count_total": 1,
                            "min_ttc_same_lane_sec": 2.4,
                            "lowest_same_lane_batch_id": "BATCH_LANE_001",
                            "min_ttc_adjacent_lane_sec": 3.1,
                            "lowest_adjacent_lane_batch_id": "BATCH_LANE_001",
                            "min_ttc_any_lane_sec": 2.4,
                            "lowest_any_lane_batch_id": "BATCH_LANE_001",
                            "ttc_under_3s_same_lane_total": 1,
                            "ttc_under_3s_adjacent_lane_total": 2,
                            "same_lane_rows_total": 12,
                            "adjacent_lane_rows_total": 8,
                            "other_lane_rows_total": 3,
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
                "- phase3_lane_risk: `evaluated=1, runs=2, gate_results=hold:1, gate_reasons_total=1,",
                proc.stdout,
            )
            self.assertIn("min_ttc_same_lane=2.400 (BATCH_LANE_001)", proc.stdout)

    def test_notification_warns_on_phase3_lane_risk_threshold_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_LANE_NOTIFY_MISMATCH_WARN_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_lane_risk_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "lane_risk_summary_run_count_total": 2,
                            "gate_result_counts": {"pass": 1},
                            "gate_reason_count_total": 0,
                            "min_ttc_same_lane_sec": 3.0,
                            "lowest_same_lane_batch_id": "BATCH_LANE_001",
                            "min_ttc_adjacent_lane_sec": 3.0,
                            "lowest_adjacent_lane_batch_id": "BATCH_LANE_001",
                            "min_ttc_any_lane_sec": 3.0,
                            "lowest_any_lane_batch_id": "BATCH_LANE_001",
                            "ttc_under_3s_same_lane_total": 0,
                            "ttc_under_3s_adjacent_lane_total": 0,
                            "same_lane_rows_total": 10,
                            "adjacent_lane_rows_total": 10,
                            "other_lane_rows_total": 0,
                            "gate_min_ttc_same_lane_sec_counts": {"2.0": 1},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_path),
                "--out-json",
                str(out_path),
                "--phase3-lane-risk-min-ttc-same-lane-warn-min",
                "2.5",
                "--phase3-lane-risk-min-ttc-same-lane-hold-min",
                "2.0",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "WARN")
            self.assertEqual(str(payload.get("phase3_lane_risk_threshold_drift_severity", "")), "WARN")
            self.assertIn(
                "phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch",
                payload.get("phase3_lane_risk_warning_reasons", []),
            )
            self.assertIn(
                "min_ttc_same_lane_warn_min=expected:2.500,observed:2.0:1",
                str(payload.get("phase3_lane_risk_threshold_drift_summary_text", "")),
            )
            self.assertIn(
                "phase3_lane_risk_threshold_drift_severity=WARN",
                str(payload.get("message_text", "")),
            )

    def test_notification_holds_on_phase3_lane_risk_threshold_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_LANE_NOTIFY_MISMATCH_HOLD_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_lane_risk_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "lane_risk_summary_run_count_total": 2,
                            "gate_result_counts": {"pass": 1},
                            "gate_reason_count_total": 0,
                            "min_ttc_same_lane_sec": 3.0,
                            "lowest_same_lane_batch_id": "BATCH_LANE_001",
                            "min_ttc_adjacent_lane_sec": 3.0,
                            "lowest_adjacent_lane_batch_id": "BATCH_LANE_001",
                            "min_ttc_any_lane_sec": 3.0,
                            "lowest_any_lane_batch_id": "BATCH_LANE_001",
                            "ttc_under_3s_same_lane_total": 0,
                            "ttc_under_3s_adjacent_lane_total": 0,
                            "same_lane_rows_total": 10,
                            "adjacent_lane_rows_total": 10,
                            "other_lane_rows_total": 0,
                            "gate_max_ttc_under_3s_any_lane_total_counts": {"3": 1},
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_path),
                "--out-json",
                str(out_path),
                "--phase3-lane-risk-ttc-under-3s-any-lane-warn-max",
                "3",
                "--phase3-lane-risk-ttc-under-3s-any-lane-hold-max",
                "2",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "HOLD")
            self.assertEqual(str(payload.get("phase3_lane_risk_threshold_drift_severity", "")), "HOLD")
            self.assertIn(
                "phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch",
                payload.get("phase3_lane_risk_warning_reasons", []),
            )
            self.assertIn(
                "ttc_under_3s_any_lane_hold_max=expected:2,observed:3:1",
                str(payload.get("phase3_lane_risk_threshold_drift_summary_text", "")),
            )
            self.assertIn(
                "phase3_lane_risk_threshold_drift_severity=HOLD",
                str(payload.get("message_text", "")),
            )


if __name__ == "__main__":
    unittest.main()
