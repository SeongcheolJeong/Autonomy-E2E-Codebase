#!/usr/bin/env python3
"""Minimal Sensor Sim bridge with pluggable camera/lidar/radar stub adapters."""

from __future__ import annotations

import argparse
import json
import math
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


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _resolve_camera_dynamic_range_bounds(raw_value: Any) -> tuple[float, float]:
    min_ev = 4.0
    max_ev = 14.0
    if isinstance(raw_value, dict):
        min_ev = _to_float(raw_value.get("min", min_ev), default=min_ev)
        max_ev = _to_float(raw_value.get("max", max_ev), default=max_ev)
    elif isinstance(raw_value, (list, tuple)) and len(raw_value) >= 2:
        min_ev = _to_float(raw_value[0], default=min_ev)
        max_ev = _to_float(raw_value[1], default=max_ev)
    min_ev = _clamp_float(min_ev, minimum=-10.0, maximum=24.0)
    max_ev = _clamp_float(max_ev, minimum=-10.0, maximum=24.0)
    if max_ev < min_ev:
        min_ev, max_ev = max_ev, min_ev
    return (min_ev, max_ev)


def _estimate_scene_ev100(*, ambient_light_lux: float) -> float:
    lux = max(ambient_light_lux, 0.1)
    return math.log2(lux * 0.125)


def _resolve_camera_physics_config(sensor_config: dict[str, Any]) -> dict[str, Any]:
    standard_params = _as_dict(sensor_config.get("standard_params"))
    lens_params = _as_dict(sensor_config.get("lens_params"))
    if not lens_params:
        lens_params = _as_dict(standard_params.get("lens_params"))
    sensor_params = _as_dict(sensor_config.get("sensor_params"))
    if not sensor_params:
        sensor_params = _as_dict(standard_params.get("sensor_params"))
    system_params = _as_dict(sensor_config.get("system_params"))
    if not system_params:
        system_params = _as_dict(standard_params.get("system_params"))
    exposure_params = _as_dict(system_params.get("exposure"))
    fixed_pattern_noise = _as_dict(sensor_params.get("fixed_pattern_noise"))
    if not fixed_pattern_noise:
        fixed_pattern_noise = _as_dict(sensor_config.get("fixed_pattern_noise"))
    rolling_shutter = _as_dict(sensor_params.get("rolling_shutter"))
    if not rolling_shutter:
        rolling_shutter = _as_dict(sensor_config.get("rolling_shutter"))
    camera_intrinsic_params = _as_dict(lens_params.get("camera_intrinsic_params"))
    field_of_view = _as_dict(standard_params.get("field_of_view"))

    explicit_physics_input_present = any(
        key in sensor_config
        for key in (
            "f_number",
            "iso",
            "shutter_speed",
            "shutter_speed_hz",
            "readout_noise",
            "quantum_efficiency",
            "full_well_capacity",
            "fixed_pattern_noise",
            "rolling_shutter",
            "frame_rate_hz",
            "field_of_view_deg",
            "field_of_view_az_rad",
        )
    ) or any(
        key in lens_params for key in ("f_number", "focal_length", "camera_intrinsic_params")
    ) or any(
        key in sensor_params
        for key in (
            "iso",
            "shutter_speed",
            "readout_noise",
            "quantum_efficiency",
            "full_well_capacity",
            "fixed_pattern_noise",
            "rolling_shutter",
        )
    ) or bool(exposure_params)

    f_number = _clamp_float(
        _to_float(
            sensor_config.get(
                "f_number",
                lens_params.get("f_number", 2.8),
            ),
            default=2.8,
        ),
        minimum=0.7,
        maximum=32.0,
    )
    iso = _clamp_float(
        _to_float(
            sensor_config.get(
                "iso",
                sensor_params.get("iso", 100.0),
            ),
            default=100.0,
        ),
        minimum=25.0,
        maximum=25600.0,
    )
    shutter_speed_hz = _clamp_float(
        _to_float(
            sensor_config.get(
                "shutter_speed_hz",
                sensor_config.get("shutter_speed", sensor_params.get("shutter_speed", 125.0)),
            ),
            default=125.0,
        ),
        minimum=1.0,
        maximum=120000.0,
    )
    quantum_efficiency = _clamp_float(
        _to_float(
            sensor_config.get(
                "quantum_efficiency",
                sensor_params.get("quantum_efficiency", 0.55),
            ),
            default=0.55,
        ),
        minimum=0.01,
        maximum=1.0,
    )
    readout_noise_norm = _clamp_float(
        _to_float(
            sensor_config.get(
                "readout_noise",
                sensor_params.get("readout_noise", 1.0 / 4096.0),
            ),
            default=1.0 / 4096.0,
        ),
        minimum=0.0,
        maximum=1.0,
    )
    full_well_capacity = _clamp_float(
        _to_float(
            sensor_config.get(
                "full_well_capacity",
                sensor_params.get("full_well_capacity", 45000.0),
            ),
            default=45000.0,
        ),
        minimum=1000.0,
        maximum=1_000_000.0,
    )
    dsnu = _clamp_float(
        _to_float(
            fixed_pattern_noise.get("dsnu", sensor_config.get("fixed_pattern_noise_dsnu", 0.0)),
            default=0.0,
        ),
        minimum=0.0,
        maximum=1.0,
    )
    prnu = _clamp_float(
        _to_float(
            fixed_pattern_noise.get("prnu", sensor_config.get("fixed_pattern_noise_prnu", 0.0)),
            default=0.0,
        ),
        minimum=0.0,
        maximum=1.0,
    )
    row_delay_ns = _clamp_float(
        _to_float(
            rolling_shutter.get("row_delay", sensor_config.get("rolling_shutter_row_delay_ns", 0.0)),
            default=0.0,
        ),
        minimum=0.0,
        maximum=1_000_000.0,
    )
    col_delay_ns = _clamp_float(
        _to_float(
            rolling_shutter.get("col_delay", sensor_config.get("rolling_shutter_col_delay_ns", 0.0)),
            default=0.0,
        ),
        minimum=0.0,
        maximum=1_000_000.0,
    )
    num_time_steps = _to_non_negative_int(
        rolling_shutter.get("num_time_steps", sensor_config.get("rolling_shutter_num_time_steps", 0))
    )
    num_exposure_samples_per_pixel = _to_non_negative_int(
        rolling_shutter.get(
            "num_exposure_samples_per_pixel",
            sensor_config.get("rolling_shutter_num_exposure_samples_per_pixel", 1),
        )
    )
    frame_rate_hz = _clamp_float(
        _to_float(
            sensor_config.get(
                "frame_rate_hz",
                standard_params.get("frame_rate", 30.0),
            ),
            default=30.0,
        ),
        minimum=1.0,
        maximum=240.0,
    )
    field_of_view_az_rad = _to_float(
        sensor_config.get(
            "field_of_view_az_rad",
            field_of_view.get("az", sensor_config.get("field_of_view_deg", 90.0)),
        ),
        default=math.radians(90.0),
    )
    if field_of_view_az_rad > math.pi:
        field_of_view_az_rad = math.radians(field_of_view_az_rad)
    field_of_view_az_rad = _clamp_float(field_of_view_az_rad, minimum=0.1, maximum=math.pi - 0.01)
    focal_length_px = _to_float(camera_intrinsic_params.get("fx", 0.0), default=0.0)
    if focal_length_px <= 0.0:
        focal_length_px = _clamp_float(
            _to_float(
                sensor_config.get("focal_length_px", 0.0),
                default=0.0,
            ),
            minimum=0.0,
            maximum=100000.0,
        )

    auto_exposure = _to_bool(
        sensor_config.get(
            "auto_exposure",
            exposure_params.get("auto_exposure", False),
        ),
        default=False,
    )
    exposure_speed = _clamp_float(
        _to_float(
            sensor_config.get("exposure_speed", exposure_params.get("speed", 1.0)),
            default=1.0,
        ),
        minimum=0.0,
        maximum=20.0,
    )
    dynamic_range_min_ev, dynamic_range_max_ev = _resolve_camera_dynamic_range_bounds(
        exposure_params.get(
            "dynamic_range",
            {
                "min": sensor_config.get("exposure_dynamic_range_min_ev", 4.0),
                "max": sensor_config.get("exposure_dynamic_range_max_ev", 14.0),
            },
        )
    )

    return {
        "physics_input_present": bool(explicit_physics_input_present),
        "f_number": float(f_number),
        "iso": float(iso),
        "shutter_speed_hz": float(shutter_speed_hz),
        "quantum_efficiency": float(quantum_efficiency),
        "readout_noise_norm": float(readout_noise_norm),
        "full_well_capacity": float(full_well_capacity),
        "fixed_pattern_noise_dsnu": float(dsnu),
        "fixed_pattern_noise_prnu": float(prnu),
        "row_delay_ns": float(row_delay_ns),
        "col_delay_ns": float(col_delay_ns),
        "num_time_steps": int(num_time_steps),
        "num_exposure_samples_per_pixel": int(max(1, num_exposure_samples_per_pixel)),
        "frame_rate_hz": float(frame_rate_hz),
        "field_of_view_az_rad": float(field_of_view_az_rad),
        "focal_length_px": float(focal_length_px),
        "auto_exposure": bool(auto_exposure),
        "exposure_speed": float(exposure_speed),
        "dynamic_range_min_ev": float(dynamic_range_min_ev),
        "dynamic_range_max_ev": float(dynamic_range_max_ev),
    }


def _compute_camera_physics(
    *,
    sensor_config: dict[str, Any],
    environment: dict[str, float],
    image_width_px: int,
    image_height_px: int,
) -> dict[str, Any]:
    config = _resolve_camera_physics_config(sensor_config)
    f_number = float(config["f_number"])
    iso = float(config["iso"])
    shutter_speed_hz = float(config["shutter_speed_hz"])
    quantum_efficiency = float(config["quantum_efficiency"])
    readout_noise_norm = float(config["readout_noise_norm"])
    full_well_capacity = float(config["full_well_capacity"])
    dsnu = float(config["fixed_pattern_noise_dsnu"])
    prnu = float(config["fixed_pattern_noise_prnu"])
    row_delay_ns = float(config["row_delay_ns"])
    col_delay_ns = float(config["col_delay_ns"])
    num_time_steps = int(config["num_time_steps"])
    num_exposure_samples_per_pixel = int(config["num_exposure_samples_per_pixel"])
    frame_rate_hz = float(config["frame_rate_hz"])
    field_of_view_az_rad = float(config["field_of_view_az_rad"])
    focal_length_px = float(config["focal_length_px"])
    auto_exposure = bool(config["auto_exposure"])
    exposure_speed = float(config["exposure_speed"])
    dynamic_range_min_ev = float(config["dynamic_range_min_ev"])
    dynamic_range_max_ev = float(config["dynamic_range_max_ev"])
    if focal_length_px <= 0.0:
        focal_length_px = float(image_width_px) / (2.0 * math.tan(field_of_view_az_rad / 2.0))

    ambient_light_lux = float(environment["ambient_light_lux"])
    precipitation_intensity = float(environment["precipitation_intensity"])
    fog_density = float(environment["fog_density"])
    ego_speed_mps = float(environment["ego_speed_mps"])

    exposure_time_sec = 1.0 / shutter_speed_hz
    aperture_area_scale = 1.0 / (f_number * f_number)
    weather_transmittance = _clamp_float(
        1.0 - (0.28 * precipitation_intensity) - (0.32 * fog_density),
        minimum=0.05,
        maximum=1.0,
    )
    scene_ev100 = _estimate_scene_ev100(ambient_light_lux=ambient_light_lux)
    setting_ev = math.log2(max((f_number * f_number) * shutter_speed_hz * 100.0 / max(iso, 1.0), 1e-6))
    exposure_fill_ratio = (
        ambient_light_lux
        * exposure_time_sec
        * aperture_area_scale
        * weather_transmittance
        * (iso / 100.0)
        * quantum_efficiency
        / 120.0
    )
    if auto_exposure:
        target_ev = _clamp_float(scene_ev100, minimum=dynamic_range_min_ev, maximum=dynamic_range_max_ev)
        auto_speed_gain = _clamp_float(exposure_speed / 5.0, minimum=0.05, maximum=1.0)
        ev_delta = _clamp_float(setting_ev - target_ev, minimum=-4.0, maximum=4.0)
        exposure_fill_ratio *= 2.0 ** (ev_delta * auto_speed_gain)
    signal_saturation_ratio = _clamp_float(exposure_fill_ratio, minimum=0.0, maximum=1.0)
    signal_electrons = signal_saturation_ratio * full_well_capacity

    shot_noise_electrons_std = math.sqrt(max(signal_electrons, 0.0))
    readout_noise_electrons_std = readout_noise_norm * full_well_capacity
    dsnu_noise_electrons_std = dsnu * full_well_capacity
    prnu_noise_electrons_std = prnu * signal_electrons
    total_noise_electrons_std = math.sqrt(
        (shot_noise_electrons_std * shot_noise_electrons_std)
        + (readout_noise_electrons_std * readout_noise_electrons_std)
        + (dsnu_noise_electrons_std * dsnu_noise_electrons_std)
        + (prnu_noise_electrons_std * prnu_noise_electrons_std)
    )
    snr_linear = 0.0
    if total_noise_electrons_std > 0.0:
        snr_linear = signal_electrons / total_noise_electrons_std
    snr_db = 20.0 * math.log10(max(snr_linear, 1e-6))

    rolling_shutter_total_delay_ns = (
        row_delay_ns * max(0, image_height_px - 1)
        + col_delay_ns * max(0, image_width_px - 1)
    )
    rolling_shutter_total_delay_sec = rolling_shutter_total_delay_ns * 1e-9
    frame_period_sec = 1.0 / frame_rate_hz
    rolling_shutter_fraction = _clamp_float(
        rolling_shutter_total_delay_sec / frame_period_sec,
        minimum=0.0,
        maximum=1.0,
    )
    motion_blur_px_est = (
        ego_speed_mps
        * exposure_time_sec
        * (focal_length_px / 25.0)
        * (1.0 + (0.6 * rolling_shutter_fraction))
        * min(2.5, max(1.0, float(num_exposure_samples_per_pixel) / 8.0))
    )
    normalized_total_noise = total_noise_electrons_std / full_well_capacity
    dynamic_range_db_est = 20.0 * math.log10(
        max(full_well_capacity, 1.0) / max(readout_noise_electrons_std, 1.0)
    )
    camera_noise_stddev_px_delta = min(2.0, normalized_total_noise * 60.0)
    motion_blur_level_delta = int(round(min(3.0, motion_blur_px_est / 6.0)))
    dynamic_range_stops_delta = _clamp_float(
        (dynamic_range_db_est - 72.0) / 18.0,
        minimum=-1.5,
        maximum=1.5,
    )

    return {
        "physics_input_present": bool(config["physics_input_present"]),
        "auto_exposure_enabled": bool(auto_exposure),
        "f_number": float(f_number),
        "iso": float(iso),
        "shutter_speed_hz": float(shutter_speed_hz),
        "exposure_time_ms": float(exposure_time_sec * 1000.0),
        "quantum_efficiency": float(quantum_efficiency),
        "full_well_capacity": float(full_well_capacity),
        "signal_saturation_ratio": float(signal_saturation_ratio),
        "signal_electrons_est": float(signal_electrons),
        "shot_noise_electrons_std": float(shot_noise_electrons_std),
        "readout_noise_norm": float(readout_noise_norm),
        "readout_noise_electrons_std": float(readout_noise_electrons_std),
        "fixed_pattern_noise_dsnu": float(dsnu),
        "fixed_pattern_noise_prnu": float(prnu),
        "total_noise_electrons_std": float(total_noise_electrons_std),
        "normalized_total_noise": float(normalized_total_noise),
        "snr_linear": float(snr_linear),
        "snr_db": float(snr_db),
        "scene_ev100_est": float(scene_ev100),
        "setting_ev100_est": float(setting_ev),
        "dynamic_range_ev_min": float(dynamic_range_min_ev),
        "dynamic_range_ev_max": float(dynamic_range_max_ev),
        "dynamic_range_db_est": float(dynamic_range_db_est),
        "rolling_shutter_row_delay_ns": float(row_delay_ns),
        "rolling_shutter_col_delay_ns": float(col_delay_ns),
        "rolling_shutter_num_time_steps": int(num_time_steps),
        "rolling_shutter_num_exposure_samples_per_pixel": int(num_exposure_samples_per_pixel),
        "rolling_shutter_total_delay_ms": float(rolling_shutter_total_delay_sec * 1000.0),
        "rolling_shutter_fraction_of_frame": float(rolling_shutter_fraction),
        "focal_length_px_est": float(focal_length_px),
        "motion_blur_px_est": float(motion_blur_px_est),
        "camera_noise_stddev_px_delta": float(camera_noise_stddev_px_delta),
        "motion_blur_level_delta": int(motion_blur_level_delta),
        "dynamic_range_stops_delta": float(dynamic_range_stops_delta),
        "weather_transmittance": float(weather_transmittance),
    }


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
        image_width_px = int(sensor_config.get("image_width_px", 1920))
        image_height_px = int(sensor_config.get("image_height_px", 1080))
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
        camera_physics = _compute_camera_physics(
            sensor_config=sensor_config,
            environment=environment,
            image_width_px=image_width_px,
            image_height_px=image_height_px,
        )
        if bool(camera_physics.get("physics_input_present", False)):
            camera_noise_stddev_px += _to_non_negative_float(camera_physics.get("camera_noise_stddev_px_delta", 0.0))
            motion_blur_level += _to_non_negative_int(camera_physics.get("motion_blur_level_delta", 0))
            dynamic_range_stops += _to_float(camera_physics.get("dynamic_range_stops_delta", 0.0), default=0.0)
            snr_db = _to_float(camera_physics.get("snr_db", 40.0), default=40.0)
            snr_visibility_scale = _clamp_float(1.0 - max(0.0, 18.0 - snr_db) / 120.0, minimum=0.85, maximum=1.0)
            visibility_score *= snr_visibility_scale
            visible_actor_count = int(round(float(len(actors)) * visibility_score))
        dynamic_range_stops = max(4.0, dynamic_range_stops)
        return {
            "modality": "camera",
            "image_width_px": image_width_px,
            "image_height_px": image_height_px,
            "visible_actor_count": visible_actor_count,
            "exposure_mode": str(sensor_config.get("exposure_mode", "auto")),
            "camera_noise_stddev_px": float(camera_noise_stddev_px),
            "motion_blur_level": int(motion_blur_level),
            "dynamic_range_stops": float(dynamic_range_stops),
            "visibility_score": float(visibility_score),
            "weather_precipitation_intensity": precipitation_intensity,
            "weather_fog_density": fog_density,
            "ambient_light_lux": ambient_light_lux,
            "camera_physics": camera_physics,
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
    camera_snr_db_total = 0.0
    camera_exposure_time_ms_total = 0.0
    camera_signal_saturation_ratio_total = 0.0
    camera_rolling_shutter_total_delay_ms_total = 0.0
    camera_normalized_total_noise_total = 0.0
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
            camera_physics = payload.get("camera_physics", {})
            if not isinstance(camera_physics, dict):
                camera_physics = {}
            camera_snr_db_total += _to_float(camera_physics.get("snr_db", 0.0), default=0.0)
            camera_exposure_time_ms_total += _to_non_negative_float(camera_physics.get("exposure_time_ms", 0.0))
            camera_signal_saturation_ratio_total += _to_non_negative_float(
                camera_physics.get("signal_saturation_ratio", 0.0)
            )
            camera_rolling_shutter_total_delay_ms_total += _to_non_negative_float(
                camera_physics.get("rolling_shutter_total_delay_ms", 0.0)
            )
            camera_normalized_total_noise_total += _to_non_negative_float(
                camera_physics.get("normalized_total_noise", 0.0)
            )
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
    camera_snr_db_avg = (
        camera_snr_db_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_exposure_time_ms_avg = (
        camera_exposure_time_ms_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_signal_saturation_ratio_avg = (
        camera_signal_saturation_ratio_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_rolling_shutter_total_delay_ms_avg = (
        camera_rolling_shutter_total_delay_ms_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_normalized_total_noise_avg = (
        camera_normalized_total_noise_total / float(camera_frame_count)
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
        "camera_snr_db_avg": float(camera_snr_db_avg),
        "camera_exposure_time_ms_avg": float(camera_exposure_time_ms_avg),
        "camera_signal_saturation_ratio_avg": float(camera_signal_saturation_ratio_avg),
        "camera_rolling_shutter_total_delay_ms_avg": float(camera_rolling_shutter_total_delay_ms_avg),
        "camera_normalized_total_noise_avg": float(camera_normalized_total_noise_avg),
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
