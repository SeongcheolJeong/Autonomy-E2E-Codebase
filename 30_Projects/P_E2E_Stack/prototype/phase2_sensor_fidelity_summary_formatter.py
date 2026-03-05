#!/usr/bin/env python3
"""Shared Phase2 sensor-fidelity summary formatter."""

from __future__ import annotations

from typing import Any, Callable


CountsFormatter = Callable[[Any], str]


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_batch_id(value: Any) -> str:
    return str(value).strip() or "n/a"


def _format_with_batch(value: str, batch_id: str, *, spaced: bool) -> str:
    if spaced:
        return f"{value} ({batch_id})"
    return f"{value}({batch_id})"


def format_phase2_sensor_fidelity_summary(
    payload: Any,
    *,
    format_counts: CountsFormatter,
    spaced: bool = False,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    evaluated_count = _to_int(payload.get("evaluated_manifest_count", 0))
    if evaluated_count <= 0:
        return "n/a"

    separator = ", " if spaced else ","

    fidelity_score_max = _to_float(payload.get("fidelity_tier_score_max", 0.0))
    highest_fidelity_score_batch_id = _to_batch_id(
        payload.get("highest_fidelity_tier_score_batch_id", "")
    )
    frame_count_max = _to_int(payload.get("sensor_frame_count_max", 0))
    highest_sensor_frame_count_batch_id = _to_batch_id(
        payload.get("highest_sensor_frame_count_batch_id", "")
    )

    summary_parts = [
        f"evaluated={evaluated_count}",
        f"tier_counts={format_counts(payload.get('fidelity_tier_counts', {}))}",
        f"fidelity_score_avg={_to_float(payload.get('fidelity_tier_score_avg', 0.0)):.3f}",
        "fidelity_score_max="
        f"{_format_with_batch(f'{fidelity_score_max:.3f}', highest_fidelity_score_batch_id, spaced=spaced)}",
        f"frame_total={_to_int(payload.get('sensor_frame_count_total', 0))}",
        f"frame_avg={_to_float(payload.get('sensor_frame_count_avg', 0.0)):.3f}",
        "frame_max="
        f"{_format_with_batch(str(frame_count_max), highest_sensor_frame_count_batch_id, spaced=spaced)}",
        f"modality_total={format_counts(payload.get('sensor_modality_counts_total', {}))}",
        f"camera_noise_avg_px={_to_float(payload.get('sensor_camera_noise_stddev_px_avg', 0.0)):.3f}",
        f"lidar_point_total={_to_int(payload.get('sensor_lidar_point_count_total', 0))}",
        f"lidar_point_avg={_to_float(payload.get('sensor_lidar_point_count_avg', 0.0)):.3f}",
        f"radar_fp_total={_to_int(payload.get('sensor_radar_false_positive_count_total', 0))}",
        f"radar_fp_avg={_to_float(payload.get('sensor_radar_false_positive_count_avg', 0.0)):.3f}",
        f"radar_fp_rate_avg={_to_float(payload.get('sensor_radar_false_positive_rate_avg', 0.0)):.6f}",
        f"camera_depth_enabled_total={_to_int(payload.get('sensor_camera_depth_enabled_frame_count_total', 0))}",
        f"camera_depth_min_avg_m={_to_float(payload.get('sensor_camera_depth_min_m_avg', 0.0)):.3f}",
        f"camera_depth_max_avg_m={_to_float(payload.get('sensor_camera_depth_max_m_avg', 0.0)):.3f}",
        f"camera_depth_bit_depth_avg={_to_float(payload.get('sensor_camera_depth_bit_depth_avg', 0.0)):.3f}",
        f"camera_depth_modes={format_counts(payload.get('sensor_camera_depth_mode_counts_total', {}))}",
        f"camera_flow_enabled_total={_to_int(payload.get('sensor_camera_optical_flow_enabled_frame_count_total', 0))}",
        f"camera_flow_mag_avg_px={_to_float(payload.get('sensor_camera_optical_flow_magnitude_px_avg', 0.0)):.3f}",
        "camera_flow_velocity_dirs="
        f"{format_counts(payload.get('sensor_camera_optical_flow_velocity_direction_counts_total', {}))}",
        "camera_flow_y_axis_dirs="
        f"{format_counts(payload.get('sensor_camera_optical_flow_y_axis_direction_counts_total', {}))}",
    ]

    rig_sweep_evaluated_count = _to_int(payload.get("rig_sweep_evaluated_manifest_count", 0))
    if rig_sweep_evaluated_count <= 0:
        return separator.join(summary_parts)

    rig_sweep_candidate_count_max = _to_int(payload.get("rig_sweep_candidate_count_max", 0))
    rig_sweep_highest_candidate_count_batch_id = _to_batch_id(
        payload.get("rig_sweep_highest_candidate_count_batch_id", "")
    )
    rig_sweep_best_score_max = _to_float(payload.get("rig_sweep_best_heuristic_score_max", 0.0))
    rig_sweep_best_score_max_batch_id = _to_batch_id(
        payload.get("rig_sweep_highest_best_heuristic_score_batch_id", "")
    )
    rig_sweep_best_camera_visibility_score_max = _to_float(
        payload.get("rig_sweep_best_camera_visibility_score_max", 0.0)
    )
    rig_sweep_best_camera_visibility_score_max_batch_id = _to_batch_id(
        payload.get("rig_sweep_best_camera_visibility_score_max_batch_id", "")
    )
    rig_sweep_best_lidar_detection_ratio_max = _to_float(
        payload.get("rig_sweep_best_lidar_detection_ratio_max", 0.0)
    )
    rig_sweep_best_lidar_detection_ratio_max_batch_id = _to_batch_id(
        payload.get("rig_sweep_best_lidar_detection_ratio_max_batch_id", "")
    )
    rig_sweep_best_radar_target_detection_ratio_max = _to_float(
        payload.get("rig_sweep_best_radar_target_detection_ratio_max", 0.0)
    )
    rig_sweep_best_radar_target_detection_ratio_max_batch_id = _to_batch_id(
        payload.get("rig_sweep_best_radar_target_detection_ratio_max_batch_id", "")
    )
    rig_sweep_best_radar_false_positive_rate_min = _to_float(
        payload.get("rig_sweep_best_radar_false_positive_rate_min", 0.0)
    )
    rig_sweep_best_radar_false_positive_rate_min_batch_id = _to_batch_id(
        payload.get("rig_sweep_best_radar_false_positive_rate_min_batch_id", "")
    )
    rig_sweep_best_radar_clutter_index_min = _to_float(
        payload.get("rig_sweep_best_radar_clutter_index_min", 0.0)
    )
    rig_sweep_best_radar_clutter_index_min_batch_id = _to_batch_id(
        payload.get("rig_sweep_best_radar_clutter_index_min_batch_id", "")
    )

    summary_parts.extend(
        [
            f"rig_sweep_evaluated={rig_sweep_evaluated_count}",
            f"rig_sweep_tier_counts={format_counts(payload.get('rig_sweep_fidelity_tier_counts', {}))}",
            f"rig_sweep_candidate_total={_to_int(payload.get('rig_sweep_candidate_count_total', 0))}",
            f"rig_sweep_candidate_avg={_to_float(payload.get('rig_sweep_candidate_count_avg', 0.0)):.3f}",
            "rig_sweep_candidate_max="
            f"{_format_with_batch(str(rig_sweep_candidate_count_max), rig_sweep_highest_candidate_count_batch_id, spaced=spaced)}",
            "rig_sweep_best_score_max="
            f"{_format_with_batch(f'{rig_sweep_best_score_max:.3f}', rig_sweep_best_score_max_batch_id, spaced=spaced)}",
            f"rig_sweep_best_rig_counts={format_counts(payload.get('rig_sweep_best_rig_id_counts', {}))}",
            f"rig_sweep_quality_samples={_to_int(payload.get('rig_sweep_best_quality_sample_count', 0))}",
            f"rig_sweep_best_camera_visibility_avg={_to_float(payload.get('rig_sweep_best_camera_visibility_score_avg', 0.0)):.3f}",
            f"rig_sweep_best_camera_noise_avg_px={_to_float(payload.get('rig_sweep_best_camera_noise_stddev_px_avg', 0.0)):.3f}",
            f"rig_sweep_best_lidar_detection_avg={_to_float(payload.get('rig_sweep_best_lidar_detection_ratio_avg', 0.0)):.3f}",
            f"rig_sweep_best_lidar_range_ratio_avg={_to_float(payload.get('rig_sweep_best_lidar_effective_range_ratio_avg', 0.0)):.3f}",
            f"rig_sweep_best_radar_detect_ratio_avg={_to_float(payload.get('rig_sweep_best_radar_target_detection_ratio_avg', 0.0)):.3f}",
            f"rig_sweep_best_radar_fp_rate_avg={_to_float(payload.get('rig_sweep_best_radar_false_positive_rate_avg', 0.0)):.6f}",
            f"rig_sweep_best_radar_clutter_avg={_to_float(payload.get('rig_sweep_best_radar_clutter_index_avg', 0.0)):.3f}",
            "rig_sweep_best_camera_visibility_max="
            f"{_format_with_batch(f'{rig_sweep_best_camera_visibility_score_max:.3f}', rig_sweep_best_camera_visibility_score_max_batch_id, spaced=spaced)}",
            "rig_sweep_best_lidar_detection_max="
            f"{_format_with_batch(f'{rig_sweep_best_lidar_detection_ratio_max:.3f}', rig_sweep_best_lidar_detection_ratio_max_batch_id, spaced=spaced)}",
            "rig_sweep_best_radar_detect_max="
            f"{_format_with_batch(f'{rig_sweep_best_radar_target_detection_ratio_max:.3f}', rig_sweep_best_radar_target_detection_ratio_max_batch_id, spaced=spaced)}",
            "rig_sweep_best_radar_fp_rate_min="
            f"{_format_with_batch(f'{rig_sweep_best_radar_false_positive_rate_min:.6f}', rig_sweep_best_radar_false_positive_rate_min_batch_id, spaced=spaced)}",
            "rig_sweep_best_radar_clutter_min="
            f"{_format_with_batch(f'{rig_sweep_best_radar_clutter_index_min:.3f}', rig_sweep_best_radar_clutter_index_min_batch_id, spaced=spaced)}",
        ]
    )
    return separator.join(summary_parts)
