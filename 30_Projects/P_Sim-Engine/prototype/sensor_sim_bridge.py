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


def _resolve_rgba_norm_channels(
    raw_value: Any,
    *,
    default_r: float,
    default_g: float,
    default_b: float,
    default_a: float,
    minimum: float,
    maximum: float,
) -> dict[str, float]:
    r = default_r
    g = default_g
    b = default_b
    a = default_a
    if isinstance(raw_value, dict):
        r = _to_float(raw_value.get("r", raw_value.get("R", r)), default=r)
        g = _to_float(raw_value.get("g", raw_value.get("G", g)), default=g)
        b = _to_float(raw_value.get("b", raw_value.get("B", b)), default=b)
        a = _to_float(raw_value.get("a", raw_value.get("A", a)), default=a)
    elif isinstance(raw_value, (list, tuple)):
        if len(raw_value) > 0:
            r = _to_float(raw_value[0], default=r)
        if len(raw_value) > 1:
            g = _to_float(raw_value[1], default=g)
        if len(raw_value) > 2:
            b = _to_float(raw_value[2], default=b)
        if len(raw_value) > 3:
            a = _to_float(raw_value[3], default=a)
    elif raw_value is not None and not isinstance(raw_value, bool):
        scalar = _to_float(raw_value, default=default_r)
        r = scalar
        g = scalar
        b = scalar
        a = scalar
    return {
        "r": _clamp_float(r, minimum=minimum, maximum=maximum),
        "g": _clamp_float(g, minimum=minimum, maximum=maximum),
        "b": _clamp_float(b, minimum=minimum, maximum=maximum),
        "a": _clamp_float(a, minimum=minimum, maximum=maximum),
    }


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


def _resolve_camera_auto_exposure_mode(raw_value: Any) -> str:
    mode = str(raw_value if raw_value is not None else "").strip().upper()
    if mode in {"DEFAULT", "REALISTIC", "IMMEDIATE"}:
        return mode
    return "DEFAULT"


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
    auto_exposure_mode = _resolve_camera_auto_exposure_mode(
        exposure_params.get("auto_exposure_mode", sensor_config.get("auto_exposure_mode", "DEFAULT"))
    )
    exposure_range = _clamp_float(
        _to_float(
            exposure_params.get("range", sensor_config.get("exposure_range", 0.0)),
            default=0.0,
        ),
        minimum=-4.0,
        maximum=4.0,
    )
    exposure_range_multiplier = _clamp_float(
        math.pow(10.0, exposure_range),
        minimum=1e-4,
        maximum=1e4,
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
        "auto_exposure_mode": str(auto_exposure_mode),
        "exposure_speed": float(exposure_speed),
        "exposure_range": float(exposure_range),
        "exposure_range_multiplier": float(exposure_range_multiplier),
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
    auto_exposure_mode = str(config["auto_exposure_mode"])
    exposure_speed = float(config["exposure_speed"])
    exposure_range = float(config["exposure_range"])
    exposure_range_multiplier = float(config["exposure_range_multiplier"])
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
    exposure_fill_ratio *= exposure_range_multiplier
    auto_exposure_mode_effective = "MANUAL"
    auto_speed_gain = 0.0
    auto_exposure_gain_applied = 1.0
    ev_delta = 0.0
    if auto_exposure:
        auto_exposure_mode_effective = (
            "IMMEDIATE" if auto_exposure_mode == "IMMEDIATE" else "REALISTIC"
        )
        target_ev = _clamp_float(scene_ev100, minimum=dynamic_range_min_ev, maximum=dynamic_range_max_ev)
        if auto_exposure_mode_effective == "IMMEDIATE":
            auto_speed_gain = 1.0
        else:
            auto_speed_gain = _clamp_float(exposure_speed / 5.0, minimum=0.05, maximum=1.0)
        ev_delta = _clamp_float(setting_ev - target_ev, minimum=-4.0, maximum=4.0)
        auto_exposure_gain_applied = 2.0 ** (ev_delta * auto_speed_gain)
        exposure_fill_ratio *= auto_exposure_gain_applied
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
        "auto_exposure_mode": str(auto_exposure_mode),
        "auto_exposure_mode_effective": str(auto_exposure_mode_effective),
        "auto_exposure_speed_gain": float(auto_speed_gain),
        "auto_exposure_gain_applied": float(auto_exposure_gain_applied),
        "auto_exposure_ev_delta": float(ev_delta),
        "f_number": float(f_number),
        "iso": float(iso),
        "shutter_speed_hz": float(shutter_speed_hz),
        "exposure_time_ms": float(exposure_time_sec * 1000.0),
        "exposure_range": float(exposure_range),
        "exposure_range_multiplier": float(exposure_range_multiplier),
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
        "frame_rate_hz": float(frame_rate_hz),
        "focal_length_px_est": float(focal_length_px),
        "motion_blur_px_est": float(motion_blur_px_est),
        "camera_noise_stddev_px_delta": float(camera_noise_stddev_px_delta),
        "motion_blur_level_delta": int(motion_blur_level_delta),
        "dynamic_range_stops_delta": float(dynamic_range_stops_delta),
        "weather_transmittance": float(weather_transmittance),
    }


def _resolve_bloom_level(raw: Any) -> str:
    value = str(raw if raw is not None else "").strip().upper()
    if value in {"LOW", "HIGH"}:
        return value
    return "LOW"


def _resolve_camera_color_space(raw: Any) -> str:
    value = str(raw if raw is not None else "").strip().upper()
    if value in {"RGB", "LABD65", "XYZD65", "MONO", "RAINBOW"}:
        return value
    return "RGB"


def _resolve_camera_output_data_type(raw: Any) -> str:
    value = str(raw if raw is not None else "").strip().upper()
    if value in {"UINT", "FLOAT"}:
        return value
    return "UINT"


def _resolve_piecewise_linear_mapping(raw: Any) -> list[dict[str, float]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, float]] = []
    for point in raw:
        if not isinstance(point, dict):
            continue
        point_input = _clamp_float(
            _to_float(point.get("input", 0.0), default=0.0),
            minimum=0.0,
            maximum=1.0,
        )
        point_output = _clamp_float(
            _to_float(point.get("output", 0.0), default=0.0),
            minimum=0.0,
            maximum=1.0,
        )
        normalized.append({"input": float(point_input), "output": float(point_output)})
    normalized.sort(key=lambda item: item["input"])
    deduped: list[dict[str, float]] = []
    for point in normalized:
        if deduped and abs(point["input"] - deduped[-1]["input"]) <= 1e-9:
            deduped[-1] = point
            continue
        deduped.append(point)
    return deduped


def _evaluate_piecewise_linear_mapping(points: list[dict[str, float]], value: float) -> float:
    x = _clamp_float(value, minimum=0.0, maximum=1.0)
    if not points:
        return x
    if x <= points[0]["input"]:
        return float(points[0]["output"])
    if x >= points[-1]["input"]:
        return float(points[-1]["output"])
    for idx in range(1, len(points)):
        left = points[idx - 1]
        right = points[idx]
        left_x = float(left["input"])
        right_x = float(right["input"])
        if x > right_x:
            continue
        span = max(1e-9, right_x - left_x)
        t = _clamp_float((x - left_x) / span, minimum=0.0, maximum=1.0)
        left_y = float(left["output"])
        right_y = float(right["output"])
        return left_y + ((right_y - left_y) * t)
    return float(points[-1]["output"])


def _resolve_camera_postprocess_config(sensor_config: dict[str, Any]) -> dict[str, Any]:
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
    fidelity = _as_dict(sensor_config.get("fidelity"))

    vignetting = _as_dict(lens_params.get("vignetting"))
    fidelity_bloom = _as_dict(fidelity.get("bloom"))
    auto_black_level_offset = _as_dict(system_params.get("auto_black_level_offset"))
    black_level_offset_raw = system_params.get("black_level_offset", sensor_config.get("black_level_offset"))
    saturation_raw = system_params.get("saturation", sensor_config.get("saturation"))
    piecewise_linear_mapping = _resolve_piecewise_linear_mapping(
        system_params.get("piecewise_linear_mapping", sensor_config.get("piecewise_linear_mapping"))
    )
    color_space = _resolve_camera_color_space(
        system_params.get("color_space", sensor_config.get("color_space", "RGB"))
    )
    output_data_type = _resolve_camera_output_data_type(
        system_params.get("data_type", sensor_config.get("data_type", "UINT"))
    )

    explicit_input_present = any(
        key in lens_params
        for key in (
            "chromatic_aberration",
            "lens_flare",
            "vignetting",
            "spot_size",
        )
    ) or any(
        key in system_params
        for key in (
            "gain",
            "gamma",
            "white_balance",
            "auto_black_level_offset",
            "black_level_offset",
            "saturation",
            "color_space",
            "data_type",
            "piecewise_linear_mapping",
        )
    ) or any(
        key in fidelity
        for key in (
            "bloom",
            "disable_tonemapper",
        )
    ) or any(
        key in sensor_params for key in ("bloom",)
    ) or any(
        key in sensor_config
        for key in (
            "chromatic_aberration",
            "lens_flare",
            "vignetting_intensity",
            "vignetting_alpha",
            "vignetting_radius",
            "gain",
            "gamma",
            "white_balance",
            "auto_black_level_offset",
            "black_level_offset",
            "saturation",
            "color_space",
            "data_type",
            "piecewise_linear_mapping",
            "bloom",
            "bloom_disable",
            "bloom_level",
            "disable_tonemapper",
        )
    )

    chromatic_aberration_mm = _clamp_float(
        _to_float(
            sensor_config.get(
                "chromatic_aberration",
                lens_params.get("chromatic_aberration", 0.0),
            ),
            default=0.0,
        ),
        minimum=0.0,
        maximum=5.0,
    )
    lens_flare = _clamp_float(
        _to_float(
            sensor_config.get(
                "lens_flare",
                lens_params.get("lens_flare", 0.0),
            ),
            default=0.0,
        ),
        minimum=0.0,
        maximum=2.0,
    )
    vignetting_intensity = _clamp_float(
        _to_float(
            sensor_config.get(
                "vignetting_intensity",
                vignetting.get("intensity", 0.0),
            ),
            default=0.0,
        ),
        minimum=0.0,
        maximum=2.0,
    )
    vignetting_alpha = _clamp_float(
        _to_float(
            sensor_config.get(
                "vignetting_alpha",
                vignetting.get("alpha", 1.0),
            ),
            default=1.0,
        ),
        minimum=0.0,
        maximum=4.0,
    )
    vignetting_radius = _clamp_float(
        _to_float(
            sensor_config.get(
                "vignetting_radius",
                vignetting.get("radius", 1.0),
            ),
            default=1.0,
        ),
        minimum=0.1,
        maximum=2.0,
    )
    spot_size_rms = _clamp_float(
        _to_float(
            sensor_config.get(
                "spot_size",
                lens_params.get("spot_size", 0.0),
            ),
            default=0.0,
        ),
        minimum=0.0,
        maximum=100.0,
    )
    sensor_bloom_intensity = _clamp_float(
        _to_float(
            sensor_config.get(
                "bloom",
                sensor_params.get("bloom", 0.0),
            ),
            default=0.0,
        ),
        minimum=0.0,
        maximum=2.0,
    )
    gain_db = _clamp_float(
        _to_float(
            sensor_config.get("gain", system_params.get("gain", 0.0)),
            default=0.0,
        ),
        minimum=-24.0,
        maximum=48.0,
    )
    gamma = _clamp_float(
        _to_float(
            sensor_config.get("gamma", system_params.get("gamma", 0.4545)),
            default=0.4545,
        ),
        minimum=0.1,
        maximum=4.0,
    )
    white_balance_kelvin = _clamp_float(
        _to_float(
            sensor_config.get(
                "white_balance",
                system_params.get("white_balance", 6500.0),
            ),
            default=6500.0,
        ),
        minimum=1000.0,
        maximum=15000.0,
    )
    auto_black_level_stddev_to_subtract = _clamp_float(
        _to_float(
            auto_black_level_offset.get(
                "stddev_to_subtract",
                sensor_config.get("auto_black_level_stddev_to_subtract", 0.0),
            ),
            default=0.0,
        ),
        minimum=0.0,
        maximum=6.0,
    )
    black_level_offset = _resolve_rgba_norm_channels(
        black_level_offset_raw,
        default_r=0.0,
        default_g=0.0,
        default_b=0.0,
        default_a=0.0,
        minimum=0.0,
        maximum=1.0,
    )
    saturation = _resolve_rgba_norm_channels(
        saturation_raw,
        default_r=1.0,
        default_g=1.0,
        default_b=1.0,
        default_a=1.0,
        minimum=0.0,
        maximum=4.0,
    )
    bloom_disable = _to_bool(
        sensor_config.get("bloom_disable", fidelity_bloom.get("disable", False)),
        default=False,
    )
    bloom_level = _resolve_bloom_level(
        sensor_config.get("bloom_level", fidelity_bloom.get("level", "LOW"))
    )
    disable_tonemapper = _to_bool(
        sensor_config.get("disable_tonemapper", fidelity.get("disable_tonemapper", False)),
        default=False,
    )

    return {
        "postprocess_input_present": bool(explicit_input_present),
        "chromatic_aberration_mm": float(chromatic_aberration_mm),
        "lens_flare": float(lens_flare),
        "vignetting_intensity": float(vignetting_intensity),
        "vignetting_alpha": float(vignetting_alpha),
        "vignetting_radius": float(vignetting_radius),
        "spot_size_rms": float(spot_size_rms),
        "sensor_bloom_intensity": float(sensor_bloom_intensity),
        "gain_db": float(gain_db),
        "gamma": float(gamma),
        "white_balance_kelvin": float(white_balance_kelvin),
        "auto_black_level_stddev_to_subtract": float(auto_black_level_stddev_to_subtract),
        "black_level_offset": black_level_offset,
        "saturation": saturation,
        "color_space": color_space,
        "output_data_type": output_data_type,
        "piecewise_linear_mapping": piecewise_linear_mapping,
        "bloom_disable": bool(bloom_disable),
        "bloom_level": bloom_level,
        "disable_tonemapper": bool(disable_tonemapper),
    }


def _compute_camera_postprocess(
    *,
    sensor_config: dict[str, Any],
    environment: dict[str, float],
    image_width_px: int,
    image_height_px: int,
    camera_physics: dict[str, Any],
) -> dict[str, Any]:
    config = _resolve_camera_postprocess_config(sensor_config)
    chromatic_aberration_mm = float(config["chromatic_aberration_mm"])
    lens_flare = float(config["lens_flare"])
    vignetting_intensity = float(config["vignetting_intensity"])
    vignetting_alpha = float(config["vignetting_alpha"])
    vignetting_radius = float(config["vignetting_radius"])
    spot_size_rms = float(config["spot_size_rms"])
    sensor_bloom_intensity = float(config["sensor_bloom_intensity"])
    gain_db = float(config["gain_db"])
    gamma = float(config["gamma"])
    white_balance_kelvin = float(config["white_balance_kelvin"])
    auto_black_level_stddev_to_subtract = float(config["auto_black_level_stddev_to_subtract"])
    black_level_offset = _as_dict(config.get("black_level_offset"))
    saturation = _as_dict(config.get("saturation"))
    black_level_offset_r = _to_non_negative_float(black_level_offset.get("r", 0.0))
    black_level_offset_g = _to_non_negative_float(black_level_offset.get("g", 0.0))
    black_level_offset_b = _to_non_negative_float(black_level_offset.get("b", 0.0))
    black_level_offset_a = _to_non_negative_float(black_level_offset.get("a", 0.0))
    saturation_r = _to_non_negative_float(saturation.get("r", 1.0))
    saturation_g = _to_non_negative_float(saturation.get("g", 1.0))
    saturation_b = _to_non_negative_float(saturation.get("b", 1.0))
    saturation_a = _to_non_negative_float(saturation.get("a", 1.0))
    color_space = str(config.get("color_space", "RGB"))
    output_data_type = str(config.get("output_data_type", "UINT"))
    piecewise_linear_mapping = config.get("piecewise_linear_mapping", [])
    if not isinstance(piecewise_linear_mapping, list):
        piecewise_linear_mapping = []
    bloom_disable = bool(config["bloom_disable"])
    bloom_level = str(config["bloom_level"])
    disable_tonemapper = bool(config["disable_tonemapper"])

    ambient_light_lux = float(environment["ambient_light_lux"])
    precipitation_intensity = float(environment["precipitation_intensity"])
    image_max_dim = float(max(image_width_px, image_height_px, 1))
    signal_saturation_ratio = _clamp_float(
        _to_float(camera_physics.get("signal_saturation_ratio", 0.0), default=0.0),
        minimum=0.0,
        maximum=1.0,
    )

    gain_linear = 10.0 ** (gain_db / 20.0)
    white_balance_offset_norm = _clamp_float(
        (white_balance_kelvin - 6500.0) / 6500.0,
        minimum=-1.0,
        maximum=1.0,
    )
    black_level_offset_rgb_avg = (
        black_level_offset_r + black_level_offset_g + black_level_offset_b
    ) / 3.0
    black_level_lift_norm = _clamp_float(
        black_level_offset_rgb_avg + (0.25 * black_level_offset_a),
        minimum=0.0,
        maximum=1.0,
    )
    auto_black_level_compensation = _clamp_float(
        auto_black_level_stddev_to_subtract / 6.0,
        minimum=0.0,
        maximum=1.0,
    )
    effective_black_level_lift = _clamp_float(
        black_level_lift_norm * (1.0 - (0.7 * auto_black_level_compensation)),
        minimum=0.0,
        maximum=1.0,
    )
    saturation_rgb_avg = (saturation_r + saturation_g + saturation_b) / 3.0
    saturation_effective_scale = _clamp_float(
        saturation_rgb_avg * saturation_a,
        minimum=0.0,
        maximum=4.0,
    )
    saturation_deviation = abs(saturation_effective_scale - 1.0)
    piecewise_linear_mapping_present = len(piecewise_linear_mapping) >= 2
    piecewise_linear_mapping_point_count = int(len(piecewise_linear_mapping))
    if piecewise_linear_mapping_present:
        piecewise_mapping_input_span = max(
            0.0,
            float(piecewise_linear_mapping[-1]["input"]) - float(piecewise_linear_mapping[0]["input"]),
        )
        piecewise_mapping_output_span = max(
            0.0,
            float(piecewise_linear_mapping[-1]["output"]) - float(piecewise_linear_mapping[0]["output"]),
        )
        piecewise_mapping_dynamic_range_scale = _clamp_float(
            piecewise_mapping_output_span / max(piecewise_mapping_input_span, 1e-6),
            minimum=0.1,
            maximum=4.0,
        )
        mapped_mid = _evaluate_piecewise_linear_mapping(piecewise_linear_mapping, 0.5)
        mapped_low = _evaluate_piecewise_linear_mapping(piecewise_linear_mapping, 0.45)
        mapped_high = _evaluate_piecewise_linear_mapping(piecewise_linear_mapping, 0.55)
        piecewise_mapping_midtone_gain = _clamp_float(
            mapped_mid / 0.5,
            minimum=0.0,
            maximum=4.0,
        )
        piecewise_mapping_contrast_gain = _clamp_float(
            (mapped_high - mapped_low) / 0.1,
            minimum=0.0,
            maximum=4.0,
        )
    else:
        piecewise_mapping_input_span = 0.0
        piecewise_mapping_output_span = 0.0
        piecewise_mapping_dynamic_range_scale = 1.0
        piecewise_mapping_midtone_gain = 1.0
        piecewise_mapping_contrast_gain = 1.0
    piecewise_mapping_midtone_deviation = abs(piecewise_mapping_midtone_gain - 1.0)
    piecewise_mapping_contrast_deviation = abs(piecewise_mapping_contrast_gain - 1.0)
    if color_space == "MONO":
        color_space_visibility_scale = 1.02
        color_space_noise_delta = -0.08
    elif color_space == "RAINBOW":
        color_space_visibility_scale = 0.9
        color_space_noise_delta = 0.16
    elif color_space in {"LABD65", "XYZD65"}:
        color_space_visibility_scale = 0.97
        color_space_noise_delta = 0.06
    else:
        color_space_visibility_scale = 1.0
        color_space_noise_delta = 0.0
    data_type_noise_delta = -0.06 if output_data_type == "FLOAT" else 0.0
    data_type_dynamic_range_delta = 0.12 if output_data_type == "FLOAT" else 0.0
    vignetting_radius_scale = _clamp_float((1.4 - vignetting_radius) / 1.4, minimum=0.0, maximum=1.0)
    vignetting_edge_darkening = _clamp_float(
        vignetting_intensity
        * (0.45 + (0.55 * vignetting_radius_scale))
        * (0.85 + (0.15 * min(2.0, vignetting_alpha))),
        minimum=0.0,
        maximum=0.9,
    )
    chromatic_aberration_shift_px = _clamp_float(
        chromatic_aberration_mm * (image_max_dim / 6000.0),
        minimum=0.0,
        maximum=12.0,
    )
    flare_glare_ratio = _clamp_float(
        lens_flare
        * _clamp_float(ambient_light_lux / 24000.0, minimum=0.0, maximum=1.5)
        * (1.0 + (0.15 * precipitation_intensity)),
        minimum=0.0,
        maximum=2.0,
    )
    if bloom_disable:
        bloom_halo_strength = 0.0
    else:
        bloom_level_scale = 1.3 if bloom_level == "HIGH" else 1.0
        bloom_light_scale = _clamp_float(ambient_light_lux / 12000.0, minimum=0.25, maximum=2.0)
        bloom_halo_strength = _clamp_float(
            (sensor_bloom_intensity + (0.4 * flare_glare_ratio) + (0.35 * signal_saturation_ratio))
            * bloom_level_scale
            * bloom_light_scale,
            minimum=0.0,
            maximum=2.5,
        )

    postprocess_visibility_scale = _clamp_float(
        1.0
        - (0.16 * vignetting_edge_darkening)
        - (0.04 * bloom_halo_strength)
        - (0.06 * flare_glare_ratio)
        - (chromatic_aberration_shift_px / image_max_dim * 8.0)
        - (0.22 * effective_black_level_lift)
        - (0.04 * saturation_deviation),
        minimum=0.65,
        maximum=1.0,
    )
    postprocess_visibility_scale = _clamp_float(
        postprocess_visibility_scale
        * color_space_visibility_scale
        * _clamp_float(
            1.0 - (0.04 * piecewise_mapping_midtone_deviation),
            minimum=0.85,
            maximum=1.08,
        ),
        minimum=0.5,
        maximum=1.05,
    )
    camera_noise_stddev_px_delta = _clamp_float(
        max(0.0, gain_linear - 1.0) * 0.35
        + (abs(gamma - 0.4545) * 0.12)
        + (0.1 * bloom_halo_strength)
        + (0.002 * spot_size_rms)
        + (0.22 * effective_black_level_lift)
        + (0.05 * saturation_deviation)
        + color_space_noise_delta
        + data_type_noise_delta
        + (0.05 * piecewise_mapping_contrast_deviation),
        minimum=0.0,
        maximum=2.0,
    )
    dynamic_range_stops_delta = _clamp_float(
        (-0.42 * max(0.0, gain_linear - 1.0))
        - (0.22 * bloom_halo_strength)
        - (0.08 * flare_glare_ratio)
        - (1.1 * effective_black_level_lift)
        - (0.15 * max(0.0, saturation_effective_scale - 1.0))
        + (0.45 * (piecewise_mapping_dynamic_range_scale - 1.0))
        + data_type_dynamic_range_delta
        + (0.2 if disable_tonemapper else 0.0),
        minimum=-1.5,
        maximum=0.8,
    )
    effective_luminance_gain = _clamp_float(
        gain_linear
        * (0.95 if disable_tonemapper else 1.0)
        * _clamp_float(1.0 + (0.2 * white_balance_offset_norm), minimum=0.7, maximum=1.3)
        * _clamp_float(1.0 + (0.25 * effective_black_level_lift), minimum=0.7, maximum=1.35),
        minimum=0.05,
        maximum=8.0,
    )

    return {
        "postprocess_input_present": bool(config["postprocess_input_present"]),
        "gain_db": float(gain_db),
        "gain_linear": float(gain_linear),
        "gamma": float(gamma),
        "white_balance_kelvin": float(white_balance_kelvin),
        "white_balance_offset_norm": float(white_balance_offset_norm),
        "auto_black_level_stddev_to_subtract": float(auto_black_level_stddev_to_subtract),
        "black_level_offset": {
            "r": float(black_level_offset_r),
            "g": float(black_level_offset_g),
            "b": float(black_level_offset_b),
            "a": float(black_level_offset_a),
        },
        "black_level_offset_rgb_avg": float(black_level_offset_rgb_avg),
        "black_level_lift_norm": float(effective_black_level_lift),
        "saturation": {
            "r": float(saturation_r),
            "g": float(saturation_g),
            "b": float(saturation_b),
            "a": float(saturation_a),
        },
        "color_space": color_space,
        "output_data_type": output_data_type,
        "piecewise_linear_mapping_present": bool(piecewise_linear_mapping_present),
        "piecewise_linear_mapping_point_count": int(piecewise_linear_mapping_point_count),
        "piecewise_linear_mapping_input_span": float(piecewise_mapping_input_span),
        "piecewise_linear_mapping_output_span": float(piecewise_mapping_output_span),
        "piecewise_linear_mapping_dynamic_range_scale": float(piecewise_mapping_dynamic_range_scale),
        "piecewise_linear_mapping_midtone_gain": float(piecewise_mapping_midtone_gain),
        "piecewise_linear_mapping_contrast_gain": float(piecewise_mapping_contrast_gain),
        "color_space_visibility_scale": float(color_space_visibility_scale),
        "color_space_noise_delta": float(color_space_noise_delta),
        "data_type_noise_delta": float(data_type_noise_delta),
        "data_type_dynamic_range_delta": float(data_type_dynamic_range_delta),
        "saturation_rgb_avg": float(saturation_rgb_avg),
        "saturation_effective_scale": float(saturation_effective_scale),
        "chromatic_aberration_mm": float(chromatic_aberration_mm),
        "chromatic_aberration_shift_px_est": float(chromatic_aberration_shift_px),
        "lens_flare_intensity": float(lens_flare),
        "flare_glare_ratio": float(flare_glare_ratio),
        "vignetting_intensity": float(vignetting_intensity),
        "vignetting_alpha": float(vignetting_alpha),
        "vignetting_radius": float(vignetting_radius),
        "vignetting_edge_darkening": float(vignetting_edge_darkening),
        "spot_size_rms": float(spot_size_rms),
        "sensor_bloom_intensity": float(sensor_bloom_intensity),
        "bloom_disable": bool(bloom_disable),
        "bloom_level": bloom_level,
        "bloom_halo_strength": float(bloom_halo_strength),
        "disable_tonemapper": bool(disable_tonemapper),
        "effective_luminance_gain": float(effective_luminance_gain),
        "postprocess_visibility_scale": float(postprocess_visibility_scale),
        "camera_noise_stddev_px_delta": float(camera_noise_stddev_px_delta),
        "dynamic_range_stops_delta": float(dynamic_range_stops_delta),
    }


def _resolve_depth_mode(raw: Any) -> str:
    value = str(raw if raw is not None else "").strip().upper()
    if value in {"LINEAR", "LOG", "HYP_SPLINE", "RAW"}:
        return value
    return "LOG"


def _resolve_optical_flow_velocity_direction(raw: Any) -> str:
    value = str(raw if raw is not None else "").strip().upper()
    if value in {"DEFAULT", "PREVIOUS_TO_CURRENT", "CURRENT_TO_NEXT"}:
        return value
    return "DEFAULT"


def _resolve_optical_flow_y_axis_direction(raw: Any) -> str:
    value = str(raw if raw is not None else "").strip().upper()
    if value in {"DEFAULT", "DOWN", "UP"}:
        return value
    return "DEFAULT"


def _resolve_camera_depth_and_optical_flow_config(sensor_config: dict[str, Any]) -> dict[str, Any]:
    standard_params = _as_dict(sensor_config.get("standard_params"))
    sensor_params = _as_dict(sensor_config.get("sensor_params"))
    if not sensor_params:
        sensor_params = _as_dict(standard_params.get("sensor_params"))
    system_params = _as_dict(sensor_config.get("system_params"))
    if not system_params:
        system_params = _as_dict(standard_params.get("system_params"))
    depth_params = _as_dict(system_params.get("depth_params"))
    optical_flow_2d_settings = _as_dict(sensor_params.get("optical_flow_2d_settings"))
    sensor_mode = str(
        sensor_params.get(
            "type",
            sensor_config.get("camera_mode", sensor_config.get("mode", "")),
        )
    ).strip().upper()

    depth_input_present = bool(depth_params) or ("depth_params" in system_params)
    optical_flow_input_present = bool(optical_flow_2d_settings) or ("optical_flow_2d_settings" in sensor_params)
    depth_enabled = bool(depth_input_present or (sensor_mode == "DEPTH"))
    optical_flow_enabled = bool(optical_flow_input_present or (sensor_mode == "OPTICAL_FLOW_2D"))

    depth_min_m = _clamp_float(_to_float(depth_params.get("min", 0.0), default=0.0), minimum=0.0, maximum=10000.0)
    depth_max_m = _clamp_float(_to_float(depth_params.get("max", 1000.0), default=1000.0), minimum=0.1, maximum=100000.0)
    if depth_max_m <= depth_min_m:
        depth_max_m = depth_min_m + 0.1
    depth_mode = _resolve_depth_mode(depth_params.get("type", "LOG"))
    depth_log_base = _clamp_float(_to_float(depth_params.get("log_base", 300.0), default=300.0), minimum=1.01, maximum=100000.0)
    color_depth = _to_non_negative_int(sensor_params.get("color_depth", 8))
    depth_bit_depth = _to_non_negative_int(depth_params.get("bit_depth", color_depth))
    if depth_bit_depth <= 0:
        depth_bit_depth = 8
    depth_bit_depth = min(32, depth_bit_depth)
    data_type = str(system_params.get("data_type", "UINT")).strip().upper()
    if data_type not in {"UINT", "FLOAT"}:
        data_type = "UINT"

    velocity_direction = _resolve_optical_flow_velocity_direction(
        optical_flow_2d_settings.get("velocity_direction", "DEFAULT")
    )
    y_axis_direction = _resolve_optical_flow_y_axis_direction(
        optical_flow_2d_settings.get("y_axis_direction", "DEFAULT")
    )

    return {
        "depth_input_present": bool(depth_input_present),
        "depth_enabled": bool(depth_enabled),
        "depth_min_m": float(depth_min_m),
        "depth_max_m": float(depth_max_m),
        "depth_mode": depth_mode,
        "depth_log_base": float(depth_log_base),
        "depth_bit_depth": int(depth_bit_depth),
        "depth_data_type": data_type,
        "optical_flow_input_present": bool(optical_flow_input_present),
        "optical_flow_enabled": bool(optical_flow_enabled),
        "velocity_direction": velocity_direction,
        "y_axis_direction": y_axis_direction,
    }


def _compute_camera_depth_and_optical_flow(
    *,
    sensor_config: dict[str, Any],
    environment: dict[str, float],
    camera_physics: dict[str, Any],
    camera_geometry: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = _resolve_camera_depth_and_optical_flow_config(sensor_config)

    depth_min_m = float(config["depth_min_m"])
    depth_max_m = float(config["depth_max_m"])
    depth_mode = str(config["depth_mode"])
    depth_log_base = float(config["depth_log_base"])
    depth_bit_depth = int(config["depth_bit_depth"])
    depth_data_type = str(config["depth_data_type"])
    depth_enabled = bool(config["depth_enabled"])

    depth_range_m = max(0.1, depth_max_m - depth_min_m)
    quantization_levels = max(1, (1 << depth_bit_depth) - 1)
    if depth_mode in {"LINEAR", "RAW"}:
        depth_resolution_m_at_max_est = depth_range_m / float(quantization_levels)
    elif depth_mode == "LOG":
        depth_resolution_m_at_max_est = (
            depth_range_m / float(quantization_levels)
        ) * max(1.0, math.log(depth_log_base))
    else:
        depth_resolution_m_at_max_est = (depth_range_m / float(quantization_levels)) * 1.6

    precipitation_intensity = float(environment["precipitation_intensity"])
    fog_density = float(environment["fog_density"])
    weather_visibility_scale = _clamp_float(
        1.0 - ((0.45 * fog_density) + (0.25 * precipitation_intensity)),
        minimum=0.1,
        maximum=1.0,
    )
    effective_depth_max_m_est = depth_min_m + (depth_range_m * weather_visibility_scale)
    depth_clamp_ratio_est = _clamp_float(1.0 - weather_visibility_scale, minimum=0.0, maximum=1.0)

    depth_payload = {
        "depth_input_present": bool(config["depth_input_present"]),
        "depth_enabled": bool(depth_enabled),
        "depth_mode": depth_mode,
        "depth_min_m": float(depth_min_m),
        "depth_max_m": float(depth_max_m),
        "depth_range_m": float(depth_range_m),
        "depth_log_base": float(depth_log_base),
        "depth_bit_depth": int(depth_bit_depth),
        "depth_data_type": depth_data_type,
        "depth_quantization_levels": int(quantization_levels),
        "depth_resolution_m_at_max_est": float(depth_resolution_m_at_max_est),
        "effective_depth_max_m_est": float(effective_depth_max_m_est),
        "depth_clamp_ratio_est": float(depth_clamp_ratio_est),
    }

    optical_flow_enabled = bool(config["optical_flow_enabled"])
    velocity_direction = str(config["velocity_direction"])
    y_axis_direction = str(config["y_axis_direction"])
    frame_rate_hz = _clamp_float(
        _to_float(camera_physics.get("frame_rate_hz", 30.0), default=30.0),
        minimum=1.0,
        maximum=240.0,
    )
    dt_sec = 1.0 / frame_rate_hz
    focal_length_px = _to_non_negative_float(camera_geometry.get("fx", 0.0))
    if focal_length_px <= 0.0:
        focal_length_px = _to_non_negative_float(camera_geometry.get("fy", 0.0))
    if focal_length_px <= 0.0:
        focal_length_px = _to_non_negative_float(camera_physics.get("focal_length_px_est", 960.0))

    ego_speed_mps = float(environment["ego_speed_mps"])
    flow_reference_depth_m = _clamp_float(
        depth_min_m + max(2.0, (effective_depth_max_m_est - depth_min_m) * 0.35),
        minimum=2.0,
        maximum=max(2.0, depth_max_m),
    )
    flow_base_magnitude_px = (ego_speed_mps * dt_sec * focal_length_px) / max(flow_reference_depth_m, 0.1)
    if not optical_flow_enabled:
        flow_base_magnitude_px = 0.0
    max_flow_magnitude_px_est = flow_base_magnitude_px * 2.5

    horizontal_direction_sign = -1.0 if velocity_direction == "PREVIOUS_TO_CURRENT" else 1.0
    if velocity_direction == "DEFAULT":
        horizontal_direction_sign = 1.0
    y_axis_sign = -1.0 if y_axis_direction == "UP" else 1.0
    if y_axis_direction == "DEFAULT":
        y_axis_sign = 1.0

    horizontal_bias_px_est = horizontal_direction_sign * flow_base_magnitude_px
    vertical_bias_px_est = y_axis_sign * (flow_base_magnitude_px * 0.08)

    optical_flow_payload = {
        "optical_flow_input_present": bool(config["optical_flow_input_present"]),
        "optical_flow_enabled": bool(optical_flow_enabled),
        "velocity_direction": velocity_direction,
        "y_axis_direction": y_axis_direction,
        "flow_reference_depth_m": float(flow_reference_depth_m),
        "flow_scale_px_per_mps_est": float((dt_sec * focal_length_px) / max(flow_reference_depth_m, 0.1)),
        "mean_flow_magnitude_px_est": float(flow_base_magnitude_px),
        "max_flow_magnitude_px_est": float(max_flow_magnitude_px_est),
        "horizontal_bias_px_est": float(horizontal_bias_px_est),
        "vertical_bias_px_est": float(vertical_bias_px_est),
    }

    return depth_payload, optical_flow_payload


def _resolve_camera_projection_mode(raw: Any) -> str:
    projection = str(raw if raw is not None else "").strip().upper()
    if projection in {"RECTILINEAR", "EQUIDISTANT", "ORTHOGRAPHIC"}:
        return projection
    return "RECTILINEAR"


def _resolve_opencv_distortion_params(raw: Any) -> dict[str, float]:
    keys = ("k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6")
    values: dict[str, float] = {key: 0.0 for key in keys}
    if isinstance(raw, dict):
        for key in keys:
            values[key] = _to_float(raw.get(key, 0.0), default=0.0)
        return values
    if isinstance(raw, (list, tuple)):
        for idx, value in enumerate(raw):
            if idx >= len(keys):
                break
            values[keys[idx]] = _to_float(value, default=0.0)
    return values


def _resolve_radial_distortion_params(raw: Any) -> dict[str, Any]:
    units = "NORMALIZED"
    coefficients = {"a_0": 1.0}
    if not isinstance(raw, dict):
        return {"units": units, "coefficients": coefficients}
    units_raw = str(raw.get("units", units)).strip().upper()
    if units_raw in {"NORMALIZED", "PIXELS", "RADIANS"}:
        units = units_raw
    coeff_raw = _as_dict(raw.get("coefficients"))
    if coeff_raw:
        coefficients = {}
        for idx in range(15):
            key = f"a_{idx}"
            if key in coeff_raw:
                coefficients[key] = _to_float(coeff_raw.get(key, 0.0), default=0.0)
        if "a_0" not in coefficients:
            coefficients["a_0"] = 1.0
    return {"units": units, "coefficients": coefficients}


def _compute_opencv_edge_shift_px(
    *,
    params: dict[str, float],
    image_width_px: int,
    image_height_px: int,
) -> float:
    x = 0.82
    y = 0.82 * (float(image_height_px) / float(max(image_width_px, 1)))
    r2 = (x * x) + (y * y)
    r4 = r2 * r2
    r6 = r4 * r2
    numerator = 1.0 + (params["k1"] * r2) + (params["k2"] * r4) + (params["k3"] * r6)
    denominator = 1.0 + (params["k4"] * r2) + (params["k5"] * r4) + (params["k6"] * r6)
    if abs(denominator) < 1e-6:
        denominator = 1e-6
    radial = numerator / denominator
    x_distorted = (x * radial) + (2.0 * params["p1"] * x * y) + (params["p2"] * (r2 + (2.0 * x * x)))
    y_distorted = (y * radial) + (params["p1"] * (r2 + (2.0 * y * y))) + (2.0 * params["p2"] * x * y)
    norm_shift = math.sqrt(((x_distorted - x) ** 2) + ((y_distorted - y) ** 2))
    return float(norm_shift * (float(max(image_width_px, image_height_px)) / 2.0))


def _compute_radial_edge_shift_px(
    *,
    params: dict[str, Any],
    image_width_px: int,
    image_height_px: int,
    field_of_view_az_rad: float,
    focal_length_px: float,
) -> tuple[float, float]:
    units = str(params.get("units", "NORMALIZED")).strip().upper()
    coefficients_raw = params.get("coefficients", {})
    coefficients = coefficients_raw if isinstance(coefficients_raw, dict) else {"a_0": 1.0}
    if units == "PIXELS":
        base_r = 0.85 * (float(max(image_width_px, image_height_px)) / 2.0)
    elif units == "RADIANS":
        base_r = 0.85 * (field_of_view_az_rad / 2.0)
    else:
        units = "NORMALIZED"
        base_r = 0.85
    gain = 0.0
    for idx in range(15):
        key = f"a_{idx}"
        coeff = _to_float(coefficients.get(key, 0.0), default=0.0)
        gain += coeff * (base_r ** idx)
    delta = abs(gain - 1.0) * base_r
    if units == "PIXELS":
        edge_shift_px = delta
    elif units == "RADIANS":
        edge_shift_px = delta * max(focal_length_px, 1.0)
    else:
        edge_shift_px = delta * (float(max(image_width_px, image_height_px)) / 2.0)
    return (float(edge_shift_px), float(gain))


def _compute_camera_geometry(
    *,
    sensor_config: dict[str, Any],
    image_width_px: int,
    image_height_px: int,
) -> dict[str, Any]:
    standard_params = _as_dict(sensor_config.get("standard_params"))
    lens_params = _as_dict(sensor_config.get("lens_params"))
    if not lens_params:
        lens_params = _as_dict(standard_params.get("lens_params"))
    camera_intrinsic_params = _as_dict(lens_params.get("camera_intrinsic_params"))
    field_of_view = _as_dict(standard_params.get("field_of_view"))
    projection = _resolve_camera_projection_mode(
        sensor_config.get("projection", lens_params.get("projection", "RECTILINEAR"))
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
    field_of_view_el_rad = _to_float(
        sensor_config.get("field_of_view_el_rad", field_of_view.get("el", 0.0)),
        default=0.0,
    )
    if field_of_view_el_rad > math.pi:
        field_of_view_el_rad = math.radians(field_of_view_el_rad)
    if field_of_view_el_rad <= 0.0:
        field_of_view_el_rad = 2.0 * math.atan(
            math.tan(field_of_view_az_rad / 2.0) * (float(image_height_px) / float(max(image_width_px, 1)))
        )
    field_of_view_el_rad = _clamp_float(field_of_view_el_rad, minimum=0.1, maximum=math.pi - 0.01)
    fx = _to_float(camera_intrinsic_params.get("fx", 0.0), default=0.0)
    fy = _to_float(camera_intrinsic_params.get("fy", 0.0), default=0.0)
    cx = _to_float(camera_intrinsic_params.get("cx", float(image_width_px) / 2.0), default=float(image_width_px) / 2.0)
    cy = _to_float(
        camera_intrinsic_params.get("cy", float(image_height_px) / 2.0),
        default=float(image_height_px) / 2.0,
    )
    if fx <= 0.0:
        fx = float(image_width_px) / (2.0 * math.tan(field_of_view_az_rad / 2.0))
    if fy <= 0.0:
        fy = float(image_height_px) / (2.0 * math.tan(field_of_view_el_rad / 2.0))
    principal_point_offset_x_norm = (cx - (float(image_width_px) / 2.0)) / float(max(image_width_px, 1))
    principal_point_offset_y_norm = (cy - (float(image_height_px) / 2.0)) / float(max(image_height_px, 1))
    principal_point_offset_norm = math.sqrt(
        (principal_point_offset_x_norm * principal_point_offset_x_norm)
        + (principal_point_offset_y_norm * principal_point_offset_y_norm)
    )
    opencv_params = _resolve_opencv_distortion_params(
        sensor_config.get("opencv_distortion_params", lens_params.get("opencv_distortion_params"))
    )
    opencv_distortion_edge_shift_px = _compute_opencv_edge_shift_px(
        params=opencv_params,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
    )
    radial_params = _resolve_radial_distortion_params(
        sensor_config.get("radial_distortion_params", lens_params.get("radial_distortion_params"))
    )
    radial_distortion_edge_shift_px, radial_distortion_gain_edge = _compute_radial_edge_shift_px(
        params=radial_params,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
        field_of_view_az_rad=field_of_view_az_rad,
        focal_length_px=(fx + fy) / 2.0,
    )
    rendered_field_of_view_rad = _to_float(
        sensor_config.get("rendered_field_of_view_rad", standard_params.get("rendered_field_of_view", 0.0)),
        default=0.0,
    )
    if rendered_field_of_view_rad > math.pi:
        rendered_field_of_view_rad = math.radians(rendered_field_of_view_rad)
    rendered_field_of_view_rad = _clamp_float(rendered_field_of_view_rad, minimum=0.0, maximum=math.pi - 0.01)
    cropping = _clamp_float(
        _to_float(sensor_config.get("cropping", lens_params.get("cropping", 1.0)), default=1.0),
        minimum=0.1,
        maximum=4.0,
    )
    ortho_width_m = _clamp_float(
        _to_float(sensor_config.get("ortho_width_m", lens_params.get("ortho_width", 0.0)), default=0.0),
        minimum=0.0,
        maximum=100000.0,
    )
    meters_per_pixel_x = 0.0
    meters_per_pixel_y = 0.0
    if projection == "ORTHOGRAPHIC" and ortho_width_m > 0.0:
        meters_per_pixel_x = ortho_width_m / float(max(image_width_px, 1))
        meters_per_pixel_y = (
            ortho_width_m
            * float(image_height_px)
            / float(max(image_width_px * image_height_px, 1))
        )

    distortion_input_present = False
    for value in opencv_params.values():
        if abs(value) > 1e-9:
            distortion_input_present = True
            break
    if not distortion_input_present:
        coefficients_raw = radial_params.get("coefficients", {})
        coefficients = coefficients_raw if isinstance(coefficients_raw, dict) else {}
        for key, value in coefficients.items():
            if key == "a_0":
                if abs(_to_float(value, default=1.0) - 1.0) > 1e-9:
                    distortion_input_present = True
                    break
            elif abs(_to_float(value, default=0.0)) > 1e-9:
                distortion_input_present = True
                break

    geometry_input_present = (
        bool(lens_params)
        or bool(camera_intrinsic_params)
        or ("projection" in sensor_config)
        or ("projection" in lens_params)
        or ("radial_distortion_params" in sensor_config)
        or ("radial_distortion_params" in lens_params)
        or ("opencv_distortion_params" in sensor_config)
        or ("opencv_distortion_params" in lens_params)
        or ("cropping" in sensor_config)
        or ("cropping" in lens_params)
        or ("rendered_field_of_view" in standard_params)
        or ("rendered_field_of_view_rad" in sensor_config)
        or ("field_of_view" in standard_params)
        or ("field_of_view_deg" in sensor_config)
        or ("field_of_view_az_rad" in sensor_config)
        or ("field_of_view_el_rad" in sensor_config)
    )

    return {
        "geometry_input_present": bool(geometry_input_present),
        "projection": projection,
        "field_of_view_az_rad": float(field_of_view_az_rad),
        "field_of_view_el_rad": float(field_of_view_el_rad),
        "rendered_field_of_view_rad": float(rendered_field_of_view_rad),
        "cropping": float(cropping),
        "fx": float(fx),
        "fy": float(fy),
        "cx": float(cx),
        "cy": float(cy),
        "principal_point_offset_x_norm": float(principal_point_offset_x_norm),
        "principal_point_offset_y_norm": float(principal_point_offset_y_norm),
        "principal_point_offset_norm": float(principal_point_offset_norm),
        "opencv_distortion_params": opencv_params,
        "opencv_distortion_edge_shift_px_est": float(opencv_distortion_edge_shift_px),
        "radial_distortion_units": str(radial_params.get("units", "NORMALIZED")),
        "radial_distortion_coefficients": radial_params.get("coefficients", {"a_0": 1.0}),
        "radial_distortion_gain_edge": float(radial_distortion_gain_edge),
        "radial_distortion_edge_shift_px_est": float(radial_distortion_edge_shift_px),
        "distortion_input_present": bool(distortion_input_present),
        "distortion_edge_shift_px_est": float(opencv_distortion_edge_shift_px + radial_distortion_edge_shift_px),
        "ortho_width_m": float(ortho_width_m),
        "ortho_meters_per_pixel_x": float(meters_per_pixel_x),
        "ortho_meters_per_pixel_y": float(meters_per_pixel_y),
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
        camera_geometry = _compute_camera_geometry(
            sensor_config=sensor_config,
            image_width_px=image_width_px,
            image_height_px=image_height_px,
        )
        if bool(camera_geometry.get("geometry_input_present", False)):
            distortion_edge_shift_px_est = _to_non_negative_float(
                camera_geometry.get("distortion_edge_shift_px_est", 0.0)
            )
            distortion_visibility_penalty = min(
                0.25,
                distortion_edge_shift_px_est / float(max(image_width_px, image_height_px, 1)),
            )
            cropping = _to_float(camera_geometry.get("cropping", 1.0), default=1.0)
            cropping_penalty = min(0.25, abs(cropping - 1.0) * 0.12)
            visibility_score *= (1.0 - distortion_visibility_penalty - cropping_penalty)
            visibility_score = _clamp_float(visibility_score, minimum=0.0, maximum=1.0)
            visible_actor_count = int(round(float(len(actors)) * visibility_score))
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
        camera_postprocess = _compute_camera_postprocess(
            sensor_config=sensor_config,
            environment=environment,
            image_width_px=image_width_px,
            image_height_px=image_height_px,
            camera_physics=camera_physics,
        )
        if bool(camera_postprocess.get("postprocess_input_present", False)):
            camera_noise_stddev_px += _to_non_negative_float(
                camera_postprocess.get("camera_noise_stddev_px_delta", 0.0)
            )
            dynamic_range_stops += _to_float(
                camera_postprocess.get("dynamic_range_stops_delta", 0.0),
                default=0.0,
            )
            visibility_score *= _clamp_float(
                _to_float(camera_postprocess.get("postprocess_visibility_scale", 1.0), default=1.0),
                minimum=0.0,
                maximum=1.0,
            )
            visibility_score = _clamp_float(visibility_score, minimum=0.0, maximum=1.0)
            visible_actor_count = int(round(float(len(actors)) * visibility_score))
        camera_depth, camera_optical_flow_2d = _compute_camera_depth_and_optical_flow(
            sensor_config=sensor_config,
            environment=environment,
            camera_physics=camera_physics,
            camera_geometry=camera_geometry,
        )
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
            "camera_geometry": camera_geometry,
            "camera_physics": camera_physics,
            "camera_postprocess": camera_postprocess,
            "camera_depth": camera_depth,
            "camera_optical_flow_2d": camera_optical_flow_2d,
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
        sensor_params = _as_dict(sensor_config.get("sensor_params"))
        lidar_params = _as_dict(sensor_params.get("lidar_params"))
        points_per_actor = max(
            1,
            int(
                round(
                    _to_float(
                        sensor_config.get(
                            "points_per_actor",
                            lidar_params.get("points_per_actor", 50),
                        ),
                        default=50.0,
                    )
                )
            ),
        )
        detection_sensitivity = _clamp_float(
            _to_float(
                sensor_config.get(
                    "detection_sensitivity",
                    lidar_params.get("detection_sensitivity", 1.0),
                ),
                default=1.0,
            ),
            minimum=0.5,
            maximum=1.5,
        )
        attenuation_sensitivity = _clamp_float(
            _to_float(
                sensor_config.get(
                    "attenuation_sensitivity",
                    lidar_params.get("attenuation_sensitivity", 1.0),
                ),
                default=1.0,
            ),
            minimum=0.5,
            maximum=2.0,
        )
        returns_per_laser_bias = int(
            round(
                _to_float(
                    sensor_config.get(
                        "returns_per_laser_bias",
                        lidar_params.get("returns_per_laser_bias", 0.0),
                    ),
                    default=0.0,
                )
            )
        )
        returns_per_laser_bias = max(-4, min(4, returns_per_laser_bias))
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        score = int(profile["score"])
        lidar_point_scale = float(profile["lidar_point_scale"])
        base_point_count = int(round(len(actors) * points_per_actor * lidar_point_scale))
        weather_detection_penalty = (
            (0.5 * fog_density) + (0.3 * precipitation_intensity)
        ) / max(0.1, detection_sensitivity)
        weather_detection_ratio = _clamp_float(
            1.0 - weather_detection_penalty,
            minimum=0.15,
            maximum=1.0,
        )
        point_count = int(round(float(base_point_count) * weather_detection_ratio))
        max_range_m = float(sensor_config.get("max_range_m", 120.0))
        range_weather_penalty = (
            (0.35 * fog_density) + (0.2 * precipitation_intensity)
        ) * attenuation_sensitivity
        effective_max_range_m = max(
            10.0,
            max_range_m * (1.0 - range_weather_penalty),
        )
        returns_weather_penalty_steps = int(round((fog_density + precipitation_intensity) * 1.5))
        returns_per_laser = max(1, score - returns_weather_penalty_steps + returns_per_laser_bias)
        return {
            "modality": "lidar",
            "channel_count": int(sensor_config.get("channel_count", 64)),
            "max_range_m": max_range_m,
            "effective_max_range_m": float(effective_max_range_m),
            "point_count": int(point_count),
            "returns_per_laser": int(returns_per_laser),
            "intensity_model": "stub_linear",
            "detection_ratio": float(weather_detection_ratio),
            "detection_sensitivity": float(detection_sensitivity),
            "attenuation_sensitivity": float(attenuation_sensitivity),
            "returns_per_laser_bias": int(returns_per_laser_bias),
            "weather_detection_penalty": float(weather_detection_penalty),
            "range_weather_penalty": float(range_weather_penalty),
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
        sensor_params = _as_dict(sensor_config.get("sensor_params"))
        radar_params = _as_dict(sensor_params.get("radar_params"))
        detection_sensitivity = _clamp_float(
            _to_float(
                sensor_config.get(
                    "detection_sensitivity",
                    radar_params.get("detection_sensitivity", 1.0),
                ),
                default=1.0,
            ),
            minimum=0.5,
            maximum=1.5,
        )
        clutter_sensitivity = _clamp_float(
            _to_float(
                sensor_config.get(
                    "clutter_sensitivity",
                    radar_params.get("clutter_sensitivity", 1.0),
                ),
                default=1.0,
            ),
            minimum=0.5,
            maximum=2.0,
        )
        false_positive_rate_bias = _clamp_float(
            _to_float(
                sensor_config.get(
                    "false_positive_rate_bias",
                    radar_params.get("false_positive_rate_bias", 0.0),
                ),
                default=0.0,
            ),
            minimum=-0.3,
            maximum=0.3,
        )
        profile = FIDELITY_TIER_PROFILE[fidelity_tier]
        base_false_positive_rate = float(profile["radar_false_positive_rate"])
        false_positive_rate = _clamp_float(
            base_false_positive_rate
            + (((0.08 * precipitation_intensity) + (0.05 * fog_density)) * clutter_sensitivity)
            + false_positive_rate_bias,
            minimum=0.0,
            maximum=0.9,
        )
        target_detection_penalty = (
            (0.25 * fog_density) + (0.1 * precipitation_intensity)
        ) / max(0.1, detection_sensitivity)
        target_detection_ratio = _clamp_float(
            1.0 - target_detection_penalty,
            minimum=0.4,
            maximum=1.0,
        )
        target_count = int(round(float(len(actors)) * target_detection_ratio))
        radar_clutter_index = _clamp_float(
            ((0.5 * precipitation_intensity) + (0.35 * fog_density)) * clutter_sensitivity,
            minimum=0.0,
            maximum=1.0,
        )
        ghost_target_count = 0
        if radar_clutter_index > 0.0:
            ghost_scale = 0.75 + max(0.0, false_positive_rate_bias * 2.0)
            ghost_target_count = max(1, int(round(float(len(actors)) * radar_clutter_index * ghost_scale)))
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
            "detection_sensitivity": float(detection_sensitivity),
            "clutter_sensitivity": float(clutter_sensitivity),
            "false_positive_rate_bias": float(false_positive_rate_bias),
            "target_detection_penalty": float(target_detection_penalty),
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
    camera_exposure_range_total = 0.0
    camera_exposure_range_multiplier_total = 0.0
    camera_auto_exposure_mode_counts: dict[str, int] = {}
    camera_auto_exposure_mode_effective_counts: dict[str, int] = {}
    camera_rolling_shutter_total_delay_ms_total = 0.0
    camera_normalized_total_noise_total = 0.0
    camera_distortion_edge_shift_px_total = 0.0
    camera_principal_point_offset_norm_total = 0.0
    camera_effective_focal_length_px_total = 0.0
    camera_projection_mode_counts: dict[str, int] = {}
    camera_gain_db_total = 0.0
    camera_gamma_total = 0.0
    camera_white_balance_kelvin_total = 0.0
    camera_vignetting_edge_darkening_total = 0.0
    camera_bloom_halo_strength_total = 0.0
    camera_chromatic_aberration_shift_px_total = 0.0
    camera_black_level_lift_norm_total = 0.0
    camera_auto_black_level_stddev_to_subtract_total = 0.0
    camera_saturation_rgb_avg_total = 0.0
    camera_saturation_effective_scale_total = 0.0
    camera_piecewise_linear_mapping_enabled_frame_count = 0
    camera_piecewise_linear_mapping_point_count_total = 0.0
    camera_piecewise_linear_mapping_dynamic_range_scale_total = 0.0
    camera_piecewise_linear_mapping_midtone_gain_total = 0.0
    camera_color_space_counts: dict[str, int] = {}
    camera_output_data_type_counts: dict[str, int] = {}
    camera_tonemapper_disabled_frame_count = 0
    camera_bloom_level_counts: dict[str, int] = {}
    camera_depth_enabled_frame_count = 0
    camera_depth_min_m_total = 0.0
    camera_depth_max_m_total = 0.0
    camera_depth_bit_depth_total = 0
    camera_depth_mode_counts: dict[str, int] = {}
    camera_optical_flow_enabled_frame_count = 0
    camera_optical_flow_magnitude_px_total = 0.0
    camera_optical_flow_velocity_direction_counts: dict[str, int] = {}
    camera_optical_flow_y_axis_direction_counts: dict[str, int] = {}
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
            camera_exposure_range_total += _to_float(camera_physics.get("exposure_range", 0.0), default=0.0)
            camera_exposure_range_multiplier_total += _to_non_negative_float(
                camera_physics.get("exposure_range_multiplier", 0.0)
            )
            auto_exposure_mode = str(camera_physics.get("auto_exposure_mode", "")).strip().upper()
            if auto_exposure_mode:
                camera_auto_exposure_mode_counts[auto_exposure_mode] = (
                    camera_auto_exposure_mode_counts.get(auto_exposure_mode, 0) + 1
                )
            auto_exposure_mode_effective = str(
                camera_physics.get("auto_exposure_mode_effective", "")
            ).strip().upper()
            if auto_exposure_mode_effective:
                camera_auto_exposure_mode_effective_counts[auto_exposure_mode_effective] = (
                    camera_auto_exposure_mode_effective_counts.get(auto_exposure_mode_effective, 0) + 1
                )
            camera_rolling_shutter_total_delay_ms_total += _to_non_negative_float(
                camera_physics.get("rolling_shutter_total_delay_ms", 0.0)
            )
            camera_normalized_total_noise_total += _to_non_negative_float(
                camera_physics.get("normalized_total_noise", 0.0)
            )
            camera_geometry = payload.get("camera_geometry", {})
            if not isinstance(camera_geometry, dict):
                camera_geometry = {}
            camera_distortion_edge_shift_px_total += _to_non_negative_float(
                camera_geometry.get("distortion_edge_shift_px_est", 0.0)
            )
            camera_principal_point_offset_norm_total += _to_non_negative_float(
                camera_geometry.get("principal_point_offset_norm", 0.0)
            )
            fx = _to_non_negative_float(camera_geometry.get("fx", 0.0))
            fy = _to_non_negative_float(camera_geometry.get("fy", 0.0))
            if fx > 0.0 and fy > 0.0:
                camera_effective_focal_length_px_total += (fx + fy) / 2.0
            elif fx > 0.0:
                camera_effective_focal_length_px_total += fx
            elif fy > 0.0:
                camera_effective_focal_length_px_total += fy
            projection = str(camera_geometry.get("projection", "")).strip().upper()
            if projection:
                camera_projection_mode_counts[projection] = camera_projection_mode_counts.get(projection, 0) + 1
            camera_postprocess = payload.get("camera_postprocess", {})
            if not isinstance(camera_postprocess, dict):
                camera_postprocess = {}
            camera_gain_db_total += _to_float(camera_postprocess.get("gain_db", 0.0), default=0.0)
            camera_gamma_total += _to_non_negative_float(camera_postprocess.get("gamma", 0.0))
            camera_white_balance_kelvin_total += _to_non_negative_float(
                camera_postprocess.get("white_balance_kelvin", 0.0)
            )
            camera_vignetting_edge_darkening_total += _to_non_negative_float(
                camera_postprocess.get("vignetting_edge_darkening", 0.0)
            )
            camera_bloom_halo_strength_total += _to_non_negative_float(
                camera_postprocess.get("bloom_halo_strength", 0.0)
            )
            camera_chromatic_aberration_shift_px_total += _to_non_negative_float(
                camera_postprocess.get("chromatic_aberration_shift_px_est", 0.0)
            )
            camera_black_level_lift_norm_total += _to_non_negative_float(
                camera_postprocess.get("black_level_lift_norm", 0.0)
            )
            camera_auto_black_level_stddev_to_subtract_total += _to_non_negative_float(
                camera_postprocess.get("auto_black_level_stddev_to_subtract", 0.0)
            )
            camera_saturation_rgb_avg_total += _to_non_negative_float(
                camera_postprocess.get("saturation_rgb_avg", 0.0)
            )
            camera_saturation_effective_scale_total += _to_non_negative_float(
                camera_postprocess.get("saturation_effective_scale", 0.0)
            )
            if bool(camera_postprocess.get("piecewise_linear_mapping_present", False)):
                camera_piecewise_linear_mapping_enabled_frame_count += 1
            camera_piecewise_linear_mapping_point_count_total += _to_non_negative_float(
                camera_postprocess.get("piecewise_linear_mapping_point_count", 0.0)
            )
            camera_piecewise_linear_mapping_dynamic_range_scale_total += _to_non_negative_float(
                camera_postprocess.get("piecewise_linear_mapping_dynamic_range_scale", 0.0)
            )
            camera_piecewise_linear_mapping_midtone_gain_total += _to_non_negative_float(
                camera_postprocess.get("piecewise_linear_mapping_midtone_gain", 0.0)
            )
            color_space = str(camera_postprocess.get("color_space", "")).strip().upper()
            if color_space:
                camera_color_space_counts[color_space] = camera_color_space_counts.get(color_space, 0) + 1
            output_data_type = str(camera_postprocess.get("output_data_type", "")).strip().upper()
            if output_data_type:
                camera_output_data_type_counts[output_data_type] = (
                    camera_output_data_type_counts.get(output_data_type, 0) + 1
                )
            if bool(camera_postprocess.get("disable_tonemapper", False)):
                camera_tonemapper_disabled_frame_count += 1
            bloom_level = str(camera_postprocess.get("bloom_level", "")).strip().upper()
            if bloom_level:
                camera_bloom_level_counts[bloom_level] = camera_bloom_level_counts.get(bloom_level, 0) + 1
            camera_depth = payload.get("camera_depth", {})
            if not isinstance(camera_depth, dict):
                camera_depth = {}
            if bool(camera_depth.get("depth_enabled", False)):
                camera_depth_enabled_frame_count += 1
            camera_depth_min_m_total += _to_non_negative_float(camera_depth.get("depth_min_m", 0.0))
            camera_depth_max_m_total += _to_non_negative_float(camera_depth.get("depth_max_m", 0.0))
            camera_depth_bit_depth_total += _to_non_negative_int(camera_depth.get("depth_bit_depth", 0))
            depth_mode = str(camera_depth.get("depth_mode", "")).strip().upper()
            if depth_mode:
                camera_depth_mode_counts[depth_mode] = camera_depth_mode_counts.get(depth_mode, 0) + 1
            camera_optical_flow = payload.get("camera_optical_flow_2d", {})
            if not isinstance(camera_optical_flow, dict):
                camera_optical_flow = {}
            if bool(camera_optical_flow.get("optical_flow_enabled", False)):
                camera_optical_flow_enabled_frame_count += 1
            camera_optical_flow_magnitude_px_total += _to_non_negative_float(
                camera_optical_flow.get("mean_flow_magnitude_px_est", 0.0)
            )
            velocity_direction = str(camera_optical_flow.get("velocity_direction", "")).strip().upper()
            if velocity_direction:
                camera_optical_flow_velocity_direction_counts[velocity_direction] = (
                    camera_optical_flow_velocity_direction_counts.get(velocity_direction, 0) + 1
                )
            y_axis_direction = str(camera_optical_flow.get("y_axis_direction", "")).strip().upper()
            if y_axis_direction:
                camera_optical_flow_y_axis_direction_counts[y_axis_direction] = (
                    camera_optical_flow_y_axis_direction_counts.get(y_axis_direction, 0) + 1
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
    camera_exposure_range_avg = (
        camera_exposure_range_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_exposure_range_multiplier_avg = (
        camera_exposure_range_multiplier_total / float(camera_frame_count)
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
    camera_effective_focal_length_px_avg = (
        camera_effective_focal_length_px_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_gain_db_avg = (
        camera_gain_db_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_gamma_avg = (
        camera_gamma_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_white_balance_kelvin_avg = (
        camera_white_balance_kelvin_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_vignetting_edge_darkening_avg = (
        camera_vignetting_edge_darkening_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_bloom_halo_strength_avg = (
        camera_bloom_halo_strength_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_chromatic_aberration_shift_px_avg = (
        camera_chromatic_aberration_shift_px_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_black_level_lift_norm_avg = (
        camera_black_level_lift_norm_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_auto_black_level_stddev_to_subtract_avg = (
        camera_auto_black_level_stddev_to_subtract_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_saturation_rgb_avg = (
        camera_saturation_rgb_avg_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_saturation_effective_scale_avg = (
        camera_saturation_effective_scale_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_piecewise_linear_mapping_point_count_avg = (
        camera_piecewise_linear_mapping_point_count_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_piecewise_linear_mapping_dynamic_range_scale_avg = (
        camera_piecewise_linear_mapping_dynamic_range_scale_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_piecewise_linear_mapping_midtone_gain_avg = (
        camera_piecewise_linear_mapping_midtone_gain_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_depth_min_m_avg = (
        camera_depth_min_m_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_depth_max_m_avg = (
        camera_depth_max_m_total / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_depth_bit_depth_avg = (
        float(camera_depth_bit_depth_total) / float(camera_frame_count)
        if camera_frame_count > 0
        else 0.0
    )
    camera_optical_flow_magnitude_px_avg = (
        camera_optical_flow_magnitude_px_total / float(camera_frame_count)
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
        "camera_exposure_range_avg": float(camera_exposure_range_avg),
        "camera_exposure_range_multiplier_avg": float(camera_exposure_range_multiplier_avg),
        "camera_auto_exposure_mode_counts": {
            key: camera_auto_exposure_mode_counts[key] for key in sorted(camera_auto_exposure_mode_counts.keys())
        },
        "camera_auto_exposure_mode_effective_counts": {
            key: camera_auto_exposure_mode_effective_counts[key]
            for key in sorted(camera_auto_exposure_mode_effective_counts.keys())
        },
        "camera_rolling_shutter_total_delay_ms_avg": float(camera_rolling_shutter_total_delay_ms_avg),
        "camera_normalized_total_noise_avg": float(camera_normalized_total_noise_avg),
        "camera_distortion_edge_shift_px_avg": float(camera_distortion_edge_shift_px_avg),
        "camera_principal_point_offset_norm_avg": float(camera_principal_point_offset_norm_avg),
        "camera_effective_focal_length_px_avg": float(camera_effective_focal_length_px_avg),
        "camera_projection_mode_counts": {
            key: camera_projection_mode_counts[key] for key in sorted(camera_projection_mode_counts.keys())
        },
        "camera_gain_db_avg": float(camera_gain_db_avg),
        "camera_gamma_avg": float(camera_gamma_avg),
        "camera_white_balance_kelvin_avg": float(camera_white_balance_kelvin_avg),
        "camera_vignetting_edge_darkening_avg": float(camera_vignetting_edge_darkening_avg),
        "camera_bloom_halo_strength_avg": float(camera_bloom_halo_strength_avg),
        "camera_chromatic_aberration_shift_px_avg": float(camera_chromatic_aberration_shift_px_avg),
        "camera_black_level_lift_norm_avg": float(camera_black_level_lift_norm_avg),
        "camera_auto_black_level_stddev_to_subtract_avg": float(
            camera_auto_black_level_stddev_to_subtract_avg
        ),
        "camera_saturation_rgb_avg": float(camera_saturation_rgb_avg),
        "camera_saturation_effective_scale_avg": float(camera_saturation_effective_scale_avg),
        "camera_piecewise_linear_mapping_enabled_frame_count": int(
            camera_piecewise_linear_mapping_enabled_frame_count
        ),
        "camera_piecewise_linear_mapping_point_count_avg": float(
            camera_piecewise_linear_mapping_point_count_avg
        ),
        "camera_piecewise_linear_mapping_dynamic_range_scale_avg": float(
            camera_piecewise_linear_mapping_dynamic_range_scale_avg
        ),
        "camera_piecewise_linear_mapping_midtone_gain_avg": float(
            camera_piecewise_linear_mapping_midtone_gain_avg
        ),
        "camera_color_space_counts": {
            key: camera_color_space_counts[key] for key in sorted(camera_color_space_counts.keys())
        },
        "camera_output_data_type_counts": {
            key: camera_output_data_type_counts[key]
            for key in sorted(camera_output_data_type_counts.keys())
        },
        "camera_tonemapper_disabled_frame_count": int(camera_tonemapper_disabled_frame_count),
        "camera_bloom_level_counts": {
            key: camera_bloom_level_counts[key] for key in sorted(camera_bloom_level_counts.keys())
        },
        "camera_depth_enabled_frame_count": int(camera_depth_enabled_frame_count),
        "camera_depth_min_m_avg": float(camera_depth_min_m_avg),
        "camera_depth_max_m_avg": float(camera_depth_max_m_avg),
        "camera_depth_bit_depth_avg": float(camera_depth_bit_depth_avg),
        "camera_depth_mode_counts": {
            key: camera_depth_mode_counts[key] for key in sorted(camera_depth_mode_counts.keys())
        },
        "camera_optical_flow_enabled_frame_count": int(camera_optical_flow_enabled_frame_count),
        "camera_optical_flow_magnitude_px_avg": float(camera_optical_flow_magnitude_px_avg),
        "camera_optical_flow_velocity_direction_counts": {
            key: camera_optical_flow_velocity_direction_counts[key]
            for key in sorted(camera_optical_flow_velocity_direction_counts.keys())
        },
        "camera_optical_flow_y_axis_direction_counts": {
            key: camera_optical_flow_y_axis_direction_counts[key]
            for key in sorted(camera_optical_flow_y_axis_direction_counts.keys())
        },
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
