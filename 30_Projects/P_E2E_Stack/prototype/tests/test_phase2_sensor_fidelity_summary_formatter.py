#!/usr/bin/env python3
"""Unit tests for shared Phase2 sensor-fidelity summary formatter."""

from __future__ import annotations

import unittest
from typing import Any

from phase2_sensor_fidelity_summary_formatter import (
    format_phase2_sensor_fidelity_summary,
)


def _fmt_counts_compact(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return "n/a"
    return ",".join(f"{key}:{payload[key]}" for key in sorted(payload))


def _fmt_counts_spaced(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return "n/a"
    return ", ".join(f"{key}:{payload[key]}" for key in sorted(payload))


class Phase2SensorFidelitySummaryFormatterTests(unittest.TestCase):
    def test_returns_na_for_invalid_payload(self) -> None:
        self.assertEqual(
            format_phase2_sensor_fidelity_summary(
                None,
                format_counts=_fmt_counts_compact,
            ),
            "n/a",
        )
        self.assertEqual(
            format_phase2_sensor_fidelity_summary(
                {"evaluated_manifest_count": 0},
                format_counts=_fmt_counts_compact,
            ),
            "n/a",
        )

    def test_formats_compact_and_spaced_styles_with_rig_sweep_extrema(self) -> None:
        payload = {
            "evaluated_manifest_count": 1,
            "fidelity_tier_counts": {"high": 1},
            "fidelity_tier_score_avg": 0.8123,
            "fidelity_tier_score_max": 0.88,
            "highest_fidelity_tier_score_batch_id": "BATCH_MAIN",
            "sensor_frame_count_total": 42,
            "sensor_frame_count_avg": 42.0,
            "sensor_frame_count_max": 42,
            "highest_sensor_frame_count_batch_id": "BATCH_MAIN",
            "sensor_modality_counts_total": {"camera": 2, "lidar": 1, "radar": 1},
            "sensor_camera_noise_stddev_px_avg": 0.09,
            "sensor_lidar_point_count_total": 8000,
            "sensor_lidar_point_count_avg": 8000.0,
            "sensor_lidar_atmospheric_transmittance_avg": 0.82,
            "sensor_lidar_backscatter_noise_ratio_avg": 0.14,
            "sensor_lidar_reflectivity_detection_scale_avg": 0.96,
            "sensor_lidar_beam_spot_size_cm_at_max_range_avg": 14.2,
            "sensor_radar_false_positive_count_total": 20,
            "sensor_radar_false_positive_count_avg": 20.0,
            "sensor_radar_false_positive_rate_avg": 0.005,
            "sensor_camera_rolling_shutter_total_delay_ms_avg": 29.5,
            "sensor_camera_rolling_shutter_time_step_us_avg": 27.1,
            "sensor_camera_rolling_shutter_temporal_aliasing_risk_avg": 0.08,
            "sensor_camera_rolling_shutter_temporal_sampling_quality_avg": 1.0,
            "sensor_camera_rolling_shutter_pixel_motion_per_step_px_avg": 0.029148,
            "sensor_camera_shroud_input_enabled_frame_count_total": 12,
            "sensor_camera_shroud_dirt_intensity_avg": 0.8,
            "sensor_camera_shroud_fog_intensity_avg": 0.6,
            "sensor_camera_shroud_occlusion_ratio_avg": 0.32,
            "sensor_camera_shroud_scatter_strength_avg": 0.41,
            "sensor_camera_shroud_droplet_coverage_ratio_avg": 0.27,
            "sensor_camera_shroud_droplets_state_counts_total": {"DYNAMIC": 12},
            "sensor_camera_depth_enabled_frame_count_total": 12,
            "sensor_camera_depth_min_m_avg": 0.2,
            "sensor_camera_depth_max_m_avg": 75.0,
            "sensor_camera_depth_bit_depth_avg": 16.0,
            "sensor_camera_depth_mode_counts_total": {"linear": 1},
            "sensor_camera_optical_flow_enabled_frame_count_total": 10,
            "sensor_camera_optical_flow_magnitude_px_avg": 3.2,
            "sensor_camera_optical_flow_velocity_direction_counts_total": {
                "x+": 5,
                "x-": 2,
            },
            "sensor_camera_optical_flow_y_axis_direction_counts_total": {"y+": 4},
            "rig_sweep_evaluated_manifest_count": 1,
            "rig_sweep_fidelity_tier_counts": {"high": 1},
            "rig_sweep_best_rig_id_counts": {"rig_cam_lidar": 1},
            "rig_sweep_candidate_count_total": 4,
            "rig_sweep_candidate_count_avg": 4.0,
            "rig_sweep_candidate_count_max": 4,
            "rig_sweep_highest_candidate_count_batch_id": "BATCH_SWEEP",
            "rig_sweep_best_heuristic_score_max": 0.934,
            "rig_sweep_highest_best_heuristic_score_batch_id": "BATCH_SWEEP",
            "rig_sweep_best_quality_sample_count": 1,
            "rig_sweep_best_camera_visibility_score_avg": 0.91,
            "rig_sweep_best_camera_noise_stddev_px_avg": 0.041,
            "rig_sweep_best_lidar_detection_ratio_avg": 0.93,
            "rig_sweep_best_lidar_effective_range_ratio_avg": 0.79,
            "rig_sweep_best_radar_target_detection_ratio_avg": 0.88,
            "rig_sweep_best_radar_false_positive_rate_avg": 0.00123,
            "rig_sweep_best_radar_clutter_index_avg": 0.13,
            "rig_sweep_best_camera_visibility_score_max": 0.92,
            "rig_sweep_best_camera_visibility_score_max_batch_id": "BATCH_SWEEP",
            "rig_sweep_best_lidar_detection_ratio_max": 0.95,
            "rig_sweep_best_lidar_detection_ratio_max_batch_id": "BATCH_SWEEP",
            "rig_sweep_best_radar_target_detection_ratio_max": 0.9,
            "rig_sweep_best_radar_target_detection_ratio_max_batch_id": "BATCH_SWEEP",
            "rig_sweep_best_radar_false_positive_rate_min": 0.00091,
            "rig_sweep_best_radar_false_positive_rate_min_batch_id": "BATCH_SWEEP",
            "rig_sweep_best_radar_clutter_index_min": 0.11,
            "rig_sweep_best_radar_clutter_index_min_batch_id": "BATCH_SWEEP",
        }
        compact = format_phase2_sensor_fidelity_summary(
            payload,
            format_counts=_fmt_counts_compact,
            spaced=False,
        )
        self.assertIn("camera_rs_step_avg_us=27.100", compact)
        self.assertIn("lidar_atmo_trans_avg=0.820", compact)
        self.assertIn("lidar_backscatter_avg=0.140", compact)
        self.assertIn("lidar_reflectivity_scale_avg=0.960", compact)
        self.assertIn("lidar_beam_spot_avg_cm=14.200", compact)
        self.assertIn("fidelity_score_max=0.880(BATCH_MAIN)", compact)
        self.assertIn("camera_shroud_states=DYNAMIC:12", compact)
        self.assertIn("rig_sweep_best_camera_visibility_max=0.920(BATCH_SWEEP)", compact)
        self.assertIn("rig_sweep_best_radar_fp_rate_min=0.000910(BATCH_SWEEP)", compact)
        self.assertNotIn("0.880 (BATCH_MAIN)", compact)

        spaced = format_phase2_sensor_fidelity_summary(
            payload,
            format_counts=_fmt_counts_spaced,
            spaced=True,
        )
        self.assertIn("camera_rs_step_avg_us=27.100", spaced)
        self.assertIn("lidar_atmo_trans_avg=0.820", spaced)
        self.assertIn("lidar_backscatter_avg=0.140", spaced)
        self.assertIn("lidar_reflectivity_scale_avg=0.960", spaced)
        self.assertIn("lidar_beam_spot_avg_cm=14.200", spaced)
        self.assertIn("fidelity_score_max=0.880 (BATCH_MAIN)", spaced)
        self.assertIn("camera_shroud_states=DYNAMIC:12", spaced)
        self.assertIn("rig_sweep_best_camera_visibility_max=0.920 (BATCH_SWEEP)", spaced)
        self.assertIn("rig_sweep_best_radar_fp_rate_min=0.000910 (BATCH_SWEEP)", spaced)
        self.assertIn(", rig_sweep_evaluated=1, ", spaced)


if __name__ == "__main__":
    unittest.main()
