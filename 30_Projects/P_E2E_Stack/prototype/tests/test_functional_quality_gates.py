from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from run_e2e_pipeline import (
    evaluate_phase2_route_quality_gate,
    evaluate_phase3_core_sim_gate,
    evaluate_phase3_core_sim_matrix_gate,
    evaluate_phase3_control_quality_gate,
    evaluate_phase3_dataset_traffic_gate,
    evaluate_phase3_lane_risk_gate,
)


PROTOTYPE_DIR = Path(__file__).resolve().parents[1]
RUN_CI_PIPELINE_SCRIPT = PROTOTYPE_DIR / "run_ci_pipeline.py"
PYTHON = sys.executable


def run_script(*args: str, expected_rc: int = 0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [PYTHON, str(RUN_CI_PIPELINE_SCRIPT), *args],
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


class FunctionalQualityGateLogicTests(unittest.TestCase):
    def test_phase2_route_quality_gate_passes_when_thresholds_satisfied(self) -> None:
        result, reasons, details = evaluate_phase2_route_quality_gate(
            phase2_enable_hooks=True,
            phase2_hooks={
                "map_route_status": "pass",
                "map_route_lane_count": 3,
                "map_route_total_length_m": 220.0,
            },
            require_status_pass=True,
            min_lane_count=2,
            min_total_length_m=100.0,
        )
        self.assertEqual(result, "PASS")
        self.assertEqual(reasons, ["phase2 map-route quality checks satisfied"])
        self.assertTrue(details.get("configured"))

    def test_phase2_route_quality_gate_holds_when_route_is_insufficient(self) -> None:
        result, reasons, details = evaluate_phase2_route_quality_gate(
            phase2_enable_hooks=True,
            phase2_hooks={
                "map_route_status": "fail",
                "map_route_lane_count": 1,
                "map_route_total_length_m": 25.0,
            },
            require_status_pass=True,
            min_lane_count=2,
            min_total_length_m=100.0,
        )
        self.assertEqual(result, "HOLD")
        self.assertGreaterEqual(len(reasons), 2)
        self.assertIn("phase2_map_route_status fail != pass", reasons)
        self.assertIn("phase2_map_route_lane_count 1 < min_lane_count 2", reasons)
        self.assertIn("phase2_map_route_total_length_m 25.000 < min_total_length_m 100.000", reasons)
        self.assertEqual(details.get("observed_lane_count"), 1)

    def test_phase3_control_quality_gate_holds_on_control_threshold_breach(self) -> None:
        result, reasons, details = evaluate_phase3_control_quality_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "vehicle_dynamics": {
                    "control_throttle_brake_overlap_ratio": 0.4,
                    "control_max_abs_steering_rate_degps": 180.0,
                    "control_max_throttle_plus_brake": 1.5,
                    "speed_tracking_error_abs_mps_max": 2.7,
                }
            },
            max_overlap_ratio=0.2,
            max_steering_rate_degps=120.0,
            max_throttle_plus_brake=1.2,
            max_speed_tracking_error_abs_mps=2.0,
        )
        self.assertEqual(result, "HOLD")
        self.assertGreaterEqual(len(reasons), 3)
        self.assertIn("phase3_control_overlap_ratio 0.400000 > max_overlap_ratio 0.200000", reasons)
        self.assertIn(
            "phase3_control_max_abs_steering_rate_degps 180.000000 > max_steering_rate_degps 120.000000",
            reasons,
        )
        self.assertIn(
            "phase3_speed_tracking_error_abs_mps_max 2.700000 > max_speed_tracking_error_abs_mps 2.000000",
            reasons,
        )
        self.assertEqual(details.get("observed_throttle_plus_brake"), 1.5)

    def test_phase3_dataset_traffic_gate_passes_when_diversity_thresholds_satisfied(self) -> None:
        result, reasons, details = evaluate_phase3_dataset_traffic_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "dataset_traffic_diversity": {
                    "run_summary_count": 4,
                    "traffic_profile_count": 3,
                    "traffic_actor_pattern_count": 2,
                    "traffic_npc_count_avg": 3.25,
                }
            },
            min_run_summary_count=3,
            min_traffic_profile_count=2,
            min_actor_pattern_count=2,
            min_avg_npc_count=2.5,
        )
        self.assertEqual(result, "PASS")
        self.assertEqual(reasons, ["phase3 dataset traffic diversity checks satisfied"])
        self.assertTrue(details.get("configured"))

    def test_phase3_dataset_traffic_gate_holds_when_diversity_is_insufficient(self) -> None:
        result, reasons, details = evaluate_phase3_dataset_traffic_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "dataset_traffic_diversity": {
                    "run_summary_count": 1,
                    "traffic_profile_count": 1,
                    "traffic_actor_pattern_count": 1,
                    "traffic_npc_count_avg": 1.0,
                }
            },
            min_run_summary_count=2,
            min_traffic_profile_count=2,
            min_actor_pattern_count=2,
            min_avg_npc_count=2.0,
        )
        self.assertEqual(result, "HOLD")
        self.assertIn("phase3_dataset_run_summary_count 1 < min_run_summary_count 2", reasons)
        self.assertIn("phase3_dataset_traffic_profile_count 1 < min_traffic_profile_count 2", reasons)
        self.assertIn("phase3_dataset_traffic_actor_pattern_count 1 < min_actor_pattern_count 2", reasons)
        self.assertIn("phase3_dataset_traffic_npc_count_avg 1.000000 < min_avg_npc_count 2.000000", reasons)
        self.assertEqual(details.get("observed_traffic_profile_count"), 1)

    def test_phase3_core_sim_gate_passes_when_requirements_are_satisfied(self) -> None:
        result, reasons, details = evaluate_phase3_core_sim_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "phase3_core_sim": {
                    "enabled": True,
                    "status": "success",
                    "collision": False,
                    "timeout": False,
                    "min_ttc_same_lane_sec": 6.5,
                    "min_ttc_any_lane_sec": 5.0,
                }
            },
            require_success=True,
            min_ttc_same_lane_sec=3.0,
            min_ttc_any_lane_sec=2.0,
        )
        self.assertEqual(result, "PASS")
        self.assertEqual(reasons, ["phase3 core-sim safety checks satisfied"])
        self.assertTrue(details.get("configured"))
        self.assertEqual(details.get("observed_status"), "success")

    def test_phase3_core_sim_gate_holds_on_failure_or_low_ttc(self) -> None:
        result, reasons, details = evaluate_phase3_core_sim_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "phase3_core_sim": {
                    "enabled": True,
                    "status": "failed",
                    "collision": True,
                    "timeout": False,
                    "min_ttc_same_lane_sec": 1.2,
                    "min_ttc_any_lane_sec": 1.2,
                }
            },
            require_success=True,
            min_ttc_same_lane_sec=2.0,
            min_ttc_any_lane_sec=1.5,
        )
        self.assertEqual(result, "HOLD")
        self.assertIn("phase3_core_sim_status failed != success", reasons)
        self.assertIn("phase3_core_sim_collision true", reasons)
        self.assertIn("phase3_core_sim_min_ttc_same_lane_sec 1.200000 < min_ttc_same_lane_sec 2.000000", reasons)
        self.assertIn("phase3_core_sim_min_ttc_any_lane_sec 1.200000 < min_ttc_any_lane_sec 1.500000", reasons)
        self.assertTrue(details.get("observed_collision"))

    def test_phase3_core_sim_matrix_gate_passes_when_requirements_are_satisfied(self) -> None:
        result, reasons, details = evaluate_phase3_core_sim_matrix_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "phase3_core_sim_matrix": {
                    "enabled": True,
                    "all_cases_success": True,
                    "failed_case_count": 0,
                    "collision_case_count": 0,
                    "timeout_case_count": 0,
                    "min_ttc_same_lane_sec_min": 2.8,
                    "min_ttc_any_lane_sec_min": 2.5,
                }
            },
            require_all_cases_success=True,
            min_ttc_same_lane_sec=2.5,
            min_ttc_any_lane_sec=2.0,
            max_failed_cases=1,
            max_collision_cases=1,
            max_timeout_cases=1,
        )
        self.assertEqual(result, "PASS")
        self.assertEqual(reasons, ["phase3 core-sim matrix safety checks satisfied"])
        self.assertTrue(details.get("configured"))
        self.assertTrue(bool(details.get("observed_all_cases_success")))

    def test_phase3_core_sim_matrix_gate_holds_on_threshold_breaches(self) -> None:
        result, reasons, details = evaluate_phase3_core_sim_matrix_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "phase3_core_sim_matrix": {
                    "enabled": True,
                    "all_cases_success": False,
                    "failed_case_count": 3,
                    "collision_case_count": 2,
                    "timeout_case_count": 2,
                    "min_ttc_same_lane_sec_min": 1.3,
                    "min_ttc_any_lane_sec_min": 1.2,
                }
            },
            require_all_cases_success=True,
            min_ttc_same_lane_sec=2.0,
            min_ttc_any_lane_sec=1.5,
            max_failed_cases=1,
            max_collision_cases=1,
            max_timeout_cases=1,
        )
        self.assertEqual(result, "HOLD")
        self.assertIn("phase3_core_sim_matrix_all_cases_success false != true", reasons)
        self.assertIn("phase3_core_sim_matrix_min_ttc_same_lane_sec 1.300000 < min_ttc_same_lane_sec 2.000000", reasons)
        self.assertIn("phase3_core_sim_matrix_min_ttc_any_lane_sec 1.200000 < min_ttc_any_lane_sec 1.500000", reasons)
        self.assertIn("phase3_core_sim_matrix_failed_cases 3 > max_failed_cases 1", reasons)
        self.assertIn("phase3_core_sim_matrix_collision_cases 2 > max_collision_cases 1", reasons)
        self.assertIn("phase3_core_sim_matrix_timeout_cases 2 > max_timeout_cases 1", reasons)
        self.assertEqual(int(details.get("observed_failed_cases", 0) or 0), 3)

    def test_phase3_lane_risk_gate_passes_when_requirements_are_satisfied(self) -> None:
        result, reasons, details = evaluate_phase3_lane_risk_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "lane_risk_summary": {
                    "lane_risk_summary_run_count": 4,
                    "min_ttc_same_lane_sec": 3.2,
                    "min_ttc_adjacent_lane_sec": 3.1,
                    "min_ttc_any_lane_sec": 3.0,
                    "ttc_under_3s_same_lane_total": 2,
                    "ttc_under_3s_adjacent_lane_total": 1,
                }
            },
            min_ttc_same_lane_sec=2.5,
            min_ttc_adjacent_lane_sec=2.5,
            min_ttc_any_lane_sec=2.0,
            max_ttc_under_3s_same_lane_total=3,
            max_ttc_under_3s_adjacent_lane_total=2,
            max_ttc_under_3s_any_lane_total=4,
        )
        self.assertEqual(result, "PASS")
        self.assertEqual(reasons, ["phase3 lane-risk safety checks satisfied"])
        self.assertTrue(details.get("configured"))
        self.assertEqual(details.get("observed_ttc_under_3s_any_lane_total"), 3)

    def test_phase3_lane_risk_gate_holds_when_requirements_are_breached(self) -> None:
        result, reasons, details = evaluate_phase3_lane_risk_gate(
            phase3_enable_hooks=True,
            phase3_hooks={
                "lane_risk_summary": {
                    "lane_risk_summary_run_count": 3,
                    "min_ttc_same_lane_sec": 1.8,
                    "min_ttc_adjacent_lane_sec": 2.3,
                    "min_ttc_any_lane_sec": 1.6,
                    "ttc_under_3s_same_lane_total": 5,
                    "ttc_under_3s_adjacent_lane_total": 4,
                }
            },
            min_ttc_same_lane_sec=2.0,
            min_ttc_adjacent_lane_sec=2.5,
            min_ttc_any_lane_sec=2.0,
            max_ttc_under_3s_same_lane_total=3,
            max_ttc_under_3s_adjacent_lane_total=2,
            max_ttc_under_3s_any_lane_total=6,
        )
        self.assertEqual(result, "HOLD")
        self.assertIn("phase3_lane_risk_min_ttc_same_lane_sec 1.800000 < min_ttc_same_lane_sec 2.000000", reasons)
        self.assertIn(
            "phase3_lane_risk_min_ttc_adjacent_lane_sec 2.300000 < min_ttc_adjacent_lane_sec 2.500000",
            reasons,
        )
        self.assertIn("phase3_lane_risk_min_ttc_any_lane_sec 1.600000 < min_ttc_any_lane_sec 2.000000", reasons)
        self.assertIn(
            "phase3_lane_risk_ttc_under_3s_same_lane_total 5 > max_ttc_under_3s_same_lane_total 3",
            reasons,
        )
        self.assertIn(
            "phase3_lane_risk_ttc_under_3s_adjacent_lane_total 4 > max_ttc_under_3s_adjacent_lane_total 2",
            reasons,
        )
        self.assertIn(
            "phase3_lane_risk_ttc_under_3s_any_lane_total 9 > max_ttc_under_3s_any_lane_total 6",
            reasons,
        )
        self.assertEqual(details.get("observed_ttc_under_3s_any_lane_total"), 9)


class FunctionalQualityGateForwardingTests(unittest.TestCase):
    def test_run_ci_pipeline_dry_run_forwards_functional_quality_gate_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch.json"
            batch_spec.write_text(
                json.dumps({"batch_id": "BATCH_TEST", "runs": []}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            proc = run_script(
                "--batch-spec",
                str(batch_spec),
                "--release-id",
                "REL_TEST_FUNCTIONAL_GATE",
                "--default-sds-versions",
                "sds_v0.1.0",
                "--phase2-enable-hooks-input",
                "true",
                "--phase2-route-gate-require-status-pass-input",
                "true",
                "--phase2-route-gate-min-lane-count",
                "2",
                "--phase2-route-gate-min-total-length-m",
                "100",
                "--phase3-enable-hooks-input",
                "true",
                "--phase3-control-gate-max-overlap-ratio",
                "0.2",
                "--phase3-control-gate-max-steering-rate-degps",
                "120",
                "--phase3-control-gate-max-throttle-plus-brake",
                "1.2",
                "--phase3-control-gate-max-speed-tracking-error-abs-mps",
                "2.0",
                "--phase3-dataset-gate-min-run-summary-count",
                "3",
                "--phase3-dataset-gate-min-traffic-profile-count",
                "2",
                "--phase3-dataset-gate-min-actor-pattern-count",
                "2",
                "--phase3-dataset-gate-min-avg-npc-count",
                "2.5",
                "--phase3-lane-risk-gate-min-ttc-same-lane-sec",
                "2.5",
                "--phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
                "2.5",
                "--phase3-lane-risk-gate-min-ttc-any-lane-sec",
                "2.0",
                "--phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
                "3",
                "--phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
                "2",
                "--phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total",
                "4",
                "--phase3-core-sim-gate-require-success-input",
                "true",
                "--phase3-core-sim-gate-min-ttc-same-lane-sec",
                "2.0",
                "--phase3-core-sim-gate-min-ttc-any-lane-sec",
                "1.5",
                "--phase3-core-sim-matrix-gate-require-all-cases-success-input",
                "true",
                "--phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
                "2.0",
                "--phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
                "1.5",
                "--phase3-core-sim-matrix-gate-max-failed-cases",
                "1",
                "--phase3-core-sim-matrix-gate-max-collision-cases",
                "1",
                "--phase3-core-sim-matrix-gate-max-timeout-cases",
                "1",
                "--dry-run",
            )
            self.assertIn("--phase2-route-gate-require-status-pass", proc.stdout)
            self.assertIn("--phase2-route-gate-min-lane-count 2", proc.stdout)
            self.assertIn("--phase2-route-gate-min-total-length-m 100.0", proc.stdout)
            self.assertIn("--phase3-control-gate-max-overlap-ratio 0.2", proc.stdout)
            self.assertIn("--phase3-control-gate-max-steering-rate-degps 120.0", proc.stdout)
            self.assertIn("--phase3-control-gate-max-throttle-plus-brake 1.2", proc.stdout)
            self.assertIn("--phase3-control-gate-max-speed-tracking-error-abs-mps 2.0", proc.stdout)
            self.assertIn("--phase3-dataset-gate-min-run-summary-count 3", proc.stdout)
            self.assertIn("--phase3-dataset-gate-min-traffic-profile-count 2", proc.stdout)
            self.assertIn("--phase3-dataset-gate-min-actor-pattern-count 2", proc.stdout)
            self.assertIn("--phase3-dataset-gate-min-avg-npc-count 2.5", proc.stdout)
            self.assertIn("--phase3-lane-risk-gate-min-ttc-same-lane-sec 2.5", proc.stdout)
            self.assertIn("--phase3-lane-risk-gate-min-ttc-adjacent-lane-sec 2.5", proc.stdout)
            self.assertIn("--phase3-lane-risk-gate-min-ttc-any-lane-sec 2.0", proc.stdout)
            self.assertIn("--phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total 3", proc.stdout)
            self.assertIn("--phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total 2", proc.stdout)
            self.assertIn("--phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total 4", proc.stdout)
            self.assertIn("--phase3-core-sim-gate-require-success", proc.stdout)
            self.assertIn("--phase3-core-sim-gate-min-ttc-same-lane-sec 2.0", proc.stdout)
            self.assertIn("--phase3-core-sim-gate-min-ttc-any-lane-sec 1.5", proc.stdout)
            self.assertIn("--phase3-core-sim-matrix-gate-require-all-cases-success", proc.stdout)
            self.assertIn("--phase3-core-sim-matrix-gate-min-ttc-same-lane-sec 2.0", proc.stdout)
            self.assertIn("--phase3-core-sim-matrix-gate-min-ttc-any-lane-sec 1.5", proc.stdout)
            self.assertIn("--phase3-core-sim-matrix-gate-max-failed-cases 1", proc.stdout)
            self.assertIn("--phase3-core-sim-matrix-gate-max-collision-cases 1", proc.stdout)
            self.assertIn("--phase3-core-sim-matrix-gate-max-timeout-cases 1", proc.stdout)

    def test_run_ci_pipeline_rejects_phase3_control_gate_without_phase3_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch.json"
            batch_spec.write_text(
                json.dumps({"batch_id": "BATCH_TEST", "runs": []}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    PYTHON,
                    str(RUN_CI_PIPELINE_SCRIPT),
                    "--batch-spec",
                    str(batch_spec),
                    "--release-id",
                    "REL_TEST_FUNCTIONAL_GATE_FAIL",
                    "--default-sds-versions",
                    "sds_v0.1.0",
                    "--phase3-control-gate-max-overlap-ratio",
                    "0.2",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(
                "phase3-control-gate-*/phase3-dataset-gate-* inputs require phase3-enable-hooks-input=true",
                proc.stdout + proc.stderr,
            )

    def test_run_ci_pipeline_rejects_phase3_lane_risk_gate_without_phase3_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch.json"
            batch_spec.write_text(
                json.dumps({"batch_id": "BATCH_TEST", "runs": []}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    PYTHON,
                    str(RUN_CI_PIPELINE_SCRIPT),
                    "--batch-spec",
                    str(batch_spec),
                    "--release-id",
                    "REL_TEST_FUNCTIONAL_GATE_FAIL_LANE_RISK",
                    "--default-sds-versions",
                    "sds_v0.1.0",
                    "--phase3-lane-risk-gate-min-ttc-any-lane-sec",
                    "2.0",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(
                "phase3-control-gate-*/phase3-dataset-gate-* inputs require phase3-enable-hooks-input=true",
                proc.stdout + proc.stderr,
            )

    def test_run_ci_pipeline_rejects_phase3_dataset_gate_without_phase3_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch.json"
            batch_spec.write_text(
                json.dumps({"batch_id": "BATCH_TEST", "runs": []}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    PYTHON,
                    str(RUN_CI_PIPELINE_SCRIPT),
                    "--batch-spec",
                    str(batch_spec),
                    "--release-id",
                    "REL_TEST_FUNCTIONAL_GATE_FAIL_DATASET",
                    "--default-sds-versions",
                    "sds_v0.1.0",
                    "--phase3-dataset-gate-min-traffic-profile-count",
                    "2",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(
                "phase3-control-gate-*/phase3-dataset-gate-* inputs require phase3-enable-hooks-input=true",
                proc.stdout + proc.stderr,
            )

    def test_run_ci_pipeline_rejects_phase3_core_sim_gate_without_phase3_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch.json"
            batch_spec.write_text(
                json.dumps({"batch_id": "BATCH_TEST", "runs": []}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    PYTHON,
                    str(RUN_CI_PIPELINE_SCRIPT),
                    "--batch-spec",
                    str(batch_spec),
                    "--release-id",
                    "REL_TEST_FUNCTIONAL_GATE_FAIL_CORESIM",
                    "--default-sds-versions",
                    "sds_v0.1.0",
                    "--phase3-core-sim-gate-require-success-input",
                    "true",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(
                "phase3-control-gate-*/phase3-dataset-gate-* inputs require phase3-enable-hooks-input=true",
                proc.stdout + proc.stderr,
            )

    def test_run_ci_pipeline_rejects_phase3_core_sim_matrix_gate_without_phase3_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch.json"
            batch_spec.write_text(
                json.dumps({"batch_id": "BATCH_TEST", "runs": []}, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    PYTHON,
                    str(RUN_CI_PIPELINE_SCRIPT),
                    "--batch-spec",
                    str(batch_spec),
                    "--release-id",
                    "REL_TEST_FUNCTIONAL_GATE_FAIL_CORE_MATRIX",
                    "--default-sds-versions",
                    "sds_v0.1.0",
                    "--phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
                    "1.5",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(
                "phase3-control-gate-*/phase3-dataset-gate-* inputs require phase3-enable-hooks-input=true",
                proc.stdout + proc.stderr,
            )


if __name__ == "__main__":
    unittest.main()
