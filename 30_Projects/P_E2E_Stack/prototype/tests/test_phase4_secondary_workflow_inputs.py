from __future__ import annotations

import unittest
from pathlib import Path


PROTOTYPE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROTOTYPE_DIR.parents[2]


class Phase4SecondaryWorkflowInputTests(unittest.TestCase):
    def test_pr_quick_workflow_exposes_phase4_secondary_notification_inputs(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "e2e-pr-quick.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        self.assertIn("notify_phase4_primary_warn_ratio:", workflow_text)
        self.assertIn("notify_phase4_primary_hold_ratio:", workflow_text)
        self.assertIn("notify_phase4_primary_module_warn_thresholds:", workflow_text)
        self.assertIn("notify_phase4_primary_module_hold_thresholds:", workflow_text)
        self.assertIn("notify_phase4_secondary_warn_ratio:", workflow_text)
        self.assertIn("notify_phase4_secondary_hold_ratio:", workflow_text)
        self.assertIn("notify_phase4_secondary_warn_min_modules:", workflow_text)
        self.assertIn("notify_phase4_secondary_module_warn_thresholds:", workflow_text)
        self.assertIn("notify_phase4_secondary_module_hold_thresholds:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_speed_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_speed_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_position_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_position_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_speed_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_speed_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_position_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_position_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_heading_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_heading_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_lateral_position_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_lateral_position_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_heading_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_heading_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_lateral_position_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_lateral_position_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_rate_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_rate_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_yaw_rate_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_yaw_rate_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_velocity_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_velocity_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_accel_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_accel_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_accel_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_accel_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_accel_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_accel_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_jerk_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_jerk_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_jerk_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_jerk_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_jerk_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_jerk_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_position_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_position_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_road_grade_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_road_grade_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_grade_force_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_grade_force_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_overlap_ratio_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_overlap_ratio_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_steering_rate_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_steering_rate_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_throttle_plus_brake_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_throttle_plus_brake_hold_max:", workflow_text)
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_WARN_RATIO: ${{ github.event.inputs.notify_phase4_primary_warn_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_HOLD_RATIO: ${{ github.event.inputs.notify_phase4_primary_hold_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_MODULE_WARN_THRESHOLDS: ${{ github.event.inputs.notify_phase4_primary_module_warn_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_MODULE_HOLD_THRESHOLDS: ${{ github.event.inputs.notify_phase4_primary_module_hold_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_WARN_RATIO: ${{ github.event.inputs.notify_phase4_secondary_warn_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_HOLD_RATIO: ${{ github.event.inputs.notify_phase4_secondary_hold_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_WARN_MIN_MODULES: ${{ github.event.inputs.notify_phase4_secondary_warn_min_modules }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_MODULE_WARN_THRESHOLDS: ${{ github.event.inputs.notify_phase4_secondary_module_warn_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_MODULE_HOLD_THRESHOLDS: ${{ github.event.inputs.notify_phase4_secondary_module_hold_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_SPEED_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_speed_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_SPEED_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_speed_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_POSITION_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_position_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_POSITION_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_position_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_SPEED_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_speed_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_SPEED_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_speed_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_POSITION_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_position_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_POSITION_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_position_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_HEADING_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_heading_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_HEADING_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_heading_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_LATERAL_POSITION_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_lateral_position_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_LATERAL_POSITION_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_lateral_position_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_HEADING_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_heading_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_HEADING_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_heading_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_LATERAL_POSITION_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_lateral_position_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_LATERAL_POSITION_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_lateral_position_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_RATE_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_rate_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_RATE_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_rate_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_YAW_RATE_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_yaw_rate_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_YAW_RATE_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_yaw_rate_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_VELOCITY_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_velocity_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_VELOCITY_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_velocity_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ACCEL_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_accel_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ACCEL_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_accel_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_ACCEL_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_accel_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_ACCEL_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_accel_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_ACCEL_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_accel_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_ACCEL_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_accel_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_JERK_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_jerk_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_JERK_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_jerk_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_JERK_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_jerk_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_JERK_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_jerk_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_JERK_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_jerk_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_JERK_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_jerk_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_POSITION_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_position_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_POSITION_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_position_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ROAD_GRADE_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_road_grade_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ROAD_GRADE_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_road_grade_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_GRADE_FORCE_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_grade_force_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_GRADE_FORCE_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_grade_force_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_overlap_ratio_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_overlap_ratio_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_steering_rate_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_steering_rate_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_throttle_plus_brake_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_throttle_plus_brake_hold_max }}",
            workflow_text,
        )

    def test_nightly_workflow_exposes_phase4_secondary_notification_inputs(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "e2e-nightly.yml"
        workflow_text = workflow_path.read_text(encoding="utf-8")
        self.assertIn("notify_phase4_primary_warn_ratio:", workflow_text)
        self.assertIn("notify_phase4_primary_hold_ratio:", workflow_text)
        self.assertIn("notify_phase4_primary_module_warn_thresholds:", workflow_text)
        self.assertIn("notify_phase4_primary_module_hold_thresholds:", workflow_text)
        self.assertIn("notify_phase4_secondary_warn_ratio:", workflow_text)
        self.assertIn("notify_phase4_secondary_hold_ratio:", workflow_text)
        self.assertIn("notify_phase4_secondary_warn_min_modules:", workflow_text)
        self.assertIn("notify_phase4_secondary_module_warn_thresholds:", workflow_text)
        self.assertIn("notify_phase4_secondary_module_hold_thresholds:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_speed_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_speed_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_position_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_position_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_speed_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_speed_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_position_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_position_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_heading_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_heading_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_lateral_position_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_final_lateral_position_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_heading_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_heading_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_lateral_position_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_lateral_position_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_rate_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_rate_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_yaw_rate_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_delta_yaw_rate_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_velocity_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_velocity_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_accel_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_accel_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_accel_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_accel_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_accel_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_accel_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_jerk_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_jerk_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_jerk_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_jerk_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_jerk_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_yaw_jerk_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_position_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_lateral_position_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_road_grade_abs_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_road_grade_abs_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_grade_force_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_grade_force_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_overlap_ratio_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_overlap_ratio_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_steering_rate_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_steering_rate_hold_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_throttle_plus_brake_warn_max:", workflow_text)
        self.assertIn("notify_phase3_vehicle_control_throttle_plus_brake_hold_max:", workflow_text)
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_WARN_RATIO: ${{ github.event.inputs.notify_phase4_primary_warn_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_HOLD_RATIO: ${{ github.event.inputs.notify_phase4_primary_hold_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_MODULE_WARN_THRESHOLDS: ${{ github.event.inputs.notify_phase4_primary_module_warn_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_PRIMARY_MODULE_HOLD_THRESHOLDS: ${{ github.event.inputs.notify_phase4_primary_module_hold_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_WARN_RATIO: ${{ github.event.inputs.notify_phase4_secondary_warn_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_HOLD_RATIO: ${{ github.event.inputs.notify_phase4_secondary_hold_ratio }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_WARN_MIN_MODULES: ${{ github.event.inputs.notify_phase4_secondary_warn_min_modules }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_MODULE_WARN_THRESHOLDS: ${{ github.event.inputs.notify_phase4_secondary_module_warn_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE4_SECONDARY_MODULE_HOLD_THRESHOLDS: ${{ github.event.inputs.notify_phase4_secondary_module_hold_thresholds }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_SPEED_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_speed_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_SPEED_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_speed_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_POSITION_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_position_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_POSITION_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_position_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_SPEED_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_speed_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_SPEED_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_speed_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_POSITION_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_position_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_POSITION_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_position_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_HEADING_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_heading_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_HEADING_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_heading_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_LATERAL_POSITION_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_lateral_position_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_FINAL_LATERAL_POSITION_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_final_lateral_position_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_HEADING_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_heading_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_HEADING_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_heading_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_LATERAL_POSITION_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_lateral_position_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_LATERAL_POSITION_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_lateral_position_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_RATE_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_rate_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_RATE_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_rate_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_YAW_RATE_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_yaw_rate_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_DELTA_YAW_RATE_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_delta_yaw_rate_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_VELOCITY_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_velocity_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_VELOCITY_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_velocity_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ACCEL_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_accel_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ACCEL_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_accel_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_ACCEL_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_accel_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_ACCEL_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_accel_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_ACCEL_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_accel_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_ACCEL_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_accel_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_JERK_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_jerk_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_JERK_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_jerk_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_JERK_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_jerk_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_JERK_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_jerk_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_JERK_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_jerk_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_YAW_JERK_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_yaw_jerk_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_POSITION_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_position_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_LATERAL_POSITION_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_lateral_position_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ROAD_GRADE_ABS_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_road_grade_abs_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_ROAD_GRADE_ABS_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_road_grade_abs_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_GRADE_FORCE_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_grade_force_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_GRADE_FORCE_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_grade_force_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_overlap_ratio_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_overlap_ratio_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_steering_rate_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_steering_rate_hold_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_WARN_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_throttle_plus_brake_warn_max }}",
            workflow_text,
        )
        self.assertIn(
            "NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_HOLD_MAX: ${{ github.event.inputs.notify_phase3_vehicle_control_throttle_plus_brake_hold_max }}",
            workflow_text,
        )


if __name__ == "__main__":
    unittest.main()
