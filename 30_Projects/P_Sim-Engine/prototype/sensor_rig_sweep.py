#!/usr/bin/env python3
"""Minimal sensor rig sweep evaluator based on stub sensor frame metrics."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary
from sensor_sim_bridge import FIDELITY_TIERS, generate_sensor_frames


WORLD_STATE_SCHEMA_VERSION_V0 = "world_state_v0"
RIG_SWEEP_SCHEMA_VERSION_V0 = "sensor_rig_sweep_v0"
ERROR_SOURCE = "sensor_rig_sweep.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate sensor rig candidates with stub sensor sim metrics")
    parser.add_argument("--world-state", required=True, help="World state JSON path")
    parser.add_argument("--rig-candidates", required=True, help="Rig sweep JSON path")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument(
        "--fidelity-tier",
        choices=list(FIDELITY_TIERS),
        default="contract",
        help="Sensor fidelity tier (contract|basic|high)",
    )
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _validate_world_state(world_state: dict[str, Any]) -> None:
    if str(world_state.get("world_state_schema_version", "")) != WORLD_STATE_SCHEMA_VERSION_V0:
        raise ValueError(
            "world_state_schema_version must be "
            f"{WORLD_STATE_SCHEMA_VERSION_V0}"
        )


def _validate_rig_candidates(payload: dict[str, Any]) -> None:
    if str(payload.get("rig_sweep_schema_version", "")) != RIG_SWEEP_SCHEMA_VERSION_V0:
        raise ValueError(
            "rig_sweep_schema_version must be "
            f"{RIG_SWEEP_SCHEMA_VERSION_V0}"
        )
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) == 0:
        raise ValueError("candidates must be a non-empty list")


def _to_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _to_non_negative_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed >= 0.0 else 0.0


def _score_frames(frames: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    camera_visible_actor_total = 0
    camera_visibility_score_total = 0.0
    camera_noise_stddev_px_total = 0.0
    camera_dynamic_range_stops_total = 0.0
    camera_motion_blur_level_total = 0
    camera_snr_db_total = 0.0
    camera_rolling_shutter_temporal_aliasing_risk_total = 0.0
    camera_rolling_shutter_temporal_sampling_quality_total = 0.0
    camera_distortion_edge_shift_px_total = 0.0
    camera_principal_point_offset_norm_total = 0.0
    camera_frame_count = 0
    lidar_point_count_total = 0
    lidar_detection_ratio_total = 0.0
    lidar_effective_range_ratio_total = 0.0
    lidar_returns_per_laser_total = 0
    lidar_atmospheric_transmittance_total = 0.0
    lidar_backscatter_noise_ratio_total = 0.0
    lidar_reflectivity_detection_scale_total = 0.0
    lidar_beam_spot_size_cm_at_max_range_total = 0.0
    lidar_frame_count = 0
    radar_target_count_total = 0
    radar_target_detection_ratio_total = 0.0
    radar_false_positive_rate_total = 0.0
    radar_false_positive_count_total = 0
    radar_ghost_target_count_total = 0
    radar_clutter_index_total = 0.0
    radar_track_purity_total = 0.0
    radar_false_alarm_burden_total = 0.0
    radar_ghost_to_target_ratio_total = 0.0
    radar_effective_detection_quality_total = 0.0
    radar_doppler_resolution_mps_total = 0.0
    radar_max_range_m_total = 0.0
    radar_frame_count = 0

    for frame in frames:
        payload = frame.get("payload", {})
        if not isinstance(payload, dict):
            continue
        modality = str(payload.get("modality", ""))
        if modality == "camera":
            camera_frame_count += 1
            camera_visible_actor_total += _to_non_negative_int(payload.get("visible_actor_count", 0))
            camera_visibility_score_total += _to_non_negative_float(payload.get("visibility_score", 0.0))
            camera_noise_stddev_px_total += _to_non_negative_float(payload.get("camera_noise_stddev_px", 0.0))
            camera_dynamic_range_stops_total += _to_non_negative_float(payload.get("dynamic_range_stops", 0.0))
            camera_motion_blur_level_total += _to_non_negative_int(payload.get("motion_blur_level", 0))
            camera_physics = payload.get("camera_physics", {})
            if not isinstance(camera_physics, dict):
                camera_physics = {}
            camera_geometry = payload.get("camera_geometry", {})
            if not isinstance(camera_geometry, dict):
                camera_geometry = {}
            camera_snr_db_total += _to_non_negative_float(camera_physics.get("snr_db", 0.0))
            camera_rolling_shutter_temporal_aliasing_risk_total += _to_non_negative_float(
                camera_physics.get("rolling_shutter_temporal_aliasing_risk", 0.0)
            )
            camera_rolling_shutter_temporal_sampling_quality_total += _to_non_negative_float(
                camera_physics.get("rolling_shutter_temporal_sampling_quality", 0.0)
            )
            camera_distortion_edge_shift_px_total += _to_non_negative_float(
                camera_geometry.get("distortion_edge_shift_px_est", 0.0)
            )
            camera_principal_point_offset_norm_total += _to_non_negative_float(
                camera_geometry.get("principal_point_offset_norm", 0.0)
            )
        elif modality == "lidar":
            lidar_frame_count += 1
            lidar_point_count_total += _to_non_negative_int(payload.get("point_count", 0))
            lidar_detection_ratio_total += _to_non_negative_float(payload.get("detection_ratio", 0.0))
            lidar_returns_per_laser_total += _to_non_negative_int(payload.get("returns_per_laser", 0))
            lidar_atmospheric_transmittance_total += _to_non_negative_float(
                payload.get("atmospheric_transmittance", 0.0)
            )
            lidar_backscatter_noise_ratio_total += _to_non_negative_float(
                payload.get("backscatter_noise_ratio", 0.0)
            )
            lidar_reflectivity_detection_scale_total += _to_non_negative_float(
                payload.get("reflectivity_detection_scale", 0.0)
            )
            lidar_beam_spot_size_cm_at_max_range_total += _to_non_negative_float(
                payload.get("beam_spot_size_cm_at_max_range", 0.0)
            )
            max_range_m = _to_non_negative_float(payload.get("max_range_m", 0.0))
            effective_max_range_m = _to_non_negative_float(payload.get("effective_max_range_m", 0.0))
            if max_range_m > 0.0:
                lidar_effective_range_ratio_total += min(1.0, effective_max_range_m / max_range_m)
        elif modality == "radar":
            radar_frame_count += 1
            target_count = _to_non_negative_int(payload.get("target_count", 0))
            false_positive_count = _to_non_negative_int(payload.get("false_positive_count", 0))
            ghost_target_count = _to_non_negative_int(payload.get("ghost_target_count", 0))
            target_detection_ratio = _to_non_negative_float(payload.get("target_detection_ratio", 0.0))
            radar_false_positive_rate = _to_non_negative_float(payload.get("radar_false_positive_rate", 0.0))
            radar_clutter_index = _to_non_negative_float(payload.get("radar_clutter_index", 0.0))
            doppler_resolution_mps = _to_non_negative_float(payload.get("doppler_resolution_mps", 0.0))
            max_range_m = _to_non_negative_float(payload.get("max_range_m", 0.0))
            radar_target_count_total += target_count
            radar_target_detection_ratio_total += target_detection_ratio
            radar_false_positive_rate_total += radar_false_positive_rate
            radar_false_positive_count_total += false_positive_count
            radar_ghost_target_count_total += ghost_target_count
            radar_clutter_index_total += radar_clutter_index
            observed_contact_count = max(1.0, float(target_count + false_positive_count + ghost_target_count))
            radar_track_purity_total += float(target_count) / observed_contact_count
            radar_false_alarm_burden_total += float(false_positive_count) / observed_contact_count
            radar_ghost_to_target_ratio_total += float(ghost_target_count) / float(max(1, target_count))
            radar_effective_detection_quality_total += target_detection_ratio * max(0.0, 1.0 - radar_clutter_index)
            radar_doppler_resolution_mps_total += doppler_resolution_mps
            radar_max_range_m_total += max_range_m

    camera_visibility_score_avg = (
        camera_visibility_score_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_noise_stddev_px_avg = (
        camera_noise_stddev_px_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_dynamic_range_stops_avg = (
        camera_dynamic_range_stops_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_motion_blur_level_avg = (
        float(camera_motion_blur_level_total) / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_snr_db_avg = (
        camera_snr_db_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_rolling_shutter_temporal_aliasing_risk_avg = (
        camera_rolling_shutter_temporal_aliasing_risk_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_rolling_shutter_temporal_sampling_quality_avg = (
        camera_rolling_shutter_temporal_sampling_quality_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_distortion_edge_shift_px_avg = (
        camera_distortion_edge_shift_px_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_principal_point_offset_norm_avg = (
        camera_principal_point_offset_norm_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_distortion_quality_avg = (
        1.0 / (1.0 + (camera_distortion_edge_shift_px_avg / 35.0))
        if camera_frame_count > 0
        else 0.0
    )
    lidar_detection_ratio_avg = (
        lidar_detection_ratio_total / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_effective_range_ratio_avg = (
        lidar_effective_range_ratio_total / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_returns_per_laser_avg = (
        float(lidar_returns_per_laser_total) / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_atmospheric_transmittance_avg = (
        lidar_atmospheric_transmittance_total / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_backscatter_noise_ratio_avg = (
        lidar_backscatter_noise_ratio_total / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_reflectivity_detection_scale_avg = (
        lidar_reflectivity_detection_scale_total / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_beam_spot_size_cm_at_max_range_avg = (
        lidar_beam_spot_size_cm_at_max_range_total / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_beam_focus_quality_avg = (
        1.0 / (1.0 + (lidar_beam_spot_size_cm_at_max_range_avg / 25.0))
        if lidar_frame_count > 0
        else 0.0
    )
    radar_target_detection_ratio_avg = (
        radar_target_detection_ratio_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_false_positive_rate_avg = (
        radar_false_positive_rate_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_clutter_index_avg = (
        radar_clutter_index_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_track_purity_avg = (
        radar_track_purity_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_false_alarm_burden_avg = (
        radar_false_alarm_burden_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_ghost_to_target_ratio_avg = (
        radar_ghost_to_target_ratio_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_effective_detection_quality_avg = (
        radar_effective_detection_quality_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_doppler_resolution_mps_avg = (
        radar_doppler_resolution_mps_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_max_range_m_avg = (
        radar_max_range_m_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_doppler_resolution_quality_avg = (
        1.0 / (1.0 + (radar_doppler_resolution_mps_avg / 0.12))
        if radar_frame_count > 0
        else 0.0
    )
    radar_range_coverage_quality_avg = (
        min(1.0, radar_max_range_m_avg / 180.0)
        if radar_frame_count > 0
        else 0.0
    )

    heuristic_score = (
        float(camera_visible_actor_total)
        + (camera_visibility_score_avg * 5.0)
        - (camera_noise_stddev_px_avg * 0.5)
        + (camera_dynamic_range_stops_avg * 0.35)
        + (camera_snr_db_avg * 0.08)
        - (camera_motion_blur_level_avg * 0.35)
        - (camera_rolling_shutter_temporal_aliasing_risk_avg * 2.0)
        + (camera_rolling_shutter_temporal_sampling_quality_avg * 1.0)
        + (camera_distortion_quality_avg * 1.5)
        - (camera_principal_point_offset_norm_avg * 3.0)
        + (float(lidar_point_count_total) / 100.0)
        + (lidar_detection_ratio_avg * 8.0)
        + (lidar_effective_range_ratio_avg * 6.0)
        + (lidar_returns_per_laser_avg * 0.5)
        + (lidar_atmospheric_transmittance_avg * 3.0)
        - (lidar_backscatter_noise_ratio_avg * 1.5)
        + (lidar_reflectivity_detection_scale_avg * 2.0)
        + (lidar_beam_focus_quality_avg * 1.5)
        + (float(radar_target_count_total) * 2.0)
        + (radar_target_detection_ratio_avg * 6.0)
        + (radar_effective_detection_quality_avg * 3.5)
        + (radar_track_purity_avg * 2.0)
        - (radar_false_positive_rate_avg * 10.0)
        - (radar_clutter_index_avg * 4.0)
        - (radar_false_alarm_burden_avg * 4.0)
        - (radar_ghost_to_target_ratio_avg * 2.0)
        + (radar_doppler_resolution_quality_avg * 1.2)
        + (radar_range_coverage_quality_avg * 0.8)
        - (float(radar_false_positive_count_total) * 0.5)
        - (float(radar_ghost_target_count_total) * 0.25)
    )
    metrics = {
        "camera_frame_count": int(camera_frame_count),
        "camera_visible_actor_total": int(camera_visible_actor_total),
        "camera_visibility_score_avg": float(round(camera_visibility_score_avg, 6)),
        "camera_noise_stddev_px_avg": float(round(camera_noise_stddev_px_avg, 6)),
        "camera_dynamic_range_stops_avg": float(round(camera_dynamic_range_stops_avg, 6)),
        "camera_motion_blur_level_avg": float(round(camera_motion_blur_level_avg, 6)),
        "camera_snr_db_avg": float(round(camera_snr_db_avg, 6)),
        "camera_rolling_shutter_temporal_aliasing_risk_avg": float(
            round(camera_rolling_shutter_temporal_aliasing_risk_avg, 6)
        ),
        "camera_rolling_shutter_temporal_sampling_quality_avg": float(
            round(camera_rolling_shutter_temporal_sampling_quality_avg, 6)
        ),
        "camera_distortion_edge_shift_px_avg": float(round(camera_distortion_edge_shift_px_avg, 6)),
        "camera_principal_point_offset_norm_avg": float(round(camera_principal_point_offset_norm_avg, 6)),
        "camera_distortion_quality_avg": float(round(camera_distortion_quality_avg, 6)),
        "lidar_frame_count": int(lidar_frame_count),
        "lidar_point_count_total": int(lidar_point_count_total),
        "lidar_detection_ratio_avg": float(round(lidar_detection_ratio_avg, 6)),
        "lidar_effective_range_ratio_avg": float(round(lidar_effective_range_ratio_avg, 6)),
        "lidar_returns_per_laser_avg": float(round(lidar_returns_per_laser_avg, 6)),
        "lidar_atmospheric_transmittance_avg": float(round(lidar_atmospheric_transmittance_avg, 6)),
        "lidar_backscatter_noise_ratio_avg": float(round(lidar_backscatter_noise_ratio_avg, 6)),
        "lidar_reflectivity_detection_scale_avg": float(round(lidar_reflectivity_detection_scale_avg, 6)),
        "lidar_beam_spot_size_cm_at_max_range_avg": float(round(lidar_beam_spot_size_cm_at_max_range_avg, 6)),
        "lidar_beam_focus_quality_avg": float(round(lidar_beam_focus_quality_avg, 6)),
        "radar_frame_count": int(radar_frame_count),
        "radar_target_count_total": int(radar_target_count_total),
        "radar_target_detection_ratio_avg": float(round(radar_target_detection_ratio_avg, 6)),
        "radar_false_positive_rate_avg": float(round(radar_false_positive_rate_avg, 6)),
        "radar_false_positive_count_total": int(radar_false_positive_count_total),
        "radar_ghost_target_count_total": int(radar_ghost_target_count_total),
        "radar_clutter_index_avg": float(round(radar_clutter_index_avg, 6)),
        "radar_track_purity_avg": float(round(radar_track_purity_avg, 6)),
        "radar_false_alarm_burden_avg": float(round(radar_false_alarm_burden_avg, 6)),
        "radar_ghost_to_target_ratio_avg": float(round(radar_ghost_to_target_ratio_avg, 6)),
        "radar_effective_detection_quality_avg": float(round(radar_effective_detection_quality_avg, 6)),
        "radar_doppler_resolution_mps_avg": float(round(radar_doppler_resolution_mps_avg, 6)),
        "radar_doppler_resolution_quality_avg": float(round(radar_doppler_resolution_quality_avg, 6)),
        "radar_max_range_m_avg": float(round(radar_max_range_m_avg, 6)),
        "radar_range_coverage_quality_avg": float(round(radar_range_coverage_quality_avg, 6)),
    }
    return heuristic_score, metrics


def main() -> int:
    try:
        args = parse_args()
        world_state_path = Path(args.world_state).resolve()
        rig_candidates_path = Path(args.rig_candidates).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        world_state = _load_json_object(world_state_path, "world state")
        rig_candidates_payload = _load_json_object(rig_candidates_path, "rig candidates")
        _validate_world_state(world_state)
        _validate_rig_candidates(rig_candidates_payload)

        rankings: list[dict[str, Any]] = []
        fidelity_tier = str(args.fidelity_tier).strip().lower()
        candidates = rig_candidates_payload.get("candidates", [])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            rig_id = str(candidate.get("rig_id", "")).strip()
            if not rig_id:
                raise ValueError("each candidate requires rig_id")
            sensors = candidate.get("sensors", [])
            if not isinstance(sensors, list) or len(sensors) == 0:
                raise ValueError(f"rig {rig_id} sensors must be a non-empty list")
            frames = generate_sensor_frames(
                world_state,
                {"rig_schema_version": "sensor_rig_v0", "sensors": sensors},
                fidelity_tier=fidelity_tier,
            )
            heuristic_score, metrics = _score_frames(frames)
            rankings.append(
                {
                    "rig_id": rig_id,
                    "sensor_count": len(sensors),
                    "heuristic_score": round(heuristic_score, 6),
                    "metrics": metrics,
                }
            )

        rankings_sorted = sorted(
            rankings,
            key=lambda row: (-float(row.get("heuristic_score", 0.0)), str(row.get("rig_id", ""))),
        )
        best_rig_id = str(rankings_sorted[0]["rig_id"]) if rankings_sorted else ""
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "world_state_path": str(world_state_path),
            "rig_candidates_path": str(rig_candidates_path),
            "sensor_fidelity_tier": fidelity_tier,
            "candidate_count": len(rankings_sorted),
            "best_rig_id": best_rig_id,
            "rankings": rankings_sorted,
        }
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] candidate_count={len(rankings_sorted)}")
        print(f"[ok] best_rig_id={best_rig_id}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] sensor_rig_sweep.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
