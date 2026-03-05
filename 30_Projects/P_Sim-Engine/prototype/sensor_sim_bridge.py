#!/usr/bin/env python3
"""Minimal Sensor Sim bridge with pluggable camera/lidar/radar stub adapters."""

from __future__ import annotations

import argparse
import json
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


WORLD_STATE_SCHEMA_VERSION_V0 = "world_state_v0"
SENSOR_RIG_SCHEMA_VERSION_V0 = "sensor_rig_v0"
SENSOR_SIM_BRIDGE_REPORT_SCHEMA_VERSION_V0 = "sensor_sim_bridge_report_v0"
FIDELITY_TIERS: tuple[str, ...] = ("contract", "basic", "high")
FIDELITY_TIER_PROFILE: dict[str, dict[str, float]] = {
    "contract": {
        "score": 1.0,
        "lidar_point_scale": 1.0,
        "camera_noise_stddev_px": 0.0,
        "radar_false_positive_rate": 0.0,
    },
    "basic": {
        "score": 2.0,
        "lidar_point_scale": 1.5,
        "camera_noise_stddev_px": 0.5,
        "radar_false_positive_rate": 0.02,
    },
    "high": {
        "score": 3.0,
        "lidar_point_scale": 2.0,
        "camera_noise_stddev_px": 1.2,
        "radar_false_positive_rate": 0.05,
    },
}
ERROR_SOURCE = "sensor_sim_bridge.py"
ERROR_PHASE = "resolve_inputs"


class SensorPlugin(ABC):
    @abstractmethod
    def render(
        self,
        *,
        world_state: dict[str, Any],
        sensor_config: dict[str, Any],
        fidelity_tier: str,
    ) -> dict[str, Any]:
        raise NotImplementedError


class CameraStubPlugin(SensorPlugin):
    def render(
        self,
        *,
        world_state: dict[str, Any],
        sensor_config: dict[str, Any],
        fidelity_tier: str,
    ) -> dict[str, Any]:
        actors = world_state.get("actors", [])
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        score = int(profile["score"])
        camera_noise_stddev_px = float(profile["camera_noise_stddev_px"])
        return {
            "modality": "camera",
            "image_width_px": int(sensor_config.get("image_width_px", 1920)),
            "image_height_px": int(sensor_config.get("image_height_px", 1080)),
            "visible_actor_count": len(actors),
            "exposure_mode": str(sensor_config.get("exposure_mode", "auto")),
            "camera_noise_stddev_px": camera_noise_stddev_px,
            "motion_blur_level": max(0, score - 1),
            "dynamic_range_stops": 8 + (2 * score),
        }


class LidarStubPlugin(SensorPlugin):
    def render(
        self,
        *,
        world_state: dict[str, Any],
        sensor_config: dict[str, Any],
        fidelity_tier: str,
    ) -> dict[str, Any]:
        actors = world_state.get("actors", [])
        points_per_actor = int(sensor_config.get("points_per_actor", 50))
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        score = int(profile["score"])
        lidar_point_scale = float(profile["lidar_point_scale"])
        point_count = int(round(len(actors) * points_per_actor * lidar_point_scale))
        return {
            "modality": "lidar",
            "channel_count": int(sensor_config.get("channel_count", 64)),
            "max_range_m": float(sensor_config.get("max_range_m", 120.0)),
            "point_count": point_count,
            "returns_per_laser": score,
            "intensity_model": "stub_linear",
        }


class RadarStubPlugin(SensorPlugin):
    def render(
        self,
        *,
        world_state: dict[str, Any],
        sensor_config: dict[str, Any],
        fidelity_tier: str,
    ) -> dict[str, Any]:
        actors = world_state.get("actors", [])
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        false_positive_rate = float(profile["radar_false_positive_rate"])
        target_count = len(actors)
        false_positive_count = int(round(target_count * false_positive_rate))
        return {
            "modality": "radar",
            "max_range_m": float(sensor_config.get("max_range_m", 180.0)),
            "doppler_resolution_mps": float(sensor_config.get("doppler_resolution_mps", 0.1)),
            "target_count": target_count,
            "false_positive_count": false_positive_count,
            "radar_false_positive_rate": false_positive_rate,
        }


PLUGIN_REGISTRY: dict[str, SensorPlugin] = {
    "camera": CameraStubPlugin(),
    "lidar": LidarStubPlugin(),
    "radar": RadarStubPlugin(),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate minimal sensor frames from world state and sensor rig")
    parser.add_argument("--world-state", required=True, help="World state JSON path")
    parser.add_argument("--sensor-rig", required=True, help="Sensor rig JSON path")
    parser.add_argument("--out", required=True, help="Output sensor frame JSON path")
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


def _validate_world_state(payload: dict[str, Any]) -> None:
    if str(payload.get("world_state_schema_version", "")) != WORLD_STATE_SCHEMA_VERSION_V0:
        raise ValueError(
            "world_state_schema_version must be "
            f"{WORLD_STATE_SCHEMA_VERSION_V0}"
        )
    actors = payload.get("actors", [])
    if not isinstance(actors, list):
        raise ValueError("world_state actors must be a list")


def _validate_sensor_rig(payload: dict[str, Any]) -> None:
    if str(payload.get("rig_schema_version", "")) != SENSOR_RIG_SCHEMA_VERSION_V0:
        raise ValueError(
            "rig_schema_version must be "
            f"{SENSOR_RIG_SCHEMA_VERSION_V0}"
        )
    sensors = payload.get("sensors", [])
    if not isinstance(sensors, list) or len(sensors) == 0:
        raise ValueError("sensor rig sensors must be a non-empty list")


def _count_modality_frames(frames: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        sensor_type = str(frame.get("sensor_type", "")).strip().lower()
        if not sensor_type:
            continue
        counts[sensor_type] = counts.get(sensor_type, 0) + 1
    return {key: counts[key] for key in sorted(counts.keys())}


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


def _summarize_sensor_quality(frames: list[dict[str, Any]]) -> dict[str, Any]:
    camera_frame_count = 0
    camera_noise_stddev_px_total = 0.0
    camera_dynamic_range_stops_total = 0.0
    lidar_frame_count = 0
    lidar_point_count_total = 0
    lidar_returns_per_laser_total = 0
    radar_frame_count = 0
    radar_target_count_total = 0
    radar_false_positive_count_total = 0
    radar_false_positive_rate_total = 0.0

    for frame in frames:
        if not isinstance(frame, dict):
            continue
        sensor_type = str(frame.get("sensor_type", "")).strip().lower()
        payload = frame.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        if sensor_type == "camera":
            camera_frame_count += 1
            camera_noise_stddev_px_total += _to_non_negative_float(payload.get("camera_noise_stddev_px", 0.0))
            camera_dynamic_range_stops_total += _to_non_negative_float(payload.get("dynamic_range_stops", 0.0))
        elif sensor_type == "lidar":
            lidar_frame_count += 1
            lidar_point_count_total += _to_non_negative_int(payload.get("point_count", 0))
            lidar_returns_per_laser_total += _to_non_negative_int(payload.get("returns_per_laser", 0))
        elif sensor_type == "radar":
            radar_frame_count += 1
            radar_target_count_total += _to_non_negative_int(payload.get("target_count", 0))
            radar_false_positive_count_total += _to_non_negative_int(payload.get("false_positive_count", 0))
            radar_false_positive_rate_total += _to_non_negative_float(payload.get("radar_false_positive_rate", 0.0))

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
    lidar_point_count_avg = (
        float(lidar_point_count_total) / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_returns_per_laser_avg = (
        float(lidar_returns_per_laser_total) / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    radar_false_positive_count_avg = (
        float(radar_false_positive_count_total) / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_false_positive_rate_avg = (
        radar_false_positive_rate_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )

    return {
        "camera_frame_count": int(camera_frame_count),
        "camera_noise_stddev_px_avg": float(camera_noise_stddev_px_avg),
        "camera_dynamic_range_stops_avg": float(camera_dynamic_range_stops_avg),
        "lidar_frame_count": int(lidar_frame_count),
        "lidar_point_count_total": int(lidar_point_count_total),
        "lidar_point_count_avg": float(lidar_point_count_avg),
        "lidar_returns_per_laser_avg": float(lidar_returns_per_laser_avg),
        "radar_frame_count": int(radar_frame_count),
        "radar_target_count_total": int(radar_target_count_total),
        "radar_false_positive_count_total": int(radar_false_positive_count_total),
        "radar_false_positive_count_avg": float(radar_false_positive_count_avg),
        "radar_false_positive_rate_avg": float(radar_false_positive_rate_avg),
    }


def generate_sensor_frames(
    world_state: dict[str, Any],
    sensor_rig: dict[str, Any],
    *,
    fidelity_tier: str,
) -> list[dict[str, Any]]:
    sensors = sensor_rig.get("sensors", [])
    result: list[dict[str, Any]] = []
    tier = str(fidelity_tier).strip().lower()
    if tier not in FIDELITY_TIERS:
        raise ValueError(f"fidelity-tier must be one of: {', '.join(FIDELITY_TIERS)}; got: {fidelity_tier}")
    tier_score = int(FIDELITY_TIER_PROFILE[tier]["score"])

    sorted_sensors = sorted(
        [sensor for sensor in sensors if isinstance(sensor, dict)],
        key=lambda sensor: str(sensor.get("sensor_id", "")),
    )
    for sensor in sorted_sensors:
        sensor_id = str(sensor.get("sensor_id", "")).strip()
        sensor_type = str(sensor.get("sensor_type", "")).strip().lower()
        if not sensor_id:
            raise ValueError("sensor_id must be a non-empty string")
        plugin = PLUGIN_REGISTRY.get(sensor_type)
        if plugin is None:
            raise ValueError(f"unsupported sensor_type: {sensor_type}")
        frame_payload = plugin.render(
            world_state=world_state,
            sensor_config=sensor,
            fidelity_tier=tier,
        )
        result.append(
            {
                "sensor_id": sensor_id,
                "sensor_type": sensor_type,
                "frame_timestamp": str(world_state.get("frame_timestamp", "")),
                "sensor_fidelity_tier": tier,
                "sensor_fidelity_tier_score": tier_score,
                "payload": frame_payload,
            }
        )
    return result


def main() -> int:
    try:
        args = parse_args()
        world_path = Path(args.world_state).resolve()
        rig_path = Path(args.sensor_rig).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        world_state = _load_json_object(world_path, "world state")
        sensor_rig = _load_json_object(rig_path, "sensor rig")
        _validate_world_state(world_state)
        _validate_sensor_rig(sensor_rig)

        fidelity_tier = str(args.fidelity_tier).strip().lower()
        frames = generate_sensor_frames(
            world_state,
            sensor_rig,
            fidelity_tier=fidelity_tier,
        )
        modality_counts = _count_modality_frames(frames)
        sensor_quality_summary = _summarize_sensor_quality(frames)
        tier_score = int(FIDELITY_TIER_PROFILE[fidelity_tier]["score"])
        output_payload = {
            "sensor_bridge_schema_version": SENSOR_SIM_BRIDGE_REPORT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "world_state_path": str(world_path),
            "sensor_rig_path": str(rig_path),
            "sensor_fidelity_tier": fidelity_tier,
            "sensor_fidelity_tier_score": tier_score,
            "frame_count": len(frames),
            "sensor_stream_modality_counts": modality_counts,
            "sensor_quality_summary": sensor_quality_summary,
            "frames": frames,
        }
        out_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] sensor_frame_count={len(frames)}")
        print(f"[ok] sensor_fidelity_tier={fidelity_tier}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] sensor_sim_bridge.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
