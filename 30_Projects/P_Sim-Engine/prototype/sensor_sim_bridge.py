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


def _to_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp_float(value: float, *, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _resolve_ego_speed_mps(world_state: dict[str, Any]) -> float:
    ego_raw = world_state.get("ego", {})
    if isinstance(ego_raw, dict) and "speed_mps" in ego_raw:
        ego_speed = _to_float(ego_raw.get("speed_mps", 0.0), default=0.0)
        if ego_speed >= 0.0:
            return ego_speed
    actors = world_state.get("actors", [])
    if isinstance(actors, list):
        for actor in actors:
            if not isinstance(actor, dict):
                continue
            actor_id = str(actor.get("actor_id", "")).strip().lower()
            if actor_id != "ego":
                continue
            ego_speed = _to_float(actor.get("speed_mps", 0.0), default=0.0)
            if ego_speed >= 0.0:
                return ego_speed
    return 0.0


def _resolve_world_environment(world_state: dict[str, Any]) -> dict[str, float]:
    environment_raw = world_state.get("environment", {})
    environment = environment_raw if isinstance(environment_raw, dict) else {}
    precipitation_intensity = _clamp_float(
        _to_float(
            environment.get(
                "precipitation_intensity",
                environment.get("rain_intensity", 0.0),
            ),
            default=0.0,
        ),
        minimum=0.0,
        maximum=1.0,
    )
    fog_density = _clamp_float(
        _to_float(environment.get("fog_density", 0.0), default=0.0),
        minimum=0.0,
        maximum=1.0,
    )
    ambient_light_lux = _clamp_float(
        _to_float(environment.get("ambient_light_lux", 12000.0), default=12000.0),
        minimum=0.0,
        maximum=200000.0,
    )
    ego_speed_mps = _clamp_float(
        _resolve_ego_speed_mps(world_state),
        minimum=0.0,
        maximum=120.0,
    )
    return {
        "precipitation_intensity": float(precipitation_intensity),
        "fog_density": float(fog_density),
        "ambient_light_lux": float(ambient_light_lux),
        "ego_speed_mps": float(ego_speed_mps),
    }


def _resolve_darkness_ratio(*, ambient_light_lux: float) -> float:
    daylight_reference_lux = 10000.0
    if ambient_light_lux >= daylight_reference_lux:
        return 0.0
    darkness = 1.0 - (ambient_light_lux / daylight_reference_lux)
    return _clamp_float(darkness, minimum=0.0, maximum=1.0)


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
        environment = _resolve_world_environment(world_state)
        precipitation_intensity = float(environment["precipitation_intensity"])
        fog_density = float(environment["fog_density"])
        ambient_light_lux = float(environment["ambient_light_lux"])
        ego_speed_mps = float(environment["ego_speed_mps"])
        darkness_ratio = _resolve_darkness_ratio(ambient_light_lux=ambient_light_lux)
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        score = int(profile["score"])
        base_camera_noise_stddev_px = float(profile["camera_noise_stddev_px"])
        speed_blur_level = int(ego_speed_mps // 25.0)
        camera_noise_stddev_px = (
            base_camera_noise_stddev_px
            + (0.8 * precipitation_intensity)
            + (0.9 * fog_density)
            + (0.6 * darkness_ratio)
            + (0.05 * float(speed_blur_level))
        )
        motion_blur_level = max(0, score - 1 + speed_blur_level)
        visibility_score = _clamp_float(
            1.0 - ((0.55 * fog_density) + (0.35 * precipitation_intensity) + (0.2 * darkness_ratio)),
            minimum=0.0,
            maximum=1.0,
        )
        visible_actor_count = int(round(float(len(actors)) * visibility_score))
        dynamic_range_stops = max(
            4.0,
            float(8 + (2 * score))
            - (2.5 * fog_density)
            - (1.5 * precipitation_intensity)
            - (2.0 * darkness_ratio),
        )
        return {
            "modality": "camera",
            "image_width_px": int(sensor_config.get("image_width_px", 1920)),
            "image_height_px": int(sensor_config.get("image_height_px", 1080)),
            "visible_actor_count": visible_actor_count,
            "exposure_mode": str(sensor_config.get("exposure_mode", "auto")),
            "camera_noise_stddev_px": float(camera_noise_stddev_px),
            "motion_blur_level": int(motion_blur_level),
            "dynamic_range_stops": float(dynamic_range_stops),
            "visibility_score": float(visibility_score),
            "weather_precipitation_intensity": precipitation_intensity,
            "weather_fog_density": fog_density,
            "ambient_light_lux": ambient_light_lux,
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
        environment = _resolve_world_environment(world_state)
        precipitation_intensity = float(environment["precipitation_intensity"])
        fog_density = float(environment["fog_density"])
        points_per_actor = int(sensor_config.get("points_per_actor", 50))
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        score = int(profile["score"])
        lidar_point_scale = float(profile["lidar_point_scale"])
        base_point_count = int(round(len(actors) * points_per_actor * lidar_point_scale))
        weather_detection_ratio = _clamp_float(
            1.0 - ((0.5 * fog_density) + (0.3 * precipitation_intensity)),
            minimum=0.15,
            maximum=1.0,
        )
        point_count = int(round(float(base_point_count) * weather_detection_ratio))
        max_range_m = float(sensor_config.get("max_range_m", 120.0))
        effective_max_range_m = max(
            10.0,
            max_range_m * (1.0 - ((0.35 * fog_density) + (0.2 * precipitation_intensity))),
        )
        returns_per_laser = max(1, score - int(round((fog_density + precipitation_intensity) * 1.5)))
        return {
            "modality": "lidar",
            "channel_count": int(sensor_config.get("channel_count", 64)),
            "max_range_m": max_range_m,
            "effective_max_range_m": float(effective_max_range_m),
            "point_count": int(point_count),
            "returns_per_laser": int(returns_per_laser),
            "intensity_model": "stub_linear",
            "detection_ratio": float(weather_detection_ratio),
            "weather_precipitation_intensity": precipitation_intensity,
            "weather_fog_density": fog_density,
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
        environment = _resolve_world_environment(world_state)
        precipitation_intensity = float(environment["precipitation_intensity"])
        fog_density = float(environment["fog_density"])
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        base_false_positive_rate = float(profile["radar_false_positive_rate"])
        false_positive_rate = _clamp_float(
            base_false_positive_rate + (0.08 * precipitation_intensity) + (0.05 * fog_density),
            minimum=0.0,
            maximum=0.9,
        )
        target_detection_ratio = _clamp_float(
            1.0 - (0.25 * fog_density),
            minimum=0.6,
            maximum=1.0,
        )
        target_count = int(round(float(len(actors)) * target_detection_ratio))
        radar_clutter_index = _clamp_float(
            (0.5 * precipitation_intensity) + (0.35 * fog_density),
            minimum=0.0,
            maximum=1.0,
        )
        ghost_target_count = 0
        if radar_clutter_index > 0.0:
            ghost_target_count = max(1, int(round(float(len(actors)) * radar_clutter_index * 0.75)))
        false_positive_count = int(round(float(target_count + ghost_target_count) * false_positive_rate))
        return {
            "modality": "radar",
            "max_range_m": float(sensor_config.get("max_range_m", 180.0)),
            "doppler_resolution_mps": float(sensor_config.get("doppler_resolution_mps", 0.1)),
            "target_count": int(target_count),
            "ghost_target_count": int(ghost_target_count),
            "false_positive_count": int(false_positive_count),
            "radar_false_positive_rate": float(false_positive_rate),
            "radar_clutter_index": float(radar_clutter_index),
            "target_detection_ratio": float(target_detection_ratio),
            "weather_precipitation_intensity": precipitation_intensity,
            "weather_fog_density": fog_density,
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
    camera_visibility_score_total = 0.0
    camera_motion_blur_level_total = 0
    lidar_frame_count = 0
    lidar_point_count_total = 0
    lidar_returns_per_laser_total = 0
    lidar_detection_ratio_total = 0.0
    lidar_effective_max_range_m_total = 0.0
    radar_frame_count = 0
    radar_target_count_total = 0
    radar_ghost_target_count_total = 0
    radar_false_positive_count_total = 0
    radar_false_positive_rate_total = 0.0
    radar_clutter_index_total = 0.0

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
            camera_visibility_score_total += _to_non_negative_float(payload.get("visibility_score", 0.0))
            camera_motion_blur_level_total += _to_non_negative_int(payload.get("motion_blur_level", 0))
        elif sensor_type == "lidar":
            lidar_frame_count += 1
            lidar_point_count_total += _to_non_negative_int(payload.get("point_count", 0))
            lidar_returns_per_laser_total += _to_non_negative_int(payload.get("returns_per_laser", 0))
            lidar_detection_ratio_total += _to_non_negative_float(payload.get("detection_ratio", 0.0))
            lidar_effective_max_range_m_total += _to_non_negative_float(payload.get("effective_max_range_m", 0.0))
        elif sensor_type == "radar":
            radar_frame_count += 1
            radar_target_count_total += _to_non_negative_int(payload.get("target_count", 0))
            radar_ghost_target_count_total += _to_non_negative_int(payload.get("ghost_target_count", 0))
            radar_false_positive_count_total += _to_non_negative_int(payload.get("false_positive_count", 0))
            radar_false_positive_rate_total += _to_non_negative_float(payload.get("radar_false_positive_rate", 0.0))
            radar_clutter_index_total += _to_non_negative_float(payload.get("radar_clutter_index", 0.0))

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
    camera_visibility_score_avg = (
        camera_visibility_score_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_motion_blur_level_avg = (
        float(camera_motion_blur_level_total) / float(camera_frame_count)
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
    lidar_detection_ratio_avg = (
        lidar_detection_ratio_total / float(lidar_frame_count)
        if lidar_frame_count > 0
        else 0.0
    )
    lidar_effective_max_range_m_avg = (
        lidar_effective_max_range_m_total / float(lidar_frame_count)
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
    radar_ghost_target_count_avg = (
        float(radar_ghost_target_count_total) / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )
    radar_clutter_index_avg = (
        radar_clutter_index_total / float(radar_frame_count)
        if radar_frame_count > 0
        else 0.0
    )

    return {
        "camera_frame_count": int(camera_frame_count),
        "camera_noise_stddev_px_avg": float(camera_noise_stddev_px_avg),
        "camera_dynamic_range_stops_avg": float(camera_dynamic_range_stops_avg),
        "camera_visibility_score_avg": float(camera_visibility_score_avg),
        "camera_motion_blur_level_avg": float(camera_motion_blur_level_avg),
        "lidar_frame_count": int(lidar_frame_count),
        "lidar_point_count_total": int(lidar_point_count_total),
        "lidar_point_count_avg": float(lidar_point_count_avg),
        "lidar_returns_per_laser_avg": float(lidar_returns_per_laser_avg),
        "lidar_detection_ratio_avg": float(lidar_detection_ratio_avg),
        "lidar_effective_max_range_m_avg": float(lidar_effective_max_range_m_avg),
        "radar_frame_count": int(radar_frame_count),
        "radar_target_count_total": int(radar_target_count_total),
        "radar_ghost_target_count_total": int(radar_ghost_target_count_total),
        "radar_false_positive_count_total": int(radar_false_positive_count_total),
        "radar_false_positive_count_avg": float(radar_false_positive_count_avg),
        "radar_false_positive_rate_avg": float(radar_false_positive_rate_avg),
        "radar_ghost_target_count_avg": float(radar_ghost_target_count_avg),
        "radar_clutter_index_avg": float(radar_clutter_index_avg),
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
        world_environment = _resolve_world_environment(world_state)
        output_payload = {
            "sensor_bridge_schema_version": SENSOR_SIM_BRIDGE_REPORT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "world_state_path": str(world_path),
            "sensor_rig_path": str(rig_path),
            "world_environment": world_environment,
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
