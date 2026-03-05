from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_release_summary_artifact import summarize_phase3_core_sim


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


class Phase3CoreSimAggregateTests(unittest.TestCase):
    def test_phase3_core_sim_summary_aggregates_manifests(self) -> None:
        summary = summarize_phase3_core_sim(
            [
                {
                    "batch_id": "BATCH_CORE_002",
                    "phase3_core_sim_enabled": True,
                    "phase3_core_sim_status": "timeout",
                    "phase3_core_sim_termination_reason": "timeout",
                    "phase3_core_sim_collision": True,
                    "phase3_core_sim_timeout": True,
                    "phase3_core_sim_min_ttc_same_lane_sec": 1.8,
                    "phase3_core_sim_min_ttc_any_lane_sec": 1.6,
                    "phase3_core_sim_enable_ego_collision_avoidance": True,
                    "phase3_core_sim_avoidance_ttc_threshold_sec": 2.0,
                    "phase3_core_sim_ego_max_brake_mps2": 7.0,
                    "phase3_core_sim_tire_friction_coeff": 0.85,
                    "phase3_core_sim_surface_friction_scale": 0.9,
                    "phase3_core_sim_ego_avoidance_brake_event_count": 2,
                    "phase3_core_sim_ego_avoidance_applied_brake_mps2_max": 6.4,
                    "phase3_core_sim_gate_result": "hold",
                    "phase3_core_sim_gate_reason_count": 2,
                    "phase3_core_sim_gate_require_success": True,
                    "phase3_core_sim_gate_min_ttc_same_lane_sec": 2.5,
                    "phase3_core_sim_gate_min_ttc_any_lane_sec": 2.0,
                },
                {
                    "batch_id": "BATCH_CORE_001",
                    "phase3_core_sim_enabled": True,
                    "phase3_core_sim_status": "success",
                    "phase3_core_sim_termination_reason": "completed",
                    "phase3_core_sim_collision": False,
                    "phase3_core_sim_timeout": False,
                    "phase3_core_sim_min_ttc_same_lane_sec": 2.4,
                    "phase3_core_sim_min_ttc_any_lane_sec": 2.3,
                    "phase3_core_sim_enable_ego_collision_avoidance": True,
                    "phase3_core_sim_avoidance_ttc_threshold_sec": 2.5,
                    "phase3_core_sim_ego_max_brake_mps2": 8.0,
                    "phase3_core_sim_tire_friction_coeff": 0.92,
                    "phase3_core_sim_surface_friction_scale": 1.0,
                    "phase3_core_sim_ego_avoidance_brake_event_count": 1,
                    "phase3_core_sim_ego_avoidance_applied_brake_mps2_max": 5.1,
                    "phase3_core_sim_gate_result": "pass",
                    "phase3_core_sim_gate_reason_count": 0,
                    "phase3_core_sim_gate_require_success": True,
                    "phase3_core_sim_gate_min_ttc_same_lane_sec": 2.5,
                    "phase3_core_sim_gate_min_ttc_any_lane_sec": 2.0,
                },
                {
                    "batch_id": "BATCH_CORE_003",
                    "phase3_core_sim_enabled": False,
                    "phase3_core_sim_status": "n/a",
                    "phase3_core_sim_gate_result": "n/a",
                },
            ]
        )
        self.assertEqual(int(summary.get("pipeline_manifest_count", 0) or 0), 3)
        self.assertEqual(int(summary.get("evaluated_manifest_count", 0) or 0), 2)
        self.assertEqual(summary.get("status_counts"), {"success": 1, "timeout": 1})
        self.assertEqual(summary.get("gate_result_counts"), {"hold": 1, "pass": 1})
        self.assertEqual(int(summary.get("gate_reason_count_total", 0) or 0), 2)
        self.assertEqual(int(summary.get("gate_require_success_enabled_count", 0) or 0), 2)
        self.assertEqual(summary.get("gate_min_ttc_same_lane_sec_counts"), {"2.5": 2})
        self.assertEqual(summary.get("gate_min_ttc_any_lane_sec_counts"), {"2": 2})
        self.assertEqual(int(summary.get("success_manifest_count", 0) or 0), 1)
        self.assertEqual(int(summary.get("collision_manifest_count", 0) or 0), 1)
        self.assertEqual(int(summary.get("timeout_manifest_count", 0) or 0), 1)
        self.assertAlmostEqual(float(summary.get("min_ttc_same_lane_sec", 0.0) or 0.0), 1.8, places=6)
        self.assertEqual(str(summary.get("lowest_same_lane_batch_id", "")), "BATCH_CORE_002")
        self.assertAlmostEqual(float(summary.get("min_ttc_any_lane_sec", 0.0) or 0.0), 1.6, places=6)
        self.assertEqual(str(summary.get("lowest_any_lane_batch_id", "")), "BATCH_CORE_002")
        self.assertEqual(int(summary.get("avoidance_enabled_manifest_count", 0) or 0), 2)
        self.assertEqual(int(summary.get("ego_avoidance_brake_event_count_total", 0) or 0), 3)
        self.assertAlmostEqual(float(summary.get("max_ego_avoidance_applied_brake_mps2", 0.0) or 0.0), 6.4, places=6)
        self.assertEqual(str(summary.get("highest_ego_avoidance_applied_brake_batch_id", "")), "BATCH_CORE_002")

    def test_build_summary_artifact_collects_phase3_core_sim_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifacts_root = tmp_path / "artifacts"
            reports_root = artifacts_root / "reports"
            batch_root = artifacts_root / "batch_core"
            reports_root.mkdir(parents=True, exist_ok=True)
            batch_root.mkdir(parents=True, exist_ok=True)

            summary_path = reports_root / "REL_PHASE3_CORE_001_sds_v1.summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_CORE_001_sds_v1",
                        "sds_version": "sds_v1",
                        "final_result": "PASS",
                        "generated_at": "2026-03-02T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            pipeline_manifest_path = batch_root / "pipeline_result.json"
            pipeline_manifest_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_CORE_001_sds_v1",
                        "batch_id": "BATCH_CORE_001",
                        "overall_result": "HOLD",
                        "strict_gate": True,
                        "trend_gate": {"result": "PASS"},
                        "reports": [{"sds_version": "sds_v1"}],
                        "phase3_hooks": {
                            "enabled": True,
                            "phase3_core_sim": {
                                "enabled": True,
                                "status": "success",
                                "termination_reason": "completed",
                                "collision": False,
                                "timeout": False,
                                "min_ttc_same_lane_sec": 2.4,
                                "min_ttc_adjacent_lane_sec": 2.1,
                                "min_ttc_any_lane_sec": 2.1,
                                "enable_ego_collision_avoidance": True,
                                "avoidance_ttc_threshold_sec": 2.5,
                                "ego_max_brake_mps2": 8.0,
                                "tire_friction_coeff": 0.9,
                                "surface_friction_scale": 1.0,
                                "ego_avoidance_brake_event_count": 1,
                                "ego_avoidance_applied_brake_mps2_max": 5.2,
                            },
                        },
                        "functional_quality_gates": {
                            "phase3_core_sim_gate": {
                                "result": "PASS",
                                "reasons": ["phase3 core-sim safety checks satisfied"],
                                "details": {
                                    "require_success": True,
                                    "min_ttc_same_lane_sec": 2.0,
                                    "min_ttc_any_lane_sec": 1.5,
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
                "REL_PHASE3_CORE_001",
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
            self.assertEqual(bool(row.get("phase3_core_sim_enabled", False)), True)
            self.assertEqual(str(row.get("phase3_core_sim_status", "")), "success")
            self.assertEqual(bool(row.get("phase3_core_sim_collision", False)), False)
            self.assertAlmostEqual(float(row.get("phase3_core_sim_min_ttc_same_lane_sec", 0.0) or 0.0), 2.4, places=6)
            self.assertAlmostEqual(float(row.get("phase3_core_sim_min_ttc_any_lane_sec", 0.0) or 0.0), 2.1, places=6)
            self.assertEqual(str(row.get("phase3_core_sim_gate_result", "")), "pass")
            self.assertEqual(int(row.get("phase3_core_sim_gate_reason_count", 0) or 0), 1)
            self.assertEqual(bool(row.get("phase3_core_sim_gate_require_success", False)), True)
            self.assertAlmostEqual(float(row.get("phase3_core_sim_gate_min_ttc_same_lane_sec", 0.0) or 0.0), 2.0, places=6)
            phase3_core_summary = payload.get("phase3_core_sim_summary", {})
            self.assertEqual(int(phase3_core_summary.get("evaluated_manifest_count", 0) or 0), 1)
            self.assertEqual(phase3_core_summary.get("status_counts"), {"success": 1})
            self.assertEqual(phase3_core_summary.get("gate_result_counts"), {"pass": 1})
            self.assertIn("phase3_core_sim=evaluated:1", out_text.read_text(encoding="utf-8"))

    def test_markdown_renderer_renders_phase3_core_sim_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            summary_json.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_CORE_MD_001",
                        "summary_count": 1,
                        "sds_versions": ["sds_v1"],
                        "final_result_counts": {"PASS": 1},
                        "pipeline_manifest_count": 1,
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "pipeline_manifests": [],
                        "timing_ms": {"total": 100},
                        "phase3_core_sim_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "status_counts": {"success": 1},
                            "gate_result_counts": {"pass": 1},
                            "gate_reason_count_total": 0,
                            "gate_require_success_enabled_count": 1,
                            "success_manifest_count": 1,
                            "collision_manifest_count": 0,
                            "timeout_manifest_count": 0,
                            "min_ttc_same_lane_sec": 2.4,
                            "lowest_same_lane_batch_id": "BATCH_CORE_001",
                            "min_ttc_any_lane_sec": 2.1,
                            "lowest_any_lane_batch_id": "BATCH_CORE_001",
                            "avoidance_enabled_manifest_count": 1,
                            "ego_avoidance_brake_event_count_total": 1,
                            "max_ego_avoidance_applied_brake_mps2": 5.2,
                            "highest_ego_avoidance_applied_brake_batch_id": "BATCH_CORE_001",
                            "avg_tire_friction_coeff": 0.9,
                            "avg_surface_friction_scale": 1.0,
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
                "- phase3_core_sim: `evaluated=1, statuses=success:1, gate_results=pass:1,",
                proc.stdout,
            )
            self.assertIn("min_ttc_same_lane=2.400 (BATCH_CORE_001)", proc.stdout)

    def test_notification_includes_phase3_core_sim_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_CORE_NOTIFY_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_core_sim_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "status_counts": {"success": 1},
                            "gate_result_counts": {"pass": 1},
                            "gate_reason_count_total": 0,
                            "gate_require_success_enabled_count": 1,
                            "success_manifest_count": 1,
                            "collision_manifest_count": 0,
                            "timeout_manifest_count": 0,
                            "min_ttc_same_lane_sec": 2.4,
                            "lowest_same_lane_batch_id": "BATCH_CORE_001",
                            "min_ttc_any_lane_sec": 2.1,
                            "lowest_any_lane_batch_id": "BATCH_CORE_001",
                            "avoidance_enabled_manifest_count": 1,
                            "ego_avoidance_brake_event_count_total": 1,
                            "max_ego_avoidance_applied_brake_mps2": 5.2,
                            "highest_ego_avoidance_applied_brake_batch_id": "BATCH_CORE_001",
                            "avg_tire_friction_coeff": 0.9,
                            "avg_surface_friction_scale": 1.0,
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
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn(
                "phase3_core_sim_summary=evaluated=1,statuses=success:1,gate_results=pass:1,",
                payload.get("message_text", ""),
            )
            self.assertIn("*phase3 core sim*", json.dumps(payload.get("slack", {})))
            self.assertIn("phase3_core_sim_summary_text", payload)
            self.assertIsInstance(payload.get("phase3_core_sim_summary"), dict)

    def test_notification_holds_on_phase3_core_sim_collision_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_CORE_NOTIFY_COLLISION_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_core_sim_summary": {
                            "evaluated_manifest_count": 1,
                            "status_counts": {"success": 1},
                            "gate_result_counts": {"pass": 1},
                            "collision_manifest_count": 1,
                            "timeout_manifest_count": 0,
                            "gate_hold_manifest_count": 0,
                            "min_ttc_same_lane_sec": 2.4,
                            "lowest_same_lane_batch_id": "BATCH_CORE_001",
                            "min_ttc_any_lane_sec": 2.1,
                            "lowest_any_lane_batch_id": "BATCH_CORE_001",
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
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "HOLD")
            self.assertIn(
                "phase3_core_sim_warning=phase3_core_sim_collision_manifest_count=1 exceeded hold_max=0",
                payload.get("message_text", ""),
            )
            self.assertIn(
                "phase3_core_sim_collision_count_above_hold_max",
                payload.get("phase3_core_sim_warning_reasons", []),
            )

    def test_notification_warns_on_phase3_core_sim_ttc_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_CORE_NOTIFY_TTC_WARN_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_core_sim_summary": {
                            "evaluated_manifest_count": 1,
                            "status_counts": {"success": 1},
                            "gate_result_counts": {"pass": 1},
                            "collision_manifest_count": 0,
                            "timeout_manifest_count": 0,
                            "gate_hold_manifest_count": 0,
                            "min_ttc_same_lane_sec": 2.2,
                            "lowest_same_lane_batch_id": "BATCH_CORE_001",
                            "min_ttc_any_lane_sec": 2.1,
                            "lowest_any_lane_batch_id": "BATCH_CORE_001",
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
                "--phase3-core-sim-min-ttc-same-lane-warn-min",
                "2.5",
                "--phase3-core-sim-min-ttc-same-lane-hold-min",
                "2.0",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "WARN")
            self.assertIn(
                "phase3_core_sim_warning=phase3_core_sim_min_ttc_same_lane_sec=2.200 below warn_min=2.500",
                payload.get("message_text", ""),
            )
            self.assertIn(
                "phase3_core_sim_min_ttc_same_lane_below_warn_min",
                payload.get("phase3_core_sim_warning_reasons", []),
            )

    def test_notification_warns_on_phase3_core_sim_threshold_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_CORE_NOTIFY_MISMATCH_WARN_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_core_sim_summary": {
                            "evaluated_manifest_count": 1,
                            "status_counts": {"success": 1},
                            "gate_result_counts": {"pass": 1},
                            "collision_manifest_count": 0,
                            "timeout_manifest_count": 0,
                            "gate_hold_manifest_count": 0,
                            "min_ttc_same_lane_sec": 3.0,
                            "lowest_same_lane_batch_id": "BATCH_CORE_001",
                            "min_ttc_any_lane_sec": 3.0,
                            "lowest_any_lane_batch_id": "BATCH_CORE_001",
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
                "--phase3-core-sim-min-ttc-same-lane-warn-min",
                "2.5",
                "--phase3-core-sim-min-ttc-same-lane-hold-min",
                "2.0",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "WARN")
            self.assertEqual(str(payload.get("phase3_core_sim_threshold_drift_severity", "")), "WARN")
            self.assertIn(
                "phase3_core_sim_min_ttc_same_lane_warn_min_mismatch",
                payload.get("phase3_core_sim_warning_reasons", []),
            )
            self.assertIn(
                "min_ttc_same_lane_warn_min=expected:2.500,observed:2.0:1",
                str(payload.get("phase3_core_sim_threshold_drift_summary_text", "")),
            )

    def test_notification_holds_on_phase3_core_sim_threshold_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_CORE_NOTIFY_MISMATCH_HOLD_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_core_sim_summary": {
                            "evaluated_manifest_count": 1,
                            "status_counts": {"success": 1},
                            "gate_result_counts": {"pass": 1},
                            "collision_manifest_count": 0,
                            "timeout_manifest_count": 0,
                            "gate_hold_manifest_count": 0,
                            "min_ttc_same_lane_sec": 3.0,
                            "lowest_same_lane_batch_id": "BATCH_CORE_001",
                            "min_ttc_any_lane_sec": 3.0,
                            "lowest_any_lane_batch_id": "BATCH_CORE_001",
                            "gate_min_ttc_any_lane_sec_counts": {"2.0": 1},
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
                "--phase3-core-sim-min-ttc-any-lane-warn-min",
                "2.0",
                "--phase3-core-sim-min-ttc-any-lane-hold-min",
                "2.5",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "HOLD")
            self.assertEqual(str(payload.get("phase3_core_sim_threshold_drift_severity", "")), "HOLD")
            self.assertIn(
                "phase3_core_sim_min_ttc_any_lane_hold_min_mismatch",
                payload.get("phase3_core_sim_warning_reasons", []),
            )
            self.assertIn(
                "min_ttc_any_lane_hold_min=expected:2.500,observed:2.0:1",
                str(payload.get("phase3_core_sim_threshold_drift_summary_text", "")),
            )


if __name__ == "__main__":
    unittest.main()
