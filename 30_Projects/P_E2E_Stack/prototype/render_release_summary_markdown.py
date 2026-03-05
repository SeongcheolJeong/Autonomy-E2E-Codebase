#!/usr/bin/env python3
"""Render concise markdown from release summary JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ci_input_parsing import parse_positive_int
from ci_phases import SUMMARY_PHASE_PUBLISH_SUMMARY
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render release summary markdown from JSON")
    parser.add_argument("--summary-json", required=True, help="Path to release summary JSON")
    parser.add_argument("--title", default="Release Summary", help="Markdown title")
    parser.add_argument("--max-codes", default="", help="Max reason code items to print (>0)")
    return parser.parse_args()


def _fmt_counts(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return "n/a"
    keys = sorted(str(key) for key in payload.keys())
    parts = [f"{key}:{payload[key]}" for key in keys]
    return ", ".join(parts)


def _fmt_float_counts(payload: Any, *, decimals: int = 6) -> str:
    if not isinstance(payload, dict) or not payload:
        return "n/a"
    parts: list[str] = []
    normalized_items = sorted(
        ((str(raw_key), raw_value) for raw_key, raw_value in payload.items()),
        key=lambda item: item[0],
    )
    for key, raw_value in normalized_items:
        if not key:
            continue
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        parts.append(f"{key}:{value:.{decimals}f}")
    return ", ".join(parts) if parts else "n/a"


def _fmt_float_nested_counts(payload: Any, *, decimals: int = 6) -> str:
    if not isinstance(payload, dict) or not payload:
        return "n/a"
    parts: list[str] = []
    normalized: dict[str, dict[str, float]] = {}
    for raw_outer_key, raw_inner in payload.items():
        outer_key = str(raw_outer_key).strip()
        if not outer_key:
            continue
        if not isinstance(raw_inner, dict):
            continue
        normalized.setdefault(outer_key, {})
        for raw_inner_key, raw_value in raw_inner.items():
            inner_key = str(raw_inner_key).strip()
            if not inner_key:
                continue
            if raw_value is None or isinstance(raw_value, bool):
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            normalized[outer_key][inner_key] = value
    for outer_key in sorted(normalized.keys()):
        for inner_key in sorted(normalized[outer_key].keys()):
            parts.append(f"{outer_key}|{inner_key}:{normalized[outer_key][inner_key]:.{decimals}f}")
    return ", ".join(parts) if parts else "n/a"


def _fmt_list(values: Any, max_items: int) -> str:
    if not isinstance(values, list) or not values:
        return "n/a"
    items = [str(item).strip() for item in values if str(item).strip()]
    if not items:
        return "n/a"
    if len(items) > max_items:
        return ", ".join(items[:max_items]) + f", ... (+{len(items) - max_items} more)"
    return ", ".join(items)


def _fmt_ranked_rows(rows: Any, max_items: int) -> str:
    if not isinstance(rows, list) or not rows:
        return "n/a"
    normalized: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        try:
            count = int(item.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        normalized.append(f"{value}:{count}")
    if not normalized:
        return "n/a"
    if len(normalized) > max_items:
        return ", ".join(normalized[:max_items]) + f", ... (+{len(normalized) - max_items} more)"
    return ", ".join(normalized)


def _fmt_pipeline_manifest_overview(payload: Any, max_items: int) -> str:
    if not isinstance(payload, list) or not payload:
        return "n/a"
    items: list[str] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
        overall = str(row.get("overall_result", "")).strip() or "UNKNOWN"
        trend = str(row.get("trend_result", "")).strip() or "N/A"
        strict_gate = bool(row.get("strict_gate", False))
        item_text = f"{batch_id}:overall={overall},trend={trend},strict={strict_gate}"
        if "phase4_reference_primary_total_coverage_ratio" in row:
            try:
                primary_cov = float(row.get("phase4_reference_primary_total_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                primary_cov = 0.0
            item_text += f",phase4_primary_cov={primary_cov:.3f}"
        if "phase4_reference_secondary_total_coverage_ratio" in row:
            try:
                secondary_cov = float(row.get("phase4_reference_secondary_total_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                secondary_cov = 0.0
            secondary_module_count_raw = row.get("phase4_reference_secondary_module_count", 0)
            try:
                secondary_module_count = int(secondary_module_count_raw)
            except (TypeError, ValueError):
                secondary_module_count = 0
            item_text += (
                f",phase4_secondary_cov={secondary_cov:.3f}"
                f"(modules={secondary_module_count})"
            )
        if "phase3_vehicle_dynamics_step_count" in row:
            try:
                phase3_steps = int(row.get("phase3_vehicle_dynamics_step_count", 0))
            except (TypeError, ValueError):
                phase3_steps = 0
            if phase3_steps > 0:
                try:
                    phase3_initial_speed = float(row.get("phase3_vehicle_dynamics_initial_speed_mps", 0.0))
                except (TypeError, ValueError):
                    phase3_initial_speed = 0.0
                try:
                    phase3_initial_position = float(row.get("phase3_vehicle_dynamics_initial_position_m", 0.0))
                except (TypeError, ValueError):
                    phase3_initial_position = 0.0
                try:
                    phase3_initial_heading = float(row.get("phase3_vehicle_dynamics_initial_heading_deg", 0.0))
                except (TypeError, ValueError):
                    phase3_initial_heading = 0.0
                try:
                    phase3_initial_lateral_position = float(
                        row.get("phase3_vehicle_dynamics_initial_lateral_position_m", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_initial_lateral_position = 0.0
                try:
                    phase3_initial_lateral_velocity = float(
                        row.get("phase3_vehicle_dynamics_initial_lateral_velocity_mps", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_initial_lateral_velocity = 0.0
                try:
                    phase3_initial_yaw_rate = float(row.get("phase3_vehicle_dynamics_initial_yaw_rate_rps", 0.0))
                except (TypeError, ValueError):
                    phase3_initial_yaw_rate = 0.0
                try:
                    phase3_final_speed = float(row.get("phase3_vehicle_dynamics_final_speed_mps", 0.0))
                except (TypeError, ValueError):
                    phase3_final_speed = 0.0
                try:
                    phase3_final_position = float(row.get("phase3_vehicle_dynamics_final_position_m", 0.0))
                except (TypeError, ValueError):
                    phase3_final_position = 0.0
                try:
                    phase3_final_heading = float(row.get("phase3_vehicle_dynamics_final_heading_deg", 0.0))
                except (TypeError, ValueError):
                    phase3_final_heading = 0.0
                try:
                    phase3_final_lateral_position = float(
                        row.get("phase3_vehicle_dynamics_final_lateral_position_m", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_final_lateral_position = 0.0
                try:
                    phase3_final_lateral_velocity = float(
                        row.get("phase3_vehicle_dynamics_final_lateral_velocity_mps", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_final_lateral_velocity = 0.0
                try:
                    phase3_final_yaw_rate = float(row.get("phase3_vehicle_dynamics_final_yaw_rate_rps", 0.0))
                except (TypeError, ValueError):
                    phase3_final_yaw_rate = 0.0
                phase3_dynamic_enabled = bool(row.get("phase3_vehicle_dynamics_dynamic_bicycle_enabled", False))
                phase3_delta_speed = phase3_final_speed - phase3_initial_speed
                phase3_delta_position = phase3_final_position - phase3_initial_position
                phase3_delta_heading = phase3_final_heading - phase3_initial_heading
                phase3_delta_lateral_position = phase3_final_lateral_position - phase3_initial_lateral_position
                phase3_delta_lateral_velocity = phase3_final_lateral_velocity - phase3_initial_lateral_velocity
                phase3_delta_yaw_rate = phase3_final_yaw_rate - phase3_initial_yaw_rate
                try:
                    phase3_control_command_step_count = int(
                        row.get("phase3_vehicle_control_command_step_count", 0)
                    )
                except (TypeError, ValueError):
                    phase3_control_command_step_count = 0
                try:
                    phase3_control_overlap_step_count = int(
                        row.get("phase3_vehicle_control_throttle_brake_overlap_step_count", 0)
                    )
                except (TypeError, ValueError):
                    phase3_control_overlap_step_count = 0
                try:
                    phase3_control_overlap_ratio = float(
                        row.get("phase3_vehicle_control_throttle_brake_overlap_ratio", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_control_overlap_ratio = 0.0
                try:
                    phase3_control_steering_rate = float(
                        row.get("phase3_vehicle_control_max_abs_steering_rate_degps", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_control_steering_rate = 0.0
                try:
                    phase3_control_throttle_plus_brake = float(
                        row.get("phase3_vehicle_control_max_throttle_plus_brake", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_control_throttle_plus_brake = 0.0
                try:
                    phase3_speed_tracking_target_steps = int(
                        row.get("phase3_vehicle_speed_tracking_target_step_count", 0)
                    )
                except (TypeError, ValueError):
                    phase3_speed_tracking_target_steps = 0
                try:
                    phase3_speed_tracking_abs_error_max = float(
                        row.get("phase3_vehicle_speed_tracking_error_abs_mps_max", 0.0)
                    )
                except (TypeError, ValueError):
                    phase3_speed_tracking_abs_error_max = 0.0
                item_text += (
                    f",phase3_steps={phase3_steps},"
                    f"phase3_dynamic={phase3_dynamic_enabled},"
                    f"phase3_final_speed={phase3_final_speed:.3f},"
                    f"phase3_final_position={phase3_final_position:.3f},"
                    f"phase3_delta_speed={phase3_delta_speed:.3f},"
                    f"phase3_delta_position={phase3_delta_position:.3f},"
                    f"phase3_final_heading={phase3_final_heading:.3f},"
                    f"phase3_final_lateral_position={phase3_final_lateral_position:.3f},"
                    f"phase3_final_lateral_velocity={phase3_final_lateral_velocity:.3f},"
                    f"phase3_final_yaw_rate={phase3_final_yaw_rate:.3f},"
                    f"phase3_delta_heading={phase3_delta_heading:.3f},"
                    f"phase3_delta_lateral_position={phase3_delta_lateral_position:.3f},"
                    f"phase3_delta_lateral_velocity={phase3_delta_lateral_velocity:.3f},"
                    f"phase3_delta_yaw_rate={phase3_delta_yaw_rate:.3f},"
                    f"phase3_control_steps={phase3_control_command_step_count},"
                    f"phase3_control_overlap_steps={phase3_control_overlap_step_count},"
                    f"phase3_control_overlap_ratio={phase3_control_overlap_ratio:.3f},"
                    f"phase3_control_steering_rate={phase3_control_steering_rate:.3f},"
                    f"phase3_control_throttle_plus_brake={phase3_control_throttle_plus_brake:.3f},"
                    f"phase3_speed_tracking_target_steps={phase3_speed_tracking_target_steps},"
                    f"phase3_speed_tracking_abs_error_max={phase3_speed_tracking_abs_error_max:.3f}"
                )
        items.append(item_text)
    if not items:
        return "n/a"
    if len(items) > max_items:
        return ", ".join(items[:max_items]) + f", ... (+{len(items) - max_items} more)"
    return ", ".join(items)


def _fmt_phase4_secondary_coverage_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    try:
        min_cov = float(payload.get("min_coverage_ratio", 0.0))
    except (TypeError, ValueError):
        min_cov = 0.0
    try:
        avg_cov = float(payload.get("avg_coverage_ratio", 0.0))
    except (TypeError, ValueError):
        avg_cov = 0.0
    try:
        max_cov = float(payload.get("max_coverage_ratio", 0.0))
    except (TypeError, ValueError):
        max_cov = 0.0
    lowest_batch = str(payload.get("lowest_batch_id", "")).strip() or "n/a"
    highest_batch = str(payload.get("highest_batch_id", "")).strip() or "n/a"
    return (
        f"evaluated={evaluated_count}, "
        f"min={min_cov:.3f} ({lowest_batch}), "
        f"avg={avg_cov:.3f}, "
        f"max={max_cov:.3f} ({highest_batch})"
    )


def _fmt_phase4_primary_coverage_summary(payload: Any) -> str:
    return _fmt_phase4_secondary_coverage_summary(payload)


def _fmt_phase4_secondary_module_coverage_summary(payload: Any, max_items: int) -> str:
    if not isinstance(payload, dict) or not payload:
        return "n/a"
    items: list[str] = []
    for module_name in sorted(payload.keys()):
        module_payload = payload.get(module_name, {})
        if not isinstance(module_payload, dict):
            continue
        try:
            min_cov = float(module_payload.get("min_coverage_ratio", 0.0))
        except (TypeError, ValueError):
            min_cov = 0.0
        try:
            avg_cov = float(module_payload.get("avg_coverage_ratio", 0.0))
        except (TypeError, ValueError):
            avg_cov = 0.0
        try:
            max_cov = float(module_payload.get("max_coverage_ratio", 0.0))
        except (TypeError, ValueError):
            max_cov = 0.0
        lowest_batch = str(module_payload.get("lowest_batch_id", "")).strip() or "n/a"
        highest_batch = str(module_payload.get("highest_batch_id", "")).strip() or "n/a"
        items.append(
            f"{module_name}:min={min_cov:.3f} ({lowest_batch}), avg={avg_cov:.3f}, max={max_cov:.3f} ({highest_batch})"
        )
    if not items:
        return "n/a"
    if len(items) > max_items:
        return "; ".join(items[:max_items]) + f"; ... (+{len(items) - max_items} more)"
    return "; ".join(items)


def _fmt_phase4_primary_module_coverage_summary(payload: Any, max_items: int) -> str:
    return _fmt_phase4_secondary_module_coverage_summary(payload, max_items)


def _fmt_phase3_vehicle_dynamics_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    models = payload.get("models", [])
    models_text = (
        ",".join(str(item).strip() for item in models if str(item).strip())
        if isinstance(models, list)
        else ""
    ) or "n/a"
    try:
        dynamic_enabled_count = int(payload.get("dynamic_enabled_manifest_count", 0))
    except (TypeError, ValueError):
        dynamic_enabled_count = 0
    try:
        min_speed = float(payload.get("min_final_speed_mps", 0.0))
    except (TypeError, ValueError):
        min_speed = 0.0
    try:
        avg_speed = float(payload.get("avg_final_speed_mps", 0.0))
    except (TypeError, ValueError):
        avg_speed = 0.0
    try:
        max_speed = float(payload.get("max_final_speed_mps", 0.0))
    except (TypeError, ValueError):
        max_speed = 0.0
    try:
        min_position = float(payload.get("min_final_position_m", 0.0))
    except (TypeError, ValueError):
        min_position = 0.0
    try:
        avg_position = float(payload.get("avg_final_position_m", 0.0))
    except (TypeError, ValueError):
        avg_position = 0.0
    try:
        max_position = float(payload.get("max_final_position_m", 0.0))
    except (TypeError, ValueError):
        max_position = 0.0
    try:
        min_delta_speed = float(payload.get("min_delta_speed_mps", 0.0))
    except (TypeError, ValueError):
        min_delta_speed = 0.0
    try:
        avg_delta_speed = float(payload.get("avg_delta_speed_mps", 0.0))
    except (TypeError, ValueError):
        avg_delta_speed = 0.0
    try:
        max_delta_speed = float(payload.get("max_delta_speed_mps", 0.0))
    except (TypeError, ValueError):
        max_delta_speed = 0.0
    try:
        min_delta_position = float(payload.get("min_delta_position_m", 0.0))
    except (TypeError, ValueError):
        min_delta_position = 0.0
    try:
        avg_delta_position = float(payload.get("avg_delta_position_m", 0.0))
    except (TypeError, ValueError):
        avg_delta_position = 0.0
    try:
        max_delta_position = float(payload.get("max_delta_position_m", 0.0))
    except (TypeError, ValueError):
        max_delta_position = 0.0
    try:
        min_heading = float(payload.get("min_final_heading_deg", 0.0))
    except (TypeError, ValueError):
        min_heading = 0.0
    try:
        avg_heading = float(payload.get("avg_final_heading_deg", 0.0))
    except (TypeError, ValueError):
        avg_heading = 0.0
    try:
        max_heading = float(payload.get("max_final_heading_deg", 0.0))
    except (TypeError, ValueError):
        max_heading = 0.0
    try:
        min_lateral_position = float(payload.get("min_final_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        min_lateral_position = 0.0
    try:
        avg_lateral_position = float(payload.get("avg_final_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        avg_lateral_position = 0.0
    try:
        max_lateral_position = float(payload.get("max_final_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        max_lateral_position = 0.0
    try:
        min_lateral_velocity = float(payload.get("min_final_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        min_lateral_velocity = 0.0
    try:
        avg_lateral_velocity = float(payload.get("avg_final_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        avg_lateral_velocity = 0.0
    try:
        max_lateral_velocity = float(payload.get("max_final_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        max_lateral_velocity = 0.0
    try:
        min_yaw_rate_final = float(payload.get("min_final_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        min_yaw_rate_final = 0.0
    try:
        avg_yaw_rate_final = float(payload.get("avg_final_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        avg_yaw_rate_final = 0.0
    try:
        max_yaw_rate_final = float(payload.get("max_final_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        max_yaw_rate_final = 0.0
    try:
        min_delta_heading = float(payload.get("min_delta_heading_deg", 0.0))
    except (TypeError, ValueError):
        min_delta_heading = 0.0
    try:
        avg_delta_heading = float(payload.get("avg_delta_heading_deg", 0.0))
    except (TypeError, ValueError):
        avg_delta_heading = 0.0
    try:
        max_delta_heading = float(payload.get("max_delta_heading_deg", 0.0))
    except (TypeError, ValueError):
        max_delta_heading = 0.0
    try:
        min_delta_lateral_position = float(payload.get("min_delta_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        min_delta_lateral_position = 0.0
    try:
        avg_delta_lateral_position = float(payload.get("avg_delta_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        avg_delta_lateral_position = 0.0
    try:
        max_delta_lateral_position = float(payload.get("max_delta_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        max_delta_lateral_position = 0.0
    try:
        min_delta_lateral_velocity = float(payload.get("min_delta_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        min_delta_lateral_velocity = 0.0
    try:
        avg_delta_lateral_velocity = float(payload.get("avg_delta_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        avg_delta_lateral_velocity = 0.0
    try:
        max_delta_lateral_velocity = float(payload.get("max_delta_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        max_delta_lateral_velocity = 0.0
    try:
        min_delta_yaw_rate = float(payload.get("min_delta_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        min_delta_yaw_rate = 0.0
    try:
        avg_delta_yaw_rate = float(payload.get("avg_delta_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        avg_delta_yaw_rate = 0.0
    try:
        max_delta_yaw_rate = float(payload.get("max_delta_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        max_delta_yaw_rate = 0.0
    try:
        max_abs_yaw_rate = float(payload.get("max_abs_yaw_rate_rps", 0.0))
    except (TypeError, ValueError):
        max_abs_yaw_rate = 0.0
    try:
        max_abs_lateral_velocity = float(payload.get("max_abs_lateral_velocity_mps", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_velocity = 0.0
    try:
        max_abs_accel = float(payload.get("max_abs_accel_mps2", 0.0))
    except (TypeError, ValueError):
        max_abs_accel = 0.0
    try:
        max_abs_lateral_accel = float(payload.get("max_abs_lateral_accel_mps2", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_accel = 0.0
    try:
        max_abs_yaw_accel = float(payload.get("max_abs_yaw_accel_rps2", 0.0))
    except (TypeError, ValueError):
        max_abs_yaw_accel = 0.0
    try:
        max_abs_jerk = float(payload.get("max_abs_jerk_mps3", 0.0))
    except (TypeError, ValueError):
        max_abs_jerk = 0.0
    try:
        max_abs_lateral_jerk = float(payload.get("max_abs_lateral_jerk_mps3", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_jerk = 0.0
    try:
        max_abs_yaw_jerk = float(payload.get("max_abs_yaw_jerk_rps3", 0.0))
    except (TypeError, ValueError):
        max_abs_yaw_jerk = 0.0
    try:
        max_abs_lateral_position = float(payload.get("max_abs_lateral_position_m", 0.0))
    except (TypeError, ValueError):
        max_abs_lateral_position = 0.0
    try:
        min_road_grade = float(payload.get("min_road_grade_percent", 0.0))
    except (TypeError, ValueError):
        min_road_grade = 0.0
    try:
        avg_road_grade = float(payload.get("avg_road_grade_percent", 0.0))
    except (TypeError, ValueError):
        avg_road_grade = 0.0
    try:
        max_road_grade = float(payload.get("max_road_grade_percent", 0.0))
    except (TypeError, ValueError):
        max_road_grade = 0.0
    try:
        max_abs_grade_force = float(payload.get("max_abs_grade_force_n", 0.0))
    except (TypeError, ValueError):
        max_abs_grade_force = 0.0
    try:
        control_command_manifest_count = int(payload.get("control_command_manifest_count", 0))
    except (TypeError, ValueError):
        control_command_manifest_count = 0
    try:
        control_command_step_count_total = int(payload.get("control_command_step_count_total", 0))
    except (TypeError, ValueError):
        control_command_step_count_total = 0
    try:
        control_overlap_step_count_total = int(payload.get("control_throttle_brake_overlap_step_count_total", 0))
    except (TypeError, ValueError):
        control_overlap_step_count_total = 0
    try:
        control_overlap_ratio_avg = float(payload.get("control_throttle_brake_overlap_ratio_avg", 0.0))
    except (TypeError, ValueError):
        control_overlap_ratio_avg = 0.0
    try:
        control_overlap_ratio_max = float(payload.get("control_throttle_brake_overlap_ratio_max", 0.0))
    except (TypeError, ValueError):
        control_overlap_ratio_max = 0.0
    try:
        control_steering_rate_avg = float(payload.get("control_max_abs_steering_rate_degps_avg", 0.0))
    except (TypeError, ValueError):
        control_steering_rate_avg = 0.0
    try:
        control_steering_rate_max = float(payload.get("control_max_abs_steering_rate_degps_max", 0.0))
    except (TypeError, ValueError):
        control_steering_rate_max = 0.0
    try:
        control_throttle_rate_avg = float(payload.get("control_max_abs_throttle_rate_per_sec_avg", 0.0))
    except (TypeError, ValueError):
        control_throttle_rate_avg = 0.0
    try:
        control_throttle_rate_max = float(payload.get("control_max_abs_throttle_rate_per_sec_max", 0.0))
    except (TypeError, ValueError):
        control_throttle_rate_max = 0.0
    try:
        control_brake_rate_avg = float(payload.get("control_max_abs_brake_rate_per_sec_avg", 0.0))
    except (TypeError, ValueError):
        control_brake_rate_avg = 0.0
    try:
        control_brake_rate_max = float(payload.get("control_max_abs_brake_rate_per_sec_max", 0.0))
    except (TypeError, ValueError):
        control_brake_rate_max = 0.0
    try:
        control_throttle_plus_brake_avg = float(payload.get("control_max_throttle_plus_brake_avg", 0.0))
    except (TypeError, ValueError):
        control_throttle_plus_brake_avg = 0.0
    try:
        control_throttle_plus_brake_max = float(payload.get("control_max_throttle_plus_brake_max", 0.0))
    except (TypeError, ValueError):
        control_throttle_plus_brake_max = 0.0
    try:
        speed_tracking_manifest_count = int(payload.get("speed_tracking_manifest_count", 0))
    except (TypeError, ValueError):
        speed_tracking_manifest_count = 0
    try:
        speed_tracking_target_step_count_total = int(payload.get("speed_tracking_target_step_count_total", 0))
    except (TypeError, ValueError):
        speed_tracking_target_step_count_total = 0
    try:
        min_speed_tracking_error = float(payload.get("min_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        min_speed_tracking_error = 0.0
    try:
        avg_speed_tracking_error = float(payload.get("avg_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        avg_speed_tracking_error = 0.0
    try:
        max_speed_tracking_error = float(payload.get("max_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        max_speed_tracking_error = 0.0
    try:
        avg_abs_speed_tracking_error = float(payload.get("avg_abs_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        avg_abs_speed_tracking_error = 0.0
    try:
        max_abs_speed_tracking_error = float(payload.get("max_abs_speed_tracking_error_mps", 0.0))
    except (TypeError, ValueError):
        max_abs_speed_tracking_error = 0.0
    lowest_speed_batch = str(payload.get("lowest_speed_batch_id", "")).strip() or "n/a"
    highest_speed_batch = str(payload.get("highest_speed_batch_id", "")).strip() or "n/a"
    lowest_position_batch = str(payload.get("lowest_position_batch_id", "")).strip() or "n/a"
    highest_position_batch = str(payload.get("highest_position_batch_id", "")).strip() or "n/a"
    lowest_delta_speed_batch = str(payload.get("lowest_delta_speed_batch_id", "")).strip() or "n/a"
    highest_delta_speed_batch = str(payload.get("highest_delta_speed_batch_id", "")).strip() or "n/a"
    lowest_delta_position_batch = str(payload.get("lowest_delta_position_batch_id", "")).strip() or "n/a"
    highest_delta_position_batch = str(payload.get("highest_delta_position_batch_id", "")).strip() or "n/a"
    lowest_heading_batch = str(payload.get("lowest_heading_batch_id", "")).strip() or "n/a"
    highest_heading_batch = str(payload.get("highest_heading_batch_id", "")).strip() or "n/a"
    lowest_lateral_position_batch = str(payload.get("lowest_lateral_position_batch_id", "")).strip() or "n/a"
    highest_lateral_position_batch = str(payload.get("highest_lateral_position_batch_id", "")).strip() or "n/a"
    lowest_lateral_velocity_batch = str(payload.get("lowest_lateral_velocity_batch_id", "")).strip() or "n/a"
    highest_lateral_velocity_batch = str(payload.get("highest_lateral_velocity_batch_id", "")).strip() or "n/a"
    lowest_yaw_rate_batch = str(payload.get("lowest_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_yaw_rate_batch = str(payload.get("highest_yaw_rate_batch_id", "")).strip() or "n/a"
    lowest_delta_heading_batch = str(payload.get("lowest_delta_heading_batch_id", "")).strip() or "n/a"
    highest_delta_heading_batch = str(payload.get("highest_delta_heading_batch_id", "")).strip() or "n/a"
    lowest_delta_lateral_position_batch = (
        str(payload.get("lowest_delta_lateral_position_batch_id", "")).strip() or "n/a"
    )
    highest_delta_lateral_position_batch = (
        str(payload.get("highest_delta_lateral_position_batch_id", "")).strip() or "n/a"
    )
    lowest_delta_lateral_velocity_batch = (
        str(payload.get("lowest_delta_lateral_velocity_batch_id", "")).strip() or "n/a"
    )
    highest_delta_lateral_velocity_batch = (
        str(payload.get("highest_delta_lateral_velocity_batch_id", "")).strip() or "n/a"
    )
    lowest_delta_yaw_rate_batch = str(payload.get("lowest_delta_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_delta_yaw_rate_batch = str(payload.get("highest_delta_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_abs_yaw_rate_batch = str(payload.get("highest_abs_yaw_rate_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_velocity_batch = (
        str(payload.get("highest_abs_lateral_velocity_batch_id", "")).strip() or "n/a"
    )
    highest_abs_accel_batch = str(payload.get("highest_abs_accel_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_accel_batch = (
        str(payload.get("highest_abs_lateral_accel_batch_id", "")).strip() or "n/a"
    )
    highest_abs_yaw_accel_batch = str(payload.get("highest_abs_yaw_accel_batch_id", "")).strip() or "n/a"
    highest_abs_jerk_batch = str(payload.get("highest_abs_jerk_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_jerk_batch = (
        str(payload.get("highest_abs_lateral_jerk_batch_id", "")).strip() or "n/a"
    )
    highest_abs_yaw_jerk_batch = str(payload.get("highest_abs_yaw_jerk_batch_id", "")).strip() or "n/a"
    highest_abs_lateral_position_batch = (
        str(payload.get("highest_abs_lateral_position_batch_id", "")).strip() or "n/a"
    )
    lowest_road_grade_batch = str(payload.get("lowest_road_grade_batch_id", "")).strip() or "n/a"
    highest_road_grade_batch = str(payload.get("highest_road_grade_batch_id", "")).strip() or "n/a"
    highest_abs_grade_force_batch = str(payload.get("highest_abs_grade_force_batch_id", "")).strip() or "n/a"
    highest_control_overlap_batch = str(payload.get("highest_control_overlap_ratio_batch_id", "")).strip() or "n/a"
    highest_control_steering_rate_batch = (
        str(payload.get("highest_control_steering_rate_batch_id", "")).strip() or "n/a"
    )
    highest_control_throttle_rate_batch = (
        str(payload.get("highest_control_throttle_rate_batch_id", "")).strip() or "n/a"
    )
    highest_control_brake_rate_batch = str(payload.get("highest_control_brake_rate_batch_id", "")).strip() or "n/a"
    highest_control_throttle_plus_brake_batch = (
        str(payload.get("highest_control_throttle_plus_brake_batch_id", "")).strip() or "n/a"
    )
    lowest_speed_tracking_error_batch = (
        str(payload.get("lowest_speed_tracking_error_batch_id", "")).strip() or "n/a"
    )
    highest_speed_tracking_error_batch = (
        str(payload.get("highest_speed_tracking_error_batch_id", "")).strip() or "n/a"
    )
    highest_abs_speed_tracking_error_batch = (
        str(payload.get("highest_abs_speed_tracking_error_batch_id", "")).strip() or "n/a"
    )
    return (
        f"evaluated={evaluated_count}, dynamic_enabled={dynamic_enabled_count}, models={models_text}, "
        f"speed=min={min_speed:.3f} ({lowest_speed_batch}), avg={avg_speed:.3f}, max={max_speed:.3f} ({highest_speed_batch}), "
        f"position=min={min_position:.3f} ({lowest_position_batch}), avg={avg_position:.3f}, max={max_position:.3f} ({highest_position_batch}), "
        f"delta_speed=min={min_delta_speed:.3f} ({lowest_delta_speed_batch}), avg={avg_delta_speed:.3f}, max={max_delta_speed:.3f} ({highest_delta_speed_batch}), "
        f"delta_position=min={min_delta_position:.3f} ({lowest_delta_position_batch}), avg={avg_delta_position:.3f}, max={max_delta_position:.3f} ({highest_delta_position_batch}), "
        f"heading=min={min_heading:.3f} ({lowest_heading_batch}), avg={avg_heading:.3f}, max={max_heading:.3f} ({highest_heading_batch}), "
        f"lateral_position=min={min_lateral_position:.3f} ({lowest_lateral_position_batch}), avg={avg_lateral_position:.3f}, max={max_lateral_position:.3f} ({highest_lateral_position_batch}), "
        f"lateral_velocity=min={min_lateral_velocity:.3f} ({lowest_lateral_velocity_batch}), avg={avg_lateral_velocity:.3f}, max={max_lateral_velocity:.3f} ({highest_lateral_velocity_batch}), "
        f"yaw_rate_final=min={min_yaw_rate_final:.3f} ({lowest_yaw_rate_batch}), avg={avg_yaw_rate_final:.3f}, max={max_yaw_rate_final:.3f} ({highest_yaw_rate_batch}), "
        f"delta_heading=min={min_delta_heading:.3f} ({lowest_delta_heading_batch}), avg={avg_delta_heading:.3f}, max={max_delta_heading:.3f} ({highest_delta_heading_batch}), "
        f"delta_lateral_position=min={min_delta_lateral_position:.3f} ({lowest_delta_lateral_position_batch}), avg={avg_delta_lateral_position:.3f}, max={max_delta_lateral_position:.3f} ({highest_delta_lateral_position_batch}), "
        f"delta_lateral_velocity=min={min_delta_lateral_velocity:.3f} ({lowest_delta_lateral_velocity_batch}), avg={avg_delta_lateral_velocity:.3f}, max={max_delta_lateral_velocity:.3f} ({highest_delta_lateral_velocity_batch}), "
        f"delta_yaw_rate=min={min_delta_yaw_rate:.3f} ({lowest_delta_yaw_rate_batch}), avg={avg_delta_yaw_rate:.3f}, max={max_delta_yaw_rate:.3f} ({highest_delta_yaw_rate_batch}), "
        f"yaw_rate=max_abs={max_abs_yaw_rate:.3f} ({highest_abs_yaw_rate_batch}), "
        f"lateral_velocity=max_abs={max_abs_lateral_velocity:.3f} ({highest_abs_lateral_velocity_batch}), "
        f"accel=max_abs={max_abs_accel:.3f} ({highest_abs_accel_batch}), "
        f"lateral_accel=max_abs={max_abs_lateral_accel:.3f} ({highest_abs_lateral_accel_batch}), "
        f"yaw_accel=max_abs={max_abs_yaw_accel:.3f} ({highest_abs_yaw_accel_batch}), "
        f"jerk=max_abs={max_abs_jerk:.3f} ({highest_abs_jerk_batch}), "
        f"lateral_jerk=max_abs={max_abs_lateral_jerk:.3f} ({highest_abs_lateral_jerk_batch}), "
        f"yaw_jerk=max_abs={max_abs_yaw_jerk:.3f} ({highest_abs_yaw_jerk_batch}), "
        f"lateral_abs=max={max_abs_lateral_position:.3f} ({highest_abs_lateral_position_batch}), "
        f"road_grade=min={min_road_grade:.3f} ({lowest_road_grade_batch}), avg={avg_road_grade:.3f}, max={max_road_grade:.3f} ({highest_road_grade_batch}), "
        f"grade_force=max_abs={max_abs_grade_force:.3f} ({highest_abs_grade_force_batch}), "
        f"control_input=manifests:{control_command_manifest_count},steps:{control_command_step_count_total},"
        f"overlap_steps:{control_overlap_step_count_total},"
        f"overlap_ratio_avg:{control_overlap_ratio_avg:.3f},"
        f"overlap_ratio_max:{control_overlap_ratio_max:.3f} ({highest_control_overlap_batch}), "
        f"steering_rate_avg:{control_steering_rate_avg:.3f},"
        f"steering_rate_max:{control_steering_rate_max:.3f} ({highest_control_steering_rate_batch}), "
        f"throttle_rate_avg:{control_throttle_rate_avg:.3f},"
        f"throttle_rate_max:{control_throttle_rate_max:.3f} ({highest_control_throttle_rate_batch}), "
        f"brake_rate_avg:{control_brake_rate_avg:.3f},"
        f"brake_rate_max:{control_brake_rate_max:.3f} ({highest_control_brake_rate_batch}), "
        f"throttle_plus_brake_avg:{control_throttle_plus_brake_avg:.3f},"
        f"throttle_plus_brake_max:{control_throttle_plus_brake_max:.3f} ({highest_control_throttle_plus_brake_batch}), "
        f"speed_tracking=manifests:{speed_tracking_manifest_count},"
        f"target_steps:{speed_tracking_target_step_count_total},"
        f"error_min:{min_speed_tracking_error:.3f} ({lowest_speed_tracking_error_batch}),"
        f"error_avg:{avg_speed_tracking_error:.3f},"
        f"error_max:{max_speed_tracking_error:.3f} ({highest_speed_tracking_error_batch}),"
        f"error_abs_avg:{avg_abs_speed_tracking_error:.3f},"
        f"error_abs_max:{max_abs_speed_tracking_error:.3f} ({highest_abs_speed_tracking_error_batch})"
    )


def _fmt_phase3_core_sim_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    status_counts_text = _fmt_counts(payload.get("status_counts", {}))
    gate_result_counts_text = _fmt_counts(payload.get("gate_result_counts", {}))
    try:
        gate_reason_count_total = int(payload.get("gate_reason_count_total", 0))
    except (TypeError, ValueError):
        gate_reason_count_total = 0
    try:
        require_success_enabled_count = int(payload.get("gate_require_success_enabled_count", 0))
    except (TypeError, ValueError):
        require_success_enabled_count = 0
    try:
        success_manifest_count = int(payload.get("success_manifest_count", 0))
    except (TypeError, ValueError):
        success_manifest_count = 0
    try:
        collision_manifest_count = int(payload.get("collision_manifest_count", 0))
    except (TypeError, ValueError):
        collision_manifest_count = 0
    try:
        timeout_manifest_count = int(payload.get("timeout_manifest_count", 0))
    except (TypeError, ValueError):
        timeout_manifest_count = 0
    try:
        min_ttc_same_lane = float(payload.get("min_ttc_same_lane_sec"))
        min_ttc_same_lane_text = f"{min_ttc_same_lane:.3f}"
    except (TypeError, ValueError):
        min_ttc_same_lane_text = "n/a"
    try:
        min_ttc_any_lane = float(payload.get("min_ttc_any_lane_sec"))
        min_ttc_any_lane_text = f"{min_ttc_any_lane:.3f}"
    except (TypeError, ValueError):
        min_ttc_any_lane_text = "n/a"
    lowest_same_lane_batch = str(payload.get("lowest_same_lane_batch_id", "")).strip() or "n/a"
    lowest_any_lane_batch = str(payload.get("lowest_any_lane_batch_id", "")).strip() or "n/a"
    try:
        avoidance_enabled_manifest_count = int(payload.get("avoidance_enabled_manifest_count", 0))
    except (TypeError, ValueError):
        avoidance_enabled_manifest_count = 0
    try:
        avoidance_brake_event_count_total = int(payload.get("ego_avoidance_brake_event_count_total", 0))
    except (TypeError, ValueError):
        avoidance_brake_event_count_total = 0
    try:
        max_avoidance_brake = float(payload.get("max_ego_avoidance_applied_brake_mps2", 0.0))
    except (TypeError, ValueError):
        max_avoidance_brake = 0.0
    highest_avoidance_brake_batch = (
        str(payload.get("highest_ego_avoidance_applied_brake_batch_id", "")).strip() or "n/a"
    )
    try:
        avg_tire_friction = float(payload.get("avg_tire_friction_coeff", 0.0))
    except (TypeError, ValueError):
        avg_tire_friction = 0.0
    try:
        avg_surface_friction = float(payload.get("avg_surface_friction_scale", 0.0))
    except (TypeError, ValueError):
        avg_surface_friction = 0.0
    return (
        f"evaluated={evaluated_count}, statuses={status_counts_text}, gate_results={gate_result_counts_text}, "
        f"gate_reasons_total={gate_reason_count_total}, require_success_enabled={require_success_enabled_count}, "
        f"success={success_manifest_count}, collision={collision_manifest_count}, timeout={timeout_manifest_count}, "
        f"min_ttc_same_lane={min_ttc_same_lane_text} ({lowest_same_lane_batch}), "
        f"min_ttc_any_lane={min_ttc_any_lane_text} ({lowest_any_lane_batch}), "
        f"avoidance_enabled={avoidance_enabled_manifest_count}, "
        f"avoidance_brake_events_total={avoidance_brake_event_count_total}, "
        f"avoidance_brake_applied_max={max_avoidance_brake:.3f} ({highest_avoidance_brake_batch}), "
        f"tire_friction_avg={avg_tire_friction:.3f}, surface_friction_avg={avg_surface_friction:.3f}"
    )


def _fmt_phase3_core_sim_matrix_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    try:
        enabled_manifest_count = int(payload.get("enabled_manifest_count", 0))
    except (TypeError, ValueError):
        enabled_manifest_count = 0
    try:
        case_count_total = int(payload.get("case_count_total", 0))
    except (TypeError, ValueError):
        case_count_total = 0
    try:
        success_case_count_total = int(payload.get("success_case_count_total", 0))
    except (TypeError, ValueError):
        success_case_count_total = 0
    try:
        failed_case_count_total = int(payload.get("failed_case_count_total", 0))
    except (TypeError, ValueError):
        failed_case_count_total = 0
    try:
        all_cases_success_manifest_count = int(payload.get("all_cases_success_manifest_count", 0))
    except (TypeError, ValueError):
        all_cases_success_manifest_count = 0
    try:
        collision_case_count_total = int(payload.get("collision_case_count_total", 0))
    except (TypeError, ValueError):
        collision_case_count_total = 0
    try:
        timeout_case_count_total = int(payload.get("timeout_case_count_total", 0))
    except (TypeError, ValueError):
        timeout_case_count_total = 0
    status_counts_text = _fmt_counts(payload.get("status_counts", {}))
    returncode_counts_text = _fmt_counts(payload.get("returncode_counts", {}))

    def _fmt_ttc(value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "n/a"

    min_ttc_same_lane_text = _fmt_ttc(payload.get("min_ttc_same_lane_sec_min"))
    min_ttc_any_lane_text = _fmt_ttc(payload.get("min_ttc_any_lane_sec_min"))
    lowest_same_lane_batch = str(payload.get("lowest_ttc_same_lane_batch_id", "")).strip() or "n/a"
    lowest_same_lane_run = str(payload.get("lowest_ttc_same_lane_run_id", "")).strip() or "n/a"
    lowest_any_lane_batch = str(payload.get("lowest_ttc_any_lane_batch_id", "")).strip() or "n/a"
    lowest_any_lane_run = str(payload.get("lowest_ttc_any_lane_run_id", "")).strip() or "n/a"
    return (
        f"evaluated={evaluated_count}, enabled_manifests={enabled_manifest_count}, "
        f"cases_total={case_count_total}, success_cases_total={success_case_count_total}, "
        f"failed_cases_total={failed_case_count_total}, "
        f"all_cases_success_manifests={all_cases_success_manifest_count}, "
        f"collision_cases_total={collision_case_count_total}, timeout_cases_total={timeout_case_count_total}, "
        f"statuses={status_counts_text}, returncodes={returncode_counts_text}, "
        f"min_ttc_same_lane={min_ttc_same_lane_text} ({lowest_same_lane_batch}|{lowest_same_lane_run}), "
        f"min_ttc_any_lane={min_ttc_any_lane_text} ({lowest_any_lane_batch}|{lowest_any_lane_run})"
    )


def _fmt_phase3_lane_risk_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    try:
        run_count_total = int(payload.get("lane_risk_summary_run_count_total", 0))
    except (TypeError, ValueError):
        run_count_total = 0
    gate_result_counts_text = _fmt_counts(payload.get("gate_result_counts", {})).replace(", ", ",")
    try:
        gate_reason_count_total = int(payload.get("gate_reason_count_total", 0))
    except (TypeError, ValueError):
        gate_reason_count_total = 0

    def _fmt_ttc(value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "n/a"

    min_ttc_same_lane_text = _fmt_ttc(payload.get("min_ttc_same_lane_sec"))
    min_ttc_adjacent_lane_text = _fmt_ttc(payload.get("min_ttc_adjacent_lane_sec"))
    min_ttc_any_lane_text = _fmt_ttc(payload.get("min_ttc_any_lane_sec"))
    lowest_same_lane_batch = str(payload.get("lowest_same_lane_batch_id", "")).strip() or "n/a"
    lowest_adjacent_lane_batch = str(payload.get("lowest_adjacent_lane_batch_id", "")).strip() or "n/a"
    lowest_any_lane_batch = str(payload.get("lowest_any_lane_batch_id", "")).strip() or "n/a"
    try:
        ttc_under_3s_same_lane_total = int(payload.get("ttc_under_3s_same_lane_total", 0))
    except (TypeError, ValueError):
        ttc_under_3s_same_lane_total = 0
    try:
        ttc_under_3s_adjacent_lane_total = int(payload.get("ttc_under_3s_adjacent_lane_total", 0))
    except (TypeError, ValueError):
        ttc_under_3s_adjacent_lane_total = 0
    try:
        same_lane_rows_total = int(payload.get("same_lane_rows_total", 0))
    except (TypeError, ValueError):
        same_lane_rows_total = 0
    try:
        adjacent_lane_rows_total = int(payload.get("adjacent_lane_rows_total", 0))
    except (TypeError, ValueError):
        adjacent_lane_rows_total = 0
    try:
        other_lane_rows_total = int(payload.get("other_lane_rows_total", 0))
    except (TypeError, ValueError):
        other_lane_rows_total = 0
    return (
        f"evaluated={evaluated_count}, runs={run_count_total}, "
        f"gate_results={gate_result_counts_text}, gate_reasons_total={gate_reason_count_total}, "
        f"min_ttc_same_lane={min_ttc_same_lane_text} ({lowest_same_lane_batch}), "
        f"min_ttc_adjacent_lane={min_ttc_adjacent_lane_text} ({lowest_adjacent_lane_batch}), "
        f"min_ttc_any_lane={min_ttc_any_lane_text} ({lowest_any_lane_batch}), "
        f"ttc_under_3s_same_lane_total={ttc_under_3s_same_lane_total}, "
        f"ttc_under_3s_adjacent_lane_total={ttc_under_3s_adjacent_lane_total}, "
        f"rows=same:{same_lane_rows_total},adjacent:{adjacent_lane_rows_total},other:{other_lane_rows_total}"
    )


def _fmt_phase3_dataset_traffic_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    gate_result_counts_text = _fmt_counts(payload.get("gate_result_counts", {}))
    try:
        gate_reason_count_total = int(payload.get("gate_reason_count_total", 0))
    except (TypeError, ValueError):
        gate_reason_count_total = 0
    try:
        run_summary_count_total = int(payload.get("run_summary_count_total", 0))
    except (TypeError, ValueError):
        run_summary_count_total = 0
    run_status_counts_text = _fmt_counts(payload.get("run_status_counts", {}))
    try:
        profile_unique_count = int(payload.get("traffic_profile_unique_count", 0))
    except (TypeError, ValueError):
        profile_unique_count = 0
    profile_ids_text = _fmt_list(payload.get("traffic_profile_ids"), 20)
    try:
        profile_count_avg = float(payload.get("traffic_profile_count_avg", 0.0))
    except (TypeError, ValueError):
        profile_count_avg = 0.0
    try:
        max_profile_count = int(payload.get("max_traffic_profile_count", 0))
    except (TypeError, ValueError):
        max_profile_count = 0
    max_profile_batch = str(payload.get("highest_traffic_profile_batch_id", "")).strip() or "n/a"
    try:
        profile_source_unique_count = int(payload.get("traffic_profile_source_unique_count", 0))
    except (TypeError, ValueError):
        profile_source_unique_count = 0
    profile_source_ids_text = _fmt_list(payload.get("traffic_profile_source_ids"), 20)
    try:
        profile_source_count_avg = float(payload.get("traffic_profile_source_count_avg", 0.0))
    except (TypeError, ValueError):
        profile_source_count_avg = 0.0
    try:
        max_profile_source_count = int(payload.get("max_traffic_profile_source_count", 0))
    except (TypeError, ValueError):
        max_profile_source_count = 0
    max_profile_source_batch = (
        str(payload.get("highest_traffic_profile_source_batch_id", "")).strip() or "n/a"
    )
    try:
        actor_pattern_unique_count = int(payload.get("traffic_actor_pattern_unique_count", 0))
    except (TypeError, ValueError):
        actor_pattern_unique_count = 0
    actor_pattern_ids_text = _fmt_list(payload.get("traffic_actor_pattern_ids"), 20)
    try:
        actor_pattern_count_avg = float(payload.get("traffic_actor_pattern_count_avg", 0.0))
    except (TypeError, ValueError):
        actor_pattern_count_avg = 0.0
    try:
        max_actor_pattern_count = int(payload.get("max_traffic_actor_pattern_count", 0))
    except (TypeError, ValueError):
        max_actor_pattern_count = 0
    max_actor_pattern_batch = str(payload.get("highest_traffic_actor_pattern_batch_id", "")).strip() or "n/a"
    try:
        lane_profile_signature_unique_count = int(
            payload.get("traffic_lane_profile_signature_unique_count", 0)
        )
    except (TypeError, ValueError):
        lane_profile_signature_unique_count = 0
    lane_profile_signatures_text = _fmt_list(payload.get("traffic_lane_profile_signatures"), 20)
    try:
        lane_profile_signature_count_avg = float(
            payload.get("traffic_lane_profile_signature_count_avg", 0.0)
        )
    except (TypeError, ValueError):
        lane_profile_signature_count_avg = 0.0
    try:
        max_lane_profile_signature_count = int(
            payload.get("max_traffic_lane_profile_signature_count", 0)
        )
    except (TypeError, ValueError):
        max_lane_profile_signature_count = 0
    max_lane_profile_signature_batch = (
        str(payload.get("highest_traffic_lane_profile_signature_batch_id", "")).strip() or "n/a"
    )
    try:
        npc_count_avg_avg = float(payload.get("traffic_npc_count_avg_avg", 0.0))
    except (TypeError, ValueError):
        npc_count_avg_avg = 0.0
    try:
        npc_count_avg_max = float(payload.get("traffic_npc_count_avg_max", 0.0))
    except (TypeError, ValueError):
        npc_count_avg_max = 0.0
    npc_count_avg_max_batch = str(payload.get("highest_traffic_npc_avg_batch_id", "")).strip() or "n/a"
    try:
        npc_count_max_max = int(payload.get("traffic_npc_count_max_max", 0))
    except (TypeError, ValueError):
        npc_count_max_max = 0
    npc_count_max_max_batch = str(payload.get("highest_traffic_npc_max_batch_id", "")).strip() or "n/a"
    try:
        npc_initial_gap_avg_avg = float(payload.get("traffic_npc_initial_gap_m_avg_avg", 0.0))
    except (TypeError, ValueError):
        npc_initial_gap_avg_avg = 0.0
    npc_initial_gap_avg_batch = (
        str(payload.get("highest_traffic_npc_initial_gap_m_avg_batch_id", "")).strip() or "n/a"
    )
    try:
        npc_gap_step_avg_avg = float(payload.get("traffic_npc_gap_step_m_avg_avg", 0.0))
    except (TypeError, ValueError):
        npc_gap_step_avg_avg = 0.0
    npc_gap_step_avg_batch = (
        str(payload.get("highest_traffic_npc_gap_step_m_avg_batch_id", "")).strip() or "n/a"
    )
    try:
        npc_speed_scale_avg_avg = float(payload.get("traffic_npc_speed_scale_avg_avg", 0.0))
    except (TypeError, ValueError):
        npc_speed_scale_avg_avg = 0.0
    npc_speed_scale_avg_batch = (
        str(payload.get("highest_traffic_npc_speed_scale_avg_batch_id", "")).strip() or "n/a"
    )
    try:
        npc_speed_jitter_avg_avg = float(payload.get("traffic_npc_speed_jitter_mps_avg_avg", 0.0))
    except (TypeError, ValueError):
        npc_speed_jitter_avg_avg = 0.0
    npc_speed_jitter_avg_batch = (
        str(payload.get("highest_traffic_npc_speed_jitter_mps_avg_batch_id", "")).strip() or "n/a"
    )
    try:
        lane_index_unique_count = int(payload.get("traffic_lane_indices_unique_count", 0))
    except (TypeError, ValueError):
        lane_index_unique_count = 0
    try:
        lane_index_unique_count_avg = float(payload.get("traffic_lane_index_unique_count_avg", 0.0))
    except (TypeError, ValueError):
        lane_index_unique_count_avg = 0.0
    lane_indices_raw = payload.get("traffic_lane_indices")
    lane_indices: list[int] = []
    if isinstance(lane_indices_raw, list):
        for lane_index_raw in lane_indices_raw:
            try:
                lane_index = int(lane_index_raw)
            except (TypeError, ValueError):
                continue
            lane_indices.append(lane_index)
    lane_indices_text = ",".join(str(value) for value in sorted(set(lane_indices))) or "n/a"
    try:
        dataset_manifest_counts_rows_total = int(payload.get("dataset_manifest_counts_rows_total", 0))
    except (TypeError, ValueError):
        dataset_manifest_counts_rows_total = 0
    try:
        dataset_manifest_run_summary_count_total = int(payload.get("dataset_manifest_run_summary_count_total", 0))
    except (TypeError, ValueError):
        dataset_manifest_run_summary_count_total = 0
    try:
        dataset_manifest_release_summary_count_total = int(
            payload.get("dataset_manifest_release_summary_count_total", 0)
        )
    except (TypeError, ValueError):
        dataset_manifest_release_summary_count_total = 0
    dataset_manifest_versions_text = _fmt_list(payload.get("dataset_manifest_versions"), 20)
    return (
        f"evaluated={evaluated_count}, gate_results={gate_result_counts_text}, gate_reasons_total={gate_reason_count_total}, "
        f"runs_total={run_summary_count_total}, run_statuses={run_status_counts_text}, "
        f"profiles=unique:{profile_unique_count},ids:{profile_ids_text},avg:{profile_count_avg:.3f},"
        f"max:{max_profile_count}({max_profile_batch}), "
        f"profile_sources=unique:{profile_source_unique_count},ids:{profile_source_ids_text},avg:{profile_source_count_avg:.3f},"
        f"max:{max_profile_source_count}({max_profile_source_batch}), "
        f"actor_patterns=unique:{actor_pattern_unique_count},ids:{actor_pattern_ids_text},avg:{actor_pattern_count_avg:.3f},"
        f"max:{max_actor_pattern_count}({max_actor_pattern_batch}), "
        f"lane_profiles=unique:{lane_profile_signature_unique_count},patterns:{lane_profile_signatures_text},"
        f"avg:{lane_profile_signature_count_avg:.3f},max:{max_lane_profile_signature_count}({max_lane_profile_signature_batch}), "
        f"npc_avg=avg:{npc_count_avg_avg:.3f},max:{npc_count_avg_max:.3f}({npc_count_avg_max_batch}),"
        f"npc_max:{npc_count_max_max}({npc_count_max_max_batch}), "
        f"npc_initial_gap_avg:{npc_initial_gap_avg_avg:.3f}({npc_initial_gap_avg_batch}),"
        f"npc_gap_step_avg:{npc_gap_step_avg_avg:.3f}({npc_gap_step_avg_batch}),"
        f"npc_speed_scale_avg:{npc_speed_scale_avg_avg:.3f}({npc_speed_scale_avg_batch}),"
        f"npc_speed_jitter_avg:{npc_speed_jitter_avg_avg:.3f}({npc_speed_jitter_avg_batch}), "
        f"lane_indices=unique:{lane_index_unique_count},avg_unique_per_manifest:{lane_index_unique_count_avg:.3f},"
        f"indices:{lane_indices_text}, "
        f"dataset_manifest=counts_rows_total:{dataset_manifest_counts_rows_total},"
        f"run_summaries_total:{dataset_manifest_run_summary_count_total},"
        f"release_summaries_total:{dataset_manifest_release_summary_count_total},"
        f"versions:{dataset_manifest_versions_text}"
    )


def _fmt_phase2_map_routing_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    status_counts_text = _fmt_counts(payload.get("status_counts", {}))
    try:
        error_count_total = int(payload.get("error_count_total", 0))
    except (TypeError, ValueError):
        error_count_total = 0
    try:
        warning_count_total = int(payload.get("warning_count_total", 0))
    except (TypeError, ValueError):
        warning_count_total = 0
    try:
        semantic_warning_count_total = int(payload.get("semantic_warning_count_total", 0))
    except (TypeError, ValueError):
        semantic_warning_count_total = 0
    try:
        unreachable_lane_count_total = int(payload.get("unreachable_lane_count_total", 0))
    except (TypeError, ValueError):
        unreachable_lane_count_total = 0
    try:
        non_reciprocal_link_count_total = int(payload.get("non_reciprocal_link_count_total", 0))
    except (TypeError, ValueError):
        non_reciprocal_link_count_total = 0
    try:
        continuity_gap_warning_count_total = int(payload.get("continuity_gap_warning_count_total", 0))
    except (TypeError, ValueError):
        continuity_gap_warning_count_total = 0
    try:
        max_unreachable_lane_count = int(payload.get("max_unreachable_lane_count", 0))
    except (TypeError, ValueError):
        max_unreachable_lane_count = 0
    highest_unreachable_batch_id = str(payload.get("highest_unreachable_batch_id", "")).strip() or "n/a"
    try:
        max_non_reciprocal_link_count = int(payload.get("max_non_reciprocal_link_count", 0))
    except (TypeError, ValueError):
        max_non_reciprocal_link_count = 0
    highest_non_reciprocal_batch_id = str(payload.get("highest_non_reciprocal_batch_id", "")).strip() or "n/a"
    try:
        max_continuity_gap_warning_count = int(payload.get("max_continuity_gap_warning_count", 0))
    except (TypeError, ValueError):
        max_continuity_gap_warning_count = 0
    highest_continuity_gap_batch_id = str(payload.get("highest_continuity_gap_batch_id", "")).strip() or "n/a"
    try:
        route_evaluated_count = int(payload.get("route_evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        route_evaluated_count = 0
    route_status_counts_text = _fmt_counts(payload.get("route_status_counts", {}))
    try:
        route_lane_count_total = int(payload.get("route_lane_count_total", 0))
    except (TypeError, ValueError):
        route_lane_count_total = 0
    try:
        route_hop_count_total = int(payload.get("route_hop_count_total", 0))
    except (TypeError, ValueError):
        route_hop_count_total = 0
    try:
        route_total_length_m_total = float(payload.get("route_total_length_m_total", 0.0))
    except (TypeError, ValueError):
        route_total_length_m_total = 0.0
    try:
        route_total_length_m_avg = float(payload.get("route_total_length_m_avg", 0.0))
    except (TypeError, ValueError):
        route_total_length_m_avg = 0.0
    try:
        route_segment_count_total = int(payload.get("route_segment_count_total", 0))
    except (TypeError, ValueError):
        route_segment_count_total = 0
    try:
        route_segment_count_avg = float(payload.get("route_segment_count_avg", 0.0))
    except (TypeError, ValueError):
        route_segment_count_avg = 0.0
    try:
        route_with_via_manifest_count = int(payload.get("route_with_via_manifest_count", 0))
    except (TypeError, ValueError):
        route_with_via_manifest_count = 0
    try:
        route_via_lane_count_total = int(payload.get("route_via_lane_count_total", 0))
    except (TypeError, ValueError):
        route_via_lane_count_total = 0
    try:
        route_via_lane_count_avg = float(payload.get("route_via_lane_count_avg", 0.0))
    except (TypeError, ValueError):
        route_via_lane_count_avg = 0.0
    try:
        max_route_lane_count = int(payload.get("max_route_lane_count", 0))
    except (TypeError, ValueError):
        max_route_lane_count = 0
    highest_route_lane_count_batch_id = str(payload.get("highest_route_lane_count_batch_id", "")).strip() or "n/a"
    try:
        max_route_hop_count = int(payload.get("max_route_hop_count", 0))
    except (TypeError, ValueError):
        max_route_hop_count = 0
    highest_route_hop_count_batch_id = str(payload.get("highest_route_hop_count_batch_id", "")).strip() or "n/a"
    try:
        max_route_segment_count = int(payload.get("max_route_segment_count", 0))
    except (TypeError, ValueError):
        max_route_segment_count = 0
    highest_route_segment_count_batch_id = str(payload.get("highest_route_segment_count_batch_id", "")).strip() or "n/a"
    try:
        max_route_via_lane_count = int(payload.get("max_route_via_lane_count", 0))
    except (TypeError, ValueError):
        max_route_via_lane_count = 0
    highest_route_via_lane_count_batch_id = (
        str(payload.get("highest_route_via_lane_count_batch_id", "")).strip() or "n/a"
    )
    try:
        max_route_total_length_m = float(payload.get("max_route_total_length_m", 0.0))
    except (TypeError, ValueError):
        max_route_total_length_m = 0.0
    highest_route_total_length_batch_id = (
        str(payload.get("highest_route_total_length_batch_id", "")).strip() or "n/a"
    )
    return (
        f"evaluated={evaluated_count}, statuses={status_counts_text}, "
        f"errors_total={error_count_total}, warnings_total={warning_count_total}, "
        f"semantic_warnings_total={semantic_warning_count_total}, "
        f"unreachable_total={unreachable_lane_count_total}, non_reciprocal_total={non_reciprocal_link_count_total}, "
        f"continuity_gap_total={continuity_gap_warning_count_total}, "
        f"max_unreachable={max_unreachable_lane_count} ({highest_unreachable_batch_id}), "
        f"max_non_reciprocal={max_non_reciprocal_link_count} ({highest_non_reciprocal_batch_id}), "
        f"max_continuity_gap={max_continuity_gap_warning_count} ({highest_continuity_gap_batch_id}), "
        f"route_evaluated={route_evaluated_count}, route_statuses={route_status_counts_text}, "
        f"route_lane_total={route_lane_count_total}, route_hop_total={route_hop_count_total}, "
        f"route_length_total_m={route_total_length_m_total:.3f}, route_length_avg_m={route_total_length_m_avg:.3f}, "
        f"route_segment_total={route_segment_count_total}, route_segment_avg={route_segment_count_avg:.3f}, "
        f"route_with_via={route_with_via_manifest_count}, route_via_lane_total={route_via_lane_count_total}, "
        f"route_via_lane_avg={route_via_lane_count_avg:.3f}, "
        f"max_route_lane={max_route_lane_count} ({highest_route_lane_count_batch_id}), "
        f"max_route_hop={max_route_hop_count} ({highest_route_hop_count_batch_id}), "
        f"max_route_segment={max_route_segment_count} ({highest_route_segment_count_batch_id}), "
        f"max_route_via_lane={max_route_via_lane_count} ({highest_route_via_lane_count_batch_id}), "
        f"max_route_length_m={max_route_total_length_m:.3f} ({highest_route_total_length_batch_id})"
    )


def _fmt_phase2_sensor_fidelity_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    fidelity_tier_counts_text = _fmt_counts(payload.get("fidelity_tier_counts", {}))
    try:
        fidelity_tier_score_avg = float(payload.get("fidelity_tier_score_avg", 0.0))
    except (TypeError, ValueError):
        fidelity_tier_score_avg = 0.0
    try:
        fidelity_tier_score_max = float(payload.get("fidelity_tier_score_max", 0.0))
    except (TypeError, ValueError):
        fidelity_tier_score_max = 0.0
    highest_fidelity_tier_score_batch_id = (
        str(payload.get("highest_fidelity_tier_score_batch_id", "")).strip() or "n/a"
    )
    try:
        sensor_frame_count_total = int(payload.get("sensor_frame_count_total", 0))
    except (TypeError, ValueError):
        sensor_frame_count_total = 0
    try:
        sensor_frame_count_avg = float(payload.get("sensor_frame_count_avg", 0.0))
    except (TypeError, ValueError):
        sensor_frame_count_avg = 0.0
    try:
        sensor_frame_count_max = int(payload.get("sensor_frame_count_max", 0))
    except (TypeError, ValueError):
        sensor_frame_count_max = 0
    highest_sensor_frame_count_batch_id = (
        str(payload.get("highest_sensor_frame_count_batch_id", "")).strip() or "n/a"
    )
    sensor_modality_counts_total_text = _fmt_counts(payload.get("sensor_modality_counts_total", {}))
    try:
        sensor_camera_noise_stddev_px_avg = float(payload.get("sensor_camera_noise_stddev_px_avg", 0.0))
    except (TypeError, ValueError):
        sensor_camera_noise_stddev_px_avg = 0.0
    try:
        sensor_lidar_point_count_total = int(payload.get("sensor_lidar_point_count_total", 0))
    except (TypeError, ValueError):
        sensor_lidar_point_count_total = 0
    try:
        sensor_lidar_point_count_avg = float(payload.get("sensor_lidar_point_count_avg", 0.0))
    except (TypeError, ValueError):
        sensor_lidar_point_count_avg = 0.0
    try:
        sensor_radar_false_positive_count_total = int(payload.get("sensor_radar_false_positive_count_total", 0))
    except (TypeError, ValueError):
        sensor_radar_false_positive_count_total = 0
    try:
        sensor_radar_false_positive_count_avg = float(payload.get("sensor_radar_false_positive_count_avg", 0.0))
    except (TypeError, ValueError):
        sensor_radar_false_positive_count_avg = 0.0
    try:
        sensor_radar_false_positive_rate_avg = float(payload.get("sensor_radar_false_positive_rate_avg", 0.0))
    except (TypeError, ValueError):
        sensor_radar_false_positive_rate_avg = 0.0
    try:
        sensor_camera_depth_enabled_frame_count_total = int(
            payload.get("sensor_camera_depth_enabled_frame_count_total", 0)
        )
    except (TypeError, ValueError):
        sensor_camera_depth_enabled_frame_count_total = 0
    try:
        sensor_camera_depth_min_m_avg = float(payload.get("sensor_camera_depth_min_m_avg", 0.0))
    except (TypeError, ValueError):
        sensor_camera_depth_min_m_avg = 0.0
    try:
        sensor_camera_depth_max_m_avg = float(payload.get("sensor_camera_depth_max_m_avg", 0.0))
    except (TypeError, ValueError):
        sensor_camera_depth_max_m_avg = 0.0
    try:
        sensor_camera_depth_bit_depth_avg = float(payload.get("sensor_camera_depth_bit_depth_avg", 0.0))
    except (TypeError, ValueError):
        sensor_camera_depth_bit_depth_avg = 0.0
    sensor_camera_depth_mode_counts_total_text = _fmt_counts(
        payload.get("sensor_camera_depth_mode_counts_total", {})
    )
    try:
        sensor_camera_optical_flow_enabled_frame_count_total = int(
            payload.get("sensor_camera_optical_flow_enabled_frame_count_total", 0)
        )
    except (TypeError, ValueError):
        sensor_camera_optical_flow_enabled_frame_count_total = 0
    try:
        sensor_camera_optical_flow_magnitude_px_avg = float(
            payload.get("sensor_camera_optical_flow_magnitude_px_avg", 0.0)
        )
    except (TypeError, ValueError):
        sensor_camera_optical_flow_magnitude_px_avg = 0.0
    sensor_camera_optical_flow_velocity_direction_counts_total_text = _fmt_counts(
        payload.get("sensor_camera_optical_flow_velocity_direction_counts_total", {})
    )
    sensor_camera_optical_flow_y_axis_direction_counts_total_text = _fmt_counts(
        payload.get("sensor_camera_optical_flow_y_axis_direction_counts_total", {})
    )
    return (
        f"evaluated={evaluated_count}, tier_counts={fidelity_tier_counts_text}, "
        f"fidelity_score_avg={fidelity_tier_score_avg:.3f}, "
        f"fidelity_score_max={fidelity_tier_score_max:.3f} ({highest_fidelity_tier_score_batch_id}), "
        f"frame_total={sensor_frame_count_total}, frame_avg={sensor_frame_count_avg:.3f}, "
        f"frame_max={sensor_frame_count_max} ({highest_sensor_frame_count_batch_id}), "
        f"modality_total={sensor_modality_counts_total_text}, "
        f"camera_noise_avg_px={sensor_camera_noise_stddev_px_avg:.3f}, "
        f"lidar_point_total={sensor_lidar_point_count_total}, "
        f"lidar_point_avg={sensor_lidar_point_count_avg:.3f}, "
        f"radar_fp_total={sensor_radar_false_positive_count_total}, "
        f"radar_fp_avg={sensor_radar_false_positive_count_avg:.3f}, "
        f"radar_fp_rate_avg={sensor_radar_false_positive_rate_avg:.6f}, "
        f"camera_depth_enabled_total={sensor_camera_depth_enabled_frame_count_total}, "
        f"camera_depth_min_avg_m={sensor_camera_depth_min_m_avg:.3f}, "
        f"camera_depth_max_avg_m={sensor_camera_depth_max_m_avg:.3f}, "
        f"camera_depth_bit_depth_avg={sensor_camera_depth_bit_depth_avg:.3f}, "
        f"camera_depth_modes={sensor_camera_depth_mode_counts_total_text}, "
        f"camera_flow_enabled_total={sensor_camera_optical_flow_enabled_frame_count_total}, "
        f"camera_flow_mag_avg_px={sensor_camera_optical_flow_magnitude_px_avg:.3f}, "
        "camera_flow_velocity_dirs="
        f"{sensor_camera_optical_flow_velocity_direction_counts_total_text}, "
        "camera_flow_y_axis_dirs="
        f"{sensor_camera_optical_flow_y_axis_direction_counts_total_text}"
    )


def _fmt_phase2_log_replay_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    if evaluated_count <= 0:
        return "n/a"
    status_counts_text = _fmt_counts(payload.get("status_counts", {}))
    run_status_counts_text = _fmt_counts(payload.get("run_status_counts", {}))
    run_source_counts_text = _fmt_counts(payload.get("run_source_counts", {}))
    try:
        manifest_present_count = int(payload.get("manifest_present_count", 0))
    except (TypeError, ValueError):
        manifest_present_count = 0
    try:
        summary_present_count = int(payload.get("summary_present_count", 0))
    except (TypeError, ValueError):
        summary_present_count = 0
    try:
        missing_manifest_count = int(payload.get("missing_manifest_count", 0))
    except (TypeError, ValueError):
        missing_manifest_count = 0
    try:
        missing_summary_count = int(payload.get("missing_summary_count", 0))
    except (TypeError, ValueError):
        missing_summary_count = 0
    try:
        log_id_present_count = int(payload.get("log_id_present_count", 0))
    except (TypeError, ValueError):
        log_id_present_count = 0
    try:
        map_id_present_count = int(payload.get("map_id_present_count", 0))
    except (TypeError, ValueError):
        map_id_present_count = 0
    return (
        f"evaluated={evaluated_count}, statuses={status_counts_text}, "
        f"run_statuses={run_status_counts_text}, run_sources={run_source_counts_text}, "
        f"manifest_present={manifest_present_count}, summary_present={summary_present_count}, "
        f"missing_manifest={missing_manifest_count}, missing_summary={missing_summary_count}, "
        f"log_id_present={log_id_present_count}, map_id_present={map_id_present_count}"
    )


def _fmt_runtime_native_smoke_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    module_summaries = payload.get("module_summaries", {})
    if not isinstance(module_summaries, dict) or not module_summaries:
        return "n/a"
    try:
        evaluated_count = int(payload.get("evaluated_manifest_count", 0))
    except (TypeError, ValueError):
        evaluated_count = 0
    all_statuses_text = _fmt_counts(payload.get("all_modules_status_counts", {}))
    try:
        all_pass_count = int(payload.get("all_modules_pass_manifest_count", 0))
    except (TypeError, ValueError):
        all_pass_count = 0
    module_parts: list[str] = []
    for module_name in ("object_sim", "log_sim", "map_toolset"):
        module_payload = module_summaries.get(module_name, {})
        if not isinstance(module_payload, dict):
            continue
        try:
            module_evaluated_count = int(module_payload.get("evaluated_manifest_count", 0))
        except (TypeError, ValueError):
            module_evaluated_count = 0
        module_statuses_text = _fmt_counts(module_payload.get("status_counts", {}))
        module_parts.append(
            f"{module_name}(evaluated={module_evaluated_count},statuses={module_statuses_text})"
        )
    return (
        f"evaluated={evaluated_count}, all_statuses={all_statuses_text}, "
        f"all_pass={all_pass_count}, modules={'; '.join(module_parts) or 'n/a'}"
    )


def _fmt_runtime_evidence_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        record_count = int(payload.get("record_count", 0))
    except (TypeError, ValueError):
        record_count = 0
    try:
        validated_count = int(payload.get("validated_count", 0))
    except (TypeError, ValueError):
        validated_count = 0
    try:
        failed_count = int(payload.get("failed_count", 0))
    except (TypeError, ValueError):
        failed_count = 0
    try:
        availability_true_count = int(payload.get("availability_true_count", 0))
    except (TypeError, ValueError):
        availability_true_count = 0
    try:
        availability_false_count = int(payload.get("availability_false_count", 0))
    except (TypeError, ValueError):
        availability_false_count = 0
    try:
        availability_unknown_count = int(payload.get("availability_unknown_count", 0))
    except (TypeError, ValueError):
        availability_unknown_count = 0
    try:
        probe_checked_count = int(payload.get("probe_checked_count", 0))
    except (TypeError, ValueError):
        probe_checked_count = 0
    try:
        probe_executed_count = int(payload.get("probe_executed_count", 0))
    except (TypeError, ValueError):
        probe_executed_count = 0
    try:
        runtime_bin_missing_count = int(payload.get("runtime_bin_missing_count", 0))
    except (TypeError, ValueError):
        runtime_bin_missing_count = 0
    try:
        provenance_complete_count = int(payload.get("provenance_complete_count", 0))
    except (TypeError, ValueError):
        provenance_complete_count = 0
    try:
        provenance_missing_count = int(payload.get("provenance_missing_count", 0))
    except (TypeError, ValueError):
        provenance_missing_count = 0
    runtime_counts = payload.get("runtime_counts", {})
    runtime_counts_text = _fmt_counts(runtime_counts)
    status_counts = payload.get("status_counts", {})
    status_counts_text = _fmt_counts(status_counts)
    return (
        f"artifacts={artifact_count}, records={record_count}, "
        f"validated={validated_count}, failed={failed_count}, "
        f"runtimes={runtime_counts_text}, statuses={status_counts_text}, "
        f"availability=true:{availability_true_count},false:{availability_false_count},"
        f"unknown:{availability_unknown_count}, "
        f"probe_checked={probe_checked_count}, probe_executed={probe_executed_count}, "
        f"runtime_bin_missing={runtime_bin_missing_count}, "
        f"provenance_complete={provenance_complete_count}, provenance_missing={provenance_missing_count}"
    )


def _fmt_runtime_evidence_probe_args_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        effective_count = int(payload.get("probe_args_effective_count", 0))
    except (TypeError, ValueError):
        effective_count = 0
    try:
        requested_count = int(payload.get("probe_args_requested_count", 0))
    except (TypeError, ValueError):
        requested_count = 0
    try:
        flag_present_count = int(payload.get("probe_flag_present_count", 0))
    except (TypeError, ValueError):
        flag_present_count = 0
    try:
        flag_requested_present_count = int(payload.get("probe_flag_requested_present_count", 0))
    except (TypeError, ValueError):
        flag_requested_present_count = 0
    try:
        policy_enable_true_count = int(payload.get("probe_policy_enable_true_count", 0))
    except (TypeError, ValueError):
        policy_enable_true_count = 0
    try:
        policy_execute_true_count = int(payload.get("probe_policy_execute_true_count", 0))
    except (TypeError, ValueError):
        policy_execute_true_count = 0
    try:
        policy_require_availability_true_count = int(payload.get("probe_policy_require_availability_true_count", 0))
    except (TypeError, ValueError):
        policy_require_availability_true_count = 0
    try:
        policy_flag_input_present_count = int(payload.get("probe_policy_flag_input_present_count", 0))
    except (TypeError, ValueError):
        policy_flag_input_present_count = 0
    try:
        policy_args_shlex_input_present_count = int(payload.get("probe_policy_args_shlex_input_present_count", 0))
    except (TypeError, ValueError):
        policy_args_shlex_input_present_count = 0
    source_counts = payload.get("probe_args_source_counts", {})
    source_counts_text = _fmt_counts(source_counts)
    requested_source_counts = payload.get("probe_args_requested_source_counts", {})
    requested_source_counts_text = _fmt_counts(requested_source_counts)
    value_counts = payload.get("probe_arg_value_counts", {})
    value_counts_text = _fmt_counts(value_counts)
    requested_value_counts = payload.get("probe_arg_requested_value_counts", {})
    requested_value_counts_text = _fmt_counts(requested_value_counts)
    return (
        f"effective={effective_count}, requested={requested_count}, "
        f"sources={source_counts_text}, requested_sources={requested_source_counts_text}, "
        f"arg_values={value_counts_text}, requested_arg_values={requested_value_counts_text}, "
        f"flags=effective:{flag_present_count},requested:{flag_requested_present_count}, "
        "policy="
        f"enable:{policy_enable_true_count},execute:{policy_execute_true_count},"
        f"require_availability:{policy_require_availability_true_count},"
        f"flag_input:{policy_flag_input_present_count},"
        f"args_shlex_input:{policy_args_shlex_input_present_count}"
    )


def _fmt_runtime_evidence_scenario_contract_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(payload.get("scenario_contract_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        ready_true_count = int(payload.get("scenario_runtime_ready_true_count", 0))
    except (TypeError, ValueError):
        ready_true_count = 0
    try:
        ready_false_count = int(payload.get("scenario_runtime_ready_false_count", 0))
    except (TypeError, ValueError):
        ready_false_count = 0
    try:
        ready_unknown_count = int(payload.get("scenario_runtime_ready_unknown_count", 0))
    except (TypeError, ValueError):
        ready_unknown_count = 0
    try:
        actor_total = int(payload.get("scenario_actor_count_total", 0))
    except (TypeError, ValueError):
        actor_total = 0
    try:
        sensor_stream_total = int(payload.get("scenario_sensor_stream_count_total", 0))
    except (TypeError, ValueError):
        sensor_stream_total = 0
    try:
        step_total = int(payload.get("scenario_executed_step_count_total", 0))
    except (TypeError, ValueError):
        step_total = 0
    try:
        sim_duration_sec_total = float(payload.get("scenario_sim_duration_sec_total", 0.0))
    except (TypeError, ValueError):
        sim_duration_sec_total = 0.0
    status_counts = payload.get("scenario_contract_status_counts", {})
    status_counts_text = _fmt_counts(status_counts)
    return (
        f"checked={checked_count}, ready=true:{ready_true_count},false:{ready_false_count},"
        f"unknown:{ready_unknown_count}, statuses={status_counts_text}, "
        f"actor_total={actor_total}, sensor_stream_total={sensor_stream_total}, "
        f"step_total={step_total}, sim_duration_sec_total={sim_duration_sec_total:.3f}"
    )


def _fmt_runtime_evidence_interop_contract_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(payload.get("interop_contract_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        ready_true_count = int(payload.get("interop_runtime_ready_true_count", 0))
    except (TypeError, ValueError):
        ready_true_count = 0
    try:
        ready_false_count = int(payload.get("interop_runtime_ready_false_count", 0))
    except (TypeError, ValueError):
        ready_false_count = 0
    try:
        ready_unknown_count = int(payload.get("interop_runtime_ready_unknown_count", 0))
    except (TypeError, ValueError):
        ready_unknown_count = 0
    try:
        imported_actor_total = int(payload.get("interop_imported_actor_count_total", 0))
    except (TypeError, ValueError):
        imported_actor_total = 0
    try:
        xosc_entity_total = int(payload.get("interop_xosc_entity_count_total", 0))
    except (TypeError, ValueError):
        xosc_entity_total = 0
    try:
        xodr_road_total = int(payload.get("interop_xodr_road_count_total", 0))
    except (TypeError, ValueError):
        xodr_road_total = 0
    try:
        step_total = int(payload.get("interop_executed_step_count_total", 0))
    except (TypeError, ValueError):
        step_total = 0
    try:
        sim_duration_sec_total = float(payload.get("interop_sim_duration_sec_total", 0.0))
    except (TypeError, ValueError):
        sim_duration_sec_total = 0.0
    status_counts = payload.get("interop_contract_status_counts", {})
    status_counts_text = _fmt_counts(status_counts)
    return (
        f"checked={checked_count}, ready=true:{ready_true_count},false:{ready_false_count},"
        f"unknown:{ready_unknown_count}, statuses={status_counts_text}, "
        f"imported_actor_total={imported_actor_total}, xosc_entity_total={xosc_entity_total}, "
        f"xodr_road_total={xodr_road_total}, step_total={step_total}, "
        f"sim_duration_sec_total={sim_duration_sec_total:.3f}"
    )


def _fmt_runtime_evidence_interop_export_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(payload.get("interop_export_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        actor_manifest_total = int(payload.get("interop_export_actor_count_manifest_total", 0))
    except (TypeError, ValueError):
        actor_manifest_total = 0
    try:
        sensor_stream_manifest_total = int(payload.get("interop_export_sensor_stream_count_manifest_total", 0))
    except (TypeError, ValueError):
        sensor_stream_manifest_total = 0
    try:
        xosc_entity_total = int(payload.get("interop_export_xosc_entity_count_total", 0))
    except (TypeError, ValueError):
        xosc_entity_total = 0
    try:
        xodr_road_total = int(payload.get("interop_export_xodr_road_count_total", 0))
    except (TypeError, ValueError):
        xodr_road_total = 0
    try:
        generated_road_length_m_total = float(payload.get("interop_export_generated_road_length_m_total", 0.0))
    except (TypeError, ValueError):
        generated_road_length_m_total = 0.0
    status_counts = payload.get("interop_export_status_counts", {})
    status_counts_text = _fmt_counts(status_counts)
    return (
        f"checked={checked_count}, statuses={status_counts_text}, "
        f"actor_manifest_total={actor_manifest_total}, "
        f"sensor_stream_manifest_total={sensor_stream_manifest_total}, "
        f"xosc_entity_total={xosc_entity_total}, xodr_road_total={xodr_road_total}, "
        f"generated_road_length_m_total={generated_road_length_m_total:.3f}"
    )


def _fmt_runtime_evidence_interop_import_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(payload.get("interop_import_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        manifest_consistent_true_count = int(payload.get("interop_import_manifest_consistent_true_count", 0))
    except (TypeError, ValueError):
        manifest_consistent_true_count = 0
    try:
        manifest_consistent_false_count = int(payload.get("interop_import_manifest_consistent_false_count", 0))
    except (TypeError, ValueError):
        manifest_consistent_false_count = 0
    try:
        manifest_consistent_unknown_count = int(payload.get("interop_import_manifest_consistent_unknown_count", 0))
    except (TypeError, ValueError):
        manifest_consistent_unknown_count = 0
    try:
        actor_manifest_total = int(payload.get("interop_import_actor_count_manifest_total", 0))
    except (TypeError, ValueError):
        actor_manifest_total = 0
    try:
        xosc_entity_total = int(payload.get("interop_import_xosc_entity_count_total", 0))
    except (TypeError, ValueError):
        xosc_entity_total = 0
    try:
        xodr_road_total = int(payload.get("interop_import_xodr_road_count_total", 0))
    except (TypeError, ValueError):
        xodr_road_total = 0
    try:
        xodr_total_road_length_m_total = float(payload.get("interop_import_xodr_total_road_length_m_total", 0.0))
    except (TypeError, ValueError):
        xodr_total_road_length_m_total = 0.0
    status_counts = payload.get("interop_import_status_counts", {})
    status_counts_text = _fmt_counts(status_counts)
    return (
        f"checked={checked_count}, statuses={status_counts_text}, "
        "manifest_consistent=true:"
        f"{manifest_consistent_true_count},false:{manifest_consistent_false_count},"
        f"unknown:{manifest_consistent_unknown_count}, "
        f"actor_manifest_total={actor_manifest_total}, xosc_entity_total={xosc_entity_total}, "
        f"xodr_road_total={xodr_road_total}, "
        f"xodr_total_road_length_m_total={xodr_total_road_length_m_total:.3f}"
    )


def _fmt_runtime_evidence_interop_import_modes(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    manifest_mode_counts_text = _fmt_counts(payload.get("interop_import_manifest_consistency_mode_counts", {}))
    export_mode_counts_text = _fmt_counts(payload.get("interop_import_export_consistency_mode_counts", {}))
    try:
        require_manifest_true_count = int(
            payload.get("interop_import_require_manifest_consistency_input_true_count", 0)
        )
    except (TypeError, ValueError):
        require_manifest_true_count = 0
    try:
        require_export_true_count = int(
            payload.get("interop_import_require_export_consistency_input_true_count", 0)
        )
    except (TypeError, ValueError):
        require_export_true_count = 0
    if (
        manifest_mode_counts_text == "n/a"
        and export_mode_counts_text == "n/a"
        and require_manifest_true_count <= 0
        and require_export_true_count <= 0
    ):
        return "n/a"
    return (
        f"manifest={manifest_mode_counts_text}, export={export_mode_counts_text}, "
        f"require_inputs=manifest:{require_manifest_true_count},export:{require_export_true_count}"
    )


def _fmt_runtime_evidence_interop_import_inconsistent_records(payload: Any, *, max_items: int = 5) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    rows = payload.get("interop_import_manifest_inconsistent_records", [])
    if not isinstance(rows, list) or not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        release_id = str(row.get("release_id", "")).strip() or "release_unknown"
        runtime_name = str(row.get("runtime", "")).strip().lower() or "runtime_unknown"
        try:
            actor_count_manifest = int(row.get("actor_count_manifest", 0) or 0)
        except (TypeError, ValueError):
            actor_count_manifest = 0
        try:
            xosc_entity_count = int(row.get("xosc_entity_count", 0) or 0)
        except (TypeError, ValueError):
            xosc_entity_count = 0
        normalized.append(
            f"{profile_id}:{release_id}:{runtime_name}:manifest={actor_count_manifest}:imported={xosc_entity_count}"
        )
    remaining = max(0, len(rows) - max(1, int(max_items)))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized) if normalized else "n/a"


def _fmt_runtime_evidence_scene_result_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        checked_count = int(payload.get("scene_result_checked_count", 0))
    except (TypeError, ValueError):
        checked_count = 0
    try:
        ready_true_count = int(payload.get("scene_result_runtime_ready_true_count", 0))
    except (TypeError, ValueError):
        ready_true_count = 0
    try:
        ready_false_count = int(payload.get("scene_result_runtime_ready_false_count", 0))
    except (TypeError, ValueError):
        ready_false_count = 0
    try:
        ready_unknown_count = int(payload.get("scene_result_runtime_ready_unknown_count", 0))
    except (TypeError, ValueError):
        ready_unknown_count = 0
    try:
        actor_total = int(payload.get("scene_result_actor_count_total", 0))
    except (TypeError, ValueError):
        actor_total = 0
    try:
        sensor_stream_total = int(payload.get("scene_result_sensor_stream_count_total", 0))
    except (TypeError, ValueError):
        sensor_stream_total = 0
    try:
        step_total = int(payload.get("scene_result_executed_step_count_total", 0))
    except (TypeError, ValueError):
        step_total = 0
    try:
        sim_duration_sec_total = float(payload.get("scene_result_sim_duration_sec_total", 0.0))
    except (TypeError, ValueError):
        sim_duration_sec_total = 0.0
    try:
        coverage_ratio_avg = float(payload.get("scene_result_coverage_ratio_avg", 0.0))
    except (TypeError, ValueError):
        coverage_ratio_avg = 0.0
    try:
        coverage_ratio_samples = int(payload.get("scene_result_coverage_ratio_sample_count", 0))
    except (TypeError, ValueError):
        coverage_ratio_samples = 0
    try:
        ego_travel_distance_m_total = float(payload.get("scene_result_ego_travel_distance_m_total", 0.0))
    except (TypeError, ValueError):
        ego_travel_distance_m_total = 0.0
    status_counts = payload.get("scene_result_status_counts", {})
    status_counts_text = _fmt_counts(status_counts)
    return (
        f"checked={checked_count}, ready=true:{ready_true_count},false:{ready_false_count},"
        f"unknown:{ready_unknown_count}, statuses={status_counts_text}, "
        f"actor_total={actor_total}, sensor_stream_total={sensor_stream_total}, "
        f"step_total={step_total}, sim_duration_sec_total={sim_duration_sec_total:.3f}, "
        f"coverage_ratio_avg={coverage_ratio_avg:.3f}, coverage_ratio_samples={coverage_ratio_samples}, "
        f"ego_travel_distance_m_total={ego_travel_distance_m_total:.3f}"
    )


def _fmt_runtime_lane_execution_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        runtime_row_count = int(payload.get("runtime_row_count", 0))
    except (TypeError, ValueError):
        runtime_row_count = 0
    try:
        pass_count = int(payload.get("pass_count", 0))
    except (TypeError, ValueError):
        pass_count = 0
    try:
        fail_count = int(payload.get("fail_count", 0))
    except (TypeError, ValueError):
        fail_count = 0
    try:
        unknown_count = int(payload.get("unknown_count", 0))
    except (TypeError, ValueError):
        unknown_count = 0
    runtime_counts = payload.get("runtime_counts", {})
    runtime_counts_text = _fmt_counts(runtime_counts)
    result_counts = payload.get("result_counts", {})
    result_counts_text = _fmt_counts(result_counts)
    runtime_failure_reason_counts = payload.get("runtime_failure_reason_counts", {})
    runtime_failure_reason_counts_text = _fmt_counts(runtime_failure_reason_counts)
    lane_counts = payload.get("lane_counts", {})
    lane_counts_text = _fmt_counts(lane_counts)
    lane_row_counts = payload.get("lane_row_counts", {})
    lane_row_counts_text = _fmt_counts(lane_row_counts)
    runner_platform_counts = payload.get("runner_platform_counts", {})
    runner_platform_counts_text = _fmt_counts(runner_platform_counts)
    sim_runtime_input_counts = payload.get("sim_runtime_input_counts", {})
    sim_runtime_input_counts_text = _fmt_counts(sim_runtime_input_counts)
    dry_run_counts = payload.get("dry_run_counts", {})
    dry_run_counts_text = _fmt_counts(dry_run_counts)
    continue_on_runtime_failure_counts = payload.get("continue_on_runtime_failure_counts", {})
    continue_on_runtime_failure_counts_text = _fmt_counts(continue_on_runtime_failure_counts)
    runtime_exec_lane_warn_min_rows_counts = payload.get("runtime_exec_lane_warn_min_rows_counts", {})
    runtime_exec_lane_warn_min_rows_counts_text = _fmt_counts(runtime_exec_lane_warn_min_rows_counts)
    runtime_exec_lane_hold_min_rows_counts = payload.get("runtime_exec_lane_hold_min_rows_counts", {})
    runtime_exec_lane_hold_min_rows_counts_text = _fmt_counts(runtime_exec_lane_hold_min_rows_counts)
    runtime_compare_warn_min_artifacts_with_diffs_counts = payload.get(
        "runtime_compare_warn_min_artifacts_with_diffs_counts",
        {},
    )
    runtime_compare_warn_min_artifacts_with_diffs_counts_text = _fmt_counts(
        runtime_compare_warn_min_artifacts_with_diffs_counts
    )
    runtime_compare_hold_min_artifacts_with_diffs_counts = payload.get(
        "runtime_compare_hold_min_artifacts_with_diffs_counts",
        {},
    )
    runtime_compare_hold_min_artifacts_with_diffs_counts_text = _fmt_counts(
        runtime_compare_hold_min_artifacts_with_diffs_counts
    )
    runtime_asset_profile_counts = payload.get("runtime_asset_profile_counts", {})
    runtime_asset_profile_counts_text = _fmt_counts(runtime_asset_profile_counts)
    runtime_asset_archive_sha256_mode_counts = payload.get("runtime_asset_archive_sha256_mode_counts", {})
    runtime_asset_archive_sha256_mode_counts_text = _fmt_counts(runtime_asset_archive_sha256_mode_counts)
    runtime_evidence_missing_runtime_counts = payload.get("runtime_evidence_missing_runtime_counts", {})
    runtime_evidence_missing_runtime_counts_text = _fmt_counts(runtime_evidence_missing_runtime_counts)
    try:
        runtime_evidence_path_present_count = int(payload.get("runtime_evidence_path_present_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_path_present_count = 0
    try:
        runtime_evidence_exists_true_count = int(payload.get("runtime_evidence_exists_true_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_exists_true_count = 0
    try:
        runtime_evidence_exists_false_count = int(payload.get("runtime_evidence_exists_false_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_exists_false_count = 0
    try:
        runtime_evidence_exists_unknown_count = int(payload.get("runtime_evidence_exists_unknown_count", 0))
    except (TypeError, ValueError):
        runtime_evidence_exists_unknown_count = 0
    return (
        f"artifacts={artifact_count}, rows={runtime_row_count}, "
        f"pass={pass_count}, fail={fail_count}, unknown={unknown_count}, "
        f"results={result_counts_text}, failure_reasons={runtime_failure_reason_counts_text}, "
        f"runtimes={runtime_counts_text}, lanes={lane_counts_text}, "
        f"asset_profiles={runtime_asset_profile_counts_text}, "
        f"archive_sha256_modes={runtime_asset_archive_sha256_mode_counts_text}, "
        f"evidence_paths=present:{runtime_evidence_path_present_count},"
        f"exists:{runtime_evidence_exists_true_count},"
        f"missing:{runtime_evidence_exists_false_count},"
        f"unknown:{runtime_evidence_exists_unknown_count},"
        f"evidence_missing_runtimes={runtime_evidence_missing_runtime_counts_text}, "
        f"lane_rows={lane_row_counts_text}, "
        f"runner_platforms={runner_platform_counts_text}, "
        f"sim_runtime_inputs={sim_runtime_input_counts_text}, "
        f"dry_runs={dry_run_counts_text}, "
        f"continue_on_runtime_failure={continue_on_runtime_failure_counts_text}, "
        f"exec_lane_warn_min_rows={runtime_exec_lane_warn_min_rows_counts_text}, "
        f"exec_lane_hold_min_rows={runtime_exec_lane_hold_min_rows_counts_text}, "
        "runtime_compare_warn_min_artifacts_with_diffs="
        f"{runtime_compare_warn_min_artifacts_with_diffs_counts_text}, "
        "runtime_compare_hold_min_artifacts_with_diffs="
        f"{runtime_compare_hold_min_artifacts_with_diffs_counts_text}"
    )


def _fmt_runtime_evidence_compare_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        artifacts_with_diffs_count = int(payload.get("artifacts_with_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_with_diffs_count = 0
    try:
        artifacts_without_diffs_count = int(payload.get("artifacts_without_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_without_diffs_count = 0
    try:
        top_level_mismatches_count = int(payload.get("top_level_mismatches_count", 0))
    except (TypeError, ValueError):
        top_level_mismatches_count = 0
    try:
        status_count_diffs_count = int(payload.get("status_count_diffs_count", 0))
    except (TypeError, ValueError):
        status_count_diffs_count = 0
    try:
        runtime_count_diffs_count = int(payload.get("runtime_count_diffs_count", 0))
    except (TypeError, ValueError):
        runtime_count_diffs_count = 0
    try:
        interop_import_status_count_diffs_count = int(payload.get("interop_import_status_count_diffs_count", 0))
    except (TypeError, ValueError):
        interop_import_status_count_diffs_count = 0
    try:
        interop_import_manifest_consistency_diffs_count = int(
            payload.get("interop_import_manifest_consistency_diffs_count", 0)
        )
    except (TypeError, ValueError):
        interop_import_manifest_consistency_diffs_count = 0
    try:
        interop_import_profile_diff_count = int(payload.get("interop_import_profile_diff_count", 0))
    except (TypeError, ValueError):
        interop_import_profile_diff_count = 0
    try:
        shared_profile_count = int(payload.get("shared_profile_count", 0))
    except (TypeError, ValueError):
        shared_profile_count = 0
    try:
        profile_left_only_count = int(payload.get("profile_left_only_count", 0))
    except (TypeError, ValueError):
        profile_left_only_count = 0
    try:
        profile_right_only_count = int(payload.get("profile_right_only_count", 0))
    except (TypeError, ValueError):
        profile_right_only_count = 0
    try:
        profile_diff_count = int(payload.get("profile_diff_count", 0))
    except (TypeError, ValueError):
        profile_diff_count = 0
    label_pair_counts_text = _fmt_counts(payload.get("label_pair_counts"))
    return (
        f"artifacts={artifact_count}, with_diffs={artifacts_with_diffs_count}, "
        f"without_diffs={artifacts_without_diffs_count}, "
        f"top_level_mismatches={top_level_mismatches_count}, "
        f"status_count_diffs={status_count_diffs_count}, "
        f"runtime_count_diffs={runtime_count_diffs_count}, "
        f"interop_import_status_count_diffs={interop_import_status_count_diffs_count}, "
        "interop_import_manifest_consistency_diffs="
        f"{interop_import_manifest_consistency_diffs_count}, "
        f"interop_import_profile_diffs={interop_import_profile_diff_count}, "
        "profile_presence="
        f"shared:{shared_profile_count},left_only:{profile_left_only_count},"
        f"right_only:{profile_right_only_count}, "
        f"profile_diffs={profile_diff_count}, "
        f"label_pairs={label_pair_counts_text}"
    )


def _fmt_runtime_evidence_compare_interop_import_mode_diff_counts(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        manifest_mode = int(payload.get("interop_import_manifest_mode_count_diffs_count", 0))
    except (TypeError, ValueError):
        manifest_mode = 0
    try:
        export_mode = int(payload.get("interop_import_export_mode_count_diffs_count", 0))
    except (TypeError, ValueError):
        export_mode = 0
    try:
        require_manifest_input = int(payload.get("interop_import_require_manifest_input_count_diffs_count", 0))
    except (TypeError, ValueError):
        require_manifest_input = 0
    try:
        require_export_input = int(payload.get("interop_import_require_export_input_count_diffs_count", 0))
    except (TypeError, ValueError):
        require_export_input = 0
    return (
        f"manifest_mode={manifest_mode}, export_mode={export_mode}, "
        f"require_manifest_input={require_manifest_input}, require_export_input={require_export_input}"
    )


def _fmt_runtime_native_summary_compare_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    try:
        artifact_count = int(payload.get("artifact_count", 0))
    except (TypeError, ValueError):
        artifact_count = 0
    if artifact_count <= 0:
        return "n/a"
    try:
        artifacts_with_diffs_count = int(payload.get("artifacts_with_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_with_diffs_count = 0
    try:
        artifacts_without_diffs_count = int(payload.get("artifacts_without_diffs_count", 0))
    except (TypeError, ValueError):
        artifacts_without_diffs_count = 0
    try:
        versions_total = int(payload.get("versions_total", 0))
    except (TypeError, ValueError):
        versions_total = 0
    try:
        comparisons_total = int(payload.get("comparisons_total", 0))
    except (TypeError, ValueError):
        comparisons_total = 0
    try:
        versions_with_diffs_total = int(payload.get("versions_with_diffs_total", 0))
    except (TypeError, ValueError):
        versions_with_diffs_total = 0
    label_pair_counts_text = _fmt_counts(payload.get("label_pair_counts"))
    field_diff_counts_text = _fmt_counts(payload.get("field_diff_counts"))
    versions_with_diffs_counts_text = _fmt_counts(payload.get("versions_with_diffs_counts"))
    return (
        f"artifacts={artifact_count}, with_diffs={artifacts_with_diffs_count}, "
        f"without_diffs={artifacts_without_diffs_count}, versions_total={versions_total}, "
        f"comparisons_total={comparisons_total}, versions_with_diffs_total={versions_with_diffs_total}, "
        f"label_pairs={label_pair_counts_text}, field_diff_counts={field_diff_counts_text}, "
        f"versions_with_diffs={versions_with_diffs_counts_text}"
    )


def _fmt_runtime_evidence_compare_interop_import_profile_diffs(payload: Any, *, max_items: int = 5) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    rows = payload.get("interop_import_profile_diff_records", [])
    if not isinstance(rows, list) or not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        field_keys_raw = row.get("field_keys", [])
        numeric_keys_raw = row.get("numeric_keys", [])
        field_keys = (
            [str(key).strip() for key in field_keys_raw if str(key).strip()]
            if isinstance(field_keys_raw, list)
            else []
        )
        numeric_keys = (
            [str(key).strip() for key in numeric_keys_raw if str(key).strip()]
            if isinstance(numeric_keys_raw, list)
            else []
        )
        field_text = "|".join(sorted(set(field_keys))) if field_keys else "n/a"
        numeric_text = "|".join(sorted(set(numeric_keys))) if numeric_keys else "n/a"
        normalized.append(
            f"{left_label}_vs_{right_label}:{profile_id}:fields={field_text}:numeric={numeric_text}"
        )
    remaining = max(0, len(rows) - max(1, int(max_items)))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized) if normalized else "n/a"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_counts(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    field_counts_text = _fmt_counts(payload.get("interop_import_profile_diff_field_counts"))
    numeric_counts_text = _fmt_counts(payload.get("interop_import_profile_diff_numeric_counts"))
    if field_counts_text == "n/a" and numeric_counts_text == "n/a":
        return "n/a"
    return f"fields={field_counts_text}, numeric={numeric_counts_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_breakdown(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    label_pair_counts_text = _fmt_counts(payload.get("interop_import_profile_diff_label_pair_counts"))
    profile_counts_text = _fmt_counts(payload.get("interop_import_profile_diff_profile_counts"))
    if label_pair_counts_text == "n/a" and profile_counts_text == "n/a":
        return "n/a"
    return f"label_pairs={label_pair_counts_text}, profiles={profile_counts_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    delta_totals_text = _fmt_float_counts(payload.get("interop_import_profile_diff_numeric_delta_totals"))
    delta_abs_totals_text = _fmt_float_counts(payload.get("interop_import_profile_diff_numeric_delta_abs_totals"))
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text}, delta_abs_totals={delta_abs_totals_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    delta_totals_text = _fmt_float_nested_counts(
        payload.get("interop_import_profile_diff_numeric_delta_totals_by_label_pair")
    )
    delta_abs_totals_text = _fmt_float_nested_counts(
        payload.get("interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair")
    )
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text}, delta_abs_totals={delta_abs_totals_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    delta_totals_text = _fmt_float_nested_counts(
        payload.get("interop_import_profile_diff_numeric_delta_totals_by_profile")
    )
    delta_abs_totals_text = _fmt_float_nested_counts(
        payload.get("interop_import_profile_diff_numeric_delta_abs_totals_by_profile")
    )
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text}, delta_abs_totals={delta_abs_totals_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile(
    payload: Any,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    delta_totals_text = _fmt_float_nested_counts(
        payload.get("interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile")
    )
    delta_abs_totals_text = _fmt_float_nested_counts(
        payload.get("interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile")
    )
    if delta_totals_text == "n/a" and delta_abs_totals_text == "n/a":
        return "n/a"
    return f"delta_totals={delta_totals_text}, delta_abs_totals={delta_abs_totals_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    positive_text = _fmt_counts(payload.get("interop_import_profile_diff_numeric_delta_positive_counts"))
    negative_text = _fmt_counts(payload.get("interop_import_profile_diff_numeric_delta_negative_counts"))
    zero_text = _fmt_counts(payload.get("interop_import_profile_diff_numeric_delta_zero_counts"))
    if positive_text == "n/a" and negative_text == "n/a" and zero_text == "n/a":
        return "n/a"
    return f"positive={positive_text}, negative={negative_text}, zero={zero_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes(
    payload: Any,
    *,
    max_items: int = 5,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"

    def _fmt_rows(rows: Any) -> str:
        if not isinstance(rows, list) or not rows:
            return "n/a"
        normalized: list[str] = []
        for row in rows[: max(1, int(max_items))]:
            if not isinstance(row, dict):
                continue
            left_label = str(row.get("left_label", "")).strip() or "left"
            right_label = str(row.get("right_label", "")).strip() or "right"
            profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
            numeric_key = str(row.get("numeric_key", "")).strip()
            if not numeric_key:
                continue
            delta_raw = row.get("delta")
            if delta_raw is None or isinstance(delta_raw, bool):
                continue
            try:
                delta_value = float(delta_raw)
            except (TypeError, ValueError):
                continue
            normalized.append(
                f"{numeric_key}:{left_label}_vs_{right_label}:{profile_id}:delta={delta_value:.6f}"
            )
        if not normalized:
            return "n/a"
        remaining = max(0, len(rows) - len(normalized))
        if remaining > 0:
            normalized.append(f"...(+{remaining} more)")
        return "; ".join(normalized)

    positive_text = _fmt_rows(payload.get("interop_import_profile_diff_numeric_delta_key_max_positive_records"))
    negative_text = _fmt_rows(payload.get("interop_import_profile_diff_numeric_delta_key_max_negative_records"))
    if positive_text == "n/a" and negative_text == "n/a":
        return "n/a"
    return f"positive={positive_text}, negative={negative_text}"


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots(
    payload: Any,
    *,
    max_items: int = 5,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    rows = payload.get("interop_import_profile_diff_numeric_delta_hotspots", [])
    if not isinstance(rows, list) or not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        numeric_key = str(row.get("numeric_key", "")).strip()
        if not numeric_key:
            continue
        delta_raw = row.get("delta")
        if delta_raw is None or isinstance(delta_raw, bool):
            continue
        try:
            delta_value = float(delta_raw)
        except (TypeError, ValueError):
            continue
        delta_abs_raw = row.get("delta_abs")
        if delta_abs_raw is None or isinstance(delta_abs_raw, bool):
            delta_abs_value = abs(delta_value)
        else:
            try:
                delta_abs_value = float(delta_abs_raw)
            except (TypeError, ValueError):
                delta_abs_value = abs(delta_value)
        normalized.append(
            f"{numeric_key}:{left_label}_vs_{right_label}:{profile_id}:"
            f"delta={delta_value:.6f}:abs={abs(delta_abs_value):.6f}"
        )
    if not normalized:
        return "n/a"
    total_records = payload.get("interop_import_profile_diff_numeric_delta_record_count", len(rows))
    try:
        total_count = max(0, int(total_records))
    except (TypeError, ValueError):
        total_count = len(rows)
    remaining = max(0, total_count - len(normalized))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized)


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile(
    payload: Any,
    *,
    max_items: int = 5,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    rows = payload.get("interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile", [])
    if not isinstance(rows, list) or not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        numeric_key_count_raw = row.get("numeric_key_count", 0)
        if numeric_key_count_raw is None or isinstance(numeric_key_count_raw, bool):
            numeric_key_count = 0
        else:
            try:
                numeric_key_count = max(0, int(numeric_key_count_raw))
            except (TypeError, ValueError):
                numeric_key_count = 0
        delta_total_raw = row.get("delta_total")
        if delta_total_raw is None or isinstance(delta_total_raw, bool):
            delta_total_value = 0.0
        else:
            try:
                delta_total_value = float(delta_total_raw)
            except (TypeError, ValueError):
                delta_total_value = 0.0
        delta_abs_total_raw = row.get("delta_abs_total")
        if delta_abs_total_raw is None or isinstance(delta_abs_total_raw, bool):
            delta_abs_total_value = abs(delta_total_value)
        else:
            try:
                delta_abs_total_value = abs(float(delta_abs_total_raw))
            except (TypeError, ValueError):
                delta_abs_total_value = abs(delta_total_value)
        top_numeric_key = str(row.get("top_numeric_key", "")).strip() or "n/a"
        top_numeric_delta_raw = row.get("top_numeric_delta")
        if top_numeric_delta_raw is None or isinstance(top_numeric_delta_raw, bool):
            top_numeric_delta_value = 0.0
        else:
            try:
                top_numeric_delta_value = float(top_numeric_delta_raw)
            except (TypeError, ValueError):
                top_numeric_delta_value = 0.0
        top_numeric_delta_abs_raw = row.get("top_numeric_delta_abs")
        if top_numeric_delta_abs_raw is None or isinstance(top_numeric_delta_abs_raw, bool):
            top_numeric_delta_abs_value = abs(top_numeric_delta_value)
        else:
            try:
                top_numeric_delta_abs_value = abs(float(top_numeric_delta_abs_raw))
            except (TypeError, ValueError):
                top_numeric_delta_abs_value = abs(top_numeric_delta_value)
        positive_delta_abs_total_raw = row.get("positive_delta_abs_total")
        if positive_delta_abs_total_raw is None or isinstance(positive_delta_abs_total_raw, bool):
            positive_delta_abs_total_value = 0.0
        else:
            try:
                positive_delta_abs_total_value = abs(float(positive_delta_abs_total_raw))
            except (TypeError, ValueError):
                positive_delta_abs_total_value = 0.0
        negative_delta_abs_total_raw = row.get("negative_delta_abs_total")
        if negative_delta_abs_total_raw is None or isinstance(negative_delta_abs_total_raw, bool):
            negative_delta_abs_total_value = 0.0
        else:
            try:
                negative_delta_abs_total_value = abs(float(negative_delta_abs_total_raw))
            except (TypeError, ValueError):
                negative_delta_abs_total_value = 0.0
        zero_numeric_key_count_raw = row.get("zero_numeric_key_count", 0)
        if zero_numeric_key_count_raw is None or isinstance(zero_numeric_key_count_raw, bool):
            zero_numeric_key_count = 0
        else:
            try:
                zero_numeric_key_count = max(0, int(zero_numeric_key_count_raw))
            except (TypeError, ValueError):
                zero_numeric_key_count = 0
        top_positive_numeric_key = str(row.get("top_positive_numeric_key", "")).strip() or "n/a"
        top_negative_numeric_key = str(row.get("top_negative_numeric_key", "")).strip() or "n/a"
        direction_imbalance_ratio_raw = row.get("direction_imbalance_ratio")
        if direction_imbalance_ratio_raw is None or isinstance(direction_imbalance_ratio_raw, bool):
            direction_imbalance_ratio_value = 0.0
        else:
            try:
                direction_imbalance_ratio_value = max(0.0, min(1.0, float(direction_imbalance_ratio_raw)))
            except (TypeError, ValueError):
                direction_imbalance_ratio_value = 0.0
        dominant_direction = str(row.get("dominant_direction", "")).strip().lower()
        if dominant_direction not in {"positive", "negative", "balanced"}:
            dominant_direction = "n/a"
        priority_score_raw = row.get("priority_score")
        if priority_score_raw is None or isinstance(priority_score_raw, bool):
            priority_score_value = 0.0
        else:
            try:
                priority_score_value = float(priority_score_raw)
            except (TypeError, ValueError):
                priority_score_value = 0.0
        priority_bucket = str(row.get("priority_bucket", "")).strip().lower()
        if priority_bucket not in {"high", "medium", "low"}:
            priority_bucket = "n/a"
        recommended_action = str(row.get("recommended_action", "")).strip() or "n/a"
        recommended_reason = str(row.get("recommended_reason", "")).strip() or "n/a"
        normalized.append(
            f"{left_label}_vs_{right_label}:{profile_id}:"
            f"abs_total={delta_abs_total_value:.6f}:delta_total={delta_total_value:.6f}:"
            f"numeric_keys={numeric_key_count}:top_numeric={top_numeric_key}:"
            f"top_abs={top_numeric_delta_abs_value:.6f}:top_delta={top_numeric_delta_value:.6f}:"
            f"pos_abs_total={positive_delta_abs_total_value:.6f}:"
            f"neg_abs_total={negative_delta_abs_total_value:.6f}:"
            f"zero_keys={zero_numeric_key_count}:"
            f"top_pos={top_positive_numeric_key}:top_neg={top_negative_numeric_key}:"
            f"imbalance={direction_imbalance_ratio_value:.6f}:"
            f"direction={dominant_direction}:"
            f"priority_score={priority_score_value:.6f}:"
            f"priority_bucket={priority_bucket}:"
            f"action={recommended_action}:"
            f"reason={recommended_reason}"
        )
    if not normalized:
        return "n/a"
    total_records = payload.get(
        "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count",
        len(rows),
    )
    try:
        total_count = max(0, int(total_records))
    except (TypeError, ValueError):
        total_count = len(rows)
    remaining = max(0, total_count - len(normalized))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized)


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations(
    payload: Any,
    *,
    max_items: int = 5,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    rows = payload.get("interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile", [])
    if not isinstance(rows, list) or not rows:
        return "n/a"
    normalized: list[str] = []
    for row in rows[: max(1, int(max_items))]:
        if not isinstance(row, dict):
            continue
        left_label = str(row.get("left_label", "")).strip() or "left"
        right_label = str(row.get("right_label", "")).strip() or "right"
        profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
        recommended_action = str(row.get("recommended_action", "")).strip() or "n/a"
        recommended_reason = str(row.get("recommended_reason", "")).strip() or "n/a"
        checklist_raw = row.get("recommended_checklist", [])
        checklist_items = (
            [str(item).strip() for item in checklist_raw if str(item).strip()]
            if isinstance(checklist_raw, list)
            else []
        )
        checklist_text = "|".join(checklist_items) if checklist_items else "n/a"
        normalized.append(
            f"{left_label}_vs_{right_label}:{profile_id}:"
            f"action={recommended_action}:"
            f"reason={recommended_reason}:"
            f"checklist={checklist_text}"
        )
    if not normalized:
        return "n/a"
    total_records = payload.get(
        "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count",
        len(rows),
    )
    try:
        total_count = max(0, int(total_records))
    except (TypeError, ValueError):
        total_count = len(rows)
    remaining = max(0, total_count - len(normalized))
    if remaining > 0:
        normalized.append(f"...(+{remaining} more)")
    return "; ".join(normalized)


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts(
    payload: Any,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    return _fmt_counts(payload.get("interop_import_profile_diff_numeric_delta_hotspot_priority_counts", {}))


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts(
    payload: Any,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    return _fmt_counts(payload.get("interop_import_profile_diff_numeric_delta_hotspot_action_counts", {}))


def _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts(
    payload: Any,
) -> str:
    if not isinstance(payload, dict):
        return "n/a"
    return _fmt_counts(payload.get("interop_import_profile_diff_numeric_delta_hotspot_reason_counts", {}))


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _fmt_prefixed_threshold_drift_summary(payload: Any, *, prefix: str, max_items: int) -> str:
    if not isinstance(payload, dict):
        return "detected=0, severity=NONE, hold_detected=0, summary=none, reasons=n/a"
    summary_text = str(payload.get(f"{prefix}_threshold_drift_summary_text", "")).strip() or "none"
    reasons_text = _fmt_list(payload.get(f"{prefix}_threshold_drift_reasons"), max_items)
    severity = str(payload.get(f"{prefix}_threshold_drift_severity", "")).strip().upper()
    if not severity:
        detected_fallback = bool(payload.get(f"{prefix}_threshold_drift_detected", False))
        severity = "WARN" if detected_fallback else "NONE"
    detected_flag = _coerce_optional_bool(payload.get(f"{prefix}_threshold_drift_detected"))
    if detected_flag is None:
        detected_flag = severity != "NONE"
    hold_detected = _coerce_optional_bool(payload.get(f"{prefix}_threshold_drift_hold_detected"))
    if hold_detected is None:
        hold_detected = severity == "HOLD"
    return (
        f"detected={1 if detected_flag else 0}, "
        f"severity={severity}, hold_detected={1 if hold_detected else 0}, "
        f"summary={summary_text}, reasons={reasons_text}"
    )


def _fmt_runtime_threshold_drift_summary(payload: Any, max_items: int) -> str:
    if not isinstance(payload, dict):
        return "detected=0, severity=NONE, hold_detected=0, summary=none, reasons=n/a"
    summary_text = str(payload.get("runtime_threshold_drift_summary_text", "")).strip() or "none"
    reasons_text = _fmt_list(payload.get("runtime_threshold_drift_reasons"), max_items)
    severity = str(payload.get("runtime_threshold_drift_severity", "")).strip().upper()
    if not severity:
        detected_fallback = bool(payload.get("runtime_threshold_drift_detected", False))
        severity = "WARN" if detected_fallback else "NONE"
    drift_detected = _coerce_optional_bool(payload.get("runtime_threshold_drift_detected"))
    if drift_detected is None:
        drift_detected = severity != "NONE"
    hold_detected = _coerce_optional_bool(payload.get("runtime_threshold_drift_hold_detected"))
    if hold_detected is None:
        hold_detected = severity == "HOLD"
    return (
        f"detected={1 if drift_detected else 0}, "
        f"severity={severity}, hold_detected={1 if hold_detected else 0}, "
        f"summary={summary_text}, reasons={reasons_text}"
    )


def _fmt_threshold_drift_hold_policy_failure_summary(payload: Any, max_items: int) -> str:
    if not isinstance(payload, dict):
        return (
            "detected=0, count=0, summary=none, failures=n/a, scope_counts=n/a, "
            "scope_reason_key_counts=n/a, reason_keys=n/a, reason_key_counts=n/a"
        )
    failures_raw = payload.get("threshold_drift_hold_policy_failures")
    failures: list[str] = []
    if isinstance(failures_raw, list):
        failures = [str(item).strip() for item in failures_raw if str(item).strip()]
    try:
        failure_count = int(payload.get("threshold_drift_hold_policy_failure_count", len(failures)))
    except (TypeError, ValueError):
        failure_count = len(failures)
    if failure_count < 0:
        failure_count = 0
    detected = (
        bool(payload.get("threshold_drift_hold_policy_failure_detected", False))
        or failure_count > 0
        or bool(failures)
    )
    summary_text = str(payload.get("threshold_drift_hold_policy_failure_summary_text", "")).strip()
    if not summary_text:
        summary_text = "; ".join(failures) if failures else "none"
    failures_text = _fmt_list(failures, max_items) if failures else "n/a"
    scope_counts_raw = payload.get("threshold_drift_hold_policy_failure_scope_counts")
    scope_counts: dict[str, int] = {}
    if isinstance(scope_counts_raw, dict):
        for raw_key, raw_value in scope_counts_raw.items():
            key = str(raw_key).strip()
            if not key:
                continue
            try:
                count = int(raw_value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            scope_counts[key] = count
    if not scope_counts:
        marker = " threshold drift hold policy failed"
        for failure in failures:
            text = str(failure).strip()
            if not text:
                continue
            marker_idx = text.lower().find(marker)
            if marker_idx <= 0:
                scope_key = "unknown"
            else:
                scope_text = text[:marker_idx].strip().lower()
                scope_key = scope_text.replace(" ", "_") if scope_text else "unknown"
            scope_counts[scope_key] = scope_counts.get(scope_key, 0) + 1
    scope_counts_text = _fmt_counts(scope_counts) if scope_counts else "n/a"

    scope_reason_key_counts_raw = payload.get("threshold_drift_hold_policy_failure_scope_reason_key_counts")
    scope_reason_key_counts: dict[str, dict[str, int]] = {}
    if isinstance(scope_reason_key_counts_raw, dict):
        for raw_scope_key, raw_reason_counts in scope_reason_key_counts_raw.items():
            scope_key = str(raw_scope_key).strip()
            if not scope_key:
                continue
            if not isinstance(raw_reason_counts, dict):
                continue
            normalized_reason_counts: dict[str, int] = {}
            for raw_reason_key, raw_reason_count in raw_reason_counts.items():
                reason_key = str(raw_reason_key).strip()
                if not reason_key:
                    continue
                try:
                    reason_count = int(raw_reason_count)
                except (TypeError, ValueError):
                    continue
                if reason_count <= 0:
                    continue
                normalized_reason_counts[reason_key] = reason_count
            if normalized_reason_counts:
                scope_reason_key_counts[scope_key] = normalized_reason_counts
    if not scope_reason_key_counts:
        marker = " threshold drift hold policy failed"
        for failure in failures:
            text = str(failure).strip()
            if not text:
                continue
            marker_idx = text.lower().find(marker)
            if marker_idx <= 0:
                scope_key = "unknown"
            else:
                scope_text = text[:marker_idx].strip().lower()
                scope_key = scope_text.replace(" ", "_") if scope_text else "unknown"
            if "reason_keys=" not in text:
                continue
            tail = text.split("reason_keys=", 1)[1].strip()
            if not tail or tail.lower() == "n/a":
                continue
            scope_reason_counts = scope_reason_key_counts.setdefault(scope_key, {})
            for reason_key in (part.strip() for part in tail.split(",") if part.strip()):
                scope_reason_counts[reason_key] = scope_reason_counts.get(reason_key, 0) + 1
    scope_reason_key_count_parts: list[str] = []
    for scope_key in sorted(scope_reason_key_counts.keys()):
        scope_reason_counts = scope_reason_key_counts.get(scope_key, {})
        if not isinstance(scope_reason_counts, dict):
            continue
        for reason_key in sorted(scope_reason_counts.keys()):
            reason_count = int(scope_reason_counts.get(reason_key, 0) or 0)
            if reason_count <= 0:
                continue
            scope_reason_key_count_parts.append(f"{scope_key}|{reason_key}:{reason_count}")
    scope_reason_key_counts_text = ", ".join(scope_reason_key_count_parts) if scope_reason_key_count_parts else "n/a"

    reason_keys_raw = payload.get("threshold_drift_hold_policy_failure_reason_keys")
    reason_keys_observed: list[str] = []
    if isinstance(reason_keys_raw, list):
        reason_keys_observed = [str(item).strip() for item in reason_keys_raw if str(item).strip()]
    else:
        reason_keys_text_raw = str(reason_keys_raw or "").strip()
        if reason_keys_text_raw and reason_keys_text_raw.lower() != "n/a":
            reason_keys_observed = [part.strip() for part in reason_keys_text_raw.split(",") if part.strip()]
    if not reason_keys_observed:
        for failure in failures:
            if "reason_keys=" not in failure:
                continue
            tail = failure.split("reason_keys=", 1)[1].strip()
            if not tail or tail.lower() == "n/a":
                continue
            reason_keys_observed.extend(part.strip() for part in tail.split(",") if part.strip())
    reason_key_counts_raw = payload.get("threshold_drift_hold_policy_failure_reason_key_counts")
    reason_key_counts: dict[str, int] = {}
    if isinstance(reason_key_counts_raw, dict):
        for raw_key, raw_value in reason_key_counts_raw.items():
            key = str(raw_key).strip()
            if not key:
                continue
            try:
                count = int(raw_value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            reason_key_counts[key] = count
    if not reason_key_counts and reason_keys_observed:
        for key in reason_keys_observed:
            reason_key_counts[key] = reason_key_counts.get(key, 0) + 1
    reason_keys: list[str] = list(dict.fromkeys(reason_keys_observed))
    if not reason_keys and reason_key_counts:
        reason_keys = list(reason_key_counts.keys())
    reason_keys_text = _fmt_list(reason_keys, max_items) if reason_keys else "n/a"
    reason_key_counts_text = _fmt_counts(reason_key_counts) if reason_key_counts else "n/a"
    return (
        f"detected={1 if detected else 0}, count={failure_count}, "
        f"summary={summary_text}, failures={failures_text}, scope_counts={scope_counts_text}, "
        f"scope_reason_key_counts={scope_reason_key_counts_text}, "
        f"reason_keys={reason_keys_text}, "
        f"reason_key_counts={reason_key_counts_text}"
    )


def _normalize_timing_map(payload: Any) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, raw in payload.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        normalized[name] = value
    return normalized


def _fmt_slowest_stages(timing_ms: dict[str, int], limit: int = 3) -> str:
    if not timing_ms:
        return "n/a"
    stage_pairs = [(name, value) for name, value in timing_ms.items() if name != "total"]
    if not stage_pairs:
        return "n/a"
    stage_pairs.sort(key=lambda item: (-item[1], item[0]))
    selected = stage_pairs[:max(1, limit)]
    return ", ".join(f"{name}:{value}" for name, value in selected)


def main() -> int:
    args = parse_args()
    max_codes = parse_positive_int(str(args.max_codes), default=20, field="max-codes")
    summary_path = Path(args.summary_json).resolve()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("summary json must be an object")

    release_prefix = str(payload.get("release_prefix", "")).strip()
    summary_count = payload.get("summary_count", "n/a")
    sds_versions = payload.get("sds_versions", [])
    final_counts = _fmt_counts(payload.get("final_result_counts"))
    overall_counts = _fmt_counts(payload.get("pipeline_overall_counts"))
    trend_counts = _fmt_counts(payload.get("pipeline_trend_counts"))
    timing_map = _normalize_timing_map(payload.get("timing_ms"))
    timing_counts = _fmt_counts(timing_map)
    timing_total_ms = timing_map.get("total", "n/a")
    slowest_stages = _fmt_slowest_stages(timing_map, limit=3)
    manifest_count = payload.get("pipeline_manifest_count", "n/a")

    reason_diff = payload.get("reason_code_diff", {})
    diff_available = isinstance(reason_diff, dict) and bool(reason_diff)
    version_a = str(reason_diff.get("version_a", "")).strip() if diff_available else ""
    version_b = str(reason_diff.get("version_b", "")).strip() if diff_available else ""
    found_a = reason_diff.get("found_version_a", False) if diff_available else False
    found_b = reason_diff.get("found_version_b", False) if diff_available else False
    only_in_a = _fmt_list(reason_diff.get("codes_only_in_a"), max_codes) if diff_available else "n/a"
    only_in_b = _fmt_list(reason_diff.get("codes_only_in_b"), max_codes) if diff_available else "n/a"
    common = _fmt_list(reason_diff.get("codes_common"), max_codes) if diff_available else "n/a"
    root_cause = payload.get("root_cause_summary", {})
    hold_code_top = _fmt_ranked_rows(
        root_cause.get("hold_reason_codes") if isinstance(root_cause, dict) else [],
        max_codes,
    )
    hold_raw_top = _fmt_ranked_rows(
        root_cause.get("hold_reasons_raw") if isinstance(root_cause, dict) else [],
        max_codes,
    )
    gate_reason_top = _fmt_ranked_rows(
        root_cause.get("gate_reasons") if isinstance(root_cause, dict) else [],
        max_codes,
    )
    requirement_hold_top = _fmt_ranked_rows(
        root_cause.get("requirement_hold_ids") if isinstance(root_cause, dict) else [],
        max_codes,
    )
    manifest_overview = _fmt_pipeline_manifest_overview(payload.get("pipeline_manifests"), max_codes)
    phase4_primary_summary = _fmt_phase4_primary_coverage_summary(payload.get("phase4_primary_coverage_summary"))
    phase4_primary_module_summary = _fmt_phase4_primary_module_coverage_summary(
        (
            payload.get("phase4_primary_coverage_summary", {}).get("module_coverage_summary", {})
            if isinstance(payload.get("phase4_primary_coverage_summary"), dict)
            else {}
        ),
        max_codes,
    )
    phase4_secondary_summary = _fmt_phase4_secondary_coverage_summary(
        payload.get("phase4_secondary_coverage_summary")
    )
    phase4_secondary_module_summary = _fmt_phase4_secondary_module_coverage_summary(
        (
            payload.get("phase4_secondary_coverage_summary", {}).get("module_coverage_summary", {})
            if isinstance(payload.get("phase4_secondary_coverage_summary"), dict)
            else {}
        ),
        max_codes,
    )
    phase3_vehicle_dynamics_summary = _fmt_phase3_vehicle_dynamics_summary(
        payload.get("phase3_vehicle_dynamics_summary")
    )
    phase3_core_sim_summary = _fmt_phase3_core_sim_summary(payload.get("phase3_core_sim_summary"))
    phase3_core_sim_matrix_summary = _fmt_phase3_core_sim_matrix_summary(
        payload.get("phase3_core_sim_matrix_summary")
    )
    phase3_lane_risk_summary = _fmt_phase3_lane_risk_summary(payload.get("phase3_lane_risk_summary"))
    phase3_dataset_traffic_summary = _fmt_phase3_dataset_traffic_summary(
        payload.get("phase3_dataset_traffic_summary")
    )
    phase2_log_replay_summary = _fmt_phase2_log_replay_summary(payload.get("phase2_log_replay_summary"))
    phase2_map_routing_summary = _fmt_phase2_map_routing_summary(payload.get("phase2_map_routing_summary"))
    phase2_sensor_fidelity_summary = _fmt_phase2_sensor_fidelity_summary(
        payload.get("phase2_sensor_fidelity_summary")
    )
    runtime_native_smoke_summary = _fmt_runtime_native_smoke_summary(payload.get("runtime_native_smoke_summary"))
    runtime_native_summary_compare_summary = _fmt_runtime_native_summary_compare_summary(
        payload.get("runtime_native_summary_compare_summary")
    )
    runtime_native_evidence_compare_summary = _fmt_runtime_evidence_compare_summary(
        payload.get("runtime_native_evidence_compare_summary")
    )
    runtime_native_evidence_compare_interop_import_mode_diff_counts = (
        _fmt_runtime_evidence_compare_interop_import_mode_diff_counts(
            payload.get("runtime_native_evidence_compare_summary")
        )
    )
    runtime_native_evidence_compare_warning = (
        str(payload.get("runtime_native_evidence_compare_warning", "")).strip() or "n/a"
    )
    runtime_native_evidence_compare_warning_reasons = _fmt_list(
        payload.get("runtime_native_evidence_compare_warning_reasons"),
        max_codes,
    )
    runtime_evidence_summary = _fmt_runtime_evidence_summary(payload.get("runtime_evidence_summary"))
    runtime_evidence_probe_args_summary = _fmt_runtime_evidence_probe_args_summary(
        payload.get("runtime_evidence_summary")
    )
    runtime_evidence_scenario_contract_summary = _fmt_runtime_evidence_scenario_contract_summary(
        payload.get("runtime_evidence_summary")
    )
    runtime_evidence_scene_result_summary = _fmt_runtime_evidence_scene_result_summary(
        payload.get("runtime_evidence_summary")
    )
    runtime_evidence_interop_contract_summary = _fmt_runtime_evidence_interop_contract_summary(
        payload.get("runtime_evidence_summary")
    )
    runtime_evidence_interop_export_summary = _fmt_runtime_evidence_interop_export_summary(
        payload.get("runtime_evidence_summary")
    )
    runtime_evidence_interop_import_summary = _fmt_runtime_evidence_interop_import_summary(
        payload.get("runtime_evidence_summary")
    )
    runtime_evidence_interop_import_modes = _fmt_runtime_evidence_interop_import_modes(
        payload.get("runtime_evidence_summary")
    )
    runtime_evidence_interop_import_inconsistent_records = _fmt_runtime_evidence_interop_import_inconsistent_records(
        payload.get("runtime_evidence_summary")
    )
    runtime_lane_execution_summary = _fmt_runtime_lane_execution_summary(
        payload.get("runtime_lane_execution_summary")
    )
    runtime_evidence_compare_summary = _fmt_runtime_evidence_compare_summary(
        payload.get("runtime_evidence_compare_summary")
    )
    runtime_evidence_compare_interop_import_mode_diff_counts = (
        _fmt_runtime_evidence_compare_interop_import_mode_diff_counts(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diffs = _fmt_runtime_evidence_compare_interop_import_profile_diffs(
        payload.get("runtime_evidence_compare_summary")
    )
    runtime_evidence_compare_interop_import_profile_diff_counts = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_counts(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_breakdown = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_breakdown(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts = (
        _fmt_runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts(
            payload.get("runtime_evidence_compare_summary")
        )
    )
    runtime_evidence_compare_warning = (
        str(payload.get("runtime_evidence_compare_warning", "")).strip() or "n/a"
    )
    runtime_evidence_compare_warning_reasons = _fmt_list(
        payload.get("runtime_evidence_compare_warning_reasons"),
        max_codes,
    )
    phase3_core_sim_threshold_drift_summary = _fmt_prefixed_threshold_drift_summary(
        payload,
        prefix="phase3_core_sim",
        max_items=max_codes,
    )
    phase3_lane_risk_threshold_drift_summary = _fmt_prefixed_threshold_drift_summary(
        payload,
        prefix="phase3_lane_risk",
        max_items=max_codes,
    )
    phase3_dataset_traffic_threshold_drift_summary = _fmt_prefixed_threshold_drift_summary(
        payload,
        prefix="phase3_dataset_traffic",
        max_items=max_codes,
    )
    runtime_threshold_drift_summary = _fmt_runtime_threshold_drift_summary(payload, max_codes)
    threshold_drift_hold_policy_failure_summary = _fmt_threshold_drift_hold_policy_failure_summary(
        payload,
        max_codes,
    )

    lines: list[str] = []
    lines.append(f"## {args.title}")
    lines.append("")
    lines.append(f"- release_prefix: `{release_prefix}`")
    lines.append(f"- summary_count: `{summary_count}`")
    lines.append(f"- timing_total_ms: `{timing_total_ms}`")
    lines.append(f"- sds_versions: `{_fmt_list(sds_versions, 1000)}`")
    lines.append(f"- final_result_counts: `{final_counts}`")
    lines.append(f"- pipeline_manifest_count: `{manifest_count}`")
    lines.append(f"- pipeline_overall_counts: `{overall_counts}`")
    lines.append(f"- pipeline_trend_counts: `{trend_counts}`")
    lines.append("")
    lines.append("### Performance")
    lines.append("")
    lines.append(f"- timing_total_ms: `{timing_total_ms}`")
    lines.append(f"- slowest_stages_ms: `{slowest_stages}`")
    lines.append(f"- timing_ms: `{timing_counts}`")
    lines.append("")
    lines.append("### Root Cause Summary")
    lines.append("")
    lines.append(f"- hold_reason_codes_top: `{hold_code_top}`")
    lines.append(f"- hold_reasons_raw_top: `{hold_raw_top}`")
    lines.append(f"- gate_reasons_top: `{gate_reason_top}`")
    lines.append(f"- requirement_hold_ids_top: `{requirement_hold_top}`")
    lines.append("")
    lines.append("### Pipeline Manifest Overview")
    lines.append("")
    lines.append(f"- pipeline_manifests: `{manifest_overview}`")
    lines.append(f"- phase2_log_replay: `{phase2_log_replay_summary}`")
    lines.append(f"- phase2_map_routing: `{phase2_map_routing_summary}`")
    lines.append(f"- phase2_sensor_fidelity: `{phase2_sensor_fidelity_summary}`")
    lines.append(f"- phase3_vehicle_dynamics: `{phase3_vehicle_dynamics_summary}`")
    lines.append(f"- phase3_core_sim: `{phase3_core_sim_summary}`")
    lines.append(f"- phase3_core_sim_matrix: `{phase3_core_sim_matrix_summary}`")
    lines.append(f"- phase3_lane_risk: `{phase3_lane_risk_summary}`")
    lines.append(f"- phase3_dataset_traffic: `{phase3_dataset_traffic_summary}`")
    lines.append(f"- phase3_core_sim_threshold_drift: `{phase3_core_sim_threshold_drift_summary}`")
    lines.append(f"- phase3_lane_risk_threshold_drift: `{phase3_lane_risk_threshold_drift_summary}`")
    lines.append(f"- phase3_dataset_traffic_threshold_drift: `{phase3_dataset_traffic_threshold_drift_summary}`")
    lines.append(f"- phase4_primary_coverage: `{phase4_primary_summary}`")
    lines.append(f"- phase4_primary_module_coverage: `{phase4_primary_module_summary}`")
    lines.append(f"- phase4_secondary_coverage: `{phase4_secondary_summary}`")
    lines.append(f"- phase4_secondary_module_coverage: `{phase4_secondary_module_summary}`")
    lines.append(f"- runtime_native_smoke: `{runtime_native_smoke_summary}`")
    lines.append(f"- runtime_native_summary_compare: `{runtime_native_summary_compare_summary}`")
    lines.append(f"- runtime_native_evidence_compare: `{runtime_native_evidence_compare_summary}`")
    if runtime_native_evidence_compare_interop_import_mode_diff_counts != "n/a":
        lines.append(
            "- runtime_native_evidence_compare_interop_import_mode_diff_counts: "
            f"`{runtime_native_evidence_compare_interop_import_mode_diff_counts}`"
        )
    if runtime_native_evidence_compare_warning != "n/a":
        lines.append(f"- runtime_native_evidence_compare_warning: `{runtime_native_evidence_compare_warning}`")
        lines.append(
            "- runtime_native_evidence_compare_warning_reasons: "
            f"`{runtime_native_evidence_compare_warning_reasons}`"
        )
    lines.append(f"- runtime_evidence: `{runtime_evidence_summary}`")
    lines.append(f"- runtime_evidence_probe_args: `{runtime_evidence_probe_args_summary}`")
    lines.append(f"- runtime_evidence_scenario_contract: `{runtime_evidence_scenario_contract_summary}`")
    lines.append(f"- runtime_evidence_scene_result: `{runtime_evidence_scene_result_summary}`")
    lines.append(f"- runtime_evidence_interop_contract: `{runtime_evidence_interop_contract_summary}`")
    lines.append(f"- runtime_evidence_interop_export: `{runtime_evidence_interop_export_summary}`")
    lines.append(f"- runtime_evidence_interop_import: `{runtime_evidence_interop_import_summary}`")
    if runtime_evidence_interop_import_modes != "n/a":
        lines.append(f"- runtime_evidence_interop_import_modes: `{runtime_evidence_interop_import_modes}`")
    if runtime_evidence_interop_import_inconsistent_records != "n/a":
        lines.append(
            "- runtime_evidence_interop_import_inconsistent_records: "
            f"`{runtime_evidence_interop_import_inconsistent_records}`"
        )
    lines.append(f"- runtime_lane_execution: `{runtime_lane_execution_summary}`")
    lines.append(f"- runtime_evidence_compare: `{runtime_evidence_compare_summary}`")
    if runtime_evidence_compare_interop_import_mode_diff_counts != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_mode_diff_counts: "
            f"`{runtime_evidence_compare_interop_import_mode_diff_counts}`"
        )
    if runtime_evidence_compare_interop_import_profile_diffs != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diffs: "
            f"`{runtime_evidence_compare_interop_import_profile_diffs}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_counts != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_counts: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_counts}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_breakdown != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_breakdown: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_breakdown}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_deltas: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_profile}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_deltas_by_label_pair_profile}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts}`"
        )
    if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts != "n/a":
        lines.append(
            "- runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts: "
            f"`{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts}`"
        )
    if runtime_evidence_compare_warning != "n/a":
        lines.append(f"- runtime_evidence_compare_warning: `{runtime_evidence_compare_warning}`")
        lines.append(
            "- runtime_evidence_compare_warning_reasons: "
            f"`{runtime_evidence_compare_warning_reasons}`"
        )
    lines.append(f"- runtime_threshold_drift: `{runtime_threshold_drift_summary}`")
    lines.append(f"- threshold_drift_hold_policy_failures: `{threshold_drift_hold_policy_failure_summary}`")
    lines.append("")
    lines.append("### Hold Reason Code Diff")
    lines.append("")
    if diff_available:
        lines.append(f"- version_a: `{version_a}` (found={found_a})")
        lines.append(f"- version_b: `{version_b}` (found={found_b})")
        lines.append(f"- codes_only_in_a: `{only_in_a}`")
        lines.append(f"- codes_only_in_b: `{only_in_b}`")
        lines.append(f"- codes_common: `{common}`")
    else:
        lines.append("- n/a")

    print("\n".join(lines))
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="render_release_summary_markdown.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=SUMMARY_PHASE_PUBLISH_SUMMARY,
        )
    )
