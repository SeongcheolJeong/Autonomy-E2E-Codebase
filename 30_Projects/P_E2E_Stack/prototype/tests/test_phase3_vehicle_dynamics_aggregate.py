from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_release_summary_artifact import summarize_phase3_vehicle_dynamics


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


class Phase3VehicleDynamicsAggregateTests(unittest.TestCase):
    def test_phase3_vehicle_dynamics_summary_aggregates_manifests(self) -> None:
        summary = summarize_phase3_vehicle_dynamics(
            [
                {
                    "batch_id": "BATCH_002",
                    "phase3_vehicle_dynamics_model": "longitudinal_force_balance_v1",
                    "phase3_vehicle_dynamics_step_count": 3,
                    "phase3_vehicle_dynamics_initial_speed_mps": 8.0,
                    "phase3_vehicle_dynamics_initial_position_m": 14.0,
                    "phase3_vehicle_dynamics_initial_heading_deg": 0.0,
                    "phase3_vehicle_dynamics_initial_lateral_position_m": 0.0,
                    "phase3_vehicle_dynamics_initial_lateral_velocity_mps": 0.2,
                    "phase3_vehicle_dynamics_initial_yaw_rate_rps": 0.05,
                    "phase3_vehicle_dynamics_final_speed_mps": 9.0,
                    "phase3_vehicle_dynamics_final_position_m": 15.0,
                    "phase3_vehicle_dynamics_final_heading_deg": 2.0,
                    "phase3_vehicle_dynamics_final_lateral_position_m": 0.4,
                    "phase3_vehicle_dynamics_final_lateral_velocity_mps": 0.5,
                    "phase3_vehicle_dynamics_final_yaw_rate_rps": 0.2,
                    "phase3_vehicle_dynamics_min_heading_deg": -0.2,
                    "phase3_vehicle_dynamics_avg_heading_deg": 0.9,
                    "phase3_vehicle_dynamics_max_heading_deg": 2.0,
                    "phase3_vehicle_dynamics_min_lateral_position_m": 0.0,
                    "phase3_vehicle_dynamics_avg_lateral_position_m": 0.2,
                    "phase3_vehicle_dynamics_max_lateral_position_m": 0.4,
                    "phase3_vehicle_dynamics_max_abs_lateral_position_m": 0.4,
                    "phase3_vehicle_dynamics_max_abs_yaw_rate_rps": 0.3,
                    "phase3_vehicle_dynamics_max_abs_lateral_velocity_mps": 0.6,
                    "phase3_vehicle_dynamics_max_abs_accel_mps2": 2.4,
                    "phase3_vehicle_dynamics_max_abs_lateral_accel_mps2": 1.5,
                    "phase3_vehicle_dynamics_max_abs_yaw_accel_rps2": 0.8,
                    "phase3_vehicle_dynamics_max_abs_jerk_mps3": 4.2,
                    "phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3": 3.1,
                    "phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3": 1.2,
                    "phase3_vehicle_dynamics_planar_kinematics_enabled": True,
                    "phase3_vehicle_dynamics_dynamic_bicycle_enabled": True,
                    "phase3_vehicle_dynamics_min_road_grade_percent": -2.0,
                    "phase3_vehicle_dynamics_avg_road_grade_percent": -0.5,
                    "phase3_vehicle_dynamics_max_road_grade_percent": 1.0,
                    "phase3_vehicle_dynamics_max_abs_grade_force_n": 400.0,
                    "phase3_vehicle_control_command_step_count": 3,
                    "phase3_vehicle_control_throttle_brake_overlap_step_count": 0,
                    "phase3_vehicle_control_throttle_brake_overlap_ratio": 0.0,
                    "phase3_vehicle_control_max_abs_steering_rate_degps": 12.0,
                    "phase3_vehicle_control_max_abs_throttle_rate_per_sec": 1.2,
                    "phase3_vehicle_control_max_abs_brake_rate_per_sec": 0.3,
                    "phase3_vehicle_control_max_throttle_plus_brake": 0.5,
                    "phase3_vehicle_speed_tracking_target_step_count": 3,
                    "phase3_vehicle_speed_tracking_error_mps_min": -0.4,
                    "phase3_vehicle_speed_tracking_error_mps_avg": -0.1,
                    "phase3_vehicle_speed_tracking_error_mps_max": 0.2,
                    "phase3_vehicle_speed_tracking_error_abs_mps_avg": 0.2,
                    "phase3_vehicle_speed_tracking_error_abs_mps_max": 0.4,
                },
                {
                    "batch_id": "BATCH_001",
                    "phase3_vehicle_dynamics_model": "longitudinal_force_balance_v1",
                    "phase3_vehicle_dynamics_step_count": 5,
                    "phase3_vehicle_dynamics_initial_speed_mps": 7.0,
                    "phase3_vehicle_dynamics_initial_position_m": 18.0,
                    "phase3_vehicle_dynamics_initial_heading_deg": 1.0,
                    "phase3_vehicle_dynamics_initial_lateral_position_m": -0.2,
                    "phase3_vehicle_dynamics_initial_lateral_velocity_mps": -0.1,
                    "phase3_vehicle_dynamics_initial_yaw_rate_rps": -0.05,
                    "phase3_vehicle_dynamics_final_speed_mps": 11.0,
                    "phase3_vehicle_dynamics_final_position_m": 20.0,
                    "phase3_vehicle_dynamics_final_heading_deg": 7.0,
                    "phase3_vehicle_dynamics_final_lateral_position_m": 1.0,
                    "phase3_vehicle_dynamics_final_lateral_velocity_mps": 0.1,
                    "phase3_vehicle_dynamics_final_yaw_rate_rps": 0.3,
                    "phase3_vehicle_dynamics_min_heading_deg": 1.0,
                    "phase3_vehicle_dynamics_avg_heading_deg": 4.0,
                    "phase3_vehicle_dynamics_max_heading_deg": 7.0,
                    "phase3_vehicle_dynamics_min_lateral_position_m": -0.2,
                    "phase3_vehicle_dynamics_avg_lateral_position_m": 0.4,
                    "phase3_vehicle_dynamics_max_lateral_position_m": 1.0,
                    "phase3_vehicle_dynamics_max_abs_lateral_position_m": 1.0,
                    "phase3_vehicle_dynamics_max_abs_yaw_rate_rps": 0.7,
                    "phase3_vehicle_dynamics_max_abs_lateral_velocity_mps": 0.4,
                    "phase3_vehicle_dynamics_max_abs_accel_mps2": 2.1,
                    "phase3_vehicle_dynamics_max_abs_lateral_accel_mps2": 1.8,
                    "phase3_vehicle_dynamics_max_abs_yaw_accel_rps2": 0.6,
                    "phase3_vehicle_dynamics_max_abs_jerk_mps3": 3.9,
                    "phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3": 3.4,
                    "phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3": 1.0,
                    "phase3_vehicle_dynamics_planar_kinematics_enabled": True,
                    "phase3_vehicle_dynamics_dynamic_bicycle_enabled": False,
                    "phase3_vehicle_dynamics_min_road_grade_percent": 1.0,
                    "phase3_vehicle_dynamics_avg_road_grade_percent": 3.0,
                    "phase3_vehicle_dynamics_max_road_grade_percent": 5.0,
                    "phase3_vehicle_dynamics_max_abs_grade_force_n": 900.0,
                    "phase3_vehicle_control_command_step_count": 5,
                    "phase3_vehicle_control_throttle_brake_overlap_step_count": 1,
                    "phase3_vehicle_control_throttle_brake_overlap_ratio": 0.2,
                    "phase3_vehicle_control_max_abs_steering_rate_degps": 15.0,
                    "phase3_vehicle_control_max_abs_throttle_rate_per_sec": 0.8,
                    "phase3_vehicle_control_max_abs_brake_rate_per_sec": 0.4,
                    "phase3_vehicle_control_max_throttle_plus_brake": 0.7,
                    "phase3_vehicle_speed_tracking_target_step_count": 5,
                    "phase3_vehicle_speed_tracking_error_mps_min": -0.2,
                    "phase3_vehicle_speed_tracking_error_mps_avg": 0.3,
                    "phase3_vehicle_speed_tracking_error_mps_max": 0.8,
                    "phase3_vehicle_speed_tracking_error_abs_mps_avg": 0.35,
                    "phase3_vehicle_speed_tracking_error_abs_mps_max": 0.8,
                },
                {
                    "batch_id": "BATCH_003",
                    "phase3_vehicle_dynamics_model": "longitudinal_force_balance_v1",
                    "phase3_vehicle_dynamics_step_count": 0,
                    "phase3_vehicle_dynamics_initial_speed_mps": 20.0,
                    "phase3_vehicle_dynamics_initial_position_m": 20.0,
                    "phase3_vehicle_dynamics_initial_heading_deg": 0.0,
                    "phase3_vehicle_dynamics_initial_lateral_position_m": 0.0,
                    "phase3_vehicle_dynamics_final_speed_mps": 99.0,
                    "phase3_vehicle_dynamics_final_position_m": 999.0,
                    "phase3_vehicle_dynamics_final_heading_deg": 99.0,
                    "phase3_vehicle_dynamics_final_lateral_position_m": 99.0,
                    "phase3_vehicle_dynamics_max_abs_lateral_position_m": 99.0,
                    "phase3_vehicle_dynamics_max_abs_yaw_rate_rps": 99.0,
                    "phase3_vehicle_dynamics_planar_kinematics_enabled": True,
                    "phase3_vehicle_dynamics_min_road_grade_percent": -20.0,
                    "phase3_vehicle_dynamics_avg_road_grade_percent": 0.0,
                    "phase3_vehicle_dynamics_max_road_grade_percent": 20.0,
                    "phase3_vehicle_dynamics_max_abs_grade_force_n": 9999.0,
                },
            ]
        )
        self.assertEqual(summary.get("pipeline_manifest_count"), 3)
        self.assertEqual(summary.get("evaluated_manifest_count"), 2)
        self.assertEqual(summary.get("models"), ["longitudinal_force_balance_v1"])
        self.assertAlmostEqual(float(summary.get("min_final_speed_mps", 0.0)), 9.0)
        self.assertAlmostEqual(float(summary.get("avg_final_speed_mps", 0.0)), 10.0)
        self.assertAlmostEqual(float(summary.get("max_final_speed_mps", 0.0)), 11.0)
        self.assertEqual(summary.get("lowest_speed_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_speed_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_final_position_m", 0.0)), 15.0)
        self.assertAlmostEqual(float(summary.get("avg_final_position_m", 0.0)), 17.5)
        self.assertAlmostEqual(float(summary.get("max_final_position_m", 0.0)), 20.0)
        self.assertAlmostEqual(float(summary.get("min_delta_speed_mps", 0.0)), 1.0)
        self.assertAlmostEqual(float(summary.get("avg_delta_speed_mps", 0.0)), 2.5)
        self.assertAlmostEqual(float(summary.get("max_delta_speed_mps", 0.0)), 4.0)
        self.assertEqual(summary.get("lowest_delta_speed_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_delta_speed_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_delta_position_m", 0.0)), 1.0)
        self.assertAlmostEqual(float(summary.get("avg_delta_position_m", 0.0)), 1.5)
        self.assertAlmostEqual(float(summary.get("max_delta_position_m", 0.0)), 2.0)
        self.assertEqual(summary.get("lowest_delta_position_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_delta_position_batch_id"), "BATCH_001")
        self.assertEqual(int(summary.get("planar_enabled_manifest_count", 0)), 2)
        self.assertEqual(int(summary.get("dynamic_enabled_manifest_count", 0)), 1)
        self.assertAlmostEqual(float(summary.get("min_final_heading_deg", 0.0)), 2.0)
        self.assertAlmostEqual(float(summary.get("avg_final_heading_deg", 0.0)), 4.5)
        self.assertAlmostEqual(float(summary.get("max_final_heading_deg", 0.0)), 7.0)
        self.assertEqual(summary.get("lowest_heading_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_heading_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_final_lateral_position_m", 0.0)), 0.4)
        self.assertAlmostEqual(float(summary.get("avg_final_lateral_position_m", 0.0)), 0.7)
        self.assertAlmostEqual(float(summary.get("max_final_lateral_position_m", 0.0)), 1.0)
        self.assertEqual(summary.get("lowest_lateral_position_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_lateral_position_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_final_lateral_velocity_mps", 0.0)), 0.1)
        self.assertAlmostEqual(float(summary.get("avg_final_lateral_velocity_mps", 0.0)), 0.3)
        self.assertAlmostEqual(float(summary.get("max_final_lateral_velocity_mps", 0.0)), 0.5)
        self.assertEqual(summary.get("lowest_lateral_velocity_batch_id"), "BATCH_001")
        self.assertEqual(summary.get("highest_lateral_velocity_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("min_final_yaw_rate_rps", 0.0)), 0.2)
        self.assertAlmostEqual(float(summary.get("avg_final_yaw_rate_rps", 0.0)), 0.25)
        self.assertAlmostEqual(float(summary.get("max_final_yaw_rate_rps", 0.0)), 0.3)
        self.assertEqual(summary.get("lowest_yaw_rate_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_yaw_rate_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_delta_heading_deg", 0.0)), 2.0)
        self.assertAlmostEqual(float(summary.get("avg_delta_heading_deg", 0.0)), 4.0)
        self.assertAlmostEqual(float(summary.get("max_delta_heading_deg", 0.0)), 6.0)
        self.assertEqual(summary.get("lowest_delta_heading_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_delta_heading_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_delta_lateral_position_m", 0.0)), 0.4)
        self.assertAlmostEqual(float(summary.get("avg_delta_lateral_position_m", 0.0)), 0.8)
        self.assertAlmostEqual(float(summary.get("max_delta_lateral_position_m", 0.0)), 1.2)
        self.assertEqual(summary.get("lowest_delta_lateral_position_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_delta_lateral_position_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_delta_lateral_velocity_mps", 0.0)), 0.2)
        self.assertAlmostEqual(float(summary.get("avg_delta_lateral_velocity_mps", 0.0)), 0.25)
        self.assertAlmostEqual(float(summary.get("max_delta_lateral_velocity_mps", 0.0)), 0.3)
        self.assertEqual(summary.get("lowest_delta_lateral_velocity_batch_id"), "BATCH_001")
        self.assertEqual(summary.get("highest_delta_lateral_velocity_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("min_delta_yaw_rate_rps", 0.0)), 0.15)
        self.assertAlmostEqual(float(summary.get("avg_delta_yaw_rate_rps", 0.0)), 0.25)
        self.assertAlmostEqual(float(summary.get("max_delta_yaw_rate_rps", 0.0)), 0.35)
        self.assertEqual(summary.get("lowest_delta_yaw_rate_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_delta_yaw_rate_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("max_abs_yaw_rate_rps", 0.0)), 0.7)
        self.assertEqual(summary.get("highest_abs_yaw_rate_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("max_abs_lateral_velocity_mps", 0.0)), 0.6)
        self.assertEqual(summary.get("highest_abs_lateral_velocity_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("max_abs_accel_mps2", 0.0)), 2.4)
        self.assertEqual(summary.get("highest_abs_accel_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("max_abs_lateral_accel_mps2", 0.0)), 1.8)
        self.assertEqual(summary.get("highest_abs_lateral_accel_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("max_abs_yaw_accel_rps2", 0.0)), 0.8)
        self.assertEqual(summary.get("highest_abs_yaw_accel_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("max_abs_jerk_mps3", 0.0)), 4.2)
        self.assertEqual(summary.get("highest_abs_jerk_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("max_abs_lateral_jerk_mps3", 0.0)), 3.4)
        self.assertEqual(summary.get("highest_abs_lateral_jerk_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("max_abs_yaw_jerk_rps3", 0.0)), 1.2)
        self.assertEqual(summary.get("highest_abs_yaw_jerk_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("max_abs_lateral_position_m", 0.0)), 1.0)
        self.assertEqual(summary.get("highest_abs_lateral_position_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("min_road_grade_percent", 0.0)), -2.0)
        self.assertAlmostEqual(float(summary.get("avg_road_grade_percent", 0.0)), 1.25)
        self.assertAlmostEqual(float(summary.get("max_road_grade_percent", 0.0)), 5.0)
        self.assertEqual(summary.get("lowest_road_grade_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_road_grade_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("max_abs_grade_force_n", 0.0)), 900.0)
        self.assertEqual(summary.get("highest_abs_grade_force_batch_id"), "BATCH_001")
        self.assertEqual(int(summary.get("control_command_manifest_count", 0)), 2)
        self.assertEqual(int(summary.get("control_command_step_count_total", 0)), 8)
        self.assertEqual(int(summary.get("control_throttle_brake_overlap_step_count_total", 0)), 1)
        self.assertAlmostEqual(float(summary.get("control_throttle_brake_overlap_ratio_avg", 0.0)), 0.125)
        self.assertAlmostEqual(float(summary.get("control_throttle_brake_overlap_ratio_max", 0.0)), 0.2)
        self.assertEqual(summary.get("highest_control_overlap_ratio_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("control_max_abs_steering_rate_degps_avg", 0.0)), 13.5)
        self.assertAlmostEqual(float(summary.get("control_max_abs_steering_rate_degps_max", 0.0)), 15.0)
        self.assertEqual(summary.get("highest_control_steering_rate_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("control_max_abs_throttle_rate_per_sec_avg", 0.0)), 1.0)
        self.assertAlmostEqual(float(summary.get("control_max_abs_throttle_rate_per_sec_max", 0.0)), 1.2)
        self.assertEqual(summary.get("highest_control_throttle_rate_batch_id"), "BATCH_002")
        self.assertAlmostEqual(float(summary.get("control_max_abs_brake_rate_per_sec_avg", 0.0)), 0.35)
        self.assertAlmostEqual(float(summary.get("control_max_abs_brake_rate_per_sec_max", 0.0)), 0.4)
        self.assertEqual(summary.get("highest_control_brake_rate_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("control_max_throttle_plus_brake_avg", 0.0)), 0.6)
        self.assertAlmostEqual(float(summary.get("control_max_throttle_plus_brake_max", 0.0)), 0.7)
        self.assertEqual(summary.get("highest_control_throttle_plus_brake_batch_id"), "BATCH_001")
        self.assertEqual(int(summary.get("speed_tracking_manifest_count", 0)), 2)
        self.assertEqual(int(summary.get("speed_tracking_target_step_count_total", 0)), 8)
        self.assertAlmostEqual(float(summary.get("min_speed_tracking_error_mps", 0.0)), -0.4)
        self.assertAlmostEqual(float(summary.get("avg_speed_tracking_error_mps", 0.0)), 0.1)
        self.assertAlmostEqual(float(summary.get("max_speed_tracking_error_mps", 0.0)), 0.8)
        self.assertEqual(summary.get("lowest_speed_tracking_error_batch_id"), "BATCH_002")
        self.assertEqual(summary.get("highest_speed_tracking_error_batch_id"), "BATCH_001")
        self.assertAlmostEqual(float(summary.get("avg_abs_speed_tracking_error_mps", 0.0)), 0.275)
        self.assertAlmostEqual(float(summary.get("max_abs_speed_tracking_error_mps", 0.0)), 0.8)
        self.assertEqual(summary.get("highest_abs_speed_tracking_error_batch_id"), "BATCH_001")

    def test_build_summary_artifact_extracts_phase3_vehicle_dynamics_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifacts_root = tmp_path / "artifacts"
            reports_root = artifacts_root / "reports"
            batch_root = artifacts_root / "batch_x"
            reports_root.mkdir(parents=True, exist_ok=True)
            batch_root.mkdir(parents=True, exist_ok=True)

            summary_path = reports_root / "REL_PHASE3_VEH_SUMMARY_001_sds_v1.summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_VEH_SUMMARY_001_sds_v1",
                        "sds_version": "sds_v1",
                        "final_result": "PASS",
                        "generated_at": "2026-02-28T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            pipeline_manifest_path = batch_root / "pipeline_result.json"
            pipeline_manifest_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_VEH_SUMMARY_001_sds_v1",
                        "batch_id": "BATCH_PHASE3_001",
                        "overall_result": "PASS",
                        "strict_gate": True,
                        "trend_gate": {"result": "PASS"},
                        "reports": [{"sds_version": "sds_v1"}],
                        "phase3_hooks": {
                            "enabled": True,
                            "vehicle_dynamics": {
                                "vehicle_dynamics_model": "longitudinal_force_balance_v1",
                                "step_count": 3,
                                "initial_speed_mps": 6.0,
                                "initial_position_m": 1.5,
                                "initial_heading_deg": 5.0,
                                "initial_lateral_position_m": -0.3,
                                "initial_lateral_velocity_mps": -0.1,
                                "initial_yaw_rate_rps": 0.05,
                                "final_speed_mps": 6.5,
                                "final_position_m": 5.4,
                                "final_heading_deg": 6.2,
                                "final_lateral_position_m": 0.4,
                                "final_lateral_velocity_mps": 0.2,
                                "final_yaw_rate_rps": 0.3,
                                "min_heading_deg": 5.0,
                                "avg_heading_deg": 5.8,
                                "max_heading_deg": 6.2,
                                "min_lateral_position_m": -0.3,
                                "avg_lateral_position_m": 0.1,
                                "max_lateral_position_m": 0.4,
                                "max_abs_lateral_position_m": 0.4,
                                "max_abs_yaw_rate_rps": 0.45,
                                "max_abs_lateral_velocity_mps": 0.25,
                                "max_abs_accel_mps2": 2.2,
                                "max_abs_lateral_accel_mps2": 1.1,
                                "max_abs_yaw_accel_rps2": 0.6,
                                "max_abs_jerk_mps3": 3.5,
                                "max_abs_lateral_jerk_mps3": 2.1,
                                "max_abs_yaw_jerk_rps3": 0.9,
                                "planar_kinematics_enabled": True,
                                "dynamic_bicycle_enabled": True,
                                "min_road_grade_percent": -1.0,
                                "avg_road_grade_percent": 0.5,
                                "max_road_grade_percent": 2.0,
                                "max_abs_grade_force_n": 123.4,
                                "control_command_step_count": 3,
                                "control_throttle_brake_overlap_step_count": 1,
                                "control_throttle_brake_overlap_ratio": 1.0 / 3.0,
                                "control_max_abs_steering_rate_degps": 12.5,
                                "control_max_abs_throttle_rate_per_sec": 1.5,
                                "control_max_abs_brake_rate_per_sec": 0.8,
                                "control_max_throttle_plus_brake": 0.9,
                                "speed_tracking_target_step_count": 3,
                                "speed_tracking_error_mps_min": -0.2,
                                "speed_tracking_error_mps_avg": 0.1,
                                "speed_tracking_error_mps_max": 0.4,
                                "speed_tracking_error_abs_mps_avg": 0.2,
                                "speed_tracking_error_abs_mps_max": 0.4,
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            out_text = tmp_path / "summary.txt"
            out_json = tmp_path / "summary.json"
            run_script(
                PROTOTYPE_DIR / "build_release_summary_artifact.py",
                "--artifacts-root",
                str(artifacts_root),
                "--release-prefix",
                "REL_PHASE3_VEH_SUMMARY_001",
                "--out-text",
                str(out_text),
                "--out-json",
                str(out_json),
                "--out-db",
                str(tmp_path / "summary.sqlite"),
            )

            payload = json.loads(out_json.read_text(encoding="utf-8"))
            row = payload.get("pipeline_manifests", [])[0]
            self.assertEqual(row.get("phase3_vehicle_dynamics_model"), "longitudinal_force_balance_v1")
            self.assertEqual(int(row.get("phase3_vehicle_dynamics_step_count", 0)), 3)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_initial_speed_mps", 0.0)), 6.0)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_final_speed_mps", 0.0)), 6.5)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_initial_heading_deg", 0.0)), 5.0)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_final_heading_deg", 0.0)), 6.2)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_initial_lateral_position_m", 0.0)), -0.3)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_final_lateral_position_m", 0.0)), 0.4)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_initial_lateral_velocity_mps", 0.0)), -0.1)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_final_lateral_velocity_mps", 0.0)), 0.2)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_initial_yaw_rate_rps", 0.0)), 0.05)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_final_yaw_rate_rps", 0.0)), 0.3)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_yaw_rate_rps", 0.0)), 0.45)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_lateral_velocity_mps", 0.0)), 0.25)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_accel_mps2", 0.0)), 2.2)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_lateral_accel_mps2", 0.0)), 1.1)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_yaw_accel_rps2", 0.0)), 0.6)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_jerk_mps3", 0.0)), 3.5)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3", 0.0)), 2.1)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3", 0.0)), 0.9)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_lateral_position_m", 0.0)), 0.4)
            self.assertTrue(bool(row.get("phase3_vehicle_dynamics_planar_kinematics_enabled", False)))
            self.assertTrue(bool(row.get("phase3_vehicle_dynamics_dynamic_bicycle_enabled", False)))
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_min_road_grade_percent", 0.0)), -1.0)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_avg_road_grade_percent", 0.0)), 0.5)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_road_grade_percent", 0.0)), 2.0)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_dynamics_max_abs_grade_force_n", 0.0)), 123.4)
            self.assertEqual(int(row.get("phase3_vehicle_control_command_step_count", 0) or 0), 3)
            self.assertEqual(int(row.get("phase3_vehicle_control_throttle_brake_overlap_step_count", 0) or 0), 1)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_control_throttle_brake_overlap_ratio", 0.0)), 1.0 / 3.0)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_control_max_abs_steering_rate_degps", 0.0)), 12.5)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_control_max_abs_throttle_rate_per_sec", 0.0)), 1.5)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_control_max_abs_brake_rate_per_sec", 0.0)), 0.8)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_control_max_throttle_plus_brake", 0.0)), 0.9)
            self.assertEqual(int(row.get("phase3_vehicle_speed_tracking_target_step_count", 0) or 0), 3)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_speed_tracking_error_mps_min", 0.0)), -0.2)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_speed_tracking_error_mps_avg", 0.0)), 0.1)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_speed_tracking_error_mps_max", 0.0)), 0.4)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_speed_tracking_error_abs_mps_avg", 0.0)), 0.2)
            self.assertAlmostEqual(float(row.get("phase3_vehicle_speed_tracking_error_abs_mps_max", 0.0)), 0.4)
            phase3_summary = payload.get("phase3_vehicle_dynamics_summary", {})
            self.assertEqual(int(phase3_summary.get("evaluated_manifest_count", 0)), 1)
            self.assertEqual(phase3_summary.get("models"), ["longitudinal_force_balance_v1"])
            self.assertEqual(int(phase3_summary.get("planar_enabled_manifest_count", 0)), 1)
            self.assertEqual(int(phase3_summary.get("dynamic_enabled_manifest_count", 0)), 1)
            self.assertAlmostEqual(float(phase3_summary.get("min_final_position_m", 0.0)), 5.4)
            self.assertAlmostEqual(float(phase3_summary.get("min_delta_speed_mps", 0.0)), 0.5)
            self.assertAlmostEqual(float(phase3_summary.get("min_delta_position_m", 0.0)), 3.9)
            self.assertAlmostEqual(float(phase3_summary.get("min_final_heading_deg", 0.0)), 6.2)
            self.assertAlmostEqual(float(phase3_summary.get("min_delta_heading_deg", 0.0)), 1.2)
            self.assertAlmostEqual(float(phase3_summary.get("min_final_lateral_position_m", 0.0)), 0.4)
            self.assertAlmostEqual(float(phase3_summary.get("min_delta_lateral_position_m", 0.0)), 0.7)
            self.assertAlmostEqual(float(phase3_summary.get("min_final_lateral_velocity_mps", 0.0)), 0.2)
            self.assertAlmostEqual(float(phase3_summary.get("min_final_yaw_rate_rps", 0.0)), 0.3)
            self.assertAlmostEqual(float(phase3_summary.get("min_delta_lateral_velocity_mps", 0.0)), 0.3)
            self.assertAlmostEqual(float(phase3_summary.get("min_delta_yaw_rate_rps", 0.0)), 0.25)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_yaw_rate_rps", 0.0)), 0.45)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_lateral_velocity_mps", 0.0)), 0.25)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_accel_mps2", 0.0)), 2.2)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_lateral_accel_mps2", 0.0)), 1.1)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_yaw_accel_rps2", 0.0)), 0.6)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_jerk_mps3", 0.0)), 3.5)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_lateral_jerk_mps3", 0.0)), 2.1)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_yaw_jerk_rps3", 0.0)), 0.9)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_lateral_position_m", 0.0)), 0.4)
            self.assertAlmostEqual(float(phase3_summary.get("min_road_grade_percent", 0.0)), -1.0)
            self.assertAlmostEqual(float(phase3_summary.get("avg_road_grade_percent", 0.0)), 0.5)
            self.assertAlmostEqual(float(phase3_summary.get("max_road_grade_percent", 0.0)), 2.0)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_grade_force_n", 0.0)), 123.4)
            self.assertEqual(int(phase3_summary.get("control_command_manifest_count", 0)), 1)
            self.assertEqual(int(phase3_summary.get("control_command_step_count_total", 0)), 3)
            self.assertEqual(int(phase3_summary.get("control_throttle_brake_overlap_step_count_total", 0)), 1)
            self.assertAlmostEqual(float(phase3_summary.get("control_throttle_brake_overlap_ratio_avg", 0.0)), 1.0 / 3.0)
            self.assertAlmostEqual(float(phase3_summary.get("control_throttle_brake_overlap_ratio_max", 0.0)), 1.0 / 3.0)
            self.assertAlmostEqual(float(phase3_summary.get("control_max_abs_steering_rate_degps_max", 0.0)), 12.5)
            self.assertAlmostEqual(float(phase3_summary.get("control_max_abs_throttle_rate_per_sec_max", 0.0)), 1.5)
            self.assertAlmostEqual(float(phase3_summary.get("control_max_abs_brake_rate_per_sec_max", 0.0)), 0.8)
            self.assertAlmostEqual(float(phase3_summary.get("control_max_throttle_plus_brake_max", 0.0)), 0.9)
            self.assertEqual(int(phase3_summary.get("speed_tracking_manifest_count", 0)), 1)
            self.assertEqual(int(phase3_summary.get("speed_tracking_target_step_count_total", 0)), 3)
            self.assertAlmostEqual(float(phase3_summary.get("min_speed_tracking_error_mps", 0.0)), -0.2)
            self.assertAlmostEqual(float(phase3_summary.get("avg_speed_tracking_error_mps", 0.0)), 0.1)
            self.assertAlmostEqual(float(phase3_summary.get("max_speed_tracking_error_mps", 0.0)), 0.4)
            self.assertAlmostEqual(float(phase3_summary.get("avg_abs_speed_tracking_error_mps", 0.0)), 0.2)
            self.assertAlmostEqual(float(phase3_summary.get("max_abs_speed_tracking_error_mps", 0.0)), 0.4)
            out_text_body = out_text.read_text(encoding="utf-8")
            self.assertIn("phase3_vehicle_dynamics=evaluated:1", out_text_body)
            self.assertIn("accel:max_abs=2.200(BATCH_PHASE3_001)", out_text_body)
            self.assertIn("lateral_jerk:max_abs=2.100(BATCH_PHASE3_001)", out_text_body)
            self.assertIn("control_input:manifests=1,steps=3", out_text_body)
            self.assertIn("speed_tracking:manifests=1,target_steps=3", out_text_body)

    def test_markdown_renderer_renders_phase3_vehicle_dynamics_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            summary_json.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_VEH_MARKDOWN_001",
                        "summary_count": 1,
                        "sds_versions": ["sds_v1"],
                        "final_result_counts": {"PASS": 1},
                        "pipeline_manifest_count": 1,
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "pipeline_manifests": [
                            {
                                "batch_id": "BATCH_PHASE3_001",
                                "overall_result": "PASS",
                                "trend_result": "PASS",
                                "strict_gate": True,
                                "phase3_vehicle_dynamics_step_count": 3,
                                "phase3_vehicle_dynamics_initial_speed_mps": 6.0,
                                "phase3_vehicle_dynamics_initial_position_m": 1.5,
                                "phase3_vehicle_dynamics_initial_heading_deg": 5.0,
                                "phase3_vehicle_dynamics_initial_lateral_position_m": -0.3,
                                "phase3_vehicle_dynamics_initial_lateral_velocity_mps": -0.1,
                                "phase3_vehicle_dynamics_initial_yaw_rate_rps": 0.05,
                                "phase3_vehicle_dynamics_final_speed_mps": 6.5,
                                "phase3_vehicle_dynamics_final_position_m": 5.4,
                                "phase3_vehicle_dynamics_final_heading_deg": 6.2,
                                "phase3_vehicle_dynamics_final_lateral_position_m": 0.4,
                                "phase3_vehicle_dynamics_final_lateral_velocity_mps": 0.2,
                                "phase3_vehicle_dynamics_final_yaw_rate_rps": 0.3,
                                "phase3_vehicle_dynamics_dynamic_bicycle_enabled": True,
                                "phase3_vehicle_control_command_step_count": 3,
                                "phase3_vehicle_control_throttle_brake_overlap_step_count": 1,
                                "phase3_vehicle_control_throttle_brake_overlap_ratio": 1.0 / 3.0,
                                "phase3_vehicle_control_max_abs_steering_rate_degps": 12.5,
                                "phase3_vehicle_control_max_throttle_plus_brake": 0.9,
                                "phase3_vehicle_speed_tracking_target_step_count": 3,
                                "phase3_vehicle_speed_tracking_error_abs_mps_max": 0.4,
                            }
                        ],
                        "timing_ms": {"total": 100},
                        "phase3_vehicle_dynamics_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "models": ["longitudinal_force_balance_v1"],
                            "planar_enabled_manifest_count": 1,
                            "dynamic_enabled_manifest_count": 1,
                            "min_final_speed_mps": 6.5,
                            "avg_final_speed_mps": 6.5,
                            "max_final_speed_mps": 6.5,
                            "lowest_speed_batch_id": "BATCH_PHASE3_001",
                            "highest_speed_batch_id": "BATCH_PHASE3_001",
                            "min_final_position_m": 5.4,
                            "avg_final_position_m": 5.4,
                            "max_final_position_m": 5.4,
                            "lowest_position_batch_id": "BATCH_PHASE3_001",
                            "highest_position_batch_id": "BATCH_PHASE3_001",
                            "min_delta_speed_mps": 0.5,
                            "avg_delta_speed_mps": 0.5,
                            "max_delta_speed_mps": 0.5,
                            "lowest_delta_speed_batch_id": "BATCH_PHASE3_001",
                            "highest_delta_speed_batch_id": "BATCH_PHASE3_001",
                            "min_delta_position_m": 3.9,
                            "avg_delta_position_m": 3.9,
                            "max_delta_position_m": 3.9,
                            "lowest_delta_position_batch_id": "BATCH_PHASE3_001",
                            "highest_delta_position_batch_id": "BATCH_PHASE3_001",
                            "min_final_heading_deg": 6.2,
                            "avg_final_heading_deg": 6.2,
                            "max_final_heading_deg": 6.2,
                            "lowest_heading_batch_id": "BATCH_PHASE3_001",
                            "highest_heading_batch_id": "BATCH_PHASE3_001",
                            "min_final_lateral_position_m": 0.4,
                            "avg_final_lateral_position_m": 0.4,
                            "max_final_lateral_position_m": 0.4,
                            "lowest_lateral_position_batch_id": "BATCH_PHASE3_001",
                            "highest_lateral_position_batch_id": "BATCH_PHASE3_001",
                            "min_final_lateral_velocity_mps": 0.2,
                            "avg_final_lateral_velocity_mps": 0.2,
                            "max_final_lateral_velocity_mps": 0.2,
                            "lowest_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                            "highest_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                            "min_final_yaw_rate_rps": 0.3,
                            "avg_final_yaw_rate_rps": 0.3,
                            "max_final_yaw_rate_rps": 0.3,
                            "lowest_yaw_rate_batch_id": "BATCH_PHASE3_001",
                            "highest_yaw_rate_batch_id": "BATCH_PHASE3_001",
                            "min_delta_heading_deg": 1.2,
                            "avg_delta_heading_deg": 1.2,
                            "max_delta_heading_deg": 1.2,
                            "lowest_delta_heading_batch_id": "BATCH_PHASE3_001",
                            "highest_delta_heading_batch_id": "BATCH_PHASE3_001",
                            "min_delta_lateral_position_m": 0.7,
                            "avg_delta_lateral_position_m": 0.7,
                            "max_delta_lateral_position_m": 0.7,
                            "lowest_delta_lateral_position_batch_id": "BATCH_PHASE3_001",
                            "highest_delta_lateral_position_batch_id": "BATCH_PHASE3_001",
                            "min_delta_lateral_velocity_mps": 0.3,
                            "avg_delta_lateral_velocity_mps": 0.3,
                            "max_delta_lateral_velocity_mps": 0.3,
                            "lowest_delta_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                            "highest_delta_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                            "min_delta_yaw_rate_rps": 0.25,
                            "avg_delta_yaw_rate_rps": 0.25,
                            "max_delta_yaw_rate_rps": 0.25,
                            "lowest_delta_yaw_rate_batch_id": "BATCH_PHASE3_001",
                            "highest_delta_yaw_rate_batch_id": "BATCH_PHASE3_001",
                            "max_abs_yaw_rate_rps": 0.45,
                            "highest_abs_yaw_rate_batch_id": "BATCH_PHASE3_001",
                            "max_abs_lateral_velocity_mps": 0.25,
                            "highest_abs_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                            "max_abs_accel_mps2": 2.2,
                            "highest_abs_accel_batch_id": "BATCH_PHASE3_001",
                            "max_abs_lateral_accel_mps2": 1.1,
                            "highest_abs_lateral_accel_batch_id": "BATCH_PHASE3_001",
                            "max_abs_yaw_accel_rps2": 0.6,
                            "highest_abs_yaw_accel_batch_id": "BATCH_PHASE3_001",
                            "max_abs_jerk_mps3": 3.5,
                            "highest_abs_jerk_batch_id": "BATCH_PHASE3_001",
                            "max_abs_lateral_jerk_mps3": 2.1,
                            "highest_abs_lateral_jerk_batch_id": "BATCH_PHASE3_001",
                            "max_abs_yaw_jerk_rps3": 0.9,
                            "highest_abs_yaw_jerk_batch_id": "BATCH_PHASE3_001",
                            "max_abs_lateral_position_m": 0.4,
                            "highest_abs_lateral_position_batch_id": "BATCH_PHASE3_001",
                            "min_road_grade_percent": -1.0,
                            "avg_road_grade_percent": 0.5,
                            "max_road_grade_percent": 2.0,
                            "lowest_road_grade_batch_id": "BATCH_PHASE3_001",
                            "highest_road_grade_batch_id": "BATCH_PHASE3_001",
                            "max_abs_grade_force_n": 123.4,
                            "highest_abs_grade_force_batch_id": "BATCH_PHASE3_001",
                            "control_command_manifest_count": 1,
                            "control_command_step_count_total": 3,
                            "control_throttle_brake_overlap_step_count_total": 1,
                            "control_throttle_brake_overlap_ratio_avg": 1.0 / 3.0,
                            "control_throttle_brake_overlap_ratio_max": 1.0 / 3.0,
                            "highest_control_overlap_ratio_batch_id": "BATCH_PHASE3_001",
                            "control_max_abs_steering_rate_degps_avg": 12.5,
                            "control_max_abs_steering_rate_degps_max": 12.5,
                            "highest_control_steering_rate_batch_id": "BATCH_PHASE3_001",
                            "control_max_abs_throttle_rate_per_sec_avg": 1.5,
                            "control_max_abs_throttle_rate_per_sec_max": 1.5,
                            "highest_control_throttle_rate_batch_id": "BATCH_PHASE3_001",
                            "control_max_abs_brake_rate_per_sec_avg": 0.8,
                            "control_max_abs_brake_rate_per_sec_max": 0.8,
                            "highest_control_brake_rate_batch_id": "BATCH_PHASE3_001",
                            "control_max_throttle_plus_brake_avg": 0.9,
                            "control_max_throttle_plus_brake_max": 0.9,
                            "highest_control_throttle_plus_brake_batch_id": "BATCH_PHASE3_001",
                            "speed_tracking_manifest_count": 1,
                            "speed_tracking_target_step_count_total": 3,
                            "min_speed_tracking_error_mps": -0.2,
                            "avg_speed_tracking_error_mps": 0.1,
                            "max_speed_tracking_error_mps": 0.4,
                            "lowest_speed_tracking_error_batch_id": "BATCH_PHASE3_001",
                            "highest_speed_tracking_error_batch_id": "BATCH_PHASE3_001",
                            "avg_abs_speed_tracking_error_mps": 0.2,
                            "max_abs_speed_tracking_error_mps": 0.4,
                            "highest_abs_speed_tracking_error_batch_id": "BATCH_PHASE3_001",
                        },
                        "phase4_secondary_coverage_summary": {
                            "evaluated_manifest_count": 0,
                            "pipeline_manifest_count": 1,
                            "min_coverage_ratio": None,
                            "avg_coverage_ratio": None,
                            "max_coverage_ratio": None,
                            "lowest_batch_id": "",
                            "highest_batch_id": "",
                            "module_coverage_summary": {},
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
                "- phase3_vehicle_dynamics: `evaluated=1, dynamic_enabled=1, models=longitudinal_force_balance_v1,",
                proc.stdout,
            )
            self.assertIn("accel=max_abs=2.200 (BATCH_PHASE3_001)", proc.stdout)
            self.assertIn("yaw_accel=max_abs=0.600 (BATCH_PHASE3_001)", proc.stdout)
            self.assertIn("jerk=max_abs=3.500 (BATCH_PHASE3_001)", proc.stdout)
            self.assertIn("yaw_jerk=max_abs=0.900 (BATCH_PHASE3_001)", proc.stdout)
            self.assertIn(
                "grade_force=max_abs=123.400 (BATCH_PHASE3_001), control_input=manifests:1,steps:3",
                proc.stdout,
            )
            self.assertIn("control_input=manifests:1,steps:3", proc.stdout)
            self.assertIn("speed_tracking=manifests:1,target_steps:3", proc.stdout)
            self.assertIn("phase3_control_steps=3", proc.stdout)
            self.assertIn("phase3_control_overlap_ratio=0.333", proc.stdout)
            self.assertIn("phase3_speed_tracking_abs_error_max=0.400", proc.stdout)
            self.assertIn(
                "BATCH_PHASE3_001:overall=PASS,trend=PASS,strict=True,phase3_steps=3,phase3_dynamic=True,phase3_final_speed=6.500,phase3_final_position=5.400,phase3_delta_speed=0.500,phase3_delta_position=3.900,phase3_final_heading=6.200,phase3_final_lateral_position=0.400,phase3_final_lateral_velocity=0.200,phase3_final_yaw_rate=0.300,phase3_delta_heading=1.200,phase3_delta_lateral_position=0.700,phase3_delta_lateral_velocity=0.300,phase3_delta_yaw_rate=0.250",
                proc.stdout,
            )


if __name__ == "__main__":
    unittest.main()
