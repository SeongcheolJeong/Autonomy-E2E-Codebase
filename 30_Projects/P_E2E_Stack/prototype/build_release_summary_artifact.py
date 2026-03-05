#!/usr/bin/env python3
"""Build a release summary artifact from downloaded report summary files."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from ci_input_parsing import parse_positive_int
from ci_phases import SUMMARY_PHASE_BUILD_SUMMARY
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_subprocess import run_logged_capture_stdout_or_raise
from ci_sync_utils import resolve_repo_root

TIMING_KEYS = (
    "scan_summary_files",
    "load_summary_payloads",
    "scan_pipeline_manifests",
    "scan_runtime_evidence",
    "ingest",
    "query_release_latest",
    "query_hold_reason_codes",
    "query_hold_reasons_raw",
    "query_release_diff",
    "total",
)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build release summary artifact from report summaries")
    parser.add_argument("--artifacts-root", required=True, help="Root directory to scan for *.summary.json")
    parser.add_argument(
        "--summary-files-root",
        default="",
        help="Optional root directory to scan for *_*.summary.json (defaults to artifacts-root)",
    )
    parser.add_argument(
        "--summary-files-subpath",
        default="",
        help=(
            "Optional relative subpath scanned under <summary-files-root> and its direct children "
            "(useful for downloaded_artifacts/<artifact-name>/...)"
        ),
    )
    parser.add_argument(
        "--pipeline-manifests-root",
        default="",
        help="Optional root directory to scan for pipeline_result.json (defaults to artifacts-root)",
    )
    parser.add_argument(
        "--pipeline-manifests-subpath",
        default="",
        help=(
            "Optional relative subpath scanned under <pipeline-manifests-root> and its direct children "
            "(useful for downloaded_artifacts/<artifact-name>/...)"
        ),
    )
    parser.add_argument("--release-prefix", required=True, help="Release prefix without SDS suffix")
    parser.add_argument("--out-text", required=True, help="Output text report path")
    parser.add_argument("--out-json", default="", help="Optional output JSON report path")
    parser.add_argument("--out-db", required=True, help="Output SQLite path for temporary release summary DB")
    parser.add_argument("--version-a", default="", help="SDS version A for release-diff")
    parser.add_argument("--version-b", default="", help="SDS version B for release-diff")
    parser.add_argument("--latest-limit", default="", help="Row limit for release-latest output (>0)")
    parser.add_argument("--hold-reason-limit", default="", help="Row limit for hold reason aggregation (>0)")
    parser.add_argument("--python-bin", default="python3", help="Python executable")
    return parser.parse_args()


def run_cmd(cmd: list[str]) -> str:
    return run_logged_capture_stdout_or_raise(cmd, context="command")


def run_cmd_quiet(cmd: list[str]) -> str:
    return run_logged_capture_stdout_or_raise(
        cmd,
        context="command",
        emit_output_on_success=False,
    )


def _is_release_diff_no_assessment_output(text: str) -> bool:
    normalized = str(text).strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("[error]")
        and "no release assessment found for prefix=" in normalized
    )


def _empty_timing_ms() -> dict[str, int]:
    return {key: 0 for key in TIMING_KEYS}


def _elapsed_ms(started_at: float) -> int:
    return max(0, int(round((time.perf_counter() - started_at) * 1000)))


def _fmt_timing_ms(timing_ms: dict[str, int]) -> str:
    return ",".join(f"{key}:{int(timing_ms.get(key, 0))}" for key in TIMING_KEYS)


def _matches_release_prefix(*, release_id: str, prefix: str) -> bool:
    normalized_release_id = str(release_id).strip()
    normalized_prefix = str(prefix).strip()
    if not normalized_release_id or not normalized_prefix:
        return False
    if normalized_release_id == normalized_prefix:
        return True
    return normalized_release_id.startswith(f"{normalized_prefix}_")


def _to_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_non_negative_int_or_none(value: Any) -> int | None:
    parsed = _to_int(value, default=-1)
    if parsed < 0:
        return None
    return parsed


def _to_non_negative_float_or_none(value: Any) -> float | None:
    parsed = _to_float_or_none(value)
    if parsed is None or parsed < 0.0:
        return None
    return parsed


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_smoke_status(value: Any) -> str:
    text = str(value).strip().lower()
    if not text or text in {"n/a", "na", "none"}:
        return "n/a"
    if text in {"pass", "passed", "success", "ok", "validated"}:
        return "pass"
    if text in {"fail", "failed", "error", "timeout"}:
        return "fail"
    if text in {"warn", "warning", "partial", "degraded", "unknown"}:
        return "partial"
    return "partial"


def resolve_scan_roots(base_root: Path, subpath: str) -> list[Path]:
    normalized_subpath = str(subpath).strip().strip("/")
    if not normalized_subpath:
        return [base_root]

    if not base_root.is_dir():
        return []

    relative = Path(normalized_subpath)
    scan_roots: list[Path] = []
    seen: set[Path] = set()

    def _append_if_dir(candidate: Path) -> None:
        if not candidate.is_dir():
            return
        resolved = candidate.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        scan_roots.append(resolved)

    _append_if_dir(base_root / relative)
    for child in sorted(base_root.iterdir()):
        if child.is_dir():
            _append_if_dir(child / relative)

    return scan_roots


def discover_summary_files(scan_roots: list[Path], release_prefix: str) -> list[Path]:
    pattern = f"{release_prefix}_*.summary.json"
    summary_paths: set[Path] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob(pattern):
            summary_paths.add(path.resolve())
    return sorted(summary_paths)


def load_summary_payloads(summary_files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in summary_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        items.append({"path": str(path), "payload": payload})
    return items


def discover_versions(summary_items: list[dict[str, Any]]) -> list[str]:
    versions: set[str] = set()
    for item in summary_items:
        payload = item["payload"]
        version = str(payload.get("sds_version", "")).strip()
        if version:
            versions.add(version)
    return sorted(versions)


def summarize_final_results(summary_items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in summary_items:
        payload = item["payload"]
        final_result = str(payload.get("final_result", "")).strip()
        key = final_result if final_result else "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _append_values(values: Any, counts: dict[str, int]) -> None:
    if not isinstance(values, list):
        return
    for raw_value in values:
        value = str(raw_value).strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1


def _normalize_text_list(values: Any) -> list[str]:
    normalized: list[str] = []
    if not isinstance(values, list):
        return normalized
    for raw_value in values:
        value = str(raw_value).strip()
        if value:
            normalized.append(value)
    return normalized


def _to_ranked_rows(counts: dict[str, int]) -> list[dict[str, Any]]:
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"value": key, "count": count} for key, count in ranked]


def summarize_root_causes(summary_items: list[dict[str, Any]]) -> dict[str, Any]:
    hold_reason_codes: dict[str, int] = {}
    hold_reasons_raw: dict[str, int] = {}
    gate_reasons: dict[str, int] = {}
    requirement_hold_ids: dict[str, int] = {}

    for item in summary_items:
        payload = item["payload"]
        _append_values(payload.get("hold_reason_codes"), hold_reason_codes)
        _append_values(payload.get("hold_reasons"), hold_reasons_raw)
        _append_values(payload.get("gate_reasons"), gate_reasons)

        requirement_records = payload.get("requirement_hold_records")
        if not isinstance(requirement_records, list):
            continue
        for record in requirement_records:
            if not isinstance(record, dict):
                continue
            status = str(record.get("status", "")).strip().upper()
            if status != "HOLD":
                continue
            requirement_id = str(record.get("requirement_id", "")).strip()
            if not requirement_id:
                continue
            requirement_hold_ids[requirement_id] = requirement_hold_ids.get(requirement_id, 0) + 1

    return {
        "hold_reason_codes": _to_ranked_rows(hold_reason_codes),
        "hold_reasons_raw": _to_ranked_rows(hold_reasons_raw),
        "gate_reasons": _to_ranked_rows(gate_reasons),
        "requirement_hold_ids": _to_ranked_rows(requirement_hold_ids),
    }


def append_ranked_table(lines: list[str], *, title: str, rows: list[dict[str, Any]]) -> None:
    lines.append(f"### {title}")
    if not rows:
        lines.append("[info] none")
        lines.append("")
        return
    lines.append("| value | count |")
    lines.append("| --- | --- |")
    for row in rows:
        lines.append(f"| {row['value']} | {row['count']} |")
    lines.append("")


def _latest_summary_by_version(summary_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for item in summary_items:
        payload = item["payload"]
        version = str(payload.get("sds_version", "")).strip()
        if not version:
            continue
        generated_at = str(payload.get("generated_at", "")).strip()
        current = selected.get(version)
        if current is None:
            selected[version] = item
            continue
        current_generated_at = str(current["payload"].get("generated_at", "")).strip()
        current_path = str(current.get("path", ""))
        this_path = str(item.get("path", ""))
        if generated_at > current_generated_at:
            selected[version] = item
        elif generated_at == current_generated_at and this_path > current_path:
            selected[version] = item
    return selected


def summarize_reason_code_diff(
    summary_items: list[dict[str, Any]], version_a: str, version_b: str
) -> dict[str, Any]:
    latest_by_version = _latest_summary_by_version(summary_items)
    item_a = latest_by_version.get(version_a)
    item_b = latest_by_version.get(version_b)

    codes_a: set[str] = set()
    codes_b: set[str] = set()

    if item_a is not None:
        payload_a = item_a["payload"]
        raw_a = payload_a.get("hold_reason_codes", [])
        if isinstance(raw_a, list):
            codes_a = {str(code).strip() for code in raw_a if str(code).strip()}

    if item_b is not None:
        payload_b = item_b["payload"]
        raw_b = payload_b.get("hold_reason_codes", [])
        if isinstance(raw_b, list):
            codes_b = {str(code).strip() for code in raw_b if str(code).strip()}

    return {
        "version_a": version_a,
        "version_b": version_b,
        "found_version_a": item_a is not None,
        "found_version_b": item_b is not None,
        "codes_a": sorted(codes_a),
        "codes_b": sorted(codes_b),
        "codes_only_in_a": sorted(codes_a - codes_b),
        "codes_only_in_b": sorted(codes_b - codes_a),
        "codes_common": sorted(codes_a & codes_b),
    }


def discover_pipeline_manifests(scan_roots: list[Path], release_prefix: str) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    seen_manifest_paths: set[Path] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("pipeline_result.json"):
            resolved = path.resolve()
            if resolved in seen_manifest_paths:
                continue
            seen_manifest_paths.add(resolved)
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            release_id = str(payload.get("release_id", "")).strip()
            if not _matches_release_prefix(release_id=release_id, prefix=release_prefix):
                continue

            trend_gate = payload.get("trend_gate")
            trend_result = ""
            if isinstance(trend_gate, dict):
                trend_result = str(trend_gate.get("result", "")).strip()

            sds_versions: list[str] = []
            reports = payload.get("reports")
            if isinstance(reports, list):
                for item in reports:
                    if not isinstance(item, dict):
                        continue
                    version = str(item.get("sds_version", "")).strip()
                    if version:
                        sds_versions.append(version)

            phase3_vehicle_dynamics_enabled = False
            phase3_vehicle_dynamics_model = ""
            phase3_vehicle_dynamics_step_count = 0
            phase3_vehicle_dynamics_initial_speed_mps = 0.0
            phase3_vehicle_dynamics_initial_position_m = 0.0
            phase3_vehicle_dynamics_initial_heading_deg = 0.0
            phase3_vehicle_dynamics_initial_lateral_position_m = 0.0
            phase3_vehicle_dynamics_initial_lateral_velocity_mps = 0.0
            phase3_vehicle_dynamics_initial_yaw_rate_rps = 0.0
            phase3_vehicle_dynamics_final_speed_mps = 0.0
            phase3_vehicle_dynamics_final_position_m = 0.0
            phase3_vehicle_dynamics_final_heading_deg = 0.0
            phase3_vehicle_dynamics_final_lateral_position_m = 0.0
            phase3_vehicle_dynamics_final_lateral_velocity_mps = 0.0
            phase3_vehicle_dynamics_final_yaw_rate_rps = 0.0
            phase3_vehicle_dynamics_min_heading_deg = 0.0
            phase3_vehicle_dynamics_avg_heading_deg = 0.0
            phase3_vehicle_dynamics_max_heading_deg = 0.0
            phase3_vehicle_dynamics_min_lateral_position_m = 0.0
            phase3_vehicle_dynamics_avg_lateral_position_m = 0.0
            phase3_vehicle_dynamics_max_lateral_position_m = 0.0
            phase3_vehicle_dynamics_max_abs_lateral_position_m = 0.0
            phase3_vehicle_dynamics_max_abs_yaw_rate_rps = 0.0
            phase3_vehicle_dynamics_max_abs_lateral_velocity_mps = 0.0
            phase3_vehicle_dynamics_max_abs_accel_mps2 = 0.0
            phase3_vehicle_dynamics_max_abs_lateral_accel_mps2 = 0.0
            phase3_vehicle_dynamics_max_abs_yaw_accel_rps2 = 0.0
            phase3_vehicle_dynamics_max_abs_jerk_mps3 = 0.0
            phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3 = 0.0
            phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3 = 0.0
            phase3_vehicle_dynamics_min_road_grade_percent = 0.0
            phase3_vehicle_dynamics_avg_road_grade_percent = 0.0
            phase3_vehicle_dynamics_max_road_grade_percent = 0.0
            phase3_vehicle_dynamics_max_abs_grade_force_n = 0.0
            phase3_vehicle_control_command_step_count = 0
            phase3_vehicle_control_throttle_brake_overlap_step_count = 0
            phase3_vehicle_control_throttle_brake_overlap_ratio = 0.0
            phase3_vehicle_control_max_abs_steering_rate_degps = 0.0
            phase3_vehicle_control_max_abs_throttle_rate_per_sec = 0.0
            phase3_vehicle_control_max_abs_brake_rate_per_sec = 0.0
            phase3_vehicle_control_max_throttle_plus_brake = 0.0
            phase3_vehicle_speed_tracking_target_step_count = 0
            phase3_vehicle_speed_tracking_error_mps_min = 0.0
            phase3_vehicle_speed_tracking_error_mps_avg = 0.0
            phase3_vehicle_speed_tracking_error_mps_max = 0.0
            phase3_vehicle_speed_tracking_error_abs_mps_avg = 0.0
            phase3_vehicle_speed_tracking_error_abs_mps_max = 0.0
            phase3_vehicle_dynamics_planar_kinematics_enabled = False
            phase3_vehicle_dynamics_dynamic_bicycle_enabled = False
            phase3_core_sim_enabled = False
            phase3_core_sim_status = "n/a"
            phase3_core_sim_termination_reason = ""
            phase3_core_sim_collision = False
            phase3_core_sim_timeout = False
            phase3_core_sim_min_ttc_same_lane_sec: float | None = None
            phase3_core_sim_min_ttc_adjacent_lane_sec: float | None = None
            phase3_core_sim_min_ttc_any_lane_sec: float | None = None
            phase3_core_sim_enable_ego_collision_avoidance = False
            phase3_core_sim_avoidance_ttc_threshold_sec = 0.0
            phase3_core_sim_ego_max_brake_mps2 = 0.0
            phase3_core_sim_tire_friction_coeff = 0.0
            phase3_core_sim_surface_friction_scale = 0.0
            phase3_core_sim_ego_avoidance_brake_event_count = 0
            phase3_core_sim_ego_avoidance_applied_brake_mps2_max = 0.0
            phase3_core_sim_gate_result = "n/a"
            phase3_core_sim_gate_reason_count = 0
            phase3_core_sim_gate_reasons: list[str] = []
            phase3_core_sim_gate_require_success = False
            phase3_core_sim_gate_min_ttc_same_lane_sec = 0.0
            phase3_core_sim_gate_min_ttc_any_lane_sec = 0.0
            phase3_core_sim_matrix_enabled = False
            phase3_core_sim_matrix_schema_version = ""
            phase3_core_sim_matrix_case_count = 0
            phase3_core_sim_matrix_success_case_count = 0
            phase3_core_sim_matrix_failed_case_count = 0
            phase3_core_sim_matrix_all_cases_success = False
            phase3_core_sim_matrix_collision_case_count = 0
            phase3_core_sim_matrix_timeout_case_count = 0
            phase3_core_sim_matrix_min_ttc_same_lane_sec_min: float | None = None
            phase3_core_sim_matrix_lowest_ttc_same_lane_run_id = ""
            phase3_core_sim_matrix_min_ttc_any_lane_sec_min: float | None = None
            phase3_core_sim_matrix_lowest_ttc_any_lane_run_id = ""
            phase3_core_sim_matrix_status_counts: dict[str, int] = {}
            phase3_core_sim_matrix_returncode_counts: dict[str, int] = {}
            phase3_object_sim_checked = False
            phase3_object_sim_status = "n/a"
            phase3_sim_runtime_scenario_contract_checked = False
            phase3_sim_runtime_scenario_contract_status = "n/a"
            phase3_sim_runtime_scenario_contract_runtime_ready: bool | None = None
            phase3_sim_runtime_scene_result_checked = False
            phase3_sim_runtime_scene_result_status = "n/a"
            phase3_sim_runtime_scene_result_runtime_ready: bool | None = None
            phase3_dataset_traffic_run_summary_count = 0
            phase3_dataset_traffic_run_status_counts: dict[str, int] = {}
            phase3_dataset_traffic_profile_count = 0
            phase3_dataset_traffic_profile_ids: list[str] = []
            phase3_dataset_traffic_profile_source_count = 0
            phase3_dataset_traffic_profile_source_ids: list[str] = []
            phase3_dataset_traffic_actor_pattern_count = 0
            phase3_dataset_traffic_actor_pattern_ids: list[str] = []
            phase3_dataset_traffic_lane_profile_signature_count = 0
            phase3_dataset_traffic_lane_profile_signatures: list[str] = []
            phase3_dataset_traffic_npc_count_sample_count = 0
            phase3_dataset_traffic_npc_count_min = 0
            phase3_dataset_traffic_npc_count_avg = 0.0
            phase3_dataset_traffic_npc_count_max = 0
            phase3_dataset_traffic_npc_initial_gap_m_sample_count = 0
            phase3_dataset_traffic_npc_initial_gap_m_min = 0.0
            phase3_dataset_traffic_npc_initial_gap_m_avg = 0.0
            phase3_dataset_traffic_npc_initial_gap_m_max = 0.0
            phase3_dataset_traffic_npc_gap_step_m_sample_count = 0
            phase3_dataset_traffic_npc_gap_step_m_min = 0.0
            phase3_dataset_traffic_npc_gap_step_m_avg = 0.0
            phase3_dataset_traffic_npc_gap_step_m_max = 0.0
            phase3_dataset_traffic_npc_speed_scale_sample_count = 0
            phase3_dataset_traffic_npc_speed_scale_min = 0.0
            phase3_dataset_traffic_npc_speed_scale_avg = 0.0
            phase3_dataset_traffic_npc_speed_scale_max = 0.0
            phase3_dataset_traffic_npc_speed_jitter_mps_sample_count = 0
            phase3_dataset_traffic_npc_speed_jitter_mps_min = 0.0
            phase3_dataset_traffic_npc_speed_jitter_mps_avg = 0.0
            phase3_dataset_traffic_npc_speed_jitter_mps_max = 0.0
            phase3_dataset_traffic_lane_index_unique_count = 0
            phase3_dataset_traffic_lane_indices: list[int] = []
            phase3_dataset_manifest_counts_rows = 0
            phase3_dataset_manifest_run_summary_count = 0
            phase3_dataset_manifest_release_summary_count = 0
            phase3_dataset_manifest_versions: list[str] = []
            phase3_dataset_traffic_gate_result = "n/a"
            phase3_dataset_traffic_gate_reason_count = 0
            phase3_dataset_traffic_gate_reasons: list[str] = []
            phase3_dataset_traffic_gate_min_run_summary_count = 0
            phase3_dataset_traffic_gate_min_traffic_profile_count = 0
            phase3_dataset_traffic_gate_min_actor_pattern_count = 0
            phase3_dataset_traffic_gate_min_avg_npc_count = 0.0
            phase3_lane_risk_gate_result = "n/a"
            phase3_lane_risk_gate_reason_count = 0
            phase3_lane_risk_gate_reasons: list[str] = []
            phase3_lane_risk_gate_min_ttc_same_lane_sec = 0.0
            phase3_lane_risk_gate_min_ttc_adjacent_lane_sec = 0.0
            phase3_lane_risk_gate_min_ttc_any_lane_sec = 0.0
            phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total = 0
            phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total = 0
            phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total = 0
            phase3_hooks = payload.get("phase3_hooks")
            if isinstance(phase3_hooks, dict):
                phase3_vehicle_dynamics_enabled = bool(phase3_hooks.get("enabled", False))
                vehicle_dynamics = phase3_hooks.get("vehicle_dynamics")
                if isinstance(vehicle_dynamics, dict):
                    phase3_vehicle_dynamics_model = str(
                        vehicle_dynamics.get("vehicle_dynamics_model", "")
                    ).strip()
                    try:
                        phase3_vehicle_dynamics_step_count = int(
                            vehicle_dynamics.get("step_count", 0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_step_count = 0
                    try:
                        phase3_vehicle_dynamics_initial_speed_mps = float(
                            vehicle_dynamics.get("initial_speed_mps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_initial_speed_mps = 0.0
                    try:
                        phase3_vehicle_dynamics_initial_position_m = float(
                            vehicle_dynamics.get("initial_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_initial_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_initial_heading_deg = float(
                            vehicle_dynamics.get("initial_heading_deg", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_initial_heading_deg = 0.0
                    try:
                        phase3_vehicle_dynamics_initial_lateral_position_m = float(
                            vehicle_dynamics.get("initial_lateral_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_initial_lateral_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_initial_lateral_velocity_mps = float(
                            vehicle_dynamics.get("initial_lateral_velocity_mps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_initial_lateral_velocity_mps = 0.0
                    try:
                        phase3_vehicle_dynamics_initial_yaw_rate_rps = float(
                            vehicle_dynamics.get("initial_yaw_rate_rps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_initial_yaw_rate_rps = 0.0
                    try:
                        phase3_vehicle_dynamics_final_speed_mps = float(
                            vehicle_dynamics.get("final_speed_mps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_final_speed_mps = 0.0
                    try:
                        phase3_vehicle_dynamics_final_position_m = float(
                            vehicle_dynamics.get("final_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_final_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_final_heading_deg = float(
                            vehicle_dynamics.get("final_heading_deg", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_final_heading_deg = 0.0
                    try:
                        phase3_vehicle_dynamics_final_lateral_position_m = float(
                            vehicle_dynamics.get("final_lateral_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_final_lateral_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_final_lateral_velocity_mps = float(
                            vehicle_dynamics.get("final_lateral_velocity_mps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_final_lateral_velocity_mps = 0.0
                    try:
                        phase3_vehicle_dynamics_final_yaw_rate_rps = float(
                            vehicle_dynamics.get("final_yaw_rate_rps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_final_yaw_rate_rps = 0.0
                    try:
                        phase3_vehicle_dynamics_min_heading_deg = float(
                            vehicle_dynamics.get("min_heading_deg", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_min_heading_deg = 0.0
                    try:
                        phase3_vehicle_dynamics_avg_heading_deg = float(
                            vehicle_dynamics.get("avg_heading_deg", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_avg_heading_deg = 0.0
                    try:
                        phase3_vehicle_dynamics_max_heading_deg = float(
                            vehicle_dynamics.get("max_heading_deg", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_heading_deg = 0.0
                    try:
                        phase3_vehicle_dynamics_min_lateral_position_m = float(
                            vehicle_dynamics.get("min_lateral_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_min_lateral_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_avg_lateral_position_m = float(
                            vehicle_dynamics.get("avg_lateral_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_avg_lateral_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_max_lateral_position_m = float(
                            vehicle_dynamics.get("max_lateral_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_lateral_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_lateral_position_m = float(
                            vehicle_dynamics.get("max_abs_lateral_position_m", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_lateral_position_m = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_yaw_rate_rps = float(
                            vehicle_dynamics.get("max_abs_yaw_rate_rps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_yaw_rate_rps = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_lateral_velocity_mps = float(
                            vehicle_dynamics.get("max_abs_lateral_velocity_mps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_lateral_velocity_mps = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_accel_mps2 = float(
                            vehicle_dynamics.get("max_abs_accel_mps2", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_accel_mps2 = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_lateral_accel_mps2 = float(
                            vehicle_dynamics.get("max_abs_lateral_accel_mps2", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_lateral_accel_mps2 = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_yaw_accel_rps2 = float(
                            vehicle_dynamics.get("max_abs_yaw_accel_rps2", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_yaw_accel_rps2 = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_jerk_mps3 = float(
                            vehicle_dynamics.get("max_abs_jerk_mps3", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_jerk_mps3 = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3 = float(
                            vehicle_dynamics.get("max_abs_lateral_jerk_mps3", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3 = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3 = float(
                            vehicle_dynamics.get("max_abs_yaw_jerk_rps3", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3 = 0.0
                    phase3_vehicle_dynamics_planar_kinematics_enabled = bool(
                        vehicle_dynamics.get("planar_kinematics_enabled", False)
                    )
                    phase3_vehicle_dynamics_dynamic_bicycle_enabled = bool(
                        vehicle_dynamics.get("dynamic_bicycle_enabled", False)
                    )
                    try:
                        phase3_vehicle_dynamics_min_road_grade_percent = float(
                            vehicle_dynamics.get("min_road_grade_percent", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_min_road_grade_percent = 0.0
                    try:
                        phase3_vehicle_dynamics_avg_road_grade_percent = float(
                            vehicle_dynamics.get("avg_road_grade_percent", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_avg_road_grade_percent = 0.0
                    try:
                        phase3_vehicle_dynamics_max_road_grade_percent = float(
                            vehicle_dynamics.get("max_road_grade_percent", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_road_grade_percent = 0.0
                    try:
                        phase3_vehicle_dynamics_max_abs_grade_force_n = float(
                            vehicle_dynamics.get("max_abs_grade_force_n", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_dynamics_max_abs_grade_force_n = 0.0
                    try:
                        phase3_vehicle_control_command_step_count = int(
                            vehicle_dynamics.get("control_command_step_count", 0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_control_command_step_count = 0
                    try:
                        phase3_vehicle_control_throttle_brake_overlap_step_count = int(
                            vehicle_dynamics.get("control_throttle_brake_overlap_step_count", 0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_control_throttle_brake_overlap_step_count = 0
                    try:
                        phase3_vehicle_control_throttle_brake_overlap_ratio = float(
                            vehicle_dynamics.get("control_throttle_brake_overlap_ratio", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_control_throttle_brake_overlap_ratio = 0.0
                    try:
                        phase3_vehicle_control_max_abs_steering_rate_degps = float(
                            vehicle_dynamics.get("control_max_abs_steering_rate_degps", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_control_max_abs_steering_rate_degps = 0.0
                    try:
                        phase3_vehicle_control_max_abs_throttle_rate_per_sec = float(
                            vehicle_dynamics.get("control_max_abs_throttle_rate_per_sec", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_control_max_abs_throttle_rate_per_sec = 0.0
                    try:
                        phase3_vehicle_control_max_abs_brake_rate_per_sec = float(
                            vehicle_dynamics.get("control_max_abs_brake_rate_per_sec", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_control_max_abs_brake_rate_per_sec = 0.0
                    try:
                        phase3_vehicle_control_max_throttle_plus_brake = float(
                            vehicle_dynamics.get("control_max_throttle_plus_brake", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_control_max_throttle_plus_brake = 0.0
                    try:
                        phase3_vehicle_speed_tracking_target_step_count = int(
                            vehicle_dynamics.get("speed_tracking_target_step_count", 0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_speed_tracking_target_step_count = 0
                    try:
                        phase3_vehicle_speed_tracking_error_mps_min = float(
                            vehicle_dynamics.get("speed_tracking_error_mps_min", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_speed_tracking_error_mps_min = 0.0
                    try:
                        phase3_vehicle_speed_tracking_error_mps_avg = float(
                            vehicle_dynamics.get("speed_tracking_error_mps_avg", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_speed_tracking_error_mps_avg = 0.0
                    try:
                        phase3_vehicle_speed_tracking_error_mps_max = float(
                            vehicle_dynamics.get("speed_tracking_error_mps_max", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_speed_tracking_error_mps_max = 0.0
                    try:
                        phase3_vehicle_speed_tracking_error_abs_mps_avg = float(
                            vehicle_dynamics.get("speed_tracking_error_abs_mps_avg", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_speed_tracking_error_abs_mps_avg = 0.0
                    try:
                        phase3_vehicle_speed_tracking_error_abs_mps_max = float(
                            vehicle_dynamics.get("speed_tracking_error_abs_mps_max", 0.0)
                        )
                    except (TypeError, ValueError):
                        phase3_vehicle_speed_tracking_error_abs_mps_max = 0.0
                phase3_core_sim = phase3_hooks.get("phase3_core_sim")
                if isinstance(phase3_core_sim, dict):
                    phase3_core_sim_enabled = bool(phase3_core_sim.get("enabled", False))
                    phase3_core_sim_status = str(phase3_core_sim.get("status", "")).strip().lower() or "n/a"
                    phase3_core_sim_termination_reason = str(
                        phase3_core_sim.get("termination_reason", "")
                    ).strip()
                    phase3_core_sim_collision = bool(phase3_core_sim.get("collision", False))
                    phase3_core_sim_timeout = bool(phase3_core_sim.get("timeout", False))
                    phase3_core_sim_min_ttc_same_lane_sec = _to_float_or_none(
                        phase3_core_sim.get("min_ttc_same_lane_sec")
                    )
                    phase3_core_sim_min_ttc_adjacent_lane_sec = _to_float_or_none(
                        phase3_core_sim.get("min_ttc_adjacent_lane_sec")
                    )
                    phase3_core_sim_min_ttc_any_lane_sec = _to_float_or_none(
                        phase3_core_sim.get("min_ttc_any_lane_sec")
                    )
                    phase3_core_sim_enable_ego_collision_avoidance = bool(
                        phase3_core_sim.get("enable_ego_collision_avoidance", False)
                    )
                    phase3_core_sim_avoidance_ttc_threshold_sec = max(
                        0.0,
                        _to_float_or_none(phase3_core_sim.get("avoidance_ttc_threshold_sec"))
                        or 0.0,
                    )
                    phase3_core_sim_ego_max_brake_mps2 = max(
                        0.0,
                        _to_float_or_none(phase3_core_sim.get("ego_max_brake_mps2")) or 0.0,
                    )
                    phase3_core_sim_tire_friction_coeff = max(
                        0.0,
                        _to_float_or_none(phase3_core_sim.get("tire_friction_coeff")) or 0.0,
                    )
                    phase3_core_sim_surface_friction_scale = max(
                        0.0,
                        _to_float_or_none(phase3_core_sim.get("surface_friction_scale")) or 0.0,
                    )
                    phase3_core_sim_ego_avoidance_brake_event_count = max(
                        0,
                        _to_int(phase3_core_sim.get("ego_avoidance_brake_event_count"), default=0),
                    )
                    phase3_core_sim_ego_avoidance_applied_brake_mps2_max = max(
                        0.0,
                        _to_float_or_none(
                            phase3_core_sim.get("ego_avoidance_applied_brake_mps2_max")
                        )
                        or 0.0,
                    )
                phase3_core_sim_matrix = phase3_hooks.get("phase3_core_sim_matrix")
                if isinstance(phase3_core_sim_matrix, dict):
                    phase3_core_sim_matrix_enabled = bool(phase3_core_sim_matrix.get("enabled", False))
                    phase3_core_sim_matrix_schema_version = str(
                        phase3_core_sim_matrix.get("phase3_core_sim_matrix_schema_version", "")
                    ).strip()
                    phase3_core_sim_matrix_case_count = max(
                        0,
                        _to_int(phase3_core_sim_matrix.get("case_count"), default=0),
                    )
                    phase3_core_sim_matrix_success_case_count = max(
                        0,
                        _to_int(phase3_core_sim_matrix.get("success_case_count"), default=0),
                    )
                    phase3_core_sim_matrix_failed_case_count = max(
                        0,
                        _to_int(phase3_core_sim_matrix.get("failed_case_count"), default=0),
                    )
                    phase3_core_sim_matrix_all_cases_success = bool(
                        phase3_core_sim_matrix.get("all_cases_success", False)
                    )
                    phase3_core_sim_matrix_collision_case_count = max(
                        0,
                        _to_int(phase3_core_sim_matrix.get("collision_case_count"), default=0),
                    )
                    phase3_core_sim_matrix_timeout_case_count = max(
                        0,
                        _to_int(phase3_core_sim_matrix.get("timeout_case_count"), default=0),
                    )
                    phase3_core_sim_matrix_min_ttc_same_lane_sec_min = _to_float_or_none(
                        phase3_core_sim_matrix.get("min_ttc_same_lane_sec_min")
                    )
                    phase3_core_sim_matrix_lowest_ttc_same_lane_run_id = str(
                        phase3_core_sim_matrix.get("lowest_ttc_same_lane_run_id", "")
                    ).strip()
                    phase3_core_sim_matrix_min_ttc_any_lane_sec_min = _to_float_or_none(
                        phase3_core_sim_matrix.get("min_ttc_any_lane_sec_min")
                    )
                    phase3_core_sim_matrix_lowest_ttc_any_lane_run_id = str(
                        phase3_core_sim_matrix.get("lowest_ttc_any_lane_run_id", "")
                    ).strip()
                    status_counts_raw = phase3_core_sim_matrix.get("status_counts")
                    if isinstance(status_counts_raw, dict):
                        for status_raw, count_raw in status_counts_raw.items():
                            status = str(status_raw).strip().lower()
                            if not status:
                                continue
                            phase3_core_sim_matrix_status_counts[status] = max(
                                0,
                                _to_int(count_raw, default=0),
                            )
                    returncode_counts_raw = phase3_core_sim_matrix.get("returncode_counts")
                    if isinstance(returncode_counts_raw, dict):
                        for returncode_raw, count_raw in returncode_counts_raw.items():
                            returncode = str(returncode_raw).strip()
                            if not returncode:
                                continue
                            phase3_core_sim_matrix_returncode_counts[returncode] = max(
                                0,
                                _to_int(count_raw, default=0),
                            )
                sim_runtime_scenario_contract = phase3_hooks.get("sim_runtime_scenario_contract")
                if isinstance(sim_runtime_scenario_contract, dict):
                    phase3_sim_runtime_scenario_contract_enabled = bool(
                        sim_runtime_scenario_contract.get("enabled", False)
                    )
                    phase3_sim_runtime_scenario_contract_status_value = (
                        str(sim_runtime_scenario_contract.get("scenario_contract_status", "")).strip().lower()
                    )
                    phase3_sim_runtime_scenario_contract_runtime_ready = bool(
                        sim_runtime_scenario_contract.get("runtime_ready", False)
                    )
                    if (
                        phase3_sim_runtime_scenario_contract_enabled
                        or phase3_sim_runtime_scenario_contract_status_value
                    ):
                        phase3_sim_runtime_scenario_contract_checked = True
                        phase3_sim_runtime_scenario_contract_status = (
                            phase3_sim_runtime_scenario_contract_status_value or "n/a"
                        )
                    else:
                        phase3_sim_runtime_scenario_contract_runtime_ready = None
                sim_runtime_scene_result = phase3_hooks.get("sim_runtime_scene_result")
                if isinstance(sim_runtime_scene_result, dict):
                    phase3_sim_runtime_scene_result_enabled = bool(sim_runtime_scene_result.get("enabled", False))
                    phase3_sim_runtime_scene_result_status_value = (
                        str(sim_runtime_scene_result.get("scene_result_status", "")).strip().lower()
                    )
                    phase3_sim_runtime_scene_result_runtime_ready = bool(
                        sim_runtime_scene_result.get("runtime_ready", False)
                    )
                    if phase3_sim_runtime_scene_result_enabled or phase3_sim_runtime_scene_result_status_value:
                        phase3_sim_runtime_scene_result_checked = True
                        phase3_sim_runtime_scene_result_status = (
                            phase3_sim_runtime_scene_result_status_value or "n/a"
                        )
                    else:
                        phase3_sim_runtime_scene_result_runtime_ready = None
                if phase3_sim_runtime_scenario_contract_checked or phase3_sim_runtime_scene_result_checked:
                    phase3_object_sim_checked = True
                    object_sim_status_candidates = [
                        phase3_sim_runtime_scenario_contract_status,
                        phase3_sim_runtime_scene_result_status,
                    ]
                    if "fail" in object_sim_status_candidates:
                        phase3_object_sim_status = "fail"
                    elif (
                        phase3_sim_runtime_scenario_contract_checked
                        and phase3_sim_runtime_scene_result_checked
                        and phase3_sim_runtime_scenario_contract_status == "pass"
                        and phase3_sim_runtime_scene_result_status == "pass"
                        and phase3_sim_runtime_scenario_contract_runtime_ready is True
                        and phase3_sim_runtime_scene_result_runtime_ready is True
                    ):
                        phase3_object_sim_status = "pass"
                    elif "pass" in object_sim_status_candidates:
                        phase3_object_sim_status = "partial"
                    else:
                        phase3_object_sim_status = "n/a"
                dataset_traffic_diversity = phase3_hooks.get("dataset_traffic_diversity")
                if isinstance(dataset_traffic_diversity, dict):
                    phase3_dataset_traffic_run_summary_count = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("run_summary_count"), default=0),
                    )
                    run_status_counts_raw = dataset_traffic_diversity.get("run_status_counts")
                    if isinstance(run_status_counts_raw, dict):
                        for status_raw, count_raw in run_status_counts_raw.items():
                            status = str(status_raw).strip().lower()
                            if not status:
                                continue
                            phase3_dataset_traffic_run_status_counts[status] = max(
                                0,
                                _to_int(count_raw, default=0),
                            )
                    phase3_dataset_traffic_profile_count = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("traffic_profile_count"), default=0),
                    )
                    phase3_dataset_traffic_profile_ids = _normalize_text_list(
                        dataset_traffic_diversity.get("traffic_profile_ids")
                    )
                    phase3_dataset_traffic_profile_source_count = max(
                        0,
                        _to_int(
                            dataset_traffic_diversity.get("traffic_profile_source_count"),
                            default=0,
                        ),
                    )
                    phase3_dataset_traffic_profile_source_ids = _normalize_text_list(
                        dataset_traffic_diversity.get("traffic_profile_source_ids")
                    )
                    phase3_dataset_traffic_actor_pattern_count = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("traffic_actor_pattern_count"), default=0),
                    )
                    phase3_dataset_traffic_actor_pattern_ids = _normalize_text_list(
                        dataset_traffic_diversity.get("traffic_actor_pattern_ids")
                    )
                    phase3_dataset_traffic_lane_profile_signature_count = max(
                        0,
                        _to_int(
                            dataset_traffic_diversity.get("traffic_lane_profile_signature_count"),
                            default=0,
                        ),
                    )
                    phase3_dataset_traffic_lane_profile_signatures = _normalize_text_list(
                        dataset_traffic_diversity.get("traffic_lane_profile_signatures")
                    )
                    phase3_dataset_traffic_npc_count_sample_count = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("traffic_npc_count_sample_count"), default=0),
                    )
                    phase3_dataset_traffic_npc_count_min = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("traffic_npc_count_min"), default=0),
                    )
                    phase3_dataset_traffic_npc_count_avg_raw = _to_float_or_none(
                        dataset_traffic_diversity.get("traffic_npc_count_avg")
                    )
                    phase3_dataset_traffic_npc_count_avg = (
                        max(0.0, float(phase3_dataset_traffic_npc_count_avg_raw))
                        if phase3_dataset_traffic_npc_count_avg_raw is not None
                        else 0.0
                    )
                    phase3_dataset_traffic_npc_count_max = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("traffic_npc_count_max"), default=0),
                    )
                    phase3_dataset_traffic_npc_initial_gap_m_sample_count = max(
                        0,
                        _to_int(
                            dataset_traffic_diversity.get("traffic_npc_initial_gap_m_sample_count"),
                            default=0,
                        ),
                    )
                    phase3_dataset_traffic_npc_initial_gap_m_min = max(
                        0.0,
                        _to_float_or_none(
                            dataset_traffic_diversity.get("traffic_npc_initial_gap_m_min")
                        )
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_initial_gap_m_avg = max(
                        0.0,
                        _to_float_or_none(
                            dataset_traffic_diversity.get("traffic_npc_initial_gap_m_avg")
                        )
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_initial_gap_m_max = max(
                        0.0,
                        _to_float_or_none(
                            dataset_traffic_diversity.get("traffic_npc_initial_gap_m_max")
                        )
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_gap_step_m_sample_count = max(
                        0,
                        _to_int(
                            dataset_traffic_diversity.get("traffic_npc_gap_step_m_sample_count"),
                            default=0,
                        ),
                    )
                    phase3_dataset_traffic_npc_gap_step_m_min = max(
                        0.0,
                        _to_float_or_none(dataset_traffic_diversity.get("traffic_npc_gap_step_m_min"))
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_gap_step_m_avg = max(
                        0.0,
                        _to_float_or_none(dataset_traffic_diversity.get("traffic_npc_gap_step_m_avg"))
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_gap_step_m_max = max(
                        0.0,
                        _to_float_or_none(dataset_traffic_diversity.get("traffic_npc_gap_step_m_max"))
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_speed_scale_sample_count = max(
                        0,
                        _to_int(
                            dataset_traffic_diversity.get("traffic_npc_speed_scale_sample_count"),
                            default=0,
                        ),
                    )
                    phase3_dataset_traffic_npc_speed_scale_min = max(
                        0.0,
                        _to_float_or_none(dataset_traffic_diversity.get("traffic_npc_speed_scale_min"))
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_speed_scale_avg = max(
                        0.0,
                        _to_float_or_none(dataset_traffic_diversity.get("traffic_npc_speed_scale_avg"))
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_speed_scale_max = max(
                        0.0,
                        _to_float_or_none(dataset_traffic_diversity.get("traffic_npc_speed_scale_max"))
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_speed_jitter_mps_sample_count = max(
                        0,
                        _to_int(
                            dataset_traffic_diversity.get("traffic_npc_speed_jitter_mps_sample_count"),
                            default=0,
                        ),
                    )
                    phase3_dataset_traffic_npc_speed_jitter_mps_min = max(
                        0.0,
                        _to_float_or_none(
                            dataset_traffic_diversity.get("traffic_npc_speed_jitter_mps_min")
                        )
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_speed_jitter_mps_avg = max(
                        0.0,
                        _to_float_or_none(
                            dataset_traffic_diversity.get("traffic_npc_speed_jitter_mps_avg")
                        )
                        or 0.0,
                    )
                    phase3_dataset_traffic_npc_speed_jitter_mps_max = max(
                        0.0,
                        _to_float_or_none(
                            dataset_traffic_diversity.get("traffic_npc_speed_jitter_mps_max")
                        )
                        or 0.0,
                    )
                    phase3_dataset_traffic_lane_index_unique_count = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("traffic_lane_index_unique_count"), default=0),
                    )
                    lane_indices_raw = dataset_traffic_diversity.get("traffic_lane_indices")
                    lane_indices: set[int] = set()
                    if isinstance(lane_indices_raw, list):
                        for lane_index_raw in lane_indices_raw:
                            try:
                                lane_index = int(lane_index_raw)
                            except (TypeError, ValueError):
                                continue
                            lane_indices.add(lane_index)
                    phase3_dataset_traffic_lane_indices = sorted(lane_indices)
                    phase3_dataset_manifest_counts_rows = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("dataset_manifest_counts_rows"), default=0),
                    )
                    phase3_dataset_manifest_run_summary_count = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("dataset_manifest_run_summary_count"), default=0),
                    )
                    phase3_dataset_manifest_release_summary_count = max(
                        0,
                        _to_int(dataset_traffic_diversity.get("dataset_manifest_release_summary_count"), default=0),
                    )
                    phase3_dataset_manifest_versions = _normalize_text_list(
                        dataset_traffic_diversity.get("dataset_manifest_versions")
                    )
            functional_quality_gates = payload.get("functional_quality_gates")
            if isinstance(functional_quality_gates, dict):
                phase3_core_sim_gate = functional_quality_gates.get("phase3_core_sim_gate")
                if isinstance(phase3_core_sim_gate, dict):
                    phase3_core_sim_gate_result = (
                        str(phase3_core_sim_gate.get("result", "")).strip().lower() or "n/a"
                    )
                    phase3_core_sim_gate_reasons_raw = phase3_core_sim_gate.get("reasons")
                    if isinstance(phase3_core_sim_gate_reasons_raw, list):
                        phase3_core_sim_gate_reasons = _normalize_text_list(
                            phase3_core_sim_gate_reasons_raw
                        )
                        phase3_core_sim_gate_reason_count = len(phase3_core_sim_gate_reasons)
                    phase3_core_sim_gate_details = phase3_core_sim_gate.get("details")
                    if isinstance(phase3_core_sim_gate_details, dict):
                        phase3_core_sim_gate_require_success = bool(
                            phase3_core_sim_gate_details.get("require_success", False)
                        )
                        phase3_core_sim_gate_min_ttc_same_lane_sec = max(
                            0.0,
                            _to_float_or_none(
                                phase3_core_sim_gate_details.get("min_ttc_same_lane_sec")
                            )
                            or 0.0,
                        )
                        phase3_core_sim_gate_min_ttc_any_lane_sec = max(
                            0.0,
                            _to_float_or_none(
                                phase3_core_sim_gate_details.get("min_ttc_any_lane_sec")
                            )
                            or 0.0,
                        )
                phase3_dataset_traffic_gate = functional_quality_gates.get("phase3_dataset_traffic_gate")
                if isinstance(phase3_dataset_traffic_gate, dict):
                    phase3_dataset_traffic_gate_result = (
                        str(phase3_dataset_traffic_gate.get("result", "")).strip().lower() or "n/a"
                    )
                    phase3_dataset_traffic_gate_reasons_raw = phase3_dataset_traffic_gate.get("reasons")
                    if isinstance(phase3_dataset_traffic_gate_reasons_raw, list):
                        phase3_dataset_traffic_gate_reasons = _normalize_text_list(
                            phase3_dataset_traffic_gate_reasons_raw
                        )
                        phase3_dataset_traffic_gate_reason_count = len(phase3_dataset_traffic_gate_reasons)
                    phase3_dataset_traffic_gate_details = phase3_dataset_traffic_gate.get("details")
                    if isinstance(phase3_dataset_traffic_gate_details, dict):
                        phase3_dataset_traffic_gate_min_run_summary_count = max(
                            0,
                            _to_int(
                                phase3_dataset_traffic_gate_details.get("min_run_summary_count"),
                                default=0,
                            ),
                        )
                        phase3_dataset_traffic_gate_min_traffic_profile_count = max(
                            0,
                            _to_int(
                                phase3_dataset_traffic_gate_details.get("min_traffic_profile_count"),
                                default=0,
                            ),
                        )
                        phase3_dataset_traffic_gate_min_actor_pattern_count = max(
                            0,
                            _to_int(
                                phase3_dataset_traffic_gate_details.get("min_actor_pattern_count"),
                                default=0,
                            ),
                        )
                        phase3_dataset_traffic_gate_min_avg_npc_count_raw = _to_float_or_none(
                            phase3_dataset_traffic_gate_details.get("min_avg_npc_count")
                        )
                        phase3_dataset_traffic_gate_min_avg_npc_count = (
                            max(0.0, float(phase3_dataset_traffic_gate_min_avg_npc_count_raw))
                            if phase3_dataset_traffic_gate_min_avg_npc_count_raw is not None
                            else 0.0
                        )
                phase3_lane_risk_gate = functional_quality_gates.get("phase3_lane_risk_gate")
                if isinstance(phase3_lane_risk_gate, dict):
                    phase3_lane_risk_gate_result = (
                        str(phase3_lane_risk_gate.get("result", "")).strip().lower() or "n/a"
                    )
                    phase3_lane_risk_gate_reasons_raw = phase3_lane_risk_gate.get("reasons")
                    if isinstance(phase3_lane_risk_gate_reasons_raw, list):
                        phase3_lane_risk_gate_reasons = _normalize_text_list(
                            phase3_lane_risk_gate_reasons_raw
                        )
                        phase3_lane_risk_gate_reason_count = len(phase3_lane_risk_gate_reasons)
                    phase3_lane_risk_gate_details = phase3_lane_risk_gate.get("details")
                    if isinstance(phase3_lane_risk_gate_details, dict):
                        phase3_lane_risk_gate_min_ttc_same_lane_sec = max(
                            0.0,
                            _to_float_or_none(
                                phase3_lane_risk_gate_details.get("min_ttc_same_lane_sec")
                            )
                            or 0.0,
                        )
                        phase3_lane_risk_gate_min_ttc_adjacent_lane_sec = max(
                            0.0,
                            _to_float_or_none(
                                phase3_lane_risk_gate_details.get("min_ttc_adjacent_lane_sec")
                            )
                            or 0.0,
                        )
                        phase3_lane_risk_gate_min_ttc_any_lane_sec = max(
                            0.0,
                            _to_float_or_none(
                                phase3_lane_risk_gate_details.get("min_ttc_any_lane_sec")
                            )
                            or 0.0,
                        )
                        phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total = max(
                            0,
                            _to_int(
                                phase3_lane_risk_gate_details.get(
                                    "max_ttc_under_3s_same_lane_total"
                                ),
                                default=0,
                            ),
                        )
                        phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total = max(
                            0,
                            _to_int(
                                phase3_lane_risk_gate_details.get(
                                    "max_ttc_under_3s_adjacent_lane_total"
                                ),
                                default=0,
                            ),
                        )
                        phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total = max(
                            0,
                            _to_int(
                                phase3_lane_risk_gate_details.get(
                                    "max_ttc_under_3s_any_lane_total"
                                ),
                                default=0,
                            ),
                        )

            phase4_reference_primary_total_coverage_ratio = 0.0
            phase4_reference_primary_module_coverage: dict[str, float] = {}
            phase4_reference_secondary_total_coverage_ratio = 0.0
            phase4_reference_secondary_module_count = 0
            phase4_reference_secondary_module_coverage: dict[str, float] = {}
            phase4_hooks = payload.get("phase4_hooks")
            if isinstance(phase4_hooks, dict):
                reference_pattern_scan = phase4_hooks.get("reference_pattern_scan")
                if isinstance(reference_pattern_scan, dict):
                    primary_coverage_raw = reference_pattern_scan.get("reference_pattern_total_coverage_ratio", 0.0)
                    secondary_coverage_raw = reference_pattern_scan.get(
                        "reference_pattern_secondary_total_coverage_ratio", 0.0
                    )
                    secondary_module_count_raw = reference_pattern_scan.get(
                        "reference_pattern_secondary_module_count", 0
                    )
                    try:
                        phase4_reference_primary_total_coverage_ratio = float(primary_coverage_raw)
                    except (TypeError, ValueError):
                        phase4_reference_primary_total_coverage_ratio = 0.0
                    try:
                        phase4_reference_secondary_total_coverage_ratio = float(secondary_coverage_raw)
                    except (TypeError, ValueError):
                        phase4_reference_secondary_total_coverage_ratio = 0.0
                    try:
                        phase4_reference_secondary_module_count = int(secondary_module_count_raw)
                    except (TypeError, ValueError):
                        phase4_reference_secondary_module_count = 0
                    primary_module_coverage_raw = reference_pattern_scan.get(
                        "reference_pattern_module_coverage",
                        {},
                    )
                    if isinstance(primary_module_coverage_raw, dict):
                        for module_name_raw, module_coverage_raw in primary_module_coverage_raw.items():
                            module_name = str(module_name_raw).strip()
                            if not module_name:
                                continue
                            try:
                                phase4_reference_primary_module_coverage[module_name] = float(module_coverage_raw)
                            except (TypeError, ValueError):
                                phase4_reference_primary_module_coverage[module_name] = 0.0
                    secondary_module_coverage_raw = reference_pattern_scan.get(
                        "reference_pattern_secondary_module_coverage",
                        {},
                    )
                    if isinstance(secondary_module_coverage_raw, dict):
                        for module_name_raw, module_coverage_raw in secondary_module_coverage_raw.items():
                            module_name = str(module_name_raw).strip()
                            if not module_name:
                                continue
                            try:
                                phase4_reference_secondary_module_coverage[module_name] = float(module_coverage_raw)
                            except (TypeError, ValueError):
                                phase4_reference_secondary_module_coverage[module_name] = 0.0

            phase2_map_routing_checked = False
            phase2_map_routing_error_count = 0
            phase2_map_routing_warning_count = 0
            phase2_map_routing_semantic_warning_count = 0
            phase2_map_routing_unreachable_lane_count = 0
            phase2_map_routing_non_reciprocal_link_count = 0
            phase2_map_routing_continuity_gap_warning_count = 0
            phase2_map_routing_status = "n/a"
            phase2_map_route_checked = False
            phase2_map_route_status = "n/a"
            phase2_map_route_lane_count = 0
            phase2_map_route_hop_count = 0
            phase2_map_route_total_length_m = 0.0
            phase2_map_route_segment_count = 0
            phase2_map_route_via_lane_count = 0
            phase2_map_route_entry_lane_id = ""
            phase2_map_route_exit_lane_id = ""
            phase2_sensor_checked = False
            phase2_sensor_fidelity_tier = "n/a"
            phase2_sensor_fidelity_tier_score = 0.0
            phase2_sensor_frame_count = 0
            phase2_sensor_modality_counts: dict[str, int] = {}
            phase2_sensor_camera_frame_count = 0
            phase2_sensor_camera_noise_stddev_px_avg = 0.0
            phase2_sensor_camera_dynamic_range_stops_avg = 0.0
            phase2_sensor_camera_visibility_score_avg = 0.0
            phase2_sensor_camera_motion_blur_level_avg = 0.0
            phase2_sensor_camera_snr_db_avg = 0.0
            phase2_sensor_camera_exposure_time_ms_avg = 0.0
            phase2_sensor_camera_signal_saturation_ratio_avg = 0.0
            phase2_sensor_camera_rolling_shutter_total_delay_ms_avg = 0.0
            phase2_sensor_camera_normalized_total_noise_avg = 0.0
            phase2_sensor_camera_distortion_edge_shift_px_avg = 0.0
            phase2_sensor_camera_principal_point_offset_norm_avg = 0.0
            phase2_sensor_camera_effective_focal_length_px_avg = 0.0
            phase2_sensor_camera_projection_mode_counts: dict[str, int] = {}
            phase2_sensor_camera_gain_db_avg = 0.0
            phase2_sensor_camera_gamma_avg = 0.0
            phase2_sensor_camera_white_balance_kelvin_avg = 0.0
            phase2_sensor_camera_vignetting_edge_darkening_avg = 0.0
            phase2_sensor_camera_bloom_halo_strength_avg = 0.0
            phase2_sensor_camera_chromatic_aberration_shift_px_avg = 0.0
            phase2_sensor_camera_tonemapper_disabled_frame_count = 0
            phase2_sensor_camera_bloom_level_counts: dict[str, int] = {}
            phase2_sensor_camera_depth_enabled_frame_count = 0
            phase2_sensor_camera_depth_min_m_avg = 0.0
            phase2_sensor_camera_depth_max_m_avg = 0.0
            phase2_sensor_camera_depth_bit_depth_avg = 0.0
            phase2_sensor_camera_depth_mode_counts: dict[str, int] = {}
            phase2_sensor_camera_optical_flow_enabled_frame_count = 0
            phase2_sensor_camera_optical_flow_magnitude_px_avg = 0.0
            phase2_sensor_camera_optical_flow_velocity_direction_counts: dict[str, int] = {}
            phase2_sensor_camera_optical_flow_y_axis_direction_counts: dict[str, int] = {}
            phase2_sensor_lidar_frame_count = 0
            phase2_sensor_lidar_point_count_total = 0
            phase2_sensor_lidar_point_count_avg = 0.0
            phase2_sensor_lidar_returns_per_laser_avg = 0.0
            phase2_sensor_lidar_detection_ratio_avg = 0.0
            phase2_sensor_lidar_effective_max_range_m_avg = 0.0
            phase2_sensor_radar_frame_count = 0
            phase2_sensor_radar_target_count_total = 0
            phase2_sensor_radar_ghost_target_count_total = 0
            phase2_sensor_radar_false_positive_count_total = 0
            phase2_sensor_radar_false_positive_count_avg = 0.0
            phase2_sensor_radar_false_positive_rate_avg = 0.0
            phase2_sensor_radar_ghost_target_count_avg = 0.0
            phase2_sensor_radar_clutter_index_avg = 0.0
            phase2_sensor_sweep_checked = False
            phase2_sensor_sweep_fidelity_tier = "n/a"
            phase2_sensor_sweep_candidate_count = 0
            phase2_sensor_sweep_best_rig_id = ""
            phase2_sensor_sweep_best_heuristic_score = 0.0
            phase2_log_replay_checked = False
            phase2_log_replay_manifest_present = False
            phase2_log_replay_summary_present = False
            phase2_log_replay_status = "n/a"
            phase2_log_replay_run_source = "n/a"
            phase2_log_replay_run_status = "n/a"
            phase2_log_replay_log_id = ""
            phase2_log_replay_map_id = ""
            phase2_hooks = payload.get("phase2_hooks")
            if isinstance(phase2_hooks, dict) and bool(phase2_hooks.get("enabled", False)):
                sensor_bridge_out_raw = str(phase2_hooks.get("sensor_bridge_out", "")).strip()
                loaded_sensor_bridge_report: dict[str, Any] = {}
                if sensor_bridge_out_raw:
                    phase2_sensor_checked = True
                    sensor_bridge_report_path = Path(sensor_bridge_out_raw)
                    if not sensor_bridge_report_path.is_absolute():
                        sensor_bridge_report_path = (resolved.parent / sensor_bridge_report_path).resolve()
                    try:
                        raw_sensor_bridge_report = json.loads(sensor_bridge_report_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        raw_sensor_bridge_report = {}
                    if isinstance(raw_sensor_bridge_report, dict):
                        loaded_sensor_bridge_report = raw_sensor_bridge_report
                phase2_sensor_fidelity_tier_raw = loaded_sensor_bridge_report.get(
                    "sensor_fidelity_tier",
                    phase2_hooks.get("sensor_fidelity_tier", ""),
                )
                phase2_sensor_fidelity_tier = str(phase2_sensor_fidelity_tier_raw).strip().lower()
                if not phase2_sensor_fidelity_tier:
                    phase2_sensor_fidelity_tier_input = str(
                        phase2_hooks.get("sensor_bridge_fidelity_tier_input", "")
                    ).strip().lower()
                    phase2_sensor_fidelity_tier = phase2_sensor_fidelity_tier_input or "n/a"
                phase2_sensor_fidelity_tier_score_raw = loaded_sensor_bridge_report.get(
                    "sensor_fidelity_tier_score",
                    phase2_hooks.get("sensor_fidelity_tier_score", 0.0),
                )
                try:
                    phase2_sensor_fidelity_tier_score = float(phase2_sensor_fidelity_tier_score_raw)
                except (TypeError, ValueError):
                    phase2_sensor_fidelity_tier_score = 0.0
                phase2_sensor_fidelity_tier_score = max(0.0, phase2_sensor_fidelity_tier_score)
                phase2_sensor_frame_count_raw = loaded_sensor_bridge_report.get(
                    "frame_count",
                    phase2_hooks.get("sensor_frame_count", 0),
                )
                try:
                    phase2_sensor_frame_count = int(phase2_sensor_frame_count_raw)
                except (TypeError, ValueError):
                    phase2_sensor_frame_count = 0
                if phase2_sensor_frame_count <= 0:
                    sensor_frames_raw = loaded_sensor_bridge_report.get("frames", [])
                    if isinstance(sensor_frames_raw, list):
                        phase2_sensor_frame_count = len(sensor_frames_raw)
                phase2_sensor_frame_count = max(0, phase2_sensor_frame_count)
                phase2_sensor_modality_counts_raw = loaded_sensor_bridge_report.get(
                    "sensor_stream_modality_counts",
                    phase2_hooks.get("sensor_stream_modality_counts", {}),
                )
                if isinstance(phase2_sensor_modality_counts_raw, dict):
                    for raw_key, raw_value in phase2_sensor_modality_counts_raw.items():
                        key = str(raw_key).strip().lower()
                        if not key:
                            continue
                        value = max(0, _to_int(raw_value, default=0))
                        phase2_sensor_modality_counts[key] = value
                phase2_sensor_quality_summary_raw = loaded_sensor_bridge_report.get("sensor_quality_summary")
                if not isinstance(phase2_sensor_quality_summary_raw, dict):
                    fallback_quality_summary_raw = phase2_hooks.get("sensor_quality_summary", {})
                    if isinstance(fallback_quality_summary_raw, dict):
                        phase2_sensor_quality_summary_raw = fallback_quality_summary_raw
                    else:
                        phase2_sensor_quality_summary_raw = {}
                if isinstance(phase2_sensor_quality_summary_raw, dict) and phase2_sensor_quality_summary_raw:
                    phase2_sensor_camera_frame_count = max(
                        0,
                        _to_int(phase2_sensor_quality_summary_raw.get("camera_frame_count"), default=0),
                    )
                    phase2_sensor_camera_noise_stddev_px_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_noise_stddev_px_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_dynamic_range_stops_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_dynamic_range_stops_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_visibility_score_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_visibility_score_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_motion_blur_level_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_motion_blur_level_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_snr_db_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_snr_db_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_exposure_time_ms_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_exposure_time_ms_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_signal_saturation_ratio_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_signal_saturation_ratio_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_rolling_shutter_total_delay_ms_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get(
                                    "camera_rolling_shutter_total_delay_ms_avg"
                                )
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_normalized_total_noise_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_normalized_total_noise_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_distortion_edge_shift_px_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_distortion_edge_shift_px_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_principal_point_offset_norm_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get(
                                    "camera_principal_point_offset_norm_avg"
                                )
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_effective_focal_length_px_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get(
                                    "camera_effective_focal_length_px_avg"
                                )
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_projection_mode_counts_raw = phase2_sensor_quality_summary_raw.get(
                        "camera_projection_mode_counts",
                        {},
                    )
                    if isinstance(phase2_sensor_camera_projection_mode_counts_raw, dict):
                        for raw_key, raw_value in phase2_sensor_camera_projection_mode_counts_raw.items():
                            key = str(raw_key).strip().upper()
                            if not key:
                                continue
                            value = max(0, _to_int(raw_value, default=0))
                            phase2_sensor_camera_projection_mode_counts[key] = value
                    phase2_sensor_camera_gain_db_avg = float(
                        _to_float_or_none(
                            phase2_sensor_quality_summary_raw.get("camera_gain_db_avg")
                        )
                        or 0.0
                    )
                    phase2_sensor_camera_gamma_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_gamma_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_white_balance_kelvin_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_white_balance_kelvin_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_vignetting_edge_darkening_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get(
                                    "camera_vignetting_edge_darkening_avg"
                                )
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_bloom_halo_strength_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_bloom_halo_strength_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_chromatic_aberration_shift_px_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get(
                                    "camera_chromatic_aberration_shift_px_avg"
                                )
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_tonemapper_disabled_frame_count = max(
                        0,
                        _to_int(
                            phase2_sensor_quality_summary_raw.get(
                                "camera_tonemapper_disabled_frame_count"
                            ),
                            default=0,
                        ),
                    )
                    phase2_sensor_camera_bloom_level_counts_raw = phase2_sensor_quality_summary_raw.get(
                        "camera_bloom_level_counts",
                        {},
                    )
                    if isinstance(phase2_sensor_camera_bloom_level_counts_raw, dict):
                        for raw_key, raw_value in phase2_sensor_camera_bloom_level_counts_raw.items():
                            key = str(raw_key).strip().upper()
                            if not key:
                                continue
                            value = max(0, _to_int(raw_value, default=0))
                            phase2_sensor_camera_bloom_level_counts[key] = value
                    phase2_sensor_camera_depth_enabled_frame_count = max(
                        0,
                        _to_int(
                            phase2_sensor_quality_summary_raw.get(
                                "camera_depth_enabled_frame_count"
                            ),
                            default=0,
                        ),
                    )
                    phase2_sensor_camera_depth_min_m_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_depth_min_m_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_depth_max_m_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_depth_max_m_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_depth_bit_depth_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("camera_depth_bit_depth_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_depth_mode_counts_raw = phase2_sensor_quality_summary_raw.get(
                        "camera_depth_mode_counts",
                        {},
                    )
                    if isinstance(phase2_sensor_camera_depth_mode_counts_raw, dict):
                        for raw_key, raw_value in phase2_sensor_camera_depth_mode_counts_raw.items():
                            key = str(raw_key).strip().upper()
                            if not key:
                                continue
                            value = max(0, _to_int(raw_value, default=0))
                            phase2_sensor_camera_depth_mode_counts[key] = value
                    phase2_sensor_camera_optical_flow_enabled_frame_count = max(
                        0,
                        _to_int(
                            phase2_sensor_quality_summary_raw.get(
                                "camera_optical_flow_enabled_frame_count"
                            ),
                            default=0,
                        ),
                    )
                    phase2_sensor_camera_optical_flow_magnitude_px_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get(
                                    "camera_optical_flow_magnitude_px_avg"
                                )
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_camera_optical_flow_velocity_direction_counts_raw = (
                        phase2_sensor_quality_summary_raw.get(
                            "camera_optical_flow_velocity_direction_counts",
                            {},
                        )
                    )
                    if isinstance(
                        phase2_sensor_camera_optical_flow_velocity_direction_counts_raw,
                        dict,
                    ):
                        for (
                            raw_key,
                            raw_value,
                        ) in phase2_sensor_camera_optical_flow_velocity_direction_counts_raw.items():
                            key = str(raw_key).strip().upper()
                            if not key:
                                continue
                            value = max(0, _to_int(raw_value, default=0))
                            phase2_sensor_camera_optical_flow_velocity_direction_counts[key] = value
                    phase2_sensor_camera_optical_flow_y_axis_direction_counts_raw = (
                        phase2_sensor_quality_summary_raw.get(
                            "camera_optical_flow_y_axis_direction_counts",
                            {},
                        )
                    )
                    if isinstance(
                        phase2_sensor_camera_optical_flow_y_axis_direction_counts_raw,
                        dict,
                    ):
                        for (
                            raw_key,
                            raw_value,
                        ) in phase2_sensor_camera_optical_flow_y_axis_direction_counts_raw.items():
                            key = str(raw_key).strip().upper()
                            if not key:
                                continue
                            value = max(0, _to_int(raw_value, default=0))
                            phase2_sensor_camera_optical_flow_y_axis_direction_counts[key] = value
                    phase2_sensor_lidar_frame_count = max(
                        0,
                        _to_int(phase2_sensor_quality_summary_raw.get("lidar_frame_count"), default=0),
                    )
                    phase2_sensor_lidar_point_count_total = max(
                        0,
                        _to_int(phase2_sensor_quality_summary_raw.get("lidar_point_count_total"), default=0),
                    )
                    phase2_sensor_lidar_point_count_avg = max(
                        0.0,
                        float(_to_float_or_none(phase2_sensor_quality_summary_raw.get("lidar_point_count_avg")) or 0.0),
                    )
                    phase2_sensor_lidar_returns_per_laser_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("lidar_returns_per_laser_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_lidar_detection_ratio_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("lidar_detection_ratio_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_lidar_effective_max_range_m_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("lidar_effective_max_range_m_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_radar_frame_count = max(
                        0,
                        _to_int(phase2_sensor_quality_summary_raw.get("radar_frame_count"), default=0),
                    )
                    phase2_sensor_radar_target_count_total = max(
                        0,
                        _to_int(phase2_sensor_quality_summary_raw.get("radar_target_count_total"), default=0),
                    )
                    phase2_sensor_radar_ghost_target_count_total = max(
                        0,
                        _to_int(
                            phase2_sensor_quality_summary_raw.get("radar_ghost_target_count_total"),
                            default=0,
                        ),
                    )
                    phase2_sensor_radar_false_positive_count_total = max(
                        0,
                        _to_int(phase2_sensor_quality_summary_raw.get("radar_false_positive_count_total"), default=0),
                    )
                    phase2_sensor_radar_false_positive_count_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("radar_false_positive_count_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_radar_false_positive_rate_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("radar_false_positive_rate_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_radar_ghost_target_count_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("radar_ghost_target_count_avg")
                            )
                            or 0.0
                        ),
                    )
                    phase2_sensor_radar_clutter_index_avg = max(
                        0.0,
                        float(
                            _to_float_or_none(
                                phase2_sensor_quality_summary_raw.get("radar_clutter_index_avg")
                            )
                            or 0.0
                        ),
                    )
                if (
                    not phase2_sensor_checked
                    and (
                        (phase2_sensor_fidelity_tier and phase2_sensor_fidelity_tier != "n/a")
                        or phase2_sensor_fidelity_tier_score > 0.0
                        or phase2_sensor_frame_count > 0
                        or phase2_sensor_modality_counts
                        or phase2_sensor_camera_depth_enabled_frame_count > 0
                        or phase2_sensor_camera_optical_flow_enabled_frame_count > 0
                        or phase2_sensor_lidar_point_count_total > 0
                        or phase2_sensor_radar_false_positive_count_total > 0
                    )
                ):
                    phase2_sensor_checked = True

                sensor_sweep_out_raw = str(phase2_hooks.get("sensor_sweep_out", "")).strip()
                loaded_sensor_sweep_report: dict[str, Any] = {}
                if sensor_sweep_out_raw:
                    phase2_sensor_sweep_checked = True
                    sensor_sweep_report_path = Path(sensor_sweep_out_raw)
                    if not sensor_sweep_report_path.is_absolute():
                        sensor_sweep_report_path = (resolved.parent / sensor_sweep_report_path).resolve()
                    try:
                        raw_sensor_sweep_report = json.loads(sensor_sweep_report_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        raw_sensor_sweep_report = {}
                    if isinstance(raw_sensor_sweep_report, dict):
                        loaded_sensor_sweep_report = raw_sensor_sweep_report
                phase2_sensor_sweep_fidelity_tier_raw = loaded_sensor_sweep_report.get(
                    "sensor_fidelity_tier",
                    phase2_hooks.get("sensor_sweep_fidelity_tier", ""),
                )
                phase2_sensor_sweep_fidelity_tier = (
                    str(phase2_sensor_sweep_fidelity_tier_raw).strip().lower() or "n/a"
                )
                if phase2_sensor_sweep_fidelity_tier == "n/a":
                    phase2_sensor_sweep_fidelity_tier = phase2_sensor_fidelity_tier or "n/a"
                phase2_sensor_sweep_candidate_count_raw = loaded_sensor_sweep_report.get(
                    "candidate_count",
                    phase2_hooks.get("sensor_sweep_candidate_count", 0),
                )
                phase2_sensor_sweep_candidate_count = max(
                    0,
                    _to_int(phase2_sensor_sweep_candidate_count_raw, default=0),
                )
                if phase2_sensor_sweep_candidate_count <= 0:
                    sweep_rankings_raw = loaded_sensor_sweep_report.get("rankings", [])
                    if isinstance(sweep_rankings_raw, list):
                        phase2_sensor_sweep_candidate_count = len(
                            [row for row in sweep_rankings_raw if isinstance(row, dict)]
                        )
                phase2_sensor_sweep_best_rig_id = str(
                    loaded_sensor_sweep_report.get(
                        "best_rig_id",
                        phase2_hooks.get("sensor_sweep_best_rig_id", ""),
                    )
                ).strip()
                phase2_sensor_sweep_best_heuristic_score = max(
                    0.0,
                    float(
                        _to_float_or_none(
                            loaded_sensor_sweep_report.get(
                                "best_heuristic_score",
                                phase2_hooks.get("sensor_sweep_best_heuristic_score", 0.0),
                            )
                        )
                        or 0.0
                    ),
                )
                if phase2_sensor_sweep_best_heuristic_score <= 0.0:
                    sweep_rankings_raw = loaded_sensor_sweep_report.get("rankings", [])
                    if isinstance(sweep_rankings_raw, list) and sweep_rankings_raw:
                        first_ranking = sweep_rankings_raw[0]
                        if isinstance(first_ranking, dict):
                            phase2_sensor_sweep_best_heuristic_score = max(
                                0.0,
                                float(_to_float_or_none(first_ranking.get("heuristic_score")) or 0.0),
                            )
                            if not phase2_sensor_sweep_best_rig_id:
                                phase2_sensor_sweep_best_rig_id = str(
                                    first_ranking.get("rig_id", "")
                                ).strip()
                if (
                    not phase2_sensor_sweep_checked
                    and (
                        phase2_sensor_sweep_candidate_count > 0
                        or bool(phase2_sensor_sweep_best_rig_id)
                        or phase2_sensor_sweep_best_heuristic_score > 0.0
                    )
                ):
                    phase2_sensor_sweep_checked = True

                log_replay_manifest_path_raw = str(phase2_hooks.get("log_replay_manifest_path", "")).strip()
                if log_replay_manifest_path_raw:
                    phase2_log_replay_checked = True
                    log_replay_manifest_path = Path(log_replay_manifest_path_raw)
                    if not log_replay_manifest_path.is_absolute():
                        log_replay_manifest_path = (resolved.parent / log_replay_manifest_path).resolve()
                    loaded_log_replay_manifest: dict[str, Any] = {}
                    if log_replay_manifest_path.exists():
                        try:
                            raw_log_replay_manifest = json.loads(log_replay_manifest_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            raw_log_replay_manifest = {}
                        if isinstance(raw_log_replay_manifest, dict):
                            loaded_log_replay_manifest = raw_log_replay_manifest
                    if loaded_log_replay_manifest:
                        phase2_log_replay_manifest_present = True
                    phase2_log_replay_log_id = str(loaded_log_replay_manifest.get("log_id", "")).strip()
                    phase2_log_replay_run_status_raw = str(loaded_log_replay_manifest.get("status", "")).strip().lower()
                    phase2_log_replay_run_source = "manifest"
                    log_replay_summary_path_raw = str(loaded_log_replay_manifest.get("summary_path", "")).strip()
                    if log_replay_summary_path_raw:
                        log_replay_summary_path = Path(log_replay_summary_path_raw)
                        if not log_replay_summary_path.is_absolute():
                            log_replay_summary_path = (resolved.parent / log_replay_summary_path).resolve()
                        if log_replay_summary_path.exists():
                            try:
                                raw_log_replay_summary = json.loads(log_replay_summary_path.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError):
                                raw_log_replay_summary = {}
                            if isinstance(raw_log_replay_summary, dict):
                                phase2_log_replay_summary_present = True
                                phase2_log_replay_run_status_candidate = str(
                                    raw_log_replay_summary.get("status", "")
                                ).strip().lower()
                                if phase2_log_replay_run_status_candidate:
                                    phase2_log_replay_run_status_raw = phase2_log_replay_run_status_candidate
                                    phase2_log_replay_run_source = "summary"
                    if not phase2_log_replay_run_status_raw:
                        phase2_log_replay_run_status_raw = (
                            str(phase2_hooks.get("log_replay_status", "")).strip().lower()
                        )
                        if phase2_log_replay_run_status_raw:
                            phase2_log_replay_run_source = "phase2_hooks"
                    if not phase2_log_replay_log_id:
                        phase2_log_replay_log_id = str(phase2_hooks.get("log_id", "")).strip()
                    phase2_log_replay_run_status = phase2_log_replay_run_status_raw or "n/a"
                    phase2_log_replay_map_id = str(phase2_hooks.get("map_id", "")).strip()
                    if not phase2_log_replay_map_id:
                        log_scene_path_raw = str(loaded_log_replay_manifest.get("log_scene_path", "")).strip() or str(
                            phase2_hooks.get("log_scene", "")
                        ).strip()
                        if log_scene_path_raw:
                            log_scene_path = Path(log_scene_path_raw)
                            if not log_scene_path.is_absolute():
                                log_scene_path = (resolved.parent / log_scene_path).resolve()
                            if log_scene_path.exists():
                                try:
                                    raw_log_scene_payload = json.loads(log_scene_path.read_text(encoding="utf-8"))
                                except (OSError, json.JSONDecodeError):
                                    raw_log_scene_payload = {}
                                if isinstance(raw_log_scene_payload, dict):
                                    phase2_log_replay_map_id = str(raw_log_scene_payload.get("map_id", "")).strip()
                    if phase2_log_replay_run_status in {"success", "pass"}:
                        phase2_log_replay_status = "pass"
                    elif phase2_log_replay_run_status in {"fail", "failed", "error", "timeout"}:
                        phase2_log_replay_status = "fail"
                    elif phase2_log_replay_manifest_present:
                        phase2_log_replay_status = "partial"
                    else:
                        phase2_log_replay_status = "fail"

                map_validate_report_out_raw = str(phase2_hooks.get("map_validate_report_out", "")).strip()
                if map_validate_report_out_raw:
                    phase2_map_routing_checked = True
                    map_validate_report_path = Path(map_validate_report_out_raw)
                    if not map_validate_report_path.is_absolute():
                        map_validate_report_path = (resolved.parent / map_validate_report_path).resolve()
                    loaded_map_validate_report: dict[str, Any] = {}
                    try:
                        raw_map_validate_report = json.loads(
                            map_validate_report_path.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError):
                        raw_map_validate_report = {}
                    if isinstance(raw_map_validate_report, dict):
                        loaded_map_validate_report = raw_map_validate_report
                    phase2_map_routing_error_count = max(
                        0,
                        _to_int(loaded_map_validate_report.get("error_count"), default=0),
                    )
                    phase2_map_routing_warning_count = max(
                        0,
                        _to_int(loaded_map_validate_report.get("warning_count"), default=0),
                    )
                    routing_semantic_summary = loaded_map_validate_report.get("routing_semantic_summary")
                    if isinstance(routing_semantic_summary, dict):
                        phase2_map_routing_semantic_warning_count = max(
                            0,
                            _to_int(routing_semantic_summary.get("routing_semantic_warning_count"), default=0),
                        )
                        phase2_map_routing_unreachable_lane_count = max(
                            0,
                            _to_int(routing_semantic_summary.get("unreachable_lane_count"), default=0),
                        )
                        phase2_map_routing_non_reciprocal_link_count = max(
                            0,
                            _to_int(
                                routing_semantic_summary.get("non_reciprocal_predecessor_warning_count"),
                                default=0,
                            ),
                        ) + max(
                            0,
                            _to_int(
                                routing_semantic_summary.get("non_reciprocal_successor_warning_count"),
                                default=0,
                            ),
                        )
                        phase2_map_routing_continuity_gap_warning_count = max(
                            0,
                            _to_int(routing_semantic_summary.get("continuity_gap_warning_count"), default=0),
                        )
                        phase2_map_routing_status = (
                            str(routing_semantic_summary.get("routing_semantic_status", "")).strip().lower() or "n/a"
                        )
                    elif phase2_map_routing_error_count > 0:
                        phase2_map_routing_status = "fail"
                    elif phase2_map_routing_warning_count > 0:
                        phase2_map_routing_status = "warn"
                    else:
                        phase2_map_routing_status = "pass"

                map_route_report_out_raw = str(phase2_hooks.get("map_route_report_out", "")).strip()
                loaded_map_route_report: dict[str, Any] = {}
                if map_route_report_out_raw:
                    phase2_map_route_checked = True
                    map_route_report_path = Path(map_route_report_out_raw)
                    if not map_route_report_path.is_absolute():
                        map_route_report_path = (resolved.parent / map_route_report_path).resolve()
                    try:
                        raw_map_route_report = json.loads(
                            map_route_report_path.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError):
                        raw_map_route_report = {}
                    if isinstance(raw_map_route_report, dict):
                        loaded_map_route_report = raw_map_route_report

                phase2_map_route_status_raw = loaded_map_route_report.get(
                    "route_status",
                    phase2_hooks.get("map_route_status", ""),
                )
                phase2_map_route_lane_count_raw = loaded_map_route_report.get(
                    "route_lane_count",
                    phase2_hooks.get("map_route_lane_count", 0),
                )
                phase2_map_route_hop_count_raw = loaded_map_route_report.get(
                    "route_hop_count",
                    phase2_hooks.get("map_route_hop_count", 0),
                )
                phase2_map_route_total_length_m_raw = loaded_map_route_report.get(
                    "route_total_length_m",
                    phase2_hooks.get("map_route_total_length_m", 0.0),
                )
                phase2_map_route_segment_count_raw = loaded_map_route_report.get(
                    "route_segment_count",
                    phase2_hooks.get("map_route_segment_count", 0),
                )
                phase2_map_route_via_lane_ids_raw = loaded_map_route_report.get(
                    "via_lane_ids_input",
                    phase2_hooks.get("map_route_via_lane_ids", []),
                )
                phase2_map_route_lane_count = max(
                    0,
                    _to_int(phase2_map_route_lane_count_raw, default=0),
                )
                phase2_map_route_hop_count = max(
                    0,
                    _to_int(phase2_map_route_hop_count_raw, default=0),
                )
                phase2_map_route_segment_count = max(
                    0,
                    _to_int(phase2_map_route_segment_count_raw, default=0),
                )
                phase2_map_route_via_lane_count = len(_normalize_text_list(phase2_map_route_via_lane_ids_raw))
                try:
                    phase2_map_route_total_length_m = float(phase2_map_route_total_length_m_raw)
                except (TypeError, ValueError):
                    phase2_map_route_total_length_m = 0.0
                phase2_map_route_total_length_m = max(0.0, phase2_map_route_total_length_m)
                phase2_map_route_entry_lane_id = str(
                    loaded_map_route_report.get(
                        "selected_entry_lane_id",
                        phase2_hooks.get("map_route_entry_lane_id", ""),
                    )
                ).strip()
                phase2_map_route_exit_lane_id = str(
                    loaded_map_route_report.get(
                        "selected_exit_lane_id",
                        phase2_hooks.get("map_route_exit_lane_id", ""),
                    )
                ).strip()

                if (
                    not phase2_map_route_checked
                    and (
                        str(phase2_map_route_status_raw).strip()
                        or phase2_map_route_lane_count > 0
                        or phase2_map_route_hop_count > 0
                        or phase2_map_route_total_length_m > 0.0
                        or phase2_map_route_segment_count > 0
                        or phase2_map_route_via_lane_count > 0
                        or phase2_map_route_entry_lane_id
                        or phase2_map_route_exit_lane_id
                    )
                ):
                    phase2_map_route_checked = True
                phase2_map_route_status = str(phase2_map_route_status_raw).strip().lower()
                if not phase2_map_route_status:
                    if phase2_map_route_checked and phase2_map_route_lane_count > 0:
                        phase2_map_route_status = "pass"
                    else:
                        phase2_map_route_status = "n/a"

            phase3_lane_risk_summary_run_count = 0
            phase3_lane_risk_min_ttc_same_lane_sec: float | None = None
            phase3_lane_risk_min_ttc_adjacent_lane_sec: float | None = None
            phase3_lane_risk_min_ttc_any_lane_sec: float | None = None
            phase3_lane_risk_ttc_under_3s_same_lane_total = 0
            phase3_lane_risk_ttc_under_3s_adjacent_lane_total = 0
            phase3_lane_risk_same_lane_rows_total = 0
            phase3_lane_risk_adjacent_lane_rows_total = 0
            phase3_lane_risk_other_lane_rows_total = 0
            batch_result_path_raw = str(payload.get("batch_result_path", "")).strip()
            if batch_result_path_raw:
                batch_result_path = Path(batch_result_path_raw)
                if not batch_result_path.is_absolute():
                    batch_result_path = (resolved.parent / batch_result_path).resolve()
                batch_result_payload: dict[str, Any] = {}
                try:
                    loaded_batch_result_payload = json.loads(batch_result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    loaded_batch_result_payload = {}
                if isinstance(loaded_batch_result_payload, dict):
                    batch_result_payload = loaded_batch_result_payload
                lane_risk_batch_summary = batch_result_payload.get("lane_risk_batch_summary")
                if isinstance(lane_risk_batch_summary, dict):
                    phase3_lane_risk_summary_run_count = max(
                        0,
                        _to_int(
                            lane_risk_batch_summary.get("lane_risk_summary_run_count"),
                            default=0,
                        ),
                    )
                    phase3_lane_risk_min_ttc_same_lane_sec = _to_float_or_none(
                        lane_risk_batch_summary.get("min_ttc_same_lane_sec")
                    )
                    phase3_lane_risk_min_ttc_adjacent_lane_sec = _to_float_or_none(
                        lane_risk_batch_summary.get("min_ttc_adjacent_lane_sec")
                    )
                    phase3_lane_risk_min_ttc_any_lane_sec = _to_float_or_none(
                        lane_risk_batch_summary.get("min_ttc_any_lane_sec")
                    )
                    phase3_lane_risk_ttc_under_3s_same_lane_total = max(
                        0,
                        _to_int(
                            lane_risk_batch_summary.get("ttc_under_3s_same_lane_total"),
                            default=0,
                        ),
                    )
                    phase3_lane_risk_ttc_under_3s_adjacent_lane_total = max(
                        0,
                        _to_int(
                            lane_risk_batch_summary.get("ttc_under_3s_adjacent_lane_total"),
                            default=0,
                        ),
                    )
                    phase3_lane_risk_same_lane_rows_total = max(
                        0,
                        _to_int(
                            lane_risk_batch_summary.get("same_lane_rows_total"),
                            default=0,
                        ),
                    )
                    phase3_lane_risk_adjacent_lane_rows_total = max(
                        0,
                        _to_int(
                            lane_risk_batch_summary.get("adjacent_lane_rows_total"),
                            default=0,
                        ),
                    )
                    phase3_lane_risk_other_lane_rows_total = max(
                        0,
                        _to_int(
                            lane_risk_batch_summary.get("other_lane_rows_total"),
                            default=0,
                        ),
                    )

            manifests.append(
                {
                    "batch_id": str(payload.get("batch_id", "")).strip(),
                    "overall_result": str(payload.get("overall_result", "")).strip(),
                    "trend_result": trend_result,
                    "strict_gate": bool(payload.get("strict_gate", False)),
                    "sds_versions": sorted(set(sds_versions)),
                    "phase3_vehicle_dynamics_enabled": phase3_vehicle_dynamics_enabled,
                    "phase3_vehicle_dynamics_model": phase3_vehicle_dynamics_model,
                    "phase3_vehicle_dynamics_step_count": phase3_vehicle_dynamics_step_count,
                    "phase3_vehicle_dynamics_initial_speed_mps": phase3_vehicle_dynamics_initial_speed_mps,
                    "phase3_vehicle_dynamics_initial_position_m": phase3_vehicle_dynamics_initial_position_m,
                    "phase3_vehicle_dynamics_initial_heading_deg": phase3_vehicle_dynamics_initial_heading_deg,
                    "phase3_vehicle_dynamics_initial_lateral_position_m": (
                        phase3_vehicle_dynamics_initial_lateral_position_m
                    ),
                    "phase3_vehicle_dynamics_initial_lateral_velocity_mps": (
                        phase3_vehicle_dynamics_initial_lateral_velocity_mps
                    ),
                    "phase3_vehicle_dynamics_initial_yaw_rate_rps": phase3_vehicle_dynamics_initial_yaw_rate_rps,
                    "phase3_vehicle_dynamics_final_speed_mps": phase3_vehicle_dynamics_final_speed_mps,
                    "phase3_vehicle_dynamics_final_position_m": phase3_vehicle_dynamics_final_position_m,
                    "phase3_vehicle_dynamics_final_heading_deg": phase3_vehicle_dynamics_final_heading_deg,
                    "phase3_vehicle_dynamics_final_lateral_position_m": (
                        phase3_vehicle_dynamics_final_lateral_position_m
                    ),
                    "phase3_vehicle_dynamics_final_lateral_velocity_mps": (
                        phase3_vehicle_dynamics_final_lateral_velocity_mps
                    ),
                    "phase3_vehicle_dynamics_final_yaw_rate_rps": phase3_vehicle_dynamics_final_yaw_rate_rps,
                    "phase3_vehicle_dynamics_min_heading_deg": phase3_vehicle_dynamics_min_heading_deg,
                    "phase3_vehicle_dynamics_avg_heading_deg": phase3_vehicle_dynamics_avg_heading_deg,
                    "phase3_vehicle_dynamics_max_heading_deg": phase3_vehicle_dynamics_max_heading_deg,
                    "phase3_vehicle_dynamics_min_lateral_position_m": (
                        phase3_vehicle_dynamics_min_lateral_position_m
                    ),
                    "phase3_vehicle_dynamics_avg_lateral_position_m": (
                        phase3_vehicle_dynamics_avg_lateral_position_m
                    ),
                    "phase3_vehicle_dynamics_max_lateral_position_m": (
                        phase3_vehicle_dynamics_max_lateral_position_m
                    ),
                    "phase3_vehicle_dynamics_max_abs_lateral_position_m": (
                        phase3_vehicle_dynamics_max_abs_lateral_position_m
                    ),
                    "phase3_vehicle_dynamics_max_abs_yaw_rate_rps": (
                        phase3_vehicle_dynamics_max_abs_yaw_rate_rps
                    ),
                    "phase3_vehicle_dynamics_max_abs_lateral_velocity_mps": (
                        phase3_vehicle_dynamics_max_abs_lateral_velocity_mps
                    ),
                    "phase3_vehicle_dynamics_max_abs_accel_mps2": phase3_vehicle_dynamics_max_abs_accel_mps2,
                    "phase3_vehicle_dynamics_max_abs_lateral_accel_mps2": (
                        phase3_vehicle_dynamics_max_abs_lateral_accel_mps2
                    ),
                    "phase3_vehicle_dynamics_max_abs_yaw_accel_rps2": (
                        phase3_vehicle_dynamics_max_abs_yaw_accel_rps2
                    ),
                    "phase3_vehicle_dynamics_max_abs_jerk_mps3": phase3_vehicle_dynamics_max_abs_jerk_mps3,
                    "phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3": (
                        phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3
                    ),
                    "phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3": (
                        phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3
                    ),
                    "phase3_vehicle_dynamics_planar_kinematics_enabled": (
                        phase3_vehicle_dynamics_planar_kinematics_enabled
                    ),
                    "phase3_vehicle_dynamics_dynamic_bicycle_enabled": (
                        phase3_vehicle_dynamics_dynamic_bicycle_enabled
                    ),
                    "phase3_vehicle_dynamics_min_road_grade_percent": phase3_vehicle_dynamics_min_road_grade_percent,
                    "phase3_vehicle_dynamics_avg_road_grade_percent": phase3_vehicle_dynamics_avg_road_grade_percent,
                    "phase3_vehicle_dynamics_max_road_grade_percent": phase3_vehicle_dynamics_max_road_grade_percent,
                    "phase3_vehicle_dynamics_max_abs_grade_force_n": phase3_vehicle_dynamics_max_abs_grade_force_n,
                    "phase3_vehicle_control_command_step_count": phase3_vehicle_control_command_step_count,
                    "phase3_vehicle_control_throttle_brake_overlap_step_count": (
                        phase3_vehicle_control_throttle_brake_overlap_step_count
                    ),
                    "phase3_vehicle_control_throttle_brake_overlap_ratio": (
                        phase3_vehicle_control_throttle_brake_overlap_ratio
                    ),
                    "phase3_vehicle_control_max_abs_steering_rate_degps": (
                        phase3_vehicle_control_max_abs_steering_rate_degps
                    ),
                    "phase3_vehicle_control_max_abs_throttle_rate_per_sec": (
                        phase3_vehicle_control_max_abs_throttle_rate_per_sec
                    ),
                    "phase3_vehicle_control_max_abs_brake_rate_per_sec": (
                        phase3_vehicle_control_max_abs_brake_rate_per_sec
                    ),
                    "phase3_vehicle_control_max_throttle_plus_brake": (
                        phase3_vehicle_control_max_throttle_plus_brake
                    ),
                    "phase3_vehicle_speed_tracking_target_step_count": (
                        phase3_vehicle_speed_tracking_target_step_count
                    ),
                    "phase3_vehicle_speed_tracking_error_mps_min": (
                        phase3_vehicle_speed_tracking_error_mps_min
                    ),
                    "phase3_vehicle_speed_tracking_error_mps_avg": (
                        phase3_vehicle_speed_tracking_error_mps_avg
                    ),
                    "phase3_vehicle_speed_tracking_error_mps_max": (
                        phase3_vehicle_speed_tracking_error_mps_max
                    ),
                    "phase3_vehicle_speed_tracking_error_abs_mps_avg": (
                        phase3_vehicle_speed_tracking_error_abs_mps_avg
                    ),
                    "phase3_vehicle_speed_tracking_error_abs_mps_max": (
                        phase3_vehicle_speed_tracking_error_abs_mps_max
                    ),
                    "phase3_core_sim_enabled": phase3_core_sim_enabled,
                    "phase3_core_sim_status": phase3_core_sim_status,
                    "phase3_core_sim_termination_reason": phase3_core_sim_termination_reason,
                    "phase3_core_sim_collision": phase3_core_sim_collision,
                    "phase3_core_sim_timeout": phase3_core_sim_timeout,
                    "phase3_core_sim_min_ttc_same_lane_sec": phase3_core_sim_min_ttc_same_lane_sec,
                    "phase3_core_sim_min_ttc_adjacent_lane_sec": (
                        phase3_core_sim_min_ttc_adjacent_lane_sec
                    ),
                    "phase3_core_sim_min_ttc_any_lane_sec": phase3_core_sim_min_ttc_any_lane_sec,
                    "phase3_core_sim_enable_ego_collision_avoidance": (
                        phase3_core_sim_enable_ego_collision_avoidance
                    ),
                    "phase3_core_sim_avoidance_ttc_threshold_sec": (
                        phase3_core_sim_avoidance_ttc_threshold_sec
                    ),
                    "phase3_core_sim_ego_max_brake_mps2": phase3_core_sim_ego_max_brake_mps2,
                    "phase3_core_sim_tire_friction_coeff": phase3_core_sim_tire_friction_coeff,
                    "phase3_core_sim_surface_friction_scale": phase3_core_sim_surface_friction_scale,
                    "phase3_core_sim_ego_avoidance_brake_event_count": (
                        phase3_core_sim_ego_avoidance_brake_event_count
                    ),
                    "phase3_core_sim_ego_avoidance_applied_brake_mps2_max": (
                        phase3_core_sim_ego_avoidance_applied_brake_mps2_max
                    ),
                    "phase3_core_sim_gate_result": phase3_core_sim_gate_result,
                    "phase3_core_sim_gate_reason_count": phase3_core_sim_gate_reason_count,
                    "phase3_core_sim_gate_reasons": phase3_core_sim_gate_reasons,
                    "phase3_core_sim_gate_require_success": phase3_core_sim_gate_require_success,
                    "phase3_core_sim_gate_min_ttc_same_lane_sec": (
                        phase3_core_sim_gate_min_ttc_same_lane_sec
                    ),
                    "phase3_core_sim_gate_min_ttc_any_lane_sec": (
                        phase3_core_sim_gate_min_ttc_any_lane_sec
                    ),
                    "phase3_core_sim_matrix_enabled": phase3_core_sim_matrix_enabled,
                    "phase3_core_sim_matrix_schema_version": phase3_core_sim_matrix_schema_version,
                    "phase3_core_sim_matrix_case_count": phase3_core_sim_matrix_case_count,
                    "phase3_core_sim_matrix_success_case_count": (
                        phase3_core_sim_matrix_success_case_count
                    ),
                    "phase3_core_sim_matrix_failed_case_count": (
                        phase3_core_sim_matrix_failed_case_count
                    ),
                    "phase3_core_sim_matrix_all_cases_success": (
                        phase3_core_sim_matrix_all_cases_success
                    ),
                    "phase3_core_sim_matrix_collision_case_count": (
                        phase3_core_sim_matrix_collision_case_count
                    ),
                    "phase3_core_sim_matrix_timeout_case_count": (
                        phase3_core_sim_matrix_timeout_case_count
                    ),
                    "phase3_core_sim_matrix_min_ttc_same_lane_sec_min": (
                        phase3_core_sim_matrix_min_ttc_same_lane_sec_min
                    ),
                    "phase3_core_sim_matrix_lowest_ttc_same_lane_run_id": (
                        phase3_core_sim_matrix_lowest_ttc_same_lane_run_id
                    ),
                    "phase3_core_sim_matrix_min_ttc_any_lane_sec_min": (
                        phase3_core_sim_matrix_min_ttc_any_lane_sec_min
                    ),
                    "phase3_core_sim_matrix_lowest_ttc_any_lane_run_id": (
                        phase3_core_sim_matrix_lowest_ttc_any_lane_run_id
                    ),
                    "phase3_core_sim_matrix_status_counts": phase3_core_sim_matrix_status_counts,
                    "phase3_core_sim_matrix_returncode_counts": phase3_core_sim_matrix_returncode_counts,
                    "phase3_dataset_traffic_gate_result": phase3_dataset_traffic_gate_result,
                    "phase3_dataset_traffic_gate_reason_count": phase3_dataset_traffic_gate_reason_count,
                    "phase3_dataset_traffic_gate_reasons": phase3_dataset_traffic_gate_reasons,
                    "phase3_dataset_traffic_gate_min_run_summary_count": (
                        phase3_dataset_traffic_gate_min_run_summary_count
                    ),
                    "phase3_dataset_traffic_gate_min_traffic_profile_count": (
                        phase3_dataset_traffic_gate_min_traffic_profile_count
                    ),
                    "phase3_dataset_traffic_gate_min_actor_pattern_count": (
                        phase3_dataset_traffic_gate_min_actor_pattern_count
                    ),
                    "phase3_dataset_traffic_gate_min_avg_npc_count": (
                        phase3_dataset_traffic_gate_min_avg_npc_count
                    ),
                    "phase3_dataset_traffic_run_summary_count": phase3_dataset_traffic_run_summary_count,
                    "phase3_dataset_traffic_run_status_counts": phase3_dataset_traffic_run_status_counts,
                    "phase3_dataset_traffic_profile_count": phase3_dataset_traffic_profile_count,
                    "phase3_dataset_traffic_profile_ids": phase3_dataset_traffic_profile_ids,
                    "phase3_dataset_traffic_profile_source_count": phase3_dataset_traffic_profile_source_count,
                    "phase3_dataset_traffic_profile_source_ids": phase3_dataset_traffic_profile_source_ids,
                    "phase3_dataset_traffic_actor_pattern_count": phase3_dataset_traffic_actor_pattern_count,
                    "phase3_dataset_traffic_actor_pattern_ids": phase3_dataset_traffic_actor_pattern_ids,
                    "phase3_dataset_traffic_lane_profile_signature_count": (
                        phase3_dataset_traffic_lane_profile_signature_count
                    ),
                    "phase3_dataset_traffic_lane_profile_signatures": (
                        phase3_dataset_traffic_lane_profile_signatures
                    ),
                    "phase3_dataset_traffic_npc_count_sample_count": phase3_dataset_traffic_npc_count_sample_count,
                    "phase3_dataset_traffic_npc_count_min": phase3_dataset_traffic_npc_count_min,
                    "phase3_dataset_traffic_npc_count_avg": phase3_dataset_traffic_npc_count_avg,
                    "phase3_dataset_traffic_npc_count_max": phase3_dataset_traffic_npc_count_max,
                    "phase3_dataset_traffic_npc_initial_gap_m_sample_count": (
                        phase3_dataset_traffic_npc_initial_gap_m_sample_count
                    ),
                    "phase3_dataset_traffic_npc_initial_gap_m_min": (
                        phase3_dataset_traffic_npc_initial_gap_m_min
                    ),
                    "phase3_dataset_traffic_npc_initial_gap_m_avg": (
                        phase3_dataset_traffic_npc_initial_gap_m_avg
                    ),
                    "phase3_dataset_traffic_npc_initial_gap_m_max": (
                        phase3_dataset_traffic_npc_initial_gap_m_max
                    ),
                    "phase3_dataset_traffic_npc_gap_step_m_sample_count": (
                        phase3_dataset_traffic_npc_gap_step_m_sample_count
                    ),
                    "phase3_dataset_traffic_npc_gap_step_m_min": phase3_dataset_traffic_npc_gap_step_m_min,
                    "phase3_dataset_traffic_npc_gap_step_m_avg": phase3_dataset_traffic_npc_gap_step_m_avg,
                    "phase3_dataset_traffic_npc_gap_step_m_max": phase3_dataset_traffic_npc_gap_step_m_max,
                    "phase3_dataset_traffic_npc_speed_scale_sample_count": (
                        phase3_dataset_traffic_npc_speed_scale_sample_count
                    ),
                    "phase3_dataset_traffic_npc_speed_scale_min": (
                        phase3_dataset_traffic_npc_speed_scale_min
                    ),
                    "phase3_dataset_traffic_npc_speed_scale_avg": (
                        phase3_dataset_traffic_npc_speed_scale_avg
                    ),
                    "phase3_dataset_traffic_npc_speed_scale_max": (
                        phase3_dataset_traffic_npc_speed_scale_max
                    ),
                    "phase3_dataset_traffic_npc_speed_jitter_mps_sample_count": (
                        phase3_dataset_traffic_npc_speed_jitter_mps_sample_count
                    ),
                    "phase3_dataset_traffic_npc_speed_jitter_mps_min": (
                        phase3_dataset_traffic_npc_speed_jitter_mps_min
                    ),
                    "phase3_dataset_traffic_npc_speed_jitter_mps_avg": (
                        phase3_dataset_traffic_npc_speed_jitter_mps_avg
                    ),
                    "phase3_dataset_traffic_npc_speed_jitter_mps_max": (
                        phase3_dataset_traffic_npc_speed_jitter_mps_max
                    ),
                    "phase3_dataset_traffic_lane_index_unique_count": (
                        phase3_dataset_traffic_lane_index_unique_count
                    ),
                    "phase3_dataset_traffic_lane_indices": phase3_dataset_traffic_lane_indices,
                    "phase3_dataset_manifest_counts_rows": phase3_dataset_manifest_counts_rows,
                    "phase3_dataset_manifest_run_summary_count": phase3_dataset_manifest_run_summary_count,
                    "phase3_dataset_manifest_release_summary_count": (
                        phase3_dataset_manifest_release_summary_count
                    ),
                    "phase3_dataset_manifest_versions": phase3_dataset_manifest_versions,
                    "phase3_lane_risk_summary_run_count": phase3_lane_risk_summary_run_count,
                    "phase3_lane_risk_min_ttc_same_lane_sec": phase3_lane_risk_min_ttc_same_lane_sec,
                    "phase3_lane_risk_min_ttc_adjacent_lane_sec": phase3_lane_risk_min_ttc_adjacent_lane_sec,
                    "phase3_lane_risk_min_ttc_any_lane_sec": phase3_lane_risk_min_ttc_any_lane_sec,
                    "phase3_lane_risk_gate_result": phase3_lane_risk_gate_result,
                    "phase3_lane_risk_gate_reason_count": phase3_lane_risk_gate_reason_count,
                    "phase3_lane_risk_gate_reasons": phase3_lane_risk_gate_reasons,
                    "phase3_lane_risk_gate_min_ttc_same_lane_sec": (
                        phase3_lane_risk_gate_min_ttc_same_lane_sec
                    ),
                    "phase3_lane_risk_gate_min_ttc_adjacent_lane_sec": (
                        phase3_lane_risk_gate_min_ttc_adjacent_lane_sec
                    ),
                    "phase3_lane_risk_gate_min_ttc_any_lane_sec": (
                        phase3_lane_risk_gate_min_ttc_any_lane_sec
                    ),
                    "phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total": (
                        phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total
                    ),
                    "phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total": (
                        phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total
                    ),
                    "phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total": (
                        phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total
                    ),
                    "phase3_lane_risk_ttc_under_3s_same_lane_total": (
                        phase3_lane_risk_ttc_under_3s_same_lane_total
                    ),
                    "phase3_lane_risk_ttc_under_3s_adjacent_lane_total": (
                        phase3_lane_risk_ttc_under_3s_adjacent_lane_total
                    ),
                    "phase3_lane_risk_same_lane_rows_total": phase3_lane_risk_same_lane_rows_total,
                    "phase3_lane_risk_adjacent_lane_rows_total": phase3_lane_risk_adjacent_lane_rows_total,
                    "phase3_lane_risk_other_lane_rows_total": phase3_lane_risk_other_lane_rows_total,
                    "phase3_object_sim_checked": phase3_object_sim_checked,
                    "phase3_object_sim_status": phase3_object_sim_status,
                    "phase3_sim_runtime_scenario_contract_checked": (
                        phase3_sim_runtime_scenario_contract_checked
                    ),
                    "phase3_sim_runtime_scenario_contract_status": (
                        phase3_sim_runtime_scenario_contract_status
                    ),
                    "phase3_sim_runtime_scenario_contract_runtime_ready": (
                        phase3_sim_runtime_scenario_contract_runtime_ready
                    ),
                    "phase3_sim_runtime_scene_result_checked": phase3_sim_runtime_scene_result_checked,
                    "phase3_sim_runtime_scene_result_status": phase3_sim_runtime_scene_result_status,
                    "phase3_sim_runtime_scene_result_runtime_ready": (
                        phase3_sim_runtime_scene_result_runtime_ready
                    ),
                    "phase2_log_replay_checked": phase2_log_replay_checked,
                    "phase2_log_replay_manifest_present": phase2_log_replay_manifest_present,
                    "phase2_log_replay_summary_present": phase2_log_replay_summary_present,
                    "phase2_log_replay_status": phase2_log_replay_status,
                    "phase2_log_replay_run_source": phase2_log_replay_run_source,
                    "phase2_log_replay_run_status": phase2_log_replay_run_status,
                    "phase2_log_replay_log_id": phase2_log_replay_log_id,
                    "phase2_log_replay_map_id": phase2_log_replay_map_id,
                    "phase2_map_routing_checked": phase2_map_routing_checked,
                    "phase2_map_routing_status": phase2_map_routing_status,
                    "phase2_map_routing_error_count": phase2_map_routing_error_count,
                    "phase2_map_routing_warning_count": phase2_map_routing_warning_count,
                    "phase2_map_routing_semantic_warning_count": phase2_map_routing_semantic_warning_count,
                    "phase2_map_routing_unreachable_lane_count": phase2_map_routing_unreachable_lane_count,
                    "phase2_map_routing_non_reciprocal_link_count": phase2_map_routing_non_reciprocal_link_count,
                    "phase2_map_routing_continuity_gap_warning_count": (
                        phase2_map_routing_continuity_gap_warning_count
                    ),
                    "phase2_map_route_checked": phase2_map_route_checked,
                    "phase2_map_route_status": phase2_map_route_status,
                    "phase2_map_route_lane_count": phase2_map_route_lane_count,
                    "phase2_map_route_hop_count": phase2_map_route_hop_count,
                    "phase2_map_route_total_length_m": phase2_map_route_total_length_m,
                    "phase2_map_route_segment_count": phase2_map_route_segment_count,
                    "phase2_map_route_via_lane_count": phase2_map_route_via_lane_count,
                    "phase2_map_route_entry_lane_id": phase2_map_route_entry_lane_id,
                    "phase2_map_route_exit_lane_id": phase2_map_route_exit_lane_id,
                    "phase2_sensor_checked": phase2_sensor_checked,
                    "phase2_sensor_fidelity_tier": phase2_sensor_fidelity_tier,
                    "phase2_sensor_fidelity_tier_score": phase2_sensor_fidelity_tier_score,
                    "phase2_sensor_frame_count": phase2_sensor_frame_count,
                    "phase2_sensor_modality_counts": {
                        key: phase2_sensor_modality_counts[key]
                        for key in sorted(phase2_sensor_modality_counts.keys())
                    },
                    "phase2_sensor_camera_frame_count": phase2_sensor_camera_frame_count,
                    "phase2_sensor_camera_noise_stddev_px_avg": phase2_sensor_camera_noise_stddev_px_avg,
                    "phase2_sensor_camera_dynamic_range_stops_avg": phase2_sensor_camera_dynamic_range_stops_avg,
                    "phase2_sensor_camera_visibility_score_avg": phase2_sensor_camera_visibility_score_avg,
                    "phase2_sensor_camera_motion_blur_level_avg": phase2_sensor_camera_motion_blur_level_avg,
                    "phase2_sensor_camera_snr_db_avg": phase2_sensor_camera_snr_db_avg,
                    "phase2_sensor_camera_exposure_time_ms_avg": phase2_sensor_camera_exposure_time_ms_avg,
                    "phase2_sensor_camera_signal_saturation_ratio_avg": (
                        phase2_sensor_camera_signal_saturation_ratio_avg
                    ),
                    "phase2_sensor_camera_rolling_shutter_total_delay_ms_avg": (
                        phase2_sensor_camera_rolling_shutter_total_delay_ms_avg
                    ),
                    "phase2_sensor_camera_normalized_total_noise_avg": (
                        phase2_sensor_camera_normalized_total_noise_avg
                    ),
                    "phase2_sensor_camera_distortion_edge_shift_px_avg": (
                        phase2_sensor_camera_distortion_edge_shift_px_avg
                    ),
                    "phase2_sensor_camera_principal_point_offset_norm_avg": (
                        phase2_sensor_camera_principal_point_offset_norm_avg
                    ),
                    "phase2_sensor_camera_effective_focal_length_px_avg": (
                        phase2_sensor_camera_effective_focal_length_px_avg
                    ),
                    "phase2_sensor_camera_projection_mode_counts": {
                        key: phase2_sensor_camera_projection_mode_counts[key]
                        for key in sorted(phase2_sensor_camera_projection_mode_counts.keys())
                    },
                    "phase2_sensor_camera_gain_db_avg": phase2_sensor_camera_gain_db_avg,
                    "phase2_sensor_camera_gamma_avg": phase2_sensor_camera_gamma_avg,
                    "phase2_sensor_camera_white_balance_kelvin_avg": (
                        phase2_sensor_camera_white_balance_kelvin_avg
                    ),
                    "phase2_sensor_camera_vignetting_edge_darkening_avg": (
                        phase2_sensor_camera_vignetting_edge_darkening_avg
                    ),
                    "phase2_sensor_camera_bloom_halo_strength_avg": (
                        phase2_sensor_camera_bloom_halo_strength_avg
                    ),
                    "phase2_sensor_camera_chromatic_aberration_shift_px_avg": (
                        phase2_sensor_camera_chromatic_aberration_shift_px_avg
                    ),
                    "phase2_sensor_camera_tonemapper_disabled_frame_count": (
                        phase2_sensor_camera_tonemapper_disabled_frame_count
                    ),
                    "phase2_sensor_camera_bloom_level_counts": {
                        key: phase2_sensor_camera_bloom_level_counts[key]
                        for key in sorted(phase2_sensor_camera_bloom_level_counts.keys())
                    },
                    "phase2_sensor_camera_depth_enabled_frame_count": (
                        phase2_sensor_camera_depth_enabled_frame_count
                    ),
                    "phase2_sensor_camera_depth_min_m_avg": phase2_sensor_camera_depth_min_m_avg,
                    "phase2_sensor_camera_depth_max_m_avg": phase2_sensor_camera_depth_max_m_avg,
                    "phase2_sensor_camera_depth_bit_depth_avg": (
                        phase2_sensor_camera_depth_bit_depth_avg
                    ),
                    "phase2_sensor_camera_depth_mode_counts": {
                        key: phase2_sensor_camera_depth_mode_counts[key]
                        for key in sorted(phase2_sensor_camera_depth_mode_counts.keys())
                    },
                    "phase2_sensor_camera_optical_flow_enabled_frame_count": (
                        phase2_sensor_camera_optical_flow_enabled_frame_count
                    ),
                    "phase2_sensor_camera_optical_flow_magnitude_px_avg": (
                        phase2_sensor_camera_optical_flow_magnitude_px_avg
                    ),
                    "phase2_sensor_camera_optical_flow_velocity_direction_counts": {
                        key: phase2_sensor_camera_optical_flow_velocity_direction_counts[key]
                        for key in sorted(
                            phase2_sensor_camera_optical_flow_velocity_direction_counts.keys()
                        )
                    },
                    "phase2_sensor_camera_optical_flow_y_axis_direction_counts": {
                        key: phase2_sensor_camera_optical_flow_y_axis_direction_counts[key]
                        for key in sorted(
                            phase2_sensor_camera_optical_flow_y_axis_direction_counts.keys()
                        )
                    },
                    "phase2_sensor_lidar_frame_count": phase2_sensor_lidar_frame_count,
                    "phase2_sensor_lidar_point_count_total": phase2_sensor_lidar_point_count_total,
                    "phase2_sensor_lidar_point_count_avg": phase2_sensor_lidar_point_count_avg,
                    "phase2_sensor_lidar_returns_per_laser_avg": phase2_sensor_lidar_returns_per_laser_avg,
                    "phase2_sensor_lidar_detection_ratio_avg": phase2_sensor_lidar_detection_ratio_avg,
                    "phase2_sensor_lidar_effective_max_range_m_avg": (
                        phase2_sensor_lidar_effective_max_range_m_avg
                    ),
                    "phase2_sensor_radar_frame_count": phase2_sensor_radar_frame_count,
                    "phase2_sensor_radar_target_count_total": phase2_sensor_radar_target_count_total,
                    "phase2_sensor_radar_ghost_target_count_total": phase2_sensor_radar_ghost_target_count_total,
                    "phase2_sensor_radar_false_positive_count_total": phase2_sensor_radar_false_positive_count_total,
                    "phase2_sensor_radar_false_positive_count_avg": phase2_sensor_radar_false_positive_count_avg,
                    "phase2_sensor_radar_false_positive_rate_avg": phase2_sensor_radar_false_positive_rate_avg,
                    "phase2_sensor_radar_ghost_target_count_avg": phase2_sensor_radar_ghost_target_count_avg,
                    "phase2_sensor_radar_clutter_index_avg": phase2_sensor_radar_clutter_index_avg,
                    "phase2_sensor_sweep_checked": phase2_sensor_sweep_checked,
                    "phase2_sensor_sweep_fidelity_tier": phase2_sensor_sweep_fidelity_tier,
                    "phase2_sensor_sweep_candidate_count": phase2_sensor_sweep_candidate_count,
                    "phase2_sensor_sweep_best_rig_id": phase2_sensor_sweep_best_rig_id,
                    "phase2_sensor_sweep_best_heuristic_score": phase2_sensor_sweep_best_heuristic_score,
                    "phase4_reference_primary_total_coverage_ratio": phase4_reference_primary_total_coverage_ratio,
                    "phase4_reference_primary_module_coverage": phase4_reference_primary_module_coverage,
                    "phase4_reference_secondary_total_coverage_ratio": phase4_reference_secondary_total_coverage_ratio,
                    "phase4_reference_secondary_module_count": phase4_reference_secondary_module_count,
                    "phase4_reference_secondary_module_coverage": phase4_reference_secondary_module_coverage,
                    "manifest_path": str(resolved),
                }
            )

    manifests.sort(key=lambda item: item["manifest_path"])
    return manifests


def discover_runtime_evidence_artifacts(scan_roots: list[Path], release_prefix: str) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*runtime*evidence*.json"):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            records_raw = payload.get("runtime_evidence_records")
            if not isinstance(records_raw, list):
                continue
            payload_release_prefix = str(payload.get("release_prefix", "")).strip()
            if payload_release_prefix:
                if not _matches_release_prefix(release_id=payload_release_prefix, prefix=release_prefix):
                    continue
            elif not str(resolved.name).startswith(f"{release_prefix}_"):
                continue

            records: list[dict[str, Any]] = []
            for raw_record in records_raw:
                if isinstance(raw_record, dict):
                    records.append(raw_record)
            try:
                profile_count = max(0, int(payload.get("profile_count", 0)))
            except (TypeError, ValueError):
                profile_count = 0
            try:
                failure_count = max(0, int(payload.get("failure_count", 0)))
            except (TypeError, ValueError):
                failure_count = 0
            try:
                runtime_evidence_count = max(0, int(payload.get("runtime_evidence_count", len(records))))
            except (TypeError, ValueError):
                runtime_evidence_count = len(records)

            artifacts.append(
                {
                    "artifact_path": str(resolved),
                    "release_prefix": payload_release_prefix,
                    "sim_runtime": str(payload.get("sim_runtime", "")).strip().lower() or "none",
                    "sim_runtime_assert_artifacts": bool(payload.get("sim_runtime_assert_artifacts", False)),
                    "sim_runtime_probe_enable": bool(payload.get("sim_runtime_probe_enable", False)),
                    "sim_runtime_probe_execute": bool(payload.get("sim_runtime_probe_execute", False)),
                    "sim_runtime_probe_require_availability": bool(
                        payload.get("sim_runtime_probe_require_availability", False)
                    ),
                    "sim_runtime_probe_flag": str(payload.get("sim_runtime_probe_flag", "")).strip(),
                    "sim_runtime_probe_args_shlex": str(payload.get("sim_runtime_probe_args_shlex", "")).strip(),
                    "sim_runtime_scenario_contract_enable": bool(
                        payload.get("sim_runtime_scenario_contract_enable", False)
                    ),
                    "sim_runtime_scenario_contract_require_runtime_ready": bool(
                        payload.get("sim_runtime_scenario_contract_require_runtime_ready", False)
                    ),
                    "sim_runtime_scene_result_enable": bool(
                        payload.get("sim_runtime_scene_result_enable", False)
                    ),
                    "sim_runtime_scene_result_require_runtime_ready": bool(
                        payload.get("sim_runtime_scene_result_require_runtime_ready", False)
                    ),
                    "sim_runtime_interop_contract_enable": bool(
                        payload.get("sim_runtime_interop_contract_enable", False)
                    ),
                    "sim_runtime_interop_contract_require_runtime_ready": bool(
                        payload.get("sim_runtime_interop_contract_require_runtime_ready", False)
                    ),
                    "profile_count": profile_count,
                    "failure_count": failure_count,
                    "runtime_evidence_count": runtime_evidence_count,
                    "runtime_evidence_records": records,
                }
            )

    artifacts.sort(key=lambda item: str(item.get("artifact_path", "")))
    return artifacts


def discover_runtime_lane_execution_artifacts(scan_roots: list[Path], release_prefix: str) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*runtime*lane*execution*summary*.json"):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            schema_version = str(payload.get("schema_version", "")).strip()
            if schema_version and schema_version != "runtime_lane_execution_summary_v0":
                continue
            rows_raw = payload.get("runtime_rows")
            if not isinstance(rows_raw, list):
                continue
            runtime_evidence_missing_runtime_counts_raw = payload.get(
                "runtime_evidence_missing_runtime_counts",
                None,
            )
            runtime_evidence_missing_runtime_counts: dict[str, int] = {}
            runtime_evidence_missing_runtime_counts_provided = isinstance(
                runtime_evidence_missing_runtime_counts_raw,
                dict,
            )
            if isinstance(runtime_evidence_missing_runtime_counts_raw, dict):
                for key, value in runtime_evidence_missing_runtime_counts_raw.items():
                    runtime_name = str(key).strip().lower()
                    if not runtime_name:
                        continue
                    try:
                        parsed_count = int(value)
                    except (TypeError, ValueError):
                        continue
                    if parsed_count <= 0:
                        continue
                    runtime_evidence_missing_runtime_counts[runtime_name] = (
                        runtime_evidence_missing_runtime_counts.get(runtime_name, 0) + parsed_count
                    )
            payload_release_prefix = str(payload.get("release_prefix", "")).strip()
            if payload_release_prefix:
                if not _matches_release_prefix(release_id=payload_release_prefix, prefix=release_prefix):
                    continue
            elif not str(resolved.name).startswith(f"{release_prefix}_"):
                continue

            rows: list[dict[str, Any]] = []
            for raw_row in rows_raw:
                if not isinstance(raw_row, dict):
                    continue
                runtime_evidence_path = str(raw_row.get("runtime_evidence_path", "")).strip()
                runtime_evidence_exists_raw = raw_row.get("runtime_evidence_exists", None)
                runtime_evidence_exists: bool | None
                if isinstance(runtime_evidence_exists_raw, bool):
                    runtime_evidence_exists = runtime_evidence_exists_raw
                else:
                    runtime_evidence_exists = None
                rows.append(
                    {
                        "runtime": str(raw_row.get("runtime", "")).strip().lower() or "unknown",
                        "release_id": str(raw_row.get("release_id", "")).strip() or "release_unknown",
                        "result": str(raw_row.get("result", "")).strip().lower() or "unknown",
                        "runtime_evidence_path": runtime_evidence_path,
                        "runtime_evidence_exists": runtime_evidence_exists,
                        "runtime_failure_reason": str(raw_row.get("runtime_failure_reason", "")).strip().lower(),
                    }
                )

            artifacts.append(
                {
                    "artifact_path": str(resolved),
                    "release_prefix": payload_release_prefix,
                    "lane_input": str(payload.get("lane_input", "")).strip().lower(),
                    "lane_resolved": str(payload.get("lane_resolved", "")).strip().lower(),
                    "runner_platform": str(payload.get("runner_platform", "")).strip(),
                    "sim_runtime_input": str(payload.get("sim_runtime_input", "")).strip().lower(),
                    "runtime_asset_profile": str(payload.get("runtime_asset_profile", "")).strip().lower(),
                    "runtime_asset_archive_sha256_mode": str(
                        payload.get("runtime_asset_archive_sha256_mode", "")
                    )
                    .strip()
                    .lower(),
                    "runtime_exec_lane_warn_min_rows": _to_non_negative_int_or_none(
                        payload.get("runtime_exec_lane_warn_min_rows", None)
                    ),
                    "runtime_exec_lane_hold_min_rows": _to_non_negative_int_or_none(
                        payload.get("runtime_exec_lane_hold_min_rows", None)
                    ),
                    "runtime_evidence_compare_warn_min_artifacts_with_diffs": _to_non_negative_int_or_none(
                        payload.get("runtime_evidence_compare_warn_min_artifacts_with_diffs", None)
                    ),
                    "runtime_evidence_compare_hold_min_artifacts_with_diffs": _to_non_negative_int_or_none(
                        payload.get("runtime_evidence_compare_hold_min_artifacts_with_diffs", None)
                    ),
                    "phase2_sensor_fidelity_score_avg_warn_min": _to_non_negative_float_or_none(
                        payload.get("phase2_sensor_fidelity_score_avg_warn_min", None)
                    ),
                    "phase2_sensor_fidelity_score_avg_hold_min": _to_non_negative_float_or_none(
                        payload.get("phase2_sensor_fidelity_score_avg_hold_min", None)
                    ),
                    "phase2_sensor_frame_count_avg_warn_min": _to_non_negative_float_or_none(
                        payload.get("phase2_sensor_frame_count_avg_warn_min", None)
                    ),
                    "phase2_sensor_frame_count_avg_hold_min": _to_non_negative_float_or_none(
                        payload.get("phase2_sensor_frame_count_avg_hold_min", None)
                    ),
                    "dry_run": str(payload.get("dry_run", "")).strip(),
                    "continue_on_runtime_failure": str(payload.get("continue_on_runtime_failure", "")).strip(),
                    "runtime_evidence_missing_runtime_counts": runtime_evidence_missing_runtime_counts,
                    "runtime_evidence_missing_runtime_counts_provided": runtime_evidence_missing_runtime_counts_provided,
                    "runtime_rows": rows,
                }
            )

    artifacts.sort(key=lambda item: str(item.get("artifact_path", "")))
    return artifacts


def discover_runtime_evidence_compare_artifacts(scan_roots: list[Path], release_prefix: str) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*runtime*evidence*compare*.json"):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            schema_version = str(payload.get("schema_version", "")).strip()
            if schema_version and schema_version != "runtime_evidence_compare_v0":
                continue

            left = payload.get("left")
            right = payload.get("right")
            diff = payload.get("diff")
            if not isinstance(left, dict) or not isinstance(right, dict) or not isinstance(diff, dict):
                continue

            compare_release_prefixes: set[str] = set()
            for side in (left, right):
                summary = side.get("summary")
                if not isinstance(summary, dict):
                    continue
                side_release_prefix = str(summary.get("release_prefix", "")).strip()
                if side_release_prefix:
                    compare_release_prefixes.add(side_release_prefix)

            if compare_release_prefixes:
                if not any(
                    _matches_release_prefix(release_id=side_release_prefix, prefix=release_prefix)
                    for side_release_prefix in compare_release_prefixes
                ):
                    continue
            elif not str(resolved.name).startswith(f"{release_prefix}_"):
                continue

            top_level_mismatches = diff.get("top_level_mismatches")
            status_count_diffs = diff.get("status_count_diffs")
            runtime_count_diffs = diff.get("runtime_count_diffs")
            interop_import_status_count_diffs = diff.get("interop_import_status_count_diffs")
            interop_import_manifest_consistency_diffs = diff.get("interop_import_manifest_consistency_diffs")
            interop_import_manifest_mode_count_diffs = diff.get("interop_import_manifest_mode_count_diffs")
            interop_import_export_mode_count_diffs = diff.get("interop_import_export_mode_count_diffs")
            interop_import_require_manifest_input_count_diffs = diff.get(
                "interop_import_require_manifest_input_count_diffs"
            )
            interop_import_require_export_input_count_diffs = diff.get(
                "interop_import_require_export_input_count_diffs"
            )
            interop_import_profile_diffs = diff.get("interop_import_profile_diffs")
            profile_presence = diff.get("profile_presence")
            profile_diffs = diff.get("profile_diffs")

            if not isinstance(profile_presence, dict):
                profile_presence = {}
            left_only_raw = profile_presence.get("left_only", [])
            right_only_raw = profile_presence.get("right_only", [])
            if not isinstance(left_only_raw, list):
                left_only_raw = []
            if not isinstance(right_only_raw, list):
                right_only_raw = []
            left_only_ids = [str(item).strip() for item in left_only_raw if str(item).strip()]
            right_only_ids = [str(item).strip() for item in right_only_raw if str(item).strip()]
            try:
                shared_profile_count = max(0, int(profile_presence.get("shared_count", 0)))
            except (TypeError, ValueError):
                shared_profile_count = 0

            top_level_mismatches_count = len(top_level_mismatches) if isinstance(top_level_mismatches, dict) else 0
            status_count_diffs_count = len(status_count_diffs) if isinstance(status_count_diffs, dict) else 0
            runtime_count_diffs_count = len(runtime_count_diffs) if isinstance(runtime_count_diffs, dict) else 0
            interop_import_status_count_diffs_count = (
                len(interop_import_status_count_diffs) if isinstance(interop_import_status_count_diffs, dict) else 0
            )
            interop_import_manifest_consistency_diffs_count = (
                len(interop_import_manifest_consistency_diffs)
                if isinstance(interop_import_manifest_consistency_diffs, dict)
                else 0
            )
            interop_import_manifest_mode_count_diffs_count = (
                len(interop_import_manifest_mode_count_diffs)
                if isinstance(interop_import_manifest_mode_count_diffs, dict)
                else 0
            )
            interop_import_export_mode_count_diffs_count = (
                len(interop_import_export_mode_count_diffs)
                if isinstance(interop_import_export_mode_count_diffs, dict)
                else 0
            )
            interop_import_require_manifest_input_count_diffs_count = (
                len(interop_import_require_manifest_input_count_diffs)
                if isinstance(interop_import_require_manifest_input_count_diffs, dict)
                else 0
            )
            interop_import_require_export_input_count_diffs_count = (
                len(interop_import_require_export_input_count_diffs)
                if isinstance(interop_import_require_export_input_count_diffs, dict)
                else 0
            )
            interop_import_profile_diff_count = (
                len(interop_import_profile_diffs) if isinstance(interop_import_profile_diffs, list) else 0
            )
            profile_diff_count = len(profile_diffs) if isinstance(profile_diffs, list) else 0
            interop_import_profile_diff_records: list[dict[str, Any]] = []
            interop_import_profile_diff_numeric_delta_totals: dict[str, float] = {}
            interop_import_profile_diff_numeric_delta_abs_totals: dict[str, float] = {}
            interop_import_profile_diff_numeric_delta_records: list[dict[str, Any]] = []
            if isinstance(interop_import_profile_diffs, list):
                for row in interop_import_profile_diffs:
                    if not isinstance(row, dict):
                        continue
                    profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
                    field_mismatches_raw = row.get("field_mismatches", {})
                    numeric_deltas_raw = row.get("numeric_deltas", {})
                    field_keys = (
                        sorted(
                            str(key).strip()
                            for key in field_mismatches_raw.keys()
                            if str(key).strip()
                        )
                        if isinstance(field_mismatches_raw, dict)
                        else []
                    )
                    numeric_keys = (
                        sorted(
                            str(key).strip()
                            for key in numeric_deltas_raw.keys()
                            if str(key).strip()
                        )
                        if isinstance(numeric_deltas_raw, dict)
                        else []
                    )
                    if not field_keys and not numeric_keys:
                        continue
                    interop_import_profile_diff_records.append(
                        {
                            "profile_id": profile_id,
                            "field_keys": field_keys,
                            "numeric_keys": numeric_keys,
                        }
                    )
                    if isinstance(numeric_deltas_raw, dict):
                        for numeric_key, delta_obj in numeric_deltas_raw.items():
                            numeric_key_text = str(numeric_key).strip()
                            if not numeric_key_text:
                                continue
                            if not isinstance(delta_obj, dict):
                                continue
                            delta_raw = delta_obj.get("delta")
                            if delta_raw is None or isinstance(delta_raw, bool):
                                continue
                            try:
                                delta_value = float(delta_raw)
                            except (TypeError, ValueError):
                                continue
                            interop_import_profile_diff_numeric_delta_totals[numeric_key_text] = (
                                interop_import_profile_diff_numeric_delta_totals.get(numeric_key_text, 0.0)
                                + delta_value
                            )
                            interop_import_profile_diff_numeric_delta_abs_totals[numeric_key_text] = (
                                interop_import_profile_diff_numeric_delta_abs_totals.get(numeric_key_text, 0.0)
                                + abs(delta_value)
                            )
                            interop_import_profile_diff_numeric_delta_records.append(
                                {
                                    "profile_id": profile_id,
                                    "numeric_key": numeric_key_text,
                                    "delta": float(round(delta_value, 6)),
                                    "delta_abs": float(round(abs(delta_value), 6)),
                                }
                            )
            interop_import_profile_diff_records.sort(
                key=lambda row: (
                    str(row.get("profile_id", "")),
                    ",".join(str(item) for item in row.get("field_keys", [])),
                    ",".join(str(item) for item in row.get("numeric_keys", [])),
                )
            )
            interop_import_profile_diff_numeric_delta_records.sort(
                key=lambda row: (
                    -float(row.get("delta_abs", 0.0)),
                    str(row.get("numeric_key", "")),
                    str(row.get("profile_id", "")),
                    float(row.get("delta", 0.0)),
                )
            )

            artifacts.append(
                {
                    "artifact_path": str(resolved),
                    "schema_version": schema_version or "runtime_evidence_compare_v0",
                    "left_label": str(left.get("label", "")).strip() or "left",
                    "right_label": str(right.get("label", "")).strip() or "right",
                    "left_path": str(left.get("path", "")).strip(),
                    "right_path": str(right.get("path", "")).strip(),
                    "release_prefixes": sorted(compare_release_prefixes),
                    "top_level_mismatches_count": top_level_mismatches_count,
                    "status_count_diffs_count": status_count_diffs_count,
                    "runtime_count_diffs_count": runtime_count_diffs_count,
                    "interop_import_status_count_diffs_count": interop_import_status_count_diffs_count,
                    "interop_import_manifest_consistency_diffs_count": interop_import_manifest_consistency_diffs_count,
                    "interop_import_manifest_mode_count_diffs_count": interop_import_manifest_mode_count_diffs_count,
                    "interop_import_export_mode_count_diffs_count": interop_import_export_mode_count_diffs_count,
                    "interop_import_require_manifest_input_count_diffs_count": (
                        interop_import_require_manifest_input_count_diffs_count
                    ),
                    "interop_import_require_export_input_count_diffs_count": (
                        interop_import_require_export_input_count_diffs_count
                    ),
                    "interop_import_profile_diff_count": interop_import_profile_diff_count,
                    "interop_import_profile_diff_records": interop_import_profile_diff_records,
                    "interop_import_profile_diff_numeric_delta_totals": {
                        key: float(round(interop_import_profile_diff_numeric_delta_totals[key], 6))
                        for key in sorted(interop_import_profile_diff_numeric_delta_totals.keys())
                    },
                    "interop_import_profile_diff_numeric_delta_abs_totals": {
                        key: float(round(interop_import_profile_diff_numeric_delta_abs_totals[key], 6))
                        for key in sorted(interop_import_profile_diff_numeric_delta_abs_totals.keys())
                    },
                    "interop_import_profile_diff_numeric_delta_records": interop_import_profile_diff_numeric_delta_records,
                    "profile_left_only_count": len(left_only_ids),
                    "profile_right_only_count": len(right_only_ids),
                    "shared_profile_count": shared_profile_count,
                    "profile_diff_count": profile_diff_count,
                    "has_diffs": bool(
                        top_level_mismatches_count > 0
                        or status_count_diffs_count > 0
                        or runtime_count_diffs_count > 0
                        or interop_import_status_count_diffs_count > 0
                        or interop_import_manifest_consistency_diffs_count > 0
                        or interop_import_manifest_mode_count_diffs_count > 0
                        or interop_import_export_mode_count_diffs_count > 0
                        or interop_import_require_manifest_input_count_diffs_count > 0
                        or interop_import_require_export_input_count_diffs_count > 0
                        or interop_import_profile_diff_count > 0
                        or profile_diff_count > 0
                        or len(left_only_ids) > 0
                        or len(right_only_ids) > 0
                    ),
                }
            )

    artifacts.sort(key=lambda item: str(item.get("artifact_path", "")))
    return artifacts


def discover_runtime_native_summary_compare_artifacts(
    scan_roots: list[Path],
    release_prefix: str,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*runtime*native*summary*compare*.json"):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                payload = json.loads(resolved.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            schema_version = str(payload.get("schema_version", "")).strip()
            if schema_version and schema_version != "runtime_native_summary_compare_v0":
                continue

            left_release_prefix = str(payload.get("left_release_prefix", "")).strip()
            right_release_prefix = str(payload.get("right_release_prefix", "")).strip()
            compare_release_prefixes = [
                value for value in [left_release_prefix, right_release_prefix] if value
            ]
            if compare_release_prefixes:
                if not any(
                    _matches_release_prefix(release_id=value, prefix=release_prefix)
                    for value in compare_release_prefixes
                ):
                    continue
            elif not str(resolved.name).startswith(f"{release_prefix}_"):
                continue

            summary_raw = payload.get("summary", {})
            summary = summary_raw if isinstance(summary_raw, dict) else {}
            try:
                version_count = max(0, int(summary.get("version_count", 0)))
            except (TypeError, ValueError):
                version_count = 0
            try:
                comparison_count = max(0, int(summary.get("comparison_count", 0)))
            except (TypeError, ValueError):
                comparison_count = 0
            try:
                versions_with_diffs_count = max(0, int(summary.get("versions_with_diffs_count", 0)))
            except (TypeError, ValueError):
                versions_with_diffs_count = 0

            versions_with_diffs_raw = summary.get("versions_with_diffs", [])
            versions_with_diffs: list[str] = []
            if isinstance(versions_with_diffs_raw, list):
                versions_with_diffs = [
                    str(item).strip() for item in versions_with_diffs_raw if str(item).strip()
                ]

            field_diff_counts_raw = summary.get("field_diff_counts", {})
            field_diff_counts: dict[str, int] = {}
            if isinstance(field_diff_counts_raw, dict):
                for raw_key, raw_value in field_diff_counts_raw.items():
                    key = str(raw_key).strip()
                    if not key:
                        continue
                    try:
                        parsed_value = int(raw_value)
                    except (TypeError, ValueError):
                        continue
                    if parsed_value > 0:
                        field_diff_counts[key] = parsed_value

            artifacts.append(
                {
                    "artifact_path": str(resolved),
                    "schema_version": schema_version or "runtime_native_summary_compare_v0",
                    "left_label": str(payload.get("left_label", "")).strip() or "left",
                    "right_label": str(payload.get("right_label", "")).strip() or "right",
                    "left_release_prefix": left_release_prefix,
                    "right_release_prefix": right_release_prefix,
                    "release_prefixes": compare_release_prefixes,
                    "versions": [
                        str(item).strip()
                        for item in payload.get("versions", [])
                        if str(item).strip()
                    ]
                    if isinstance(payload.get("versions", []), list)
                    else [],
                    "version_count": version_count,
                    "comparison_count": comparison_count,
                    "versions_with_diffs_count": versions_with_diffs_count,
                    "versions_with_diffs": versions_with_diffs,
                    "field_diff_counts": {
                        key: field_diff_counts[key] for key in sorted(field_diff_counts.keys())
                    },
                    "has_diffs": bool(
                        versions_with_diffs_count > 0
                        or any(value > 0 for value in field_diff_counts.values())
                    ),
                }
            )

    artifacts.sort(key=lambda item: str(item.get("artifact_path", "")))
    return artifacts


def filter_runtime_native_evidence_compare_artifacts(
    runtime_compare_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for artifact in runtime_compare_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_path = str(artifact.get("artifact_path", "")).strip().lower()
        if "runtime_native_both_evidence_compare" not in artifact_path:
            continue
        artifacts.append(artifact)
    artifacts.sort(key=lambda item: str(item.get("artifact_path", "")))
    return artifacts


def summarize_runtime_evidence(runtime_evidence_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    runtime_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    failed_records: list[dict[str, str]] = []
    artifact_count = 0
    record_count = 0
    availability_true_count = 0
    availability_false_count = 0
    availability_unknown_count = 0
    probe_checked_count = 0
    probe_executed_count = 0
    runtime_bin_missing_count = 0
    provenance_complete_count = 0
    provenance_missing_count = 0
    probe_args_effective_count = 0
    probe_args_requested_count = 0
    probe_flag_present_count = 0
    probe_flag_requested_present_count = 0
    probe_policy_enable_true_count = 0
    probe_policy_execute_true_count = 0
    probe_policy_require_availability_true_count = 0
    probe_policy_flag_input_present_count = 0
    probe_policy_args_shlex_input_present_count = 0
    probe_args_source_counts: dict[str, int] = {}
    probe_args_requested_source_counts: dict[str, int] = {}
    probe_arg_value_counts: dict[str, int] = {}
    probe_arg_requested_value_counts: dict[str, int] = {}
    scenario_contract_checked_count = 0
    scenario_runtime_ready_true_count = 0
    scenario_runtime_ready_false_count = 0
    scenario_runtime_ready_unknown_count = 0
    scenario_contract_status_counts: dict[str, int] = {}
    scenario_actor_count_total = 0
    scenario_sensor_stream_count_total = 0
    scenario_executed_step_count_total = 0
    scenario_sim_duration_sec_total = 0.0
    scene_result_checked_count = 0
    scene_result_runtime_ready_true_count = 0
    scene_result_runtime_ready_false_count = 0
    scene_result_runtime_ready_unknown_count = 0
    scene_result_status_counts: dict[str, int] = {}
    scene_result_actor_count_total = 0
    scene_result_sensor_stream_count_total = 0
    scene_result_executed_step_count_total = 0
    scene_result_sim_duration_sec_total = 0.0
    scene_result_coverage_ratio_total = 0.0
    scene_result_coverage_ratio_sample_count = 0
    scene_result_ego_travel_distance_m_total = 0.0
    interop_contract_checked_count = 0
    interop_runtime_ready_true_count = 0
    interop_runtime_ready_false_count = 0
    interop_runtime_ready_unknown_count = 0
    interop_contract_status_counts: dict[str, int] = {}
    interop_imported_actor_count_total = 0
    interop_xosc_entity_count_total = 0
    interop_xodr_road_count_total = 0
    interop_executed_step_count_total = 0
    interop_sim_duration_sec_total = 0.0
    interop_export_checked_count = 0
    interop_export_status_counts: dict[str, int] = {}
    interop_export_actor_count_manifest_total = 0
    interop_export_sensor_stream_count_manifest_total = 0
    interop_export_xosc_entity_count_total = 0
    interop_export_xodr_road_count_total = 0
    interop_export_generated_road_length_m_total = 0.0
    interop_import_checked_count = 0
    interop_import_status_counts: dict[str, int] = {}
    interop_import_manifest_consistency_mode_counts: dict[str, int] = {}
    interop_import_export_consistency_mode_counts: dict[str, int] = {}
    interop_import_require_manifest_consistency_input_true_count = 0
    interop_import_require_export_consistency_input_true_count = 0
    interop_import_manifest_consistent_true_count = 0
    interop_import_manifest_consistent_false_count = 0
    interop_import_manifest_consistent_unknown_count = 0
    interop_import_actor_count_manifest_total = 0
    interop_import_xosc_entity_count_total = 0
    interop_import_xodr_road_count_total = 0
    interop_import_xodr_total_road_length_m_total = 0.0
    interop_import_manifest_inconsistent_records: list[dict[str, Any]] = []

    for artifact in runtime_evidence_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_count += 1
        if bool(artifact.get("sim_runtime_probe_enable", False)):
            probe_policy_enable_true_count += 1
        if bool(artifact.get("sim_runtime_probe_execute", False)):
            probe_policy_execute_true_count += 1
        if bool(artifact.get("sim_runtime_probe_require_availability", False)):
            probe_policy_require_availability_true_count += 1
        if bool(str(artifact.get("sim_runtime_probe_flag", "")).strip()):
            probe_policy_flag_input_present_count += 1
        if bool(str(artifact.get("sim_runtime_probe_args_shlex", "")).strip()):
            probe_policy_args_shlex_input_present_count += 1
        runtime_name = str(artifact.get("sim_runtime", "")).strip().lower() or "none"
        runtime_counts[runtime_name] = runtime_counts.get(runtime_name, 0) + 1

        records = artifact.get("runtime_evidence_records")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            record_count += 1
            status = str(record.get("status", "")).strip().lower() or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            record_profile_id = str(record.get("profile_id", "")).strip() or "profile_unknown"
            record_release_id = str(record.get("release_id", "")).strip() or "release_unknown"
            if status == "failed":
                failed_records.append(
                    {
                        "profile_id": record_profile_id,
                        "release_id": record_release_id,
                        "error": str(record.get("error", "")).strip(),
                    }
                )

            runtime_artifacts = record.get("runtime_artifacts")
            if not isinstance(runtime_artifacts, dict):
                availability_unknown_count += 1
                scenario_runtime_ready_unknown_count += 1
                scene_result_runtime_ready_unknown_count += 1
                interop_runtime_ready_unknown_count += 1
                interop_import_manifest_consistent_unknown_count += 1
                continue
            if bool(runtime_artifacts.get("probe_checked", False)):
                probe_checked_count += 1
            if bool(runtime_artifacts.get("probe_executed", False)):
                probe_executed_count += 1
            probe_args_source = str(runtime_artifacts.get("probe_args_source", "")).strip()
            if probe_args_source:
                probe_args_source_counts[probe_args_source] = probe_args_source_counts.get(probe_args_source, 0) + 1
            probe_args_requested_source = str(runtime_artifacts.get("probe_args_requested_source", "")).strip()
            if probe_args_requested_source:
                probe_args_requested_source_counts[probe_args_requested_source] = (
                    probe_args_requested_source_counts.get(probe_args_requested_source, 0) + 1
                )
            probe_flag = str(runtime_artifacts.get("probe_flag", "")).strip()
            probe_flag_requested = str(runtime_artifacts.get("probe_flag_requested", "")).strip()
            if probe_flag:
                probe_flag_present_count += 1
            if probe_flag_requested:
                probe_flag_requested_present_count += 1
            probe_args_values = _normalize_text_list(runtime_artifacts.get("probe_args"))
            if not probe_args_values and probe_flag:
                probe_args_values = [probe_flag]
            if probe_args_values:
                probe_args_effective_count += 1
                _append_values(probe_args_values, probe_arg_value_counts)
            probe_args_requested_values = _normalize_text_list(runtime_artifacts.get("probe_args_requested"))
            if not probe_args_requested_values and probe_flag_requested:
                probe_args_requested_values = [probe_flag_requested]
            if probe_args_requested_values:
                probe_args_requested_count += 1
                _append_values(probe_args_requested_values, probe_arg_requested_value_counts)
            if bool(runtime_artifacts.get("scenario_contract_checked", False)):
                scenario_contract_checked_count += 1
            scenario_contract_status = str(runtime_artifacts.get("scenario_contract_status", "")).strip().lower()
            if scenario_contract_status:
                scenario_contract_status_counts[scenario_contract_status] = (
                    scenario_contract_status_counts.get(scenario_contract_status, 0) + 1
                )
            if "scenario_runtime_ready" not in runtime_artifacts:
                scenario_runtime_ready_unknown_count += 1
            elif bool(runtime_artifacts.get("scenario_runtime_ready", False)):
                scenario_runtime_ready_true_count += 1
            else:
                scenario_runtime_ready_false_count += 1
            try:
                scenario_actor_count_total += max(0, int(runtime_artifacts.get("scenario_actor_count", 0) or 0))
            except (TypeError, ValueError):
                pass
            try:
                scenario_sensor_stream_count_total += max(
                    0,
                    int(runtime_artifacts.get("scenario_sensor_stream_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                scenario_executed_step_count_total += max(
                    0,
                    int(runtime_artifacts.get("scenario_executed_step_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                scenario_sim_duration_sec_total += max(
                    0.0,
                    float(runtime_artifacts.get("scenario_sim_duration_sec", 0.0) or 0.0),
                )
            except (TypeError, ValueError):
                pass
            if bool(runtime_artifacts.get("scene_result_checked", False)):
                scene_result_checked_count += 1
            scene_result_status = str(runtime_artifacts.get("scene_result_status", "")).strip().lower()
            if scene_result_status:
                scene_result_status_counts[scene_result_status] = (
                    scene_result_status_counts.get(scene_result_status, 0) + 1
                )
            if "scene_result_runtime_ready" not in runtime_artifacts:
                scene_result_runtime_ready_unknown_count += 1
            elif bool(runtime_artifacts.get("scene_result_runtime_ready", False)):
                scene_result_runtime_ready_true_count += 1
            else:
                scene_result_runtime_ready_false_count += 1
            try:
                scene_result_actor_count_total += max(0, int(runtime_artifacts.get("scene_result_actor_count", 0) or 0))
            except (TypeError, ValueError):
                pass
            try:
                scene_result_sensor_stream_count_total += max(
                    0,
                    int(runtime_artifacts.get("scene_result_sensor_stream_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                scene_result_executed_step_count_total += max(
                    0,
                    int(runtime_artifacts.get("scene_result_executed_step_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                scene_result_sim_duration_sec_total += max(
                    0.0,
                    float(runtime_artifacts.get("scene_result_sim_duration_sec", 0.0) or 0.0),
                )
            except (TypeError, ValueError):
                pass
            try:
                scene_result_coverage_ratio = float(runtime_artifacts.get("scene_result_coverage_ratio", 0.0) or 0.0)
            except (TypeError, ValueError):
                scene_result_coverage_ratio = 0.0
            if scene_result_coverage_ratio > 0.0:
                scene_result_coverage_ratio_total += max(0.0, scene_result_coverage_ratio)
                scene_result_coverage_ratio_sample_count += 1
            try:
                scene_result_ego_travel_distance_m_total += max(
                    0.0,
                    float(runtime_artifacts.get("scene_result_ego_travel_distance_m", 0.0) or 0.0),
                )
            except (TypeError, ValueError):
                pass
            if bool(runtime_artifacts.get("interop_contract_checked", False)):
                interop_contract_checked_count += 1
            interop_contract_status = str(runtime_artifacts.get("interop_contract_status", "")).strip().lower()
            if interop_contract_status:
                interop_contract_status_counts[interop_contract_status] = (
                    interop_contract_status_counts.get(interop_contract_status, 0) + 1
                )
            if "interop_runtime_ready" not in runtime_artifacts:
                interop_runtime_ready_unknown_count += 1
            elif bool(runtime_artifacts.get("interop_runtime_ready", False)):
                interop_runtime_ready_true_count += 1
            else:
                interop_runtime_ready_false_count += 1
            try:
                interop_imported_actor_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_imported_actor_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_xosc_entity_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_xosc_entity_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_xodr_road_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_xodr_road_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_executed_step_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_executed_step_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_sim_duration_sec_total += max(
                    0.0,
                    float(runtime_artifacts.get("interop_sim_duration_sec", 0.0) or 0.0),
                )
            except (TypeError, ValueError):
                pass
            if bool(runtime_artifacts.get("interop_export_checked", False)):
                interop_export_checked_count += 1
            interop_export_status = str(runtime_artifacts.get("interop_export_status", "")).strip().lower()
            if interop_export_status:
                interop_export_status_counts[interop_export_status] = (
                    interop_export_status_counts.get(interop_export_status, 0) + 1
                )
            try:
                interop_export_actor_count_manifest_total += max(
                    0,
                    int(runtime_artifacts.get("interop_export_actor_count_manifest", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_export_sensor_stream_count_manifest_total += max(
                    0,
                    int(runtime_artifacts.get("interop_export_sensor_stream_count_manifest", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_export_xosc_entity_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_export_xosc_entity_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_export_xodr_road_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_export_xodr_road_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_export_generated_road_length_m_total += max(
                    0.0,
                    float(runtime_artifacts.get("interop_export_generated_road_length_m", 0.0) or 0.0),
                )
            except (TypeError, ValueError):
                pass
            if bool(runtime_artifacts.get("interop_import_checked", False)):
                interop_import_checked_count += 1
            interop_import_status = str(runtime_artifacts.get("interop_import_status", "")).strip().lower()
            if interop_import_status:
                interop_import_status_counts[interop_import_status] = (
                    interop_import_status_counts.get(interop_import_status, 0) + 1
                )
            interop_import_manifest_consistency_mode = str(
                runtime_artifacts.get("interop_import_manifest_consistency_mode", "")
            ).strip().lower()
            if interop_import_manifest_consistency_mode in {"require", "allow"}:
                interop_import_manifest_consistency_mode_counts[interop_import_manifest_consistency_mode] = (
                    interop_import_manifest_consistency_mode_counts.get(
                        interop_import_manifest_consistency_mode,
                        0,
                    )
                    + 1
                )
            interop_import_export_consistency_mode = str(
                runtime_artifacts.get("interop_import_export_consistency_mode", "")
            ).strip().lower()
            if interop_import_export_consistency_mode in {"require", "allow"}:
                interop_import_export_consistency_mode_counts[interop_import_export_consistency_mode] = (
                    interop_import_export_consistency_mode_counts.get(
                        interop_import_export_consistency_mode,
                        0,
                    )
                    + 1
                )
            if bool(runtime_artifacts.get("interop_import_require_manifest_consistency_input", False)):
                interop_import_require_manifest_consistency_input_true_count += 1
            if bool(runtime_artifacts.get("interop_import_require_export_consistency_input", False)):
                interop_import_require_export_consistency_input_true_count += 1
            if "interop_import_manifest_consistent" not in runtime_artifacts:
                interop_import_manifest_consistent_unknown_count += 1
            elif bool(runtime_artifacts.get("interop_import_manifest_consistent", False)):
                interop_import_manifest_consistent_true_count += 1
            else:
                interop_import_manifest_consistent_false_count += 1
                interop_import_actor_count_manifest_value = 0
                interop_import_xosc_entity_count_value = 0
                try:
                    interop_import_actor_count_manifest_value = max(
                        0,
                        int(runtime_artifacts.get("interop_import_actor_count_manifest", 0) or 0),
                    )
                except (TypeError, ValueError):
                    interop_import_actor_count_manifest_value = 0
                try:
                    interop_import_xosc_entity_count_value = max(
                        0,
                        int(runtime_artifacts.get("interop_import_xosc_entity_count", 0) or 0),
                    )
                except (TypeError, ValueError):
                    interop_import_xosc_entity_count_value = 0
                interop_import_manifest_inconsistent_records.append(
                    {
                        "profile_id": record_profile_id,
                        "release_id": record_release_id,
                        "runtime": runtime_name,
                        "actor_count_manifest": interop_import_actor_count_manifest_value,
                        "xosc_entity_count": interop_import_xosc_entity_count_value,
                    }
                )
            try:
                interop_import_actor_count_manifest_total += max(
                    0,
                    int(runtime_artifacts.get("interop_import_actor_count_manifest", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_import_xosc_entity_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_import_xosc_entity_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_import_xodr_road_count_total += max(
                    0,
                    int(runtime_artifacts.get("interop_import_xodr_road_count", 0) or 0),
                )
            except (TypeError, ValueError):
                pass
            try:
                interop_import_xodr_total_road_length_m_total += max(
                    0.0,
                    float(runtime_artifacts.get("interop_import_xodr_total_road_length_m", 0.0) or 0.0),
                )
            except (TypeError, ValueError):
                pass
            if runtime_artifacts.get("runtime_bin_resolved_exists", None) is False:
                runtime_bin_missing_count += 1
            try:
                runtime_bin_size_bytes = int(runtime_artifacts.get("runtime_bin_size_bytes", 0) or 0)
            except (TypeError, ValueError):
                runtime_bin_size_bytes = 0
            provenance_complete = (
                bool(str(runtime_artifacts.get("runner_host", "")).strip())
                and bool(str(runtime_artifacts.get("runner_platform", "")).strip())
                and bool(str(runtime_artifacts.get("runner_python", "")).strip())
                and bool(str(runtime_artifacts.get("runtime_bin_sha256", "")).strip())
                and runtime_bin_size_bytes > 0
            )
            if bool(runtime_artifacts.get("probe_executed", False)):
                provenance_complete = provenance_complete and bool(
                    str(runtime_artifacts.get("probe_command", "")).strip()
                )
            if provenance_complete:
                provenance_complete_count += 1
            else:
                provenance_missing_count += 1
            if "runtime_available" not in runtime_artifacts:
                availability_unknown_count += 1
            elif bool(runtime_artifacts.get("runtime_available", False)):
                availability_true_count += 1
            else:
                availability_false_count += 1

    failed_records.sort(
        key=lambda item: (
            str(item.get("profile_id", "")),
            str(item.get("release_id", "")),
            str(item.get("error", "")),
        )
    )
    interop_import_manifest_inconsistent_records.sort(
        key=lambda item: (
            str(item.get("profile_id", "")),
            str(item.get("release_id", "")),
            str(item.get("runtime", "")),
            int(item.get("actor_count_manifest", 0) or 0),
            int(item.get("xosc_entity_count", 0) or 0),
        )
    )
    return {
        "artifact_count": artifact_count,
        "record_count": record_count,
        "runtime_counts": {key: runtime_counts[key] for key in sorted(runtime_counts.keys())},
        "status_counts": {key: status_counts[key] for key in sorted(status_counts.keys())},
        "validated_count": status_counts.get("validated", 0),
        "failed_count": status_counts.get("failed", 0),
        "availability_true_count": availability_true_count,
        "availability_false_count": availability_false_count,
        "availability_unknown_count": availability_unknown_count,
        "probe_checked_count": probe_checked_count,
        "probe_executed_count": probe_executed_count,
        "runtime_bin_missing_count": runtime_bin_missing_count,
        "provenance_complete_count": provenance_complete_count,
        "provenance_missing_count": provenance_missing_count,
        "probe_args_effective_count": probe_args_effective_count,
        "probe_args_requested_count": probe_args_requested_count,
        "probe_flag_present_count": probe_flag_present_count,
        "probe_flag_requested_present_count": probe_flag_requested_present_count,
        "probe_policy_enable_true_count": probe_policy_enable_true_count,
        "probe_policy_execute_true_count": probe_policy_execute_true_count,
        "probe_policy_require_availability_true_count": probe_policy_require_availability_true_count,
        "probe_policy_flag_input_present_count": probe_policy_flag_input_present_count,
        "probe_policy_args_shlex_input_present_count": probe_policy_args_shlex_input_present_count,
        "probe_args_source_counts": {
            key: probe_args_source_counts[key] for key in sorted(probe_args_source_counts.keys())
        },
        "probe_args_requested_source_counts": {
            key: probe_args_requested_source_counts[key]
            for key in sorted(probe_args_requested_source_counts.keys())
        },
        "probe_arg_value_counts": {
            key: probe_arg_value_counts[key] for key in sorted(probe_arg_value_counts.keys())
        },
        "probe_arg_requested_value_counts": {
            key: probe_arg_requested_value_counts[key]
            for key in sorted(probe_arg_requested_value_counts.keys())
        },
        "scenario_contract_checked_count": scenario_contract_checked_count,
        "scenario_runtime_ready_true_count": scenario_runtime_ready_true_count,
        "scenario_runtime_ready_false_count": scenario_runtime_ready_false_count,
        "scenario_runtime_ready_unknown_count": scenario_runtime_ready_unknown_count,
        "scenario_contract_status_counts": {
            key: scenario_contract_status_counts[key]
            for key in sorted(scenario_contract_status_counts.keys())
        },
        "scenario_actor_count_total": scenario_actor_count_total,
        "scenario_sensor_stream_count_total": scenario_sensor_stream_count_total,
        "scenario_executed_step_count_total": scenario_executed_step_count_total,
        "scenario_sim_duration_sec_total": float(round(scenario_sim_duration_sec_total, 6)),
        "scene_result_checked_count": scene_result_checked_count,
        "scene_result_runtime_ready_true_count": scene_result_runtime_ready_true_count,
        "scene_result_runtime_ready_false_count": scene_result_runtime_ready_false_count,
        "scene_result_runtime_ready_unknown_count": scene_result_runtime_ready_unknown_count,
        "scene_result_status_counts": {
            key: scene_result_status_counts[key]
            for key in sorted(scene_result_status_counts.keys())
        },
        "scene_result_actor_count_total": scene_result_actor_count_total,
        "scene_result_sensor_stream_count_total": scene_result_sensor_stream_count_total,
        "scene_result_executed_step_count_total": scene_result_executed_step_count_total,
        "scene_result_sim_duration_sec_total": float(round(scene_result_sim_duration_sec_total, 6)),
        "scene_result_coverage_ratio_avg": (
            float(round(scene_result_coverage_ratio_total / float(scene_result_coverage_ratio_sample_count), 6))
            if scene_result_coverage_ratio_sample_count > 0
            else 0.0
        ),
        "scene_result_coverage_ratio_sample_count": scene_result_coverage_ratio_sample_count,
        "scene_result_ego_travel_distance_m_total": float(round(scene_result_ego_travel_distance_m_total, 6)),
        "interop_contract_checked_count": interop_contract_checked_count,
        "interop_runtime_ready_true_count": interop_runtime_ready_true_count,
        "interop_runtime_ready_false_count": interop_runtime_ready_false_count,
        "interop_runtime_ready_unknown_count": interop_runtime_ready_unknown_count,
        "interop_contract_status_counts": {
            key: interop_contract_status_counts[key]
            for key in sorted(interop_contract_status_counts.keys())
        },
        "interop_imported_actor_count_total": interop_imported_actor_count_total,
        "interop_xosc_entity_count_total": interop_xosc_entity_count_total,
        "interop_xodr_road_count_total": interop_xodr_road_count_total,
        "interop_executed_step_count_total": interop_executed_step_count_total,
        "interop_sim_duration_sec_total": float(round(interop_sim_duration_sec_total, 6)),
        "interop_export_checked_count": interop_export_checked_count,
        "interop_export_status_counts": {
            key: interop_export_status_counts[key]
            for key in sorted(interop_export_status_counts.keys())
        },
        "interop_export_actor_count_manifest_total": interop_export_actor_count_manifest_total,
        "interop_export_sensor_stream_count_manifest_total": interop_export_sensor_stream_count_manifest_total,
        "interop_export_xosc_entity_count_total": interop_export_xosc_entity_count_total,
        "interop_export_xodr_road_count_total": interop_export_xodr_road_count_total,
        "interop_export_generated_road_length_m_total": float(round(interop_export_generated_road_length_m_total, 6)),
        "interop_import_checked_count": interop_import_checked_count,
        "interop_import_status_counts": {
            key: interop_import_status_counts[key]
            for key in sorted(interop_import_status_counts.keys())
        },
        "interop_import_manifest_consistency_mode_counts": {
            key: interop_import_manifest_consistency_mode_counts[key]
            for key in sorted(interop_import_manifest_consistency_mode_counts.keys())
        },
        "interop_import_export_consistency_mode_counts": {
            key: interop_import_export_consistency_mode_counts[key]
            for key in sorted(interop_import_export_consistency_mode_counts.keys())
        },
        "interop_import_require_manifest_consistency_input_true_count": (
            interop_import_require_manifest_consistency_input_true_count
        ),
        "interop_import_require_export_consistency_input_true_count": (
            interop_import_require_export_consistency_input_true_count
        ),
        "interop_import_manifest_consistent_true_count": interop_import_manifest_consistent_true_count,
        "interop_import_manifest_consistent_false_count": interop_import_manifest_consistent_false_count,
        "interop_import_manifest_consistent_unknown_count": interop_import_manifest_consistent_unknown_count,
        "interop_import_manifest_inconsistent_count": len(interop_import_manifest_inconsistent_records),
        "interop_import_manifest_inconsistent_records": interop_import_manifest_inconsistent_records,
        "interop_import_actor_count_manifest_total": interop_import_actor_count_manifest_total,
        "interop_import_xosc_entity_count_total": interop_import_xosc_entity_count_total,
        "interop_import_xodr_road_count_total": interop_import_xodr_road_count_total,
        "interop_import_xodr_total_road_length_m_total": float(round(interop_import_xodr_total_road_length_m_total, 6)),
        "failed_records": failed_records,
    }


def summarize_runtime_lane_execution(runtime_lane_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    def _format_threshold_float_key(value: float) -> str:
        text = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def _float_sort_key(raw_key: str) -> tuple[int, float | str]:
        key_text = str(raw_key).strip()
        try:
            return (0, float(key_text))
        except (TypeError, ValueError):
            return (1, key_text)

    artifact_count = 0
    runtime_row_count = 0
    runtime_counts: dict[str, int] = {}
    result_counts: dict[str, int] = {}
    lane_counts: dict[str, int] = {}
    lane_row_counts: dict[str, int] = {}
    runner_platform_counts: dict[str, int] = {}
    sim_runtime_input_counts: dict[str, int] = {}
    dry_run_counts: dict[str, int] = {}
    continue_on_runtime_failure_counts: dict[str, int] = {}
    runtime_asset_profile_counts: dict[str, int] = {}
    runtime_asset_archive_sha256_mode_counts: dict[str, int] = {}
    runtime_exec_lane_warn_min_rows_counts: dict[str, int] = {}
    runtime_exec_lane_hold_min_rows_counts: dict[str, int] = {}
    runtime_compare_warn_min_artifacts_with_diffs_counts: dict[str, int] = {}
    runtime_compare_hold_min_artifacts_with_diffs_counts: dict[str, int] = {}
    phase2_sensor_fidelity_score_avg_warn_min_counts: dict[str, int] = {}
    phase2_sensor_fidelity_score_avg_hold_min_counts: dict[str, int] = {}
    phase2_sensor_frame_count_avg_warn_min_counts: dict[str, int] = {}
    phase2_sensor_frame_count_avg_hold_min_counts: dict[str, int] = {}
    phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts: dict[str, int] = {}
    phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts: dict[str, int] = {}
    phase2_sensor_lidar_point_count_avg_warn_min_counts: dict[str, int] = {}
    phase2_sensor_lidar_point_count_avg_hold_min_counts: dict[str, int] = {}
    phase2_sensor_radar_false_positive_rate_avg_warn_max_counts: dict[str, int] = {}
    phase2_sensor_radar_false_positive_rate_avg_hold_max_counts: dict[str, int] = {}
    runtime_evidence_missing_runtime_counts: dict[str, int] = {}
    runtime_failure_reason_counts: dict[str, int] = {}
    failed_rows: list[dict[str, str]] = []
    runtime_evidence_path_present_count = 0
    runtime_evidence_exists_true_count = 0
    runtime_evidence_exists_false_count = 0
    runtime_evidence_exists_unknown_count = 0

    for artifact in runtime_lane_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_count += 1
        artifact_runtime_evidence_missing_runtime_counts = artifact.get(
            "runtime_evidence_missing_runtime_counts",
            {},
        )
        artifact_runtime_evidence_missing_runtime_counts_provided = bool(
            artifact.get("runtime_evidence_missing_runtime_counts_provided", False)
        )
        if (
            artifact_runtime_evidence_missing_runtime_counts_provided
            and isinstance(artifact_runtime_evidence_missing_runtime_counts, dict)
        ):
            for key, value in artifact_runtime_evidence_missing_runtime_counts.items():
                runtime_name = str(key).strip().lower()
                if not runtime_name:
                    continue
                try:
                    parsed_count = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed_count <= 0:
                    continue
                runtime_evidence_missing_runtime_counts[runtime_name] = (
                    runtime_evidence_missing_runtime_counts.get(runtime_name, 0) + parsed_count
                )
        lane_value = str(artifact.get("lane_resolved", "")).strip().lower()
        if not lane_value:
            lane_value = str(artifact.get("lane_input", "")).strip().lower() or "unknown"
        lane_counts[lane_value] = lane_counts.get(lane_value, 0) + 1
        runner_platform_value = str(artifact.get("runner_platform", "")).strip() or "unknown"
        runner_platform_counts[runner_platform_value] = runner_platform_counts.get(runner_platform_value, 0) + 1
        sim_runtime_input_value = str(artifact.get("sim_runtime_input", "")).strip().lower() or "unknown"
        sim_runtime_input_counts[sim_runtime_input_value] = sim_runtime_input_counts.get(sim_runtime_input_value, 0) + 1
        dry_run_value = str(artifact.get("dry_run", "")).strip() or "unknown"
        dry_run_counts[dry_run_value] = dry_run_counts.get(dry_run_value, 0) + 1
        continue_on_runtime_failure_value = str(artifact.get("continue_on_runtime_failure", "")).strip() or "unknown"
        continue_on_runtime_failure_counts[continue_on_runtime_failure_value] = (
            continue_on_runtime_failure_counts.get(continue_on_runtime_failure_value, 0) + 1
        )
        runtime_asset_profile = str(artifact.get("runtime_asset_profile", "")).strip().lower() or "unknown"
        runtime_asset_profile_counts[runtime_asset_profile] = (
            runtime_asset_profile_counts.get(runtime_asset_profile, 0) + 1
        )
        runtime_asset_archive_sha256_mode = (
            str(artifact.get("runtime_asset_archive_sha256_mode", "")).strip().lower() or "unknown"
        )
        runtime_asset_archive_sha256_mode_counts[runtime_asset_archive_sha256_mode] = (
            runtime_asset_archive_sha256_mode_counts.get(runtime_asset_archive_sha256_mode, 0) + 1
        )
        runtime_exec_lane_warn_min_rows = _to_non_negative_int_or_none(
            artifact.get("runtime_exec_lane_warn_min_rows", None)
        )
        if runtime_exec_lane_warn_min_rows is not None:
            runtime_exec_lane_warn_min_rows_key = str(runtime_exec_lane_warn_min_rows)
            runtime_exec_lane_warn_min_rows_counts[runtime_exec_lane_warn_min_rows_key] = (
                runtime_exec_lane_warn_min_rows_counts.get(runtime_exec_lane_warn_min_rows_key, 0) + 1
            )
        runtime_exec_lane_hold_min_rows = _to_non_negative_int_or_none(
            artifact.get("runtime_exec_lane_hold_min_rows", None)
        )
        if runtime_exec_lane_hold_min_rows is not None:
            runtime_exec_lane_hold_min_rows_key = str(runtime_exec_lane_hold_min_rows)
            runtime_exec_lane_hold_min_rows_counts[runtime_exec_lane_hold_min_rows_key] = (
                runtime_exec_lane_hold_min_rows_counts.get(runtime_exec_lane_hold_min_rows_key, 0) + 1
            )
        runtime_compare_warn_min_artifacts_with_diffs = _to_non_negative_int_or_none(
            artifact.get("runtime_evidence_compare_warn_min_artifacts_with_diffs", None)
        )
        if runtime_compare_warn_min_artifacts_with_diffs is not None:
            runtime_compare_warn_min_artifacts_with_diffs_key = str(
                runtime_compare_warn_min_artifacts_with_diffs
            )
            runtime_compare_warn_min_artifacts_with_diffs_counts[
                runtime_compare_warn_min_artifacts_with_diffs_key
            ] = (
                runtime_compare_warn_min_artifacts_with_diffs_counts.get(
                    runtime_compare_warn_min_artifacts_with_diffs_key,
                    0,
                )
                + 1
            )
        runtime_compare_hold_min_artifacts_with_diffs = _to_non_negative_int_or_none(
            artifact.get("runtime_evidence_compare_hold_min_artifacts_with_diffs", None)
        )
        if runtime_compare_hold_min_artifacts_with_diffs is not None:
            runtime_compare_hold_min_artifacts_with_diffs_key = str(
                runtime_compare_hold_min_artifacts_with_diffs
            )
            runtime_compare_hold_min_artifacts_with_diffs_counts[
                runtime_compare_hold_min_artifacts_with_diffs_key
            ] = (
                runtime_compare_hold_min_artifacts_with_diffs_counts.get(
                    runtime_compare_hold_min_artifacts_with_diffs_key,
                    0,
                )
                + 1
            )
        phase2_sensor_fidelity_score_avg_warn_min = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_fidelity_score_avg_warn_min", None)
        )
        if phase2_sensor_fidelity_score_avg_warn_min is not None:
            phase2_sensor_fidelity_score_avg_warn_min_key = _format_threshold_float_key(
                phase2_sensor_fidelity_score_avg_warn_min
            )
            phase2_sensor_fidelity_score_avg_warn_min_counts[phase2_sensor_fidelity_score_avg_warn_min_key] = (
                phase2_sensor_fidelity_score_avg_warn_min_counts.get(
                    phase2_sensor_fidelity_score_avg_warn_min_key,
                    0,
                )
                + 1
            )
        phase2_sensor_fidelity_score_avg_hold_min = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_fidelity_score_avg_hold_min", None)
        )
        if phase2_sensor_fidelity_score_avg_hold_min is not None:
            phase2_sensor_fidelity_score_avg_hold_min_key = _format_threshold_float_key(
                phase2_sensor_fidelity_score_avg_hold_min
            )
            phase2_sensor_fidelity_score_avg_hold_min_counts[phase2_sensor_fidelity_score_avg_hold_min_key] = (
                phase2_sensor_fidelity_score_avg_hold_min_counts.get(
                    phase2_sensor_fidelity_score_avg_hold_min_key,
                    0,
                )
                + 1
            )
        phase2_sensor_frame_count_avg_warn_min = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_frame_count_avg_warn_min", None)
        )
        if phase2_sensor_frame_count_avg_warn_min is not None:
            phase2_sensor_frame_count_avg_warn_min_key = _format_threshold_float_key(
                phase2_sensor_frame_count_avg_warn_min
            )
            phase2_sensor_frame_count_avg_warn_min_counts[phase2_sensor_frame_count_avg_warn_min_key] = (
                phase2_sensor_frame_count_avg_warn_min_counts.get(
                    phase2_sensor_frame_count_avg_warn_min_key,
                    0,
                )
                + 1
            )
        phase2_sensor_frame_count_avg_hold_min = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_frame_count_avg_hold_min", None)
        )
        if phase2_sensor_frame_count_avg_hold_min is not None:
            phase2_sensor_frame_count_avg_hold_min_key = _format_threshold_float_key(
                phase2_sensor_frame_count_avg_hold_min
            )
            phase2_sensor_frame_count_avg_hold_min_counts[phase2_sensor_frame_count_avg_hold_min_key] = (
                phase2_sensor_frame_count_avg_hold_min_counts.get(
                    phase2_sensor_frame_count_avg_hold_min_key,
                    0,
                )
                + 1
            )
        phase2_sensor_camera_noise_stddev_px_avg_warn_max = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_camera_noise_stddev_px_avg_warn_max", None)
        )
        if phase2_sensor_camera_noise_stddev_px_avg_warn_max is not None:
            phase2_sensor_camera_noise_stddev_px_avg_warn_max_key = _format_threshold_float_key(
                phase2_sensor_camera_noise_stddev_px_avg_warn_max
            )
            phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts[
                phase2_sensor_camera_noise_stddev_px_avg_warn_max_key
            ] = (
                phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts.get(
                    phase2_sensor_camera_noise_stddev_px_avg_warn_max_key,
                    0,
                )
                + 1
            )
        phase2_sensor_camera_noise_stddev_px_avg_hold_max = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_camera_noise_stddev_px_avg_hold_max", None)
        )
        if phase2_sensor_camera_noise_stddev_px_avg_hold_max is not None:
            phase2_sensor_camera_noise_stddev_px_avg_hold_max_key = _format_threshold_float_key(
                phase2_sensor_camera_noise_stddev_px_avg_hold_max
            )
            phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts[
                phase2_sensor_camera_noise_stddev_px_avg_hold_max_key
            ] = (
                phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts.get(
                    phase2_sensor_camera_noise_stddev_px_avg_hold_max_key,
                    0,
                )
                + 1
            )
        phase2_sensor_lidar_point_count_avg_warn_min = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_lidar_point_count_avg_warn_min", None)
        )
        if phase2_sensor_lidar_point_count_avg_warn_min is not None:
            phase2_sensor_lidar_point_count_avg_warn_min_key = _format_threshold_float_key(
                phase2_sensor_lidar_point_count_avg_warn_min
            )
            phase2_sensor_lidar_point_count_avg_warn_min_counts[
                phase2_sensor_lidar_point_count_avg_warn_min_key
            ] = (
                phase2_sensor_lidar_point_count_avg_warn_min_counts.get(
                    phase2_sensor_lidar_point_count_avg_warn_min_key,
                    0,
                )
                + 1
            )
        phase2_sensor_lidar_point_count_avg_hold_min = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_lidar_point_count_avg_hold_min", None)
        )
        if phase2_sensor_lidar_point_count_avg_hold_min is not None:
            phase2_sensor_lidar_point_count_avg_hold_min_key = _format_threshold_float_key(
                phase2_sensor_lidar_point_count_avg_hold_min
            )
            phase2_sensor_lidar_point_count_avg_hold_min_counts[
                phase2_sensor_lidar_point_count_avg_hold_min_key
            ] = (
                phase2_sensor_lidar_point_count_avg_hold_min_counts.get(
                    phase2_sensor_lidar_point_count_avg_hold_min_key,
                    0,
                )
                + 1
            )
        phase2_sensor_radar_false_positive_rate_avg_warn_max = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_radar_false_positive_rate_avg_warn_max", None)
        )
        if phase2_sensor_radar_false_positive_rate_avg_warn_max is not None:
            phase2_sensor_radar_false_positive_rate_avg_warn_max_key = _format_threshold_float_key(
                phase2_sensor_radar_false_positive_rate_avg_warn_max
            )
            phase2_sensor_radar_false_positive_rate_avg_warn_max_counts[
                phase2_sensor_radar_false_positive_rate_avg_warn_max_key
            ] = (
                phase2_sensor_radar_false_positive_rate_avg_warn_max_counts.get(
                    phase2_sensor_radar_false_positive_rate_avg_warn_max_key,
                    0,
                )
                + 1
            )
        phase2_sensor_radar_false_positive_rate_avg_hold_max = _to_non_negative_float_or_none(
            artifact.get("phase2_sensor_radar_false_positive_rate_avg_hold_max", None)
        )
        if phase2_sensor_radar_false_positive_rate_avg_hold_max is not None:
            phase2_sensor_radar_false_positive_rate_avg_hold_max_key = _format_threshold_float_key(
                phase2_sensor_radar_false_positive_rate_avg_hold_max
            )
            phase2_sensor_radar_false_positive_rate_avg_hold_max_counts[
                phase2_sensor_radar_false_positive_rate_avg_hold_max_key
            ] = (
                phase2_sensor_radar_false_positive_rate_avg_hold_max_counts.get(
                    phase2_sensor_radar_false_positive_rate_avg_hold_max_key,
                    0,
                )
                + 1
            )
        rows = artifact.get("runtime_rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            runtime_row_count += 1
            lane_row_counts[lane_value] = lane_row_counts.get(lane_value, 0) + 1
            runtime_name = str(row.get("runtime", "")).strip().lower() or "unknown"
            result = str(row.get("result", "")).strip().lower() or "unknown"
            release_id = str(row.get("release_id", "")).strip() or "release_unknown"
            runtime_evidence_path = str(row.get("runtime_evidence_path", "")).strip()
            runtime_evidence_exists_raw = row.get("runtime_evidence_exists", None)
            runtime_evidence_exists = (
                runtime_evidence_exists_raw if isinstance(runtime_evidence_exists_raw, bool) else None
            )
            if runtime_evidence_path:
                runtime_evidence_path_present_count += 1
            if runtime_evidence_exists is True:
                runtime_evidence_exists_true_count += 1
            elif runtime_evidence_exists is False:
                runtime_evidence_exists_false_count += 1
                if not artifact_runtime_evidence_missing_runtime_counts_provided:
                    runtime_evidence_missing_runtime_counts[runtime_name] = (
                        runtime_evidence_missing_runtime_counts.get(runtime_name, 0) + 1
                    )
            else:
                runtime_evidence_exists_unknown_count += 1
            runtime_counts[runtime_name] = runtime_counts.get(runtime_name, 0) + 1
            result_counts[result] = result_counts.get(result, 0) + 1
            if result == "fail":
                runtime_failure_reason = str(row.get("runtime_failure_reason", "")).strip().lower() or "unknown"
                runtime_failure_reason_counts[runtime_failure_reason] = (
                    runtime_failure_reason_counts.get(runtime_failure_reason, 0) + 1
                )
                failed_rows.append(
                    {
                        "runtime": runtime_name,
                        "release_id": release_id,
                        "lane": lane_value,
                        "runtime_failure_reason": runtime_failure_reason,
                    }
                )

    failed_rows.sort(
        key=lambda item: (
            str(item.get("release_id", "")),
            str(item.get("runtime", "")),
            str(item.get("lane", "")),
            str(item.get("runtime_failure_reason", "")),
        )
    )

    return {
        "artifact_count": artifact_count,
        "runtime_row_count": runtime_row_count,
        "runtime_counts": {key: runtime_counts[key] for key in sorted(runtime_counts.keys())},
        "result_counts": {key: result_counts[key] for key in sorted(result_counts.keys())},
        "lane_counts": {key: lane_counts[key] for key in sorted(lane_counts.keys())},
        "lane_row_counts": {key: lane_row_counts[key] for key in sorted(lane_row_counts.keys())},
        "runner_platform_counts": {key: runner_platform_counts[key] for key in sorted(runner_platform_counts.keys())},
        "sim_runtime_input_counts": {
            key: sim_runtime_input_counts[key] for key in sorted(sim_runtime_input_counts.keys())
        },
        "dry_run_counts": {key: dry_run_counts[key] for key in sorted(dry_run_counts.keys())},
        "continue_on_runtime_failure_counts": {
            key: continue_on_runtime_failure_counts[key]
            for key in sorted(continue_on_runtime_failure_counts.keys())
        },
        "runtime_asset_profile_counts": {
            key: runtime_asset_profile_counts[key] for key in sorted(runtime_asset_profile_counts.keys())
        },
        "runtime_asset_archive_sha256_mode_counts": {
            key: runtime_asset_archive_sha256_mode_counts[key]
            for key in sorted(runtime_asset_archive_sha256_mode_counts.keys())
        },
        "runtime_exec_lane_warn_min_rows_counts": {
            key: runtime_exec_lane_warn_min_rows_counts[key]
            for key in sorted(runtime_exec_lane_warn_min_rows_counts.keys(), key=lambda raw_key: int(raw_key))
        },
        "runtime_exec_lane_hold_min_rows_counts": {
            key: runtime_exec_lane_hold_min_rows_counts[key]
            for key in sorted(runtime_exec_lane_hold_min_rows_counts.keys(), key=lambda raw_key: int(raw_key))
        },
        "runtime_compare_warn_min_artifacts_with_diffs_counts": {
            key: runtime_compare_warn_min_artifacts_with_diffs_counts[key]
            for key in sorted(
                runtime_compare_warn_min_artifacts_with_diffs_counts.keys(),
                key=lambda raw_key: int(raw_key),
            )
        },
        "runtime_compare_hold_min_artifacts_with_diffs_counts": {
            key: runtime_compare_hold_min_artifacts_with_diffs_counts[key]
            for key in sorted(
                runtime_compare_hold_min_artifacts_with_diffs_counts.keys(),
                key=lambda raw_key: int(raw_key),
            )
        },
        "phase2_sensor_fidelity_score_avg_warn_min_counts": {
            key: phase2_sensor_fidelity_score_avg_warn_min_counts[key]
            for key in sorted(phase2_sensor_fidelity_score_avg_warn_min_counts.keys(), key=_float_sort_key)
        },
        "phase2_sensor_fidelity_score_avg_hold_min_counts": {
            key: phase2_sensor_fidelity_score_avg_hold_min_counts[key]
            for key in sorted(phase2_sensor_fidelity_score_avg_hold_min_counts.keys(), key=_float_sort_key)
        },
        "phase2_sensor_frame_count_avg_warn_min_counts": {
            key: phase2_sensor_frame_count_avg_warn_min_counts[key]
            for key in sorted(phase2_sensor_frame_count_avg_warn_min_counts.keys(), key=_float_sort_key)
        },
        "phase2_sensor_frame_count_avg_hold_min_counts": {
            key: phase2_sensor_frame_count_avg_hold_min_counts[key]
            for key in sorted(phase2_sensor_frame_count_avg_hold_min_counts.keys(), key=_float_sort_key)
        },
        "phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts": {
            key: phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts[key]
            for key in sorted(
                phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts.keys(),
                key=_float_sort_key,
            )
        },
        "phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts": {
            key: phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts[key]
            for key in sorted(
                phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts.keys(),
                key=_float_sort_key,
            )
        },
        "phase2_sensor_lidar_point_count_avg_warn_min_counts": {
            key: phase2_sensor_lidar_point_count_avg_warn_min_counts[key]
            for key in sorted(
                phase2_sensor_lidar_point_count_avg_warn_min_counts.keys(),
                key=_float_sort_key,
            )
        },
        "phase2_sensor_lidar_point_count_avg_hold_min_counts": {
            key: phase2_sensor_lidar_point_count_avg_hold_min_counts[key]
            for key in sorted(
                phase2_sensor_lidar_point_count_avg_hold_min_counts.keys(),
                key=_float_sort_key,
            )
        },
        "phase2_sensor_radar_false_positive_rate_avg_warn_max_counts": {
            key: phase2_sensor_radar_false_positive_rate_avg_warn_max_counts[key]
            for key in sorted(
                phase2_sensor_radar_false_positive_rate_avg_warn_max_counts.keys(),
                key=_float_sort_key,
            )
        },
        "phase2_sensor_radar_false_positive_rate_avg_hold_max_counts": {
            key: phase2_sensor_radar_false_positive_rate_avg_hold_max_counts[key]
            for key in sorted(
                phase2_sensor_radar_false_positive_rate_avg_hold_max_counts.keys(),
                key=_float_sort_key,
            )
        },
        "runtime_evidence_missing_runtime_counts": {
            key: runtime_evidence_missing_runtime_counts[key]
            for key in sorted(runtime_evidence_missing_runtime_counts.keys())
        },
        "runtime_failure_reason_counts": {
            key: runtime_failure_reason_counts[key] for key in sorted(runtime_failure_reason_counts.keys())
        },
        "exec_lane_row_count": lane_row_counts.get("exec", 0),
        "pass_count": result_counts.get("pass", 0),
        "fail_count": result_counts.get("fail", 0),
        "unknown_count": result_counts.get("unknown", 0),
        "failed_rows": failed_rows,
        "runtime_evidence_path_present_count": runtime_evidence_path_present_count,
        "runtime_evidence_exists_true_count": runtime_evidence_exists_true_count,
        "runtime_evidence_exists_false_count": runtime_evidence_exists_false_count,
        "runtime_evidence_exists_unknown_count": runtime_evidence_exists_unknown_count,
    }


def summarize_runtime_evidence_compare(runtime_compare_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    artifact_count = 0
    artifacts_with_diffs_count = 0
    top_level_mismatches_count = 0
    status_count_diffs_count = 0
    runtime_count_diffs_count = 0
    interop_import_status_count_diffs_count = 0
    interop_import_manifest_consistency_diffs_count = 0
    interop_import_manifest_mode_count_diffs_count = 0
    interop_import_export_mode_count_diffs_count = 0
    interop_import_require_manifest_input_count_diffs_count = 0
    interop_import_require_export_input_count_diffs_count = 0
    interop_import_profile_diff_count = 0
    profile_left_only_count = 0
    profile_right_only_count = 0
    shared_profile_count = 0
    profile_diff_count = 0
    label_pair_counts: dict[str, int] = {}
    interop_import_profile_diff_records: list[dict[str, Any]] = []
    interop_import_profile_diff_field_counts: dict[str, int] = {}
    interop_import_profile_diff_numeric_counts: dict[str, int] = {}
    interop_import_profile_diff_label_pair_counts: dict[str, int] = {}
    interop_import_profile_diff_profile_counts: dict[str, int] = {}
    interop_import_profile_diff_numeric_delta_totals: dict[str, float] = {}
    interop_import_profile_diff_numeric_delta_abs_totals: dict[str, float] = {}
    interop_import_profile_diff_numeric_delta_totals_by_label_pair: dict[str, dict[str, float]] = {}
    interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair: dict[str, dict[str, float]] = {}
    interop_import_profile_diff_numeric_delta_totals_by_profile: dict[str, dict[str, float]] = {}
    interop_import_profile_diff_numeric_delta_abs_totals_by_profile: dict[str, dict[str, float]] = {}
    interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile: dict[str, dict[str, float]] = {}
    interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile: dict[str, dict[str, float]] = {}
    interop_import_profile_diff_numeric_delta_records: list[dict[str, Any]] = []
    interop_import_profile_diff_numeric_delta_positive_counts: dict[str, int] = {}
    interop_import_profile_diff_numeric_delta_negative_counts: dict[str, int] = {}
    interop_import_profile_diff_numeric_delta_zero_counts: dict[str, int] = {}

    for artifact in runtime_compare_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_count += 1
        if bool(artifact.get("has_diffs", False)):
            artifacts_with_diffs_count += 1
        try:
            top_level_mismatches_count += max(0, int(artifact.get("top_level_mismatches_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            status_count_diffs_count += max(0, int(artifact.get("status_count_diffs_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            runtime_count_diffs_count += max(0, int(artifact.get("runtime_count_diffs_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            interop_import_status_count_diffs_count += max(
                0,
                int(artifact.get("interop_import_status_count_diffs_count", 0)),
            )
        except (TypeError, ValueError):
            pass
        try:
            interop_import_manifest_consistency_diffs_count += max(
                0,
                int(artifact.get("interop_import_manifest_consistency_diffs_count", 0)),
            )
        except (TypeError, ValueError):
            pass
        try:
            interop_import_manifest_mode_count_diffs_count += max(
                0,
                int(artifact.get("interop_import_manifest_mode_count_diffs_count", 0)),
            )
        except (TypeError, ValueError):
            pass
        try:
            interop_import_export_mode_count_diffs_count += max(
                0,
                int(artifact.get("interop_import_export_mode_count_diffs_count", 0)),
            )
        except (TypeError, ValueError):
            pass
        try:
            interop_import_require_manifest_input_count_diffs_count += max(
                0,
                int(artifact.get("interop_import_require_manifest_input_count_diffs_count", 0)),
            )
        except (TypeError, ValueError):
            pass
        try:
            interop_import_require_export_input_count_diffs_count += max(
                0,
                int(artifact.get("interop_import_require_export_input_count_diffs_count", 0)),
            )
        except (TypeError, ValueError):
            pass
        try:
            interop_import_profile_diff_count += max(
                0,
                int(artifact.get("interop_import_profile_diff_count", 0)),
            )
        except (TypeError, ValueError):
            pass
        try:
            profile_left_only_count += max(0, int(artifact.get("profile_left_only_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            profile_right_only_count += max(0, int(artifact.get("profile_right_only_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            shared_profile_count += max(0, int(artifact.get("shared_profile_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            profile_diff_count += max(0, int(artifact.get("profile_diff_count", 0)))
        except (TypeError, ValueError):
            pass
        left_label = str(artifact.get("left_label", "")).strip() or "left"
        right_label = str(artifact.get("right_label", "")).strip() or "right"
        label_pair_key = f"{left_label}_vs_{right_label}"
        label_pair_counts[label_pair_key] = label_pair_counts.get(label_pair_key, 0) + 1
        records_raw = artifact.get("interop_import_profile_diff_records", [])
        if isinstance(records_raw, list):
            for row in records_raw:
                if not isinstance(row, dict):
                    continue
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
                if not field_keys and not numeric_keys:
                    continue
                interop_import_profile_diff_records.append(
                    {
                        "left_label": left_label,
                        "right_label": right_label,
                        "profile_id": str(row.get("profile_id", "")).strip() or "profile_unknown",
                        "field_keys": sorted(set(field_keys)),
                        "numeric_keys": sorted(set(numeric_keys)),
                    }
                )
                interop_import_profile_diff_label_pair_key = f"{left_label}_vs_{right_label}"
                interop_import_profile_diff_label_pair_counts[interop_import_profile_diff_label_pair_key] = (
                    interop_import_profile_diff_label_pair_counts.get(interop_import_profile_diff_label_pair_key, 0) + 1
                )
                interop_import_profile_id_key = str(row.get("profile_id", "")).strip() or "profile_unknown"
                interop_import_profile_diff_profile_counts[interop_import_profile_id_key] = (
                    interop_import_profile_diff_profile_counts.get(interop_import_profile_id_key, 0) + 1
                )
                for field_key in sorted(set(field_keys)):
                    interop_import_profile_diff_field_counts[field_key] = (
                        interop_import_profile_diff_field_counts.get(field_key, 0) + 1
                    )
                for numeric_key in sorted(set(numeric_keys)):
                    interop_import_profile_diff_numeric_counts[numeric_key] = (
                        interop_import_profile_diff_numeric_counts.get(numeric_key, 0) + 1
                    )
        numeric_delta_totals_raw = artifact.get("interop_import_profile_diff_numeric_delta_totals", {})
        if isinstance(numeric_delta_totals_raw, dict):
            for numeric_key, raw_value in numeric_delta_totals_raw.items():
                numeric_key_text = str(numeric_key).strip()
                if not numeric_key_text:
                    continue
                if raw_value is None or isinstance(raw_value, bool):
                    continue
                try:
                    delta_value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                interop_import_profile_diff_numeric_delta_totals[numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_totals.get(numeric_key_text, 0.0) + delta_value
                )
        numeric_delta_abs_totals_raw = artifact.get("interop_import_profile_diff_numeric_delta_abs_totals", {})
        if isinstance(numeric_delta_abs_totals_raw, dict):
            for numeric_key, raw_value in numeric_delta_abs_totals_raw.items():
                numeric_key_text = str(numeric_key).strip()
                if not numeric_key_text:
                    continue
                if raw_value is None or isinstance(raw_value, bool):
                    continue
                try:
                    delta_value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                interop_import_profile_diff_numeric_delta_abs_totals[numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_abs_totals.get(numeric_key_text, 0.0) + delta_value
                )
        numeric_delta_records_raw = artifact.get("interop_import_profile_diff_numeric_delta_records", [])
        if isinstance(numeric_delta_records_raw, list):
            for row in numeric_delta_records_raw:
                if not isinstance(row, dict):
                    continue
                numeric_key_text = str(row.get("numeric_key", "")).strip()
                if not numeric_key_text:
                    continue
                profile_id = str(row.get("profile_id", "")).strip() or "profile_unknown"
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
                interop_import_profile_diff_numeric_delta_records.append(
                    {
                        "left_label": left_label,
                        "right_label": right_label,
                        "profile_id": profile_id,
                        "numeric_key": numeric_key_text,
                        "delta": float(round(delta_value, 6)),
                        "delta_abs": float(round(abs(delta_abs_value), 6)),
                    }
                )
                interop_import_profile_diff_numeric_delta_totals_by_label_pair.setdefault(
                    label_pair_key,
                    {},
                )
                interop_import_profile_diff_numeric_delta_totals_by_label_pair[
                    label_pair_key
                ][numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_totals_by_label_pair[
                        label_pair_key
                    ].get(numeric_key_text, 0.0)
                    + delta_value
                )
                interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair.setdefault(
                    label_pair_key,
                    {},
                )
                interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair[
                    label_pair_key
                ][numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair[
                        label_pair_key
                    ].get(numeric_key_text, 0.0)
                    + abs(delta_abs_value)
                )
                interop_import_profile_diff_numeric_delta_totals_by_profile.setdefault(
                    profile_id,
                    {},
                )
                interop_import_profile_diff_numeric_delta_totals_by_profile[
                    profile_id
                ][numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_totals_by_profile[
                        profile_id
                    ].get(numeric_key_text, 0.0)
                    + delta_value
                )
                interop_import_profile_diff_numeric_delta_abs_totals_by_profile.setdefault(
                    profile_id,
                    {},
                )
                interop_import_profile_diff_numeric_delta_abs_totals_by_profile[
                    profile_id
                ][numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_abs_totals_by_profile[
                        profile_id
                    ].get(numeric_key_text, 0.0)
                    + abs(delta_abs_value)
                )
                label_pair_profile_key = f"{label_pair_key}|{profile_id}"
                interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile.setdefault(
                    label_pair_profile_key,
                    {},
                )
                interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile[
                    label_pair_profile_key
                ][numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile[
                        label_pair_profile_key
                    ].get(numeric_key_text, 0.0)
                    + delta_value
                )
                interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile.setdefault(
                    label_pair_profile_key,
                    {},
                )
                interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile[
                    label_pair_profile_key
                ][numeric_key_text] = (
                    interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile[
                        label_pair_profile_key
                    ].get(numeric_key_text, 0.0)
                    + abs(delta_abs_value)
                )
                if delta_value > 0.0:
                    interop_import_profile_diff_numeric_delta_positive_counts[numeric_key_text] = (
                        interop_import_profile_diff_numeric_delta_positive_counts.get(numeric_key_text, 0) + 1
                    )
                elif delta_value < 0.0:
                    interop_import_profile_diff_numeric_delta_negative_counts[numeric_key_text] = (
                        interop_import_profile_diff_numeric_delta_negative_counts.get(numeric_key_text, 0) + 1
                    )
                else:
                    interop_import_profile_diff_numeric_delta_zero_counts[numeric_key_text] = (
                        interop_import_profile_diff_numeric_delta_zero_counts.get(numeric_key_text, 0) + 1
                    )

    interop_import_profile_diff_records.sort(
        key=lambda row: (
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
            ",".join(str(item) for item in row.get("field_keys", [])),
            ",".join(str(item) for item in row.get("numeric_keys", [])),
        )
    )
    interop_import_profile_diff_numeric_delta_records.sort(
        key=lambda row: (
            -float(row.get("delta_abs", 0.0)),
            str(row.get("numeric_key", "")),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
            float(row.get("delta", 0.0)),
        )
    )
    interop_import_profile_diff_numeric_delta_hotspots = interop_import_profile_diff_numeric_delta_records[:5]

    def _build_group_hotspot_checklist(
        *,
        recommended_action: str,
        top_numeric_key: str,
        top_positive_numeric_key: str,
        top_negative_numeric_key: str,
    ) -> list[str]:
        numeric_key = top_numeric_key.strip() or "numeric_key"
        positive_key = top_positive_numeric_key.strip() or numeric_key
        negative_key = top_negative_numeric_key.strip() or numeric_key
        if recommended_action == "audit_upward_drift":
            return [
                f"validate_source_counts:{positive_key}",
                f"trace_positive_pipeline:{positive_key}",
            ]
        if recommended_action == "audit_downward_drift":
            return [
                f"validate_sink_counts:{negative_key}",
                f"trace_negative_pipeline:{negative_key}",
            ]
        if recommended_action == "audit_bidirectional_drift":
            return [
                f"reconcile_bidirectional_keys:{numeric_key}",
                "diff_runtime_transform_paths",
            ]
        if recommended_action == "inspect_top_positive_key":
            return [
                f"inspect_top_positive_key:{positive_key}",
                "confirm_profile_contributor",
            ]
        if recommended_action == "inspect_top_negative_key":
            return [
                f"inspect_top_negative_key:{negative_key}",
                "confirm_profile_contributor",
            ]
        if recommended_action == "inspect_bidirectional_keys":
            return [
                f"inspect_top_pair_keys:{numeric_key}",
                "compare_directional_breakdown",
            ]
        if recommended_action == "monitor_zero_delta_noise":
            return [
                "monitor_zero_delta_noise",
                "check_metric_stability",
            ]
        return [
            f"monitor_recurrence:{numeric_key}",
            "escalate_if_priority_increases",
        ]

    interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile: list[dict[str, Any]] = []
    for label_pair_profile_key in sorted(
        interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile.keys()
    ):
        delta_abs_totals_raw = interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile.get(
            label_pair_profile_key,
            {},
        )
        if not isinstance(delta_abs_totals_raw, dict) or not delta_abs_totals_raw:
            continue
        delta_totals_raw = interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile.get(
            label_pair_profile_key,
            {},
        )
        if not isinstance(delta_totals_raw, dict):
            delta_totals_raw = {}
        delta_abs_total = 0.0
        delta_total = 0.0
        numeric_key_count = 0
        positive_delta_abs_total = 0.0
        negative_delta_abs_total = 0.0
        zero_numeric_key_count = 0
        top_numeric_key = ""
        top_numeric_delta_abs = 0.0
        top_numeric_delta = 0.0
        top_positive_numeric_key = ""
        top_positive_delta = 0.0
        top_positive_delta_abs = 0.0
        top_negative_numeric_key = ""
        top_negative_delta = 0.0
        top_negative_delta_abs = 0.0
        for numeric_key_raw in sorted(delta_abs_totals_raw.keys()):
            numeric_key = str(numeric_key_raw).strip()
            if not numeric_key:
                continue
            delta_abs_raw = delta_abs_totals_raw.get(numeric_key_raw)
            if delta_abs_raw is None or isinstance(delta_abs_raw, bool):
                continue
            try:
                delta_abs_value = abs(float(delta_abs_raw))
            except (TypeError, ValueError):
                continue
            delta_raw = delta_totals_raw.get(numeric_key, delta_totals_raw.get(numeric_key_raw, 0.0))
            if delta_raw is None or isinstance(delta_raw, bool):
                delta_value = 0.0
            else:
                try:
                    delta_value = float(delta_raw)
                except (TypeError, ValueError):
                    delta_value = 0.0
            delta_abs_total += delta_abs_value
            delta_total += delta_value
            numeric_key_count += 1
            if delta_value > 0.0:
                positive_delta_abs_total += delta_abs_value
                if (
                    delta_abs_value > top_positive_delta_abs
                    or (
                        abs(delta_abs_value - top_positive_delta_abs) <= 1e-12
                        and (not top_positive_numeric_key or numeric_key < top_positive_numeric_key)
                    )
                ):
                    top_positive_numeric_key = numeric_key
                    top_positive_delta = delta_value
                    top_positive_delta_abs = delta_abs_value
            elif delta_value < 0.0:
                negative_delta_abs_total += delta_abs_value
                if (
                    delta_abs_value > top_negative_delta_abs
                    or (
                        abs(delta_abs_value - top_negative_delta_abs) <= 1e-12
                        and (not top_negative_numeric_key or numeric_key < top_negative_numeric_key)
                    )
                ):
                    top_negative_numeric_key = numeric_key
                    top_negative_delta = delta_value
                    top_negative_delta_abs = delta_abs_value
            else:
                zero_numeric_key_count += 1
            if (
                delta_abs_value > top_numeric_delta_abs
                or (
                    abs(delta_abs_value - top_numeric_delta_abs) <= 1e-12
                    and (not top_numeric_key or numeric_key < top_numeric_key)
                )
            ):
                top_numeric_key = numeric_key
                top_numeric_delta_abs = delta_abs_value
                top_numeric_delta = delta_value
        if numeric_key_count <= 0:
            continue
        label_pair_key_raw, _, profile_id_raw = str(label_pair_profile_key).partition("|")
        label_pair_key = label_pair_key_raw.strip()
        profile_id = profile_id_raw.strip() or "profile_unknown"
        left_label = "left"
        right_label = "right"
        if "_vs_" in label_pair_key:
            left_candidate, right_candidate = label_pair_key.split("_vs_", 1)
            left_label = left_candidate.strip() or "left"
            right_label = right_candidate.strip() or "right"
        elif label_pair_key:
            left_label = label_pair_key
        if delta_abs_total <= 0.0:
            imbalance_ratio = 0.0
        else:
            imbalance_ratio = abs(positive_delta_abs_total - negative_delta_abs_total) / delta_abs_total
        priority_score = (delta_abs_total * (1.0 + imbalance_ratio)) + (0.1 * float(numeric_key_count))
        if priority_score >= 5.0:
            priority_bucket = "high"
        elif priority_score >= 2.0:
            priority_bucket = "medium"
        else:
            priority_bucket = "low"
        if positive_delta_abs_total > negative_delta_abs_total + 1e-12:
            dominant_direction = "positive"
        elif negative_delta_abs_total > positive_delta_abs_total + 1e-12:
            dominant_direction = "negative"
        else:
            dominant_direction = "balanced"
        if priority_bucket == "high":
            if dominant_direction == "positive":
                recommended_action = "audit_upward_drift"
                recommended_reason = "high_priority_positive_imbalance"
            elif dominant_direction == "negative":
                recommended_action = "audit_downward_drift"
                recommended_reason = "high_priority_negative_imbalance"
            else:
                recommended_action = "audit_bidirectional_drift"
                recommended_reason = "high_priority_balanced_drift"
        elif priority_bucket == "medium":
            if dominant_direction == "positive":
                recommended_action = "inspect_top_positive_key"
                recommended_reason = "medium_priority_positive_drift"
            elif dominant_direction == "negative":
                recommended_action = "inspect_top_negative_key"
                recommended_reason = "medium_priority_negative_drift"
            else:
                recommended_action = "inspect_bidirectional_keys"
                recommended_reason = "medium_priority_balanced_drift"
        elif zero_numeric_key_count >= numeric_key_count:
            recommended_action = "monitor_zero_delta_noise"
            recommended_reason = "low_priority_zero_deltas"
        else:
            recommended_action = "monitor_for_recurrence"
            recommended_reason = "low_priority_drift"
        recommended_checklist = _build_group_hotspot_checklist(
            recommended_action=recommended_action,
            top_numeric_key=top_numeric_key,
            top_positive_numeric_key=top_positive_numeric_key,
            top_negative_numeric_key=top_negative_numeric_key,
        )
        interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile.append(
            {
                "left_label": left_label,
                "right_label": right_label,
                "profile_id": profile_id,
                "numeric_key_count": int(numeric_key_count),
                "delta_total": float(round(delta_total, 6)),
                "delta_abs_total": float(round(delta_abs_total, 6)),
                "positive_delta_abs_total": float(round(positive_delta_abs_total, 6)),
                "negative_delta_abs_total": float(round(negative_delta_abs_total, 6)),
                "zero_numeric_key_count": int(zero_numeric_key_count),
                "direction_imbalance_ratio": float(round(imbalance_ratio, 6)),
                "dominant_direction": dominant_direction,
                "priority_score": float(round(priority_score, 6)),
                "priority_bucket": priority_bucket,
                "recommended_action": recommended_action,
                "recommended_reason": recommended_reason,
                "recommended_checklist": recommended_checklist,
                "top_numeric_key": top_numeric_key,
                "top_numeric_delta": float(round(top_numeric_delta, 6)),
                "top_numeric_delta_abs": float(round(top_numeric_delta_abs, 6)),
                "top_positive_numeric_key": top_positive_numeric_key,
                "top_positive_delta": float(round(top_positive_delta, 6)),
                "top_negative_numeric_key": top_negative_numeric_key,
                "top_negative_delta": float(round(top_negative_delta, 6)),
            }
        )
    interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile.sort(
        key=lambda row: (
            -float(row.get("priority_score", 0.0)),
            -float(row.get("delta_abs_total", 0.0)),
            -int(row.get("numeric_key_count", 0)),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
        )
    )
    interop_import_profile_diff_numeric_delta_hotspot_priority_counts: dict[str, int] = {}
    interop_import_profile_diff_numeric_delta_hotspot_action_counts: dict[str, int] = {}
    interop_import_profile_diff_numeric_delta_hotspot_reason_counts: dict[str, int] = {}
    for row in interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile:
        if not isinstance(row, dict):
            continue
        priority_bucket = str(row.get("priority_bucket", "")).strip().lower()
        if priority_bucket:
            interop_import_profile_diff_numeric_delta_hotspot_priority_counts[priority_bucket] = (
                interop_import_profile_diff_numeric_delta_hotspot_priority_counts.get(priority_bucket, 0) + 1
            )
        recommended_action = str(row.get("recommended_action", "")).strip()
        if recommended_action:
            interop_import_profile_diff_numeric_delta_hotspot_action_counts[recommended_action] = (
                interop_import_profile_diff_numeric_delta_hotspot_action_counts.get(recommended_action, 0) + 1
            )
        recommended_reason = str(row.get("recommended_reason", "")).strip()
        if recommended_reason:
            interop_import_profile_diff_numeric_delta_hotspot_reason_counts[recommended_reason] = (
                interop_import_profile_diff_numeric_delta_hotspot_reason_counts.get(recommended_reason, 0) + 1
            )
    interop_import_profile_diff_numeric_delta_key_max_positive_records_by_key: dict[str, dict[str, Any]] = {}
    interop_import_profile_diff_numeric_delta_key_max_negative_records_by_key: dict[str, dict[str, Any]] = {}
    for row in interop_import_profile_diff_numeric_delta_records:
        if not isinstance(row, dict):
            continue
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
        normalized_row = {
            "left_label": str(row.get("left_label", "")).strip() or "left",
            "right_label": str(row.get("right_label", "")).strip() or "right",
            "profile_id": str(row.get("profile_id", "")).strip() or "profile_unknown",
            "numeric_key": numeric_key,
            "delta": float(round(delta_value, 6)),
            "delta_abs": float(round(abs(delta_value), 6)),
        }
        if delta_value > 0.0:
            existing = interop_import_profile_diff_numeric_delta_key_max_positive_records_by_key.get(numeric_key)
            if not isinstance(existing, dict) or float(existing.get("delta", 0.0)) < delta_value:
                interop_import_profile_diff_numeric_delta_key_max_positive_records_by_key[numeric_key] = normalized_row
        elif delta_value < 0.0:
            existing = interop_import_profile_diff_numeric_delta_key_max_negative_records_by_key.get(numeric_key)
            if not isinstance(existing, dict) or float(existing.get("delta", 0.0)) > delta_value:
                interop_import_profile_diff_numeric_delta_key_max_negative_records_by_key[numeric_key] = normalized_row
    interop_import_profile_diff_numeric_delta_key_max_positive_records = sorted(
        interop_import_profile_diff_numeric_delta_key_max_positive_records_by_key.values(),
        key=lambda row: (
            -abs(float(row.get("delta", 0.0))),
            str(row.get("numeric_key", "")),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
        ),
    )
    interop_import_profile_diff_numeric_delta_key_max_negative_records = sorted(
        interop_import_profile_diff_numeric_delta_key_max_negative_records_by_key.values(),
        key=lambda row: (
            -abs(float(row.get("delta", 0.0))),
            str(row.get("numeric_key", "")),
            str(row.get("left_label", "")),
            str(row.get("right_label", "")),
            str(row.get("profile_id", "")),
        ),
    )

    return {
        "artifact_count": artifact_count,
        "artifacts_with_diffs_count": artifacts_with_diffs_count,
        "artifacts_without_diffs_count": max(0, artifact_count - artifacts_with_diffs_count),
        "top_level_mismatches_count": top_level_mismatches_count,
        "status_count_diffs_count": status_count_diffs_count,
        "runtime_count_diffs_count": runtime_count_diffs_count,
        "interop_import_status_count_diffs_count": interop_import_status_count_diffs_count,
        "interop_import_manifest_consistency_diffs_count": interop_import_manifest_consistency_diffs_count,
        "interop_import_manifest_mode_count_diffs_count": interop_import_manifest_mode_count_diffs_count,
        "interop_import_export_mode_count_diffs_count": interop_import_export_mode_count_diffs_count,
        "interop_import_require_manifest_input_count_diffs_count": (
            interop_import_require_manifest_input_count_diffs_count
        ),
        "interop_import_require_export_input_count_diffs_count": (
            interop_import_require_export_input_count_diffs_count
        ),
        "interop_import_profile_diff_count": interop_import_profile_diff_count,
        "interop_import_profile_diff_records": interop_import_profile_diff_records,
        "interop_import_profile_diff_field_counts": {
            key: interop_import_profile_diff_field_counts[key]
            for key in sorted(interop_import_profile_diff_field_counts.keys())
        },
        "interop_import_profile_diff_numeric_counts": {
            key: interop_import_profile_diff_numeric_counts[key]
            for key in sorted(interop_import_profile_diff_numeric_counts.keys())
        },
        "interop_import_profile_diff_label_pair_counts": {
            key: interop_import_profile_diff_label_pair_counts[key]
            for key in sorted(interop_import_profile_diff_label_pair_counts.keys())
        },
        "interop_import_profile_diff_profile_counts": {
            key: interop_import_profile_diff_profile_counts[key]
            for key in sorted(interop_import_profile_diff_profile_counts.keys())
        },
        "interop_import_profile_diff_numeric_delta_totals": {
            key: float(round(interop_import_profile_diff_numeric_delta_totals[key], 6))
            for key in sorted(interop_import_profile_diff_numeric_delta_totals.keys())
        },
        "interop_import_profile_diff_numeric_delta_abs_totals": {
            key: float(round(interop_import_profile_diff_numeric_delta_abs_totals[key], 6))
            for key in sorted(interop_import_profile_diff_numeric_delta_abs_totals.keys())
        },
        "interop_import_profile_diff_numeric_delta_totals_by_label_pair": {
            label_pair: {
                numeric_key: float(
                    round(
                        interop_import_profile_diff_numeric_delta_totals_by_label_pair[label_pair][numeric_key],
                        6,
                    )
                )
                for numeric_key in sorted(
                    interop_import_profile_diff_numeric_delta_totals_by_label_pair[label_pair].keys()
                )
            }
            for label_pair in sorted(interop_import_profile_diff_numeric_delta_totals_by_label_pair.keys())
        },
        "interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair": {
            label_pair: {
                numeric_key: float(
                    round(
                        interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair[label_pair][numeric_key],
                        6,
                    )
                )
                for numeric_key in sorted(
                    interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair[label_pair].keys()
                )
            }
            for label_pair in sorted(interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair.keys())
        },
        "interop_import_profile_diff_numeric_delta_totals_by_profile": {
            profile_id: {
                numeric_key: float(
                    round(
                        interop_import_profile_diff_numeric_delta_totals_by_profile[profile_id][numeric_key],
                        6,
                    )
                )
                for numeric_key in sorted(
                    interop_import_profile_diff_numeric_delta_totals_by_profile[profile_id].keys()
                )
            }
            for profile_id in sorted(interop_import_profile_diff_numeric_delta_totals_by_profile.keys())
        },
        "interop_import_profile_diff_numeric_delta_abs_totals_by_profile": {
            profile_id: {
                numeric_key: float(
                    round(
                        interop_import_profile_diff_numeric_delta_abs_totals_by_profile[profile_id][numeric_key],
                        6,
                    )
                )
                for numeric_key in sorted(
                    interop_import_profile_diff_numeric_delta_abs_totals_by_profile[profile_id].keys()
                )
            }
            for profile_id in sorted(interop_import_profile_diff_numeric_delta_abs_totals_by_profile.keys())
        },
        "interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile": {
            label_pair_profile_key: {
                numeric_key: float(
                    round(
                        interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile[label_pair_profile_key][
                            numeric_key
                        ],
                        6,
                    )
                )
                for numeric_key in sorted(
                    interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile[label_pair_profile_key].keys()
                )
            }
            for label_pair_profile_key in sorted(
                interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile.keys()
            )
        },
        "interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile": {
            label_pair_profile_key: {
                numeric_key: float(
                    round(
                        interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile[
                            label_pair_profile_key
                        ][numeric_key],
                        6,
                    )
                )
                for numeric_key in sorted(
                    interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile[
                        label_pair_profile_key
                    ].keys()
                )
            }
            for label_pair_profile_key in sorted(
                interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile.keys()
            )
        },
        "interop_import_profile_diff_numeric_delta_positive_counts": {
            key: interop_import_profile_diff_numeric_delta_positive_counts[key]
            for key in sorted(interop_import_profile_diff_numeric_delta_positive_counts.keys())
        },
        "interop_import_profile_diff_numeric_delta_negative_counts": {
            key: interop_import_profile_diff_numeric_delta_negative_counts[key]
            for key in sorted(interop_import_profile_diff_numeric_delta_negative_counts.keys())
        },
        "interop_import_profile_diff_numeric_delta_zero_counts": {
            key: interop_import_profile_diff_numeric_delta_zero_counts[key]
            for key in sorted(interop_import_profile_diff_numeric_delta_zero_counts.keys())
        },
        "interop_import_profile_diff_numeric_delta_record_count": len(
            interop_import_profile_diff_numeric_delta_records
        ),
        "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count": len(
            interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile
        ),
        "interop_import_profile_diff_numeric_delta_hotspot_priority_counts": {
            key: interop_import_profile_diff_numeric_delta_hotspot_priority_counts[key]
            for key in sorted(interop_import_profile_diff_numeric_delta_hotspot_priority_counts.keys())
        },
        "interop_import_profile_diff_numeric_delta_hotspot_action_counts": {
            key: interop_import_profile_diff_numeric_delta_hotspot_action_counts[key]
            for key in sorted(interop_import_profile_diff_numeric_delta_hotspot_action_counts.keys())
        },
        "interop_import_profile_diff_numeric_delta_hotspot_reason_counts": {
            key: interop_import_profile_diff_numeric_delta_hotspot_reason_counts[key]
            for key in sorted(interop_import_profile_diff_numeric_delta_hotspot_reason_counts.keys())
        },
        "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile": (
            interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile[:5]
        ),
        "interop_import_profile_diff_numeric_delta_hotspots": (
            interop_import_profile_diff_numeric_delta_hotspots
        ),
        "interop_import_profile_diff_numeric_delta_key_max_positive_records": (
            interop_import_profile_diff_numeric_delta_key_max_positive_records
        ),
        "interop_import_profile_diff_numeric_delta_key_max_negative_records": (
            interop_import_profile_diff_numeric_delta_key_max_negative_records
        ),
        "profile_left_only_count": profile_left_only_count,
        "profile_right_only_count": profile_right_only_count,
        "shared_profile_count": shared_profile_count,
        "profile_diff_count": profile_diff_count,
        "label_pair_counts": {
            key: label_pair_counts[key]
            for key in sorted(label_pair_counts.keys())
        },
    }


def summarize_phase4_secondary_coverage(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        batch_id = str(manifest.get("batch_id", "")).strip() or "batch_unknown"
        module_count_raw = manifest.get("phase4_reference_secondary_module_count", 0)
        coverage_raw = manifest.get("phase4_reference_secondary_total_coverage_ratio", 0.0)
        try:
            module_count = int(module_count_raw)
        except (TypeError, ValueError):
            module_count = 0
        try:
            coverage_ratio = float(coverage_raw)
        except (TypeError, ValueError):
            coverage_ratio = 0.0
        normalized_rows.append(
            {
                "batch_id": batch_id,
                "secondary_module_count": module_count,
                "secondary_coverage_ratio": coverage_ratio,
                "secondary_module_coverage": manifest.get("phase4_reference_secondary_module_coverage", {}),
            }
        )

    eligible_rows = [row for row in normalized_rows if int(row.get("secondary_module_count", 0)) > 0]
    if not eligible_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "min_coverage_ratio": None,
            "max_coverage_ratio": None,
            "avg_coverage_ratio": None,
            "lowest_batch_id": "",
            "highest_batch_id": "",
            "lowest_batch_secondary_module_count": None,
            "secondary_coverage_by_min_modules": {},
            "module_coverage_summary": {},
        }

    lowest_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("secondary_coverage_ratio", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("secondary_coverage_ratio", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    coverage_values = [float(row.get("secondary_coverage_ratio", 0.0)) for row in eligible_rows]
    average_coverage = sum(coverage_values) / float(len(coverage_values))
    max_secondary_module_count = max(int(row.get("secondary_module_count", 0)) for row in eligible_rows)
    secondary_coverage_by_min_modules: dict[str, Any] = {}
    for min_modules in range(1, max_secondary_module_count + 1):
        candidate_rows = [
            row for row in eligible_rows if int(row.get("secondary_module_count", 0)) >= min_modules
        ]
        if not candidate_rows:
            continue
        lowest_candidate_row = min(
            candidate_rows,
            key=lambda row: (
                float(row.get("secondary_coverage_ratio", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        secondary_coverage_by_min_modules[str(min_modules)] = {
            "evaluated_manifest_count": len(candidate_rows),
            "min_coverage_ratio": float(lowest_candidate_row.get("secondary_coverage_ratio", 0.0)),
            "lowest_batch_id": str(lowest_candidate_row.get("batch_id", "")),
            "lowest_batch_secondary_module_count": int(lowest_candidate_row.get("secondary_module_count", 0)),
        }
    module_samples: dict[str, list[dict[str, Any]]] = {}
    for row in eligible_rows:
        batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
        module_coverage_raw = row.get("secondary_module_coverage", {})
        if not isinstance(module_coverage_raw, dict):
            continue
        for module_name_raw, module_coverage_raw in module_coverage_raw.items():
            module_name = str(module_name_raw).strip().lower()
            if not module_name:
                continue
            try:
                module_coverage_ratio = float(module_coverage_raw)
            except (TypeError, ValueError):
                module_coverage_ratio = 0.0
            module_samples.setdefault(module_name, []).append(
                {
                    "batch_id": batch_id,
                    "coverage_ratio": module_coverage_ratio,
                }
            )

    module_coverage_summary: dict[str, Any] = {}
    for module_name in sorted(module_samples.keys()):
        rows = module_samples[module_name]
        if not rows:
            continue
        min_row = min(
            rows,
            key=lambda row: (
                float(row.get("coverage_ratio", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        max_row = max(
            rows,
            key=lambda row: (
                float(row.get("coverage_ratio", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        values = [float(row.get("coverage_ratio", 0.0)) for row in rows]
        module_coverage_summary[module_name] = {
            "sample_count": len(rows),
            "min_coverage_ratio": float(min_row.get("coverage_ratio", 0.0)),
            "avg_coverage_ratio": float(sum(values) / float(len(values))),
            "max_coverage_ratio": float(max_row.get("coverage_ratio", 0.0)),
            "lowest_batch_id": str(min_row.get("batch_id", "")),
            "highest_batch_id": str(max_row.get("batch_id", "")),
        }

    return {
        "evaluated_manifest_count": len(eligible_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "min_coverage_ratio": float(lowest_row.get("secondary_coverage_ratio", 0.0)),
        "max_coverage_ratio": float(highest_row.get("secondary_coverage_ratio", 0.0)),
        "avg_coverage_ratio": float(average_coverage),
        "lowest_batch_id": str(lowest_row.get("batch_id", "")),
        "highest_batch_id": str(highest_row.get("batch_id", "")),
        "lowest_batch_secondary_module_count": int(lowest_row.get("secondary_module_count", 0)),
        "secondary_coverage_by_min_modules": secondary_coverage_by_min_modules,
        "module_coverage_summary": module_coverage_summary,
    }


def summarize_phase4_primary_coverage(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        batch_id = str(manifest.get("batch_id", "")).strip() or "batch_unknown"
        coverage_raw = manifest.get("phase4_reference_primary_total_coverage_ratio", 0.0)
        try:
            coverage_ratio = float(coverage_raw)
        except (TypeError, ValueError):
            coverage_ratio = 0.0
        module_coverage_raw = manifest.get("phase4_reference_primary_module_coverage", {})
        module_coverage: dict[str, float] = {}
        if isinstance(module_coverage_raw, dict):
            for module_name_raw, module_coverage_value_raw in module_coverage_raw.items():
                module_name = str(module_name_raw).strip().lower()
                if not module_name:
                    continue
                try:
                    module_coverage[module_name] = float(module_coverage_value_raw)
                except (TypeError, ValueError):
                    module_coverage[module_name] = 0.0
        normalized_rows.append(
            {
                "batch_id": batch_id,
                "primary_coverage_ratio": coverage_ratio,
                "primary_module_coverage": module_coverage,
            }
        )

    eligible_rows = [
        row
        for row in normalized_rows
        if row.get("primary_module_coverage") or float(row.get("primary_coverage_ratio", 0.0)) > 0.0
    ]
    if not eligible_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "min_coverage_ratio": None,
            "max_coverage_ratio": None,
            "avg_coverage_ratio": None,
            "lowest_batch_id": "",
            "highest_batch_id": "",
            "module_coverage_summary": {},
        }

    lowest_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("primary_coverage_ratio", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("primary_coverage_ratio", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    coverage_values = [float(row.get("primary_coverage_ratio", 0.0)) for row in eligible_rows]
    average_coverage = sum(coverage_values) / float(len(coverage_values))
    module_samples: dict[str, list[dict[str, Any]]] = {}
    for row in eligible_rows:
        batch_id = str(row.get("batch_id", "")).strip() or "batch_unknown"
        module_coverage_raw = row.get("primary_module_coverage", {})
        if not isinstance(module_coverage_raw, dict):
            continue
        for module_name_raw, module_coverage_raw in module_coverage_raw.items():
            module_name = str(module_name_raw).strip().lower()
            if not module_name:
                continue
            try:
                module_coverage_ratio = float(module_coverage_raw)
            except (TypeError, ValueError):
                module_coverage_ratio = 0.0
            module_samples.setdefault(module_name, []).append(
                {
                    "batch_id": batch_id,
                    "coverage_ratio": module_coverage_ratio,
                }
            )

    module_coverage_summary: dict[str, Any] = {}
    for module_name in sorted(module_samples.keys()):
        rows = module_samples[module_name]
        if not rows:
            continue
        min_row = min(
            rows,
            key=lambda row: (
                float(row.get("coverage_ratio", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        max_row = max(
            rows,
            key=lambda row: (
                float(row.get("coverage_ratio", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        values = [float(row.get("coverage_ratio", 0.0)) for row in rows]
        module_coverage_summary[module_name] = {
            "sample_count": len(rows),
            "min_coverage_ratio": float(min_row.get("coverage_ratio", 0.0)),
            "avg_coverage_ratio": float(sum(values) / float(len(values))),
            "max_coverage_ratio": float(max_row.get("coverage_ratio", 0.0)),
            "lowest_batch_id": str(min_row.get("batch_id", "")),
            "highest_batch_id": str(max_row.get("batch_id", "")),
        }

    return {
        "evaluated_manifest_count": len(eligible_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "min_coverage_ratio": float(lowest_row.get("primary_coverage_ratio", 0.0)),
        "max_coverage_ratio": float(highest_row.get("primary_coverage_ratio", 0.0)),
        "avg_coverage_ratio": float(average_coverage),
        "lowest_batch_id": str(lowest_row.get("batch_id", "")),
        "highest_batch_id": str(highest_row.get("batch_id", "")),
        "module_coverage_summary": module_coverage_summary,
    }


def summarize_phase3_vehicle_dynamics(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    model_values: set[str] = set()
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        batch_id = str(manifest.get("batch_id", "")).strip() or "batch_unknown"
        model = str(manifest.get("phase3_vehicle_dynamics_model", "")).strip()
        step_count_raw = manifest.get("phase3_vehicle_dynamics_step_count", 0)
        initial_speed_raw = manifest.get("phase3_vehicle_dynamics_initial_speed_mps", 0.0)
        initial_position_raw = manifest.get("phase3_vehicle_dynamics_initial_position_m", 0.0)
        initial_heading_raw = manifest.get("phase3_vehicle_dynamics_initial_heading_deg", 0.0)
        initial_lateral_position_raw = manifest.get("phase3_vehicle_dynamics_initial_lateral_position_m", 0.0)
        initial_lateral_velocity_raw = manifest.get("phase3_vehicle_dynamics_initial_lateral_velocity_mps", 0.0)
        initial_yaw_rate_raw = manifest.get("phase3_vehicle_dynamics_initial_yaw_rate_rps", 0.0)
        final_speed_raw = manifest.get("phase3_vehicle_dynamics_final_speed_mps", 0.0)
        final_position_raw = manifest.get("phase3_vehicle_dynamics_final_position_m", 0.0)
        final_heading_raw = manifest.get("phase3_vehicle_dynamics_final_heading_deg", 0.0)
        final_lateral_position_raw = manifest.get("phase3_vehicle_dynamics_final_lateral_position_m", 0.0)
        final_lateral_velocity_raw = manifest.get("phase3_vehicle_dynamics_final_lateral_velocity_mps", 0.0)
        final_yaw_rate_raw = manifest.get("phase3_vehicle_dynamics_final_yaw_rate_rps", 0.0)
        min_heading_raw = manifest.get("phase3_vehicle_dynamics_min_heading_deg", 0.0)
        avg_heading_raw = manifest.get("phase3_vehicle_dynamics_avg_heading_deg", 0.0)
        max_heading_raw = manifest.get("phase3_vehicle_dynamics_max_heading_deg", 0.0)
        min_lateral_position_raw = manifest.get("phase3_vehicle_dynamics_min_lateral_position_m", 0.0)
        avg_lateral_position_raw = manifest.get("phase3_vehicle_dynamics_avg_lateral_position_m", 0.0)
        max_lateral_position_raw = manifest.get("phase3_vehicle_dynamics_max_lateral_position_m", 0.0)
        max_abs_lateral_position_raw = manifest.get("phase3_vehicle_dynamics_max_abs_lateral_position_m", 0.0)
        max_abs_yaw_rate_raw = manifest.get("phase3_vehicle_dynamics_max_abs_yaw_rate_rps", 0.0)
        max_abs_lateral_velocity_raw = manifest.get("phase3_vehicle_dynamics_max_abs_lateral_velocity_mps", 0.0)
        max_abs_accel_raw = manifest.get("phase3_vehicle_dynamics_max_abs_accel_mps2", 0.0)
        max_abs_lateral_accel_raw = manifest.get("phase3_vehicle_dynamics_max_abs_lateral_accel_mps2", 0.0)
        max_abs_yaw_accel_raw = manifest.get("phase3_vehicle_dynamics_max_abs_yaw_accel_rps2", 0.0)
        max_abs_jerk_raw = manifest.get("phase3_vehicle_dynamics_max_abs_jerk_mps3", 0.0)
        max_abs_lateral_jerk_raw = manifest.get("phase3_vehicle_dynamics_max_abs_lateral_jerk_mps3", 0.0)
        max_abs_yaw_jerk_raw = manifest.get("phase3_vehicle_dynamics_max_abs_yaw_jerk_rps3", 0.0)
        planar_enabled = bool(manifest.get("phase3_vehicle_dynamics_planar_kinematics_enabled", False))
        dynamic_enabled = bool(manifest.get("phase3_vehicle_dynamics_dynamic_bicycle_enabled", False))
        min_road_grade_raw = manifest.get("phase3_vehicle_dynamics_min_road_grade_percent", 0.0)
        avg_road_grade_raw = manifest.get("phase3_vehicle_dynamics_avg_road_grade_percent", 0.0)
        max_road_grade_raw = manifest.get("phase3_vehicle_dynamics_max_road_grade_percent", 0.0)
        max_abs_grade_force_raw = manifest.get("phase3_vehicle_dynamics_max_abs_grade_force_n", 0.0)
        control_command_step_count_raw = manifest.get("phase3_vehicle_control_command_step_count", 0)
        control_throttle_brake_overlap_step_count_raw = manifest.get(
            "phase3_vehicle_control_throttle_brake_overlap_step_count",
            0,
        )
        control_throttle_brake_overlap_ratio_raw = manifest.get("phase3_vehicle_control_throttle_brake_overlap_ratio", 0.0)
        control_max_abs_steering_rate_degps_raw = manifest.get("phase3_vehicle_control_max_abs_steering_rate_degps", 0.0)
        control_max_abs_throttle_rate_per_sec_raw = manifest.get("phase3_vehicle_control_max_abs_throttle_rate_per_sec", 0.0)
        control_max_abs_brake_rate_per_sec_raw = manifest.get("phase3_vehicle_control_max_abs_brake_rate_per_sec", 0.0)
        control_max_throttle_plus_brake_raw = manifest.get("phase3_vehicle_control_max_throttle_plus_brake", 0.0)
        speed_tracking_target_step_count_raw = manifest.get("phase3_vehicle_speed_tracking_target_step_count", 0)
        speed_tracking_error_mps_min_raw = manifest.get("phase3_vehicle_speed_tracking_error_mps_min", 0.0)
        speed_tracking_error_mps_avg_raw = manifest.get("phase3_vehicle_speed_tracking_error_mps_avg", 0.0)
        speed_tracking_error_mps_max_raw = manifest.get("phase3_vehicle_speed_tracking_error_mps_max", 0.0)
        speed_tracking_error_abs_mps_avg_raw = manifest.get("phase3_vehicle_speed_tracking_error_abs_mps_avg", 0.0)
        speed_tracking_error_abs_mps_max_raw = manifest.get("phase3_vehicle_speed_tracking_error_abs_mps_max", 0.0)
        try:
            step_count = int(step_count_raw)
        except (TypeError, ValueError):
            step_count = 0
        try:
            initial_speed_mps = float(initial_speed_raw)
        except (TypeError, ValueError):
            initial_speed_mps = 0.0
        try:
            initial_position_m = float(initial_position_raw)
        except (TypeError, ValueError):
            initial_position_m = 0.0
        try:
            initial_heading_deg = float(initial_heading_raw)
        except (TypeError, ValueError):
            initial_heading_deg = 0.0
        try:
            initial_lateral_position_m = float(initial_lateral_position_raw)
        except (TypeError, ValueError):
            initial_lateral_position_m = 0.0
        try:
            initial_lateral_velocity_mps = float(initial_lateral_velocity_raw)
        except (TypeError, ValueError):
            initial_lateral_velocity_mps = 0.0
        try:
            initial_yaw_rate_rps = float(initial_yaw_rate_raw)
        except (TypeError, ValueError):
            initial_yaw_rate_rps = 0.0
        try:
            final_speed_mps = float(final_speed_raw)
        except (TypeError, ValueError):
            final_speed_mps = 0.0
        try:
            final_position_m = float(final_position_raw)
        except (TypeError, ValueError):
            final_position_m = 0.0
        try:
            final_heading_deg = float(final_heading_raw)
        except (TypeError, ValueError):
            final_heading_deg = 0.0
        try:
            final_lateral_position_m = float(final_lateral_position_raw)
        except (TypeError, ValueError):
            final_lateral_position_m = 0.0
        try:
            final_lateral_velocity_mps = float(final_lateral_velocity_raw)
        except (TypeError, ValueError):
            final_lateral_velocity_mps = 0.0
        try:
            final_yaw_rate_rps = float(final_yaw_rate_raw)
        except (TypeError, ValueError):
            final_yaw_rate_rps = 0.0
        try:
            min_heading_deg = float(min_heading_raw)
        except (TypeError, ValueError):
            min_heading_deg = 0.0
        try:
            avg_heading_deg = float(avg_heading_raw)
        except (TypeError, ValueError):
            avg_heading_deg = 0.0
        try:
            max_heading_deg = float(max_heading_raw)
        except (TypeError, ValueError):
            max_heading_deg = 0.0
        try:
            min_lateral_position_m = float(min_lateral_position_raw)
        except (TypeError, ValueError):
            min_lateral_position_m = 0.0
        try:
            avg_lateral_position_m = float(avg_lateral_position_raw)
        except (TypeError, ValueError):
            avg_lateral_position_m = 0.0
        try:
            max_lateral_position_m = float(max_lateral_position_raw)
        except (TypeError, ValueError):
            max_lateral_position_m = 0.0
        try:
            max_abs_lateral_position_m = float(max_abs_lateral_position_raw)
        except (TypeError, ValueError):
            max_abs_lateral_position_m = 0.0
        try:
            max_abs_yaw_rate_rps = float(max_abs_yaw_rate_raw)
        except (TypeError, ValueError):
            max_abs_yaw_rate_rps = 0.0
        try:
            max_abs_lateral_velocity_mps = float(max_abs_lateral_velocity_raw)
        except (TypeError, ValueError):
            max_abs_lateral_velocity_mps = 0.0
        try:
            max_abs_accel_mps2 = float(max_abs_accel_raw)
        except (TypeError, ValueError):
            max_abs_accel_mps2 = 0.0
        try:
            max_abs_lateral_accel_mps2 = float(max_abs_lateral_accel_raw)
        except (TypeError, ValueError):
            max_abs_lateral_accel_mps2 = 0.0
        try:
            max_abs_yaw_accel_rps2 = float(max_abs_yaw_accel_raw)
        except (TypeError, ValueError):
            max_abs_yaw_accel_rps2 = 0.0
        try:
            max_abs_jerk_mps3 = float(max_abs_jerk_raw)
        except (TypeError, ValueError):
            max_abs_jerk_mps3 = 0.0
        try:
            max_abs_lateral_jerk_mps3 = float(max_abs_lateral_jerk_raw)
        except (TypeError, ValueError):
            max_abs_lateral_jerk_mps3 = 0.0
        try:
            max_abs_yaw_jerk_rps3 = float(max_abs_yaw_jerk_raw)
        except (TypeError, ValueError):
            max_abs_yaw_jerk_rps3 = 0.0
        try:
            min_road_grade_percent = float(min_road_grade_raw)
        except (TypeError, ValueError):
            min_road_grade_percent = 0.0
        try:
            avg_road_grade_percent = float(avg_road_grade_raw)
        except (TypeError, ValueError):
            avg_road_grade_percent = 0.0
        try:
            max_road_grade_percent = float(max_road_grade_raw)
        except (TypeError, ValueError):
            max_road_grade_percent = 0.0
        try:
            max_abs_grade_force_n = float(max_abs_grade_force_raw)
        except (TypeError, ValueError):
            max_abs_grade_force_n = 0.0
        try:
            control_command_step_count = int(control_command_step_count_raw)
        except (TypeError, ValueError):
            control_command_step_count = 0
        try:
            control_throttle_brake_overlap_step_count = int(control_throttle_brake_overlap_step_count_raw)
        except (TypeError, ValueError):
            control_throttle_brake_overlap_step_count = 0
        try:
            control_throttle_brake_overlap_ratio = float(control_throttle_brake_overlap_ratio_raw)
        except (TypeError, ValueError):
            control_throttle_brake_overlap_ratio = 0.0
        try:
            control_max_abs_steering_rate_degps = float(control_max_abs_steering_rate_degps_raw)
        except (TypeError, ValueError):
            control_max_abs_steering_rate_degps = 0.0
        try:
            control_max_abs_throttle_rate_per_sec = float(control_max_abs_throttle_rate_per_sec_raw)
        except (TypeError, ValueError):
            control_max_abs_throttle_rate_per_sec = 0.0
        try:
            control_max_abs_brake_rate_per_sec = float(control_max_abs_brake_rate_per_sec_raw)
        except (TypeError, ValueError):
            control_max_abs_brake_rate_per_sec = 0.0
        try:
            control_max_throttle_plus_brake = float(control_max_throttle_plus_brake_raw)
        except (TypeError, ValueError):
            control_max_throttle_plus_brake = 0.0
        try:
            speed_tracking_target_step_count = int(speed_tracking_target_step_count_raw)
        except (TypeError, ValueError):
            speed_tracking_target_step_count = 0
        try:
            speed_tracking_error_mps_min = float(speed_tracking_error_mps_min_raw)
        except (TypeError, ValueError):
            speed_tracking_error_mps_min = 0.0
        try:
            speed_tracking_error_mps_avg = float(speed_tracking_error_mps_avg_raw)
        except (TypeError, ValueError):
            speed_tracking_error_mps_avg = 0.0
        try:
            speed_tracking_error_mps_max = float(speed_tracking_error_mps_max_raw)
        except (TypeError, ValueError):
            speed_tracking_error_mps_max = 0.0
        try:
            speed_tracking_error_abs_mps_avg = float(speed_tracking_error_abs_mps_avg_raw)
        except (TypeError, ValueError):
            speed_tracking_error_abs_mps_avg = 0.0
        try:
            speed_tracking_error_abs_mps_max = float(speed_tracking_error_abs_mps_max_raw)
        except (TypeError, ValueError):
            speed_tracking_error_abs_mps_max = 0.0
        normalized_rows.append(
            {
                "batch_id": batch_id,
                "model": model,
                "step_count": step_count,
                "planar_kinematics_enabled": planar_enabled,
                "dynamic_bicycle_enabled": dynamic_enabled,
                "initial_speed_mps": initial_speed_mps,
                "initial_position_m": initial_position_m,
                "initial_heading_deg": initial_heading_deg,
                "initial_lateral_position_m": initial_lateral_position_m,
                "initial_lateral_velocity_mps": initial_lateral_velocity_mps,
                "initial_yaw_rate_rps": initial_yaw_rate_rps,
                "final_speed_mps": final_speed_mps,
                "final_position_m": final_position_m,
                "final_heading_deg": final_heading_deg,
                "final_lateral_position_m": final_lateral_position_m,
                "final_lateral_velocity_mps": final_lateral_velocity_mps,
                "final_yaw_rate_rps": final_yaw_rate_rps,
                "delta_speed_mps": final_speed_mps - initial_speed_mps,
                "delta_position_m": final_position_m - initial_position_m,
                "delta_heading_deg": final_heading_deg - initial_heading_deg,
                "delta_lateral_position_m": final_lateral_position_m - initial_lateral_position_m,
                "delta_lateral_velocity_mps": final_lateral_velocity_mps - initial_lateral_velocity_mps,
                "delta_yaw_rate_rps": final_yaw_rate_rps - initial_yaw_rate_rps,
                "min_heading_deg": min_heading_deg,
                "avg_heading_deg": avg_heading_deg,
                "max_heading_deg": max_heading_deg,
                "min_lateral_position_m": min_lateral_position_m,
                "avg_lateral_position_m": avg_lateral_position_m,
                "max_lateral_position_m": max_lateral_position_m,
                "max_abs_lateral_position_m": max_abs_lateral_position_m,
                "max_abs_yaw_rate_rps": max_abs_yaw_rate_rps,
                "max_abs_lateral_velocity_mps": max_abs_lateral_velocity_mps,
                "max_abs_accel_mps2": max_abs_accel_mps2,
                "max_abs_lateral_accel_mps2": max_abs_lateral_accel_mps2,
                "max_abs_yaw_accel_rps2": max_abs_yaw_accel_rps2,
                "max_abs_jerk_mps3": max_abs_jerk_mps3,
                "max_abs_lateral_jerk_mps3": max_abs_lateral_jerk_mps3,
                "max_abs_yaw_jerk_rps3": max_abs_yaw_jerk_rps3,
                "min_road_grade_percent": min_road_grade_percent,
                "avg_road_grade_percent": avg_road_grade_percent,
                "max_road_grade_percent": max_road_grade_percent,
                "max_abs_grade_force_n": max_abs_grade_force_n,
                "control_command_step_count": control_command_step_count,
                "control_throttle_brake_overlap_step_count": control_throttle_brake_overlap_step_count,
                "control_throttle_brake_overlap_ratio": control_throttle_brake_overlap_ratio,
                "control_max_abs_steering_rate_degps": control_max_abs_steering_rate_degps,
                "control_max_abs_throttle_rate_per_sec": control_max_abs_throttle_rate_per_sec,
                "control_max_abs_brake_rate_per_sec": control_max_abs_brake_rate_per_sec,
                "control_max_throttle_plus_brake": control_max_throttle_plus_brake,
                "speed_tracking_target_step_count": speed_tracking_target_step_count,
                "speed_tracking_error_mps_min": speed_tracking_error_mps_min,
                "speed_tracking_error_mps_avg": speed_tracking_error_mps_avg,
                "speed_tracking_error_mps_max": speed_tracking_error_mps_max,
                "speed_tracking_error_abs_mps_avg": speed_tracking_error_abs_mps_avg,
                "speed_tracking_error_abs_mps_max": speed_tracking_error_abs_mps_max,
            }
        )
        if model:
            model_values.add(model)

    eligible_rows = [row for row in normalized_rows if int(row.get("step_count", 0)) > 0]
    if not eligible_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "models": sorted(model_values),
            "planar_enabled_manifest_count": 0,
            "dynamic_enabled_manifest_count": 0,
            "min_final_speed_mps": None,
            "avg_final_speed_mps": None,
            "max_final_speed_mps": None,
            "lowest_speed_batch_id": "",
            "highest_speed_batch_id": "",
            "min_final_position_m": None,
            "avg_final_position_m": None,
            "max_final_position_m": None,
            "lowest_position_batch_id": "",
            "highest_position_batch_id": "",
            "min_delta_speed_mps": None,
            "avg_delta_speed_mps": None,
            "max_delta_speed_mps": None,
            "lowest_delta_speed_batch_id": "",
            "highest_delta_speed_batch_id": "",
            "min_delta_position_m": None,
            "avg_delta_position_m": None,
            "max_delta_position_m": None,
            "lowest_delta_position_batch_id": "",
            "highest_delta_position_batch_id": "",
            "min_final_heading_deg": None,
            "avg_final_heading_deg": None,
            "max_final_heading_deg": None,
            "lowest_heading_batch_id": "",
            "highest_heading_batch_id": "",
            "min_final_lateral_position_m": None,
            "avg_final_lateral_position_m": None,
            "max_final_lateral_position_m": None,
            "lowest_lateral_position_batch_id": "",
            "highest_lateral_position_batch_id": "",
            "min_final_lateral_velocity_mps": None,
            "avg_final_lateral_velocity_mps": None,
            "max_final_lateral_velocity_mps": None,
            "lowest_lateral_velocity_batch_id": "",
            "highest_lateral_velocity_batch_id": "",
            "min_final_yaw_rate_rps": None,
            "avg_final_yaw_rate_rps": None,
            "max_final_yaw_rate_rps": None,
            "lowest_yaw_rate_batch_id": "",
            "highest_yaw_rate_batch_id": "",
            "min_delta_heading_deg": None,
            "avg_delta_heading_deg": None,
            "max_delta_heading_deg": None,
            "lowest_delta_heading_batch_id": "",
            "highest_delta_heading_batch_id": "",
            "min_delta_lateral_position_m": None,
            "avg_delta_lateral_position_m": None,
            "max_delta_lateral_position_m": None,
            "lowest_delta_lateral_position_batch_id": "",
            "highest_delta_lateral_position_batch_id": "",
            "min_delta_lateral_velocity_mps": None,
            "avg_delta_lateral_velocity_mps": None,
            "max_delta_lateral_velocity_mps": None,
            "lowest_delta_lateral_velocity_batch_id": "",
            "highest_delta_lateral_velocity_batch_id": "",
            "min_delta_yaw_rate_rps": None,
            "avg_delta_yaw_rate_rps": None,
            "max_delta_yaw_rate_rps": None,
            "lowest_delta_yaw_rate_batch_id": "",
            "highest_delta_yaw_rate_batch_id": "",
            "max_abs_yaw_rate_rps": None,
            "highest_abs_yaw_rate_batch_id": "",
            "max_abs_lateral_velocity_mps": None,
            "highest_abs_lateral_velocity_batch_id": "",
            "max_abs_accel_mps2": None,
            "highest_abs_accel_batch_id": "",
            "max_abs_lateral_accel_mps2": None,
            "highest_abs_lateral_accel_batch_id": "",
            "max_abs_yaw_accel_rps2": None,
            "highest_abs_yaw_accel_batch_id": "",
            "max_abs_jerk_mps3": None,
            "highest_abs_jerk_batch_id": "",
            "max_abs_lateral_jerk_mps3": None,
            "highest_abs_lateral_jerk_batch_id": "",
            "max_abs_yaw_jerk_rps3": None,
            "highest_abs_yaw_jerk_batch_id": "",
            "max_abs_lateral_position_m": None,
            "highest_abs_lateral_position_batch_id": "",
            "min_road_grade_percent": None,
            "avg_road_grade_percent": None,
            "max_road_grade_percent": None,
            "lowest_road_grade_batch_id": "",
            "highest_road_grade_batch_id": "",
            "max_abs_grade_force_n": None,
            "highest_abs_grade_force_batch_id": "",
            "control_command_manifest_count": 0,
            "control_command_step_count_total": 0,
            "control_throttle_brake_overlap_step_count_total": 0,
            "control_throttle_brake_overlap_ratio_avg": 0.0,
            "control_throttle_brake_overlap_ratio_max": 0.0,
            "highest_control_overlap_ratio_batch_id": "",
            "control_max_abs_steering_rate_degps_avg": 0.0,
            "control_max_abs_steering_rate_degps_max": 0.0,
            "highest_control_steering_rate_batch_id": "",
            "control_max_abs_throttle_rate_per_sec_avg": 0.0,
            "control_max_abs_throttle_rate_per_sec_max": 0.0,
            "highest_control_throttle_rate_batch_id": "",
            "control_max_abs_brake_rate_per_sec_avg": 0.0,
            "control_max_abs_brake_rate_per_sec_max": 0.0,
            "highest_control_brake_rate_batch_id": "",
            "control_max_throttle_plus_brake_avg": 0.0,
            "control_max_throttle_plus_brake_max": 0.0,
            "highest_control_throttle_plus_brake_batch_id": "",
            "speed_tracking_manifest_count": 0,
            "speed_tracking_target_step_count_total": 0,
            "min_speed_tracking_error_mps": 0.0,
            "avg_speed_tracking_error_mps": 0.0,
            "max_speed_tracking_error_mps": 0.0,
            "lowest_speed_tracking_error_batch_id": "",
            "highest_speed_tracking_error_batch_id": "",
            "avg_abs_speed_tracking_error_mps": 0.0,
            "max_abs_speed_tracking_error_mps": 0.0,
            "highest_abs_speed_tracking_error_batch_id": "",
        }

    lowest_speed_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_speed_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_speed_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_speed_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_position_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_position_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_delta_speed_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_speed_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_delta_speed_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_speed_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_delta_position_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_delta_position_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_heading_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_heading_deg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_heading_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_heading_deg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_lateral_position_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_lateral_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_lateral_position_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_lateral_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_lateral_velocity_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_lateral_velocity_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_lateral_velocity_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_lateral_velocity_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_yaw_rate_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_yaw_rate_rps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_yaw_rate_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("final_yaw_rate_rps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_delta_heading_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_heading_deg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_delta_heading_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_heading_deg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_delta_lateral_position_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_lateral_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_delta_lateral_position_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_lateral_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_delta_lateral_velocity_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_lateral_velocity_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_delta_lateral_velocity_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_lateral_velocity_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_delta_yaw_rate_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_yaw_rate_rps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_delta_yaw_rate_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("delta_yaw_rate_rps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    lowest_road_grade_row = min(
        eligible_rows,
        key=lambda row: (
            float(row.get("min_road_grade_percent", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_road_grade_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_road_grade_percent", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_grade_force_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_grade_force_n", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_yaw_rate_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_yaw_rate_rps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_lateral_velocity_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_lateral_velocity_mps", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_accel_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_accel_mps2", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_lateral_accel_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_lateral_accel_mps2", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_yaw_accel_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_yaw_accel_rps2", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_jerk_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_jerk_mps3", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_lateral_jerk_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_lateral_jerk_mps3", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_yaw_jerk_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_yaw_jerk_rps3", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_abs_lateral_position_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("max_abs_lateral_position_m", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    control_eligible_rows = [
        row
        for row in eligible_rows
        if int(row.get("control_command_step_count", 0)) > 0
    ]
    control_command_step_count_total = 0
    control_throttle_brake_overlap_step_count_total = 0
    control_throttle_brake_overlap_ratio_avg = 0.0
    control_throttle_brake_overlap_ratio_max = 0.0
    highest_control_overlap_ratio_batch_id = ""
    control_max_abs_steering_rate_degps_avg = 0.0
    control_max_abs_steering_rate_degps_max = 0.0
    highest_control_steering_rate_batch_id = ""
    control_max_abs_throttle_rate_per_sec_avg = 0.0
    control_max_abs_throttle_rate_per_sec_max = 0.0
    highest_control_throttle_rate_batch_id = ""
    control_max_abs_brake_rate_per_sec_avg = 0.0
    control_max_abs_brake_rate_per_sec_max = 0.0
    highest_control_brake_rate_batch_id = ""
    control_max_throttle_plus_brake_avg = 0.0
    control_max_throttle_plus_brake_max = 0.0
    highest_control_throttle_plus_brake_batch_id = ""
    if control_eligible_rows:
        highest_control_overlap_ratio_row = max(
            control_eligible_rows,
            key=lambda row: (
                float(row.get("control_throttle_brake_overlap_ratio", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_control_steering_rate_row = max(
            control_eligible_rows,
            key=lambda row: (
                float(row.get("control_max_abs_steering_rate_degps", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_control_throttle_rate_row = max(
            control_eligible_rows,
            key=lambda row: (
                float(row.get("control_max_abs_throttle_rate_per_sec", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_control_brake_rate_row = max(
            control_eligible_rows,
            key=lambda row: (
                float(row.get("control_max_abs_brake_rate_per_sec", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_control_throttle_plus_brake_row = max(
            control_eligible_rows,
            key=lambda row: (
                float(row.get("control_max_throttle_plus_brake", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        control_command_step_count_total = sum(
            int(row.get("control_command_step_count", 0)) for row in control_eligible_rows
        )
        control_throttle_brake_overlap_step_count_total = sum(
            int(row.get("control_throttle_brake_overlap_step_count", 0))
            for row in control_eligible_rows
        )
        control_throttle_brake_overlap_ratio_avg = (
            float(control_throttle_brake_overlap_step_count_total) / float(control_command_step_count_total)
            if control_command_step_count_total > 0
            else 0.0
        )
        control_throttle_brake_overlap_ratio_max = float(
            highest_control_overlap_ratio_row.get("control_throttle_brake_overlap_ratio", 0.0)
        )
        highest_control_overlap_ratio_batch_id = str(highest_control_overlap_ratio_row.get("batch_id", ""))
        control_max_abs_steering_rate_degps_avg = (
            sum(float(row.get("control_max_abs_steering_rate_degps", 0.0)) for row in control_eligible_rows)
            / float(len(control_eligible_rows))
        )
        control_max_abs_steering_rate_degps_max = float(
            highest_control_steering_rate_row.get("control_max_abs_steering_rate_degps", 0.0)
        )
        highest_control_steering_rate_batch_id = str(highest_control_steering_rate_row.get("batch_id", ""))
        control_max_abs_throttle_rate_per_sec_avg = (
            sum(float(row.get("control_max_abs_throttle_rate_per_sec", 0.0)) for row in control_eligible_rows)
            / float(len(control_eligible_rows))
        )
        control_max_abs_throttle_rate_per_sec_max = float(
            highest_control_throttle_rate_row.get("control_max_abs_throttle_rate_per_sec", 0.0)
        )
        highest_control_throttle_rate_batch_id = str(highest_control_throttle_rate_row.get("batch_id", ""))
        control_max_abs_brake_rate_per_sec_avg = (
            sum(float(row.get("control_max_abs_brake_rate_per_sec", 0.0)) for row in control_eligible_rows)
            / float(len(control_eligible_rows))
        )
        control_max_abs_brake_rate_per_sec_max = float(
            highest_control_brake_rate_row.get("control_max_abs_brake_rate_per_sec", 0.0)
        )
        highest_control_brake_rate_batch_id = str(highest_control_brake_rate_row.get("batch_id", ""))
        control_max_throttle_plus_brake_avg = (
            sum(float(row.get("control_max_throttle_plus_brake", 0.0)) for row in control_eligible_rows)
            / float(len(control_eligible_rows))
        )
        control_max_throttle_plus_brake_max = float(
            highest_control_throttle_plus_brake_row.get("control_max_throttle_plus_brake", 0.0)
        )
        highest_control_throttle_plus_brake_batch_id = str(
            highest_control_throttle_plus_brake_row.get("batch_id", "")
        )
    speed_tracking_eligible_rows = [
        row
        for row in eligible_rows
        if int(row.get("speed_tracking_target_step_count", 0)) > 0
    ]
    speed_tracking_manifest_count = len(speed_tracking_eligible_rows)
    speed_tracking_target_step_count_total = 0
    min_speed_tracking_error_mps = 0.0
    avg_speed_tracking_error_mps = 0.0
    max_speed_tracking_error_mps = 0.0
    lowest_speed_tracking_error_batch_id = ""
    highest_speed_tracking_error_batch_id = ""
    avg_abs_speed_tracking_error_mps = 0.0
    max_abs_speed_tracking_error_mps = 0.0
    highest_abs_speed_tracking_error_batch_id = ""
    if speed_tracking_eligible_rows:
        lowest_speed_tracking_error_row = min(
            speed_tracking_eligible_rows,
            key=lambda row: (
                float(row.get("speed_tracking_error_mps_min", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_speed_tracking_error_row = max(
            speed_tracking_eligible_rows,
            key=lambda row: (
                float(row.get("speed_tracking_error_mps_max", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_abs_speed_tracking_error_row = max(
            speed_tracking_eligible_rows,
            key=lambda row: (
                float(row.get("speed_tracking_error_abs_mps_max", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
        speed_tracking_target_step_count_total = sum(
            int(row.get("speed_tracking_target_step_count", 0)) for row in speed_tracking_eligible_rows
        )
        min_speed_tracking_error_mps = float(
            lowest_speed_tracking_error_row.get("speed_tracking_error_mps_min", 0.0)
        )
        avg_speed_tracking_error_mps = (
            sum(float(row.get("speed_tracking_error_mps_avg", 0.0)) for row in speed_tracking_eligible_rows)
            / float(len(speed_tracking_eligible_rows))
        )
        max_speed_tracking_error_mps = float(
            highest_speed_tracking_error_row.get("speed_tracking_error_mps_max", 0.0)
        )
        lowest_speed_tracking_error_batch_id = str(lowest_speed_tracking_error_row.get("batch_id", ""))
        highest_speed_tracking_error_batch_id = str(highest_speed_tracking_error_row.get("batch_id", ""))
        avg_abs_speed_tracking_error_mps = (
            sum(float(row.get("speed_tracking_error_abs_mps_avg", 0.0)) for row in speed_tracking_eligible_rows)
            / float(len(speed_tracking_eligible_rows))
        )
        max_abs_speed_tracking_error_mps = float(
            highest_abs_speed_tracking_error_row.get("speed_tracking_error_abs_mps_max", 0.0)
        )
        highest_abs_speed_tracking_error_batch_id = str(
            highest_abs_speed_tracking_error_row.get("batch_id", "")
        )
    speed_values = [float(row.get("final_speed_mps", 0.0)) for row in eligible_rows]
    position_values = [float(row.get("final_position_m", 0.0)) for row in eligible_rows]
    delta_speed_values = [float(row.get("delta_speed_mps", 0.0)) for row in eligible_rows]
    delta_position_values = [float(row.get("delta_position_m", 0.0)) for row in eligible_rows]
    heading_values = [float(row.get("final_heading_deg", 0.0)) for row in eligible_rows]
    lateral_position_values = [float(row.get("final_lateral_position_m", 0.0)) for row in eligible_rows]
    lateral_velocity_values = [float(row.get("final_lateral_velocity_mps", 0.0)) for row in eligible_rows]
    yaw_rate_values = [float(row.get("final_yaw_rate_rps", 0.0)) for row in eligible_rows]
    delta_heading_values = [float(row.get("delta_heading_deg", 0.0)) for row in eligible_rows]
    delta_lateral_position_values = [float(row.get("delta_lateral_position_m", 0.0)) for row in eligible_rows]
    delta_lateral_velocity_values = [float(row.get("delta_lateral_velocity_mps", 0.0)) for row in eligible_rows]
    delta_yaw_rate_values = [float(row.get("delta_yaw_rate_rps", 0.0)) for row in eligible_rows]
    avg_road_grade_values = [float(row.get("avg_road_grade_percent", 0.0)) for row in eligible_rows]
    avg_speed = sum(speed_values) / float(len(speed_values))
    avg_position = sum(position_values) / float(len(position_values))
    avg_delta_speed = sum(delta_speed_values) / float(len(delta_speed_values))
    avg_delta_position = sum(delta_position_values) / float(len(delta_position_values))
    avg_heading = sum(heading_values) / float(len(heading_values))
    avg_lateral_position = sum(lateral_position_values) / float(len(lateral_position_values))
    avg_lateral_velocity = sum(lateral_velocity_values) / float(len(lateral_velocity_values))
    avg_yaw_rate = sum(yaw_rate_values) / float(len(yaw_rate_values))
    avg_delta_heading = sum(delta_heading_values) / float(len(delta_heading_values))
    avg_delta_lateral_position = sum(delta_lateral_position_values) / float(len(delta_lateral_position_values))
    avg_delta_lateral_velocity = sum(delta_lateral_velocity_values) / float(len(delta_lateral_velocity_values))
    avg_delta_yaw_rate = sum(delta_yaw_rate_values) / float(len(delta_yaw_rate_values))
    avg_road_grade_percent = sum(avg_road_grade_values) / float(len(avg_road_grade_values))

    return {
        "evaluated_manifest_count": len(eligible_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "models": sorted(model_values),
        "planar_enabled_manifest_count": sum(
            1 for row in eligible_rows if bool(row.get("planar_kinematics_enabled", False))
        ),
        "dynamic_enabled_manifest_count": sum(1 for row in eligible_rows if bool(row.get("dynamic_bicycle_enabled", False))),
        "min_final_speed_mps": float(lowest_speed_row.get("final_speed_mps", 0.0)),
        "avg_final_speed_mps": float(avg_speed),
        "max_final_speed_mps": float(highest_speed_row.get("final_speed_mps", 0.0)),
        "lowest_speed_batch_id": str(lowest_speed_row.get("batch_id", "")),
        "highest_speed_batch_id": str(highest_speed_row.get("batch_id", "")),
        "min_final_position_m": float(lowest_position_row.get("final_position_m", 0.0)),
        "avg_final_position_m": float(avg_position),
        "max_final_position_m": float(highest_position_row.get("final_position_m", 0.0)),
        "lowest_position_batch_id": str(lowest_position_row.get("batch_id", "")),
        "highest_position_batch_id": str(highest_position_row.get("batch_id", "")),
        "min_delta_speed_mps": float(lowest_delta_speed_row.get("delta_speed_mps", 0.0)),
        "avg_delta_speed_mps": float(avg_delta_speed),
        "max_delta_speed_mps": float(highest_delta_speed_row.get("delta_speed_mps", 0.0)),
        "lowest_delta_speed_batch_id": str(lowest_delta_speed_row.get("batch_id", "")),
        "highest_delta_speed_batch_id": str(highest_delta_speed_row.get("batch_id", "")),
        "min_delta_position_m": float(lowest_delta_position_row.get("delta_position_m", 0.0)),
        "avg_delta_position_m": float(avg_delta_position),
        "max_delta_position_m": float(highest_delta_position_row.get("delta_position_m", 0.0)),
        "lowest_delta_position_batch_id": str(lowest_delta_position_row.get("batch_id", "")),
        "highest_delta_position_batch_id": str(highest_delta_position_row.get("batch_id", "")),
        "min_final_heading_deg": float(lowest_heading_row.get("final_heading_deg", 0.0)),
        "avg_final_heading_deg": float(avg_heading),
        "max_final_heading_deg": float(highest_heading_row.get("final_heading_deg", 0.0)),
        "lowest_heading_batch_id": str(lowest_heading_row.get("batch_id", "")),
        "highest_heading_batch_id": str(highest_heading_row.get("batch_id", "")),
        "min_final_lateral_position_m": float(lowest_lateral_position_row.get("final_lateral_position_m", 0.0)),
        "avg_final_lateral_position_m": float(avg_lateral_position),
        "max_final_lateral_position_m": float(highest_lateral_position_row.get("final_lateral_position_m", 0.0)),
        "lowest_lateral_position_batch_id": str(lowest_lateral_position_row.get("batch_id", "")),
        "highest_lateral_position_batch_id": str(highest_lateral_position_row.get("batch_id", "")),
        "min_final_lateral_velocity_mps": float(lowest_lateral_velocity_row.get("final_lateral_velocity_mps", 0.0)),
        "avg_final_lateral_velocity_mps": float(avg_lateral_velocity),
        "max_final_lateral_velocity_mps": float(highest_lateral_velocity_row.get("final_lateral_velocity_mps", 0.0)),
        "lowest_lateral_velocity_batch_id": str(lowest_lateral_velocity_row.get("batch_id", "")),
        "highest_lateral_velocity_batch_id": str(highest_lateral_velocity_row.get("batch_id", "")),
        "min_final_yaw_rate_rps": float(lowest_yaw_rate_row.get("final_yaw_rate_rps", 0.0)),
        "avg_final_yaw_rate_rps": float(avg_yaw_rate),
        "max_final_yaw_rate_rps": float(highest_yaw_rate_row.get("final_yaw_rate_rps", 0.0)),
        "lowest_yaw_rate_batch_id": str(lowest_yaw_rate_row.get("batch_id", "")),
        "highest_yaw_rate_batch_id": str(highest_yaw_rate_row.get("batch_id", "")),
        "min_delta_heading_deg": float(lowest_delta_heading_row.get("delta_heading_deg", 0.0)),
        "avg_delta_heading_deg": float(avg_delta_heading),
        "max_delta_heading_deg": float(highest_delta_heading_row.get("delta_heading_deg", 0.0)),
        "lowest_delta_heading_batch_id": str(lowest_delta_heading_row.get("batch_id", "")),
        "highest_delta_heading_batch_id": str(highest_delta_heading_row.get("batch_id", "")),
        "min_delta_lateral_position_m": float(
            lowest_delta_lateral_position_row.get("delta_lateral_position_m", 0.0)
        ),
        "avg_delta_lateral_position_m": float(avg_delta_lateral_position),
        "max_delta_lateral_position_m": float(
            highest_delta_lateral_position_row.get("delta_lateral_position_m", 0.0)
        ),
        "lowest_delta_lateral_position_batch_id": str(lowest_delta_lateral_position_row.get("batch_id", "")),
        "highest_delta_lateral_position_batch_id": str(highest_delta_lateral_position_row.get("batch_id", "")),
        "min_delta_lateral_velocity_mps": float(
            lowest_delta_lateral_velocity_row.get("delta_lateral_velocity_mps", 0.0)
        ),
        "avg_delta_lateral_velocity_mps": float(avg_delta_lateral_velocity),
        "max_delta_lateral_velocity_mps": float(
            highest_delta_lateral_velocity_row.get("delta_lateral_velocity_mps", 0.0)
        ),
        "lowest_delta_lateral_velocity_batch_id": str(lowest_delta_lateral_velocity_row.get("batch_id", "")),
        "highest_delta_lateral_velocity_batch_id": str(highest_delta_lateral_velocity_row.get("batch_id", "")),
        "min_delta_yaw_rate_rps": float(lowest_delta_yaw_rate_row.get("delta_yaw_rate_rps", 0.0)),
        "avg_delta_yaw_rate_rps": float(avg_delta_yaw_rate),
        "max_delta_yaw_rate_rps": float(highest_delta_yaw_rate_row.get("delta_yaw_rate_rps", 0.0)),
        "lowest_delta_yaw_rate_batch_id": str(lowest_delta_yaw_rate_row.get("batch_id", "")),
        "highest_delta_yaw_rate_batch_id": str(highest_delta_yaw_rate_row.get("batch_id", "")),
        "max_abs_yaw_rate_rps": float(highest_abs_yaw_rate_row.get("max_abs_yaw_rate_rps", 0.0)),
        "highest_abs_yaw_rate_batch_id": str(highest_abs_yaw_rate_row.get("batch_id", "")),
        "max_abs_lateral_velocity_mps": float(
            highest_abs_lateral_velocity_row.get("max_abs_lateral_velocity_mps", 0.0)
        ),
        "highest_abs_lateral_velocity_batch_id": str(highest_abs_lateral_velocity_row.get("batch_id", "")),
        "max_abs_accel_mps2": float(highest_abs_accel_row.get("max_abs_accel_mps2", 0.0)),
        "highest_abs_accel_batch_id": str(highest_abs_accel_row.get("batch_id", "")),
        "max_abs_lateral_accel_mps2": float(
            highest_abs_lateral_accel_row.get("max_abs_lateral_accel_mps2", 0.0)
        ),
        "highest_abs_lateral_accel_batch_id": str(highest_abs_lateral_accel_row.get("batch_id", "")),
        "max_abs_yaw_accel_rps2": float(highest_abs_yaw_accel_row.get("max_abs_yaw_accel_rps2", 0.0)),
        "highest_abs_yaw_accel_batch_id": str(highest_abs_yaw_accel_row.get("batch_id", "")),
        "max_abs_jerk_mps3": float(highest_abs_jerk_row.get("max_abs_jerk_mps3", 0.0)),
        "highest_abs_jerk_batch_id": str(highest_abs_jerk_row.get("batch_id", "")),
        "max_abs_lateral_jerk_mps3": float(
            highest_abs_lateral_jerk_row.get("max_abs_lateral_jerk_mps3", 0.0)
        ),
        "highest_abs_lateral_jerk_batch_id": str(highest_abs_lateral_jerk_row.get("batch_id", "")),
        "max_abs_yaw_jerk_rps3": float(highest_abs_yaw_jerk_row.get("max_abs_yaw_jerk_rps3", 0.0)),
        "highest_abs_yaw_jerk_batch_id": str(highest_abs_yaw_jerk_row.get("batch_id", "")),
        "max_abs_lateral_position_m": float(
            highest_abs_lateral_position_row.get("max_abs_lateral_position_m", 0.0)
        ),
        "highest_abs_lateral_position_batch_id": str(highest_abs_lateral_position_row.get("batch_id", "")),
        "min_road_grade_percent": float(lowest_road_grade_row.get("min_road_grade_percent", 0.0)),
        "avg_road_grade_percent": float(avg_road_grade_percent),
        "max_road_grade_percent": float(highest_road_grade_row.get("max_road_grade_percent", 0.0)),
        "lowest_road_grade_batch_id": str(lowest_road_grade_row.get("batch_id", "")),
        "highest_road_grade_batch_id": str(highest_road_grade_row.get("batch_id", "")),
        "max_abs_grade_force_n": float(highest_abs_grade_force_row.get("max_abs_grade_force_n", 0.0)),
        "highest_abs_grade_force_batch_id": str(highest_abs_grade_force_row.get("batch_id", "")),
        "control_command_manifest_count": len(control_eligible_rows),
        "control_command_step_count_total": int(control_command_step_count_total),
        "control_throttle_brake_overlap_step_count_total": int(control_throttle_brake_overlap_step_count_total),
        "control_throttle_brake_overlap_ratio_avg": float(control_throttle_brake_overlap_ratio_avg),
        "control_throttle_brake_overlap_ratio_max": float(control_throttle_brake_overlap_ratio_max),
        "highest_control_overlap_ratio_batch_id": str(highest_control_overlap_ratio_batch_id),
        "control_max_abs_steering_rate_degps_avg": float(control_max_abs_steering_rate_degps_avg),
        "control_max_abs_steering_rate_degps_max": float(control_max_abs_steering_rate_degps_max),
        "highest_control_steering_rate_batch_id": str(highest_control_steering_rate_batch_id),
        "control_max_abs_throttle_rate_per_sec_avg": float(control_max_abs_throttle_rate_per_sec_avg),
        "control_max_abs_throttle_rate_per_sec_max": float(control_max_abs_throttle_rate_per_sec_max),
        "highest_control_throttle_rate_batch_id": str(highest_control_throttle_rate_batch_id),
        "control_max_abs_brake_rate_per_sec_avg": float(control_max_abs_brake_rate_per_sec_avg),
        "control_max_abs_brake_rate_per_sec_max": float(control_max_abs_brake_rate_per_sec_max),
        "highest_control_brake_rate_batch_id": str(highest_control_brake_rate_batch_id),
        "control_max_throttle_plus_brake_avg": float(control_max_throttle_plus_brake_avg),
        "control_max_throttle_plus_brake_max": float(control_max_throttle_plus_brake_max),
        "highest_control_throttle_plus_brake_batch_id": str(highest_control_throttle_plus_brake_batch_id),
        "speed_tracking_manifest_count": int(speed_tracking_manifest_count),
        "speed_tracking_target_step_count_total": int(speed_tracking_target_step_count_total),
        "min_speed_tracking_error_mps": float(min_speed_tracking_error_mps),
        "avg_speed_tracking_error_mps": float(avg_speed_tracking_error_mps),
        "max_speed_tracking_error_mps": float(max_speed_tracking_error_mps),
        "lowest_speed_tracking_error_batch_id": str(lowest_speed_tracking_error_batch_id),
        "highest_speed_tracking_error_batch_id": str(highest_speed_tracking_error_batch_id),
        "avg_abs_speed_tracking_error_mps": float(avg_abs_speed_tracking_error_mps),
        "max_abs_speed_tracking_error_mps": float(max_abs_speed_tracking_error_mps),
        "highest_abs_speed_tracking_error_batch_id": str(highest_abs_speed_tracking_error_batch_id),
    }


def summarize_phase2_map_routing(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        route_total_length_m_value = _to_float_or_none(manifest.get("phase2_map_route_total_length_m"))
        if route_total_length_m_value is None:
            route_total_length_m_value = 0.0
        route_total_length_m_value = max(0.0, route_total_length_m_value)
        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "checked": bool(manifest.get("phase2_map_routing_checked", False)),
                "status": str(manifest.get("phase2_map_routing_status", "")).strip().lower() or "n/a",
                "error_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_routing_error_count"), default=0),
                ),
                "warning_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_routing_warning_count"), default=0),
                ),
                "semantic_warning_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_routing_semantic_warning_count"), default=0),
                ),
                "unreachable_lane_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_routing_unreachable_lane_count"), default=0),
                ),
                "non_reciprocal_link_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_routing_non_reciprocal_link_count"), default=0),
                ),
                "continuity_gap_warning_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_routing_continuity_gap_warning_count"), default=0),
                ),
                "route_checked": bool(manifest.get("phase2_map_route_checked", False)),
                "route_status": str(manifest.get("phase2_map_route_status", "")).strip().lower() or "n/a",
                "route_lane_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_route_lane_count"), default=0),
                ),
                "route_hop_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_route_hop_count"), default=0),
                ),
                "route_total_length_m": float(route_total_length_m_value),
                "route_segment_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_route_segment_count"), default=0),
                ),
                "route_via_lane_count": max(
                    0,
                    _to_int(manifest.get("phase2_map_route_via_lane_count"), default=0),
                ),
            }
        )

    checked_rows = [row for row in normalized_rows if bool(row.get("checked", False))]
    if not checked_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "status_counts": {},
            "error_count_total": 0,
            "warning_count_total": 0,
            "semantic_warning_count_total": 0,
            "unreachable_lane_count_total": 0,
            "non_reciprocal_link_count_total": 0,
            "continuity_gap_warning_count_total": 0,
            "max_unreachable_lane_count": 0,
            "highest_unreachable_batch_id": "",
            "max_non_reciprocal_link_count": 0,
            "highest_non_reciprocal_batch_id": "",
            "max_continuity_gap_warning_count": 0,
            "highest_continuity_gap_batch_id": "",
            "route_evaluated_manifest_count": 0,
            "route_status_counts": {},
            "route_lane_count_total": 0,
            "route_hop_count_total": 0,
            "route_total_length_m_total": 0.0,
            "route_total_length_m_avg": 0.0,
            "route_segment_count_total": 0,
            "route_segment_count_avg": 0.0,
            "route_with_via_manifest_count": 0,
            "route_via_lane_count_total": 0,
            "route_via_lane_count_avg": 0.0,
            "max_route_lane_count": 0,
            "highest_route_lane_count_batch_id": "",
            "max_route_hop_count": 0,
            "highest_route_hop_count_batch_id": "",
            "max_route_segment_count": 0,
            "highest_route_segment_count_batch_id": "",
            "max_route_via_lane_count": 0,
            "highest_route_via_lane_count_batch_id": "",
            "max_route_total_length_m": 0.0,
            "highest_route_total_length_batch_id": "",
        }

    status_counts: dict[str, int] = {}
    for row in checked_rows:
        status = str(row.get("status", "")).strip().lower() or "n/a"
        status_counts[status] = status_counts.get(status, 0) + 1

    highest_unreachable_row = max(
        checked_rows,
        key=lambda row: (
            int(row.get("unreachable_lane_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_non_reciprocal_row = max(
        checked_rows,
        key=lambda row: (
            int(row.get("non_reciprocal_link_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_continuity_gap_row = max(
        checked_rows,
        key=lambda row: (
            int(row.get("continuity_gap_warning_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    route_checked_rows = [row for row in checked_rows if bool(row.get("route_checked", False))]
    route_status_counts: dict[str, int] = {}
    for row in route_checked_rows:
        route_status = str(row.get("route_status", "")).strip().lower() or "n/a"
        route_status_counts[route_status] = route_status_counts.get(route_status, 0) + 1
    route_lane_count_total = sum(int(row.get("route_lane_count", 0)) for row in route_checked_rows)
    route_hop_count_total = sum(int(row.get("route_hop_count", 0)) for row in route_checked_rows)
    route_total_length_m_total = sum(float(row.get("route_total_length_m", 0.0)) for row in route_checked_rows)
    route_segment_count_total = sum(int(row.get("route_segment_count", 0)) for row in route_checked_rows)
    route_with_via_manifest_count = sum(1 for row in route_checked_rows if int(row.get("route_via_lane_count", 0)) > 0)
    route_via_lane_count_total = sum(int(row.get("route_via_lane_count", 0)) for row in route_checked_rows)
    route_total_length_m_avg = (
        route_total_length_m_total / float(len(route_checked_rows))
        if route_checked_rows
        else 0.0
    )
    route_segment_count_avg = (
        route_segment_count_total / float(len(route_checked_rows))
        if route_checked_rows
        else 0.0
    )
    route_via_lane_count_avg = (
        route_via_lane_count_total / float(len(route_checked_rows))
        if route_checked_rows
        else 0.0
    )
    if route_checked_rows:
        highest_route_lane_row = max(
            route_checked_rows,
            key=lambda row: (
                int(row.get("route_lane_count", 0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_route_hop_row = max(
            route_checked_rows,
            key=lambda row: (
                int(row.get("route_hop_count", 0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_route_segment_row = max(
            route_checked_rows,
            key=lambda row: (
                int(row.get("route_segment_count", 0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_route_via_lane_row = max(
            route_checked_rows,
            key=lambda row: (
                int(row.get("route_via_lane_count", 0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_route_total_length_row = max(
            route_checked_rows,
            key=lambda row: (
                float(row.get("route_total_length_m", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
    else:
        highest_route_lane_row = {"route_lane_count": 0, "batch_id": ""}
        highest_route_hop_row = {"route_hop_count": 0, "batch_id": ""}
        highest_route_segment_row = {"route_segment_count": 0, "batch_id": ""}
        highest_route_via_lane_row = {"route_via_lane_count": 0, "batch_id": ""}
        highest_route_total_length_row = {"route_total_length_m": 0.0, "batch_id": ""}
    return {
        "evaluated_manifest_count": len(checked_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "status_counts": {key: status_counts[key] for key in sorted(status_counts.keys())},
        "error_count_total": sum(int(row.get("error_count", 0)) for row in checked_rows),
        "warning_count_total": sum(int(row.get("warning_count", 0)) for row in checked_rows),
        "semantic_warning_count_total": sum(int(row.get("semantic_warning_count", 0)) for row in checked_rows),
        "unreachable_lane_count_total": sum(int(row.get("unreachable_lane_count", 0)) for row in checked_rows),
        "non_reciprocal_link_count_total": sum(int(row.get("non_reciprocal_link_count", 0)) for row in checked_rows),
        "continuity_gap_warning_count_total": sum(
            int(row.get("continuity_gap_warning_count", 0)) for row in checked_rows
        ),
        "max_unreachable_lane_count": int(highest_unreachable_row.get("unreachable_lane_count", 0)),
        "highest_unreachable_batch_id": str(highest_unreachable_row.get("batch_id", "")),
        "max_non_reciprocal_link_count": int(highest_non_reciprocal_row.get("non_reciprocal_link_count", 0)),
        "highest_non_reciprocal_batch_id": str(highest_non_reciprocal_row.get("batch_id", "")),
        "max_continuity_gap_warning_count": int(highest_continuity_gap_row.get("continuity_gap_warning_count", 0)),
        "highest_continuity_gap_batch_id": str(highest_continuity_gap_row.get("batch_id", "")),
        "route_evaluated_manifest_count": len(route_checked_rows),
        "route_status_counts": {key: route_status_counts[key] for key in sorted(route_status_counts.keys())},
        "route_lane_count_total": int(route_lane_count_total),
        "route_hop_count_total": int(route_hop_count_total),
        "route_total_length_m_total": float(route_total_length_m_total),
        "route_total_length_m_avg": float(route_total_length_m_avg),
        "route_segment_count_total": int(route_segment_count_total),
        "route_segment_count_avg": float(route_segment_count_avg),
        "route_with_via_manifest_count": int(route_with_via_manifest_count),
        "route_via_lane_count_total": int(route_via_lane_count_total),
        "route_via_lane_count_avg": float(route_via_lane_count_avg),
        "max_route_lane_count": int(highest_route_lane_row.get("route_lane_count", 0)),
        "highest_route_lane_count_batch_id": str(highest_route_lane_row.get("batch_id", "")),
        "max_route_hop_count": int(highest_route_hop_row.get("route_hop_count", 0)),
        "highest_route_hop_count_batch_id": str(highest_route_hop_row.get("batch_id", "")),
        "max_route_segment_count": int(highest_route_segment_row.get("route_segment_count", 0)),
        "highest_route_segment_count_batch_id": str(highest_route_segment_row.get("batch_id", "")),
        "max_route_via_lane_count": int(highest_route_via_lane_row.get("route_via_lane_count", 0)),
        "highest_route_via_lane_count_batch_id": str(highest_route_via_lane_row.get("batch_id", "")),
        "max_route_total_length_m": float(highest_route_total_length_row.get("route_total_length_m", 0.0)),
        "highest_route_total_length_batch_id": str(highest_route_total_length_row.get("batch_id", "")),
    }


def summarize_phase2_sensor_fidelity(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        modality_counts: dict[str, int] = {}
        camera_projection_mode_counts: dict[str, int] = {}
        camera_bloom_level_counts: dict[str, int] = {}
        camera_depth_mode_counts: dict[str, int] = {}
        camera_optical_flow_velocity_direction_counts: dict[str, int] = {}
        camera_optical_flow_y_axis_direction_counts: dict[str, int] = {}
        modality_counts_raw = manifest.get("phase2_sensor_modality_counts", {})
        if isinstance(modality_counts_raw, dict):
            for raw_key, raw_value in modality_counts_raw.items():
                key = str(raw_key).strip().lower()
                if not key:
                    continue
                modality_counts[key] = max(0, _to_int(raw_value, default=0))
        camera_projection_mode_counts_raw = manifest.get("phase2_sensor_camera_projection_mode_counts", {})
        if isinstance(camera_projection_mode_counts_raw, dict):
            for raw_key, raw_value in camera_projection_mode_counts_raw.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                camera_projection_mode_counts[key] = max(0, _to_int(raw_value, default=0))
        camera_bloom_level_counts_raw = manifest.get("phase2_sensor_camera_bloom_level_counts", {})
        if isinstance(camera_bloom_level_counts_raw, dict):
            for raw_key, raw_value in camera_bloom_level_counts_raw.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                camera_bloom_level_counts[key] = max(0, _to_int(raw_value, default=0))
        camera_depth_mode_counts_raw = manifest.get("phase2_sensor_camera_depth_mode_counts", {})
        if isinstance(camera_depth_mode_counts_raw, dict):
            for raw_key, raw_value in camera_depth_mode_counts_raw.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                camera_depth_mode_counts[key] = max(0, _to_int(raw_value, default=0))
        camera_optical_flow_velocity_direction_counts_raw = manifest.get(
            "phase2_sensor_camera_optical_flow_velocity_direction_counts",
            {},
        )
        if isinstance(camera_optical_flow_velocity_direction_counts_raw, dict):
            for raw_key, raw_value in camera_optical_flow_velocity_direction_counts_raw.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                camera_optical_flow_velocity_direction_counts[key] = max(
                    0,
                    _to_int(raw_value, default=0),
                )
        camera_optical_flow_y_axis_direction_counts_raw = manifest.get(
            "phase2_sensor_camera_optical_flow_y_axis_direction_counts",
            {},
        )
        if isinstance(camera_optical_flow_y_axis_direction_counts_raw, dict):
            for raw_key, raw_value in camera_optical_flow_y_axis_direction_counts_raw.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                camera_optical_flow_y_axis_direction_counts[key] = max(
                    0,
                    _to_int(raw_value, default=0),
                )
        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "checked": bool(manifest.get("phase2_sensor_checked", False)),
                "fidelity_tier": str(manifest.get("phase2_sensor_fidelity_tier", "")).strip().lower() or "n/a",
                "fidelity_tier_score": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_fidelity_tier_score")) or 0.0),
                ),
                "frame_count": max(0, _to_int(manifest.get("phase2_sensor_frame_count"), default=0)),
                "modality_counts": modality_counts,
                "camera_frame_count": max(0, _to_int(manifest.get("phase2_sensor_camera_frame_count"), default=0)),
                "camera_noise_stddev_px_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_noise_stddev_px_avg")) or 0.0),
                ),
                "camera_dynamic_range_stops_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_dynamic_range_stops_avg")) or 0.0),
                ),
                "camera_visibility_score_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_visibility_score_avg")) or 0.0),
                ),
                "camera_motion_blur_level_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_motion_blur_level_avg")) or 0.0),
                ),
                "camera_snr_db_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_snr_db_avg")) or 0.0),
                ),
                "camera_exposure_time_ms_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_exposure_time_ms_avg")) or 0.0),
                ),
                "camera_signal_saturation_ratio_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(manifest.get("phase2_sensor_camera_signal_saturation_ratio_avg")) or 0.0
                    ),
                ),
                "camera_rolling_shutter_total_delay_ms_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(
                            manifest.get("phase2_sensor_camera_rolling_shutter_total_delay_ms_avg")
                        )
                        or 0.0
                    ),
                ),
                "camera_normalized_total_noise_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_normalized_total_noise_avg")) or 0.0),
                ),
                "camera_distortion_edge_shift_px_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_distortion_edge_shift_px_avg")) or 0.0),
                ),
                "camera_principal_point_offset_norm_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(manifest.get("phase2_sensor_camera_principal_point_offset_norm_avg"))
                        or 0.0
                    ),
                ),
                "camera_effective_focal_length_px_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(manifest.get("phase2_sensor_camera_effective_focal_length_px_avg"))
                        or 0.0
                    ),
                ),
                "camera_projection_mode_counts": {
                    key: camera_projection_mode_counts[key] for key in sorted(camera_projection_mode_counts.keys())
                },
                "camera_gain_db_avg": float(_to_float_or_none(manifest.get("phase2_sensor_camera_gain_db_avg")) or 0.0),
                "camera_gamma_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_gamma_avg")) or 0.0),
                ),
                "camera_white_balance_kelvin_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(manifest.get("phase2_sensor_camera_white_balance_kelvin_avg")) or 0.0
                    ),
                ),
                "camera_vignetting_edge_darkening_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(
                            manifest.get("phase2_sensor_camera_vignetting_edge_darkening_avg")
                        )
                        or 0.0
                    ),
                ),
                "camera_bloom_halo_strength_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(manifest.get("phase2_sensor_camera_bloom_halo_strength_avg")) or 0.0
                    ),
                ),
                "camera_chromatic_aberration_shift_px_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(
                            manifest.get("phase2_sensor_camera_chromatic_aberration_shift_px_avg")
                        )
                        or 0.0
                    ),
                ),
                "camera_tonemapper_disabled_frame_count": max(
                    0,
                    _to_int(manifest.get("phase2_sensor_camera_tonemapper_disabled_frame_count"), default=0),
                ),
                "camera_bloom_level_counts": {
                    key: camera_bloom_level_counts[key] for key in sorted(camera_bloom_level_counts.keys())
                },
                "camera_depth_enabled_frame_count": max(
                    0,
                    _to_int(manifest.get("phase2_sensor_camera_depth_enabled_frame_count"), default=0),
                ),
                "camera_depth_min_m_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_depth_min_m_avg")) or 0.0),
                ),
                "camera_depth_max_m_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_depth_max_m_avg")) or 0.0),
                ),
                "camera_depth_bit_depth_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_camera_depth_bit_depth_avg")) or 0.0),
                ),
                "camera_depth_mode_counts": {
                    key: camera_depth_mode_counts[key] for key in sorted(camera_depth_mode_counts.keys())
                },
                "camera_optical_flow_enabled_frame_count": max(
                    0,
                    _to_int(
                        manifest.get("phase2_sensor_camera_optical_flow_enabled_frame_count"),
                        default=0,
                    ),
                ),
                "camera_optical_flow_magnitude_px_avg": max(
                    0.0,
                    float(
                        _to_float_or_none(
                            manifest.get("phase2_sensor_camera_optical_flow_magnitude_px_avg")
                        )
                        or 0.0
                    ),
                ),
                "camera_optical_flow_velocity_direction_counts": {
                    key: camera_optical_flow_velocity_direction_counts[key]
                    for key in sorted(camera_optical_flow_velocity_direction_counts.keys())
                },
                "camera_optical_flow_y_axis_direction_counts": {
                    key: camera_optical_flow_y_axis_direction_counts[key]
                    for key in sorted(camera_optical_flow_y_axis_direction_counts.keys())
                },
                "lidar_frame_count": max(0, _to_int(manifest.get("phase2_sensor_lidar_frame_count"), default=0)),
                "lidar_point_count_total": max(
                    0,
                    _to_int(manifest.get("phase2_sensor_lidar_point_count_total"), default=0),
                ),
                "lidar_point_count_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_lidar_point_count_avg")) or 0.0),
                ),
                "lidar_returns_per_laser_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_lidar_returns_per_laser_avg")) or 0.0),
                ),
                "lidar_detection_ratio_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_lidar_detection_ratio_avg")) or 0.0),
                ),
                "lidar_effective_max_range_m_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_lidar_effective_max_range_m_avg")) or 0.0),
                ),
                "radar_frame_count": max(0, _to_int(manifest.get("phase2_sensor_radar_frame_count"), default=0)),
                "radar_target_count_total": max(
                    0,
                    _to_int(manifest.get("phase2_sensor_radar_target_count_total"), default=0),
                ),
                "radar_ghost_target_count_total": max(
                    0,
                    _to_int(manifest.get("phase2_sensor_radar_ghost_target_count_total"), default=0),
                ),
                "radar_false_positive_count_total": max(
                    0,
                    _to_int(manifest.get("phase2_sensor_radar_false_positive_count_total"), default=0),
                ),
                "radar_false_positive_count_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_radar_false_positive_count_avg")) or 0.0),
                ),
                "radar_false_positive_rate_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_radar_false_positive_rate_avg")) or 0.0),
                ),
                "radar_ghost_target_count_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_radar_ghost_target_count_avg")) or 0.0),
                ),
                "radar_clutter_index_avg": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_radar_clutter_index_avg")) or 0.0),
                ),
                "rig_sweep_checked": bool(manifest.get("phase2_sensor_sweep_checked", False)),
                "rig_sweep_fidelity_tier": (
                    str(manifest.get("phase2_sensor_sweep_fidelity_tier", "")).strip().lower() or "n/a"
                ),
                "rig_sweep_candidate_count": max(
                    0,
                    _to_int(manifest.get("phase2_sensor_sweep_candidate_count"), default=0),
                ),
                "rig_sweep_best_rig_id": str(manifest.get("phase2_sensor_sweep_best_rig_id", "")).strip(),
                "rig_sweep_best_heuristic_score": max(
                    0.0,
                    float(_to_float_or_none(manifest.get("phase2_sensor_sweep_best_heuristic_score")) or 0.0),
                ),
            }
        )

    checked_rows = [row for row in normalized_rows if bool(row.get("checked", False))]
    if not checked_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "fidelity_tier_counts": {},
            "fidelity_tier_score_avg": 0.0,
            "fidelity_tier_score_max": 0.0,
            "highest_fidelity_tier_score_batch_id": "",
            "sensor_frame_count_total": 0,
            "sensor_frame_count_avg": 0.0,
            "sensor_frame_count_max": 0,
            "highest_sensor_frame_count_batch_id": "",
            "sensor_modality_counts_total": {},
            "sensor_camera_frame_count_total": 0,
            "sensor_camera_noise_stddev_px_avg": 0.0,
            "sensor_camera_dynamic_range_stops_avg": 0.0,
            "sensor_camera_visibility_score_avg": 0.0,
            "sensor_camera_motion_blur_level_avg": 0.0,
            "sensor_camera_snr_db_avg": 0.0,
            "sensor_camera_exposure_time_ms_avg": 0.0,
            "sensor_camera_signal_saturation_ratio_avg": 0.0,
            "sensor_camera_rolling_shutter_total_delay_ms_avg": 0.0,
            "sensor_camera_normalized_total_noise_avg": 0.0,
            "sensor_camera_distortion_edge_shift_px_avg": 0.0,
            "sensor_camera_principal_point_offset_norm_avg": 0.0,
            "sensor_camera_effective_focal_length_px_avg": 0.0,
            "sensor_camera_projection_mode_counts_total": {},
            "sensor_camera_gain_db_avg": 0.0,
            "sensor_camera_gamma_avg": 0.0,
            "sensor_camera_white_balance_kelvin_avg": 0.0,
            "sensor_camera_vignetting_edge_darkening_avg": 0.0,
            "sensor_camera_bloom_halo_strength_avg": 0.0,
            "sensor_camera_chromatic_aberration_shift_px_avg": 0.0,
            "sensor_camera_tonemapper_disabled_frame_count_total": 0,
            "sensor_camera_bloom_level_counts_total": {},
            "sensor_camera_depth_enabled_frame_count_total": 0,
            "sensor_camera_depth_min_m_avg": 0.0,
            "sensor_camera_depth_max_m_avg": 0.0,
            "sensor_camera_depth_bit_depth_avg": 0.0,
            "sensor_camera_depth_mode_counts_total": {},
            "sensor_camera_optical_flow_enabled_frame_count_total": 0,
            "sensor_camera_optical_flow_magnitude_px_avg": 0.0,
            "sensor_camera_optical_flow_velocity_direction_counts_total": {},
            "sensor_camera_optical_flow_y_axis_direction_counts_total": {},
            "sensor_lidar_frame_count_total": 0,
            "sensor_lidar_point_count_total": 0,
            "sensor_lidar_point_count_avg": 0.0,
            "sensor_lidar_returns_per_laser_avg": 0.0,
            "sensor_lidar_detection_ratio_avg": 0.0,
            "sensor_lidar_effective_max_range_m_avg": 0.0,
            "sensor_radar_frame_count_total": 0,
            "sensor_radar_target_count_total": 0,
            "sensor_radar_ghost_target_count_total": 0,
            "sensor_radar_false_positive_count_total": 0,
            "sensor_radar_false_positive_count_avg": 0.0,
            "sensor_radar_false_positive_rate_avg": 0.0,
            "sensor_radar_ghost_target_count_avg": 0.0,
            "sensor_radar_clutter_index_avg": 0.0,
            "rig_sweep_evaluated_manifest_count": 0,
            "rig_sweep_fidelity_tier_counts": {},
            "rig_sweep_candidate_count_total": 0,
            "rig_sweep_candidate_count_avg": 0.0,
            "rig_sweep_candidate_count_max": 0,
            "rig_sweep_highest_candidate_count_batch_id": "",
            "rig_sweep_best_heuristic_score_max": 0.0,
            "rig_sweep_highest_best_heuristic_score_batch_id": "",
            "rig_sweep_best_rig_id_counts": {},
        }

    fidelity_tier_counts: dict[str, int] = {}
    sensor_modality_counts_total: dict[str, int] = {}
    sensor_camera_projection_mode_counts_total: dict[str, int] = {}
    sensor_camera_bloom_level_counts_total: dict[str, int] = {}
    sensor_camera_depth_mode_counts_total: dict[str, int] = {}
    sensor_camera_optical_flow_velocity_direction_counts_total: dict[str, int] = {}
    sensor_camera_optical_flow_y_axis_direction_counts_total: dict[str, int] = {}
    for row in checked_rows:
        tier = str(row.get("fidelity_tier", "")).strip().lower() or "n/a"
        fidelity_tier_counts[tier] = fidelity_tier_counts.get(tier, 0) + 1
        modality_counts_row = row.get("modality_counts", {})
        if isinstance(modality_counts_row, dict):
            for raw_key, raw_value in modality_counts_row.items():
                key = str(raw_key).strip().lower()
                if not key:
                    continue
                value = max(0, _to_int(raw_value, default=0))
                sensor_modality_counts_total[key] = sensor_modality_counts_total.get(key, 0) + value
        camera_projection_mode_counts_row = row.get("camera_projection_mode_counts", {})
        if isinstance(camera_projection_mode_counts_row, dict):
            for raw_key, raw_value in camera_projection_mode_counts_row.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                value = max(0, _to_int(raw_value, default=0))
                sensor_camera_projection_mode_counts_total[key] = (
                    sensor_camera_projection_mode_counts_total.get(key, 0) + value
                )
        camera_bloom_level_counts_row = row.get("camera_bloom_level_counts", {})
        if isinstance(camera_bloom_level_counts_row, dict):
            for raw_key, raw_value in camera_bloom_level_counts_row.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                value = max(0, _to_int(raw_value, default=0))
                sensor_camera_bloom_level_counts_total[key] = (
                    sensor_camera_bloom_level_counts_total.get(key, 0) + value
                )
        camera_depth_mode_counts_row = row.get("camera_depth_mode_counts", {})
        if isinstance(camera_depth_mode_counts_row, dict):
            for raw_key, raw_value in camera_depth_mode_counts_row.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                value = max(0, _to_int(raw_value, default=0))
                sensor_camera_depth_mode_counts_total[key] = (
                    sensor_camera_depth_mode_counts_total.get(key, 0) + value
                )
        camera_optical_flow_velocity_direction_counts_row = row.get(
            "camera_optical_flow_velocity_direction_counts",
            {},
        )
        if isinstance(camera_optical_flow_velocity_direction_counts_row, dict):
            for raw_key, raw_value in camera_optical_flow_velocity_direction_counts_row.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                value = max(0, _to_int(raw_value, default=0))
                sensor_camera_optical_flow_velocity_direction_counts_total[key] = (
                    sensor_camera_optical_flow_velocity_direction_counts_total.get(key, 0) + value
                )
        camera_optical_flow_y_axis_direction_counts_row = row.get(
            "camera_optical_flow_y_axis_direction_counts",
            {},
        )
        if isinstance(camera_optical_flow_y_axis_direction_counts_row, dict):
            for raw_key, raw_value in camera_optical_flow_y_axis_direction_counts_row.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                value = max(0, _to_int(raw_value, default=0))
                sensor_camera_optical_flow_y_axis_direction_counts_total[key] = (
                    sensor_camera_optical_flow_y_axis_direction_counts_total.get(key, 0) + value
                )

    fidelity_tier_score_total = sum(float(row.get("fidelity_tier_score", 0.0)) for row in checked_rows)
    sensor_frame_count_total = sum(int(row.get("frame_count", 0)) for row in checked_rows)
    camera_frame_count_total = sum(int(row.get("camera_frame_count", 0)) for row in checked_rows)
    lidar_frame_count_total = sum(int(row.get("lidar_frame_count", 0)) for row in checked_rows)
    radar_frame_count_total = sum(int(row.get("radar_frame_count", 0)) for row in checked_rows)
    camera_noise_weighted_total = sum(
        float(row.get("camera_noise_stddev_px_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_dynamic_range_weighted_total = sum(
        float(row.get("camera_dynamic_range_stops_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_visibility_score_weighted_total = sum(
        float(row.get("camera_visibility_score_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_motion_blur_level_weighted_total = sum(
        float(row.get("camera_motion_blur_level_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_snr_db_weighted_total = sum(
        float(row.get("camera_snr_db_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_exposure_time_ms_weighted_total = sum(
        float(row.get("camera_exposure_time_ms_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_signal_saturation_ratio_weighted_total = sum(
        float(row.get("camera_signal_saturation_ratio_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_rolling_shutter_total_delay_ms_weighted_total = sum(
        float(row.get("camera_rolling_shutter_total_delay_ms_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_normalized_total_noise_weighted_total = sum(
        float(row.get("camera_normalized_total_noise_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_distortion_edge_shift_px_weighted_total = sum(
        float(row.get("camera_distortion_edge_shift_px_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_principal_point_offset_norm_weighted_total = sum(
        float(row.get("camera_principal_point_offset_norm_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_effective_focal_length_px_weighted_total = sum(
        float(row.get("camera_effective_focal_length_px_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_gain_db_weighted_total = sum(
        float(row.get("camera_gain_db_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_gamma_weighted_total = sum(
        float(row.get("camera_gamma_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_white_balance_kelvin_weighted_total = sum(
        float(row.get("camera_white_balance_kelvin_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_vignetting_edge_darkening_weighted_total = sum(
        float(row.get("camera_vignetting_edge_darkening_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_bloom_halo_strength_weighted_total = sum(
        float(row.get("camera_bloom_halo_strength_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_chromatic_aberration_shift_px_weighted_total = sum(
        float(row.get("camera_chromatic_aberration_shift_px_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_tonemapper_disabled_frame_count_total = sum(
        int(row.get("camera_tonemapper_disabled_frame_count", 0)) for row in checked_rows
    )
    camera_depth_enabled_frame_count_total = sum(
        int(row.get("camera_depth_enabled_frame_count", 0)) for row in checked_rows
    )
    camera_depth_min_m_weighted_total = sum(
        float(row.get("camera_depth_min_m_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_depth_max_m_weighted_total = sum(
        float(row.get("camera_depth_max_m_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_depth_bit_depth_weighted_total = sum(
        float(row.get("camera_depth_bit_depth_avg", 0.0)) * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    camera_optical_flow_enabled_frame_count_total = sum(
        int(row.get("camera_optical_flow_enabled_frame_count", 0)) for row in checked_rows
    )
    camera_optical_flow_magnitude_px_weighted_total = sum(
        float(row.get("camera_optical_flow_magnitude_px_avg", 0.0))
        * float(int(row.get("camera_frame_count", 0)))
        for row in checked_rows
    )
    lidar_point_count_total = sum(int(row.get("lidar_point_count_total", 0)) for row in checked_rows)
    lidar_returns_per_laser_weighted_total = sum(
        float(row.get("lidar_returns_per_laser_avg", 0.0)) * float(int(row.get("lidar_frame_count", 0)))
        for row in checked_rows
    )
    lidar_detection_ratio_weighted_total = sum(
        float(row.get("lidar_detection_ratio_avg", 0.0)) * float(int(row.get("lidar_frame_count", 0)))
        for row in checked_rows
    )
    lidar_effective_max_range_m_weighted_total = sum(
        float(row.get("lidar_effective_max_range_m_avg", 0.0)) * float(int(row.get("lidar_frame_count", 0)))
        for row in checked_rows
    )
    radar_target_count_total = sum(int(row.get("radar_target_count_total", 0)) for row in checked_rows)
    radar_ghost_target_count_total = sum(int(row.get("radar_ghost_target_count_total", 0)) for row in checked_rows)
    radar_false_positive_count_total = sum(int(row.get("radar_false_positive_count_total", 0)) for row in checked_rows)
    radar_false_positive_rate_weighted_total = sum(
        float(row.get("radar_false_positive_rate_avg", 0.0)) * float(int(row.get("radar_frame_count", 0)))
        for row in checked_rows
    )
    radar_ghost_target_count_weighted_total = sum(
        float(row.get("radar_ghost_target_count_avg", 0.0)) * float(int(row.get("radar_frame_count", 0)))
        for row in checked_rows
    )
    radar_clutter_index_weighted_total = sum(
        float(row.get("radar_clutter_index_avg", 0.0)) * float(int(row.get("radar_frame_count", 0)))
        for row in checked_rows
    )
    rig_sweep_checked_rows = [row for row in checked_rows if bool(row.get("rig_sweep_checked", False))]
    rig_sweep_fidelity_tier_counts: dict[str, int] = {}
    rig_sweep_best_rig_id_counts: dict[str, int] = {}
    for row in rig_sweep_checked_rows:
        sweep_tier = str(row.get("rig_sweep_fidelity_tier", "")).strip().lower() or "n/a"
        rig_sweep_fidelity_tier_counts[sweep_tier] = rig_sweep_fidelity_tier_counts.get(sweep_tier, 0) + 1
        best_rig_id = str(row.get("rig_sweep_best_rig_id", "")).strip()
        if best_rig_id:
            rig_sweep_best_rig_id_counts[best_rig_id] = rig_sweep_best_rig_id_counts.get(best_rig_id, 0) + 1
    rig_sweep_candidate_count_total = sum(int(row.get("rig_sweep_candidate_count", 0)) for row in rig_sweep_checked_rows)
    if rig_sweep_checked_rows:
        highest_rig_sweep_candidate_count_row = max(
            rig_sweep_checked_rows,
            key=lambda row: (
                int(row.get("rig_sweep_candidate_count", 0)),
                str(row.get("batch_id", "")),
            ),
        )
        highest_rig_sweep_best_score_row = max(
            rig_sweep_checked_rows,
            key=lambda row: (
                float(row.get("rig_sweep_best_heuristic_score", 0.0)),
                str(row.get("batch_id", "")),
            ),
        )
    else:
        highest_rig_sweep_candidate_count_row = {"rig_sweep_candidate_count": 0, "batch_id": ""}
        highest_rig_sweep_best_score_row = {"rig_sweep_best_heuristic_score": 0.0, "batch_id": ""}
    highest_fidelity_tier_score_row = max(
        checked_rows,
        key=lambda row: (
            float(row.get("fidelity_tier_score", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_sensor_frame_count_row = max(
        checked_rows,
        key=lambda row: (
            int(row.get("frame_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    return {
        "evaluated_manifest_count": len(checked_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "fidelity_tier_counts": {key: fidelity_tier_counts[key] for key in sorted(fidelity_tier_counts.keys())},
        "fidelity_tier_score_avg": float(fidelity_tier_score_total / float(len(checked_rows))),
        "fidelity_tier_score_max": float(highest_fidelity_tier_score_row.get("fidelity_tier_score", 0.0)),
        "highest_fidelity_tier_score_batch_id": str(highest_fidelity_tier_score_row.get("batch_id", "")),
        "sensor_frame_count_total": int(sensor_frame_count_total),
        "sensor_frame_count_avg": float(sensor_frame_count_total / float(len(checked_rows))),
        "sensor_frame_count_max": int(highest_sensor_frame_count_row.get("frame_count", 0)),
        "highest_sensor_frame_count_batch_id": str(highest_sensor_frame_count_row.get("batch_id", "")),
        "sensor_modality_counts_total": {
            key: sensor_modality_counts_total[key] for key in sorted(sensor_modality_counts_total.keys())
        },
        "sensor_camera_frame_count_total": int(camera_frame_count_total),
        "sensor_camera_noise_stddev_px_avg": (
            float(camera_noise_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_dynamic_range_stops_avg": (
            float(camera_dynamic_range_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_visibility_score_avg": (
            float(camera_visibility_score_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_motion_blur_level_avg": (
            float(camera_motion_blur_level_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_snr_db_avg": (
            float(camera_snr_db_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_exposure_time_ms_avg": (
            float(camera_exposure_time_ms_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_signal_saturation_ratio_avg": (
            float(camera_signal_saturation_ratio_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_rolling_shutter_total_delay_ms_avg": (
            float(camera_rolling_shutter_total_delay_ms_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_normalized_total_noise_avg": (
            float(camera_normalized_total_noise_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_distortion_edge_shift_px_avg": (
            float(camera_distortion_edge_shift_px_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_principal_point_offset_norm_avg": (
            float(camera_principal_point_offset_norm_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_effective_focal_length_px_avg": (
            float(camera_effective_focal_length_px_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_projection_mode_counts_total": {
            key: sensor_camera_projection_mode_counts_total[key]
            for key in sorted(sensor_camera_projection_mode_counts_total.keys())
        },
        "sensor_camera_gain_db_avg": (
            float(camera_gain_db_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_gamma_avg": (
            float(camera_gamma_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_white_balance_kelvin_avg": (
            float(camera_white_balance_kelvin_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_vignetting_edge_darkening_avg": (
            float(camera_vignetting_edge_darkening_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_bloom_halo_strength_avg": (
            float(camera_bloom_halo_strength_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_chromatic_aberration_shift_px_avg": (
            float(camera_chromatic_aberration_shift_px_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_tonemapper_disabled_frame_count_total": int(
            camera_tonemapper_disabled_frame_count_total
        ),
        "sensor_camera_bloom_level_counts_total": {
            key: sensor_camera_bloom_level_counts_total[key]
            for key in sorted(sensor_camera_bloom_level_counts_total.keys())
        },
        "sensor_camera_depth_enabled_frame_count_total": int(camera_depth_enabled_frame_count_total),
        "sensor_camera_depth_min_m_avg": (
            float(camera_depth_min_m_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_depth_max_m_avg": (
            float(camera_depth_max_m_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_depth_bit_depth_avg": (
            float(camera_depth_bit_depth_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_depth_mode_counts_total": {
            key: sensor_camera_depth_mode_counts_total[key]
            for key in sorted(sensor_camera_depth_mode_counts_total.keys())
        },
        "sensor_camera_optical_flow_enabled_frame_count_total": int(
            camera_optical_flow_enabled_frame_count_total
        ),
        "sensor_camera_optical_flow_magnitude_px_avg": (
            float(camera_optical_flow_magnitude_px_weighted_total / float(camera_frame_count_total))
            if camera_frame_count_total > 0
            else 0.0
        ),
        "sensor_camera_optical_flow_velocity_direction_counts_total": {
            key: sensor_camera_optical_flow_velocity_direction_counts_total[key]
            for key in sorted(sensor_camera_optical_flow_velocity_direction_counts_total.keys())
        },
        "sensor_camera_optical_flow_y_axis_direction_counts_total": {
            key: sensor_camera_optical_flow_y_axis_direction_counts_total[key]
            for key in sorted(sensor_camera_optical_flow_y_axis_direction_counts_total.keys())
        },
        "sensor_lidar_frame_count_total": int(lidar_frame_count_total),
        "sensor_lidar_point_count_total": int(lidar_point_count_total),
        "sensor_lidar_point_count_avg": (
            float(lidar_point_count_total / float(lidar_frame_count_total))
            if lidar_frame_count_total > 0
            else 0.0
        ),
        "sensor_lidar_returns_per_laser_avg": (
            float(lidar_returns_per_laser_weighted_total / float(lidar_frame_count_total))
            if lidar_frame_count_total > 0
            else 0.0
        ),
        "sensor_lidar_detection_ratio_avg": (
            float(lidar_detection_ratio_weighted_total / float(lidar_frame_count_total))
            if lidar_frame_count_total > 0
            else 0.0
        ),
        "sensor_lidar_effective_max_range_m_avg": (
            float(lidar_effective_max_range_m_weighted_total / float(lidar_frame_count_total))
            if lidar_frame_count_total > 0
            else 0.0
        ),
        "sensor_radar_frame_count_total": int(radar_frame_count_total),
        "sensor_radar_target_count_total": int(radar_target_count_total),
        "sensor_radar_ghost_target_count_total": int(radar_ghost_target_count_total),
        "sensor_radar_false_positive_count_total": int(radar_false_positive_count_total),
        "sensor_radar_false_positive_count_avg": (
            float(radar_false_positive_count_total / float(radar_frame_count_total))
            if radar_frame_count_total > 0
            else 0.0
        ),
        "sensor_radar_false_positive_rate_avg": (
            float(radar_false_positive_rate_weighted_total / float(radar_frame_count_total))
            if radar_frame_count_total > 0
            else 0.0
        ),
        "sensor_radar_ghost_target_count_avg": (
            float(radar_ghost_target_count_weighted_total / float(radar_frame_count_total))
            if radar_frame_count_total > 0
            else 0.0
        ),
        "sensor_radar_clutter_index_avg": (
            float(radar_clutter_index_weighted_total / float(radar_frame_count_total))
            if radar_frame_count_total > 0
            else 0.0
        ),
        "rig_sweep_evaluated_manifest_count": len(rig_sweep_checked_rows),
        "rig_sweep_fidelity_tier_counts": {
            key: rig_sweep_fidelity_tier_counts[key] for key in sorted(rig_sweep_fidelity_tier_counts.keys())
        },
        "rig_sweep_candidate_count_total": int(rig_sweep_candidate_count_total),
        "rig_sweep_candidate_count_avg": (
            float(rig_sweep_candidate_count_total / float(len(rig_sweep_checked_rows)))
            if rig_sweep_checked_rows
            else 0.0
        ),
        "rig_sweep_candidate_count_max": int(
            highest_rig_sweep_candidate_count_row.get("rig_sweep_candidate_count", 0)
        ),
        "rig_sweep_highest_candidate_count_batch_id": str(
            highest_rig_sweep_candidate_count_row.get("batch_id", "")
        ),
        "rig_sweep_best_heuristic_score_max": float(
            highest_rig_sweep_best_score_row.get("rig_sweep_best_heuristic_score", 0.0)
        ),
        "rig_sweep_highest_best_heuristic_score_batch_id": str(
            highest_rig_sweep_best_score_row.get("batch_id", "")
        ),
        "rig_sweep_best_rig_id_counts": {
            key: rig_sweep_best_rig_id_counts[key] for key in sorted(rig_sweep_best_rig_id_counts.keys())
        },
    }


def summarize_runtime_native_summary_compare(
    runtime_native_compare_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    artifact_count = 0
    artifacts_with_diffs_count = 0
    versions_total = 0
    comparisons_total = 0
    versions_with_diffs_total = 0
    label_pair_counts: dict[str, int] = {}
    field_diff_counts: dict[str, int] = {}
    versions_with_diffs_counts: dict[str, int] = {}

    for artifact in runtime_native_compare_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_count += 1
        if bool(artifact.get("has_diffs", False)):
            artifacts_with_diffs_count += 1
        try:
            versions_total += max(0, int(artifact.get("version_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            comparisons_total += max(0, int(artifact.get("comparison_count", 0)))
        except (TypeError, ValueError):
            pass
        try:
            versions_with_diffs_total += max(0, int(artifact.get("versions_with_diffs_count", 0)))
        except (TypeError, ValueError):
            pass

        left_label = str(artifact.get("left_label", "")).strip() or "left"
        right_label = str(artifact.get("right_label", "")).strip() or "right"
        label_pair_key = f"{left_label}_vs_{right_label}"
        label_pair_counts[label_pair_key] = label_pair_counts.get(label_pair_key, 0) + 1

        field_diff_counts_raw = artifact.get("field_diff_counts", {})
        if isinstance(field_diff_counts_raw, dict):
            for raw_key, raw_value in field_diff_counts_raw.items():
                key = str(raw_key).strip()
                if not key:
                    continue
                try:
                    parsed_value = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if parsed_value > 0:
                    field_diff_counts[key] = field_diff_counts.get(key, 0) + parsed_value

        versions_with_diffs_raw = artifact.get("versions_with_diffs", [])
        if isinstance(versions_with_diffs_raw, list):
            for item in versions_with_diffs_raw:
                version = str(item).strip()
                if not version:
                    continue
                versions_with_diffs_counts[version] = versions_with_diffs_counts.get(version, 0) + 1

    return {
        "artifact_count": artifact_count,
        "artifacts_with_diffs_count": artifacts_with_diffs_count,
        "artifacts_without_diffs_count": max(0, artifact_count - artifacts_with_diffs_count),
        "versions_total": versions_total,
        "comparisons_total": comparisons_total,
        "versions_with_diffs_total": versions_with_diffs_total,
        "label_pair_counts": {key: label_pair_counts[key] for key in sorted(label_pair_counts.keys())},
        "field_diff_counts": {key: field_diff_counts[key] for key in sorted(field_diff_counts.keys())},
        "versions_with_diffs_counts": {
            key: versions_with_diffs_counts[key] for key in sorted(versions_with_diffs_counts.keys())
        },
    }


def summarize_phase2_log_replay(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "checked": bool(manifest.get("phase2_log_replay_checked", False)),
                "status": _normalize_smoke_status(manifest.get("phase2_log_replay_status", "n/a")),
                "manifest_present": bool(manifest.get("phase2_log_replay_manifest_present", False)),
                "summary_present": bool(manifest.get("phase2_log_replay_summary_present", False)),
                "run_source": str(manifest.get("phase2_log_replay_run_source", "")).strip().lower() or "n/a",
                "run_status": _normalize_smoke_status(manifest.get("phase2_log_replay_run_status", "n/a")),
                "log_id_present": bool(str(manifest.get("phase2_log_replay_log_id", "")).strip()),
                "map_id_present": bool(str(manifest.get("phase2_log_replay_map_id", "")).strip()),
            }
        )

    checked_rows = [row for row in normalized_rows if bool(row.get("checked", False))]
    if not checked_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "status_counts": {},
            "run_source_counts": {},
            "run_status_counts": {},
            "manifest_present_count": 0,
            "summary_present_count": 0,
            "missing_manifest_count": 0,
            "missing_summary_count": 0,
            "log_id_present_count": 0,
            "map_id_present_count": 0,
        }

    status_counts: dict[str, int] = {}
    run_source_counts: dict[str, int] = {}
    run_status_counts: dict[str, int] = {}
    manifest_present_count = 0
    summary_present_count = 0
    log_id_present_count = 0
    map_id_present_count = 0
    for row in checked_rows:
        status = str(row.get("status", "n/a")).strip().lower() or "n/a"
        status_counts[status] = status_counts.get(status, 0) + 1
        run_source = str(row.get("run_source", "n/a")).strip().lower() or "n/a"
        run_source_counts[run_source] = run_source_counts.get(run_source, 0) + 1
        run_status = str(row.get("run_status", "n/a")).strip().lower() or "n/a"
        run_status_counts[run_status] = run_status_counts.get(run_status, 0) + 1
        if bool(row.get("manifest_present", False)):
            manifest_present_count += 1
        if bool(row.get("summary_present", False)):
            summary_present_count += 1
        if bool(row.get("log_id_present", False)):
            log_id_present_count += 1
        if bool(row.get("map_id_present", False)):
            map_id_present_count += 1

    return {
        "evaluated_manifest_count": len(checked_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "status_counts": {key: status_counts[key] for key in sorted(status_counts.keys())},
        "run_source_counts": {key: run_source_counts[key] for key in sorted(run_source_counts.keys())},
        "run_status_counts": {key: run_status_counts[key] for key in sorted(run_status_counts.keys())},
        "manifest_present_count": int(manifest_present_count),
        "summary_present_count": int(summary_present_count),
        "missing_manifest_count": int(len(checked_rows) - manifest_present_count),
        "missing_summary_count": int(len(checked_rows) - summary_present_count),
        "log_id_present_count": int(log_id_present_count),
        "map_id_present_count": int(map_id_present_count),
    }


def summarize_runtime_native_smoke(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue

        object_sim_checked = bool(manifest.get("phase3_object_sim_checked", False))
        object_sim_status = _normalize_smoke_status(manifest.get("phase3_object_sim_status", "n/a"))
        if not object_sim_checked and object_sim_status == "n/a":
            object_sim_status = "n/a"
        elif not object_sim_checked and object_sim_status != "n/a":
            object_sim_checked = True

        log_sim_checked = bool(manifest.get("phase2_log_replay_checked", False))
        log_sim_status = _normalize_smoke_status(manifest.get("phase2_log_replay_status", "n/a"))
        if not log_sim_checked and log_sim_status == "n/a":
            log_sim_status = "n/a"
        elif not log_sim_checked and log_sim_status != "n/a":
            log_sim_checked = True

        map_route_checked = bool(manifest.get("phase2_map_route_checked", False))
        map_routing_checked = bool(manifest.get("phase2_map_routing_checked", False))
        map_toolset_checked = map_routing_checked or map_route_checked
        map_routing_status = _normalize_smoke_status(manifest.get("phase2_map_routing_status", "n/a"))
        map_route_status = _normalize_smoke_status(manifest.get("phase2_map_route_status", "n/a"))
        if not map_toolset_checked and (map_routing_status != "n/a" or map_route_status != "n/a"):
            map_toolset_checked = True
        map_toolset_status = "n/a"
        if map_toolset_checked:
            if "fail" in {map_routing_status, map_route_status}:
                map_toolset_status = "fail"
            elif map_routing_checked and map_route_checked:
                if map_routing_status == "pass" and map_route_status == "pass":
                    map_toolset_status = "pass"
                elif map_routing_status == "n/a" and map_route_status == "n/a":
                    map_toolset_status = "n/a"
                else:
                    map_toolset_status = "partial"
            elif map_routing_checked:
                map_toolset_status = map_routing_status if map_routing_status != "n/a" else "partial"
            elif map_route_checked:
                map_toolset_status = map_route_status if map_route_status != "n/a" else "partial"
            else:
                map_toolset_status = "partial"

        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "object_sim_checked": object_sim_checked,
                "object_sim_status": object_sim_status,
                "log_sim_checked": log_sim_checked,
                "log_sim_status": log_sim_status,
                "map_toolset_checked": map_toolset_checked,
                "map_toolset_status": map_toolset_status,
            }
        )

    def _module_summary(rows: list[dict[str, Any]], *, checked_key: str, status_key: str) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        evaluated_count = 0
        for row in rows:
            checked = bool(row.get(checked_key, False))
            status = _normalize_smoke_status(row.get(status_key, "n/a"))
            if checked:
                evaluated_count += 1
            if checked or status != "n/a":
                status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "evaluated_manifest_count": int(evaluated_count),
            "status_counts": {key: status_counts[key] for key in sorted(status_counts.keys())},
        }

    object_sim_summary = _module_summary(
        normalized_rows,
        checked_key="object_sim_checked",
        status_key="object_sim_status",
    )
    log_sim_summary = _module_summary(
        normalized_rows,
        checked_key="log_sim_checked",
        status_key="log_sim_status",
    )
    map_toolset_summary = _module_summary(
        normalized_rows,
        checked_key="map_toolset_checked",
        status_key="map_toolset_status",
    )

    all_modules_status_counts: dict[str, int] = {}
    all_modules_evaluated_manifest_count = 0
    all_modules_pass_manifest_count = 0
    for row in normalized_rows:
        module_statuses = [
            str(row.get("object_sim_status", "n/a")).strip().lower() or "n/a",
            str(row.get("log_sim_status", "n/a")).strip().lower() or "n/a",
            str(row.get("map_toolset_status", "n/a")).strip().lower() or "n/a",
        ]
        module_checked_flags = [
            bool(row.get("object_sim_checked", False)),
            bool(row.get("log_sim_checked", False)),
            bool(row.get("map_toolset_checked", False)),
        ]
        any_checked = any(module_checked_flags)
        if any_checked:
            all_modules_evaluated_manifest_count += 1

        if all(status == "pass" for status in module_statuses):
            overall_status = "pass"
        elif any(status == "fail" for status in module_statuses):
            overall_status = "fail"
        elif any_checked:
            if all(status == "n/a" for status in module_statuses):
                overall_status = "n/a"
            else:
                overall_status = "partial"
        else:
            overall_status = "n/a"

        all_modules_status_counts[overall_status] = all_modules_status_counts.get(overall_status, 0) + 1
        if overall_status == "pass":
            all_modules_pass_manifest_count += 1

    return {
        "pipeline_manifest_count": len(normalized_rows),
        "evaluated_manifest_count": int(all_modules_evaluated_manifest_count),
        "module_summaries": {
            "object_sim": object_sim_summary,
            "log_sim": log_sim_summary,
            "map_toolset": map_toolset_summary,
        },
        "all_modules_status_counts": {
            key: all_modules_status_counts[key] for key in sorted(all_modules_status_counts.keys())
        },
        "all_modules_pass_manifest_count": int(all_modules_pass_manifest_count),
    }


def summarize_phase3_core_sim(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    def _format_threshold_float_key(value: float) -> str:
        text = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def _float_sort_key(raw_key: str) -> tuple[int, float | str]:
        key_text = str(raw_key).strip()
        try:
            return (0, float(key_text))
        except (TypeError, ValueError):
            return (1, key_text)

    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "enabled": bool(manifest.get("phase3_core_sim_enabled", False)),
                "status": str(manifest.get("phase3_core_sim_status", "")).strip().lower() or "n/a",
                "termination_reason": str(
                    manifest.get("phase3_core_sim_termination_reason", "")
                ).strip(),
                "collision": bool(manifest.get("phase3_core_sim_collision", False)),
                "timeout": bool(manifest.get("phase3_core_sim_timeout", False)),
                "min_ttc_same_lane_sec": _to_float_or_none(
                    manifest.get("phase3_core_sim_min_ttc_same_lane_sec")
                ),
                "min_ttc_any_lane_sec": _to_float_or_none(
                    manifest.get("phase3_core_sim_min_ttc_any_lane_sec")
                ),
                "enable_ego_collision_avoidance": bool(
                    manifest.get("phase3_core_sim_enable_ego_collision_avoidance", False)
                ),
                "avoidance_ttc_threshold_sec": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_core_sim_avoidance_ttc_threshold_sec")) or 0.0,
                ),
                "ego_max_brake_mps2": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_core_sim_ego_max_brake_mps2")) or 0.0,
                ),
                "tire_friction_coeff": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_core_sim_tire_friction_coeff")) or 0.0,
                ),
                "surface_friction_scale": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_core_sim_surface_friction_scale")) or 0.0,
                ),
                "ego_avoidance_brake_event_count": max(
                    0,
                    _to_int(manifest.get("phase3_core_sim_ego_avoidance_brake_event_count"), default=0),
                ),
                "ego_avoidance_applied_brake_mps2_max": max(
                    0.0,
                    _to_float_or_none(
                        manifest.get("phase3_core_sim_ego_avoidance_applied_brake_mps2_max")
                    )
                    or 0.0,
                ),
                "gate_result": str(manifest.get("phase3_core_sim_gate_result", "")).strip().lower() or "n/a",
                "gate_reason_count": max(
                    0,
                    _to_int(manifest.get("phase3_core_sim_gate_reason_count"), default=0),
                ),
                "gate_require_success": bool(manifest.get("phase3_core_sim_gate_require_success", False)),
                "gate_min_ttc_same_lane_sec": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_core_sim_gate_min_ttc_same_lane_sec")) or 0.0,
                ),
                "gate_min_ttc_any_lane_sec": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_core_sim_gate_min_ttc_any_lane_sec")) or 0.0,
                ),
            }
        )

    eligible_rows = [
        row
        for row in normalized_rows
        if bool(row.get("enabled", False))
        or str(row.get("status", "")).strip().lower() != "n/a"
        or str(row.get("gate_result", "")).strip().lower() != "n/a"
    ]
    if not eligible_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "status_counts": {},
            "termination_reason_counts": {},
            "gate_result_counts": {},
            "gate_hold_manifest_count": 0,
            "gate_reason_count_total": 0,
            "gate_require_success_enabled_count": 0,
            "gate_min_ttc_same_lane_sec_counts": {},
            "gate_min_ttc_any_lane_sec_counts": {},
            "success_manifest_count": 0,
            "collision_manifest_count": 0,
            "timeout_manifest_count": 0,
            "min_ttc_same_lane_sec": None,
            "lowest_same_lane_batch_id": "",
            "min_ttc_any_lane_sec": None,
            "lowest_any_lane_batch_id": "",
            "avoidance_enabled_manifest_count": 0,
            "ego_avoidance_brake_event_count_total": 0,
            "max_ego_avoidance_applied_brake_mps2": 0.0,
            "highest_ego_avoidance_applied_brake_batch_id": "",
            "avg_tire_friction_coeff": 0.0,
            "avg_surface_friction_scale": 0.0,
            "max_avoidance_ttc_threshold_sec": 0.0,
            "max_ego_max_brake_mps2": 0.0,
        }

    status_counts: dict[str, int] = {}
    termination_reason_counts: dict[str, int] = {}
    gate_result_counts: dict[str, int] = {}
    gate_min_ttc_same_lane_sec_counts: dict[str, int] = {}
    gate_min_ttc_any_lane_sec_counts: dict[str, int] = {}
    for row in eligible_rows:
        status = str(row.get("status", "")).strip().lower() or "n/a"
        status_counts[status] = status_counts.get(status, 0) + 1
        termination_reason = str(row.get("termination_reason", "")).strip().lower() or "n/a"
        termination_reason_counts[termination_reason] = termination_reason_counts.get(termination_reason, 0) + 1
        gate_result = str(row.get("gate_result", "")).strip().lower() or "n/a"
        gate_result_counts[gate_result] = gate_result_counts.get(gate_result, 0) + 1
        gate_min_ttc_same_lane_sec = float(row.get("gate_min_ttc_same_lane_sec", 0.0) or 0.0)
        if gate_min_ttc_same_lane_sec > 0.0:
            key = _format_threshold_float_key(gate_min_ttc_same_lane_sec)
            gate_min_ttc_same_lane_sec_counts[key] = gate_min_ttc_same_lane_sec_counts.get(key, 0) + 1
        gate_min_ttc_any_lane_sec = float(row.get("gate_min_ttc_any_lane_sec", 0.0) or 0.0)
        if gate_min_ttc_any_lane_sec > 0.0:
            key = _format_threshold_float_key(gate_min_ttc_any_lane_sec)
            gate_min_ttc_any_lane_sec_counts[key] = gate_min_ttc_any_lane_sec_counts.get(key, 0) + 1

    def _min_row(field: str) -> dict[str, Any] | None:
        rows = [row for row in eligible_rows if isinstance(row.get(field), float)]
        if not rows:
            return None
        return min(
            rows,
            key=lambda row: (
                float(row.get(field, 0.0)),
                str(row.get("batch_id", "")),
            ),
        )

    min_same_lane_row = _min_row("min_ttc_same_lane_sec")
    min_any_lane_row = _min_row("min_ttc_any_lane_sec")
    highest_avoidance_brake_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("ego_avoidance_applied_brake_mps2_max", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    avoidance_enabled_rows = [row for row in eligible_rows if bool(row.get("enable_ego_collision_avoidance", False))]

    return {
        "evaluated_manifest_count": len(eligible_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "status_counts": {key: status_counts[key] for key in sorted(status_counts.keys())},
        "termination_reason_counts": {
            key: termination_reason_counts[key]
            for key in sorted(termination_reason_counts.keys())
        },
        "gate_result_counts": {key: gate_result_counts[key] for key in sorted(gate_result_counts.keys())},
        "gate_hold_manifest_count": gate_result_counts.get("hold", 0),
        "gate_reason_count_total": sum(int(row.get("gate_reason_count", 0)) for row in eligible_rows),
        "gate_require_success_enabled_count": sum(
            1 for row in eligible_rows if bool(row.get("gate_require_success", False))
        ),
        "gate_min_ttc_same_lane_sec_counts": {
            key: gate_min_ttc_same_lane_sec_counts[key]
            for key in sorted(gate_min_ttc_same_lane_sec_counts.keys(), key=_float_sort_key)
        },
        "gate_min_ttc_any_lane_sec_counts": {
            key: gate_min_ttc_any_lane_sec_counts[key]
            for key in sorted(gate_min_ttc_any_lane_sec_counts.keys(), key=_float_sort_key)
        },
        "success_manifest_count": sum(
            1 for row in eligible_rows if str(row.get("status", "")).strip().lower() == "success"
        ),
        "collision_manifest_count": sum(1 for row in eligible_rows if bool(row.get("collision", False))),
        "timeout_manifest_count": sum(1 for row in eligible_rows if bool(row.get("timeout", False))),
        "min_ttc_same_lane_sec": (
            float(min_same_lane_row.get("min_ttc_same_lane_sec", 0.0))
            if min_same_lane_row is not None
            else None
        ),
        "lowest_same_lane_batch_id": (
            str(min_same_lane_row.get("batch_id", "")) if min_same_lane_row is not None else ""
        ),
        "min_ttc_any_lane_sec": (
            float(min_any_lane_row.get("min_ttc_any_lane_sec", 0.0))
            if min_any_lane_row is not None
            else None
        ),
        "lowest_any_lane_batch_id": (
            str(min_any_lane_row.get("batch_id", "")) if min_any_lane_row is not None else ""
        ),
        "avoidance_enabled_manifest_count": len(avoidance_enabled_rows),
        "ego_avoidance_brake_event_count_total": sum(
            int(row.get("ego_avoidance_brake_event_count", 0))
            for row in eligible_rows
        ),
        "max_ego_avoidance_applied_brake_mps2": float(
            highest_avoidance_brake_row.get("ego_avoidance_applied_brake_mps2_max", 0.0)
        ),
        "highest_ego_avoidance_applied_brake_batch_id": str(
            highest_avoidance_brake_row.get("batch_id", "")
        ),
        "avg_tire_friction_coeff": sum(
            float(row.get("tire_friction_coeff", 0.0)) for row in eligible_rows
        )
        / float(len(eligible_rows)),
        "avg_surface_friction_scale": sum(
            float(row.get("surface_friction_scale", 0.0)) for row in eligible_rows
        )
        / float(len(eligible_rows)),
        "max_avoidance_ttc_threshold_sec": max(
            float(row.get("avoidance_ttc_threshold_sec", 0.0)) for row in avoidance_enabled_rows
        )
        if avoidance_enabled_rows
        else 0.0,
        "max_ego_max_brake_mps2": max(
            float(row.get("ego_max_brake_mps2", 0.0)) for row in avoidance_enabled_rows
        )
        if avoidance_enabled_rows
        else 0.0,
    }


def summarize_phase3_core_sim_matrix(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    def _returncode_sort_key(raw_key: str) -> tuple[int, int | str]:
        key_text = str(raw_key).strip()
        try:
            return (0, int(key_text))
        except (TypeError, ValueError):
            return (1, key_text)

    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        status_counts: dict[str, int] = {}
        status_counts_raw = manifest.get("phase3_core_sim_matrix_status_counts")
        if isinstance(status_counts_raw, dict):
            for status_raw, count_raw in status_counts_raw.items():
                status = str(status_raw).strip().lower()
                if not status:
                    continue
                status_counts[status] = max(0, _to_int(count_raw, default=0))
        returncode_counts: dict[str, int] = {}
        returncode_counts_raw = manifest.get("phase3_core_sim_matrix_returncode_counts")
        if isinstance(returncode_counts_raw, dict):
            for returncode_raw, count_raw in returncode_counts_raw.items():
                returncode = str(returncode_raw).strip()
                if not returncode:
                    continue
                returncode_counts[returncode] = max(0, _to_int(count_raw, default=0))
        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "enabled": bool(manifest.get("phase3_core_sim_matrix_enabled", False)),
                "schema_version": str(
                    manifest.get("phase3_core_sim_matrix_schema_version", "")
                ).strip(),
                "case_count": max(
                    0,
                    _to_int(manifest.get("phase3_core_sim_matrix_case_count"), default=0),
                ),
                "success_case_count": max(
                    0,
                    _to_int(manifest.get("phase3_core_sim_matrix_success_case_count"), default=0),
                ),
                "failed_case_count": max(
                    0,
                    _to_int(manifest.get("phase3_core_sim_matrix_failed_case_count"), default=0),
                ),
                "all_cases_success": bool(manifest.get("phase3_core_sim_matrix_all_cases_success", False)),
                "collision_case_count": max(
                    0,
                    _to_int(manifest.get("phase3_core_sim_matrix_collision_case_count"), default=0),
                ),
                "timeout_case_count": max(
                    0,
                    _to_int(manifest.get("phase3_core_sim_matrix_timeout_case_count"), default=0),
                ),
                "min_ttc_same_lane_sec_min": _to_float_or_none(
                    manifest.get("phase3_core_sim_matrix_min_ttc_same_lane_sec_min")
                ),
                "lowest_ttc_same_lane_run_id": str(
                    manifest.get("phase3_core_sim_matrix_lowest_ttc_same_lane_run_id", "")
                ).strip(),
                "min_ttc_any_lane_sec_min": _to_float_or_none(
                    manifest.get("phase3_core_sim_matrix_min_ttc_any_lane_sec_min")
                ),
                "lowest_ttc_any_lane_run_id": str(
                    manifest.get("phase3_core_sim_matrix_lowest_ttc_any_lane_run_id", "")
                ).strip(),
                "status_counts": status_counts,
                "returncode_counts": returncode_counts,
            }
        )

    eligible_rows = [
        row
        for row in normalized_rows
        if bool(row.get("enabled", False))
        or int(row.get("case_count", 0)) > 0
        or bool(row.get("status_counts"))
    ]
    if not eligible_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "enabled_manifest_count": 0,
            "case_count_total": 0,
            "success_case_count_total": 0,
            "failed_case_count_total": 0,
            "all_cases_success_manifest_count": 0,
            "collision_case_count_total": 0,
            "timeout_case_count_total": 0,
            "status_counts": {},
            "returncode_counts": {},
            "min_ttc_same_lane_sec_min": None,
            "lowest_ttc_same_lane_batch_id": "",
            "lowest_ttc_same_lane_run_id": "",
            "min_ttc_any_lane_sec_min": None,
            "lowest_ttc_any_lane_batch_id": "",
            "lowest_ttc_any_lane_run_id": "",
        }

    status_counts_total: dict[str, int] = {}
    returncode_counts_total: dict[str, int] = {}
    for row in eligible_rows:
        row_status_counts = row.get("status_counts")
        if isinstance(row_status_counts, dict):
            for status, count in row_status_counts.items():
                status_key = str(status).strip().lower()
                if not status_key:
                    continue
                status_counts_total[status_key] = status_counts_total.get(status_key, 0) + max(
                    0,
                    _to_int(count, default=0),
                )
        row_returncode_counts = row.get("returncode_counts")
        if isinstance(row_returncode_counts, dict):
            for returncode, count in row_returncode_counts.items():
                returncode_key = str(returncode).strip()
                if not returncode_key:
                    continue
                returncode_counts_total[returncode_key] = returncode_counts_total.get(returncode_key, 0) + max(
                    0,
                    _to_int(count, default=0),
                )

    def _min_row(field: str) -> dict[str, Any] | None:
        rows = [row for row in eligible_rows if isinstance(row.get(field), float)]
        if not rows:
            return None
        return min(
            rows,
            key=lambda row: (
                float(row.get(field, 0.0)),
                str(row.get("batch_id", "")),
                str(
                    row.get("lowest_ttc_same_lane_run_id", "")
                    if field == "min_ttc_same_lane_sec_min"
                    else row.get("lowest_ttc_any_lane_run_id", "")
                ),
            ),
        )

    min_same_lane_row = _min_row("min_ttc_same_lane_sec_min")
    min_any_lane_row = _min_row("min_ttc_any_lane_sec_min")

    return {
        "evaluated_manifest_count": len(eligible_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "enabled_manifest_count": sum(1 for row in eligible_rows if bool(row.get("enabled", False))),
        "case_count_total": sum(int(row.get("case_count", 0)) for row in eligible_rows),
        "success_case_count_total": sum(int(row.get("success_case_count", 0)) for row in eligible_rows),
        "failed_case_count_total": sum(int(row.get("failed_case_count", 0)) for row in eligible_rows),
        "all_cases_success_manifest_count": sum(
            1 for row in eligible_rows if bool(row.get("all_cases_success", False))
        ),
        "collision_case_count_total": sum(int(row.get("collision_case_count", 0)) for row in eligible_rows),
        "timeout_case_count_total": sum(int(row.get("timeout_case_count", 0)) for row in eligible_rows),
        "status_counts": {key: status_counts_total[key] for key in sorted(status_counts_total.keys())},
        "returncode_counts": {
            key: returncode_counts_total[key]
            for key in sorted(returncode_counts_total.keys(), key=_returncode_sort_key)
        },
        "min_ttc_same_lane_sec_min": (
            float(min_same_lane_row.get("min_ttc_same_lane_sec_min", 0.0))
            if min_same_lane_row is not None
            else None
        ),
        "lowest_ttc_same_lane_batch_id": (
            str(min_same_lane_row.get("batch_id", "")) if min_same_lane_row is not None else ""
        ),
        "lowest_ttc_same_lane_run_id": (
            str(min_same_lane_row.get("lowest_ttc_same_lane_run_id", ""))
            if min_same_lane_row is not None
            else ""
        ),
        "min_ttc_any_lane_sec_min": (
            float(min_any_lane_row.get("min_ttc_any_lane_sec_min", 0.0))
            if min_any_lane_row is not None
            else None
        ),
        "lowest_ttc_any_lane_batch_id": (
            str(min_any_lane_row.get("batch_id", "")) if min_any_lane_row is not None else ""
        ),
        "lowest_ttc_any_lane_run_id": (
            str(min_any_lane_row.get("lowest_ttc_any_lane_run_id", ""))
            if min_any_lane_row is not None
            else ""
        ),
    }


def summarize_phase3_lane_risk(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    def _format_threshold_float_key(value: float) -> str:
        text = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def _float_sort_key(raw_key: str) -> tuple[int, float | str]:
        key_text = str(raw_key).strip()
        try:
            return (0, float(key_text))
        except (TypeError, ValueError):
            return (1, key_text)

    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "run_count": max(
                    0,
                    _to_int(manifest.get("phase3_lane_risk_summary_run_count"), default=0),
                ),
                "min_ttc_same_lane_sec": _to_float_or_none(manifest.get("phase3_lane_risk_min_ttc_same_lane_sec")),
                "min_ttc_adjacent_lane_sec": _to_float_or_none(
                    manifest.get("phase3_lane_risk_min_ttc_adjacent_lane_sec")
                ),
                "min_ttc_any_lane_sec": _to_float_or_none(manifest.get("phase3_lane_risk_min_ttc_any_lane_sec")),
                "gate_result": str(manifest.get("phase3_lane_risk_gate_result", "")).strip().lower() or "n/a",
                "gate_reason_count": max(
                    0,
                    _to_int(manifest.get("phase3_lane_risk_gate_reason_count"), default=0),
                ),
                "gate_min_ttc_same_lane_sec": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_lane_risk_gate_min_ttc_same_lane_sec"))
                    or 0.0,
                ),
                "gate_min_ttc_adjacent_lane_sec": max(
                    0.0,
                    _to_float_or_none(
                        manifest.get("phase3_lane_risk_gate_min_ttc_adjacent_lane_sec")
                    )
                    or 0.0,
                ),
                "gate_min_ttc_any_lane_sec": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_lane_risk_gate_min_ttc_any_lane_sec"))
                    or 0.0,
                ),
                "gate_max_ttc_under_3s_same_lane_total": max(
                    0,
                    _to_int(
                        manifest.get("phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total"),
                        default=0,
                    ),
                ),
                "gate_max_ttc_under_3s_adjacent_lane_total": max(
                    0,
                    _to_int(
                        manifest.get("phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total"),
                        default=0,
                    ),
                ),
                "gate_max_ttc_under_3s_any_lane_total": max(
                    0,
                    _to_int(
                        manifest.get("phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total"),
                        default=0,
                    ),
                ),
                "ttc_under_3s_same_lane_total": max(
                    0,
                    _to_int(manifest.get("phase3_lane_risk_ttc_under_3s_same_lane_total"), default=0),
                ),
                "ttc_under_3s_adjacent_lane_total": max(
                    0,
                    _to_int(manifest.get("phase3_lane_risk_ttc_under_3s_adjacent_lane_total"), default=0),
                ),
                "same_lane_rows_total": max(
                    0,
                    _to_int(manifest.get("phase3_lane_risk_same_lane_rows_total"), default=0),
                ),
                "adjacent_lane_rows_total": max(
                    0,
                    _to_int(manifest.get("phase3_lane_risk_adjacent_lane_rows_total"), default=0),
                ),
                "other_lane_rows_total": max(
                    0,
                    _to_int(manifest.get("phase3_lane_risk_other_lane_rows_total"), default=0),
                ),
            }
        )

    eligible_rows = [
        row
        for row in normalized_rows
        if int(row.get("run_count", 0)) > 0
        or str(row.get("gate_result", "")).strip().lower() != "n/a"
    ]
    if not eligible_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "lane_risk_summary_run_count_total": 0,
            "gate_result_counts": {},
            "gate_reason_count_total": 0,
            "gate_min_ttc_same_lane_sec_counts": {},
            "gate_min_ttc_adjacent_lane_sec_counts": {},
            "gate_min_ttc_any_lane_sec_counts": {},
            "gate_max_ttc_under_3s_same_lane_total_counts": {},
            "gate_max_ttc_under_3s_adjacent_lane_total_counts": {},
            "gate_max_ttc_under_3s_any_lane_total_counts": {},
            "min_ttc_same_lane_sec": None,
            "lowest_same_lane_batch_id": "",
            "min_ttc_adjacent_lane_sec": None,
            "lowest_adjacent_lane_batch_id": "",
            "min_ttc_any_lane_sec": None,
            "lowest_any_lane_batch_id": "",
            "ttc_under_3s_same_lane_total": 0,
            "ttc_under_3s_adjacent_lane_total": 0,
            "same_lane_rows_total": 0,
            "adjacent_lane_rows_total": 0,
            "other_lane_rows_total": 0,
        }

    gate_result_counts: dict[str, int] = {}
    gate_min_ttc_same_lane_sec_counts: dict[str, int] = {}
    gate_min_ttc_adjacent_lane_sec_counts: dict[str, int] = {}
    gate_min_ttc_any_lane_sec_counts: dict[str, int] = {}
    gate_max_ttc_under_3s_same_lane_total_counts: dict[str, int] = {}
    gate_max_ttc_under_3s_adjacent_lane_total_counts: dict[str, int] = {}
    gate_max_ttc_under_3s_any_lane_total_counts: dict[str, int] = {}
    for row in eligible_rows:
        gate_result = str(row.get("gate_result", "")).strip().lower() or "n/a"
        gate_result_counts[gate_result] = gate_result_counts.get(gate_result, 0) + 1
        gate_min_ttc_same_lane_sec_key = _format_threshold_float_key(
            max(0.0, float(row.get("gate_min_ttc_same_lane_sec", 0.0) or 0.0))
        )
        gate_min_ttc_same_lane_sec_counts[gate_min_ttc_same_lane_sec_key] = (
            gate_min_ttc_same_lane_sec_counts.get(gate_min_ttc_same_lane_sec_key, 0) + 1
        )
        gate_min_ttc_adjacent_lane_sec_key = _format_threshold_float_key(
            max(0.0, float(row.get("gate_min_ttc_adjacent_lane_sec", 0.0) or 0.0))
        )
        gate_min_ttc_adjacent_lane_sec_counts[gate_min_ttc_adjacent_lane_sec_key] = (
            gate_min_ttc_adjacent_lane_sec_counts.get(gate_min_ttc_adjacent_lane_sec_key, 0) + 1
        )
        gate_min_ttc_any_lane_sec_key = _format_threshold_float_key(
            max(0.0, float(row.get("gate_min_ttc_any_lane_sec", 0.0) or 0.0))
        )
        gate_min_ttc_any_lane_sec_counts[gate_min_ttc_any_lane_sec_key] = (
            gate_min_ttc_any_lane_sec_counts.get(gate_min_ttc_any_lane_sec_key, 0) + 1
        )
        gate_max_ttc_under_3s_same_lane_total_key = str(
            max(0, int(row.get("gate_max_ttc_under_3s_same_lane_total", 0) or 0))
        )
        gate_max_ttc_under_3s_same_lane_total_counts[gate_max_ttc_under_3s_same_lane_total_key] = (
            gate_max_ttc_under_3s_same_lane_total_counts.get(
                gate_max_ttc_under_3s_same_lane_total_key, 0
            )
            + 1
        )
        gate_max_ttc_under_3s_adjacent_lane_total_key = str(
            max(0, int(row.get("gate_max_ttc_under_3s_adjacent_lane_total", 0) or 0))
        )
        gate_max_ttc_under_3s_adjacent_lane_total_counts[
            gate_max_ttc_under_3s_adjacent_lane_total_key
        ] = (
            gate_max_ttc_under_3s_adjacent_lane_total_counts.get(
                gate_max_ttc_under_3s_adjacent_lane_total_key, 0
            )
            + 1
        )
        gate_max_ttc_under_3s_any_lane_total_key = str(
            max(0, int(row.get("gate_max_ttc_under_3s_any_lane_total", 0) or 0))
        )
        gate_max_ttc_under_3s_any_lane_total_counts[gate_max_ttc_under_3s_any_lane_total_key] = (
            gate_max_ttc_under_3s_any_lane_total_counts.get(
                gate_max_ttc_under_3s_any_lane_total_key, 0
            )
            + 1
        )

    def _min_row(field: str) -> dict[str, Any] | None:
        rows = [row for row in eligible_rows if isinstance(row.get(field), float)]
        if not rows:
            return None
        return min(
            rows,
            key=lambda row: (
                float(row.get(field, 0.0)),
                str(row.get("batch_id", "")),
            ),
        )

    min_same_lane_row = _min_row("min_ttc_same_lane_sec")
    min_adjacent_lane_row = _min_row("min_ttc_adjacent_lane_sec")
    min_any_lane_row = _min_row("min_ttc_any_lane_sec")

    return {
        "evaluated_manifest_count": len(eligible_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "lane_risk_summary_run_count_total": sum(int(row.get("run_count", 0)) for row in eligible_rows),
        "gate_result_counts": {key: gate_result_counts[key] for key in sorted(gate_result_counts.keys())},
        "gate_reason_count_total": sum(int(row.get("gate_reason_count", 0)) for row in eligible_rows),
        "gate_min_ttc_same_lane_sec_counts": {
            key: gate_min_ttc_same_lane_sec_counts[key]
            for key in sorted(gate_min_ttc_same_lane_sec_counts.keys(), key=_float_sort_key)
        },
        "gate_min_ttc_adjacent_lane_sec_counts": {
            key: gate_min_ttc_adjacent_lane_sec_counts[key]
            for key in sorted(gate_min_ttc_adjacent_lane_sec_counts.keys(), key=_float_sort_key)
        },
        "gate_min_ttc_any_lane_sec_counts": {
            key: gate_min_ttc_any_lane_sec_counts[key]
            for key in sorted(gate_min_ttc_any_lane_sec_counts.keys(), key=_float_sort_key)
        },
        "gate_max_ttc_under_3s_same_lane_total_counts": {
            key: gate_max_ttc_under_3s_same_lane_total_counts[key]
            for key in sorted(gate_max_ttc_under_3s_same_lane_total_counts.keys(), key=lambda raw_key: int(raw_key))
        },
        "gate_max_ttc_under_3s_adjacent_lane_total_counts": {
            key: gate_max_ttc_under_3s_adjacent_lane_total_counts[key]
            for key in sorted(
                gate_max_ttc_under_3s_adjacent_lane_total_counts.keys(),
                key=lambda raw_key: int(raw_key),
            )
        },
        "gate_max_ttc_under_3s_any_lane_total_counts": {
            key: gate_max_ttc_under_3s_any_lane_total_counts[key]
            for key in sorted(gate_max_ttc_under_3s_any_lane_total_counts.keys(), key=lambda raw_key: int(raw_key))
        },
        "min_ttc_same_lane_sec": (
            float(min_same_lane_row.get("min_ttc_same_lane_sec", 0.0))
            if min_same_lane_row is not None
            else None
        ),
        "lowest_same_lane_batch_id": (
            str(min_same_lane_row.get("batch_id", "")) if min_same_lane_row is not None else ""
        ),
        "min_ttc_adjacent_lane_sec": (
            float(min_adjacent_lane_row.get("min_ttc_adjacent_lane_sec", 0.0))
            if min_adjacent_lane_row is not None
            else None
        ),
        "lowest_adjacent_lane_batch_id": (
            str(min_adjacent_lane_row.get("batch_id", "")) if min_adjacent_lane_row is not None else ""
        ),
        "min_ttc_any_lane_sec": (
            float(min_any_lane_row.get("min_ttc_any_lane_sec", 0.0))
            if min_any_lane_row is not None
            else None
        ),
        "lowest_any_lane_batch_id": (
            str(min_any_lane_row.get("batch_id", "")) if min_any_lane_row is not None else ""
        ),
        "ttc_under_3s_same_lane_total": sum(
            int(row.get("ttc_under_3s_same_lane_total", 0))
            for row in eligible_rows
        ),
        "ttc_under_3s_adjacent_lane_total": sum(
            int(row.get("ttc_under_3s_adjacent_lane_total", 0))
            for row in eligible_rows
        ),
        "same_lane_rows_total": sum(
            int(row.get("same_lane_rows_total", 0))
            for row in eligible_rows
        ),
        "adjacent_lane_rows_total": sum(
            int(row.get("adjacent_lane_rows_total", 0))
            for row in eligible_rows
        ),
        "other_lane_rows_total": sum(
            int(row.get("other_lane_rows_total", 0))
            for row in eligible_rows
        ),
    }


def summarize_phase3_dataset_traffic(pipeline_manifests: list[dict[str, Any]]) -> dict[str, Any]:
    def _format_threshold_float_key(value: float) -> str:
        text = f"{float(value):.6f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def _float_sort_key(raw_key: str) -> tuple[int, float | str]:
        key_text = str(raw_key).strip()
        try:
            return (0, float(key_text))
        except (TypeError, ValueError):
            return (1, key_text)

    normalized_rows: list[dict[str, Any]] = []
    for manifest in pipeline_manifests:
        if not isinstance(manifest, dict):
            continue
        run_status_counts_raw = manifest.get("phase3_dataset_traffic_run_status_counts")
        run_status_counts: dict[str, int] = {}
        if isinstance(run_status_counts_raw, dict):
            for status_raw, count_raw in run_status_counts_raw.items():
                status = str(status_raw).strip().lower()
                if not status:
                    continue
                run_status_counts[status] = max(
                    0,
                    _to_int(count_raw, default=0),
                )
        lane_indices_raw = manifest.get("phase3_dataset_traffic_lane_indices")
        lane_indices: set[int] = set()
        if isinstance(lane_indices_raw, list):
            for lane_index_raw in lane_indices_raw:
                try:
                    lane_index = int(lane_index_raw)
                except (TypeError, ValueError):
                    continue
                lane_indices.add(lane_index)
        normalized_rows.append(
            {
                "batch_id": str(manifest.get("batch_id", "")).strip() or "batch_unknown",
                "gate_result": str(manifest.get("phase3_dataset_traffic_gate_result", "")).strip().lower() or "n/a",
                "gate_reason_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_gate_reason_count"), default=0),
                ),
                "gate_min_run_summary_count": max(
                    0,
                    _to_int(
                        manifest.get("phase3_dataset_traffic_gate_min_run_summary_count"),
                        default=0,
                    ),
                ),
                "gate_min_traffic_profile_count": max(
                    0,
                    _to_int(
                        manifest.get("phase3_dataset_traffic_gate_min_traffic_profile_count"),
                        default=0,
                    ),
                ),
                "gate_min_actor_pattern_count": max(
                    0,
                    _to_int(
                        manifest.get("phase3_dataset_traffic_gate_min_actor_pattern_count"),
                        default=0,
                    ),
                ),
                "gate_min_avg_npc_count": max(
                    0.0,
                    _to_float_or_none(
                        manifest.get("phase3_dataset_traffic_gate_min_avg_npc_count")
                    )
                    or 0.0,
                ),
                "run_summary_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_run_summary_count"), default=0),
                ),
                "run_status_counts": run_status_counts,
                "traffic_profile_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_profile_count"), default=0),
                ),
                "traffic_profile_ids": _normalize_text_list(manifest.get("phase3_dataset_traffic_profile_ids")),
                "traffic_profile_source_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_profile_source_count"), default=0),
                ),
                "traffic_profile_source_ids": _normalize_text_list(
                    manifest.get("phase3_dataset_traffic_profile_source_ids")
                ),
                "traffic_actor_pattern_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_actor_pattern_count"), default=0),
                ),
                "traffic_actor_pattern_ids": _normalize_text_list(
                    manifest.get("phase3_dataset_traffic_actor_pattern_ids")
                ),
                "traffic_lane_profile_signature_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_lane_profile_signature_count"), default=0),
                ),
                "traffic_lane_profile_signatures": _normalize_text_list(
                    manifest.get("phase3_dataset_traffic_lane_profile_signatures")
                ),
                "traffic_npc_count_sample_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_npc_count_sample_count"), default=0),
                ),
                "traffic_npc_count_min": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_npc_count_min"), default=0),
                ),
                "traffic_npc_count_avg": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_dataset_traffic_npc_count_avg")) or 0.0,
                ),
                "traffic_npc_count_max": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_npc_count_max"), default=0),
                ),
                "traffic_npc_initial_gap_m_sample_count": max(
                    0,
                    _to_int(
                        manifest.get("phase3_dataset_traffic_npc_initial_gap_m_sample_count"),
                        default=0,
                    ),
                ),
                "traffic_npc_initial_gap_m_avg": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_dataset_traffic_npc_initial_gap_m_avg")) or 0.0,
                ),
                "traffic_npc_gap_step_m_sample_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_npc_gap_step_m_sample_count"), default=0),
                ),
                "traffic_npc_gap_step_m_avg": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_dataset_traffic_npc_gap_step_m_avg")) or 0.0,
                ),
                "traffic_npc_speed_scale_sample_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_npc_speed_scale_sample_count"), default=0),
                ),
                "traffic_npc_speed_scale_avg": max(
                    0.0,
                    _to_float_or_none(manifest.get("phase3_dataset_traffic_npc_speed_scale_avg")) or 0.0,
                ),
                "traffic_npc_speed_jitter_mps_sample_count": max(
                    0,
                    _to_int(
                        manifest.get("phase3_dataset_traffic_npc_speed_jitter_mps_sample_count"),
                        default=0,
                    ),
                ),
                "traffic_npc_speed_jitter_mps_avg": max(
                    0.0,
                    _to_float_or_none(
                        manifest.get("phase3_dataset_traffic_npc_speed_jitter_mps_avg")
                    )
                    or 0.0,
                ),
                "traffic_lane_index_unique_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_traffic_lane_index_unique_count"), default=0),
                ),
                "traffic_lane_indices": sorted(lane_indices),
                "dataset_manifest_counts_rows": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_manifest_counts_rows"), default=0),
                ),
                "dataset_manifest_run_summary_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_manifest_run_summary_count"), default=0),
                ),
                "dataset_manifest_release_summary_count": max(
                    0,
                    _to_int(manifest.get("phase3_dataset_manifest_release_summary_count"), default=0),
                ),
                "dataset_manifest_versions": _normalize_text_list(manifest.get("phase3_dataset_manifest_versions")),
            }
        )

    eligible_rows = [
        row
        for row in normalized_rows
        if int(row.get("run_summary_count", 0)) > 0
        or int(row.get("dataset_manifest_run_summary_count", 0)) > 0
    ]
    if not eligible_rows:
        return {
            "evaluated_manifest_count": 0,
            "pipeline_manifest_count": len(normalized_rows),
            "gate_result_counts": {},
            "gate_hold_manifest_count": 0,
            "gate_reason_count_total": 0,
            "gate_min_run_summary_count_counts": {},
            "gate_min_traffic_profile_count_counts": {},
            "gate_min_actor_pattern_count_counts": {},
            "gate_min_avg_npc_count_counts": {},
            "run_summary_count_total": 0,
            "run_status_counts": {},
            "traffic_profile_count_total": 0,
            "traffic_profile_count_avg": 0.0,
            "max_traffic_profile_count": 0,
            "highest_traffic_profile_batch_id": "",
            "traffic_profile_unique_count": 0,
            "traffic_profile_ids": [],
            "traffic_profile_source_count_total": 0,
            "traffic_profile_source_count_avg": 0.0,
            "max_traffic_profile_source_count": 0,
            "highest_traffic_profile_source_batch_id": "",
            "traffic_profile_source_unique_count": 0,
            "traffic_profile_source_ids": [],
            "traffic_actor_pattern_count_total": 0,
            "traffic_actor_pattern_count_avg": 0.0,
            "max_traffic_actor_pattern_count": 0,
            "highest_traffic_actor_pattern_batch_id": "",
            "traffic_actor_pattern_unique_count": 0,
            "traffic_actor_pattern_ids": [],
            "traffic_lane_profile_signature_count_total": 0,
            "traffic_lane_profile_signature_count_avg": 0.0,
            "max_traffic_lane_profile_signature_count": 0,
            "highest_traffic_lane_profile_signature_batch_id": "",
            "traffic_lane_profile_signature_unique_count": 0,
            "traffic_lane_profile_signatures": [],
            "traffic_npc_count_sample_count_total": 0,
            "traffic_npc_count_avg_avg": 0.0,
            "traffic_npc_count_avg_max": 0.0,
            "highest_traffic_npc_avg_batch_id": "",
            "traffic_npc_count_max_max": 0,
            "highest_traffic_npc_max_batch_id": "",
            "traffic_npc_initial_gap_m_sample_count_total": 0,
            "traffic_npc_initial_gap_m_avg_avg": 0.0,
            "traffic_npc_initial_gap_m_avg_max": 0.0,
            "highest_traffic_npc_initial_gap_m_avg_batch_id": "",
            "traffic_npc_gap_step_m_sample_count_total": 0,
            "traffic_npc_gap_step_m_avg_avg": 0.0,
            "traffic_npc_gap_step_m_avg_max": 0.0,
            "highest_traffic_npc_gap_step_m_avg_batch_id": "",
            "traffic_npc_speed_scale_sample_count_total": 0,
            "traffic_npc_speed_scale_avg_avg": 0.0,
            "traffic_npc_speed_scale_avg_max": 0.0,
            "highest_traffic_npc_speed_scale_avg_batch_id": "",
            "traffic_npc_speed_jitter_mps_sample_count_total": 0,
            "traffic_npc_speed_jitter_mps_avg_avg": 0.0,
            "traffic_npc_speed_jitter_mps_avg_max": 0.0,
            "highest_traffic_npc_speed_jitter_mps_avg_batch_id": "",
            "traffic_lane_index_unique_count_total": 0,
            "traffic_lane_index_unique_count_avg": 0.0,
            "max_traffic_lane_index_unique_count": 0,
            "highest_traffic_lane_index_unique_batch_id": "",
            "traffic_lane_indices_unique_count": 0,
            "traffic_lane_indices": [],
            "dataset_manifest_counts_rows_total": 0,
            "dataset_manifest_run_summary_count_total": 0,
            "dataset_manifest_release_summary_count_total": 0,
            "dataset_manifest_versions": [],
        }

    gate_result_counts: dict[str, int] = {}
    run_status_counts_total: dict[str, int] = {}
    gate_min_run_summary_count_counts: dict[str, int] = {}
    gate_min_traffic_profile_count_counts: dict[str, int] = {}
    gate_min_actor_pattern_count_counts: dict[str, int] = {}
    gate_min_avg_npc_count_counts: dict[str, int] = {}
    traffic_profile_ids: set[str] = set()
    traffic_profile_source_ids: set[str] = set()
    traffic_actor_pattern_ids: set[str] = set()
    traffic_lane_profile_signatures: set[str] = set()
    traffic_lane_indices: set[int] = set()
    dataset_manifest_versions: set[str] = set()
    traffic_npc_initial_gap_m_sample_count_total = 0
    traffic_npc_gap_step_m_sample_count_total = 0
    traffic_npc_speed_scale_sample_count_total = 0
    traffic_npc_speed_jitter_mps_sample_count_total = 0
    traffic_npc_initial_gap_m_weighted_sum = 0.0
    traffic_npc_gap_step_m_weighted_sum = 0.0
    traffic_npc_speed_scale_weighted_sum = 0.0
    traffic_npc_speed_jitter_mps_weighted_sum = 0.0
    for row in eligible_rows:
        gate_result = str(row.get("gate_result", "")).strip().lower() or "n/a"
        gate_result_counts[gate_result] = gate_result_counts.get(gate_result, 0) + 1
        gate_min_run_summary_count_key = str(max(0, _to_int(row.get("gate_min_run_summary_count"), default=0)))
        gate_min_run_summary_count_counts[gate_min_run_summary_count_key] = (
            gate_min_run_summary_count_counts.get(gate_min_run_summary_count_key, 0) + 1
        )
        gate_min_traffic_profile_count_key = str(
            max(0, _to_int(row.get("gate_min_traffic_profile_count"), default=0))
        )
        gate_min_traffic_profile_count_counts[gate_min_traffic_profile_count_key] = (
            gate_min_traffic_profile_count_counts.get(gate_min_traffic_profile_count_key, 0) + 1
        )
        gate_min_actor_pattern_count_key = str(
            max(0, _to_int(row.get("gate_min_actor_pattern_count"), default=0))
        )
        gate_min_actor_pattern_count_counts[gate_min_actor_pattern_count_key] = (
            gate_min_actor_pattern_count_counts.get(gate_min_actor_pattern_count_key, 0) + 1
        )
        gate_min_avg_npc_count_key = _format_threshold_float_key(
            max(0.0, _to_float_or_none(row.get("gate_min_avg_npc_count")) or 0.0)
        )
        gate_min_avg_npc_count_counts[gate_min_avg_npc_count_key] = (
            gate_min_avg_npc_count_counts.get(gate_min_avg_npc_count_key, 0) + 1
        )
        run_status_counts = row.get("run_status_counts")
        if isinstance(run_status_counts, dict):
            for status, count in run_status_counts.items():
                status_key = str(status).strip().lower()
                if not status_key:
                    continue
                run_status_counts_total[status_key] = run_status_counts_total.get(status_key, 0) + max(
                    0,
                    _to_int(count, default=0),
                )
        for profile_id in row.get("traffic_profile_ids", []):
            profile_id_text = str(profile_id).strip()
            if profile_id_text:
                traffic_profile_ids.add(profile_id_text)
        for profile_source_id in row.get("traffic_profile_source_ids", []):
            profile_source_id_text = str(profile_source_id).strip()
            if profile_source_id_text:
                traffic_profile_source_ids.add(profile_source_id_text)
        for actor_pattern_id in row.get("traffic_actor_pattern_ids", []):
            actor_pattern_id_text = str(actor_pattern_id).strip()
            if actor_pattern_id_text:
                traffic_actor_pattern_ids.add(actor_pattern_id_text)
        for signature in row.get("traffic_lane_profile_signatures", []):
            signature_text = str(signature).strip()
            if signature_text:
                traffic_lane_profile_signatures.add(signature_text)
        for lane_index in row.get("traffic_lane_indices", []):
            try:
                lane_index_int = int(lane_index)
            except (TypeError, ValueError):
                continue
            traffic_lane_indices.add(lane_index_int)
        for version in row.get("dataset_manifest_versions", []):
            version_text = str(version).strip()
            if version_text:
                dataset_manifest_versions.add(version_text)
        traffic_npc_initial_gap_m_sample_count = max(
            0,
            _to_int(row.get("traffic_npc_initial_gap_m_sample_count"), default=0),
        )
        traffic_npc_initial_gap_m_sample_count_total += traffic_npc_initial_gap_m_sample_count
        traffic_npc_initial_gap_m_weighted_sum += (
            float(row.get("traffic_npc_initial_gap_m_avg", 0.0)) * float(traffic_npc_initial_gap_m_sample_count)
        )
        traffic_npc_gap_step_m_sample_count = max(
            0,
            _to_int(row.get("traffic_npc_gap_step_m_sample_count"), default=0),
        )
        traffic_npc_gap_step_m_sample_count_total += traffic_npc_gap_step_m_sample_count
        traffic_npc_gap_step_m_weighted_sum += (
            float(row.get("traffic_npc_gap_step_m_avg", 0.0)) * float(traffic_npc_gap_step_m_sample_count)
        )
        traffic_npc_speed_scale_sample_count = max(
            0,
            _to_int(row.get("traffic_npc_speed_scale_sample_count"), default=0),
        )
        traffic_npc_speed_scale_sample_count_total += traffic_npc_speed_scale_sample_count
        traffic_npc_speed_scale_weighted_sum += (
            float(row.get("traffic_npc_speed_scale_avg", 0.0)) * float(traffic_npc_speed_scale_sample_count)
        )
        traffic_npc_speed_jitter_mps_sample_count = max(
            0,
            _to_int(row.get("traffic_npc_speed_jitter_mps_sample_count"), default=0),
        )
        traffic_npc_speed_jitter_mps_sample_count_total += traffic_npc_speed_jitter_mps_sample_count
        traffic_npc_speed_jitter_mps_weighted_sum += (
            float(row.get("traffic_npc_speed_jitter_mps_avg", 0.0))
            * float(traffic_npc_speed_jitter_mps_sample_count)
        )

    highest_profile_row = max(
        eligible_rows,
        key=lambda row: (
            int(row.get("traffic_profile_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_profile_source_row = max(
        eligible_rows,
        key=lambda row: (
            int(row.get("traffic_profile_source_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_actor_pattern_row = max(
        eligible_rows,
        key=lambda row: (
            int(row.get("traffic_actor_pattern_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_lane_profile_signature_row = max(
        eligible_rows,
        key=lambda row: (
            int(row.get("traffic_lane_profile_signature_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_npc_avg_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("traffic_npc_count_avg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_npc_max_row = max(
        eligible_rows,
        key=lambda row: (
            int(row.get("traffic_npc_count_max", 0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_npc_initial_gap_avg_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("traffic_npc_initial_gap_m_avg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_npc_gap_step_avg_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("traffic_npc_gap_step_m_avg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_npc_speed_scale_avg_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("traffic_npc_speed_scale_avg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_npc_speed_jitter_avg_row = max(
        eligible_rows,
        key=lambda row: (
            float(row.get("traffic_npc_speed_jitter_mps_avg", 0.0)),
            str(row.get("batch_id", "")),
        ),
    )
    highest_lane_unique_row = max(
        eligible_rows,
        key=lambda row: (
            int(row.get("traffic_lane_index_unique_count", 0)),
            str(row.get("batch_id", "")),
        ),
    )

    traffic_profile_count_total = sum(int(row.get("traffic_profile_count", 0)) for row in eligible_rows)
    traffic_profile_source_count_total = sum(
        int(row.get("traffic_profile_source_count", 0)) for row in eligible_rows
    )
    traffic_actor_pattern_count_total = sum(int(row.get("traffic_actor_pattern_count", 0)) for row in eligible_rows)
    traffic_lane_profile_signature_count_total = sum(
        int(row.get("traffic_lane_profile_signature_count", 0)) for row in eligible_rows
    )
    traffic_npc_count_sample_count_total = sum(int(row.get("traffic_npc_count_sample_count", 0)) for row in eligible_rows)
    traffic_npc_count_avg_total = sum(float(row.get("traffic_npc_count_avg", 0.0)) for row in eligible_rows)
    traffic_lane_index_unique_count_total = sum(int(row.get("traffic_lane_index_unique_count", 0)) for row in eligible_rows)

    return {
        "evaluated_manifest_count": len(eligible_rows),
        "pipeline_manifest_count": len(normalized_rows),
        "gate_result_counts": {key: gate_result_counts[key] for key in sorted(gate_result_counts.keys())},
        "gate_hold_manifest_count": gate_result_counts.get("hold", 0),
        "gate_reason_count_total": sum(int(row.get("gate_reason_count", 0)) for row in eligible_rows),
        "gate_min_run_summary_count_counts": {
            key: gate_min_run_summary_count_counts[key]
            for key in sorted(gate_min_run_summary_count_counts.keys(), key=lambda raw_key: int(raw_key))
        },
        "gate_min_traffic_profile_count_counts": {
            key: gate_min_traffic_profile_count_counts[key]
            for key in sorted(gate_min_traffic_profile_count_counts.keys(), key=lambda raw_key: int(raw_key))
        },
        "gate_min_actor_pattern_count_counts": {
            key: gate_min_actor_pattern_count_counts[key]
            for key in sorted(gate_min_actor_pattern_count_counts.keys(), key=lambda raw_key: int(raw_key))
        },
        "gate_min_avg_npc_count_counts": {
            key: gate_min_avg_npc_count_counts[key]
            for key in sorted(gate_min_avg_npc_count_counts.keys(), key=_float_sort_key)
        },
        "run_summary_count_total": sum(int(row.get("run_summary_count", 0)) for row in eligible_rows),
        "run_status_counts": {
            key: run_status_counts_total[key] for key in sorted(run_status_counts_total.keys())
        },
        "traffic_profile_count_total": int(traffic_profile_count_total),
        "traffic_profile_count_avg": float(traffic_profile_count_total / float(len(eligible_rows))),
        "max_traffic_profile_count": int(highest_profile_row.get("traffic_profile_count", 0)),
        "highest_traffic_profile_batch_id": str(highest_profile_row.get("batch_id", "")),
        "traffic_profile_unique_count": len(traffic_profile_ids),
        "traffic_profile_ids": sorted(traffic_profile_ids),
        "traffic_profile_source_count_total": int(traffic_profile_source_count_total),
        "traffic_profile_source_count_avg": float(
            traffic_profile_source_count_total / float(len(eligible_rows))
        ),
        "max_traffic_profile_source_count": int(highest_profile_source_row.get("traffic_profile_source_count", 0)),
        "highest_traffic_profile_source_batch_id": str(highest_profile_source_row.get("batch_id", "")),
        "traffic_profile_source_unique_count": len(traffic_profile_source_ids),
        "traffic_profile_source_ids": sorted(traffic_profile_source_ids),
        "traffic_actor_pattern_count_total": int(traffic_actor_pattern_count_total),
        "traffic_actor_pattern_count_avg": float(traffic_actor_pattern_count_total / float(len(eligible_rows))),
        "max_traffic_actor_pattern_count": int(highest_actor_pattern_row.get("traffic_actor_pattern_count", 0)),
        "highest_traffic_actor_pattern_batch_id": str(highest_actor_pattern_row.get("batch_id", "")),
        "traffic_actor_pattern_unique_count": len(traffic_actor_pattern_ids),
        "traffic_actor_pattern_ids": sorted(traffic_actor_pattern_ids),
        "traffic_lane_profile_signature_count_total": int(traffic_lane_profile_signature_count_total),
        "traffic_lane_profile_signature_count_avg": float(
            traffic_lane_profile_signature_count_total / float(len(eligible_rows))
        ),
        "max_traffic_lane_profile_signature_count": int(
            highest_lane_profile_signature_row.get("traffic_lane_profile_signature_count", 0)
        ),
        "highest_traffic_lane_profile_signature_batch_id": str(
            highest_lane_profile_signature_row.get("batch_id", "")
        ),
        "traffic_lane_profile_signature_unique_count": len(traffic_lane_profile_signatures),
        "traffic_lane_profile_signatures": sorted(traffic_lane_profile_signatures),
        "traffic_npc_count_sample_count_total": int(traffic_npc_count_sample_count_total),
        "traffic_npc_count_avg_avg": float(traffic_npc_count_avg_total / float(len(eligible_rows))),
        "traffic_npc_count_avg_max": float(highest_npc_avg_row.get("traffic_npc_count_avg", 0.0)),
        "highest_traffic_npc_avg_batch_id": str(highest_npc_avg_row.get("batch_id", "")),
        "traffic_npc_count_max_max": int(highest_npc_max_row.get("traffic_npc_count_max", 0)),
        "highest_traffic_npc_max_batch_id": str(highest_npc_max_row.get("batch_id", "")),
        "traffic_npc_initial_gap_m_sample_count_total": int(traffic_npc_initial_gap_m_sample_count_total),
        "traffic_npc_initial_gap_m_avg_avg": (
            float(traffic_npc_initial_gap_m_weighted_sum / float(traffic_npc_initial_gap_m_sample_count_total))
            if traffic_npc_initial_gap_m_sample_count_total > 0
            else 0.0
        ),
        "traffic_npc_initial_gap_m_avg_max": float(
            highest_npc_initial_gap_avg_row.get("traffic_npc_initial_gap_m_avg", 0.0)
        ),
        "highest_traffic_npc_initial_gap_m_avg_batch_id": str(
            highest_npc_initial_gap_avg_row.get("batch_id", "")
        ),
        "traffic_npc_gap_step_m_sample_count_total": int(traffic_npc_gap_step_m_sample_count_total),
        "traffic_npc_gap_step_m_avg_avg": (
            float(traffic_npc_gap_step_m_weighted_sum / float(traffic_npc_gap_step_m_sample_count_total))
            if traffic_npc_gap_step_m_sample_count_total > 0
            else 0.0
        ),
        "traffic_npc_gap_step_m_avg_max": float(highest_npc_gap_step_avg_row.get("traffic_npc_gap_step_m_avg", 0.0)),
        "highest_traffic_npc_gap_step_m_avg_batch_id": str(highest_npc_gap_step_avg_row.get("batch_id", "")),
        "traffic_npc_speed_scale_sample_count_total": int(traffic_npc_speed_scale_sample_count_total),
        "traffic_npc_speed_scale_avg_avg": (
            float(traffic_npc_speed_scale_weighted_sum / float(traffic_npc_speed_scale_sample_count_total))
            if traffic_npc_speed_scale_sample_count_total > 0
            else 0.0
        ),
        "traffic_npc_speed_scale_avg_max": float(
            highest_npc_speed_scale_avg_row.get("traffic_npc_speed_scale_avg", 0.0)
        ),
        "highest_traffic_npc_speed_scale_avg_batch_id": str(highest_npc_speed_scale_avg_row.get("batch_id", "")),
        "traffic_npc_speed_jitter_mps_sample_count_total": int(traffic_npc_speed_jitter_mps_sample_count_total),
        "traffic_npc_speed_jitter_mps_avg_avg": (
            float(
                traffic_npc_speed_jitter_mps_weighted_sum
                / float(traffic_npc_speed_jitter_mps_sample_count_total)
            )
            if traffic_npc_speed_jitter_mps_sample_count_total > 0
            else 0.0
        ),
        "traffic_npc_speed_jitter_mps_avg_max": float(
            highest_npc_speed_jitter_avg_row.get("traffic_npc_speed_jitter_mps_avg", 0.0)
        ),
        "highest_traffic_npc_speed_jitter_mps_avg_batch_id": str(
            highest_npc_speed_jitter_avg_row.get("batch_id", "")
        ),
        "traffic_lane_index_unique_count_total": int(traffic_lane_index_unique_count_total),
        "traffic_lane_index_unique_count_avg": float(
            traffic_lane_index_unique_count_total / float(len(eligible_rows))
        ),
        "max_traffic_lane_index_unique_count": int(highest_lane_unique_row.get("traffic_lane_index_unique_count", 0)),
        "highest_traffic_lane_index_unique_batch_id": str(highest_lane_unique_row.get("batch_id", "")),
        "traffic_lane_indices_unique_count": len(traffic_lane_indices),
        "traffic_lane_indices": sorted(traffic_lane_indices),
        "dataset_manifest_counts_rows_total": sum(
            int(row.get("dataset_manifest_counts_rows", 0)) for row in eligible_rows
        ),
        "dataset_manifest_run_summary_count_total": sum(
            int(row.get("dataset_manifest_run_summary_count", 0)) for row in eligible_rows
        ),
        "dataset_manifest_release_summary_count_total": sum(
            int(row.get("dataset_manifest_release_summary_count", 0)) for row in eligible_rows
        ),
        "dataset_manifest_versions": sorted(dataset_manifest_versions),
    }


def main() -> int:
    args = parse_args()
    latest_limit = parse_positive_int(str(args.latest_limit), default=10, field="latest-limit")
    hold_reason_limit = parse_positive_int(str(args.hold_reason_limit), default=20, field="hold-reason-limit")
    total_started_at = time.perf_counter()
    timing_ms = _empty_timing_ms()
    root = resolve_repo_root(__file__)
    artifacts_root = Path(args.artifacts_root).resolve()
    summary_files_root = (
        Path(args.summary_files_root).resolve()
        if str(args.summary_files_root).strip()
        else artifacts_root
    )
    pipeline_manifests_root = (
        Path(args.pipeline_manifests_root).resolve()
        if str(args.pipeline_manifests_root).strip()
        else artifacts_root
    )
    summary_files_subpath = str(args.summary_files_subpath).strip()
    pipeline_manifests_subpath = str(args.pipeline_manifests_subpath).strip()
    summary_scan_roots = resolve_scan_roots(summary_files_root, summary_files_subpath)
    pipeline_manifest_scan_roots = resolve_scan_roots(
        pipeline_manifests_root,
        pipeline_manifests_subpath,
    )
    runtime_evidence_scan_roots: list[Path] = []
    seen_runtime_evidence_roots: set[Path] = set()
    for candidate_root in [artifacts_root, *summary_scan_roots, *pipeline_manifest_scan_roots]:
        resolved = candidate_root.resolve()
        if resolved in seen_runtime_evidence_roots:
            continue
        seen_runtime_evidence_roots.add(resolved)
        runtime_evidence_scan_roots.append(resolved)
    out_text = Path(args.out_text).resolve()
    out_json = Path(args.out_json).resolve() if str(args.out_json).strip() else None
    out_db = Path(args.out_db).resolve()
    out_text.parent.mkdir(parents=True, exist_ok=True)
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    summary_scan_started_at = time.perf_counter()
    summary_files = discover_summary_files(summary_scan_roots, args.release_prefix)
    timing_ms["scan_summary_files"] = _elapsed_ms(summary_scan_started_at)
    if not summary_files:
        timing_ms["total"] = _elapsed_ms(total_started_at)
        warning_text = f"[warn] no summary files found for release_prefix={args.release_prefix}\n"
        out_text.write_text(warning_text, encoding="utf-8")
        if out_json is not None:
            payload = {
                "release_prefix": args.release_prefix,
                "summary_count": 0,
                "warning": f"no summary files found for release_prefix={args.release_prefix}",
                "artifacts_root": str(artifacts_root),
                "summary_files_root": str(summary_files_root),
                "summary_files_subpath": summary_files_subpath,
                "summary_scan_roots": [str(path) for path in summary_scan_roots],
                "pipeline_manifests_root": str(pipeline_manifests_root),
                "pipeline_manifests_subpath": pipeline_manifests_subpath,
                "pipeline_manifest_scan_roots": [str(path) for path in pipeline_manifest_scan_roots],
                "timing_ms": timing_ms,
            }
            out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            print(f"[ok] out_json={out_json}")
        print(warning_text.rstrip(), file=sys.stderr)
        return 0

    load_payloads_started_at = time.perf_counter()
    summary_items = load_summary_payloads(summary_files)
    timing_ms["load_summary_payloads"] = _elapsed_ms(load_payloads_started_at)
    versions = discover_versions(summary_items)
    final_result_counts = summarize_final_results(summary_items)
    root_cause_summary = summarize_root_causes(summary_items)
    scan_manifests_started_at = time.perf_counter()
    pipeline_manifests = discover_pipeline_manifests(pipeline_manifest_scan_roots, args.release_prefix)
    timing_ms["scan_pipeline_manifests"] = _elapsed_ms(scan_manifests_started_at)
    scan_runtime_evidence_started_at = time.perf_counter()
    runtime_evidence_artifacts = discover_runtime_evidence_artifacts(
        runtime_evidence_scan_roots,
        args.release_prefix,
    )
    runtime_lane_execution_artifacts = discover_runtime_lane_execution_artifacts(
        runtime_evidence_scan_roots,
        args.release_prefix,
    )
    runtime_evidence_compare_artifacts = discover_runtime_evidence_compare_artifacts(
        runtime_evidence_scan_roots,
        args.release_prefix,
    )
    runtime_native_evidence_compare_artifacts = filter_runtime_native_evidence_compare_artifacts(
        runtime_evidence_compare_artifacts
    )
    runtime_native_summary_compare_artifacts = discover_runtime_native_summary_compare_artifacts(
        runtime_evidence_scan_roots,
        args.release_prefix,
    )
    timing_ms["scan_runtime_evidence"] = _elapsed_ms(scan_runtime_evidence_started_at)
    runtime_evidence_summary = summarize_runtime_evidence(runtime_evidence_artifacts)
    runtime_lane_execution_summary = summarize_runtime_lane_execution(runtime_lane_execution_artifacts)
    runtime_evidence_compare_summary = summarize_runtime_evidence_compare(runtime_evidence_compare_artifacts)
    runtime_native_evidence_compare_summary = summarize_runtime_evidence_compare(
        runtime_native_evidence_compare_artifacts
    )
    runtime_native_summary_compare_summary = summarize_runtime_native_summary_compare(
        runtime_native_summary_compare_artifacts
    )
    overall_counts: dict[str, int] = {}
    trend_counts: dict[str, int] = {}
    for manifest in pipeline_manifests:
        overall_key = str(manifest.get("overall_result", "")).strip() or "UNKNOWN"
        trend_key = str(manifest.get("trend_result", "")).strip() or "N/A"
        overall_counts[overall_key] = overall_counts.get(overall_key, 0) + 1
        trend_counts[trend_key] = trend_counts.get(trend_key, 0) + 1
    phase2_log_replay_summary = summarize_phase2_log_replay(pipeline_manifests)
    phase2_map_routing_summary = summarize_phase2_map_routing(pipeline_manifests)
    phase2_sensor_fidelity_summary = summarize_phase2_sensor_fidelity(pipeline_manifests)
    runtime_native_smoke_summary = summarize_runtime_native_smoke(pipeline_manifests)
    phase3_vehicle_dynamics_summary = summarize_phase3_vehicle_dynamics(pipeline_manifests)
    phase3_core_sim_summary = summarize_phase3_core_sim(pipeline_manifests)
    phase3_core_sim_matrix_summary = summarize_phase3_core_sim_matrix(pipeline_manifests)
    phase3_lane_risk_summary = summarize_phase3_lane_risk(pipeline_manifests)
    phase3_dataset_traffic_summary = summarize_phase3_dataset_traffic(pipeline_manifests)
    phase4_primary_coverage_summary = summarize_phase4_primary_coverage(pipeline_manifests)
    phase4_secondary_coverage_summary = summarize_phase4_secondary_coverage(pipeline_manifests)
    ingest_runner = root / "30_Projects/P_Data-Lake-and-Explorer/prototype/ingest_scenario_runs.py"
    query_runner = root / "30_Projects/P_Data-Lake-and-Explorer/prototype/query_scenario_runs.py"

    ingest_cmd = [args.python_bin, str(ingest_runner)]
    for summary_file in summary_files:
        ingest_cmd.extend(["--report-summary-file", str(summary_file)])
    ingest_cmd.extend(["--db", str(out_db)])
    ingest_started_at = time.perf_counter()
    run_cmd(ingest_cmd)
    timing_ms["ingest"] = _elapsed_ms(ingest_started_at)

    latest_cmd = [
        args.python_bin,
        str(query_runner),
        "--db",
        str(out_db),
        "release-latest",
        "--limit",
        str(latest_limit),
    ]
    latest_query_started_at = time.perf_counter()
    latest_output = run_cmd(latest_cmd).rstrip()
    timing_ms["query_release_latest"] = _elapsed_ms(latest_query_started_at)

    hold_reason_code_cmd = [
        args.python_bin,
        str(query_runner),
        "--db",
        str(out_db),
        "release-hold-reasons",
        "--mode",
        "code",
        "--limit",
        str(hold_reason_limit),
    ]
    hold_reason_codes_query_started_at = time.perf_counter()
    hold_reason_code_output = run_cmd(hold_reason_code_cmd).rstrip()
    timing_ms["query_hold_reason_codes"] = _elapsed_ms(hold_reason_codes_query_started_at)

    hold_reason_raw_cmd = [
        args.python_bin,
        str(query_runner),
        "--db",
        str(out_db),
        "release-hold-reasons",
        "--mode",
        "raw",
        "--limit",
        str(hold_reason_limit),
    ]
    hold_reasons_raw_query_started_at = time.perf_counter()
    hold_reason_raw_output = run_cmd(hold_reason_raw_cmd).rstrip()
    timing_ms["query_hold_reasons_raw"] = _elapsed_ms(hold_reasons_raw_query_started_at)

    version_a = str(args.version_a).strip()
    version_b = str(args.version_b).strip()
    if bool(version_a) ^ bool(version_b):
        raise ValueError("version-a and version-b must be provided together")

    if not version_a and not version_b:
        if len(versions) >= 2:
            version_a, version_b = versions[0], versions[1]

    diff_output = ""
    reason_code_diff: dict[str, Any] = {}
    if version_a and version_b:
        diff_cmd = [
            args.python_bin,
            str(query_runner),
            "--db",
            str(out_db),
            "release-diff",
            "--release-prefix",
            args.release_prefix,
            "--version-a",
            version_a,
            "--version-b",
            version_b,
        ]
        release_diff_query_started_at = time.perf_counter()
        diff_output_raw = run_cmd_quiet(diff_cmd).rstrip()
        timing_ms["query_release_diff"] = _elapsed_ms(release_diff_query_started_at)
        if _is_release_diff_no_assessment_output(diff_output_raw):
            diff_output = ""
        else:
            diff_output = diff_output_raw
        reason_code_diff = summarize_reason_code_diff(summary_items, version_a, version_b)

    timing_ms["total"] = _elapsed_ms(total_started_at)

    lines: list[str] = []
    lines.append(f"release_prefix={args.release_prefix}")
    lines.append(f"summary_count={len(summary_files)}")
    lines.append(f"summary_files_root={summary_files_root}")
    lines.append(f"summary_files_subpath={summary_files_subpath}")
    lines.append(f"summary_scan_roots={','.join(str(path) for path in summary_scan_roots)}")
    lines.append(f"sds_versions={','.join(versions)}")
    lines.append(
        "final_result_counts="
        + ",".join(f"{key}:{final_result_counts[key]}" for key in sorted(final_result_counts.keys()))
    )
    lines.append(f"pipeline_manifest_count={len(pipeline_manifests)}")
    lines.append(f"pipeline_manifests_root={pipeline_manifests_root}")
    lines.append(f"pipeline_manifests_subpath={pipeline_manifests_subpath}")
    lines.append(
        "pipeline_manifest_scan_roots="
        + ",".join(str(path) for path in pipeline_manifest_scan_roots)
    )
    lines.append(
        "pipeline_overall_counts="
        + ",".join(f"{key}:{overall_counts[key]}" for key in sorted(overall_counts.keys()))
    )
    lines.append(
        "pipeline_trend_counts="
        + ",".join(f"{key}:{trend_counts[key]}" for key in sorted(trend_counts.keys()))
    )
    runtime_evidence_artifact_count = int(runtime_evidence_summary.get("artifact_count", 0) or 0)
    lines.append(f"runtime_evidence_artifact_count={runtime_evidence_artifact_count}")
    if runtime_evidence_artifact_count > 0:
        runtime_evidence_runtime_counts = runtime_evidence_summary.get("runtime_counts", {})
        runtime_evidence_status_counts = runtime_evidence_summary.get("status_counts", {})
        runtime_counts_text = (
            ",".join(f"{key}:{runtime_evidence_runtime_counts[key]}" for key in sorted(runtime_evidence_runtime_counts))
            if isinstance(runtime_evidence_runtime_counts, dict) and runtime_evidence_runtime_counts
            else "n/a"
        )
        status_counts_text = (
            ",".join(f"{key}:{runtime_evidence_status_counts[key]}" for key in sorted(runtime_evidence_status_counts))
            if isinstance(runtime_evidence_status_counts, dict) and runtime_evidence_status_counts
            else "n/a"
        )
        lines.append(
            "runtime_evidence="
            f"records:{int(runtime_evidence_summary.get('record_count', 0) or 0)},"
            f"status:{status_counts_text},"
            f"runtimes:{runtime_counts_text},"
            f"availability:true={int(runtime_evidence_summary.get('availability_true_count', 0) or 0)},"
            f"false={int(runtime_evidence_summary.get('availability_false_count', 0) or 0)},"
            f"unknown={int(runtime_evidence_summary.get('availability_unknown_count', 0) or 0)},"
            f"probe_checked={int(runtime_evidence_summary.get('probe_checked_count', 0) or 0)},"
            f"probe_executed={int(runtime_evidence_summary.get('probe_executed_count', 0) or 0)},"
            f"runtime_bin_missing={int(runtime_evidence_summary.get('runtime_bin_missing_count', 0) or 0)},"
            f"provenance_complete={int(runtime_evidence_summary.get('provenance_complete_count', 0) or 0)},"
            f"provenance_missing={int(runtime_evidence_summary.get('provenance_missing_count', 0) or 0)}"
        )
        runtime_evidence_probe_args_source_counts = runtime_evidence_summary.get("probe_args_source_counts", {})
        runtime_evidence_probe_args_requested_source_counts = runtime_evidence_summary.get(
            "probe_args_requested_source_counts",
            {},
        )
        runtime_evidence_probe_arg_value_counts = runtime_evidence_summary.get("probe_arg_value_counts", {})
        runtime_evidence_probe_arg_requested_value_counts = runtime_evidence_summary.get(
            "probe_arg_requested_value_counts",
            {},
        )
        runtime_evidence_scenario_contract_status_counts = runtime_evidence_summary.get(
            "scenario_contract_status_counts",
            {},
        )
        runtime_evidence_scene_result_status_counts = runtime_evidence_summary.get(
            "scene_result_status_counts",
            {},
        )
        runtime_evidence_interop_contract_status_counts = runtime_evidence_summary.get(
            "interop_contract_status_counts",
            {},
        )
        runtime_evidence_interop_export_status_counts = runtime_evidence_summary.get(
            "interop_export_status_counts",
            {},
        )
        runtime_evidence_interop_import_status_counts = runtime_evidence_summary.get(
            "interop_import_status_counts",
            {},
        )
        runtime_evidence_probe_args_source_text = (
            ",".join(
                f"{key}:{runtime_evidence_probe_args_source_counts[key]}"
                for key in sorted(runtime_evidence_probe_args_source_counts)
            )
            if isinstance(runtime_evidence_probe_args_source_counts, dict) and runtime_evidence_probe_args_source_counts
            else "n/a"
        )
        runtime_evidence_probe_args_requested_source_text = (
            ",".join(
                f"{key}:{runtime_evidence_probe_args_requested_source_counts[key]}"
                for key in sorted(runtime_evidence_probe_args_requested_source_counts)
            )
            if (
                isinstance(runtime_evidence_probe_args_requested_source_counts, dict)
                and runtime_evidence_probe_args_requested_source_counts
            )
            else "n/a"
        )
        runtime_evidence_probe_arg_values_text = (
            ",".join(
                f"{key}:{runtime_evidence_probe_arg_value_counts[key]}"
                for key in sorted(runtime_evidence_probe_arg_value_counts)
            )
            if isinstance(runtime_evidence_probe_arg_value_counts, dict) and runtime_evidence_probe_arg_value_counts
            else "n/a"
        )
        runtime_evidence_probe_arg_requested_values_text = (
            ",".join(
                f"{key}:{runtime_evidence_probe_arg_requested_value_counts[key]}"
                for key in sorted(runtime_evidence_probe_arg_requested_value_counts)
            )
            if (
                isinstance(runtime_evidence_probe_arg_requested_value_counts, dict)
                and runtime_evidence_probe_arg_requested_value_counts
            )
            else "n/a"
        )
        runtime_evidence_scenario_contract_status_text = (
            ",".join(
                f"{key}:{runtime_evidence_scenario_contract_status_counts[key]}"
                for key in sorted(runtime_evidence_scenario_contract_status_counts)
            )
            if (
                isinstance(runtime_evidence_scenario_contract_status_counts, dict)
                and runtime_evidence_scenario_contract_status_counts
            )
            else "n/a"
        )
        runtime_evidence_scene_result_status_text = (
            ",".join(
                f"{key}:{runtime_evidence_scene_result_status_counts[key]}"
                for key in sorted(runtime_evidence_scene_result_status_counts)
            )
            if (
                isinstance(runtime_evidence_scene_result_status_counts, dict)
                and runtime_evidence_scene_result_status_counts
            )
            else "n/a"
        )
        runtime_evidence_interop_contract_status_text = (
            ",".join(
                f"{key}:{runtime_evidence_interop_contract_status_counts[key]}"
                for key in sorted(runtime_evidence_interop_contract_status_counts)
            )
            if (
                isinstance(runtime_evidence_interop_contract_status_counts, dict)
                and runtime_evidence_interop_contract_status_counts
            )
            else "n/a"
        )
        runtime_evidence_interop_export_status_text = (
            ",".join(
                f"{key}:{runtime_evidence_interop_export_status_counts[key]}"
                for key in sorted(runtime_evidence_interop_export_status_counts)
            )
            if (
                isinstance(runtime_evidence_interop_export_status_counts, dict)
                and runtime_evidence_interop_export_status_counts
            )
            else "n/a"
        )
        runtime_evidence_interop_import_status_text = (
            ",".join(
                f"{key}:{runtime_evidence_interop_import_status_counts[key]}"
                for key in sorted(runtime_evidence_interop_import_status_counts)
            )
            if (
                isinstance(runtime_evidence_interop_import_status_counts, dict)
                and runtime_evidence_interop_import_status_counts
            )
            else "n/a"
        )
        runtime_evidence_interop_import_manifest_mode_counts = runtime_evidence_summary.get(
            "interop_import_manifest_consistency_mode_counts",
            {},
        )
        runtime_evidence_interop_import_manifest_mode_counts_text = (
            ",".join(
                f"{key}:{runtime_evidence_interop_import_manifest_mode_counts[key]}"
                for key in sorted(runtime_evidence_interop_import_manifest_mode_counts)
            )
            if (
                isinstance(runtime_evidence_interop_import_manifest_mode_counts, dict)
                and runtime_evidence_interop_import_manifest_mode_counts
            )
            else "n/a"
        )
        runtime_evidence_interop_import_export_mode_counts = runtime_evidence_summary.get(
            "interop_import_export_consistency_mode_counts",
            {},
        )
        runtime_evidence_interop_import_export_mode_counts_text = (
            ",".join(
                f"{key}:{runtime_evidence_interop_import_export_mode_counts[key]}"
                for key in sorted(runtime_evidence_interop_import_export_mode_counts)
            )
            if (
                isinstance(runtime_evidence_interop_import_export_mode_counts, dict)
                and runtime_evidence_interop_import_export_mode_counts
            )
            else "n/a"
        )
        runtime_evidence_interop_import_require_manifest_true_count = int(
            runtime_evidence_summary.get("interop_import_require_manifest_consistency_input_true_count", 0) or 0
        )
        runtime_evidence_interop_import_require_export_true_count = int(
            runtime_evidence_summary.get("interop_import_require_export_consistency_input_true_count", 0) or 0
        )
        lines.append(
            "runtime_evidence_probe_args="
            f"effective={int(runtime_evidence_summary.get('probe_args_effective_count', 0) or 0)},"
            f"requested={int(runtime_evidence_summary.get('probe_args_requested_count', 0) or 0)},"
            f"sources:{runtime_evidence_probe_args_source_text},"
            f"requested_sources:{runtime_evidence_probe_args_requested_source_text},"
            f"arg_values:{runtime_evidence_probe_arg_values_text},"
            f"requested_arg_values:{runtime_evidence_probe_arg_requested_values_text},"
            f"policy:enable={int(runtime_evidence_summary.get('probe_policy_enable_true_count', 0) or 0)},"
            f"execute={int(runtime_evidence_summary.get('probe_policy_execute_true_count', 0) or 0)},"
            "require_availability="
            f"{int(runtime_evidence_summary.get('probe_policy_require_availability_true_count', 0) or 0)},"
            f"flag_input_present={int(runtime_evidence_summary.get('probe_policy_flag_input_present_count', 0) or 0)},"
            "args_shlex_input_present="
            f"{int(runtime_evidence_summary.get('probe_policy_args_shlex_input_present_count', 0) or 0)}"
        )
        lines.append(
            "runtime_evidence_scenario_contract="
            f"checked={int(runtime_evidence_summary.get('scenario_contract_checked_count', 0) or 0)},"
            "ready:true="
            f"{int(runtime_evidence_summary.get('scenario_runtime_ready_true_count', 0) or 0)},"
            "false="
            f"{int(runtime_evidence_summary.get('scenario_runtime_ready_false_count', 0) or 0)},"
            "unknown="
            f"{int(runtime_evidence_summary.get('scenario_runtime_ready_unknown_count', 0) or 0)},"
            f"statuses:{runtime_evidence_scenario_contract_status_text},"
            f"actor_total={int(runtime_evidence_summary.get('scenario_actor_count_total', 0) or 0)},"
            "sensor_stream_total="
            f"{int(runtime_evidence_summary.get('scenario_sensor_stream_count_total', 0) or 0)},"
            f"step_total={int(runtime_evidence_summary.get('scenario_executed_step_count_total', 0) or 0)},"
            "sim_duration_sec_total="
            f"{float(runtime_evidence_summary.get('scenario_sim_duration_sec_total', 0.0) or 0.0):.3f}"
        )
        lines.append(
            "runtime_evidence_scene_result="
            f"checked={int(runtime_evidence_summary.get('scene_result_checked_count', 0) or 0)},"
            "ready:true="
            f"{int(runtime_evidence_summary.get('scene_result_runtime_ready_true_count', 0) or 0)},"
            "false="
            f"{int(runtime_evidence_summary.get('scene_result_runtime_ready_false_count', 0) or 0)},"
            "unknown="
            f"{int(runtime_evidence_summary.get('scene_result_runtime_ready_unknown_count', 0) or 0)},"
            f"statuses:{runtime_evidence_scene_result_status_text},"
            f"actor_total={int(runtime_evidence_summary.get('scene_result_actor_count_total', 0) or 0)},"
            "sensor_stream_total="
            f"{int(runtime_evidence_summary.get('scene_result_sensor_stream_count_total', 0) or 0)},"
            f"step_total={int(runtime_evidence_summary.get('scene_result_executed_step_count_total', 0) or 0)},"
            "sim_duration_sec_total="
            f"{float(runtime_evidence_summary.get('scene_result_sim_duration_sec_total', 0.0) or 0.0):.3f},"
            "coverage_ratio_avg="
            f"{float(runtime_evidence_summary.get('scene_result_coverage_ratio_avg', 0.0) or 0.0):.3f},"
            "coverage_ratio_samples="
            f"{int(runtime_evidence_summary.get('scene_result_coverage_ratio_sample_count', 0) or 0)},"
            "ego_travel_distance_m_total="
            f"{float(runtime_evidence_summary.get('scene_result_ego_travel_distance_m_total', 0.0) or 0.0):.3f}"
        )
        lines.append(
            "runtime_evidence_interop_contract="
            f"checked={int(runtime_evidence_summary.get('interop_contract_checked_count', 0) or 0)},"
            "ready:true="
            f"{int(runtime_evidence_summary.get('interop_runtime_ready_true_count', 0) or 0)},"
            "false="
            f"{int(runtime_evidence_summary.get('interop_runtime_ready_false_count', 0) or 0)},"
            "unknown="
            f"{int(runtime_evidence_summary.get('interop_runtime_ready_unknown_count', 0) or 0)},"
            f"statuses:{runtime_evidence_interop_contract_status_text},"
            f"imported_actor_total={int(runtime_evidence_summary.get('interop_imported_actor_count_total', 0) or 0)},"
            f"xosc_entity_total={int(runtime_evidence_summary.get('interop_xosc_entity_count_total', 0) or 0)},"
            f"xodr_road_total={int(runtime_evidence_summary.get('interop_xodr_road_count_total', 0) or 0)},"
            f"step_total={int(runtime_evidence_summary.get('interop_executed_step_count_total', 0) or 0)},"
            "sim_duration_sec_total="
            f"{float(runtime_evidence_summary.get('interop_sim_duration_sec_total', 0.0) or 0.0):.3f}"
        )
        lines.append(
            "runtime_evidence_interop_export="
            f"checked={int(runtime_evidence_summary.get('interop_export_checked_count', 0) or 0)},"
            f"statuses:{runtime_evidence_interop_export_status_text},"
            "actor_manifest_total="
            f"{int(runtime_evidence_summary.get('interop_export_actor_count_manifest_total', 0) or 0)},"
            "sensor_stream_manifest_total="
            f"{int(runtime_evidence_summary.get('interop_export_sensor_stream_count_manifest_total', 0) or 0)},"
            f"xosc_entity_total={int(runtime_evidence_summary.get('interop_export_xosc_entity_count_total', 0) or 0)},"
            f"xodr_road_total={int(runtime_evidence_summary.get('interop_export_xodr_road_count_total', 0) or 0)},"
            "generated_road_length_m_total="
            f"{float(runtime_evidence_summary.get('interop_export_generated_road_length_m_total', 0.0) or 0.0):.3f}"
        )
        lines.append(
            "runtime_evidence_interop_import="
            f"checked={int(runtime_evidence_summary.get('interop_import_checked_count', 0) or 0)},"
            f"statuses:{runtime_evidence_interop_import_status_text},"
            "manifest_consistent:true="
            f"{int(runtime_evidence_summary.get('interop_import_manifest_consistent_true_count', 0) or 0)},"
            "false="
            f"{int(runtime_evidence_summary.get('interop_import_manifest_consistent_false_count', 0) or 0)},"
            "unknown="
            f"{int(runtime_evidence_summary.get('interop_import_manifest_consistent_unknown_count', 0) or 0)},"
            "actor_manifest_total="
            f"{int(runtime_evidence_summary.get('interop_import_actor_count_manifest_total', 0) or 0)},"
            f"xosc_entity_total={int(runtime_evidence_summary.get('interop_import_xosc_entity_count_total', 0) or 0)},"
            f"xodr_road_total={int(runtime_evidence_summary.get('interop_import_xodr_road_count_total', 0) or 0)},"
            "xodr_total_road_length_m_total="
            f"{float(runtime_evidence_summary.get('interop_import_xodr_total_road_length_m_total', 0.0) or 0.0):.3f}"
        )
        if (
            runtime_evidence_interop_import_manifest_mode_counts_text != "n/a"
            or runtime_evidence_interop_import_export_mode_counts_text != "n/a"
            or runtime_evidence_interop_import_require_manifest_true_count > 0
            or runtime_evidence_interop_import_require_export_true_count > 0
        ):
            lines.append(
                "runtime_evidence_interop_import_modes="
                f"manifest:{runtime_evidence_interop_import_manifest_mode_counts_text},"
                f"export:{runtime_evidence_interop_import_export_mode_counts_text},"
                "require_inputs:"
                f"manifest={runtime_evidence_interop_import_require_manifest_true_count},"
                f"export={runtime_evidence_interop_import_require_export_true_count}"
            )
        runtime_evidence_interop_import_manifest_inconsistent_records_raw = runtime_evidence_summary.get(
            "interop_import_manifest_inconsistent_records",
            [],
        )
        runtime_evidence_interop_import_manifest_inconsistent_records_text = "n/a"
        if isinstance(runtime_evidence_interop_import_manifest_inconsistent_records_raw, list):
            runtime_evidence_interop_import_manifest_inconsistent_records_values: list[str] = []
            for row in runtime_evidence_interop_import_manifest_inconsistent_records_raw[:5]:
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
                runtime_evidence_interop_import_manifest_inconsistent_records_values.append(
                    f"{profile_id}:{release_id}:{runtime_name}:manifest={actor_count_manifest}:imported={xosc_entity_count}"
                )
            if runtime_evidence_interop_import_manifest_inconsistent_records_values:
                runtime_evidence_interop_import_manifest_inconsistent_records_text = "; ".join(
                    runtime_evidence_interop_import_manifest_inconsistent_records_values
                )
                remaining = max(
                    0,
                    len(runtime_evidence_interop_import_manifest_inconsistent_records_raw)
                    - len(runtime_evidence_interop_import_manifest_inconsistent_records_values),
                )
                if remaining > 0:
                    runtime_evidence_interop_import_manifest_inconsistent_records_text += f"; ...(+{remaining} more)"
        if runtime_evidence_interop_import_manifest_inconsistent_records_text != "n/a":
            lines.append(
                "runtime_evidence_interop_import_inconsistent_records="
                f"{runtime_evidence_interop_import_manifest_inconsistent_records_text}"
            )
    else:
        lines.append("runtime_evidence=n/a")
    runtime_lane_execution_artifact_count = int(runtime_lane_execution_summary.get("artifact_count", 0) or 0)
    lines.append(f"runtime_lane_execution_artifact_count={runtime_lane_execution_artifact_count}")
    if runtime_lane_execution_artifact_count > 0:
        runtime_lane_execution_runtime_counts = runtime_lane_execution_summary.get("runtime_counts", {})
        runtime_lane_execution_result_counts = runtime_lane_execution_summary.get("result_counts", {})
        runtime_lane_execution_lane_counts = runtime_lane_execution_summary.get("lane_counts", {})
        runtime_lane_execution_lane_row_counts = runtime_lane_execution_summary.get("lane_row_counts", {})
        runtime_lane_execution_runner_platform_counts = runtime_lane_execution_summary.get("runner_platform_counts", {})
        runtime_lane_execution_sim_runtime_input_counts = runtime_lane_execution_summary.get(
            "sim_runtime_input_counts",
            {},
        )
        runtime_lane_execution_dry_run_counts = runtime_lane_execution_summary.get("dry_run_counts", {})
        runtime_lane_execution_continue_on_runtime_failure_counts = runtime_lane_execution_summary.get(
            "continue_on_runtime_failure_counts",
            {},
        )
        runtime_lane_execution_asset_profile_counts = runtime_lane_execution_summary.get(
            "runtime_asset_profile_counts", {}
        )
        runtime_lane_execution_archive_sha256_mode_counts = runtime_lane_execution_summary.get(
            "runtime_asset_archive_sha256_mode_counts", {}
        )
        runtime_lane_execution_exec_lane_warn_min_rows_counts = runtime_lane_execution_summary.get(
            "runtime_exec_lane_warn_min_rows_counts", {}
        )
        runtime_lane_execution_exec_lane_hold_min_rows_counts = runtime_lane_execution_summary.get(
            "runtime_exec_lane_hold_min_rows_counts", {}
        )
        runtime_lane_execution_runtime_compare_warn_min_counts = runtime_lane_execution_summary.get(
            "runtime_compare_warn_min_artifacts_with_diffs_counts",
            {},
        )
        runtime_lane_execution_runtime_compare_hold_min_counts = runtime_lane_execution_summary.get(
            "runtime_compare_hold_min_artifacts_with_diffs_counts",
            {},
        )
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts = runtime_lane_execution_summary.get(
            "phase2_sensor_fidelity_score_avg_warn_min_counts",
            {},
        )
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts = runtime_lane_execution_summary.get(
            "phase2_sensor_fidelity_score_avg_hold_min_counts",
            {},
        )
        runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts = runtime_lane_execution_summary.get(
            "phase2_sensor_frame_count_avg_warn_min_counts",
            {},
        )
        runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts = runtime_lane_execution_summary.get(
            "phase2_sensor_frame_count_avg_hold_min_counts",
            {},
        )
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts = (
            runtime_lane_execution_summary.get(
                "phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts",
                {},
            )
        )
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts = (
            runtime_lane_execution_summary.get(
                "phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts",
                {},
            )
        )
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts = (
            runtime_lane_execution_summary.get(
                "phase2_sensor_lidar_point_count_avg_warn_min_counts",
                {},
            )
        )
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts = (
            runtime_lane_execution_summary.get(
                "phase2_sensor_lidar_point_count_avg_hold_min_counts",
                {},
            )
        )
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts = (
            runtime_lane_execution_summary.get(
                "phase2_sensor_radar_false_positive_rate_avg_warn_max_counts",
                {},
            )
        )
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts = (
            runtime_lane_execution_summary.get(
                "phase2_sensor_radar_false_positive_rate_avg_hold_max_counts",
                {},
            )
        )
        runtime_lane_execution_evidence_path_present_count = int(
            runtime_lane_execution_summary.get("runtime_evidence_path_present_count", 0) or 0
        )
        runtime_lane_execution_evidence_exists_true_count = int(
            runtime_lane_execution_summary.get("runtime_evidence_exists_true_count", 0) or 0
        )
        runtime_lane_execution_evidence_exists_false_count = int(
            runtime_lane_execution_summary.get("runtime_evidence_exists_false_count", 0) or 0
        )
        runtime_lane_execution_evidence_exists_unknown_count = int(
            runtime_lane_execution_summary.get("runtime_evidence_exists_unknown_count", 0) or 0
        )
        runtime_lane_execution_runtime_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_runtime_counts[key]}"
                for key in sorted(runtime_lane_execution_runtime_counts)
            )
            if isinstance(runtime_lane_execution_runtime_counts, dict) and runtime_lane_execution_runtime_counts
            else "n/a"
        )
        runtime_lane_execution_result_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_result_counts[key]}"
                for key in sorted(runtime_lane_execution_result_counts)
            )
            if isinstance(runtime_lane_execution_result_counts, dict) and runtime_lane_execution_result_counts
            else "n/a"
        )
        runtime_lane_execution_failure_reason_counts = runtime_lane_execution_summary.get(
            "runtime_failure_reason_counts",
            {},
        )
        runtime_lane_execution_failure_reason_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_failure_reason_counts[key]}"
                for key in sorted(runtime_lane_execution_failure_reason_counts)
            )
            if (
                isinstance(runtime_lane_execution_failure_reason_counts, dict)
                and runtime_lane_execution_failure_reason_counts
            )
            else "n/a"
        )
        runtime_lane_execution_lane_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_lane_counts[key]}"
                for key in sorted(runtime_lane_execution_lane_counts)
            )
            if isinstance(runtime_lane_execution_lane_counts, dict) and runtime_lane_execution_lane_counts
            else "n/a"
        )
        runtime_lane_execution_lane_row_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_lane_row_counts[key]}"
                for key in sorted(runtime_lane_execution_lane_row_counts)
            )
            if (
                isinstance(runtime_lane_execution_lane_row_counts, dict)
                and runtime_lane_execution_lane_row_counts
            )
            else "n/a"
        )
        runtime_lane_execution_runner_platform_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_runner_platform_counts[key]}"
                for key in sorted(runtime_lane_execution_runner_platform_counts)
            )
            if (
                isinstance(runtime_lane_execution_runner_platform_counts, dict)
                and runtime_lane_execution_runner_platform_counts
            )
            else "n/a"
        )
        runtime_lane_execution_sim_runtime_input_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_sim_runtime_input_counts[key]}"
                for key in sorted(runtime_lane_execution_sim_runtime_input_counts)
            )
            if (
                isinstance(runtime_lane_execution_sim_runtime_input_counts, dict)
                and runtime_lane_execution_sim_runtime_input_counts
            )
            else "n/a"
        )
        runtime_lane_execution_dry_run_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_dry_run_counts[key]}"
                for key in sorted(runtime_lane_execution_dry_run_counts)
            )
            if (
                isinstance(runtime_lane_execution_dry_run_counts, dict)
                and runtime_lane_execution_dry_run_counts
            )
            else "n/a"
        )
        runtime_lane_execution_continue_on_runtime_failure_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_continue_on_runtime_failure_counts[key]}"
                for key in sorted(runtime_lane_execution_continue_on_runtime_failure_counts)
            )
            if (
                isinstance(runtime_lane_execution_continue_on_runtime_failure_counts, dict)
                and runtime_lane_execution_continue_on_runtime_failure_counts
            )
            else "n/a"
        )
        runtime_lane_execution_asset_profile_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_asset_profile_counts[key]}"
                for key in sorted(runtime_lane_execution_asset_profile_counts)
            )
            if (
                isinstance(runtime_lane_execution_asset_profile_counts, dict)
                and runtime_lane_execution_asset_profile_counts
            )
            else "n/a"
        )
        runtime_lane_execution_archive_sha256_mode_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_archive_sha256_mode_counts[key]}"
                for key in sorted(runtime_lane_execution_archive_sha256_mode_counts)
            )
            if (
                isinstance(runtime_lane_execution_archive_sha256_mode_counts, dict)
                and runtime_lane_execution_archive_sha256_mode_counts
            )
            else "n/a"
        )
        runtime_lane_execution_exec_lane_warn_min_rows_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_exec_lane_warn_min_rows_counts[key]}"
                for key in sorted(runtime_lane_execution_exec_lane_warn_min_rows_counts, key=lambda raw_key: int(raw_key))
            )
            if (
                isinstance(runtime_lane_execution_exec_lane_warn_min_rows_counts, dict)
                and runtime_lane_execution_exec_lane_warn_min_rows_counts
            )
            else "n/a"
        )
        runtime_lane_execution_exec_lane_hold_min_rows_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_exec_lane_hold_min_rows_counts[key]}"
                for key in sorted(runtime_lane_execution_exec_lane_hold_min_rows_counts, key=lambda raw_key: int(raw_key))
            )
            if (
                isinstance(runtime_lane_execution_exec_lane_hold_min_rows_counts, dict)
                and runtime_lane_execution_exec_lane_hold_min_rows_counts
            )
            else "n/a"
        )
        runtime_lane_execution_runtime_compare_warn_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_runtime_compare_warn_min_counts[key]}"
                for key in sorted(runtime_lane_execution_runtime_compare_warn_min_counts, key=lambda raw_key: int(raw_key))
            )
            if (
                isinstance(runtime_lane_execution_runtime_compare_warn_min_counts, dict)
                and runtime_lane_execution_runtime_compare_warn_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_runtime_compare_hold_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_runtime_compare_hold_min_counts[key]}"
                for key in sorted(runtime_lane_execution_runtime_compare_hold_min_counts, key=lambda raw_key: int(raw_key))
            )
            if (
                isinstance(runtime_lane_execution_runtime_compare_hold_min_counts, dict)
                and runtime_lane_execution_runtime_compare_hold_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts, dict)
                and runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts, dict)
                and runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts, dict)
                and runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts, dict)
                and runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts, dict)
                and runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts, dict)
                and runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts, dict)
                and runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts, dict)
                and runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts, dict)
                and runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts
            )
            else "n/a"
        )
        runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts[key]}"
                for key in sorted(
                    runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts,
                    key=lambda raw_key: float(raw_key),
                )
            )
            if (
                isinstance(runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts, dict)
                and runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts
            )
            else "n/a"
        )
        runtime_lane_execution_evidence_missing_runtime_counts = runtime_lane_execution_summary.get(
            "runtime_evidence_missing_runtime_counts", {}
        )
        runtime_lane_execution_evidence_missing_runtime_counts_text = (
            ",".join(
                f"{key}:{runtime_lane_execution_evidence_missing_runtime_counts[key]}"
                for key in sorted(runtime_lane_execution_evidence_missing_runtime_counts)
            )
            if (
                isinstance(runtime_lane_execution_evidence_missing_runtime_counts, dict)
                and runtime_lane_execution_evidence_missing_runtime_counts
            )
            else "n/a"
        )
        lines.append(
            "runtime_lane_execution="
            f"rows:{int(runtime_lane_execution_summary.get('runtime_row_count', 0) or 0)},"
            f"results:{runtime_lane_execution_result_counts_text},"
            f"failure_reasons:{runtime_lane_execution_failure_reason_counts_text},"
            f"runtimes:{runtime_lane_execution_runtime_counts_text},"
            f"lanes:{runtime_lane_execution_lane_counts_text},"
            f"asset_profiles:{runtime_lane_execution_asset_profile_counts_text},"
            f"archive_sha256_modes:{runtime_lane_execution_archive_sha256_mode_counts_text},"
            f"evidence_paths:present={runtime_lane_execution_evidence_path_present_count},"
            f"exists={runtime_lane_execution_evidence_exists_true_count},"
            f"missing={runtime_lane_execution_evidence_exists_false_count},"
            f"unknown={runtime_lane_execution_evidence_exists_unknown_count},"
            f"evidence_missing_runtimes={runtime_lane_execution_evidence_missing_runtime_counts_text},"
            f"lane_rows:{runtime_lane_execution_lane_row_counts_text},"
            f"runner_platforms:{runtime_lane_execution_runner_platform_counts_text},"
            f"sim_runtime_inputs:{runtime_lane_execution_sim_runtime_input_counts_text},"
            f"dry_runs:{runtime_lane_execution_dry_run_counts_text},"
            f"continue_on_runtime_failure:{runtime_lane_execution_continue_on_runtime_failure_counts_text},"
            "exec_lane_warn_min_rows:"
            f"{runtime_lane_execution_exec_lane_warn_min_rows_counts_text},"
            "exec_lane_hold_min_rows:"
            f"{runtime_lane_execution_exec_lane_hold_min_rows_counts_text},"
            "runtime_compare_warn_min_artifacts_with_diffs:"
            f"{runtime_lane_execution_runtime_compare_warn_min_counts_text},"
            "runtime_compare_hold_min_artifacts_with_diffs:"
            f"{runtime_lane_execution_runtime_compare_hold_min_counts_text},"
            "phase2_sensor_fidelity_score_avg_warn_min:"
            f"{runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_counts_text},"
            "phase2_sensor_fidelity_score_avg_hold_min:"
            f"{runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_counts_text},"
            "phase2_sensor_frame_count_avg_warn_min:"
            f"{runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_counts_text},"
            "phase2_sensor_frame_count_avg_hold_min:"
            f"{runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_counts_text},"
            "phase2_sensor_camera_noise_stddev_px_avg_warn_max:"
            f"{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_counts_text},"
            "phase2_sensor_camera_noise_stddev_px_avg_hold_max:"
            f"{runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_counts_text},"
            "phase2_sensor_lidar_point_count_avg_warn_min:"
            f"{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_counts_text},"
            "phase2_sensor_lidar_point_count_avg_hold_min:"
            f"{runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_counts_text},"
            "phase2_sensor_radar_false_positive_rate_avg_warn_max:"
            f"{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_counts_text},"
            "phase2_sensor_radar_false_positive_rate_avg_hold_max:"
            f"{runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_counts_text}"
        )
    else:
        lines.append("runtime_lane_execution=n/a")
    runtime_evidence_compare_artifact_count = int(runtime_evidence_compare_summary.get("artifact_count", 0) or 0)
    lines.append(f"runtime_evidence_compare_artifact_count={runtime_evidence_compare_artifact_count}")
    if runtime_evidence_compare_artifact_count > 0:
        runtime_evidence_compare_label_pair_counts = runtime_evidence_compare_summary.get("label_pair_counts", {})
        runtime_evidence_compare_label_pair_counts_text = (
            ",".join(
                f"{key}:{runtime_evidence_compare_label_pair_counts[key]}"
                for key in sorted(runtime_evidence_compare_label_pair_counts)
            )
            if (
                isinstance(runtime_evidence_compare_label_pair_counts, dict)
                and runtime_evidence_compare_label_pair_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_field_counts = runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_field_counts",
            {},
        )
        runtime_evidence_compare_interop_import_profile_diff_field_counts_text = (
            ",".join(
                f"{key}:{runtime_evidence_compare_interop_import_profile_diff_field_counts[key]}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_field_counts)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_field_counts, dict)
                and runtime_evidence_compare_interop_import_profile_diff_field_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_counts = runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_numeric_counts",
            {},
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_counts_text = (
            ",".join(
                f"{key}:{runtime_evidence_compare_interop_import_profile_diff_numeric_counts[key]}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_counts)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_counts, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_label_pair_counts = runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_label_pair_counts",
            {},
        )
        runtime_evidence_compare_interop_import_profile_diff_label_pair_counts_text = (
            ",".join(
                f"{key}:{runtime_evidence_compare_interop_import_profile_diff_label_pair_counts[key]}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_label_pair_counts)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_label_pair_counts, dict)
                and runtime_evidence_compare_interop_import_profile_diff_label_pair_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_profile_counts = runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_profile_counts",
            {},
        )
        runtime_evidence_compare_interop_import_profile_diff_profile_counts_text = (
            ",".join(
                f"{key}:{runtime_evidence_compare_interop_import_profile_diff_profile_counts[key]}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_profile_counts)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_profile_counts, dict)
                and runtime_evidence_compare_interop_import_profile_diff_profile_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals = runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_numeric_delta_totals",
            {},
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_text = (
            ",".join(
                f"{key}:{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals[key]):.6f}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_abs_totals",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_text = (
            ",".join(
                f"{key}:{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals[key]):.6f}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_totals_by_label_pair",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_text = (
            ",".join(
                f"{label_pair}|{numeric_key}:"
                f"{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair[label_pair][numeric_key]):.6f}"
                for label_pair in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair)
                if isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair.get(
                        label_pair
                    ),
                    dict,
                )
                for numeric_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair[label_pair]
                )
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_text = (
            ",".join(
                f"{label_pair}|{numeric_key}:"
                f"{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair[label_pair][numeric_key]):.6f}"
                for label_pair in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair)
                if isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair.get(
                        label_pair
                    ),
                    dict,
                )
                for numeric_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair[label_pair]
                )
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_totals_by_profile",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile_text = (
            ",".join(
                f"{profile_id}|{numeric_key}:"
                f"{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile[profile_id][numeric_key]):.6f}"
                for profile_id in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile)
                if isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile.get(
                        profile_id
                    ),
                    dict,
                )
                for numeric_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile[profile_id]
                )
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_abs_totals_by_profile",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile_text = (
            ",".join(
                f"{profile_id}|{numeric_key}:"
                f"{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile[profile_id][numeric_key]):.6f}"
                for profile_id in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile)
                if isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile.get(
                        profile_id
                    ),
                    dict,
                )
                for numeric_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile[profile_id]
                )
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile_text = (
            ",".join(
                f"{label_pair_profile_key}|{numeric_key}:"
                f"{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile[label_pair_profile_key][numeric_key]):.6f}"
                for label_pair_profile_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile
                )
                if isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile.get(
                        label_pair_profile_key
                    ),
                    dict,
                )
                for numeric_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile[
                        label_pair_profile_key
                    ]
                )
            )
            if (
                isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile,
                    dict,
                )
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile_text = (
            ",".join(
                f"{label_pair_profile_key}|{numeric_key}:"
                f"{float(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile[label_pair_profile_key][numeric_key]):.6f}"
                for label_pair_profile_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile
                )
                if isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile.get(
                        label_pair_profile_key
                    ),
                    dict,
                )
                for numeric_key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile[
                        label_pair_profile_key
                    ]
                )
            )
            if (
                isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile,
                    dict,
                )
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_positive_counts",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts_text = (
            ",".join(
                f"{key}:{int(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts[key])}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_negative_counts",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts_text = (
            ",".join(
                f"{key}:{int(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts[key])}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_zero_counts",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts_text = (
            ",".join(
                f"{key}:{int(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts[key])}"
                for key in sorted(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts)
            )
            if (
                isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts, dict)
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspot_priority_counts",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text = (
            ",".join(
                f"{key}:{int(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts[key])}"
                for key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts
                )
            )
            if (
                isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts,
                    dict,
                )
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspot_action_counts",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text = (
            ",".join(
                f"{key}:{int(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts[key])}"
                for key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts
                )
            )
            if (
                isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts,
                    dict,
                )
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspot_reason_counts",
                {},
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text = (
            ",".join(
                f"{key}:{int(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts[key])}"
                for key in sorted(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts
                )
            )
            if (
                isinstance(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts,
                    dict,
                )
                and runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts
            )
            else "n/a"
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_raw = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspots",
                [],
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile",
                [],
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_raw = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_key_max_positive_records",
                [],
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_raw = (
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_key_max_negative_records",
                [],
            )
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_record_count = int(
            runtime_evidence_compare_summary.get("interop_import_profile_diff_numeric_delta_record_count", 0) or 0
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count = int(
            runtime_evidence_compare_summary.get(
                "interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count",
                0,
            )
            or 0
        )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_text = "n/a"
        if isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_raw, list):
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_values: list[str] = []
            for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_raw[:5]:
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
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_values.append(
                    f"{numeric_key}:{left_label}_vs_{right_label}:{profile_id}:delta={delta_value:.6f}"
                )
            if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_values:
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_text = (
                    "; ".join(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_values)
                )
                remaining = max(
                    0,
                    len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_raw)
                    - len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_values),
                )
                if remaining > 0:
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_text += (
                        f"; ...(+{remaining} more)"
                    )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_text = "n/a"
        if isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_raw, list):
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_values: list[str] = []
            for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_raw[:5]:
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
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_values.append(
                    f"{numeric_key}:{left_label}_vs_{right_label}:{profile_id}:delta={delta_value:.6f}"
                )
            if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_values:
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_text = (
                    "; ".join(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_values)
                )
                remaining = max(
                    0,
                    len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_raw)
                    - len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_values),
                )
                if remaining > 0:
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_text += (
                        f"; ...(+{remaining} more)"
                    )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text = "n/a"
        if isinstance(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_raw, list):
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_values: list[str] = []
            for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_raw[:5]:
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
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_values.append(
                    f"{numeric_key}:{left_label}_vs_{right_label}:{profile_id}:"
                    f"delta={delta_value:.6f}:abs={abs(delta_abs_value):.6f}"
                )
            if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_values:
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text = "; ".join(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_values
                )
                baseline_total = max(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_record_count,
                    len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_raw),
                )
                remaining = max(
                    0,
                    baseline_total
                    - len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_values),
                )
                if remaining > 0:
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text += (
                        f"; ...(+{remaining} more)"
                    )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text = (
            "n/a"
        )
        if isinstance(
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw,
            list,
        ):
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_values: (
                list[str]
            ) = []
            for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw[:5]:
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
                        direction_imbalance_ratio_value = max(
                            0.0,
                            min(1.0, float(direction_imbalance_ratio_raw)),
                        )
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
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_values.append(
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
            if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_values:
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text = (
                    "; ".join(
                        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_values
                    )
                )
                baseline_total = max(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count,
                    len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw),
                )
                remaining = max(
                    0,
                    baseline_total
                    - len(
                        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_values
                    ),
                )
                if remaining > 0:
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text += (
                        f"; ...(+{remaining} more)"
                    )
        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text = "n/a"
        if isinstance(
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw,
            list,
        ):
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendation_values: list[
                str
            ] = []
            for row in runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw[:5]:
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
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendation_values.append(
                    f"{left_label}_vs_{right_label}:{profile_id}:"
                    f"action={recommended_action}:"
                    f"reason={recommended_reason}:"
                    f"checklist={checklist_text}"
                )
            if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendation_values:
                runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text = (
                    "; ".join(
                        runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendation_values
                    )
                )
                baseline_total = max(
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_count,
                    len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_raw),
                )
                remaining = max(
                    0,
                    baseline_total
                    - len(runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendation_values),
                )
                if remaining > 0:
                    runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text += (
                        f"; ...(+{remaining} more)"
                    )
        lines.append(
            "runtime_evidence_compare="
            f"artifacts_with_diffs:{int(runtime_evidence_compare_summary.get('artifacts_with_diffs_count', 0) or 0)},"
            "artifacts_without_diffs:"
            f"{int(runtime_evidence_compare_summary.get('artifacts_without_diffs_count', 0) or 0)},"
            "top_level_mismatches:"
            f"{int(runtime_evidence_compare_summary.get('top_level_mismatches_count', 0) or 0)},"
            "status_count_diffs:"
            f"{int(runtime_evidence_compare_summary.get('status_count_diffs_count', 0) or 0)},"
            "runtime_count_diffs:"
            f"{int(runtime_evidence_compare_summary.get('runtime_count_diffs_count', 0) or 0)},"
            "interop_import_status_count_diffs:"
            f"{int(runtime_evidence_compare_summary.get('interop_import_status_count_diffs_count', 0) or 0)},"
            "interop_import_manifest_consistency_diffs:"
            f"{int(runtime_evidence_compare_summary.get('interop_import_manifest_consistency_diffs_count', 0) or 0)},"
            "interop_import_profile_diffs:"
            f"{int(runtime_evidence_compare_summary.get('interop_import_profile_diff_count', 0) or 0)},"
            "profile_presence:shared="
            f"{int(runtime_evidence_compare_summary.get('shared_profile_count', 0) or 0)},"
            "left_only="
            f"{int(runtime_evidence_compare_summary.get('profile_left_only_count', 0) or 0)},"
            "right_only="
            f"{int(runtime_evidence_compare_summary.get('profile_right_only_count', 0) or 0)},"
            "profile_diffs:"
            f"{int(runtime_evidence_compare_summary.get('profile_diff_count', 0) or 0)},"
            f"label_pairs:{runtime_evidence_compare_label_pair_counts_text}"
        )
        lines.append(
            "runtime_evidence_compare_interop_import_mode_diff_counts="
            "manifest_mode:"
            f"{int(runtime_evidence_compare_summary.get('interop_import_manifest_mode_count_diffs_count', 0) or 0)},"
            "export_mode:"
            f"{int(runtime_evidence_compare_summary.get('interop_import_export_mode_count_diffs_count', 0) or 0)},"
            "require_manifest_input:"
            f"{int(runtime_evidence_compare_summary.get('interop_import_require_manifest_input_count_diffs_count', 0) or 0)},"
            "require_export_input:"
            f"{int(runtime_evidence_compare_summary.get('interop_import_require_export_input_count_diffs_count', 0) or 0)}"
        )
        if runtime_evidence_compare_interop_import_profile_diff_field_counts_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_field_counts="
                f"{runtime_evidence_compare_interop_import_profile_diff_field_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_counts_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_counts="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_label_pair_counts_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_label_pairs="
                f"{runtime_evidence_compare_interop_import_profile_diff_label_pair_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_profile_counts_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_profiles="
                f"{runtime_evidence_compare_interop_import_profile_diff_profile_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_totals_by_label_pair_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_abs_totals_by_label_pair_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_by_label_pair_profile_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_recommendations_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_priority_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_action_counts_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspot_reason_counts_text}"
            )
        if (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts_text != "n/a"
            or runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts_text != "n/a"
            or runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts_text != "n/a"
        ):
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_directions="
                "positive:"
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_positive_counts_text},"
                "negative:"
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_negative_counts_text},"
                "zero:"
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_zero_counts_text}"
            )
        if (
            runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_text != "n/a"
            or runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_text != "n/a"
        ):
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_extremes="
                "positive:"
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_positive_records_text},"
                "negative:"
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_key_max_negative_records_text}"
            )
        if runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots="
                f"{runtime_evidence_compare_interop_import_profile_diff_numeric_delta_hotspots_text}"
            )
        runtime_evidence_compare_interop_import_profile_diff_records_raw = runtime_evidence_compare_summary.get(
            "interop_import_profile_diff_records",
            [],
        )
        runtime_evidence_compare_interop_import_profile_diff_records_text = "n/a"
        if isinstance(runtime_evidence_compare_interop_import_profile_diff_records_raw, list):
            runtime_evidence_compare_interop_import_profile_diff_values: list[str] = []
            for row in runtime_evidence_compare_interop_import_profile_diff_records_raw[:5]:
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
                runtime_evidence_compare_interop_import_profile_diff_values.append(
                    f"{left_label}_vs_{right_label}:{profile_id}:fields={field_text}:numeric={numeric_text}"
                )
            if runtime_evidence_compare_interop_import_profile_diff_values:
                runtime_evidence_compare_interop_import_profile_diff_records_text = "; ".join(
                    runtime_evidence_compare_interop_import_profile_diff_values
                )
                remaining = max(
                    0,
                    len(runtime_evidence_compare_interop_import_profile_diff_records_raw)
                    - len(runtime_evidence_compare_interop_import_profile_diff_values),
                )
                if remaining > 0:
                    runtime_evidence_compare_interop_import_profile_diff_records_text += f"; ...(+{remaining} more)"
        if runtime_evidence_compare_interop_import_profile_diff_records_text != "n/a":
            lines.append(
                "runtime_evidence_compare_interop_import_profile_diffs="
                f"{runtime_evidence_compare_interop_import_profile_diff_records_text}"
            )
    else:
        lines.append("runtime_evidence_compare=n/a")
    runtime_native_evidence_compare_artifact_count = int(
        runtime_native_evidence_compare_summary.get("artifact_count", 0) or 0
    )
    lines.append(
        f"runtime_native_evidence_compare_artifact_count={runtime_native_evidence_compare_artifact_count}"
    )
    if runtime_native_evidence_compare_artifact_count > 0:
        runtime_native_evidence_compare_label_pair_counts = runtime_native_evidence_compare_summary.get(
            "label_pair_counts",
            {},
        )
        runtime_native_evidence_compare_label_pair_counts_text = (
            ",".join(
                f"{key}:{runtime_native_evidence_compare_label_pair_counts[key]}"
                for key in sorted(runtime_native_evidence_compare_label_pair_counts)
            )
            if (
                isinstance(runtime_native_evidence_compare_label_pair_counts, dict)
                and runtime_native_evidence_compare_label_pair_counts
            )
            else "n/a"
        )
        lines.append(
            "runtime_native_evidence_compare="
            "artifacts_with_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('artifacts_with_diffs_count', 0) or 0)},"
            "artifacts_without_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('artifacts_without_diffs_count', 0) or 0)},"
            "top_level_mismatches:"
            f"{int(runtime_native_evidence_compare_summary.get('top_level_mismatches_count', 0) or 0)},"
            "status_count_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('status_count_diffs_count', 0) or 0)},"
            "runtime_count_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('runtime_count_diffs_count', 0) or 0)},"
            "interop_import_status_count_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('interop_import_status_count_diffs_count', 0) or 0)},"
            "interop_import_manifest_consistency_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('interop_import_manifest_consistency_diffs_count', 0) or 0)},"
            "interop_import_profile_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('interop_import_profile_diff_count', 0) or 0)},"
            "profile_presence:shared="
            f"{int(runtime_native_evidence_compare_summary.get('shared_profile_count', 0) or 0)},"
            "left_only="
            f"{int(runtime_native_evidence_compare_summary.get('profile_left_only_count', 0) or 0)},"
            "right_only="
            f"{int(runtime_native_evidence_compare_summary.get('profile_right_only_count', 0) or 0)},"
            "profile_diffs:"
            f"{int(runtime_native_evidence_compare_summary.get('profile_diff_count', 0) or 0)},"
            f"label_pairs:{runtime_native_evidence_compare_label_pair_counts_text}"
        )
        lines.append(
            "runtime_native_evidence_compare_interop_import_mode_diff_counts="
            "manifest_mode:"
            f"{int(runtime_native_evidence_compare_summary.get('interop_import_manifest_mode_count_diffs_count', 0) or 0)},"
            "export_mode:"
            f"{int(runtime_native_evidence_compare_summary.get('interop_import_export_mode_count_diffs_count', 0) or 0)},"
            "require_manifest_input:"
            f"{int(runtime_native_evidence_compare_summary.get('interop_import_require_manifest_input_count_diffs_count', 0) or 0)},"
            "require_export_input:"
            f"{int(runtime_native_evidence_compare_summary.get('interop_import_require_export_input_count_diffs_count', 0) or 0)}"
        )
    else:
        lines.append("runtime_native_evidence_compare=n/a")
    runtime_native_summary_compare_artifact_count = int(
        runtime_native_summary_compare_summary.get("artifact_count", 0) or 0
    )
    lines.append(
        f"runtime_native_summary_compare_artifact_count={runtime_native_summary_compare_artifact_count}"
    )
    if runtime_native_summary_compare_artifact_count > 0:
        runtime_native_summary_compare_label_pair_counts = runtime_native_summary_compare_summary.get(
            "label_pair_counts",
            {},
        )
        runtime_native_summary_compare_label_pair_counts_text = (
            ",".join(
                f"{key}:{runtime_native_summary_compare_label_pair_counts[key]}"
                for key in sorted(runtime_native_summary_compare_label_pair_counts)
            )
            if (
                isinstance(runtime_native_summary_compare_label_pair_counts, dict)
                and runtime_native_summary_compare_label_pair_counts
            )
            else "n/a"
        )
        runtime_native_summary_compare_field_diff_counts = runtime_native_summary_compare_summary.get(
            "field_diff_counts",
            {},
        )
        runtime_native_summary_compare_field_diff_counts_text = (
            ",".join(
                f"{key}:{runtime_native_summary_compare_field_diff_counts[key]}"
                for key in sorted(runtime_native_summary_compare_field_diff_counts)
            )
            if (
                isinstance(runtime_native_summary_compare_field_diff_counts, dict)
                and runtime_native_summary_compare_field_diff_counts
            )
            else "n/a"
        )
        runtime_native_summary_compare_versions_with_diffs_counts = runtime_native_summary_compare_summary.get(
            "versions_with_diffs_counts",
            {},
        )
        runtime_native_summary_compare_versions_with_diffs_counts_text = (
            ",".join(
                f"{key}:{runtime_native_summary_compare_versions_with_diffs_counts[key]}"
                for key in sorted(runtime_native_summary_compare_versions_with_diffs_counts)
            )
            if (
                isinstance(runtime_native_summary_compare_versions_with_diffs_counts, dict)
                and runtime_native_summary_compare_versions_with_diffs_counts
            )
            else "n/a"
        )
        lines.append(
            "runtime_native_summary_compare="
            "artifacts_with_diffs:"
            f"{int(runtime_native_summary_compare_summary.get('artifacts_with_diffs_count', 0) or 0)},"
            "artifacts_without_diffs:"
            f"{int(runtime_native_summary_compare_summary.get('artifacts_without_diffs_count', 0) or 0)},"
            f"versions_total:{int(runtime_native_summary_compare_summary.get('versions_total', 0) or 0)},"
            "comparisons_total:"
            f"{int(runtime_native_summary_compare_summary.get('comparisons_total', 0) or 0)},"
            "versions_with_diffs_total:"
            f"{int(runtime_native_summary_compare_summary.get('versions_with_diffs_total', 0) or 0)},"
            f"label_pairs:{runtime_native_summary_compare_label_pair_counts_text}"
        )
        if runtime_native_summary_compare_field_diff_counts_text != "n/a":
            lines.append(
                "runtime_native_summary_compare_field_diff_counts="
                f"{runtime_native_summary_compare_field_diff_counts_text}"
            )
        if runtime_native_summary_compare_versions_with_diffs_counts_text != "n/a":
            lines.append(
                "runtime_native_summary_compare_versions_with_diffs="
                f"{runtime_native_summary_compare_versions_with_diffs_counts_text}"
            )
    else:
        lines.append("runtime_native_summary_compare=n/a")
    phase2_log_replay_evaluated_count = int(phase2_log_replay_summary.get("evaluated_manifest_count", 0) or 0)
    if phase2_log_replay_evaluated_count > 0:
        phase2_log_replay_status_counts = phase2_log_replay_summary.get("status_counts", {})
        phase2_log_replay_status_counts_text = (
            ",".join(
                f"{key}:{phase2_log_replay_status_counts[key]}"
                for key in sorted(phase2_log_replay_status_counts)
            )
            if isinstance(phase2_log_replay_status_counts, dict) and phase2_log_replay_status_counts
            else "n/a"
        )
        phase2_log_replay_run_status_counts = phase2_log_replay_summary.get("run_status_counts", {})
        phase2_log_replay_run_status_counts_text = (
            ",".join(
                f"{key}:{phase2_log_replay_run_status_counts[key]}"
                for key in sorted(phase2_log_replay_run_status_counts)
            )
            if isinstance(phase2_log_replay_run_status_counts, dict) and phase2_log_replay_run_status_counts
            else "n/a"
        )
        phase2_log_replay_run_source_counts = phase2_log_replay_summary.get("run_source_counts", {})
        phase2_log_replay_run_source_counts_text = (
            ",".join(
                f"{key}:{phase2_log_replay_run_source_counts[key]}"
                for key in sorted(phase2_log_replay_run_source_counts)
            )
            if isinstance(phase2_log_replay_run_source_counts, dict) and phase2_log_replay_run_source_counts
            else "n/a"
        )
        phase2_log_replay_manifest_present_count = int(
            phase2_log_replay_summary.get("manifest_present_count", 0) or 0
        )
        phase2_log_replay_summary_present_count = int(
            phase2_log_replay_summary.get("summary_present_count", 0) or 0
        )
        phase2_log_replay_missing_manifest_count = int(
            phase2_log_replay_summary.get("missing_manifest_count", 0) or 0
        )
        phase2_log_replay_missing_summary_count = int(
            phase2_log_replay_summary.get("missing_summary_count", 0) or 0
        )
        phase2_log_replay_log_id_present_count = int(
            phase2_log_replay_summary.get("log_id_present_count", 0) or 0
        )
        phase2_log_replay_map_id_present_count = int(
            phase2_log_replay_summary.get("map_id_present_count", 0) or 0
        )
        lines.append(
            "phase2_log_replay="
            f"evaluated:{phase2_log_replay_evaluated_count},"
            f"statuses:{phase2_log_replay_status_counts_text},"
            f"run_statuses:{phase2_log_replay_run_status_counts_text},"
            f"run_sources:{phase2_log_replay_run_source_counts_text},"
            f"manifest_present:{phase2_log_replay_manifest_present_count},"
            f"summary_present:{phase2_log_replay_summary_present_count},"
            f"missing_manifest:{phase2_log_replay_missing_manifest_count},"
            f"missing_summary:{phase2_log_replay_missing_summary_count},"
            f"log_id_present:{phase2_log_replay_log_id_present_count},"
            f"map_id_present:{phase2_log_replay_map_id_present_count}"
        )
    else:
        lines.append("phase2_log_replay=n/a")
    phase2_map_routing_evaluated_count = int(phase2_map_routing_summary.get("evaluated_manifest_count", 0) or 0)
    if phase2_map_routing_evaluated_count > 0:
        phase2_map_routing_status_counts = phase2_map_routing_summary.get("status_counts", {})
        phase2_map_routing_status_counts_text = (
            ",".join(
                f"{key}:{phase2_map_routing_status_counts[key]}"
                for key in sorted(phase2_map_routing_status_counts)
            )
            if isinstance(phase2_map_routing_status_counts, dict) and phase2_map_routing_status_counts
            else "n/a"
        )
        phase2_map_routing_error_total = int(phase2_map_routing_summary.get("error_count_total", 0) or 0)
        phase2_map_routing_warning_total = int(phase2_map_routing_summary.get("warning_count_total", 0) or 0)
        phase2_map_routing_semantic_warning_total = int(
            phase2_map_routing_summary.get("semantic_warning_count_total", 0) or 0
        )
        phase2_map_routing_unreachable_total = int(
            phase2_map_routing_summary.get("unreachable_lane_count_total", 0) or 0
        )
        phase2_map_routing_non_reciprocal_total = int(
            phase2_map_routing_summary.get("non_reciprocal_link_count_total", 0) or 0
        )
        phase2_map_routing_continuity_gap_total = int(
            phase2_map_routing_summary.get("continuity_gap_warning_count_total", 0) or 0
        )
        phase2_map_routing_max_unreachable = int(
            phase2_map_routing_summary.get("max_unreachable_lane_count", 0) or 0
        )
        phase2_map_routing_highest_unreachable_batch = (
            str(phase2_map_routing_summary.get("highest_unreachable_batch_id", "")).strip() or "n/a"
        )
        phase2_map_routing_max_non_reciprocal = int(
            phase2_map_routing_summary.get("max_non_reciprocal_link_count", 0) or 0
        )
        phase2_map_routing_highest_non_reciprocal_batch = (
            str(phase2_map_routing_summary.get("highest_non_reciprocal_batch_id", "")).strip() or "n/a"
        )
        phase2_map_routing_max_continuity_gap = int(
            phase2_map_routing_summary.get("max_continuity_gap_warning_count", 0) or 0
        )
        phase2_map_routing_highest_continuity_gap_batch = (
            str(phase2_map_routing_summary.get("highest_continuity_gap_batch_id", "")).strip() or "n/a"
        )
        phase2_map_route_evaluated_count = int(
            phase2_map_routing_summary.get("route_evaluated_manifest_count", 0) or 0
        )
        phase2_map_route_status_counts = phase2_map_routing_summary.get("route_status_counts", {})
        phase2_map_route_status_counts_text = (
            ",".join(
                f"{key}:{phase2_map_route_status_counts[key]}"
                for key in sorted(phase2_map_route_status_counts)
            )
            if isinstance(phase2_map_route_status_counts, dict) and phase2_map_route_status_counts
            else "n/a"
        )
        phase2_map_route_lane_total = int(phase2_map_routing_summary.get("route_lane_count_total", 0) or 0)
        phase2_map_route_hop_total = int(phase2_map_routing_summary.get("route_hop_count_total", 0) or 0)
        phase2_map_route_length_total_m = float(phase2_map_routing_summary.get("route_total_length_m_total", 0.0) or 0.0)
        phase2_map_route_length_avg_m = float(phase2_map_routing_summary.get("route_total_length_m_avg", 0.0) or 0.0)
        phase2_map_route_segment_total = int(phase2_map_routing_summary.get("route_segment_count_total", 0) or 0)
        phase2_map_route_segment_avg = float(phase2_map_routing_summary.get("route_segment_count_avg", 0.0) or 0.0)
        phase2_map_route_with_via_count = int(
            phase2_map_routing_summary.get("route_with_via_manifest_count", 0) or 0
        )
        phase2_map_route_via_lane_total = int(phase2_map_routing_summary.get("route_via_lane_count_total", 0) or 0)
        phase2_map_route_via_lane_avg = float(phase2_map_routing_summary.get("route_via_lane_count_avg", 0.0) or 0.0)
        phase2_map_route_max_lane_count = int(phase2_map_routing_summary.get("max_route_lane_count", 0) or 0)
        phase2_map_route_highest_lane_batch = (
            str(phase2_map_routing_summary.get("highest_route_lane_count_batch_id", "")).strip() or "n/a"
        )
        phase2_map_route_max_hop_count = int(phase2_map_routing_summary.get("max_route_hop_count", 0) or 0)
        phase2_map_route_highest_hop_batch = (
            str(phase2_map_routing_summary.get("highest_route_hop_count_batch_id", "")).strip() or "n/a"
        )
        phase2_map_route_max_segment_count = int(phase2_map_routing_summary.get("max_route_segment_count", 0) or 0)
        phase2_map_route_highest_segment_batch = (
            str(phase2_map_routing_summary.get("highest_route_segment_count_batch_id", "")).strip() or "n/a"
        )
        phase2_map_route_max_via_lane_count = int(phase2_map_routing_summary.get("max_route_via_lane_count", 0) or 0)
        phase2_map_route_highest_via_lane_batch = (
            str(phase2_map_routing_summary.get("highest_route_via_lane_count_batch_id", "")).strip() or "n/a"
        )
        phase2_map_route_max_length_m = float(phase2_map_routing_summary.get("max_route_total_length_m", 0.0) or 0.0)
        phase2_map_route_highest_length_batch = (
            str(phase2_map_routing_summary.get("highest_route_total_length_batch_id", "")).strip() or "n/a"
        )
        lines.append(
            "phase2_map_routing="
            f"evaluated:{phase2_map_routing_evaluated_count},"
            f"statuses:{phase2_map_routing_status_counts_text},"
            f"errors_total:{phase2_map_routing_error_total},"
            f"warnings_total:{phase2_map_routing_warning_total},"
            f"semantic_warnings_total:{phase2_map_routing_semantic_warning_total},"
            f"unreachable_total:{phase2_map_routing_unreachable_total},"
            f"non_reciprocal_total:{phase2_map_routing_non_reciprocal_total},"
            f"continuity_gap_total:{phase2_map_routing_continuity_gap_total},"
            f"max_unreachable:{phase2_map_routing_max_unreachable}({phase2_map_routing_highest_unreachable_batch}),"
            "max_non_reciprocal:"
            f"{phase2_map_routing_max_non_reciprocal}({phase2_map_routing_highest_non_reciprocal_batch}),"
            "max_continuity_gap:"
            f"{phase2_map_routing_max_continuity_gap}({phase2_map_routing_highest_continuity_gap_batch}),"
            f"route_evaluated:{phase2_map_route_evaluated_count},"
            f"route_statuses:{phase2_map_route_status_counts_text},"
            f"route_lane_total:{phase2_map_route_lane_total},"
            f"route_hop_total:{phase2_map_route_hop_total},"
            f"route_length_total_m:{phase2_map_route_length_total_m:.3f},"
            f"route_length_avg_m:{phase2_map_route_length_avg_m:.3f},"
            f"route_segment_total:{phase2_map_route_segment_total},"
            f"route_segment_avg:{phase2_map_route_segment_avg:.3f},"
            f"route_with_via:{phase2_map_route_with_via_count},"
            f"route_via_lane_total:{phase2_map_route_via_lane_total},"
            f"route_via_lane_avg:{phase2_map_route_via_lane_avg:.3f},"
            f"max_route_lane:{phase2_map_route_max_lane_count}({phase2_map_route_highest_lane_batch}),"
            f"max_route_hop:{phase2_map_route_max_hop_count}({phase2_map_route_highest_hop_batch}),"
            f"max_route_segment:{phase2_map_route_max_segment_count}({phase2_map_route_highest_segment_batch}),"
            f"max_route_via_lane:{phase2_map_route_max_via_lane_count}({phase2_map_route_highest_via_lane_batch}),"
            f"max_route_length_m:{phase2_map_route_max_length_m:.3f}({phase2_map_route_highest_length_batch})"
        )
    else:
        lines.append("phase2_map_routing=n/a")
    phase2_sensor_evaluated_count = int(phase2_sensor_fidelity_summary.get("evaluated_manifest_count", 0) or 0)
    if phase2_sensor_evaluated_count > 0:
        phase2_sensor_fidelity_tier_counts = phase2_sensor_fidelity_summary.get("fidelity_tier_counts", {})
        phase2_sensor_fidelity_tier_counts_text = (
            ",".join(
                f"{key}:{phase2_sensor_fidelity_tier_counts[key]}"
                for key in sorted(phase2_sensor_fidelity_tier_counts)
            )
            if isinstance(phase2_sensor_fidelity_tier_counts, dict) and phase2_sensor_fidelity_tier_counts
            else "n/a"
        )
        phase2_sensor_modality_counts_total = phase2_sensor_fidelity_summary.get("sensor_modality_counts_total", {})
        phase2_sensor_modality_counts_total_text = (
            ",".join(
                f"{key}:{phase2_sensor_modality_counts_total[key]}"
                for key in sorted(phase2_sensor_modality_counts_total)
            )
            if isinstance(phase2_sensor_modality_counts_total, dict) and phase2_sensor_modality_counts_total
            else "n/a"
        )
        phase2_sensor_fidelity_score_avg = float(
            phase2_sensor_fidelity_summary.get("fidelity_tier_score_avg", 0.0) or 0.0
        )
        phase2_sensor_fidelity_score_max = float(
            phase2_sensor_fidelity_summary.get("fidelity_tier_score_max", 0.0) or 0.0
        )
        phase2_sensor_fidelity_score_max_batch = (
            str(phase2_sensor_fidelity_summary.get("highest_fidelity_tier_score_batch_id", "")).strip() or "n/a"
        )
        phase2_sensor_frame_total = int(phase2_sensor_fidelity_summary.get("sensor_frame_count_total", 0) or 0)
        phase2_sensor_frame_avg = float(phase2_sensor_fidelity_summary.get("sensor_frame_count_avg", 0.0) or 0.0)
        phase2_sensor_frame_max = int(phase2_sensor_fidelity_summary.get("sensor_frame_count_max", 0) or 0)
        phase2_sensor_frame_max_batch = (
            str(phase2_sensor_fidelity_summary.get("highest_sensor_frame_count_batch_id", "")).strip() or "n/a"
        )
        phase2_sensor_camera_noise_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_noise_stddev_px_avg", 0.0) or 0.0
        )
        phase2_sensor_camera_distortion_shift_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_distortion_edge_shift_px_avg", 0.0) or 0.0
        )
        phase2_sensor_camera_projection_mode_counts_total = phase2_sensor_fidelity_summary.get(
            "sensor_camera_projection_mode_counts_total",
            {},
        )
        phase2_sensor_camera_projection_mode_counts_total_text = (
            ",".join(
                f"{key}:{phase2_sensor_camera_projection_mode_counts_total[key]}"
                for key in sorted(phase2_sensor_camera_projection_mode_counts_total)
            )
            if (
                isinstance(phase2_sensor_camera_projection_mode_counts_total, dict)
                and phase2_sensor_camera_projection_mode_counts_total
            )
            else "n/a"
        )
        phase2_sensor_camera_gain_db_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_gain_db_avg", 0.0) or 0.0
        )
        phase2_sensor_camera_bloom_halo_strength_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_bloom_halo_strength_avg", 0.0) or 0.0
        )
        phase2_sensor_camera_tonemapper_disabled_frame_total = int(
            phase2_sensor_fidelity_summary.get("sensor_camera_tonemapper_disabled_frame_count_total", 0) or 0
        )
        phase2_sensor_camera_bloom_level_counts_total = phase2_sensor_fidelity_summary.get(
            "sensor_camera_bloom_level_counts_total",
            {},
        )
        phase2_sensor_camera_bloom_level_counts_total_text = (
            ",".join(
                f"{key}:{phase2_sensor_camera_bloom_level_counts_total[key]}"
                for key in sorted(phase2_sensor_camera_bloom_level_counts_total)
            )
            if (
                isinstance(phase2_sensor_camera_bloom_level_counts_total, dict)
                and phase2_sensor_camera_bloom_level_counts_total
            )
            else "n/a"
        )
        phase2_sensor_camera_depth_enabled_frame_total = int(
            phase2_sensor_fidelity_summary.get("sensor_camera_depth_enabled_frame_count_total", 0) or 0
        )
        phase2_sensor_camera_depth_min_m_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_depth_min_m_avg", 0.0) or 0.0
        )
        phase2_sensor_camera_depth_max_m_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_depth_max_m_avg", 0.0) or 0.0
        )
        phase2_sensor_camera_depth_bit_depth_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_depth_bit_depth_avg", 0.0) or 0.0
        )
        phase2_sensor_camera_depth_mode_counts_total = phase2_sensor_fidelity_summary.get(
            "sensor_camera_depth_mode_counts_total",
            {},
        )
        phase2_sensor_camera_depth_mode_counts_total_text = (
            ",".join(
                f"{key}:{phase2_sensor_camera_depth_mode_counts_total[key]}"
                for key in sorted(phase2_sensor_camera_depth_mode_counts_total)
            )
            if (
                isinstance(phase2_sensor_camera_depth_mode_counts_total, dict)
                and phase2_sensor_camera_depth_mode_counts_total
            )
            else "n/a"
        )
        phase2_sensor_camera_optical_flow_enabled_frame_total = int(
            phase2_sensor_fidelity_summary.get(
                "sensor_camera_optical_flow_enabled_frame_count_total",
                0,
            )
            or 0
        )
        phase2_sensor_camera_optical_flow_magnitude_px_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_camera_optical_flow_magnitude_px_avg", 0.0)
            or 0.0
        )
        phase2_sensor_camera_optical_flow_velocity_direction_counts_total = (
            phase2_sensor_fidelity_summary.get(
                "sensor_camera_optical_flow_velocity_direction_counts_total",
                {},
            )
        )
        phase2_sensor_camera_optical_flow_velocity_direction_counts_total_text = (
            ",".join(
                f"{key}:{phase2_sensor_camera_optical_flow_velocity_direction_counts_total[key]}"
                for key in sorted(phase2_sensor_camera_optical_flow_velocity_direction_counts_total)
            )
            if (
                isinstance(
                    phase2_sensor_camera_optical_flow_velocity_direction_counts_total,
                    dict,
                )
                and phase2_sensor_camera_optical_flow_velocity_direction_counts_total
            )
            else "n/a"
        )
        phase2_sensor_camera_optical_flow_y_axis_direction_counts_total = (
            phase2_sensor_fidelity_summary.get(
                "sensor_camera_optical_flow_y_axis_direction_counts_total",
                {},
            )
        )
        phase2_sensor_camera_optical_flow_y_axis_direction_counts_total_text = (
            ",".join(
                f"{key}:{phase2_sensor_camera_optical_flow_y_axis_direction_counts_total[key]}"
                for key in sorted(phase2_sensor_camera_optical_flow_y_axis_direction_counts_total)
            )
            if (
                isinstance(
                    phase2_sensor_camera_optical_flow_y_axis_direction_counts_total,
                    dict,
                )
                and phase2_sensor_camera_optical_flow_y_axis_direction_counts_total
            )
            else "n/a"
        )
        phase2_sensor_lidar_point_total = int(
            phase2_sensor_fidelity_summary.get("sensor_lidar_point_count_total", 0) or 0
        )
        phase2_sensor_lidar_point_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_lidar_point_count_avg", 0.0) or 0.0
        )
        phase2_sensor_lidar_detection_ratio_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_lidar_detection_ratio_avg", 0.0) or 0.0
        )
        phase2_sensor_radar_false_positive_total = int(
            phase2_sensor_fidelity_summary.get("sensor_radar_false_positive_count_total", 0) or 0
        )
        phase2_sensor_radar_false_positive_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_radar_false_positive_count_avg", 0.0) or 0.0
        )
        phase2_sensor_radar_false_positive_rate_avg = float(
            phase2_sensor_fidelity_summary.get("sensor_radar_false_positive_rate_avg", 0.0) or 0.0
        )
        phase2_sensor_radar_ghost_target_total = int(
            phase2_sensor_fidelity_summary.get("sensor_radar_ghost_target_count_total", 0) or 0
        )
        lines.append(
            "phase2_sensor_fidelity="
            f"evaluated:{phase2_sensor_evaluated_count},"
            f"tier_counts:{phase2_sensor_fidelity_tier_counts_text},"
            f"fidelity_score_avg:{phase2_sensor_fidelity_score_avg:.3f},"
            f"fidelity_score_max:{phase2_sensor_fidelity_score_max:.3f}({phase2_sensor_fidelity_score_max_batch}),"
            f"frame_total:{phase2_sensor_frame_total},"
            f"frame_avg:{phase2_sensor_frame_avg:.3f},"
            f"frame_max:{phase2_sensor_frame_max}({phase2_sensor_frame_max_batch}),"
            f"modality_total:{phase2_sensor_modality_counts_total_text},"
            f"camera_noise_avg_px:{phase2_sensor_camera_noise_avg:.3f},"
            f"lidar_point_total:{phase2_sensor_lidar_point_total},"
            f"lidar_point_avg:{phase2_sensor_lidar_point_avg:.3f},"
            f"radar_fp_total:{phase2_sensor_radar_false_positive_total},"
            f"radar_fp_avg:{phase2_sensor_radar_false_positive_avg:.3f},"
            f"radar_fp_rate_avg:{phase2_sensor_radar_false_positive_rate_avg:.6f},"
            f"camera_distortion_shift_avg_px:{phase2_sensor_camera_distortion_shift_avg:.3f},"
            f"camera_projection_modes:{phase2_sensor_camera_projection_mode_counts_total_text},"
            f"camera_gain_avg_db:{phase2_sensor_camera_gain_db_avg:.3f},"
            f"camera_bloom_halo_avg:{phase2_sensor_camera_bloom_halo_strength_avg:.3f},"
            f"camera_tonemapper_disabled_total:{phase2_sensor_camera_tonemapper_disabled_frame_total},"
            f"camera_bloom_levels:{phase2_sensor_camera_bloom_level_counts_total_text},"
            f"camera_depth_enabled_total:{phase2_sensor_camera_depth_enabled_frame_total},"
            f"camera_depth_min_avg_m:{phase2_sensor_camera_depth_min_m_avg:.3f},"
            f"camera_depth_max_avg_m:{phase2_sensor_camera_depth_max_m_avg:.3f},"
            f"camera_depth_bit_depth_avg:{phase2_sensor_camera_depth_bit_depth_avg:.3f},"
            f"camera_depth_modes:{phase2_sensor_camera_depth_mode_counts_total_text},"
            f"camera_flow_enabled_total:{phase2_sensor_camera_optical_flow_enabled_frame_total},"
            f"camera_flow_mag_avg_px:{phase2_sensor_camera_optical_flow_magnitude_px_avg:.3f},"
            "camera_flow_velocity_dirs:"
            f"{phase2_sensor_camera_optical_flow_velocity_direction_counts_total_text},"
            "camera_flow_y_axis_dirs:"
            f"{phase2_sensor_camera_optical_flow_y_axis_direction_counts_total_text},"
            f"lidar_detection_ratio_avg:{phase2_sensor_lidar_detection_ratio_avg:.3f},"
            f"radar_ghost_total:{phase2_sensor_radar_ghost_target_total}"
        )
        phase2_sensor_rig_sweep_evaluated_count = int(
            phase2_sensor_fidelity_summary.get("rig_sweep_evaluated_manifest_count", 0) or 0
        )
        if phase2_sensor_rig_sweep_evaluated_count > 0:
            phase2_sensor_rig_sweep_tier_counts = phase2_sensor_fidelity_summary.get("rig_sweep_fidelity_tier_counts", {})
            phase2_sensor_rig_sweep_tier_counts_text = (
                ",".join(
                    f"{key}:{phase2_sensor_rig_sweep_tier_counts[key]}"
                    for key in sorted(phase2_sensor_rig_sweep_tier_counts)
                )
                if isinstance(phase2_sensor_rig_sweep_tier_counts, dict) and phase2_sensor_rig_sweep_tier_counts
                else "n/a"
            )
            phase2_sensor_rig_sweep_best_rig_counts = phase2_sensor_fidelity_summary.get("rig_sweep_best_rig_id_counts", {})
            phase2_sensor_rig_sweep_best_rig_counts_text = (
                ",".join(
                    f"{key}:{phase2_sensor_rig_sweep_best_rig_counts[key]}"
                    for key in sorted(phase2_sensor_rig_sweep_best_rig_counts)
                )
                if isinstance(phase2_sensor_rig_sweep_best_rig_counts, dict) and phase2_sensor_rig_sweep_best_rig_counts
                else "n/a"
            )
            phase2_sensor_rig_sweep_candidate_total = int(
                phase2_sensor_fidelity_summary.get("rig_sweep_candidate_count_total", 0) or 0
            )
            phase2_sensor_rig_sweep_candidate_avg = float(
                phase2_sensor_fidelity_summary.get("rig_sweep_candidate_count_avg", 0.0) or 0.0
            )
            phase2_sensor_rig_sweep_candidate_max = int(
                phase2_sensor_fidelity_summary.get("rig_sweep_candidate_count_max", 0) or 0
            )
            phase2_sensor_rig_sweep_candidate_max_batch = (
                str(phase2_sensor_fidelity_summary.get("rig_sweep_highest_candidate_count_batch_id", "")).strip() or "n/a"
            )
            phase2_sensor_rig_sweep_best_score_max = float(
                phase2_sensor_fidelity_summary.get("rig_sweep_best_heuristic_score_max", 0.0) or 0.0
            )
            phase2_sensor_rig_sweep_best_score_max_batch = (
                str(
                    phase2_sensor_fidelity_summary.get(
                        "rig_sweep_highest_best_heuristic_score_batch_id",
                        "",
                    )
                ).strip()
                or "n/a"
            )
            lines.append(
                "phase2_sensor_rig_sweep="
                f"evaluated:{phase2_sensor_rig_sweep_evaluated_count},"
                f"tier_counts:{phase2_sensor_rig_sweep_tier_counts_text},"
                f"candidate_total:{phase2_sensor_rig_sweep_candidate_total},"
                f"candidate_avg:{phase2_sensor_rig_sweep_candidate_avg:.3f},"
                f"candidate_max:{phase2_sensor_rig_sweep_candidate_max}({phase2_sensor_rig_sweep_candidate_max_batch}),"
                f"best_score_max:{phase2_sensor_rig_sweep_best_score_max:.3f}({phase2_sensor_rig_sweep_best_score_max_batch}),"
                f"best_rig_counts:{phase2_sensor_rig_sweep_best_rig_counts_text}"
            )
        else:
            lines.append("phase2_sensor_rig_sweep=n/a")
    else:
        lines.append("phase2_sensor_fidelity=n/a")
        lines.append("phase2_sensor_rig_sweep=n/a")
    runtime_native_smoke_module_summaries = runtime_native_smoke_summary.get("module_summaries", {})
    if isinstance(runtime_native_smoke_module_summaries, dict) and runtime_native_smoke_module_summaries:
        runtime_native_smoke_module_text_parts: list[str] = []
        for module_name in ("object_sim", "log_sim", "map_toolset"):
            module_payload = runtime_native_smoke_module_summaries.get(module_name, {})
            if not isinstance(module_payload, dict):
                continue
            module_status_counts = module_payload.get("status_counts", {})
            module_status_counts_text = (
                ",".join(f"{key}:{module_status_counts[key]}" for key in sorted(module_status_counts))
                if isinstance(module_status_counts, dict) and module_status_counts
                else "n/a"
            )
            module_evaluated_count = int(module_payload.get("evaluated_manifest_count", 0) or 0)
            runtime_native_smoke_module_text_parts.append(
                f"{module_name}(evaluated:{module_evaluated_count},statuses:{module_status_counts_text})"
            )
        runtime_native_smoke_all_status_counts = runtime_native_smoke_summary.get("all_modules_status_counts", {})
        runtime_native_smoke_all_status_counts_text = (
            ",".join(
                f"{key}:{runtime_native_smoke_all_status_counts[key]}"
                for key in sorted(runtime_native_smoke_all_status_counts)
            )
            if isinstance(runtime_native_smoke_all_status_counts, dict) and runtime_native_smoke_all_status_counts
            else "n/a"
        )
        runtime_native_smoke_all_pass_manifest_count = int(
            runtime_native_smoke_summary.get("all_modules_pass_manifest_count", 0) or 0
        )
        runtime_native_smoke_evaluated_count = int(runtime_native_smoke_summary.get("evaluated_manifest_count", 0) or 0)
        lines.append(
            "runtime_native_smoke="
            f"evaluated:{runtime_native_smoke_evaluated_count},"
            f"all_statuses:{runtime_native_smoke_all_status_counts_text},"
            f"all_pass:{runtime_native_smoke_all_pass_manifest_count},"
            f"modules:{';'.join(runtime_native_smoke_module_text_parts) or 'n/a'}"
        )
    else:
        lines.append("runtime_native_smoke=n/a")
    phase3_evaluated_count = int(phase3_vehicle_dynamics_summary.get("evaluated_manifest_count", 0) or 0)
    if phase3_evaluated_count > 0:
        phase3_models = phase3_vehicle_dynamics_summary.get("models", [])
        phase3_models_text = (
            ",".join(str(item).strip() for item in phase3_models if str(item).strip())
            if isinstance(phase3_models, list)
            else ""
        ) or "n/a"
        phase3_min_speed = float(phase3_vehicle_dynamics_summary.get("min_final_speed_mps", 0.0) or 0.0)
        phase3_avg_speed = float(phase3_vehicle_dynamics_summary.get("avg_final_speed_mps", 0.0) or 0.0)
        phase3_max_speed = float(phase3_vehicle_dynamics_summary.get("max_final_speed_mps", 0.0) or 0.0)
        phase3_lowest_speed_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_speed_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_speed_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_speed_batch_id", "")).strip() or "n/a"
        )
        phase3_min_position = float(phase3_vehicle_dynamics_summary.get("min_final_position_m", 0.0) or 0.0)
        phase3_avg_position = float(phase3_vehicle_dynamics_summary.get("avg_final_position_m", 0.0) or 0.0)
        phase3_max_position = float(phase3_vehicle_dynamics_summary.get("max_final_position_m", 0.0) or 0.0)
        phase3_lowest_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_position_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_position_batch_id", "")).strip() or "n/a"
        )
        phase3_min_delta_speed = float(phase3_vehicle_dynamics_summary.get("min_delta_speed_mps", 0.0) or 0.0)
        phase3_avg_delta_speed = float(phase3_vehicle_dynamics_summary.get("avg_delta_speed_mps", 0.0) or 0.0)
        phase3_max_delta_speed = float(phase3_vehicle_dynamics_summary.get("max_delta_speed_mps", 0.0) or 0.0)
        phase3_lowest_delta_speed_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_delta_speed_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_delta_speed_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_delta_speed_batch_id", "")).strip() or "n/a"
        )
        phase3_min_delta_position = float(
            phase3_vehicle_dynamics_summary.get("min_delta_position_m", 0.0) or 0.0
        )
        phase3_avg_delta_position = float(
            phase3_vehicle_dynamics_summary.get("avg_delta_position_m", 0.0) or 0.0
        )
        phase3_max_delta_position = float(
            phase3_vehicle_dynamics_summary.get("max_delta_position_m", 0.0) or 0.0
        )
        phase3_lowest_delta_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_delta_position_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_delta_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_delta_position_batch_id", "")).strip() or "n/a"
        )
        phase3_planar_enabled_count = int(
            phase3_vehicle_dynamics_summary.get("planar_enabled_manifest_count", 0) or 0
        )
        phase3_dynamic_enabled_count = int(
            phase3_vehicle_dynamics_summary.get("dynamic_enabled_manifest_count", 0) or 0
        )
        phase3_min_heading = float(phase3_vehicle_dynamics_summary.get("min_final_heading_deg", 0.0) or 0.0)
        phase3_avg_heading = float(phase3_vehicle_dynamics_summary.get("avg_final_heading_deg", 0.0) or 0.0)
        phase3_max_heading = float(phase3_vehicle_dynamics_summary.get("max_final_heading_deg", 0.0) or 0.0)
        phase3_lowest_heading_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_heading_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_heading_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_heading_batch_id", "")).strip() or "n/a"
        )
        phase3_min_lateral_position = float(
            phase3_vehicle_dynamics_summary.get("min_final_lateral_position_m", 0.0) or 0.0
        )
        phase3_avg_lateral_position = float(
            phase3_vehicle_dynamics_summary.get("avg_final_lateral_position_m", 0.0) or 0.0
        )
        phase3_max_lateral_position = float(
            phase3_vehicle_dynamics_summary.get("max_final_lateral_position_m", 0.0) or 0.0
        )
        phase3_lowest_lateral_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_lateral_position_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_lateral_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_lateral_position_batch_id", "")).strip() or "n/a"
        )
        phase3_min_lateral_velocity = float(
            phase3_vehicle_dynamics_summary.get("min_final_lateral_velocity_mps", 0.0) or 0.0
        )
        phase3_avg_lateral_velocity = float(
            phase3_vehicle_dynamics_summary.get("avg_final_lateral_velocity_mps", 0.0) or 0.0
        )
        phase3_max_lateral_velocity = float(
            phase3_vehicle_dynamics_summary.get("max_final_lateral_velocity_mps", 0.0) or 0.0
        )
        phase3_lowest_lateral_velocity_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_lateral_velocity_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_lateral_velocity_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_lateral_velocity_batch_id", "")).strip() or "n/a"
        )
        phase3_min_yaw_rate_final = float(
            phase3_vehicle_dynamics_summary.get("min_final_yaw_rate_rps", 0.0) or 0.0
        )
        phase3_avg_yaw_rate_final = float(
            phase3_vehicle_dynamics_summary.get("avg_final_yaw_rate_rps", 0.0) or 0.0
        )
        phase3_max_yaw_rate_final = float(
            phase3_vehicle_dynamics_summary.get("max_final_yaw_rate_rps", 0.0) or 0.0
        )
        phase3_lowest_yaw_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_yaw_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_yaw_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_yaw_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_min_delta_heading = float(phase3_vehicle_dynamics_summary.get("min_delta_heading_deg", 0.0) or 0.0)
        phase3_avg_delta_heading = float(phase3_vehicle_dynamics_summary.get("avg_delta_heading_deg", 0.0) or 0.0)
        phase3_max_delta_heading = float(phase3_vehicle_dynamics_summary.get("max_delta_heading_deg", 0.0) or 0.0)
        phase3_lowest_delta_heading_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_delta_heading_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_delta_heading_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_delta_heading_batch_id", "")).strip() or "n/a"
        )
        phase3_min_delta_lateral_position = float(
            phase3_vehicle_dynamics_summary.get("min_delta_lateral_position_m", 0.0) or 0.0
        )
        phase3_avg_delta_lateral_position = float(
            phase3_vehicle_dynamics_summary.get("avg_delta_lateral_position_m", 0.0) or 0.0
        )
        phase3_max_delta_lateral_position = float(
            phase3_vehicle_dynamics_summary.get("max_delta_lateral_position_m", 0.0) or 0.0
        )
        phase3_lowest_delta_lateral_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_delta_lateral_position_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_delta_lateral_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_delta_lateral_position_batch_id", "")).strip()
            or "n/a"
        )
        phase3_min_delta_lateral_velocity = float(
            phase3_vehicle_dynamics_summary.get("min_delta_lateral_velocity_mps", 0.0) or 0.0
        )
        phase3_avg_delta_lateral_velocity = float(
            phase3_vehicle_dynamics_summary.get("avg_delta_lateral_velocity_mps", 0.0) or 0.0
        )
        phase3_max_delta_lateral_velocity = float(
            phase3_vehicle_dynamics_summary.get("max_delta_lateral_velocity_mps", 0.0) or 0.0
        )
        phase3_lowest_delta_lateral_velocity_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_delta_lateral_velocity_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_delta_lateral_velocity_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_delta_lateral_velocity_batch_id", "")).strip()
            or "n/a"
        )
        phase3_min_delta_yaw_rate = float(
            phase3_vehicle_dynamics_summary.get("min_delta_yaw_rate_rps", 0.0) or 0.0
        )
        phase3_avg_delta_yaw_rate = float(
            phase3_vehicle_dynamics_summary.get("avg_delta_yaw_rate_rps", 0.0) or 0.0
        )
        phase3_max_delta_yaw_rate = float(
            phase3_vehicle_dynamics_summary.get("max_delta_yaw_rate_rps", 0.0) or 0.0
        )
        phase3_lowest_delta_yaw_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_delta_yaw_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_delta_yaw_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_delta_yaw_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_yaw_rate = float(
            phase3_vehicle_dynamics_summary.get("max_abs_yaw_rate_rps", 0.0) or 0.0
        )
        phase3_highest_abs_yaw_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_yaw_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_lateral_velocity = float(
            phase3_vehicle_dynamics_summary.get("max_abs_lateral_velocity_mps", 0.0) or 0.0
        )
        phase3_highest_abs_lateral_velocity_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_velocity_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_accel = float(phase3_vehicle_dynamics_summary.get("max_abs_accel_mps2", 0.0) or 0.0)
        phase3_highest_abs_accel_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_accel_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_lateral_accel = float(
            phase3_vehicle_dynamics_summary.get("max_abs_lateral_accel_mps2", 0.0) or 0.0
        )
        phase3_highest_abs_lateral_accel_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_accel_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_yaw_accel = float(
            phase3_vehicle_dynamics_summary.get("max_abs_yaw_accel_rps2", 0.0) or 0.0
        )
        phase3_highest_abs_yaw_accel_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_yaw_accel_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_jerk = float(phase3_vehicle_dynamics_summary.get("max_abs_jerk_mps3", 0.0) or 0.0)
        phase3_highest_abs_jerk_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_jerk_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_lateral_jerk = float(
            phase3_vehicle_dynamics_summary.get("max_abs_lateral_jerk_mps3", 0.0) or 0.0
        )
        phase3_highest_abs_lateral_jerk_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_jerk_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_yaw_jerk = float(
            phase3_vehicle_dynamics_summary.get("max_abs_yaw_jerk_rps3", 0.0) or 0.0
        )
        phase3_highest_abs_yaw_jerk_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_yaw_jerk_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_lateral_position = float(
            phase3_vehicle_dynamics_summary.get("max_abs_lateral_position_m", 0.0) or 0.0
        )
        phase3_highest_abs_lateral_position_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_lateral_position_batch_id", "")).strip()
            or "n/a"
        )
        phase3_min_road_grade = float(phase3_vehicle_dynamics_summary.get("min_road_grade_percent", 0.0) or 0.0)
        phase3_avg_road_grade = float(phase3_vehicle_dynamics_summary.get("avg_road_grade_percent", 0.0) or 0.0)
        phase3_max_road_grade = float(phase3_vehicle_dynamics_summary.get("max_road_grade_percent", 0.0) or 0.0)
        phase3_lowest_road_grade_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_road_grade_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_road_grade_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_road_grade_batch_id", "")).strip() or "n/a"
        )
        phase3_max_abs_grade_force = float(
            phase3_vehicle_dynamics_summary.get("max_abs_grade_force_n", 0.0) or 0.0
        )
        phase3_highest_abs_grade_force_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_grade_force_batch_id", "")).strip() or "n/a"
        )
        phase3_control_command_manifest_count = int(
            phase3_vehicle_dynamics_summary.get("control_command_manifest_count", 0) or 0
        )
        phase3_control_command_step_count_total = int(
            phase3_vehicle_dynamics_summary.get("control_command_step_count_total", 0) or 0
        )
        phase3_control_overlap_step_count_total = int(
            phase3_vehicle_dynamics_summary.get("control_throttle_brake_overlap_step_count_total", 0) or 0
        )
        phase3_control_overlap_ratio_avg = float(
            phase3_vehicle_dynamics_summary.get("control_throttle_brake_overlap_ratio_avg", 0.0) or 0.0
        )
        phase3_control_overlap_ratio_max = float(
            phase3_vehicle_dynamics_summary.get("control_throttle_brake_overlap_ratio_max", 0.0) or 0.0
        )
        phase3_highest_control_overlap_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_control_overlap_ratio_batch_id", "")).strip() or "n/a"
        )
        phase3_control_steering_rate_avg = float(
            phase3_vehicle_dynamics_summary.get("control_max_abs_steering_rate_degps_avg", 0.0) or 0.0
        )
        phase3_control_steering_rate_max = float(
            phase3_vehicle_dynamics_summary.get("control_max_abs_steering_rate_degps_max", 0.0) or 0.0
        )
        phase3_highest_control_steering_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_control_steering_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_control_throttle_rate_avg = float(
            phase3_vehicle_dynamics_summary.get("control_max_abs_throttle_rate_per_sec_avg", 0.0) or 0.0
        )
        phase3_control_throttle_rate_max = float(
            phase3_vehicle_dynamics_summary.get("control_max_abs_throttle_rate_per_sec_max", 0.0) or 0.0
        )
        phase3_highest_control_throttle_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_control_throttle_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_control_brake_rate_avg = float(
            phase3_vehicle_dynamics_summary.get("control_max_abs_brake_rate_per_sec_avg", 0.0) or 0.0
        )
        phase3_control_brake_rate_max = float(
            phase3_vehicle_dynamics_summary.get("control_max_abs_brake_rate_per_sec_max", 0.0) or 0.0
        )
        phase3_highest_control_brake_rate_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_control_brake_rate_batch_id", "")).strip() or "n/a"
        )
        phase3_control_throttle_plus_brake_avg = float(
            phase3_vehicle_dynamics_summary.get("control_max_throttle_plus_brake_avg", 0.0) or 0.0
        )
        phase3_control_throttle_plus_brake_max = float(
            phase3_vehicle_dynamics_summary.get("control_max_throttle_plus_brake_max", 0.0) or 0.0
        )
        phase3_highest_control_throttle_plus_brake_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_control_throttle_plus_brake_batch_id", "")).strip()
            or "n/a"
        )
        phase3_speed_tracking_manifest_count = int(
            phase3_vehicle_dynamics_summary.get("speed_tracking_manifest_count", 0) or 0
        )
        phase3_speed_tracking_target_step_count_total = int(
            phase3_vehicle_dynamics_summary.get("speed_tracking_target_step_count_total", 0) or 0
        )
        phase3_min_speed_tracking_error = float(
            phase3_vehicle_dynamics_summary.get("min_speed_tracking_error_mps", 0.0) or 0.0
        )
        phase3_avg_speed_tracking_error = float(
            phase3_vehicle_dynamics_summary.get("avg_speed_tracking_error_mps", 0.0) or 0.0
        )
        phase3_max_speed_tracking_error = float(
            phase3_vehicle_dynamics_summary.get("max_speed_tracking_error_mps", 0.0) or 0.0
        )
        phase3_lowest_speed_tracking_error_batch = (
            str(phase3_vehicle_dynamics_summary.get("lowest_speed_tracking_error_batch_id", "")).strip() or "n/a"
        )
        phase3_highest_speed_tracking_error_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_speed_tracking_error_batch_id", "")).strip() or "n/a"
        )
        phase3_avg_abs_speed_tracking_error = float(
            phase3_vehicle_dynamics_summary.get("avg_abs_speed_tracking_error_mps", 0.0) or 0.0
        )
        phase3_max_abs_speed_tracking_error = float(
            phase3_vehicle_dynamics_summary.get("max_abs_speed_tracking_error_mps", 0.0) or 0.0
        )
        phase3_highest_abs_speed_tracking_error_batch = (
            str(phase3_vehicle_dynamics_summary.get("highest_abs_speed_tracking_error_batch_id", "")).strip()
            or "n/a"
        )
        lines.append(
            "phase3_vehicle_dynamics="
            f"evaluated:{phase3_evaluated_count},"
            f"planar_enabled:{phase3_planar_enabled_count},"
            f"dynamic_enabled:{phase3_dynamic_enabled_count},"
            f"models:{phase3_models_text},"
            f"speed:min={phase3_min_speed:.3f}({phase3_lowest_speed_batch}),avg={phase3_avg_speed:.3f},max={phase3_max_speed:.3f}({phase3_highest_speed_batch}),"
            f"position:min={phase3_min_position:.3f}({phase3_lowest_position_batch}),avg={phase3_avg_position:.3f},max={phase3_max_position:.3f}({phase3_highest_position_batch}),"
            f"delta_speed:min={phase3_min_delta_speed:.3f}({phase3_lowest_delta_speed_batch}),avg={phase3_avg_delta_speed:.3f},max={phase3_max_delta_speed:.3f}({phase3_highest_delta_speed_batch}),"
            f"delta_position:min={phase3_min_delta_position:.3f}({phase3_lowest_delta_position_batch}),avg={phase3_avg_delta_position:.3f},max={phase3_max_delta_position:.3f}({phase3_highest_delta_position_batch}),"
            f"heading:min={phase3_min_heading:.3f}({phase3_lowest_heading_batch}),avg={phase3_avg_heading:.3f},max={phase3_max_heading:.3f}({phase3_highest_heading_batch}),"
            f"lateral_position:min={phase3_min_lateral_position:.3f}({phase3_lowest_lateral_position_batch}),avg={phase3_avg_lateral_position:.3f},max={phase3_max_lateral_position:.3f}({phase3_highest_lateral_position_batch}),"
            f"lateral_velocity:min={phase3_min_lateral_velocity:.3f}({phase3_lowest_lateral_velocity_batch}),avg={phase3_avg_lateral_velocity:.3f},max={phase3_max_lateral_velocity:.3f}({phase3_highest_lateral_velocity_batch}),"
            f"yaw_rate_final:min={phase3_min_yaw_rate_final:.3f}({phase3_lowest_yaw_rate_batch}),avg={phase3_avg_yaw_rate_final:.3f},max={phase3_max_yaw_rate_final:.3f}({phase3_highest_yaw_rate_batch}),"
            f"delta_heading:min={phase3_min_delta_heading:.3f}({phase3_lowest_delta_heading_batch}),avg={phase3_avg_delta_heading:.3f},max={phase3_max_delta_heading:.3f}({phase3_highest_delta_heading_batch}),"
            f"delta_lateral_position:min={phase3_min_delta_lateral_position:.3f}({phase3_lowest_delta_lateral_position_batch}),avg={phase3_avg_delta_lateral_position:.3f},max={phase3_max_delta_lateral_position:.3f}({phase3_highest_delta_lateral_position_batch}),"
            f"delta_lateral_velocity:min={phase3_min_delta_lateral_velocity:.3f}({phase3_lowest_delta_lateral_velocity_batch}),avg={phase3_avg_delta_lateral_velocity:.3f},max={phase3_max_delta_lateral_velocity:.3f}({phase3_highest_delta_lateral_velocity_batch}),"
            f"delta_yaw_rate:min={phase3_min_delta_yaw_rate:.3f}({phase3_lowest_delta_yaw_rate_batch}),avg={phase3_avg_delta_yaw_rate:.3f},max={phase3_max_delta_yaw_rate:.3f}({phase3_highest_delta_yaw_rate_batch}),"
            f"yaw_rate:max_abs={phase3_max_abs_yaw_rate:.3f}({phase3_highest_abs_yaw_rate_batch}),"
            f"lateral_velocity:max_abs={phase3_max_abs_lateral_velocity:.3f}({phase3_highest_abs_lateral_velocity_batch}),"
            f"accel:max_abs={phase3_max_abs_accel:.3f}({phase3_highest_abs_accel_batch}),"
            f"lateral_accel:max_abs={phase3_max_abs_lateral_accel:.3f}({phase3_highest_abs_lateral_accel_batch}),"
            f"yaw_accel:max_abs={phase3_max_abs_yaw_accel:.3f}({phase3_highest_abs_yaw_accel_batch}),"
            f"jerk:max_abs={phase3_max_abs_jerk:.3f}({phase3_highest_abs_jerk_batch}),"
            f"lateral_jerk:max_abs={phase3_max_abs_lateral_jerk:.3f}({phase3_highest_abs_lateral_jerk_batch}),"
            f"yaw_jerk:max_abs={phase3_max_abs_yaw_jerk:.3f}({phase3_highest_abs_yaw_jerk_batch}),"
            f"lateral_abs:max={phase3_max_abs_lateral_position:.3f}({phase3_highest_abs_lateral_position_batch}),"
            f"road_grade:min={phase3_min_road_grade:.3f}({phase3_lowest_road_grade_batch}),avg={phase3_avg_road_grade:.3f},max={phase3_max_road_grade:.3f}({phase3_highest_road_grade_batch}),"
            f"grade_force:max_abs={phase3_max_abs_grade_force:.3f}({phase3_highest_abs_grade_force_batch}),"
            f"control_input:manifests={phase3_control_command_manifest_count},steps={phase3_control_command_step_count_total},"
            f"overlap_steps={phase3_control_overlap_step_count_total},"
            f"overlap_ratio_avg={phase3_control_overlap_ratio_avg:.3f},"
            f"overlap_ratio_max={phase3_control_overlap_ratio_max:.3f}({phase3_highest_control_overlap_batch}),"
            f"steering_rate_avg={phase3_control_steering_rate_avg:.3f},"
            f"steering_rate_max={phase3_control_steering_rate_max:.3f}({phase3_highest_control_steering_rate_batch}),"
            f"throttle_rate_avg={phase3_control_throttle_rate_avg:.3f},"
            f"throttle_rate_max={phase3_control_throttle_rate_max:.3f}({phase3_highest_control_throttle_rate_batch}),"
            f"brake_rate_avg={phase3_control_brake_rate_avg:.3f},"
            f"brake_rate_max={phase3_control_brake_rate_max:.3f}({phase3_highest_control_brake_rate_batch}),"
            f"throttle_plus_brake_avg={phase3_control_throttle_plus_brake_avg:.3f},"
            f"throttle_plus_brake_max={phase3_control_throttle_plus_brake_max:.3f}"
            f"({phase3_highest_control_throttle_plus_brake_batch}),"
            f"speed_tracking:manifests={phase3_speed_tracking_manifest_count},"
            f"target_steps={phase3_speed_tracking_target_step_count_total},"
            f"error_min={phase3_min_speed_tracking_error:.3f}({phase3_lowest_speed_tracking_error_batch}),"
            f"error_avg={phase3_avg_speed_tracking_error:.3f},"
            f"error_max={phase3_max_speed_tracking_error:.3f}({phase3_highest_speed_tracking_error_batch}),"
            f"error_abs_avg={phase3_avg_abs_speed_tracking_error:.3f},"
            f"error_abs_max={phase3_max_abs_speed_tracking_error:.3f}"
            f"({phase3_highest_abs_speed_tracking_error_batch})"
        )
    else:
        lines.append("phase3_vehicle_dynamics=n/a")
    phase3_core_sim_evaluated_count = int(phase3_core_sim_summary.get("evaluated_manifest_count", 0) or 0)
    if phase3_core_sim_evaluated_count > 0:
        phase3_core_sim_status_counts_raw = phase3_core_sim_summary.get("status_counts", {})
        phase3_core_sim_status_counts = (
            phase3_core_sim_status_counts_raw
            if isinstance(phase3_core_sim_status_counts_raw, dict)
            else {}
        )
        phase3_core_sim_status_counts_text = (
            ",".join(
                f"{key}:{int(phase3_core_sim_status_counts.get(key, 0) or 0)}"
                for key in sorted(phase3_core_sim_status_counts.keys())
            )
            if phase3_core_sim_status_counts
            else "n/a"
        )
        phase3_core_sim_gate_result_counts_raw = phase3_core_sim_summary.get("gate_result_counts", {})
        phase3_core_sim_gate_result_counts = (
            phase3_core_sim_gate_result_counts_raw
            if isinstance(phase3_core_sim_gate_result_counts_raw, dict)
            else {}
        )
        phase3_core_sim_gate_result_counts_text = (
            ",".join(
                f"{key}:{int(phase3_core_sim_gate_result_counts.get(key, 0) or 0)}"
                for key in sorted(phase3_core_sim_gate_result_counts.keys())
            )
            if phase3_core_sim_gate_result_counts
            else "n/a"
        )
        phase3_core_sim_min_ttc_same_lane = _to_float_or_none(
            phase3_core_sim_summary.get("min_ttc_same_lane_sec")
        )
        phase3_core_sim_min_ttc_same_lane_text = (
            f"{phase3_core_sim_min_ttc_same_lane:.3f}"
            if phase3_core_sim_min_ttc_same_lane is not None
            else "n/a"
        )
        phase3_core_sim_min_ttc_any_lane = _to_float_or_none(
            phase3_core_sim_summary.get("min_ttc_any_lane_sec")
        )
        phase3_core_sim_min_ttc_any_lane_text = (
            f"{phase3_core_sim_min_ttc_any_lane:.3f}"
            if phase3_core_sim_min_ttc_any_lane is not None
            else "n/a"
        )
        phase3_core_sim_lowest_same_lane_batch = (
            str(phase3_core_sim_summary.get("lowest_same_lane_batch_id", "")).strip() or "n/a"
        )
        phase3_core_sim_lowest_any_lane_batch = (
            str(phase3_core_sim_summary.get("lowest_any_lane_batch_id", "")).strip() or "n/a"
        )
        phase3_core_sim_highest_avoidance_brake_batch = (
            str(
                phase3_core_sim_summary.get(
                    "highest_ego_avoidance_applied_brake_batch_id",
                    "",
                )
            ).strip()
            or "n/a"
        )
        lines.append(
            "phase3_core_sim="
            f"evaluated:{phase3_core_sim_evaluated_count},"
            f"statuses:{phase3_core_sim_status_counts_text},"
            f"gate_results:{phase3_core_sim_gate_result_counts_text},"
            f"gate_reasons_total:{int(phase3_core_sim_summary.get('gate_reason_count_total', 0) or 0)},"
            f"require_success_enabled:{int(phase3_core_sim_summary.get('gate_require_success_enabled_count', 0) or 0)},"
            f"success:{int(phase3_core_sim_summary.get('success_manifest_count', 0) or 0)},"
            f"collision:{int(phase3_core_sim_summary.get('collision_manifest_count', 0) or 0)},"
            f"timeout:{int(phase3_core_sim_summary.get('timeout_manifest_count', 0) or 0)},"
            f"min_ttc_same_lane:{phase3_core_sim_min_ttc_same_lane_text}({phase3_core_sim_lowest_same_lane_batch}),"
            f"min_ttc_any_lane:{phase3_core_sim_min_ttc_any_lane_text}({phase3_core_sim_lowest_any_lane_batch}),"
            f"avoidance_enabled:{int(phase3_core_sim_summary.get('avoidance_enabled_manifest_count', 0) or 0)},"
            f"avoidance_brake_events_total:{int(phase3_core_sim_summary.get('ego_avoidance_brake_event_count_total', 0) or 0)},"
            f"avoidance_brake_applied_max:{float(phase3_core_sim_summary.get('max_ego_avoidance_applied_brake_mps2', 0.0) or 0.0):.3f}"
            f"({phase3_core_sim_highest_avoidance_brake_batch}),"
            f"tire_friction_avg:{float(phase3_core_sim_summary.get('avg_tire_friction_coeff', 0.0) or 0.0):.3f},"
            f"surface_friction_avg:{float(phase3_core_sim_summary.get('avg_surface_friction_scale', 0.0) or 0.0):.3f}"
        )
    else:
        lines.append("phase3_core_sim=n/a")
    phase3_core_sim_matrix_evaluated_count = int(
        phase3_core_sim_matrix_summary.get("evaluated_manifest_count", 0) or 0
    )
    if phase3_core_sim_matrix_evaluated_count > 0:
        phase3_core_sim_matrix_status_counts_raw = phase3_core_sim_matrix_summary.get("status_counts", {})
        phase3_core_sim_matrix_status_counts = (
            phase3_core_sim_matrix_status_counts_raw
            if isinstance(phase3_core_sim_matrix_status_counts_raw, dict)
            else {}
        )
        phase3_core_sim_matrix_status_counts_text = (
            ",".join(
                f"{key}:{int(phase3_core_sim_matrix_status_counts.get(key, 0) or 0)}"
                for key in sorted(phase3_core_sim_matrix_status_counts.keys())
            )
            if phase3_core_sim_matrix_status_counts
            else "n/a"
        )
        phase3_core_sim_matrix_returncode_counts_raw = phase3_core_sim_matrix_summary.get(
            "returncode_counts", {}
        )
        phase3_core_sim_matrix_returncode_counts = (
            phase3_core_sim_matrix_returncode_counts_raw
            if isinstance(phase3_core_sim_matrix_returncode_counts_raw, dict)
            else {}
        )
        phase3_core_sim_matrix_returncode_counts_text = (
            ",".join(
                f"{key}:{int(phase3_core_sim_matrix_returncode_counts.get(key, 0) or 0)}"
                for key in sorted(
                    phase3_core_sim_matrix_returncode_counts.keys(),
                    key=lambda raw_key: (
                        0,
                        int(str(raw_key).strip()),
                    )
                    if str(raw_key).strip().lstrip("-").isdigit()
                    else (
                        1,
                        str(raw_key).strip(),
                    ),
                )
            )
            if phase3_core_sim_matrix_returncode_counts
            else "n/a"
        )
        phase3_core_sim_matrix_min_ttc_same_lane = _to_float_or_none(
            phase3_core_sim_matrix_summary.get("min_ttc_same_lane_sec_min")
        )
        phase3_core_sim_matrix_min_ttc_same_lane_text = (
            f"{phase3_core_sim_matrix_min_ttc_same_lane:.3f}"
            if phase3_core_sim_matrix_min_ttc_same_lane is not None
            else "n/a"
        )
        phase3_core_sim_matrix_min_ttc_any_lane = _to_float_or_none(
            phase3_core_sim_matrix_summary.get("min_ttc_any_lane_sec_min")
        )
        phase3_core_sim_matrix_min_ttc_any_lane_text = (
            f"{phase3_core_sim_matrix_min_ttc_any_lane:.3f}"
            if phase3_core_sim_matrix_min_ttc_any_lane is not None
            else "n/a"
        )
        phase3_core_sim_matrix_lowest_same_lane_batch = (
            str(phase3_core_sim_matrix_summary.get("lowest_ttc_same_lane_batch_id", "")).strip() or "n/a"
        )
        phase3_core_sim_matrix_lowest_same_lane_run = (
            str(phase3_core_sim_matrix_summary.get("lowest_ttc_same_lane_run_id", "")).strip() or "n/a"
        )
        phase3_core_sim_matrix_lowest_any_lane_batch = (
            str(phase3_core_sim_matrix_summary.get("lowest_ttc_any_lane_batch_id", "")).strip() or "n/a"
        )
        phase3_core_sim_matrix_lowest_any_lane_run = (
            str(phase3_core_sim_matrix_summary.get("lowest_ttc_any_lane_run_id", "")).strip() or "n/a"
        )
        lines.append(
            "phase3_core_sim_matrix="
            f"evaluated:{phase3_core_sim_matrix_evaluated_count},"
            f"enabled_manifests:{int(phase3_core_sim_matrix_summary.get('enabled_manifest_count', 0) or 0)},"
            f"cases_total:{int(phase3_core_sim_matrix_summary.get('case_count_total', 0) or 0)},"
            f"success_cases_total:{int(phase3_core_sim_matrix_summary.get('success_case_count_total', 0) or 0)},"
            f"failed_cases_total:{int(phase3_core_sim_matrix_summary.get('failed_case_count_total', 0) or 0)},"
            f"all_cases_success_manifests:{int(phase3_core_sim_matrix_summary.get('all_cases_success_manifest_count', 0) or 0)},"
            f"collision_cases_total:{int(phase3_core_sim_matrix_summary.get('collision_case_count_total', 0) or 0)},"
            f"timeout_cases_total:{int(phase3_core_sim_matrix_summary.get('timeout_case_count_total', 0) or 0)},"
            f"statuses:{phase3_core_sim_matrix_status_counts_text},"
            f"returncodes:{phase3_core_sim_matrix_returncode_counts_text},"
            "min_ttc_same_lane:"
            f"{phase3_core_sim_matrix_min_ttc_same_lane_text}"
            f"({phase3_core_sim_matrix_lowest_same_lane_batch}|{phase3_core_sim_matrix_lowest_same_lane_run}),"
            "min_ttc_any_lane:"
            f"{phase3_core_sim_matrix_min_ttc_any_lane_text}"
            f"({phase3_core_sim_matrix_lowest_any_lane_batch}|{phase3_core_sim_matrix_lowest_any_lane_run})"
        )
    else:
        lines.append("phase3_core_sim_matrix=n/a")
    phase3_lane_risk_evaluated_count = int(phase3_lane_risk_summary.get("evaluated_manifest_count", 0) or 0)
    if phase3_lane_risk_evaluated_count > 0:
        phase3_lane_risk_run_count_total = int(phase3_lane_risk_summary.get("lane_risk_summary_run_count_total", 0) or 0)
        phase3_lane_risk_gate_result_counts_raw = phase3_lane_risk_summary.get("gate_result_counts", {})
        phase3_lane_risk_gate_result_counts = (
            phase3_lane_risk_gate_result_counts_raw
            if isinstance(phase3_lane_risk_gate_result_counts_raw, dict)
            else {}
        )
        phase3_lane_risk_gate_result_counts_text = (
            ",".join(
                f"{key}:{int(phase3_lane_risk_gate_result_counts.get(key, 0) or 0)}"
                for key in sorted(phase3_lane_risk_gate_result_counts.keys())
            )
            if phase3_lane_risk_gate_result_counts
            else "n/a"
        )
        phase3_lane_risk_min_ttc_same_lane = _to_float_or_none(
            phase3_lane_risk_summary.get("min_ttc_same_lane_sec")
        )
        phase3_lane_risk_min_ttc_adjacent_lane = _to_float_or_none(
            phase3_lane_risk_summary.get("min_ttc_adjacent_lane_sec")
        )
        phase3_lane_risk_min_ttc_any_lane = _to_float_or_none(
            phase3_lane_risk_summary.get("min_ttc_any_lane_sec")
        )
        phase3_lane_risk_lowest_same_lane_batch = (
            str(phase3_lane_risk_summary.get("lowest_same_lane_batch_id", "")).strip() or "n/a"
        )
        phase3_lane_risk_lowest_adjacent_lane_batch = (
            str(phase3_lane_risk_summary.get("lowest_adjacent_lane_batch_id", "")).strip() or "n/a"
        )
        phase3_lane_risk_lowest_any_lane_batch = (
            str(phase3_lane_risk_summary.get("lowest_any_lane_batch_id", "")).strip() or "n/a"
        )
        phase3_lane_risk_ttc_under_3s_same_lane_total = int(
            phase3_lane_risk_summary.get("ttc_under_3s_same_lane_total", 0) or 0
        )
        phase3_lane_risk_ttc_under_3s_adjacent_lane_total = int(
            phase3_lane_risk_summary.get("ttc_under_3s_adjacent_lane_total", 0) or 0
        )
        phase3_lane_risk_same_lane_rows_total = int(
            phase3_lane_risk_summary.get("same_lane_rows_total", 0) or 0
        )
        phase3_lane_risk_adjacent_lane_rows_total = int(
            phase3_lane_risk_summary.get("adjacent_lane_rows_total", 0) or 0
        )
        phase3_lane_risk_other_lane_rows_total = int(
            phase3_lane_risk_summary.get("other_lane_rows_total", 0) or 0
        )
        phase3_lane_risk_min_ttc_same_lane_text = (
            f"{phase3_lane_risk_min_ttc_same_lane:.3f}"
            if phase3_lane_risk_min_ttc_same_lane is not None
            else "n/a"
        )
        phase3_lane_risk_min_ttc_adjacent_lane_text = (
            f"{phase3_lane_risk_min_ttc_adjacent_lane:.3f}"
            if phase3_lane_risk_min_ttc_adjacent_lane is not None
            else "n/a"
        )
        phase3_lane_risk_min_ttc_any_lane_text = (
            f"{phase3_lane_risk_min_ttc_any_lane:.3f}"
            if phase3_lane_risk_min_ttc_any_lane is not None
            else "n/a"
        )
        lines.append(
            "phase3_lane_risk="
            f"evaluated:{phase3_lane_risk_evaluated_count},"
            f"runs:{phase3_lane_risk_run_count_total},"
            f"gate_results:{phase3_lane_risk_gate_result_counts_text},"
            f"gate_reasons_total:{int(phase3_lane_risk_summary.get('gate_reason_count_total', 0) or 0)},"
            f"min_ttc_same_lane:{phase3_lane_risk_min_ttc_same_lane_text}({phase3_lane_risk_lowest_same_lane_batch}),"
            f"min_ttc_adjacent_lane:{phase3_lane_risk_min_ttc_adjacent_lane_text}({phase3_lane_risk_lowest_adjacent_lane_batch}),"
            f"min_ttc_any_lane:{phase3_lane_risk_min_ttc_any_lane_text}({phase3_lane_risk_lowest_any_lane_batch}),"
            f"ttc_under_3s_same_lane_total:{phase3_lane_risk_ttc_under_3s_same_lane_total},"
            f"ttc_under_3s_adjacent_lane_total:{phase3_lane_risk_ttc_under_3s_adjacent_lane_total},"
            f"rows:same={phase3_lane_risk_same_lane_rows_total},"
            f"adjacent={phase3_lane_risk_adjacent_lane_rows_total},"
            f"other={phase3_lane_risk_other_lane_rows_total}"
        )
    else:
        lines.append("phase3_lane_risk=n/a")
    phase3_dataset_traffic_evaluated_count = int(
        phase3_dataset_traffic_summary.get("evaluated_manifest_count", 0) or 0
    )
    if phase3_dataset_traffic_evaluated_count > 0:
        phase3_dataset_gate_results_raw = phase3_dataset_traffic_summary.get("gate_result_counts", {})
        phase3_dataset_gate_results = (
            phase3_dataset_gate_results_raw if isinstance(phase3_dataset_gate_results_raw, dict) else {}
        )
        phase3_dataset_gate_results_text = (
            ",".join(
                f"{key}:{int(phase3_dataset_gate_results.get(key, 0) or 0)}"
                for key in sorted(phase3_dataset_gate_results.keys())
            )
            if phase3_dataset_gate_results
            else "n/a"
        )
        phase3_dataset_run_status_counts_raw = phase3_dataset_traffic_summary.get("run_status_counts", {})
        phase3_dataset_run_status_counts = (
            phase3_dataset_run_status_counts_raw if isinstance(phase3_dataset_run_status_counts_raw, dict) else {}
        )
        phase3_dataset_run_status_counts_text = (
            ",".join(
                f"{key}:{int(phase3_dataset_run_status_counts.get(key, 0) or 0)}"
                for key in sorted(phase3_dataset_run_status_counts.keys())
            )
            if phase3_dataset_run_status_counts
            else "n/a"
        )
        phase3_dataset_profile_ids = _normalize_text_list(
            phase3_dataset_traffic_summary.get("traffic_profile_ids")
        )
        phase3_dataset_actor_pattern_ids = _normalize_text_list(
            phase3_dataset_traffic_summary.get("traffic_actor_pattern_ids")
        )
        phase3_dataset_profile_source_ids = _normalize_text_list(
            phase3_dataset_traffic_summary.get("traffic_profile_source_ids")
        )
        phase3_dataset_lane_profile_signatures = _normalize_text_list(
            phase3_dataset_traffic_summary.get("traffic_lane_profile_signatures")
        )
        phase3_dataset_manifest_versions = _normalize_text_list(
            phase3_dataset_traffic_summary.get("dataset_manifest_versions")
        )
        phase3_dataset_lane_indices_raw = phase3_dataset_traffic_summary.get("traffic_lane_indices")
        phase3_dataset_lane_indices: list[int] = []
        if isinstance(phase3_dataset_lane_indices_raw, list):
            for lane_index_raw in phase3_dataset_lane_indices_raw:
                try:
                    lane_index = int(lane_index_raw)
                except (TypeError, ValueError):
                    continue
                phase3_dataset_lane_indices.append(lane_index)
        phase3_dataset_lane_indices = sorted(set(phase3_dataset_lane_indices))
        lines.append(
            "phase3_dataset_traffic="
            f"evaluated:{phase3_dataset_traffic_evaluated_count},"
            f"gate_results:{phase3_dataset_gate_results_text},"
            f"gate_reasons_total:{int(phase3_dataset_traffic_summary.get('gate_reason_count_total', 0) or 0)},"
            f"runs_total:{int(phase3_dataset_traffic_summary.get('run_summary_count_total', 0) or 0)},"
            f"run_statuses:{phase3_dataset_run_status_counts_text},"
            f"profiles:unique={int(phase3_dataset_traffic_summary.get('traffic_profile_unique_count', 0) or 0)},"
            f"avg={float(phase3_dataset_traffic_summary.get('traffic_profile_count_avg', 0.0) or 0.0):.3f},"
            f"max={int(phase3_dataset_traffic_summary.get('max_traffic_profile_count', 0) or 0)}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_profile_batch_id', '')).strip() or 'n/a'}),"
            f"ids={','.join(phase3_dataset_profile_ids) if phase3_dataset_profile_ids else 'n/a'},"
            f"profile_sources:unique={int(phase3_dataset_traffic_summary.get('traffic_profile_source_unique_count', 0) or 0)},"
            f"avg={float(phase3_dataset_traffic_summary.get('traffic_profile_source_count_avg', 0.0) or 0.0):.3f},"
            f"max={int(phase3_dataset_traffic_summary.get('max_traffic_profile_source_count', 0) or 0)}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_profile_source_batch_id', '')).strip() or 'n/a'}),"
            f"ids={','.join(phase3_dataset_profile_source_ids) if phase3_dataset_profile_source_ids else 'n/a'},"
            f"actor_patterns:unique={int(phase3_dataset_traffic_summary.get('traffic_actor_pattern_unique_count', 0) or 0)},"
            f"avg={float(phase3_dataset_traffic_summary.get('traffic_actor_pattern_count_avg', 0.0) or 0.0):.3f},"
            f"max={int(phase3_dataset_traffic_summary.get('max_traffic_actor_pattern_count', 0) or 0)}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_actor_pattern_batch_id', '')).strip() or 'n/a'}),"
            f"ids={','.join(phase3_dataset_actor_pattern_ids) if phase3_dataset_actor_pattern_ids else 'n/a'},"
            f"lane_profiles:unique={int(phase3_dataset_traffic_summary.get('traffic_lane_profile_signature_unique_count', 0) or 0)},"
            f"avg={float(phase3_dataset_traffic_summary.get('traffic_lane_profile_signature_count_avg', 0.0) or 0.0):.3f},"
            f"max={int(phase3_dataset_traffic_summary.get('max_traffic_lane_profile_signature_count', 0) or 0)}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_lane_profile_signature_batch_id', '')).strip() or 'n/a'}),"
            f"patterns={','.join(phase3_dataset_lane_profile_signatures) if phase3_dataset_lane_profile_signatures else 'n/a'},"
            f"npc_avg:avg={float(phase3_dataset_traffic_summary.get('traffic_npc_count_avg_avg', 0.0) or 0.0):.3f},"
            f"max={float(phase3_dataset_traffic_summary.get('traffic_npc_count_avg_max', 0.0) or 0.0):.3f}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_npc_avg_batch_id', '')).strip() or 'n/a'}),"
            f"npc_max={int(phase3_dataset_traffic_summary.get('traffic_npc_count_max_max', 0) or 0)}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_npc_max_batch_id', '')).strip() or 'n/a'}),"
            f"npc_initial_gap_avg={float(phase3_dataset_traffic_summary.get('traffic_npc_initial_gap_m_avg_avg', 0.0) or 0.0):.3f}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_npc_initial_gap_m_avg_batch_id', '')).strip() or 'n/a'}),"
            f"npc_gap_step_avg={float(phase3_dataset_traffic_summary.get('traffic_npc_gap_step_m_avg_avg', 0.0) or 0.0):.3f}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_npc_gap_step_m_avg_batch_id', '')).strip() or 'n/a'}),"
            f"npc_speed_scale_avg={float(phase3_dataset_traffic_summary.get('traffic_npc_speed_scale_avg_avg', 0.0) or 0.0):.3f}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_npc_speed_scale_avg_batch_id', '')).strip() or 'n/a'}),"
            f"npc_speed_jitter_avg={float(phase3_dataset_traffic_summary.get('traffic_npc_speed_jitter_mps_avg_avg', 0.0) or 0.0):.3f}"
            f"({str(phase3_dataset_traffic_summary.get('highest_traffic_npc_speed_jitter_mps_avg_batch_id', '')).strip() or 'n/a'}),"
            f"lane_indices:unique={int(phase3_dataset_traffic_summary.get('traffic_lane_indices_unique_count', 0) or 0)},"
            f"avg_unique_per_manifest={float(phase3_dataset_traffic_summary.get('traffic_lane_index_unique_count_avg', 0.0) or 0.0):.3f},"
            f"indices={','.join(str(item) for item in phase3_dataset_lane_indices) if phase3_dataset_lane_indices else 'n/a'},"
            "dataset_manifest:"
            f"counts_rows_total={int(phase3_dataset_traffic_summary.get('dataset_manifest_counts_rows_total', 0) or 0)},"
            f"run_summaries_total={int(phase3_dataset_traffic_summary.get('dataset_manifest_run_summary_count_total', 0) or 0)},"
            f"release_summaries_total={int(phase3_dataset_traffic_summary.get('dataset_manifest_release_summary_count_total', 0) or 0)},"
            f"versions={','.join(phase3_dataset_manifest_versions) if phase3_dataset_manifest_versions else 'n/a'}"
        )
    else:
        lines.append("phase3_dataset_traffic=n/a")
    primary_evaluated_count = int(phase4_primary_coverage_summary.get("evaluated_manifest_count", 0) or 0)
    if primary_evaluated_count > 0:
        primary_min = float(phase4_primary_coverage_summary.get("min_coverage_ratio", 0.0) or 0.0)
        primary_avg = float(phase4_primary_coverage_summary.get("avg_coverage_ratio", 0.0) or 0.0)
        primary_max = float(phase4_primary_coverage_summary.get("max_coverage_ratio", 0.0) or 0.0)
        primary_lowest_batch = str(phase4_primary_coverage_summary.get("lowest_batch_id", "")).strip() or "n/a"
        primary_highest_batch = str(phase4_primary_coverage_summary.get("highest_batch_id", "")).strip() or "n/a"
        lines.append(
            "phase4_primary_coverage="
            f"evaluated:{primary_evaluated_count},"
            f"min:{primary_min:.3f}({primary_lowest_batch}),"
            f"avg:{primary_avg:.3f},"
            f"max:{primary_max:.3f}({primary_highest_batch})"
        )
    else:
        lines.append("phase4_primary_coverage=n/a")
    primary_module_summary_raw = phase4_primary_coverage_summary.get("module_coverage_summary", {})
    if isinstance(primary_module_summary_raw, dict) and primary_module_summary_raw:
        primary_module_parts: list[str] = []
        for module_name in sorted(primary_module_summary_raw.keys()):
            item = primary_module_summary_raw.get(module_name, {})
            if not isinstance(item, dict):
                continue
            try:
                module_min = float(item.get("min_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                module_min = 0.0
            try:
                module_avg = float(item.get("avg_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                module_avg = 0.0
            try:
                module_max = float(item.get("max_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                module_max = 0.0
            lowest_batch = str(item.get("lowest_batch_id", "")).strip() or "n/a"
            highest_batch = str(item.get("highest_batch_id", "")).strip() or "n/a"
            primary_module_parts.append(
                f"{module_name}:min={module_min:.3f}({lowest_batch}),avg={module_avg:.3f},max={module_max:.3f}({highest_batch})"
            )
        lines.append(
            "phase4_primary_module_coverage="
            + (";".join(primary_module_parts) if primary_module_parts else "n/a")
        )
    else:
        lines.append("phase4_primary_module_coverage=n/a")
    secondary_evaluated_count = int(phase4_secondary_coverage_summary.get("evaluated_manifest_count", 0) or 0)
    if secondary_evaluated_count > 0:
        secondary_min = float(phase4_secondary_coverage_summary.get("min_coverage_ratio", 0.0) or 0.0)
        secondary_avg = float(phase4_secondary_coverage_summary.get("avg_coverage_ratio", 0.0) or 0.0)
        secondary_max = float(phase4_secondary_coverage_summary.get("max_coverage_ratio", 0.0) or 0.0)
        secondary_lowest_batch = str(phase4_secondary_coverage_summary.get("lowest_batch_id", "")).strip() or "n/a"
        secondary_highest_batch = str(phase4_secondary_coverage_summary.get("highest_batch_id", "")).strip() or "n/a"
        lines.append(
            "phase4_secondary_coverage="
            f"evaluated:{secondary_evaluated_count},"
            f"min:{secondary_min:.3f}({secondary_lowest_batch}),"
            f"avg:{secondary_avg:.3f},"
            f"max:{secondary_max:.3f}({secondary_highest_batch})"
        )
    else:
        lines.append("phase4_secondary_coverage=n/a")
    module_summary_raw = phase4_secondary_coverage_summary.get("module_coverage_summary", {})
    if isinstance(module_summary_raw, dict) and module_summary_raw:
        module_parts: list[str] = []
        for module_name in sorted(module_summary_raw.keys()):
            item = module_summary_raw.get(module_name, {})
            if not isinstance(item, dict):
                continue
            try:
                module_min = float(item.get("min_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                module_min = 0.0
            try:
                module_avg = float(item.get("avg_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                module_avg = 0.0
            try:
                module_max = float(item.get("max_coverage_ratio", 0.0))
            except (TypeError, ValueError):
                module_max = 0.0
            lowest_batch = str(item.get("lowest_batch_id", "")).strip() or "n/a"
            highest_batch = str(item.get("highest_batch_id", "")).strip() or "n/a"
            module_parts.append(
                f"{module_name}:min={module_min:.3f}({lowest_batch}),avg={module_avg:.3f},max={module_max:.3f}({highest_batch})"
            )
        lines.append("phase4_secondary_module_coverage=" + (";".join(module_parts) if module_parts else "n/a"))
    else:
        lines.append("phase4_secondary_module_coverage=n/a")
    lines.append(f"timing_ms={_fmt_timing_ms(timing_ms)}")
    lines.append(f"tmp_db={out_db}")
    lines.append("")
    lines.append("## pipeline-manifests")
    if pipeline_manifests:
        lines.append("| batch_id | overall_result | trend_result | strict_gate | sds_versions | manifest_path |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in pipeline_manifests:
            lines.append(
                f"| {item['batch_id']} | {item['overall_result']} | {item['trend_result']} | "
                f"{item['strict_gate']} | {','.join(item['sds_versions'])} | {item['manifest_path']} |"
            )
    else:
        lines.append(f"[info] no matching pipeline_result.json found under {pipeline_manifests_root}")
    lines.append("")
    lines.append("## release-latest")
    lines.append(latest_output)
    lines.append("")
    lines.append("## hold-reason-codes")
    lines.append(hold_reason_code_output)
    lines.append("")
    lines.append("## hold-reasons-raw")
    lines.append(hold_reason_raw_output)
    lines.append("")
    lines.append("## root-cause-summary")
    append_ranked_table(
        lines,
        title="hold_reason_codes",
        rows=list(root_cause_summary.get("hold_reason_codes", [])),
    )
    append_ranked_table(
        lines,
        title="hold_reasons_raw",
        rows=list(root_cause_summary.get("hold_reasons_raw", [])),
    )
    append_ranked_table(
        lines,
        title="gate_reasons",
        rows=list(root_cause_summary.get("gate_reasons", [])),
    )
    append_ranked_table(
        lines,
        title="requirement_hold_ids",
        rows=list(root_cause_summary.get("requirement_hold_ids", [])),
    )
    lines.append("## release-diff")
    if diff_output:
        lines.append(diff_output)
    elif version_a and version_b:
        lines.append(
            "[info] skipped: no release assessment rows for release-diff "
            f"(release_prefix={args.release_prefix}, version_a={version_a}, version_b={version_b})"
        )
    else:
        lines.append("[info] skipped: not enough versions for release-diff")
    lines.append("")
    lines.append("## hold-reason-code-diff")
    if reason_code_diff:
        lines.append(
            f"version_a={reason_code_diff['version_a']} found={reason_code_diff['found_version_a']}, "
            f"version_b={reason_code_diff['version_b']} found={reason_code_diff['found_version_b']}"
        )
        lines.append(f"codes_only_in_a={','.join(reason_code_diff['codes_only_in_a'])}")
        lines.append(f"codes_only_in_b={','.join(reason_code_diff['codes_only_in_b'])}")
        lines.append(f"codes_common={','.join(reason_code_diff['codes_common'])}")
    else:
        lines.append("[info] skipped: not enough versions for hold-reason-code-diff")

    out_text.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    payload: dict[str, Any] = {
        "release_prefix": args.release_prefix,
        "summary_count": len(summary_files),
        "summary_files_root": str(summary_files_root),
        "summary_files_subpath": summary_files_subpath,
        "summary_scan_roots": [str(path) for path in summary_scan_roots],
        "summary_files": [str(path) for path in summary_files],
        "sds_versions": versions,
        "final_result_counts": final_result_counts,
        "root_cause_summary": root_cause_summary,
        "pipeline_manifest_count": len(pipeline_manifests),
        "pipeline_manifests_root": str(pipeline_manifests_root),
        "pipeline_manifests_subpath": pipeline_manifests_subpath,
        "pipeline_manifest_scan_roots": [str(path) for path in pipeline_manifest_scan_roots],
        "runtime_evidence_scan_roots": [str(path) for path in runtime_evidence_scan_roots],
        "pipeline_overall_counts": overall_counts,
        "pipeline_trend_counts": trend_counts,
        "pipeline_manifests": pipeline_manifests,
        "runtime_evidence_artifact_count": runtime_evidence_artifact_count,
        "runtime_evidence_artifacts": runtime_evidence_artifacts,
        "runtime_evidence_summary": runtime_evidence_summary,
        "runtime_lane_execution_artifact_count": runtime_lane_execution_artifact_count,
        "runtime_lane_execution_artifacts": runtime_lane_execution_artifacts,
        "runtime_lane_execution_summary": runtime_lane_execution_summary,
        "runtime_evidence_compare_artifact_count": runtime_evidence_compare_artifact_count,
        "runtime_evidence_compare_artifacts": runtime_evidence_compare_artifacts,
        "runtime_evidence_compare_summary": runtime_evidence_compare_summary,
        "runtime_native_evidence_compare_artifact_count": runtime_native_evidence_compare_artifact_count,
        "runtime_native_evidence_compare_artifacts": runtime_native_evidence_compare_artifacts,
        "runtime_native_evidence_compare_summary": runtime_native_evidence_compare_summary,
        "runtime_native_summary_compare_artifact_count": runtime_native_summary_compare_artifact_count,
        "runtime_native_summary_compare_artifacts": runtime_native_summary_compare_artifacts,
        "runtime_native_summary_compare_summary": runtime_native_summary_compare_summary,
        "phase2_log_replay_summary": phase2_log_replay_summary,
        "phase2_map_routing_summary": phase2_map_routing_summary,
        "phase2_sensor_fidelity_summary": phase2_sensor_fidelity_summary,
        "runtime_native_smoke_summary": runtime_native_smoke_summary,
        "phase3_vehicle_dynamics_summary": phase3_vehicle_dynamics_summary,
        "phase3_core_sim_summary": phase3_core_sim_summary,
        "phase3_core_sim_matrix_summary": phase3_core_sim_matrix_summary,
        "phase3_lane_risk_summary": phase3_lane_risk_summary,
        "phase3_dataset_traffic_summary": phase3_dataset_traffic_summary,
        "phase4_primary_coverage_summary": phase4_primary_coverage_summary,
        "phase4_secondary_coverage_summary": phase4_secondary_coverage_summary,
        "timing_ms": timing_ms,
        "tmp_db": str(out_db),
        "query_outputs": {
            "release_latest": latest_output,
            "hold_reason_codes": hold_reason_code_output,
            "hold_reasons_raw": hold_reason_raw_output,
            "release_diff": diff_output,
        },
        "query_params": {
            "latest_limit": latest_limit,
            "hold_reason_limit": hold_reason_limit,
            "version_a": version_a,
            "version_b": version_b,
        },
        "reason_code_diff": reason_code_diff,
    }
    if out_json is not None:
        out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] out_json={out_json}")

    print(f"[ok] out_text={out_text}")
    print(f"[ok] out_db={out_db}")
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="build_release_summary_artifact.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=SUMMARY_PHASE_BUILD_SUMMARY,
        )
    )
