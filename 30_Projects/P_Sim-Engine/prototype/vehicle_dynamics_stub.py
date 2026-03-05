#!/usr/bin/env python3
"""Run a minimal ego dynamics/control contract simulation."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


VEHICLE_PROFILE_SCHEMA_VERSION_V0 = "vehicle_profile_v0"
CONTROL_SEQUENCE_SCHEMA_VERSION_V0 = "control_sequence_v0"
ERROR_SOURCE = "vehicle_dynamics_stub.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run minimal ego dynamics contract simulation")
    parser.add_argument("--vehicle-profile", required=True, help="Vehicle profile JSON path")
    parser.add_argument("--control-sequence", required=True, help="Control sequence JSON path")
    parser.add_argument("--out", required=True, help="Output simulation JSON path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _parse_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if float(value) == 1.0:
            return True
        if float(value) == 0.0:
            return False
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    raise ValueError(f"{field} must be a boolean")


def _validate_vehicle_profile(payload: dict[str, Any]) -> dict[str, float]:
    if str(payload.get("profile_schema_version", "")) != VEHICLE_PROFILE_SCHEMA_VERSION_V0:
        raise ValueError(
            "profile_schema_version must be "
            f"{VEHICLE_PROFILE_SCHEMA_VERSION_V0}"
        )
    required = ["wheelbase_m", "max_accel_mps2", "max_decel_mps2", "max_speed_mps"]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"vehicle profile missing required keys: {missing}")
    wheelbase = float(payload["wheelbase_m"])
    max_accel = float(payload["max_accel_mps2"])
    max_decel = float(payload["max_decel_mps2"])
    max_speed = float(payload["max_speed_mps"])
    mass_kg = float(payload.get("mass_kg", 1500.0))
    rolling_resistance_coeff = float(payload.get("rolling_resistance_coeff", 0.0))
    drag_coefficient = float(payload.get("drag_coefficient", 0.0))
    frontal_area_m2 = float(payload.get("frontal_area_m2", 2.2))
    air_density_kgpm3 = float(payload.get("air_density_kgpm3", 1.225))
    front_axle_to_cg_m = float(payload.get("front_axle_to_cg_m", wheelbase / 2.0))
    rear_axle_to_cg_m = float(payload.get("rear_axle_to_cg_m", wheelbase - front_axle_to_cg_m))
    yaw_inertia_kgm2 = float(
        payload.get(
            "yaw_inertia_kgm2",
            max(1.0, mass_kg * (front_axle_to_cg_m * front_axle_to_cg_m + rear_axle_to_cg_m * rear_axle_to_cg_m) / 2.0),
        )
    )
    cornering_stiffness_front_nprad = float(payload.get("cornering_stiffness_front_nprad", 80000.0))
    cornering_stiffness_rear_nprad = float(payload.get("cornering_stiffness_rear_nprad", 80000.0))
    tire_friction_coeff = float(payload.get("tire_friction_coeff", 1.0))
    if wheelbase <= 0:
        raise ValueError("wheelbase_m must be > 0")
    if max_accel <= 0 or max_decel <= 0 or max_speed <= 0:
        raise ValueError("max_accel_mps2/max_decel_mps2/max_speed_mps must be > 0")
    if mass_kg <= 0:
        raise ValueError("mass_kg must be > 0")
    if rolling_resistance_coeff < 0:
        raise ValueError("rolling_resistance_coeff must be >= 0")
    if drag_coefficient < 0:
        raise ValueError("drag_coefficient must be >= 0")
    if frontal_area_m2 <= 0:
        raise ValueError("frontal_area_m2 must be > 0")
    if air_density_kgpm3 <= 0:
        raise ValueError("air_density_kgpm3 must be > 0")
    if front_axle_to_cg_m <= 0:
        raise ValueError("front_axle_to_cg_m must be > 0")
    if rear_axle_to_cg_m <= 0:
        raise ValueError("rear_axle_to_cg_m must be > 0")
    if yaw_inertia_kgm2 <= 0:
        raise ValueError("yaw_inertia_kgm2 must be > 0")
    if cornering_stiffness_front_nprad <= 0:
        raise ValueError("cornering_stiffness_front_nprad must be > 0")
    if cornering_stiffness_rear_nprad <= 0:
        raise ValueError("cornering_stiffness_rear_nprad must be > 0")
    if tire_friction_coeff <= 0:
        raise ValueError("tire_friction_coeff must be > 0")
    if abs((front_axle_to_cg_m + rear_axle_to_cg_m) - wheelbase) > 1e-6:
        raise ValueError("front_axle_to_cg_m + rear_axle_to_cg_m must equal wheelbase_m")
    return {
        "wheelbase_m": wheelbase,
        "max_accel_mps2": max_accel,
        "max_decel_mps2": max_decel,
        "max_speed_mps": max_speed,
        "mass_kg": mass_kg,
        "rolling_resistance_coeff": rolling_resistance_coeff,
        "drag_coefficient": drag_coefficient,
        "frontal_area_m2": frontal_area_m2,
        "air_density_kgpm3": air_density_kgpm3,
        "front_axle_to_cg_m": front_axle_to_cg_m,
        "rear_axle_to_cg_m": rear_axle_to_cg_m,
        "yaw_inertia_kgm2": yaw_inertia_kgm2,
        "cornering_stiffness_front_nprad": cornering_stiffness_front_nprad,
        "cornering_stiffness_rear_nprad": cornering_stiffness_rear_nprad,
        "tire_friction_coeff": tire_friction_coeff,
    }


def _validate_control_sequence(
    payload: dict[str, Any],
) -> tuple[
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    bool,
    bool,
    list[dict[str, float | None]],
]:
    if str(payload.get("sequence_schema_version", "")) != CONTROL_SEQUENCE_SCHEMA_VERSION_V0:
        raise ValueError(
            "sequence_schema_version must be "
            f"{CONTROL_SEQUENCE_SCHEMA_VERSION_V0}"
        )
    dt_sec = float(payload.get("dt_sec", 0.0))
    if dt_sec <= 0:
        raise ValueError("dt_sec must be > 0")
    initial_speed_mps = float(payload.get("initial_speed_mps", 0.0))
    initial_position_m = float(payload.get("initial_position_m", 0.0))
    initial_heading_deg = float(payload.get("initial_heading_deg", 0.0))
    initial_lateral_position_m = float(payload.get("initial_lateral_position_m", 0.0))
    initial_lateral_velocity_mps = float(payload.get("initial_lateral_velocity_mps", 0.0))
    initial_yaw_rate_rps = float(payload.get("initial_yaw_rate_rps", 0.0))
    enable_planar_kinematics = _parse_bool(
        payload.get("enable_planar_kinematics", False),
        field="enable_planar_kinematics",
    )
    enable_dynamic_bicycle = _parse_bool(
        payload.get("enable_dynamic_bicycle", False),
        field="enable_dynamic_bicycle",
    )
    default_target_speed_mps_raw = payload.get("default_target_speed_mps", None)
    default_target_speed_mps: float | None = None
    if default_target_speed_mps_raw is not None:
        try:
            default_target_speed_mps = float(default_target_speed_mps_raw)
        except (TypeError, ValueError):
            raise ValueError("default_target_speed_mps must be a number") from None
        if default_target_speed_mps < 0:
            raise ValueError("default_target_speed_mps must be >= 0")
    try:
        default_road_grade_percent = float(payload.get("default_road_grade_percent", 0.0))
    except (TypeError, ValueError):
        raise ValueError("default_road_grade_percent must be a number") from None
    try:
        default_surface_friction_scale = float(payload.get("default_surface_friction_scale", 1.0))
    except (TypeError, ValueError):
        raise ValueError("default_surface_friction_scale must be a number") from None
    if initial_speed_mps < 0:
        raise ValueError("initial_speed_mps must be >= 0")
    if enable_dynamic_bicycle and not enable_planar_kinematics:
        raise ValueError("enable_dynamic_bicycle requires enable_planar_kinematics=true")
    if default_road_grade_percent <= -100 or default_road_grade_percent >= 100:
        raise ValueError("default_road_grade_percent must be between -100 and 100")
    if default_surface_friction_scale <= 0:
        raise ValueError("default_surface_friction_scale must be > 0")
    commands = payload.get("commands", [])
    if not isinstance(commands, list) or len(commands) == 0:
        raise ValueError("commands must be a non-empty list")
    normalized: list[dict[str, float | None]] = []
    for idx, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            raise ValueError(f"commands[{idx}] must be an object")
        throttle = float(cmd.get("throttle", 0.0))
        brake = float(cmd.get("brake", 0.0))
        steering_angle_deg = float(cmd.get("steering_angle_deg", 0.0))
        target_speed_mps_raw = cmd.get("target_speed_mps", default_target_speed_mps)
        target_speed_mps: float | None = None
        if target_speed_mps_raw is not None:
            try:
                target_speed_mps = float(target_speed_mps_raw)
            except (TypeError, ValueError):
                raise ValueError(f"commands[{idx}].target_speed_mps must be a number") from None
            if target_speed_mps < 0:
                raise ValueError(f"commands[{idx}].target_speed_mps must be >= 0")
        try:
            road_grade_percent = float(cmd.get("road_grade_percent", default_road_grade_percent))
        except (TypeError, ValueError):
            raise ValueError(f"commands[{idx}].road_grade_percent must be a number") from None
        try:
            surface_friction_scale = float(
                cmd.get("surface_friction_scale", default_surface_friction_scale)
            )
        except (TypeError, ValueError):
            raise ValueError(f"commands[{idx}].surface_friction_scale must be a number") from None
        if throttle < 0 or brake < 0:
            raise ValueError(f"commands[{idx}] throttle/brake must be >= 0")
        if abs(steering_angle_deg) >= 89.9:
            raise ValueError(f"commands[{idx}].steering_angle_deg magnitude must be < 89.9")
        if road_grade_percent <= -100 or road_grade_percent >= 100:
            raise ValueError(f"commands[{idx}].road_grade_percent must be between -100 and 100")
        if surface_friction_scale <= 0:
            raise ValueError(f"commands[{idx}].surface_friction_scale must be > 0")
        normalized.append(
            {
                "throttle": min(throttle, 1.0),
                "brake": min(brake, 1.0),
                "steering_angle_deg": steering_angle_deg,
                "road_grade_percent": road_grade_percent,
                "surface_friction_scale": surface_friction_scale,
                "target_speed_mps": target_speed_mps,
            }
        )
    return (
        dt_sec,
        initial_position_m,
        initial_speed_mps,
        initial_heading_deg,
        initial_lateral_position_m,
        initial_lateral_velocity_mps,
        initial_yaw_rate_rps,
        enable_planar_kinematics,
        enable_dynamic_bicycle,
        normalized,
    )


def run_simulation(
    *,
    vehicle_profile: dict[str, float],
    dt_sec: float,
    initial_position_m: float,
    initial_speed_mps: float,
    initial_heading_deg: float,
    initial_lateral_position_m: float,
    initial_lateral_velocity_mps: float,
    initial_yaw_rate_rps: float,
    enable_planar_kinematics: bool,
    enable_dynamic_bicycle: bool,
    commands: list[dict[str, float | None]],
) -> dict[str, Any]:
    max_speed = float(vehicle_profile["max_speed_mps"])
    speed_mps = max(0.0, min(max_speed, float(initial_speed_mps)))
    position_m = float(initial_position_m)
    heading_rad = math.radians(float(initial_heading_deg))
    x_m = float(initial_position_m)
    y_m = float(initial_lateral_position_m)
    lateral_velocity_mps = float(initial_lateral_velocity_mps)
    yaw_rate_rps = float(initial_yaw_rate_rps)
    trace: list[dict[str, float]] = []
    max_accel = float(vehicle_profile["max_accel_mps2"])
    max_decel = float(vehicle_profile["max_decel_mps2"])
    wheelbase_m = float(vehicle_profile["wheelbase_m"])
    mass_kg = float(vehicle_profile["mass_kg"])
    rolling_resistance_coeff = float(vehicle_profile["rolling_resistance_coeff"])
    drag_coefficient = float(vehicle_profile["drag_coefficient"])
    frontal_area_m2 = float(vehicle_profile["frontal_area_m2"])
    air_density_kgpm3 = float(vehicle_profile["air_density_kgpm3"])
    front_axle_to_cg_m = float(vehicle_profile["front_axle_to_cg_m"])
    rear_axle_to_cg_m = float(vehicle_profile["rear_axle_to_cg_m"])
    yaw_inertia_kgm2 = float(vehicle_profile["yaw_inertia_kgm2"])
    cornering_stiffness_front_nprad = float(vehicle_profile["cornering_stiffness_front_nprad"])
    cornering_stiffness_rear_nprad = float(vehicle_profile["cornering_stiffness_rear_nprad"])
    tire_friction_coeff = float(vehicle_profile["tire_friction_coeff"])
    gravity_mps2 = 9.80665

    for idx, cmd in enumerate(commands):
        throttle = float(cmd["throttle"])
        brake = float(cmd["brake"])
        steering_angle_deg = float(cmd.get("steering_angle_deg", 0.0))
        road_grade_percent = float(cmd.get("road_grade_percent", 0.0))
        surface_friction_scale = float(cmd.get("surface_friction_scale", 1.0))
        target_speed_mps_raw = cmd.get("target_speed_mps")
        target_speed_mps: float | None
        if target_speed_mps_raw is None:
            target_speed_mps = None
        else:
            target_speed_mps = max(0.0, min(max_speed, float(target_speed_mps_raw)))
        tractive_force_n = throttle * max_accel * mass_kg
        brake_force_n = brake * max_decel * mass_kg
        longitudinal_wheel_force_n = tractive_force_n - brake_force_n
        rolling_force_n = rolling_resistance_coeff * mass_kg * gravity_mps2 if speed_mps > 0 else 0.0
        drag_force_n = 0.5 * air_density_kgpm3 * drag_coefficient * frontal_area_m2 * speed_mps * speed_mps
        slope_angle_rad = math.atan(road_grade_percent / 100.0)
        normal_force_n = mass_kg * gravity_mps2 * max(0.0, math.cos(slope_angle_rad))
        effective_friction_coeff = tire_friction_coeff * surface_friction_scale
        tire_force_limit_n = max(0.0, effective_friction_coeff * normal_force_n)
        longitudinal_wheel_force_limited_n = max(
            -tire_force_limit_n,
            min(tire_force_limit_n, longitudinal_wheel_force_n),
        )
        longitudinal_force_limited = (
            abs(longitudinal_wheel_force_limited_n - longitudinal_wheel_force_n) > 1e-9
        )
        grade_force_n = mass_kg * gravity_mps2 * math.sin(slope_angle_rad)
        resistive_force_n = rolling_force_n + drag_force_n + grade_force_n
        net_force_n = longitudinal_wheel_force_limited_n - resistive_force_n
        accel_mps2 = net_force_n / mass_kg
        speed_mps = max(0.0, min(max_speed, speed_mps + accel_mps2 * dt_sec))
        position_m += speed_mps * dt_sec
        yaw_accel_rps2 = 0.0
        lateral_accel_mps2 = 0.0
        if enable_planar_kinematics:
            steering_angle_rad = math.radians(steering_angle_deg)
            lateral_force_front_n = 0.0
            lateral_force_rear_n = 0.0
            if enable_dynamic_bicycle:
                if speed_mps > 0.5:
                    speed_for_lateral = speed_mps
                    slip_front_rad = steering_angle_rad - (
                        (lateral_velocity_mps + front_axle_to_cg_m * yaw_rate_rps) / speed_for_lateral
                    )
                    slip_rear_rad = -(
                        (lateral_velocity_mps - rear_axle_to_cg_m * yaw_rate_rps) / speed_for_lateral
                    )
                    slip_front_rad = max(-0.7, min(0.7, slip_front_rad))
                    slip_rear_rad = max(-0.7, min(0.7, slip_rear_rad))
                    lateral_force_front_n = cornering_stiffness_front_nprad * slip_front_rad
                    lateral_force_rear_n = cornering_stiffness_rear_nprad * slip_rear_rad
                    lateral_force_abs_total_n = abs(lateral_force_front_n) + abs(lateral_force_rear_n)
                    longitudinal_utilization = (
                        abs(longitudinal_wheel_force_limited_n) / tire_force_limit_n
                        if tire_force_limit_n > 0.0
                        else 1.0
                    )
                    longitudinal_utilization = max(0.0, min(1.0, longitudinal_utilization))
                    lateral_force_limit_n = (
                        tire_force_limit_n * math.sqrt(max(0.0, 1.0 - longitudinal_utilization * longitudinal_utilization))
                    )
                    if lateral_force_abs_total_n > lateral_force_limit_n and lateral_force_abs_total_n > 0.0:
                        scale = lateral_force_limit_n / lateral_force_abs_total_n
                        lateral_force_front_n *= scale
                        lateral_force_rear_n *= scale
                    lateral_accel_mps2 = (
                        (lateral_force_front_n + lateral_force_rear_n) / mass_kg
                        - speed_for_lateral * yaw_rate_rps
                    )
                    yaw_accel_rps2 = (
                        front_axle_to_cg_m * lateral_force_front_n
                        - rear_axle_to_cg_m * lateral_force_rear_n
                    ) / yaw_inertia_kgm2
                else:
                    lateral_accel_mps2 = -0.5 * lateral_velocity_mps
                    yaw_accel_rps2 = -0.5 * yaw_rate_rps
                lateral_velocity_mps += lateral_accel_mps2 * dt_sec
                yaw_rate_rps += yaw_accel_rps2 * dt_sec
            else:
                yaw_rate_rps = (speed_mps / wheelbase_m) * math.tan(steering_angle_rad)
                lateral_velocity_mps = 0.0
            heading_rad += yaw_rate_rps * dt_sec
            x_dot_mps = speed_mps * math.cos(heading_rad) - lateral_velocity_mps * math.sin(heading_rad)
            y_dot_mps = speed_mps * math.sin(heading_rad) + lateral_velocity_mps * math.cos(heading_rad)
            x_m += x_dot_mps * dt_sec
            y_m += y_dot_mps * dt_sec
        else:
            yaw_rate_rps = 0.0
            lateral_velocity_mps = 0.0
            x_m = position_m
        speed_tracking_error_mps = (
            speed_mps - target_speed_mps if target_speed_mps is not None else None
        )
        trace.append(
            {
                "step": float(idx),
                "throttle": throttle,
                "brake": brake,
                "target_speed_mps": round(target_speed_mps, 6) if target_speed_mps is not None else None,
                "speed_tracking_error_mps": (
                    round(speed_tracking_error_mps, 6) if speed_tracking_error_mps is not None else None
                ),
                "steering_angle_deg": round(steering_angle_deg, 6),
                "yaw_rate_rps": round(yaw_rate_rps, 6),
                "yaw_accel_rps2": round(yaw_accel_rps2, 6),
                "lateral_velocity_mps": round(lateral_velocity_mps, 6),
                "lateral_accel_mps2": round(lateral_accel_mps2, 6),
                "accel_mps2": round(accel_mps2, 6),
                "tractive_force_n": round(tractive_force_n, 6),
                "brake_force_n": round(brake_force_n, 6),
                "longitudinal_wheel_force_n": round(longitudinal_wheel_force_n, 6),
                "longitudinal_wheel_force_limited_n": round(longitudinal_wheel_force_limited_n, 6),
                "longitudinal_force_limited": bool(longitudinal_force_limited),
                "rolling_force_n": round(rolling_force_n, 6),
                "drag_force_n": round(drag_force_n, 6),
                "grade_force_n": round(grade_force_n, 6),
                "resistive_force_n": round(resistive_force_n, 6),
                "net_force_n": round(net_force_n, 6),
                "normal_force_n": round(normal_force_n, 6),
                "tire_force_limit_n": round(tire_force_limit_n, 6),
                "effective_friction_coeff": round(effective_friction_coeff, 6),
                "surface_friction_scale": round(surface_friction_scale, 6),
                "road_grade_percent": round(road_grade_percent, 6),
                "speed_mps": round(speed_mps, 6),
                "position_m": round(position_m, 6),
                "heading_deg": round(math.degrees(heading_rad), 6),
                "x_m": round(x_m, 6),
                "y_m": round(y_m, 6),
            }
        )

    speed_tracking_error_values = [
        float(row["speed_tracking_error_mps"])
        for row in trace
        if row.get("speed_tracking_error_mps") is not None
    ]
    speed_tracking_target_step_count = len(speed_tracking_error_values)
    speed_tracking_error_mps_min = min(speed_tracking_error_values) if speed_tracking_error_values else 0.0
    speed_tracking_error_mps_avg = (
        sum(speed_tracking_error_values) / float(speed_tracking_target_step_count)
        if speed_tracking_error_values
        else 0.0
    )
    speed_tracking_error_mps_max = max(speed_tracking_error_values) if speed_tracking_error_values else 0.0
    speed_tracking_error_abs_mps_avg = (
        sum(abs(value) for value in speed_tracking_error_values) / float(speed_tracking_target_step_count)
        if speed_tracking_error_values
        else 0.0
    )
    speed_tracking_error_abs_mps_max = (
        max(abs(value) for value in speed_tracking_error_values) if speed_tracking_error_values else 0.0
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vehicle_dynamics_model": (
            "planar_dynamic_bicycle_force_balance_v1"
            if enable_dynamic_bicycle
            else (
            "planar_bicycle_force_balance_v1"
            if enable_planar_kinematics
            else "longitudinal_force_balance_v1"
            )
        ),
        "planar_kinematics_enabled": bool(enable_planar_kinematics),
        "dynamic_bicycle_enabled": bool(enable_dynamic_bicycle),
        "step_count": len(trace),
        "initial_speed_mps": round(max(0.0, float(initial_speed_mps)), 6),
        "initial_position_m": round(float(initial_position_m), 6),
        "initial_heading_deg": round(float(initial_heading_deg), 6),
        "initial_lateral_position_m": round(float(initial_lateral_position_m), 6),
        "initial_lateral_velocity_mps": round(float(initial_lateral_velocity_mps), 6),
        "initial_yaw_rate_rps": round(float(initial_yaw_rate_rps), 6),
        "final_speed_mps": round(speed_mps, 6),
        "final_position_m": round(position_m, 6),
        "final_heading_deg": round(math.degrees(heading_rad), 6),
        "final_lateral_position_m": round(y_m, 6),
        "final_lateral_velocity_mps": round(lateral_velocity_mps, 6),
        "final_yaw_rate_rps": round(yaw_rate_rps, 6),
        "final_x_m": round(x_m, 6),
        "final_y_m": round(y_m, 6),
        "speed_tracking_target_step_count": int(speed_tracking_target_step_count),
        "speed_tracking_error_mps_min": round(speed_tracking_error_mps_min, 6),
        "speed_tracking_error_mps_avg": round(speed_tracking_error_mps_avg, 6),
        "speed_tracking_error_mps_max": round(speed_tracking_error_mps_max, 6),
        "speed_tracking_error_abs_mps_avg": round(speed_tracking_error_abs_mps_avg, 6),
        "speed_tracking_error_abs_mps_max": round(speed_tracking_error_abs_mps_max, 6),
        "trace": trace,
    }


def main() -> int:
    try:
        args = parse_args()
        profile_path = Path(args.vehicle_profile).resolve()
        sequence_path = Path(args.control_sequence).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        profile_payload = _load_json_object(profile_path, "vehicle profile")
        sequence_payload = _load_json_object(sequence_path, "control sequence")
        vehicle_profile = _validate_vehicle_profile(profile_payload)
        (
            dt_sec,
            initial_position_m,
            initial_speed_mps,
            initial_heading_deg,
            initial_lateral_position_m,
            initial_lateral_velocity_mps,
            initial_yaw_rate_rps,
            enable_planar_kinematics,
            enable_dynamic_bicycle,
            commands,
        ) = _validate_control_sequence(sequence_payload)
        result = run_simulation(
            vehicle_profile=vehicle_profile,
            dt_sec=dt_sec,
            initial_position_m=initial_position_m,
            initial_speed_mps=initial_speed_mps,
            initial_heading_deg=initial_heading_deg,
            initial_lateral_position_m=initial_lateral_position_m,
            initial_lateral_velocity_mps=initial_lateral_velocity_mps,
            initial_yaw_rate_rps=initial_yaw_rate_rps,
            enable_planar_kinematics=enable_planar_kinematics,
            enable_dynamic_bicycle=enable_dynamic_bicycle,
            commands=commands,
        )
        result["vehicle_profile_path"] = str(profile_path)
        result["control_sequence_path"] = str(sequence_path)

        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] step_count={result['step_count']}")
        print(f"[ok] final_speed_mps={result['final_speed_mps']}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] vehicle_dynamics_stub.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
