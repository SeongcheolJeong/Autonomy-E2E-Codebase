#!/usr/bin/env python3
"""One-command orchestration: Cloud batch -> Data ingest -> Validation reports."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import sys
from pathlib import Path
from typing import Any

from ci_input_parsing import (
    parse_float,
    parse_int,
    parse_non_negative_float,
    parse_positive_int,
    resolve_phase4_copilot_mode,
)
from ci_phases import PIPELINE_PHASE_RUN_PIPELINE
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_subprocess import run_logged_capture_stdout_or_raise
from ci_sync_utils import resolve_repo_root, utc_now_iso
from phase4_linkage_contract import (
    PHASE4_LINKAGE_ALLOWED_MODULES_CSV,
    PHASE4_LINKAGE_ALLOWED_MODULES_TEXT,
    PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT,
    resolve_phase4_linkage_modules as resolve_phase4_linkage_modules_contract,
    resolve_phase4_reference_pattern_modules as resolve_phase4_reference_pattern_modules_contract,
)

def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")
        return payload

    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ValueError("YAML batch spec requires PyYAML; use JSON or install PyYAML") from exc
        payload = yaml.safe_load(text)
        if not isinstance(payload, dict):
            raise ValueError("YAML payload must be an object")
        return payload

    raise ValueError(f"unsupported extension: {path.suffix}")


def resolve_ref(base_dir: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def run_cmd(cmd: list[str]) -> str:
    return run_logged_capture_stdout_or_raise(cmd, context="command")


def extract_result_path(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("[ok] result="):
            return Path(line.split("=", 1)[1].strip())
    return None


def discover_sds_versions(batch_root: Path) -> list[str]:
    versions: list[str] = []
    seen: set[str] = set()
    for summary_path in sorted(batch_root.glob("*/summary.json")):
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        version = str(payload.get("sds_version", "")).strip()
        if version and version not in seen:
            seen.add(version)
            versions.append(version)
    return versions


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value)


def resolve_phase4_linkage_modules(values: list[str]) -> list[str]:
    return resolve_phase4_linkage_modules_contract(values, default_to_allowed_when_empty=True)


def resolve_phase4_reference_pattern_modules(values: list[str]) -> list[str]:
    return resolve_phase4_reference_pattern_modules_contract(values, default_to_allowed_when_empty=False)


def evaluate_trend_gate(
    *,
    db_path: Path,
    sds_versions: list[str],
    window: int,
    min_pass_rate: float,
    min_samples: int,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    if window <= 0:
        return "N/A", [], []

    reasons: list[str] = []
    details: list[dict[str, Any]] = []

    conn = sqlite3.connect(db_path)
    try:
        for version in sds_versions:
            rows = conn.execute(
                """
                SELECT final_result
                FROM release_assessment
                WHERE sds_version = ?
                  AND generated_at IS NOT NULL
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                (version, window),
            ).fetchall()

            sample_count = len(rows)
            pass_count = sum(1 for row in rows if str(row[0]) == "PASS")
            hold_count = sum(1 for row in rows if str(row[0]) == "HOLD")
            pass_rate = (pass_count / sample_count) if sample_count > 0 else 0.0

            details.append(
                {
                    "sds_version": version,
                    "window": window,
                    "sample_count": sample_count,
                    "pass_count": pass_count,
                    "hold_count": hold_count,
                    "pass_rate": round(pass_rate, 6),
                }
            )

            if sample_count < min_samples:
                reasons.append(
                    f"{version}: sample_count {sample_count} < min_samples {min_samples}"
                )
                continue

            if pass_rate < min_pass_rate:
                reasons.append(
                    f"{version}: pass_rate {pass_rate:.4f} < min_pass_rate {min_pass_rate:.4f}"
                )
    finally:
        conn.close()

    if reasons:
        return "HOLD", reasons, details
    return "PASS", ["trend checks satisfied"], details


def evaluate_phase2_route_quality_gate(
    *,
    phase2_enable_hooks: bool,
    phase2_hooks: dict[str, Any],
    require_status_pass: bool,
    require_routing_semantic_pass: bool,
    min_lane_count: int,
    min_total_length_m: float,
    max_routing_semantic_warning_count: int,
    max_unreachable_lane_count: int,
    max_non_reciprocal_link_warning_count: int,
    max_continuity_gap_warning_count: int,
) -> tuple[str, list[str], dict[str, Any]]:
    configured = (
        bool(require_status_pass)
        or bool(require_routing_semantic_pass)
        or int(min_lane_count) > 0
        or float(min_total_length_m) > 0.0
        or int(max_routing_semantic_warning_count) > 0
        or int(max_unreachable_lane_count) > 0
        or int(max_non_reciprocal_link_warning_count) > 0
        or int(max_continuity_gap_warning_count) > 0
    )
    observed_status = str(phase2_hooks.get("map_route_status", "")).strip().lower()
    try:
        observed_lane_count = int(phase2_hooks.get("map_route_lane_count", 0) or 0)
    except (TypeError, ValueError):
        observed_lane_count = 0
    try:
        observed_total_length_m = float(phase2_hooks.get("map_route_total_length_m", 0.0) or 0.0)
    except (TypeError, ValueError):
        observed_total_length_m = 0.0
    observed_routing_semantic_status = str(
        phase2_hooks.get("map_validate_routing_semantic_status", "")
    ).strip().lower()
    try:
        observed_routing_semantic_warning_count = int(
            phase2_hooks.get("map_validate_routing_semantic_warning_count", 0) or 0
        )
    except (TypeError, ValueError):
        observed_routing_semantic_warning_count = 0
    try:
        observed_unreachable_lane_count = int(phase2_hooks.get("map_validate_unreachable_lane_count", 0) or 0)
    except (TypeError, ValueError):
        observed_unreachable_lane_count = 0
    try:
        observed_non_reciprocal_link_warning_count = int(
            phase2_hooks.get("map_validate_non_reciprocal_link_warning_count", 0) or 0
        )
    except (TypeError, ValueError):
        observed_non_reciprocal_link_warning_count = 0
    try:
        observed_continuity_gap_warning_count = int(
            phase2_hooks.get("map_validate_continuity_gap_warning_count", 0) or 0
        )
    except (TypeError, ValueError):
        observed_continuity_gap_warning_count = 0

    details = {
        "enabled": bool(phase2_enable_hooks),
        "configured": bool(configured),
        "require_status_pass": bool(require_status_pass),
        "require_routing_semantic_pass": bool(require_routing_semantic_pass),
        "min_lane_count": int(min_lane_count),
        "min_total_length_m": float(min_total_length_m),
        "max_routing_semantic_warning_count": int(max_routing_semantic_warning_count),
        "max_unreachable_lane_count": int(max_unreachable_lane_count),
        "max_non_reciprocal_link_warning_count": int(max_non_reciprocal_link_warning_count),
        "max_continuity_gap_warning_count": int(max_continuity_gap_warning_count),
        "observed_status": observed_status,
        "observed_lane_count": int(observed_lane_count),
        "observed_total_length_m": float(observed_total_length_m),
        "observed_routing_semantic_status": observed_routing_semantic_status,
        "observed_routing_semantic_warning_count": int(observed_routing_semantic_warning_count),
        "observed_unreachable_lane_count": int(observed_unreachable_lane_count),
        "observed_non_reciprocal_link_warning_count": int(observed_non_reciprocal_link_warning_count),
        "observed_continuity_gap_warning_count": int(observed_continuity_gap_warning_count),
    }

    if not phase2_enable_hooks:
        return "N/A", [], details
    if not configured:
        return "N/A", [], details

    reasons: list[str] = []
    if require_status_pass and observed_status != "pass":
        reasons.append(f"phase2_map_route_status {observed_status or 'missing'} != pass")
    if require_routing_semantic_pass and observed_routing_semantic_status != "pass":
        reasons.append(
            "phase2_map_validate_routing_semantic_status "
            f"{observed_routing_semantic_status or 'missing'} != pass"
        )
    if min_lane_count > 0 and observed_lane_count < min_lane_count:
        reasons.append(f"phase2_map_route_lane_count {observed_lane_count} < min_lane_count {min_lane_count}")
    if min_total_length_m > 0.0 and observed_total_length_m < min_total_length_m:
        reasons.append(
            "phase2_map_route_total_length_m "
            f"{observed_total_length_m:.3f} < min_total_length_m {min_total_length_m:.3f}"
        )
    if (
        max_routing_semantic_warning_count > 0
        and observed_routing_semantic_warning_count > max_routing_semantic_warning_count
    ):
        reasons.append(
            "phase2_map_validate_routing_semantic_warning_count "
            f"{observed_routing_semantic_warning_count} > "
            f"max_routing_semantic_warning_count {max_routing_semantic_warning_count}"
        )
    if max_unreachable_lane_count > 0 and observed_unreachable_lane_count > max_unreachable_lane_count:
        reasons.append(
            "phase2_map_validate_unreachable_lane_count "
            f"{observed_unreachable_lane_count} > max_unreachable_lane_count {max_unreachable_lane_count}"
        )
    if (
        max_non_reciprocal_link_warning_count > 0
        and observed_non_reciprocal_link_warning_count > max_non_reciprocal_link_warning_count
    ):
        reasons.append(
            "phase2_map_validate_non_reciprocal_link_warning_count "
            f"{observed_non_reciprocal_link_warning_count} > "
            f"max_non_reciprocal_link_warning_count {max_non_reciprocal_link_warning_count}"
        )
    if (
        max_continuity_gap_warning_count > 0
        and observed_continuity_gap_warning_count > max_continuity_gap_warning_count
    ):
        reasons.append(
            "phase2_map_validate_continuity_gap_warning_count "
            f"{observed_continuity_gap_warning_count} > "
            f"max_continuity_gap_warning_count {max_continuity_gap_warning_count}"
        )
    if reasons:
        return "HOLD", reasons, details
    return "PASS", ["phase2 map-route quality checks satisfied"], details


def evaluate_phase3_control_quality_gate(
    *,
    phase3_enable_hooks: bool,
    phase3_hooks: dict[str, Any],
    max_overlap_ratio: float,
    max_steering_rate_degps: float,
    max_throttle_plus_brake: float,
    max_speed_tracking_error_abs_mps: float,
) -> tuple[str, list[str], dict[str, Any]]:
    configured = (
        float(max_overlap_ratio) > 0.0
        or float(max_steering_rate_degps) > 0.0
        or float(max_throttle_plus_brake) > 0.0
        or float(max_speed_tracking_error_abs_mps) > 0.0
    )
    vehicle_dynamics_raw = phase3_hooks.get("vehicle_dynamics", {})
    vehicle_dynamics = vehicle_dynamics_raw if isinstance(vehicle_dynamics_raw, dict) else {}

    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    observed_overlap_ratio = _as_float(vehicle_dynamics.get("control_throttle_brake_overlap_ratio", 0.0))
    observed_steering_rate = _as_float(vehicle_dynamics.get("control_max_abs_steering_rate_degps", 0.0))
    observed_throttle_plus_brake = _as_float(vehicle_dynamics.get("control_max_throttle_plus_brake", 0.0))
    observed_speed_tracking_abs = _as_float(vehicle_dynamics.get("speed_tracking_error_abs_mps_max", 0.0))

    details = {
        "enabled": bool(phase3_enable_hooks),
        "configured": bool(configured),
        "max_overlap_ratio": float(max_overlap_ratio),
        "max_steering_rate_degps": float(max_steering_rate_degps),
        "max_throttle_plus_brake": float(max_throttle_plus_brake),
        "max_speed_tracking_error_abs_mps": float(max_speed_tracking_error_abs_mps),
        "observed_overlap_ratio": float(observed_overlap_ratio),
        "observed_steering_rate_degps": float(observed_steering_rate),
        "observed_throttle_plus_brake": float(observed_throttle_plus_brake),
        "observed_speed_tracking_error_abs_mps": float(observed_speed_tracking_abs),
    }

    if not phase3_enable_hooks:
        return "N/A", [], details
    if not configured:
        return "N/A", [], details

    reasons: list[str] = []
    if max_overlap_ratio > 0.0 and observed_overlap_ratio > max_overlap_ratio:
        reasons.append(
            "phase3_control_overlap_ratio "
            f"{observed_overlap_ratio:.6f} > max_overlap_ratio {max_overlap_ratio:.6f}"
        )
    if max_steering_rate_degps > 0.0 and observed_steering_rate > max_steering_rate_degps:
        reasons.append(
            "phase3_control_max_abs_steering_rate_degps "
            f"{observed_steering_rate:.6f} > max_steering_rate_degps {max_steering_rate_degps:.6f}"
        )
    if max_throttle_plus_brake > 0.0 and observed_throttle_plus_brake > max_throttle_plus_brake:
        reasons.append(
            "phase3_control_max_throttle_plus_brake "
            f"{observed_throttle_plus_brake:.6f} > max_throttle_plus_brake {max_throttle_plus_brake:.6f}"
        )
    if max_speed_tracking_error_abs_mps > 0.0 and observed_speed_tracking_abs > max_speed_tracking_error_abs_mps:
        reasons.append(
            "phase3_speed_tracking_error_abs_mps_max "
            f"{observed_speed_tracking_abs:.6f} > max_speed_tracking_error_abs_mps {max_speed_tracking_error_abs_mps:.6f}"
        )

    if reasons:
        return "HOLD", reasons, details
    return "PASS", ["phase3 control quality checks satisfied"], details


def summarize_phase3_dataset_traffic_diversity(*, batch_root: Path, dataset_manifest_out: Path) -> dict[str, Any]:
    run_summary_paths = sorted(batch_root.glob("*/summary.json"))

    status_counts: dict[str, int] = {
        "success": 0,
        "failed": 0,
        "timeout": 0,
        "other": 0,
    }
    traffic_profile_ids: set[str] = set()
    traffic_profile_source_ids: set[str] = set()
    traffic_actor_pattern_ids: set[str] = set()
    traffic_lane_profile_signatures: set[str] = set()
    traffic_npc_counts: list[int] = []
    traffic_lane_indices: set[int] = set()
    traffic_npc_initial_gap_values: list[float] = []
    traffic_npc_gap_step_values: list[float] = []
    traffic_npc_speed_scale_values: list[float] = []
    traffic_npc_speed_jitter_values: list[float] = []

    def _read_non_negative_float(payload: dict[str, Any], key: str) -> float | None:
        try:
            value = float(payload.get(key))
        except (TypeError, ValueError):
            return None
        if value < 0.0:
            return None
        return value

    for summary_path in run_summary_paths:
        try:
            payload_raw = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = payload_raw if isinstance(payload_raw, dict) else {}

        status = str(payload.get("status", "")).strip().lower()
        if status in status_counts:
            status_counts[status] += 1
        else:
            status_counts["other"] += 1

        traffic_profile_id = str(payload.get("traffic_profile_id", "")).strip()
        if traffic_profile_id:
            traffic_profile_ids.add(traffic_profile_id)
        traffic_profile_source_id = str(payload.get("traffic_profile_source", "")).strip()
        if traffic_profile_source_id:
            traffic_profile_source_ids.add(traffic_profile_source_id)
        traffic_actor_pattern_id = str(payload.get("traffic_actor_pattern_id", "")).strip()
        if traffic_actor_pattern_id:
            traffic_actor_pattern_ids.add(traffic_actor_pattern_id)

        try:
            traffic_npc_count = int(payload.get("traffic_npc_count", 0) or 0)
        except (TypeError, ValueError):
            traffic_npc_count = 0
        if traffic_npc_count > 0:
            traffic_npc_counts.append(traffic_npc_count)

        lane_profile_raw = payload.get("traffic_npc_lane_profile", [])
        if isinstance(lane_profile_raw, list):
            lane_profile: list[int] = []
            for lane_raw in lane_profile_raw:
                try:
                    lane_index = int(lane_raw)
                except (TypeError, ValueError):
                    continue
                lane_profile.append(lane_index)
                traffic_lane_indices.add(lane_index)
            if lane_profile:
                traffic_lane_profile_signatures.add(",".join(str(value) for value in lane_profile))

        traffic_npc_initial_gap_m = _read_non_negative_float(payload, "traffic_npc_initial_gap_m")
        if traffic_npc_initial_gap_m is not None:
            traffic_npc_initial_gap_values.append(traffic_npc_initial_gap_m)

        traffic_npc_gap_step_m = _read_non_negative_float(payload, "traffic_npc_gap_step_m")
        if traffic_npc_gap_step_m is not None:
            traffic_npc_gap_step_values.append(traffic_npc_gap_step_m)

        traffic_npc_speed_scale = _read_non_negative_float(payload, "traffic_npc_speed_scale")
        if traffic_npc_speed_scale is not None:
            traffic_npc_speed_scale_values.append(traffic_npc_speed_scale)

        traffic_npc_speed_jitter_mps = _read_non_negative_float(payload, "traffic_npc_speed_jitter_mps")
        if traffic_npc_speed_jitter_mps is not None:
            traffic_npc_speed_jitter_values.append(traffic_npc_speed_jitter_mps)

    dataset_manifest_run_summary_count = 0
    dataset_manifest_release_summary_count = 0
    dataset_manifest_sds_versions: list[str] = []
    try:
        dataset_manifest_payload_raw = json.loads(dataset_manifest_out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        dataset_manifest_payload_raw = {}
    dataset_manifest_payload = (
        dataset_manifest_payload_raw if isinstance(dataset_manifest_payload_raw, dict) else {}
    )
    try:
        dataset_manifest_run_summary_count = int(dataset_manifest_payload.get("run_summary_count", 0) or 0)
    except (TypeError, ValueError):
        dataset_manifest_run_summary_count = 0
    try:
        dataset_manifest_release_summary_count = int(
            dataset_manifest_payload.get("release_summary_count", 0) or 0
        )
    except (TypeError, ValueError):
        dataset_manifest_release_summary_count = 0
    sds_versions_raw = dataset_manifest_payload.get("sds_versions", [])
    if isinstance(sds_versions_raw, list):
        seen_versions: set[str] = set()
        for version_raw in sds_versions_raw:
            version = str(version_raw).strip()
            if version and version not in seen_versions:
                seen_versions.add(version)
                dataset_manifest_sds_versions.append(version)

    traffic_npc_count_min = min(traffic_npc_counts) if traffic_npc_counts else 0
    traffic_npc_count_max = max(traffic_npc_counts) if traffic_npc_counts else 0
    traffic_npc_count_avg = (
        (sum(traffic_npc_counts) / float(len(traffic_npc_counts))) if traffic_npc_counts else 0.0
    )

    def _summarize_non_negative_float(values: list[float]) -> tuple[int, float, float, float]:
        if not values:
            return 0, 0.0, 0.0, 0.0
        return len(values), min(values), (sum(values) / float(len(values))), max(values)

    (
        traffic_npc_initial_gap_sample_count,
        traffic_npc_initial_gap_min,
        traffic_npc_initial_gap_avg,
        traffic_npc_initial_gap_max,
    ) = _summarize_non_negative_float(traffic_npc_initial_gap_values)
    (
        traffic_npc_gap_step_sample_count,
        traffic_npc_gap_step_min,
        traffic_npc_gap_step_avg,
        traffic_npc_gap_step_max,
    ) = _summarize_non_negative_float(traffic_npc_gap_step_values)
    (
        traffic_npc_speed_scale_sample_count,
        traffic_npc_speed_scale_min,
        traffic_npc_speed_scale_avg,
        traffic_npc_speed_scale_max,
    ) = _summarize_non_negative_float(traffic_npc_speed_scale_values)
    (
        traffic_npc_speed_jitter_sample_count,
        traffic_npc_speed_jitter_min,
        traffic_npc_speed_jitter_avg,
        traffic_npc_speed_jitter_max,
    ) = _summarize_non_negative_float(traffic_npc_speed_jitter_values)

    return {
        "run_summary_count": len(run_summary_paths),
        "run_status_counts": {key: int(status_counts[key]) for key in sorted(status_counts.keys())},
        "run_status_success_count": int(status_counts["success"]),
        "run_status_failed_count": int(status_counts["failed"]),
        "run_status_timeout_count": int(status_counts["timeout"]),
        "run_status_other_count": int(status_counts["other"]),
        "traffic_profile_count": len(traffic_profile_ids),
        "traffic_profile_ids": sorted(traffic_profile_ids),
        "traffic_profile_source_count": len(traffic_profile_source_ids),
        "traffic_profile_source_ids": sorted(traffic_profile_source_ids),
        "traffic_actor_pattern_count": len(traffic_actor_pattern_ids),
        "traffic_actor_pattern_ids": sorted(traffic_actor_pattern_ids),
        "traffic_lane_profile_signature_count": len(traffic_lane_profile_signatures),
        "traffic_lane_profile_signatures": sorted(traffic_lane_profile_signatures),
        "traffic_npc_count_sample_count": len(traffic_npc_counts),
        "traffic_npc_count_min": int(traffic_npc_count_min),
        "traffic_npc_count_avg": float(traffic_npc_count_avg),
        "traffic_npc_count_max": int(traffic_npc_count_max),
        "traffic_npc_initial_gap_m_sample_count": int(traffic_npc_initial_gap_sample_count),
        "traffic_npc_initial_gap_m_min": float(traffic_npc_initial_gap_min),
        "traffic_npc_initial_gap_m_avg": float(traffic_npc_initial_gap_avg),
        "traffic_npc_initial_gap_m_max": float(traffic_npc_initial_gap_max),
        "traffic_npc_gap_step_m_sample_count": int(traffic_npc_gap_step_sample_count),
        "traffic_npc_gap_step_m_min": float(traffic_npc_gap_step_min),
        "traffic_npc_gap_step_m_avg": float(traffic_npc_gap_step_avg),
        "traffic_npc_gap_step_m_max": float(traffic_npc_gap_step_max),
        "traffic_npc_speed_scale_sample_count": int(traffic_npc_speed_scale_sample_count),
        "traffic_npc_speed_scale_min": float(traffic_npc_speed_scale_min),
        "traffic_npc_speed_scale_avg": float(traffic_npc_speed_scale_avg),
        "traffic_npc_speed_scale_max": float(traffic_npc_speed_scale_max),
        "traffic_npc_speed_jitter_mps_sample_count": int(traffic_npc_speed_jitter_sample_count),
        "traffic_npc_speed_jitter_mps_min": float(traffic_npc_speed_jitter_min),
        "traffic_npc_speed_jitter_mps_avg": float(traffic_npc_speed_jitter_avg),
        "traffic_npc_speed_jitter_mps_max": float(traffic_npc_speed_jitter_max),
        "traffic_lane_index_unique_count": len(traffic_lane_indices),
        "traffic_lane_indices": sorted(traffic_lane_indices),
        "dataset_manifest_run_summary_count": int(dataset_manifest_run_summary_count),
        "dataset_manifest_release_summary_count": int(dataset_manifest_release_summary_count),
        "dataset_manifest_sds_versions": dataset_manifest_sds_versions,
    }


def summarize_phase3_lane_risk_from_release_summaries(
    *,
    release_summary_files: list[Path],
) -> dict[str, Any]:
    normalized_rows: list[dict[str, Any]] = []
    for summary_path in release_summary_files:
        try:
            payload_raw = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        lane_risk_raw = payload.get("lane_risk_summary", {})
        lane_risk = lane_risk_raw if isinstance(lane_risk_raw, dict) else {}

        def _as_int(value: Any) -> int:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0

        def _as_float_or_none(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        normalized_rows.append(
            {
                "run_count": _as_int(lane_risk.get("lane_risk_summary_run_count", 0)),
                "min_ttc_same_lane_sec": _as_float_or_none(lane_risk.get("min_ttc_same_lane_sec")),
                "min_ttc_adjacent_lane_sec": _as_float_or_none(lane_risk.get("min_ttc_adjacent_lane_sec")),
                "min_ttc_any_lane_sec": _as_float_or_none(lane_risk.get("min_ttc_any_lane_sec")),
                "ttc_under_3s_same_lane_total": _as_int(lane_risk.get("ttc_under_3s_same_lane_total", 0)),
                "ttc_under_3s_adjacent_lane_total": _as_int(lane_risk.get("ttc_under_3s_adjacent_lane_total", 0)),
                "same_lane_rows_total": _as_int(lane_risk.get("same_lane_rows_total", 0)),
                "adjacent_lane_rows_total": _as_int(lane_risk.get("adjacent_lane_rows_total", 0)),
                "other_lane_rows_total": _as_int(lane_risk.get("other_lane_rows_total", 0)),
            }
        )

    eligible_rows = [row for row in normalized_rows if int(row.get("run_count", 0)) > 0]
    if not eligible_rows:
        return {
            "lane_risk_summary_run_count": 0,
            "min_ttc_same_lane_sec": None,
            "min_ttc_adjacent_lane_sec": None,
            "min_ttc_any_lane_sec": None,
            "ttc_under_3s_same_lane_total": 0,
            "ttc_under_3s_adjacent_lane_total": 0,
            "same_lane_rows_total": 0,
            "adjacent_lane_rows_total": 0,
            "other_lane_rows_total": 0,
        }

    def _min_float(rows: list[dict[str, Any]], field: str) -> float | None:
        values: list[float] = []
        for row in rows:
            raw_value = row.get(field)
            if isinstance(raw_value, float):
                values.append(float(raw_value))
        if not values:
            return None
        return min(values)

    return {
        "lane_risk_summary_run_count": max(int(row.get("run_count", 0) or 0) for row in eligible_rows),
        "min_ttc_same_lane_sec": _min_float(eligible_rows, "min_ttc_same_lane_sec"),
        "min_ttc_adjacent_lane_sec": _min_float(eligible_rows, "min_ttc_adjacent_lane_sec"),
        "min_ttc_any_lane_sec": _min_float(eligible_rows, "min_ttc_any_lane_sec"),
        "ttc_under_3s_same_lane_total": max(
            int(row.get("ttc_under_3s_same_lane_total", 0) or 0)
            for row in eligible_rows
        ),
        "ttc_under_3s_adjacent_lane_total": max(
            int(row.get("ttc_under_3s_adjacent_lane_total", 0) or 0)
            for row in eligible_rows
        ),
        "same_lane_rows_total": max(
            int(row.get("same_lane_rows_total", 0) or 0)
            for row in eligible_rows
        ),
        "adjacent_lane_rows_total": max(
            int(row.get("adjacent_lane_rows_total", 0) or 0)
            for row in eligible_rows
        ),
        "other_lane_rows_total": max(
            int(row.get("other_lane_rows_total", 0) or 0)
            for row in eligible_rows
        ),
    }


def evaluate_phase3_dataset_traffic_gate(
    *,
    phase3_enable_hooks: bool,
    phase3_hooks: dict[str, Any],
    min_run_summary_count: int,
    min_traffic_profile_count: int,
    min_actor_pattern_count: int,
    min_avg_npc_count: float,
) -> tuple[str, list[str], dict[str, Any]]:
    configured = (
        int(min_run_summary_count) > 0
        or int(min_traffic_profile_count) > 0
        or int(min_actor_pattern_count) > 0
        or float(min_avg_npc_count) > 0.0
    )
    diversity_raw = phase3_hooks.get("dataset_traffic_diversity", {})
    diversity = diversity_raw if isinstance(diversity_raw, dict) else {}

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    observed_run_summary_count = _as_int(diversity.get("run_summary_count", 0))
    observed_traffic_profile_count = _as_int(diversity.get("traffic_profile_count", 0))
    observed_actor_pattern_count = _as_int(diversity.get("traffic_actor_pattern_count", 0))
    observed_avg_npc_count = _as_float(diversity.get("traffic_npc_count_avg", 0.0))

    details = {
        "enabled": bool(phase3_enable_hooks),
        "configured": bool(configured),
        "min_run_summary_count": int(min_run_summary_count),
        "min_traffic_profile_count": int(min_traffic_profile_count),
        "min_actor_pattern_count": int(min_actor_pattern_count),
        "min_avg_npc_count": float(min_avg_npc_count),
        "observed_run_summary_count": int(observed_run_summary_count),
        "observed_traffic_profile_count": int(observed_traffic_profile_count),
        "observed_actor_pattern_count": int(observed_actor_pattern_count),
        "observed_avg_npc_count": float(observed_avg_npc_count),
    }

    if not phase3_enable_hooks:
        return "N/A", [], details
    if not configured:
        return "N/A", [], details

    reasons: list[str] = []
    if min_run_summary_count > 0 and observed_run_summary_count < min_run_summary_count:
        reasons.append(
            "phase3_dataset_run_summary_count "
            f"{observed_run_summary_count} < min_run_summary_count {min_run_summary_count}"
        )
    if min_traffic_profile_count > 0 and observed_traffic_profile_count < min_traffic_profile_count:
        reasons.append(
            "phase3_dataset_traffic_profile_count "
            f"{observed_traffic_profile_count} < min_traffic_profile_count {min_traffic_profile_count}"
        )
    if min_actor_pattern_count > 0 and observed_actor_pattern_count < min_actor_pattern_count:
        reasons.append(
            "phase3_dataset_traffic_actor_pattern_count "
            f"{observed_actor_pattern_count} < min_actor_pattern_count {min_actor_pattern_count}"
        )
    if min_avg_npc_count > 0.0 and observed_avg_npc_count < min_avg_npc_count:
        reasons.append(
            "phase3_dataset_traffic_npc_count_avg "
            f"{observed_avg_npc_count:.6f} < min_avg_npc_count {min_avg_npc_count:.6f}"
        )
    if reasons:
        return "HOLD", reasons, details
    return "PASS", ["phase3 dataset traffic diversity checks satisfied"], details


def evaluate_phase3_lane_risk_gate(
    *,
    phase3_enable_hooks: bool,
    phase3_hooks: dict[str, Any],
    min_ttc_same_lane_sec: float,
    min_ttc_adjacent_lane_sec: float,
    min_ttc_any_lane_sec: float,
    max_ttc_under_3s_same_lane_total: int,
    max_ttc_under_3s_adjacent_lane_total: int,
    max_ttc_under_3s_any_lane_total: int,
) -> tuple[str, list[str], dict[str, Any]]:
    configured = (
        float(min_ttc_same_lane_sec) > 0.0
        or float(min_ttc_adjacent_lane_sec) > 0.0
        or float(min_ttc_any_lane_sec) > 0.0
        or int(max_ttc_under_3s_same_lane_total) > 0
        or int(max_ttc_under_3s_adjacent_lane_total) > 0
        or int(max_ttc_under_3s_any_lane_total) > 0
    )
    lane_risk_raw = phase3_hooks.get("lane_risk_summary", {})
    lane_risk = lane_risk_raw if isinstance(lane_risk_raw, dict) else {}

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    observed_run_count = _as_int(lane_risk.get("lane_risk_summary_run_count", 0))
    observed_ttc_same_lane_raw = lane_risk.get("min_ttc_same_lane_sec")
    observed_ttc_adjacent_lane_raw = lane_risk.get("min_ttc_adjacent_lane_sec")
    observed_ttc_any_lane_raw = lane_risk.get("min_ttc_any_lane_sec")
    observed_ttc_same_lane = _as_float(observed_ttc_same_lane_raw)
    observed_ttc_adjacent_lane = _as_float(observed_ttc_adjacent_lane_raw)
    observed_ttc_any_lane = _as_float(observed_ttc_any_lane_raw)
    observed_ttc_under_3s_same_lane_total = max(0, _as_int(lane_risk.get("ttc_under_3s_same_lane_total", 0)))
    observed_ttc_under_3s_adjacent_lane_total = max(
        0,
        _as_int(lane_risk.get("ttc_under_3s_adjacent_lane_total", 0)),
    )
    observed_ttc_under_3s_any_lane_total = (
        observed_ttc_under_3s_same_lane_total + observed_ttc_under_3s_adjacent_lane_total
    )

    details = {
        "enabled": bool(phase3_enable_hooks),
        "configured": bool(configured),
        "min_ttc_same_lane_sec": float(min_ttc_same_lane_sec),
        "min_ttc_adjacent_lane_sec": float(min_ttc_adjacent_lane_sec),
        "min_ttc_any_lane_sec": float(min_ttc_any_lane_sec),
        "max_ttc_under_3s_same_lane_total": int(max_ttc_under_3s_same_lane_total),
        "max_ttc_under_3s_adjacent_lane_total": int(max_ttc_under_3s_adjacent_lane_total),
        "max_ttc_under_3s_any_lane_total": int(max_ttc_under_3s_any_lane_total),
        "observed_run_count": int(observed_run_count),
        "observed_ttc_same_lane_sec": (
            observed_ttc_same_lane_raw if observed_ttc_same_lane_raw is not None else None
        ),
        "observed_ttc_adjacent_lane_sec": (
            observed_ttc_adjacent_lane_raw if observed_ttc_adjacent_lane_raw is not None else None
        ),
        "observed_ttc_any_lane_sec": observed_ttc_any_lane_raw if observed_ttc_any_lane_raw is not None else None,
        "observed_ttc_under_3s_same_lane_total": int(observed_ttc_under_3s_same_lane_total),
        "observed_ttc_under_3s_adjacent_lane_total": int(observed_ttc_under_3s_adjacent_lane_total),
        "observed_ttc_under_3s_any_lane_total": int(observed_ttc_under_3s_any_lane_total),
    }

    if not phase3_enable_hooks:
        return "N/A", [], details
    if not configured:
        return "N/A", [], details

    reasons: list[str] = []
    if min_ttc_same_lane_sec > 0.0:
        if observed_ttc_same_lane_raw is None:
            reasons.append(
                "phase3_lane_risk_min_ttc_same_lane_sec missing "
                f"< min_ttc_same_lane_sec {min_ttc_same_lane_sec:.6f}"
            )
        elif observed_ttc_same_lane < min_ttc_same_lane_sec:
            reasons.append(
                "phase3_lane_risk_min_ttc_same_lane_sec "
                f"{observed_ttc_same_lane:.6f} < min_ttc_same_lane_sec {min_ttc_same_lane_sec:.6f}"
            )
    if min_ttc_adjacent_lane_sec > 0.0:
        if observed_ttc_adjacent_lane_raw is None:
            reasons.append(
                "phase3_lane_risk_min_ttc_adjacent_lane_sec missing "
                f"< min_ttc_adjacent_lane_sec {min_ttc_adjacent_lane_sec:.6f}"
            )
        elif observed_ttc_adjacent_lane < min_ttc_adjacent_lane_sec:
            reasons.append(
                "phase3_lane_risk_min_ttc_adjacent_lane_sec "
                f"{observed_ttc_adjacent_lane:.6f} < min_ttc_adjacent_lane_sec {min_ttc_adjacent_lane_sec:.6f}"
            )
    if min_ttc_any_lane_sec > 0.0:
        if observed_ttc_any_lane_raw is None:
            reasons.append(
                "phase3_lane_risk_min_ttc_any_lane_sec missing "
                f"< min_ttc_any_lane_sec {min_ttc_any_lane_sec:.6f}"
            )
        elif observed_ttc_any_lane < min_ttc_any_lane_sec:
            reasons.append(
                "phase3_lane_risk_min_ttc_any_lane_sec "
                f"{observed_ttc_any_lane:.6f} < min_ttc_any_lane_sec {min_ttc_any_lane_sec:.6f}"
            )
    if (
        max_ttc_under_3s_same_lane_total > 0
        and observed_ttc_under_3s_same_lane_total > max_ttc_under_3s_same_lane_total
    ):
        reasons.append(
            "phase3_lane_risk_ttc_under_3s_same_lane_total "
            f"{observed_ttc_under_3s_same_lane_total} > max_ttc_under_3s_same_lane_total "
            f"{max_ttc_under_3s_same_lane_total}"
        )
    if (
        max_ttc_under_3s_adjacent_lane_total > 0
        and observed_ttc_under_3s_adjacent_lane_total > max_ttc_under_3s_adjacent_lane_total
    ):
        reasons.append(
            "phase3_lane_risk_ttc_under_3s_adjacent_lane_total "
            f"{observed_ttc_under_3s_adjacent_lane_total} > max_ttc_under_3s_adjacent_lane_total "
            f"{max_ttc_under_3s_adjacent_lane_total}"
        )
    if max_ttc_under_3s_any_lane_total > 0 and observed_ttc_under_3s_any_lane_total > max_ttc_under_3s_any_lane_total:
        reasons.append(
            "phase3_lane_risk_ttc_under_3s_any_lane_total "
            f"{observed_ttc_under_3s_any_lane_total} > max_ttc_under_3s_any_lane_total "
            f"{max_ttc_under_3s_any_lane_total}"
        )

    if reasons:
        return "HOLD", reasons, details
    return "PASS", ["phase3 lane-risk safety checks satisfied"], details


def evaluate_phase3_core_sim_gate(
    *,
    phase3_enable_hooks: bool,
    phase3_hooks: dict[str, Any],
    require_success: bool,
    min_ttc_same_lane_sec: float,
    min_ttc_any_lane_sec: float,
) -> tuple[str, list[str], dict[str, Any]]:
    configured = bool(require_success) or float(min_ttc_same_lane_sec) > 0.0 or float(min_ttc_any_lane_sec) > 0.0
    phase3_core_sim_raw = phase3_hooks.get("phase3_core_sim", {})
    phase3_core_sim = phase3_core_sim_raw if isinstance(phase3_core_sim_raw, dict) else {}

    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    observed_status = str(phase3_core_sim.get("status", "")).strip().lower()
    observed_collision = bool(phase3_core_sim.get("collision", False))
    observed_timeout = bool(phase3_core_sim.get("timeout", False))
    observed_ttc_same_lane_raw = phase3_core_sim.get("min_ttc_same_lane_sec")
    observed_ttc_any_lane_raw = phase3_core_sim.get("min_ttc_any_lane_sec")
    observed_ttc_same_lane = _as_float(observed_ttc_same_lane_raw)
    observed_ttc_any_lane = _as_float(observed_ttc_any_lane_raw)

    details = {
        "enabled": bool(phase3_enable_hooks),
        "configured": bool(configured),
        "require_success": bool(require_success),
        "min_ttc_same_lane_sec": float(min_ttc_same_lane_sec),
        "min_ttc_any_lane_sec": float(min_ttc_any_lane_sec),
        "core_sim_enabled": bool(phase3_core_sim.get("enabled", False)),
        "observed_status": observed_status,
        "observed_collision": observed_collision,
        "observed_timeout": observed_timeout,
        "observed_ttc_same_lane_sec": (
            observed_ttc_same_lane_raw if observed_ttc_same_lane_raw is not None else None
        ),
        "observed_ttc_any_lane_sec": observed_ttc_any_lane_raw if observed_ttc_any_lane_raw is not None else None,
    }

    if not phase3_enable_hooks:
        return "N/A", [], details
    if not configured:
        return "N/A", [], details

    reasons: list[str] = []
    if require_success:
        if observed_status != "success":
            reasons.append(f"phase3_core_sim_status {observed_status or 'unknown'} != success")
        if observed_collision:
            reasons.append("phase3_core_sim_collision true")
        if observed_timeout:
            reasons.append("phase3_core_sim_timeout true")
    if min_ttc_same_lane_sec > 0.0:
        if observed_ttc_same_lane_raw is None:
            reasons.append(
                "phase3_core_sim_min_ttc_same_lane_sec missing "
                f"< min_ttc_same_lane_sec {min_ttc_same_lane_sec:.6f}"
            )
        elif observed_ttc_same_lane < min_ttc_same_lane_sec:
            reasons.append(
                "phase3_core_sim_min_ttc_same_lane_sec "
                f"{observed_ttc_same_lane:.6f} < min_ttc_same_lane_sec {min_ttc_same_lane_sec:.6f}"
            )
    if min_ttc_any_lane_sec > 0.0:
        if observed_ttc_any_lane_raw is None:
            reasons.append(
                "phase3_core_sim_min_ttc_any_lane_sec missing "
                f"< min_ttc_any_lane_sec {min_ttc_any_lane_sec:.6f}"
            )
        elif observed_ttc_any_lane < min_ttc_any_lane_sec:
            reasons.append(
                "phase3_core_sim_min_ttc_any_lane_sec "
                f"{observed_ttc_any_lane:.6f} < min_ttc_any_lane_sec {min_ttc_any_lane_sec:.6f}"
            )

    if reasons:
        return "HOLD", reasons, details
    return "PASS", ["phase3 core-sim safety checks satisfied"], details


def evaluate_phase3_core_sim_matrix_gate(
    *,
    phase3_enable_hooks: bool,
    phase3_hooks: dict[str, Any],
    require_all_cases_success: bool,
    min_ttc_same_lane_sec: float,
    min_ttc_any_lane_sec: float,
    max_failed_cases: int,
    max_collision_cases: int,
    max_timeout_cases: int,
) -> tuple[str, list[str], dict[str, Any]]:
    configured = (
        bool(require_all_cases_success)
        or float(min_ttc_same_lane_sec) > 0.0
        or float(min_ttc_any_lane_sec) > 0.0
        or int(max_failed_cases) > 0
        or int(max_collision_cases) > 0
        or int(max_timeout_cases) > 0
    )
    phase3_core_sim_matrix_raw = phase3_hooks.get("phase3_core_sim_matrix", {})
    phase3_core_sim_matrix = (
        phase3_core_sim_matrix_raw if isinstance(phase3_core_sim_matrix_raw, dict) else {}
    )

    def _as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    observed_all_cases_success = bool(phase3_core_sim_matrix.get("all_cases_success", False))
    observed_failed_cases = max(0, _as_int(phase3_core_sim_matrix.get("failed_case_count", 0)))
    observed_collision_cases = max(0, _as_int(phase3_core_sim_matrix.get("collision_case_count", 0)))
    observed_timeout_cases = max(0, _as_int(phase3_core_sim_matrix.get("timeout_case_count", 0)))
    observed_ttc_same_lane_raw = phase3_core_sim_matrix.get("min_ttc_same_lane_sec_min")
    observed_ttc_any_lane_raw = phase3_core_sim_matrix.get("min_ttc_any_lane_sec_min")
    observed_ttc_same_lane = _as_float(observed_ttc_same_lane_raw)
    observed_ttc_any_lane = _as_float(observed_ttc_any_lane_raw)

    details = {
        "enabled": bool(phase3_enable_hooks),
        "configured": bool(configured),
        "require_all_cases_success": bool(require_all_cases_success),
        "min_ttc_same_lane_sec": float(min_ttc_same_lane_sec),
        "min_ttc_any_lane_sec": float(min_ttc_any_lane_sec),
        "max_failed_cases": int(max_failed_cases),
        "max_collision_cases": int(max_collision_cases),
        "max_timeout_cases": int(max_timeout_cases),
        "core_sim_matrix_enabled": bool(phase3_core_sim_matrix.get("enabled", False)),
        "observed_all_cases_success": observed_all_cases_success,
        "observed_failed_cases": observed_failed_cases,
        "observed_collision_cases": observed_collision_cases,
        "observed_timeout_cases": observed_timeout_cases,
        "observed_ttc_same_lane_sec": (
            observed_ttc_same_lane_raw if observed_ttc_same_lane_raw is not None else None
        ),
        "observed_ttc_any_lane_sec": observed_ttc_any_lane_raw if observed_ttc_any_lane_raw is not None else None,
    }

    if not phase3_enable_hooks:
        return "N/A", [], details
    if not configured:
        return "N/A", [], details

    reasons: list[str] = []
    if require_all_cases_success and not observed_all_cases_success:
        reasons.append("phase3_core_sim_matrix_all_cases_success false != true")
    if min_ttc_same_lane_sec > 0.0:
        if observed_ttc_same_lane_raw is None:
            reasons.append(
                "phase3_core_sim_matrix_min_ttc_same_lane_sec missing "
                f"< min_ttc_same_lane_sec {min_ttc_same_lane_sec:.6f}"
            )
        elif observed_ttc_same_lane < min_ttc_same_lane_sec:
            reasons.append(
                "phase3_core_sim_matrix_min_ttc_same_lane_sec "
                f"{observed_ttc_same_lane:.6f} < min_ttc_same_lane_sec {min_ttc_same_lane_sec:.6f}"
            )
    if min_ttc_any_lane_sec > 0.0:
        if observed_ttc_any_lane_raw is None:
            reasons.append(
                "phase3_core_sim_matrix_min_ttc_any_lane_sec missing "
                f"< min_ttc_any_lane_sec {min_ttc_any_lane_sec:.6f}"
            )
        elif observed_ttc_any_lane < min_ttc_any_lane_sec:
            reasons.append(
                "phase3_core_sim_matrix_min_ttc_any_lane_sec "
                f"{observed_ttc_any_lane:.6f} < min_ttc_any_lane_sec {min_ttc_any_lane_sec:.6f}"
            )
    if max_failed_cases > 0 and observed_failed_cases > max_failed_cases:
        reasons.append(
            "phase3_core_sim_matrix_failed_cases "
            f"{observed_failed_cases} > max_failed_cases {max_failed_cases}"
        )
    if max_collision_cases > 0 and observed_collision_cases > max_collision_cases:
        reasons.append(
            "phase3_core_sim_matrix_collision_cases "
            f"{observed_collision_cases} > max_collision_cases {max_collision_cases}"
        )
    if max_timeout_cases > 0 and observed_timeout_cases > max_timeout_cases:
        reasons.append(
            "phase3_core_sim_matrix_timeout_cases "
            f"{observed_timeout_cases} > max_timeout_cases {max_timeout_cases}"
        )

    if reasons:
        return "HOLD", reasons, details
    return "PASS", ["phase3 core-sim matrix safety checks satisfied"], details


def parse_args() -> argparse.Namespace:
    root = resolve_repo_root(__file__)
    parser = argparse.ArgumentParser(description="Run one-command E2E validation pipeline")
    parser.add_argument("--batch-spec", required=True, help="Path to Cloud batch spec JSON/YAML")
    parser.add_argument("--release-id", required=True, help="Release identifier")
    parser.add_argument(
        "--db",
        default=str(root / "30_Projects/P_Data-Lake-and-Explorer/prototype/data/scenario_lake_v0.sqlite"),
        help="SQLite DB path",
    )
    parser.add_argument(
        "--report-dir",
        default=str(root / "30_Projects/P_Validation-Tooling-MVP/prototype/reports"),
        help="Directory for generated reports",
    )
    parser.add_argument(
        "--gate-profile",
        default=str(root / "30_Projects/P_Validation-Tooling-MVP/prototype/gate_profiles/h0_highway_sanity_v0.json"),
        help="Gate profile JSON file",
    )
    parser.add_argument(
        "--requirement-map",
        default=str(root / "30_Projects/P_Validation-Tooling-MVP/prototype/requirement_maps/h0_highway_trace_v0.json"),
        help="Requirement map JSON file (empty string to disable)",
    )
    parser.add_argument("--sds-version", action="append", default=[], help="Target SDS version (repeatable)")
    parser.add_argument("--batch-out", default="", help="Optional Cloud batch output root override")
    parser.add_argument("--python-bin", default="python3", help="Python executable")
    parser.add_argument(
        "--cloud-runner",
        default=str(root / "30_Projects/P_Cloud-Engine/prototype/cloud_batch_runner.py"),
    )
    parser.add_argument(
        "--ingest-runner",
        default=str(root / "30_Projects/P_Data-Lake-and-Explorer/prototype/ingest_scenario_runs.py"),
    )
    parser.add_argument(
        "--report-runner",
        default=str(root / "30_Projects/P_Validation-Tooling-MVP/prototype/generate_release_report.py"),
    )
    parser.add_argument(
        "--phase2-enable-hooks",
        action="store_true",
        help="Run optional Phase-2 module hooks (sensor/log/map) after core pipeline",
    )
    parser.add_argument(
        "--sensor-bridge-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sensor_sim_bridge.py"),
    )
    parser.add_argument(
        "--sensor-bridge-world-state",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/world_state_v0.json"),
    )
    parser.add_argument(
        "--sensor-bridge-rig",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/sensor_rig_v0.json"),
    )
    parser.add_argument(
        "--sensor-bridge-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/sensor_frames_v0.json"),
    )
    parser.add_argument(
        "--sensor-bridge-fidelity-tier",
        default="contract",
        help="Sensor bridge fidelity tier forwarded to sensor_sim_bridge.py (contract|basic|high)",
    )
    parser.add_argument(
        "--sensor-sweep-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sensor_rig_sweep.py"),
    )
    parser.add_argument(
        "--sensor-sweep-candidates",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/rig_sweep_v0.json"),
    )
    parser.add_argument(
        "--sensor-sweep-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/rig_sweep_report_v0.json"),
    )
    parser.add_argument(
        "--log-replay-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/log_replay_runner.py"),
    )
    parser.add_argument(
        "--log-scene",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/log_scene_v0.json"),
    )
    parser.add_argument(
        "--log-replay-out-root",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs"),
    )
    parser.add_argument(
        "--map-convert-runner",
        default=str(root / "30_Projects/P_Map-Toolset-MVP/prototype/convert_map_format.py"),
    )
    parser.add_argument(
        "--map-validate-runner",
        default=str(root / "30_Projects/P_Map-Toolset-MVP/prototype/validate_canonical_map.py"),
    )
    parser.add_argument(
        "--map-simple",
        default=str(root / "30_Projects/P_Map-Toolset-MVP/prototype/examples/simple_highway_segment_v0.json"),
    )
    parser.add_argument(
        "--map-canonical-out",
        default=str(
            root
            / "30_Projects/P_Map-Toolset-MVP/prototype/examples/simple_highway_segment_v0.canonical.json"
        ),
    )
    parser.add_argument(
        "--map-validate-report-out",
        default=str(
            root
            / "30_Projects/P_Map-Toolset-MVP/prototype/examples/simple_highway_segment_v0.validation.json"
        ),
    )
    parser.add_argument(
        "--map-route-runner",
        default=str(root / "30_Projects/P_Map-Toolset-MVP/prototype/compute_canonical_route.py"),
    )
    parser.add_argument(
        "--map-route-report-out",
        default=str(
            root
            / "30_Projects/P_Map-Toolset-MVP/prototype/examples/simple_highway_segment_v0.route.json"
        ),
    )
    parser.add_argument(
        "--map-route-cost-mode",
        default="hops",
        help="Route optimization objective for map route hook (hops|length)",
    )
    parser.add_argument(
        "--map-route-entry-lane-id",
        default="",
        help="Optional entry lane id override for canonical route hook",
    )
    parser.add_argument(
        "--map-route-exit-lane-id",
        default="",
        help="Optional exit lane id override for canonical route hook",
    )
    parser.add_argument(
        "--map-route-via-lane-id",
        action="append",
        default=[],
        help="Optional via lane id override for canonical route hook (repeatable)",
    )
    parser.add_argument(
        "--phase2-route-gate-require-status-pass",
        action="store_true",
        help="Hold release when Phase-2 map route status is not pass",
    )
    parser.add_argument(
        "--phase2-route-gate-require-routing-semantic-pass",
        action="store_true",
        help="Hold release when map validation routing semantic status is not pass",
    )
    parser.add_argument(
        "--phase2-route-gate-min-lane-count",
        default="",
        help="Optional minimum required route lane count for Phase-2 map route quality gate",
    )
    parser.add_argument(
        "--phase2-route-gate-min-total-length-m",
        default="",
        help="Optional minimum required route total length in meters for Phase-2 map route quality gate",
    )
    parser.add_argument(
        "--phase2-route-gate-max-routing-semantic-warning-count",
        default="",
        help="Optional maximum allowed map validation routing semantic warning count for Phase-2 map route quality gate",
    )
    parser.add_argument(
        "--phase2-route-gate-max-unreachable-lane-count",
        default="",
        help="Optional maximum allowed map validation unreachable lane count for Phase-2 map route quality gate",
    )
    parser.add_argument(
        "--phase2-route-gate-max-non-reciprocal-link-warning-count",
        default="",
        help="Optional maximum allowed map validation non-reciprocal link warning count for Phase-2 map route quality gate",
    )
    parser.add_argument(
        "--phase2-route-gate-max-continuity-gap-warning-count",
        default="",
        help="Optional maximum allowed map validation continuity gap warning count for Phase-2 map route quality gate",
    )
    parser.add_argument(
        "--phase3-enable-hooks",
        action="store_true",
        help="Run optional Phase-3 module hook (synthetic dataset manifest build+ingest)",
    )
    parser.add_argument(
        "--dataset-manifest-runner",
        default=str(root / "30_Projects/P_Data-Lake-and-Explorer/prototype/build_dataset_manifest.py"),
    )
    parser.add_argument(
        "--neural-scene-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/neural_scene_bridge.py"),
        help="Runner for neural scene scaffold hook",
    )
    parser.add_argument(
        "--neural-scene-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/neural_scene_v0.json"),
        help="Output path for Phase-3 neural scene hook",
    )
    parser.add_argument(
        "--neural-render-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/render_neural_sensor_stub.py"),
        help="Runner for neural sensor rendering hook",
    )
    parser.add_argument(
        "--neural-render-sensor-rig",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/sensor_rig_v0.json"),
        help="Sensor rig path for neural render hook",
    )
    parser.add_argument(
        "--neural-render-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/neural_sensor_frames_v0.json"),
        help="Output path for Phase-3 neural sensor render hook",
    )
    parser.add_argument(
        "--sim-runtime-adapter-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sim_runtime_adapter_stub.py"),
        help="Runner for optional runtime rendering adapter hook (AWSIM/CARLA scaffold)",
    )
    parser.add_argument(
        "--sim-runtime",
        default="none",
        help="Runtime adapter target: none|awsim|carla",
    )
    parser.add_argument(
        "--sim-runtime-scene",
        default="",
        help="Optional scene path for runtime adapter hook (default: --log-scene)",
    )
    parser.add_argument(
        "--sim-runtime-sensor-rig",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/sensor_rig_v0.json"),
        help="Optional sensor rig path for runtime adapter hook",
    )
    parser.add_argument(
        "--sim-runtime-mode",
        default="headless",
        help="Runtime adapter mode: headless|interactive",
    )
    parser.add_argument(
        "--sim-runtime-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/sim_runtime_adapter_report_v0.json"),
        help="Output path for runtime adapter hook report",
    )
    parser.add_argument(
        "--sim-runtime-probe-enable",
        action="store_true",
        help="Enable optional runtime availability probe hook",
    )
    parser.add_argument(
        "--sim-runtime-probe-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sim_runtime_probe_runner.py"),
        help="Runner for optional runtime availability probe hook",
    )
    parser.add_argument(
        "--sim-runtime-probe-runtime-bin",
        default="",
        help="Optional runtime executable override for probe hook",
    )
    parser.add_argument(
        "--sim-runtime-probe-flag",
        default="",
        help="Optional legacy probe flag forwarded to runtime probe runner",
    )
    parser.add_argument(
        "--sim-runtime-probe-args-shlex",
        default="",
        help=(
            "Optional shell-like probe args forwarded as repeated --probe-arg "
            "(takes precedence over --sim-runtime-probe-flag)"
        ),
    )
    parser.add_argument(
        "--sim-runtime-probe-execute",
        action="store_true",
        help="Execute runtime probe command when runtime binary is available",
    )
    parser.add_argument(
        "--sim-runtime-probe-require-availability",
        action="store_true",
        help="Fail runtime probe when runtime binary is unavailable",
    )
    parser.add_argument(
        "--sim-runtime-probe-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/sim_runtime_probe_report_v0.json"),
        help="Output path for runtime probe report",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-enable",
        action="store_true",
        help="Enable optional runtime scenario contract hook",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sim_runtime_scenario_contract_runner.py"),
        help="Runner for optional runtime scenario contract hook",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-require-runtime-ready",
        action="store_true",
        help="Require runtime-ready probe signal for scenario contract execution",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/sim_runtime_scenario_contract_report_v0.json"),
        help="Output path for runtime scenario contract report",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-enable",
        action="store_true",
        help="Enable optional runtime scene-result hook",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sim_runtime_scene_result_runner.py"),
        help="Runner for optional runtime scene-result hook",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-require-runtime-ready",
        action="store_true",
        help="Require runtime-ready probe signal for runtime scene result publication",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/runtime_scene_result_v0.json"),
        help="Output path for runtime scene-result report",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-enable",
        action="store_true",
        help="Enable optional OpenSCENARIO/OpenDRIVE runtime interop contract hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sim_runtime_interop_contract_runner.py"),
        help="Runner for optional OpenSCENARIO/OpenDRIVE runtime interop contract hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sim_runtime_interop_export_runner.py"),
        help="Runner for optional launch-manifest to OpenSCENARIO/OpenDRIVE export hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-road-length-scale",
        default="1.0",
        help="Road length scale used by runtime interop export hook (> 0)",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-xosc-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/runtime_interop_export_v0.xosc"),
        help="Output OpenSCENARIO path for runtime interop export hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-xodr-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/runtime_interop_export_v0.xodr"),
        help="Output OpenDRIVE path for runtime interop export hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/sim_runtime_interop_export_report_v0.json"),
        help="Output path for runtime interop export report",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/sim_runtime_interop_import_runner.py"),
        help="Runner for optional OpenSCENARIO/OpenDRIVE import verification hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/sim_runtime_interop_import_report_v0.json"),
        help="Output path for runtime interop import report",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-manifest-consistency-mode",
        choices=["require", "allow"],
        default="require",
        help="Runtime interop import manifest consistency mode (require|allow)",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-export-consistency-mode",
        choices=["require", "allow"],
        default="require",
        help="Runtime interop import/export consistency mode (require|allow)",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-xosc",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/openscenario_minimal_v0.xosc"),
        help="OpenSCENARIO input path for runtime interop contract hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-xodr",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/opendrive_minimal_v0.xodr"),
        help="OpenDRIVE input path for runtime interop contract hook",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-require-runtime-ready",
        action="store_true",
        help="Require runtime-ready probe signal for runtime interop contract execution",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/sim_runtime_interop_contract_report_v0.json"),
        help="Output path for runtime interop contract report",
    )
    parser.add_argument(
        "--vehicle-dynamics-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/vehicle_dynamics_stub.py"),
        help="Runner for vehicle dynamics contract hook",
    )
    parser.add_argument(
        "--vehicle-profile",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/vehicle_profile_v0.json"),
        help="Vehicle profile path for Phase-3 hook",
    )
    parser.add_argument(
        "--control-sequence",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/examples/control_sequence_v0.json"),
        help="Control sequence path for Phase-3 hook",
    )
    parser.add_argument(
        "--vehicle-dynamics-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/vehicle_dynamics_trace_v0.json"),
        help="Output path for Phase-3 vehicle dynamics hook",
    )
    parser.add_argument(
        "--phase3-core-sim-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/core_sim_runner.py"),
        help="Runner for optional Phase-3 core-sim scenario hook",
    )
    parser.add_argument(
        "--phase3-core-sim-scenario",
        default="",
        help="Optional scenario path for Phase-3 core-sim hook (default: --log-scene)",
    )
    parser.add_argument(
        "--phase3-core-sim-run-id",
        default="",
        help="Optional run ID override for Phase-3 core-sim hook",
    )
    parser.add_argument(
        "--phase3-core-sim-out-root",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/phase3_core_sim"),
        help="Output root for Phase-3 core-sim hook artifacts",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-enable",
        action="store_true",
        help="Run optional Phase-3 core-sim traffic parameter matrix sweep",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-runner",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/core_sim_matrix_sweep_runner.py"),
        help="Runner for Phase-3 core-sim matrix sweep hook",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-out-root",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/phase3_core_sim_matrix"),
        help="Output root for Phase-3 core-sim matrix sweep run artifacts",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-report-out",
        default=str(root / "30_Projects/P_Sim-Engine/prototype/runs/phase3_core_sim_matrix_report_v0.json"),
        help="Output path for Phase-3 core-sim matrix sweep report",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-run-id-prefix",
        default="",
        help="Optional run ID prefix override for Phase-3 core-sim matrix sweep",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-traffic-profile-ids",
        default="sumo_highway_aggressive_v0,sumo_highway_balanced_v0",
        help="Comma-separated traffic profile IDs for Phase-3 core-sim matrix sweep",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-traffic-actor-pattern-ids",
        default="sumo_platoon_sparse_v0,sumo_platoon_balanced_v0,sumo_dense_aggressive_v0",
        help="Comma-separated traffic actor-pattern IDs for Phase-3 core-sim matrix sweep",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-speed-scale-values",
        default="0.9,1.0,1.1",
        help="Comma-separated traffic NPC speed scale values for Phase-3 core-sim matrix sweep",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-tire-friction-values",
        default="0.4,0.7,1.0",
        help="Comma-separated tire friction values for Phase-3 core-sim matrix sweep",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-surface-friction-values",
        default="0.8,1.0",
        help="Comma-separated surface friction scale values for Phase-3 core-sim matrix sweep",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-limit",
        default="",
        help="Optional maximum case count for Phase-3 core-sim matrix sweep (0 means all)",
    )
    parser.add_argument(
        "--phase3-enable-ego-collision-avoidance",
        action="store_true",
        help="Enable ego collision-avoidance override in Phase-3 core-sim hook",
    )
    parser.add_argument(
        "--phase3-avoidance-ttc-threshold-sec",
        default="",
        help="Optional TTC trigger threshold override for Phase-3 core-sim hook (> 0)",
    )
    parser.add_argument(
        "--phase3-ego-max-brake-mps2",
        default="",
        help="Optional ego max brake override for Phase-3 core-sim hook (> 0)",
    )
    parser.add_argument(
        "--phase3-tire-friction-coeff",
        default="",
        help="Optional tire friction coefficient override for Phase-3 core-sim hook (> 0)",
    )
    parser.add_argument(
        "--phase3-surface-friction-scale",
        default="",
        help="Optional surface friction scale override for Phase-3 core-sim hook (> 0)",
    )
    parser.add_argument(
        "--phase3-control-gate-max-overlap-ratio",
        default="",
        help="Optional max throttle/brake overlap ratio for Phase-3 control quality gate",
    )
    parser.add_argument(
        "--phase3-control-gate-max-steering-rate-degps",
        default="",
        help="Optional max abs steering rate in deg/s for Phase-3 control quality gate",
    )
    parser.add_argument(
        "--phase3-control-gate-max-throttle-plus-brake",
        default="",
        help="Optional max throttle+brake command sum for Phase-3 control quality gate",
    )
    parser.add_argument(
        "--phase3-control-gate-max-speed-tracking-error-abs-mps",
        default="",
        help="Optional max abs speed tracking error in m/s for Phase-3 control quality gate",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-run-summary-count",
        default="",
        help="Optional minimum run summary count for Phase-3 dataset traffic diversity gate",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-traffic-profile-count",
        default="",
        help="Optional minimum traffic profile count for Phase-3 dataset traffic diversity gate",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-actor-pattern-count",
        default="",
        help="Optional minimum traffic actor-pattern count for Phase-3 dataset traffic diversity gate",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-avg-npc-count",
        default="",
        help="Optional minimum average traffic NPC count for Phase-3 dataset traffic diversity gate",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-min-ttc-same-lane-sec",
        default="",
        help="Optional minimum same-lane TTC threshold for Phase-3 lane-risk safety gate",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
        default="",
        help="Optional minimum adjacent-lane TTC threshold for Phase-3 lane-risk safety gate",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-min-ttc-any-lane-sec",
        default="",
        help="Optional minimum any-lane TTC threshold for Phase-3 lane-risk safety gate",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
        default="",
        help="Optional maximum phase3 lane-risk ttc_under_3s same-lane total",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
        default="",
        help="Optional maximum phase3 lane-risk ttc_under_3s adjacent-lane total",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total",
        default="",
        help="Optional maximum phase3 lane-risk ttc_under_3s any-lane total",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-require-success",
        action="store_true",
        help="Require Phase-3 core-sim hook to finish in success/no-collision/no-timeout state",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-min-ttc-same-lane-sec",
        default="",
        help="Optional minimum same-lane TTC threshold for Phase-3 core-sim safety gate",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-min-ttc-any-lane-sec",
        default="",
        help="Optional minimum any-lane TTC threshold for Phase-3 core-sim safety gate",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-require-all-cases-success",
        action="store_true",
        help="Require Phase-3 core-sim matrix sweep to finish with all cases successful",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
        default="",
        help="Optional minimum same-lane TTC threshold for Phase-3 core-sim matrix safety gate",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
        default="",
        help="Optional minimum any-lane TTC threshold for Phase-3 core-sim matrix safety gate",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-max-failed-cases",
        default="",
        help="Optional maximum failed case count for Phase-3 core-sim matrix safety gate",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-max-collision-cases",
        default="",
        help="Optional maximum collision case count for Phase-3 core-sim matrix safety gate",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-max-timeout-cases",
        default="",
        help="Optional maximum timeout case count for Phase-3 core-sim matrix safety gate",
    )
    parser.add_argument(
        "--dataset-id",
        default="",
        help="Optional dataset identifier override for Phase-3 hook",
    )
    parser.add_argument(
        "--dataset-manifest-out",
        default=str(root / "30_Projects/P_Data-Lake-and-Explorer/prototype/data/dataset_manifest_v0.json"),
        help="Output JSON path for Phase-3 dataset manifest hook",
    )
    parser.add_argument(
        "--phase4-enable-hooks",
        action="store_true",
        help="Run optional Phase-4 module hook (HIL sequence scheduling scaffold)",
    )
    parser.add_argument(
        "--phase4-enable-copilot-hooks",
        action="store_true",
        help="Run optional Copilot prompt/release-assist hooks inside Phase-4",
    )
    parser.add_argument(
        "--phase4-require-done",
        action="store_true",
        help="Fail when Phase-4 module linkage status is not PHASE4_DONE",
    )
    parser.add_argument(
        "--hil-sequence-runner",
        default=str(root / "30_Projects/P_Autoware-Workspace-CI-MVP/prototype/hil_sequence_runner_stub.py"),
        help="Runner for Phase-4 HIL sequence scheduling scaffold",
    )
    parser.add_argument(
        "--hil-interface",
        default=str(root / "30_Projects/P_Autoware-Workspace-CI-MVP/prototype/examples/hil_interface_v0.json"),
        help="HIL interface JSON path for Phase-4 hook",
    )
    parser.add_argument(
        "--hil-sequence",
        default=str(root / "30_Projects/P_Autoware-Workspace-CI-MVP/prototype/examples/hil_test_sequence_v0.json"),
        help="HIL test sequence JSON path for Phase-4 hook",
    )
    parser.add_argument(
        "--hil-max-runtime-sec",
        default="",
        help="Optional Phase-4 HIL runtime upper bound in seconds (0 to disable)",
    )
    parser.add_argument(
        "--hil-schedule-out",
        default=str(root / "30_Projects/P_Autoware-Workspace-CI-MVP/prototype/runs/hil_schedule_manifest_v0.json"),
        help="Output path for Phase-4 HIL schedule manifest",
    )
    parser.add_argument(
        "--adp-trace-runner",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/adp_workflow_trace_stub.py"),
        help="Runner for Phase-4 ADP workflow trace scaffold",
    )
    parser.add_argument(
        "--adp-trace-out",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/reports/adp_workflow_trace_v0.json"),
        help="Output path for Phase-4 ADP workflow trace artifact",
    )
    parser.add_argument(
        "--phase4-linkage-runner",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/phase4_module_linkage_check_stub.py"),
        help="Runner for Phase-4 module linkage checklist/matrix validation",
    )
    parser.add_argument(
        "--phase4-linkage-matrix",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/STACK_MODULE_PARITY_MATRIX.md"),
        help="Phase-4 module linkage matrix file path",
    )
    parser.add_argument(
        "--phase4-linkage-checklist",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/PHASE4_MODULE_PARITY_CHECKLIST.md"),
        help="Phase-4 module linkage checklist file path",
    )
    parser.add_argument(
        "--phase4-linkage-reference-map",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/PHASE4_EXTERNAL_REFERENCE_MAP.md"),
        help="Phase-4 external reference map file path",
    )
    parser.add_argument(
        "--phase4-linkage-module",
        action="append",
        default=[],
        help=(
            "Phase-4 module name for linkage validation "
            f"(repeatable; allowed: {PHASE4_LINKAGE_ALLOWED_MODULES_TEXT}; "
            f"default: {PHASE4_LINKAGE_ALLOWED_MODULES_CSV})"
        ),
    )
    parser.add_argument(
        "--phase4-linkage-out",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/reports/phase4_module_linkage_report_v0.json"),
        help="Output path for Phase-4 module linkage report artifact",
    )
    parser.add_argument(
        "--phase4-reference-pattern-runner",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/phase4_reference_pattern_scan_stub.py"),
        help="Runner for Phase-4 reference pattern scan validation",
    )
    parser.add_argument(
        "--phase4-reference-index",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/PHASE4_REFERENCE_SCAN_INDEX_STUB.json"),
        help="Phase-4 reference scan index JSON path",
    )
    parser.add_argument(
        "--phase4-reference-repo-root",
        default="",
        help="Optional local repository root for Phase-4 reference pattern repo scan fallback",
    )
    parser.add_argument(
        "--phase4-reference-repo-path",
        action="append",
        default=[],
        help="Optional explicit repo_id=path mapping for Phase-4 reference pattern repo scan fallback (repeatable)",
    )
    parser.add_argument(
        "--phase4-reference-max-scan-files-per-repo",
        default="",
        help="Optional max text files scanned per repo for Phase-4 reference pattern repo scan fallback",
    )
    parser.add_argument(
        "--phase4-reference-pattern-module",
        action="append",
        default=[],
        help=(
            "Phase-4 module name for reference pattern scan "
            f"(repeatable; allowed: {PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT}; "
            "default: all modules in reference map)"
        ),
    )
    parser.add_argument(
        "--phase4-reference-min-coverage-ratio",
        default="1.0",
        help="Minimum required Phase-4 reference pattern coverage ratio in range [0, 1]",
    )
    parser.add_argument(
        "--phase4-reference-secondary-min-coverage-ratio",
        default="",
        help="Optional minimum required Phase-4 secondary reference pattern coverage ratio in range [0, 1]",
    )
    parser.add_argument(
        "--phase4-reference-pattern-out",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/reports/phase4_reference_pattern_scan_report_v0.json"),
        help="Output path for Phase-4 reference pattern scan report artifact",
    )
    parser.add_argument(
        "--copilot-contract-runner",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/copilot_prompt_contract_stub.py"),
        help="Runner for Phase-4 Copilot prompt contract scaffold",
    )
    parser.add_argument(
        "--copilot-release-assist-runner",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/copilot_release_assist_hook_stub.py"),
        help="Runner for Phase-4 Copilot release-assist scaffold",
    )
    parser.add_argument(
        "--copilot-mode",
        default="scenario",
        help="Copilot prompt mode for Phase-4 hook (scenario|query)",
    )
    parser.add_argument(
        "--copilot-prompt",
        default="Generate a highway merge scenario with one cut-in actor.",
        help="Copilot prompt text for Phase-4 hook",
    )
    parser.add_argument(
        "--copilot-context-json",
        default="",
        help="Optional Copilot context JSON path for Phase-4 hook",
    )
    parser.add_argument(
        "--copilot-guard-hold-threshold",
        default="",
        help="Copilot guard HOLD threshold for Phase-4 hook",
    )
    parser.add_argument(
        "--copilot-contract-out",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/reports/copilot_prompt_contract_v0.json"),
        help="Output path for Phase-4 Copilot prompt contract artifact",
    )
    parser.add_argument(
        "--copilot-audit-log",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/reports/copilot_prompt_audit_v0.jsonl"),
        help="Output path for Phase-4 Copilot prompt audit log (JSONL)",
    )
    parser.add_argument(
        "--copilot-release-assist-out",
        default=str(root / "30_Projects/P_E2E_Stack/prototype/reports/copilot_release_assist_hook_v0.json"),
        help="Output path for Phase-4 Copilot release-assist artifact",
    )
    parser.add_argument(
        "--strict-gate",
        action="store_true",
        help="Return non-zero if overall release decision is HOLD",
    )
    parser.add_argument(
        "--trend-window",
        default="",
        help="Recent release window for trend gate (0 to disable)",
    )
    parser.add_argument(
        "--trend-min-pass-rate",
        default="",
        help="Minimum PASS rate required when trend gate is enabled",
    )
    parser.add_argument(
        "--trend-min-samples",
        default="",
        help="Minimum samples required per SDS version for trend gate",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run only Cloud dry-run and stop")
    return parser.parse_args()


def run_phase2_hooks(args: argparse.Namespace) -> dict[str, Any]:
    sensor_world = Path(args.sensor_bridge_world_state).resolve()
    sensor_rig = Path(args.sensor_bridge_rig).resolve()
    sensor_out = Path(args.sensor_bridge_out).resolve()
    sensor_out.parent.mkdir(parents=True, exist_ok=True)
    sensor_fidelity_tier_input = str(args.sensor_bridge_fidelity_tier).strip().lower() or "contract"

    sensor_cmd = [
        args.python_bin,
        str(Path(args.sensor_bridge_runner).resolve()),
        "--world-state",
        str(sensor_world),
        "--sensor-rig",
        str(sensor_rig),
        "--out",
        str(sensor_out),
        "--fidelity-tier",
        sensor_fidelity_tier_input,
    ]
    run_cmd(sensor_cmd)
    sensor_payload_raw = json.loads(sensor_out.read_text(encoding="utf-8"))
    sensor_payload = sensor_payload_raw if isinstance(sensor_payload_raw, dict) else {}
    sensor_fidelity_tier = str(sensor_payload.get("sensor_fidelity_tier", "")).strip().lower()
    if not sensor_fidelity_tier:
        sensor_fidelity_tier = sensor_fidelity_tier_input
    try:
        sensor_fidelity_tier_score = float(sensor_payload.get("sensor_fidelity_tier_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        sensor_fidelity_tier_score = 0.0
    try:
        sensor_frame_count = int(sensor_payload.get("frame_count", 0) or 0)
    except (TypeError, ValueError):
        sensor_frame_count = 0
    sensor_frames_raw = sensor_payload.get("frames", [])
    sensor_frames = sensor_frames_raw if isinstance(sensor_frames_raw, list) else []
    if sensor_frame_count <= 0:
        sensor_frame_count = len(sensor_frames)
    sensor_stream_modality_counts: dict[str, int] = {}
    sensor_stream_modality_counts_raw = sensor_payload.get("sensor_stream_modality_counts", {})
    if isinstance(sensor_stream_modality_counts_raw, dict):
        for raw_key, raw_value in sensor_stream_modality_counts_raw.items():
            key = str(raw_key).strip().lower()
            if not key:
                continue
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                value = 0
            if value < 0:
                value = 0
            sensor_stream_modality_counts[key] = value
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
    def _to_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)
    def _normalize_projection_mode_counts(raw_value: Any) -> dict[str, int]:
        normalized: dict[str, int] = {}
        if isinstance(raw_value, dict):
            for raw_key, raw_count in raw_value.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                normalized[key] = _to_non_negative_int(raw_count)
        return {key: normalized[key] for key in sorted(normalized.keys())}
    def _normalize_bloom_level_counts(raw_value: Any) -> dict[str, int]:
        normalized: dict[str, int] = {}
        if isinstance(raw_value, dict):
            for raw_key, raw_count in raw_value.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                normalized[key] = _to_non_negative_int(raw_count)
        return {key: normalized[key] for key in sorted(normalized.keys())}
    def _normalize_uppercase_mode_counts(raw_value: Any) -> dict[str, int]:
        normalized: dict[str, int] = {}
        if isinstance(raw_value, dict):
            for raw_key, raw_count in raw_value.items():
                key = str(raw_key).strip().upper()
                if not key:
                    continue
                normalized[key] = _to_non_negative_int(raw_count)
        return {key: normalized[key] for key in sorted(normalized.keys())}

    sensor_quality_summary_defaults = {
        "camera_frame_count": 0,
        "camera_noise_stddev_px_avg": 0.0,
        "camera_dynamic_range_stops_avg": 0.0,
        "camera_visibility_score_avg": 0.0,
        "camera_motion_blur_level_avg": 0.0,
        "camera_snr_db_avg": 0.0,
        "camera_exposure_time_ms_avg": 0.0,
        "camera_signal_saturation_ratio_avg": 0.0,
        "camera_exposure_range_avg": 0.0,
        "camera_exposure_range_multiplier_avg": 0.0,
        "camera_auto_exposure_mode_counts": {},
        "camera_auto_exposure_mode_effective_counts": {},
        "camera_rolling_shutter_total_delay_ms_avg": 0.0,
        "camera_normalized_total_noise_avg": 0.0,
        "camera_distortion_edge_shift_px_avg": 0.0,
        "camera_principal_point_offset_norm_avg": 0.0,
        "camera_effective_focal_length_px_avg": 0.0,
        "camera_projection_mode_counts": {},
        "camera_gain_db_avg": 0.0,
        "camera_gamma_avg": 0.0,
        "camera_white_balance_kelvin_avg": 0.0,
        "camera_vignetting_edge_darkening_avg": 0.0,
        "camera_bloom_halo_strength_avg": 0.0,
        "camera_chromatic_aberration_shift_px_avg": 0.0,
        "camera_black_level_lift_norm_avg": 0.0,
        "camera_auto_black_level_stddev_to_subtract_avg": 0.0,
        "camera_saturation_rgb_avg": 0.0,
        "camera_saturation_effective_scale_avg": 0.0,
        "camera_tonemapper_disabled_frame_count": 0,
        "camera_bloom_level_counts": {},
        "camera_depth_enabled_frame_count": 0,
        "camera_depth_min_m_avg": 0.0,
        "camera_depth_max_m_avg": 0.0,
        "camera_depth_bit_depth_avg": 0.0,
        "camera_depth_mode_counts": {},
        "camera_optical_flow_enabled_frame_count": 0,
        "camera_optical_flow_magnitude_px_avg": 0.0,
        "camera_optical_flow_velocity_direction_counts": {},
        "camera_optical_flow_y_axis_direction_counts": {},
        "lidar_frame_count": 0,
        "lidar_point_count_total": 0,
        "lidar_point_count_avg": 0.0,
        "lidar_returns_per_laser_avg": 0.0,
        "lidar_detection_ratio_avg": 0.0,
        "lidar_effective_max_range_m_avg": 0.0,
        "radar_frame_count": 0,
        "radar_target_count_total": 0,
        "radar_ghost_target_count_total": 0,
        "radar_false_positive_count_total": 0,
        "radar_false_positive_count_avg": 0.0,
        "radar_false_positive_rate_avg": 0.0,
        "radar_ghost_target_count_avg": 0.0,
        "radar_clutter_index_avg": 0.0,
    }
    sensor_quality_summary_raw = sensor_payload.get("sensor_quality_summary", {})
    sensor_quality_summary: dict[str, Any] = {}
    if isinstance(sensor_quality_summary_raw, dict):
        sensor_quality_summary = {
            "camera_frame_count": _to_non_negative_int(sensor_quality_summary_raw.get("camera_frame_count", 0)),
            "camera_noise_stddev_px_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_noise_stddev_px_avg", 0.0)
            ),
            "camera_dynamic_range_stops_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_dynamic_range_stops_avg", 0.0)
            ),
            "camera_visibility_score_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_visibility_score_avg", 0.0)
            ),
            "camera_motion_blur_level_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_motion_blur_level_avg", 0.0)
            ),
            "camera_snr_db_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_snr_db_avg", 0.0)
            ),
            "camera_exposure_time_ms_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_exposure_time_ms_avg", 0.0)
            ),
            "camera_signal_saturation_ratio_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_signal_saturation_ratio_avg", 0.0)
            ),
            "camera_exposure_range_avg": _to_float(
                sensor_quality_summary_raw.get("camera_exposure_range_avg", 0.0),
                default=0.0,
            ),
            "camera_exposure_range_multiplier_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_exposure_range_multiplier_avg", 0.0)
            ),
            "camera_auto_exposure_mode_counts": _normalize_uppercase_mode_counts(
                sensor_quality_summary_raw.get("camera_auto_exposure_mode_counts", {})
            ),
            "camera_auto_exposure_mode_effective_counts": _normalize_uppercase_mode_counts(
                sensor_quality_summary_raw.get("camera_auto_exposure_mode_effective_counts", {})
            ),
            "camera_rolling_shutter_total_delay_ms_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_rolling_shutter_total_delay_ms_avg", 0.0)
            ),
            "camera_normalized_total_noise_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_normalized_total_noise_avg", 0.0)
            ),
            "camera_distortion_edge_shift_px_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_distortion_edge_shift_px_avg", 0.0)
            ),
            "camera_principal_point_offset_norm_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_principal_point_offset_norm_avg", 0.0)
            ),
            "camera_effective_focal_length_px_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_effective_focal_length_px_avg", 0.0)
            ),
            "camera_projection_mode_counts": _normalize_projection_mode_counts(
                sensor_quality_summary_raw.get("camera_projection_mode_counts", {})
            ),
            "camera_gain_db_avg": _to_float(sensor_quality_summary_raw.get("camera_gain_db_avg", 0.0), default=0.0),
            "camera_gamma_avg": _to_non_negative_float(sensor_quality_summary_raw.get("camera_gamma_avg", 0.0)),
            "camera_white_balance_kelvin_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_white_balance_kelvin_avg", 0.0)
            ),
            "camera_vignetting_edge_darkening_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_vignetting_edge_darkening_avg", 0.0)
            ),
            "camera_bloom_halo_strength_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_bloom_halo_strength_avg", 0.0)
            ),
            "camera_chromatic_aberration_shift_px_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_chromatic_aberration_shift_px_avg", 0.0)
            ),
            "camera_black_level_lift_norm_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_black_level_lift_norm_avg", 0.0)
            ),
            "camera_auto_black_level_stddev_to_subtract_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_auto_black_level_stddev_to_subtract_avg", 0.0)
            ),
            "camera_saturation_rgb_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_saturation_rgb_avg", 0.0)
            ),
            "camera_saturation_effective_scale_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_saturation_effective_scale_avg", 0.0)
            ),
            "camera_tonemapper_disabled_frame_count": _to_non_negative_int(
                sensor_quality_summary_raw.get("camera_tonemapper_disabled_frame_count", 0)
            ),
            "camera_bloom_level_counts": _normalize_bloom_level_counts(
                sensor_quality_summary_raw.get("camera_bloom_level_counts", {})
            ),
            "camera_depth_enabled_frame_count": _to_non_negative_int(
                sensor_quality_summary_raw.get("camera_depth_enabled_frame_count", 0)
            ),
            "camera_depth_min_m_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_depth_min_m_avg", 0.0)
            ),
            "camera_depth_max_m_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_depth_max_m_avg", 0.0)
            ),
            "camera_depth_bit_depth_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_depth_bit_depth_avg", 0.0)
            ),
            "camera_depth_mode_counts": _normalize_uppercase_mode_counts(
                sensor_quality_summary_raw.get("camera_depth_mode_counts", {})
            ),
            "camera_optical_flow_enabled_frame_count": _to_non_negative_int(
                sensor_quality_summary_raw.get("camera_optical_flow_enabled_frame_count", 0)
            ),
            "camera_optical_flow_magnitude_px_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("camera_optical_flow_magnitude_px_avg", 0.0)
            ),
            "camera_optical_flow_velocity_direction_counts": _normalize_uppercase_mode_counts(
                sensor_quality_summary_raw.get("camera_optical_flow_velocity_direction_counts", {})
            ),
            "camera_optical_flow_y_axis_direction_counts": _normalize_uppercase_mode_counts(
                sensor_quality_summary_raw.get("camera_optical_flow_y_axis_direction_counts", {})
            ),
            "lidar_frame_count": _to_non_negative_int(sensor_quality_summary_raw.get("lidar_frame_count", 0)),
            "lidar_point_count_total": _to_non_negative_int(
                sensor_quality_summary_raw.get("lidar_point_count_total", 0)
            ),
            "lidar_point_count_avg": _to_non_negative_float(sensor_quality_summary_raw.get("lidar_point_count_avg", 0.0)),
            "lidar_returns_per_laser_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("lidar_returns_per_laser_avg", 0.0)
            ),
            "lidar_detection_ratio_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("lidar_detection_ratio_avg", 0.0)
            ),
            "lidar_effective_max_range_m_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("lidar_effective_max_range_m_avg", 0.0)
            ),
            "radar_frame_count": _to_non_negative_int(sensor_quality_summary_raw.get("radar_frame_count", 0)),
            "radar_target_count_total": _to_non_negative_int(
                sensor_quality_summary_raw.get("radar_target_count_total", 0)
            ),
            "radar_ghost_target_count_total": _to_non_negative_int(
                sensor_quality_summary_raw.get("radar_ghost_target_count_total", 0)
            ),
            "radar_false_positive_count_total": _to_non_negative_int(
                sensor_quality_summary_raw.get("radar_false_positive_count_total", 0)
            ),
            "radar_false_positive_count_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("radar_false_positive_count_avg", 0.0)
            ),
            "radar_false_positive_rate_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("radar_false_positive_rate_avg", 0.0)
            ),
            "radar_ghost_target_count_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("radar_ghost_target_count_avg", 0.0)
            ),
            "radar_clutter_index_avg": _to_non_negative_float(
                sensor_quality_summary_raw.get("radar_clutter_index_avg", 0.0)
            ),
        }
    if not sensor_quality_summary and sensor_frames:
        camera_frame_count = 0
        camera_noise_total = 0.0
        camera_dynamic_range_total = 0.0
        camera_visibility_score_total = 0.0
        camera_motion_blur_level_total = 0.0
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
        camera_tonemapper_disabled_frame_count = 0
        camera_bloom_level_counts: dict[str, int] = {}
        camera_depth_enabled_frame_count = 0
        camera_depth_min_m_total = 0.0
        camera_depth_max_m_total = 0.0
        camera_depth_bit_depth_total = 0.0
        camera_depth_mode_counts: dict[str, int] = {}
        camera_optical_flow_enabled_frame_count = 0
        camera_optical_flow_magnitude_px_total = 0.0
        camera_optical_flow_velocity_direction_counts: dict[str, int] = {}
        camera_optical_flow_y_axis_direction_counts: dict[str, int] = {}
        lidar_frame_count = 0
        lidar_point_total = 0
        lidar_returns_total = 0
        lidar_detection_ratio_total = 0.0
        lidar_effective_max_range_m_total = 0.0
        radar_frame_count = 0
        radar_target_total = 0
        radar_ghost_target_total = 0
        radar_false_positive_total = 0
        radar_false_positive_rate_total = 0.0
        radar_clutter_index_total = 0.0
        for frame in sensor_frames:
            if not isinstance(frame, dict):
                continue
            sensor_type = str(frame.get("sensor_type", "")).strip().lower()
            payload = frame.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            if sensor_type == "camera":
                camera_frame_count += 1
                camera_noise_total += _to_non_negative_float(payload.get("camera_noise_stddev_px", 0.0))
                camera_dynamic_range_total += _to_non_negative_float(payload.get("dynamic_range_stops", 0.0))
                camera_visibility_score_total += _to_non_negative_float(payload.get("visibility_score", 0.0))
                camera_motion_blur_level_total += _to_non_negative_float(payload.get("motion_blur_level", 0.0))
                camera_physics_payload_raw = payload.get("camera_physics", {})
                camera_physics_payload = (
                    camera_physics_payload_raw if isinstance(camera_physics_payload_raw, dict) else {}
                )
                camera_snr_db_total += _to_non_negative_float(camera_physics_payload.get("snr_db", 0.0))
                camera_exposure_time_ms_total += _to_non_negative_float(
                    camera_physics_payload.get("exposure_time_ms", 0.0)
                )
                camera_signal_saturation_ratio_total += _to_non_negative_float(
                    camera_physics_payload.get("signal_saturation_ratio", 0.0)
                )
                camera_exposure_range_total += _to_float(
                    camera_physics_payload.get("exposure_range", 0.0),
                    default=0.0,
                )
                camera_exposure_range_multiplier_total += _to_non_negative_float(
                    camera_physics_payload.get("exposure_range_multiplier", 0.0)
                )
                auto_exposure_mode = str(camera_physics_payload.get("auto_exposure_mode", "")).strip().upper()
                if auto_exposure_mode:
                    camera_auto_exposure_mode_counts[auto_exposure_mode] = (
                        camera_auto_exposure_mode_counts.get(auto_exposure_mode, 0) + 1
                    )
                auto_exposure_mode_effective = str(
                    camera_physics_payload.get("auto_exposure_mode_effective", "")
                ).strip().upper()
                if auto_exposure_mode_effective:
                    camera_auto_exposure_mode_effective_counts[auto_exposure_mode_effective] = (
                        camera_auto_exposure_mode_effective_counts.get(auto_exposure_mode_effective, 0) + 1
                    )
                camera_rolling_shutter_total_delay_ms_total += _to_non_negative_float(
                    camera_physics_payload.get("rolling_shutter_total_delay_ms", 0.0)
                )
                camera_normalized_total_noise_total += _to_non_negative_float(
                    camera_physics_payload.get("normalized_total_noise", 0.0)
                )
                camera_geometry_payload_raw = payload.get("camera_geometry", {})
                camera_geometry_payload = (
                    camera_geometry_payload_raw if isinstance(camera_geometry_payload_raw, dict) else {}
                )
                camera_distortion_edge_shift_px_total += _to_non_negative_float(
                    camera_geometry_payload.get("distortion_edge_shift_px_est", 0.0)
                )
                camera_principal_point_offset_norm_total += _to_non_negative_float(
                    camera_geometry_payload.get("principal_point_offset_norm", 0.0)
                )
                fx = _to_non_negative_float(camera_geometry_payload.get("fx", 0.0))
                fy = _to_non_negative_float(camera_geometry_payload.get("fy", 0.0))
                if fx > 0.0 and fy > 0.0:
                    camera_effective_focal_length_px_total += (fx + fy) / 2.0
                elif fx > 0.0:
                    camera_effective_focal_length_px_total += fx
                elif fy > 0.0:
                    camera_effective_focal_length_px_total += fy
                projection = str(camera_geometry_payload.get("projection", "")).strip().upper()
                if projection:
                    camera_projection_mode_counts[projection] = (
                        camera_projection_mode_counts.get(projection, 0) + 1
                    )
                camera_postprocess_payload_raw = payload.get("camera_postprocess", {})
                camera_postprocess_payload = (
                    camera_postprocess_payload_raw if isinstance(camera_postprocess_payload_raw, dict) else {}
                )
                camera_gain_db_total += _to_float(camera_postprocess_payload.get("gain_db", 0.0), default=0.0)
                camera_gamma_total += _to_non_negative_float(camera_postprocess_payload.get("gamma", 0.0))
                camera_white_balance_kelvin_total += _to_non_negative_float(
                    camera_postprocess_payload.get("white_balance_kelvin", 0.0)
                )
                camera_vignetting_edge_darkening_total += _to_non_negative_float(
                    camera_postprocess_payload.get("vignetting_edge_darkening", 0.0)
                )
                camera_bloom_halo_strength_total += _to_non_negative_float(
                    camera_postprocess_payload.get("bloom_halo_strength", 0.0)
                )
                camera_chromatic_aberration_shift_px_total += _to_non_negative_float(
                    camera_postprocess_payload.get("chromatic_aberration_shift_px_est", 0.0)
                )
                camera_black_level_lift_norm_total += _to_non_negative_float(
                    camera_postprocess_payload.get("black_level_lift_norm", 0.0)
                )
                camera_auto_black_level_stddev_to_subtract_total += _to_non_negative_float(
                    camera_postprocess_payload.get("auto_black_level_stddev_to_subtract", 0.0)
                )
                camera_saturation_rgb_avg_total += _to_non_negative_float(
                    camera_postprocess_payload.get("saturation_rgb_avg", 0.0)
                )
                camera_saturation_effective_scale_total += _to_non_negative_float(
                    camera_postprocess_payload.get("saturation_effective_scale", 0.0)
                )
                if bool(camera_postprocess_payload.get("disable_tonemapper", False)):
                    camera_tonemapper_disabled_frame_count += 1
                bloom_level = str(camera_postprocess_payload.get("bloom_level", "")).strip().upper()
                if bloom_level:
                    camera_bloom_level_counts[bloom_level] = camera_bloom_level_counts.get(bloom_level, 0) + 1
                camera_depth_payload_raw = payload.get("camera_depth", {})
                camera_depth_payload = (
                    camera_depth_payload_raw if isinstance(camera_depth_payload_raw, dict) else {}
                )
                if bool(camera_depth_payload.get("depth_enabled", False)):
                    camera_depth_enabled_frame_count += 1
                camera_depth_min_m_total += _to_non_negative_float(camera_depth_payload.get("depth_min_m", 0.0))
                camera_depth_max_m_total += _to_non_negative_float(camera_depth_payload.get("depth_max_m", 0.0))
                camera_depth_bit_depth_total += _to_non_negative_float(
                    camera_depth_payload.get("depth_bit_depth", 0.0)
                )
                depth_mode = str(camera_depth_payload.get("depth_mode", "")).strip().upper()
                if depth_mode:
                    camera_depth_mode_counts[depth_mode] = camera_depth_mode_counts.get(depth_mode, 0) + 1
                camera_optical_flow_payload_raw = payload.get("camera_optical_flow_2d", {})
                camera_optical_flow_payload = (
                    camera_optical_flow_payload_raw if isinstance(camera_optical_flow_payload_raw, dict) else {}
                )
                if bool(camera_optical_flow_payload.get("optical_flow_enabled", False)):
                    camera_optical_flow_enabled_frame_count += 1
                camera_optical_flow_magnitude_px_total += _to_non_negative_float(
                    camera_optical_flow_payload.get("mean_flow_magnitude_px_est", 0.0)
                )
                velocity_direction = str(
                    camera_optical_flow_payload.get("velocity_direction", "")
                ).strip().upper()
                if velocity_direction:
                    camera_optical_flow_velocity_direction_counts[velocity_direction] = (
                        camera_optical_flow_velocity_direction_counts.get(velocity_direction, 0) + 1
                    )
                y_axis_direction = str(
                    camera_optical_flow_payload.get("y_axis_direction", "")
                ).strip().upper()
                if y_axis_direction:
                    camera_optical_flow_y_axis_direction_counts[y_axis_direction] = (
                        camera_optical_flow_y_axis_direction_counts.get(y_axis_direction, 0) + 1
                    )
            elif sensor_type == "lidar":
                lidar_frame_count += 1
                lidar_point_total += _to_non_negative_int(payload.get("point_count", 0))
                lidar_returns_total += _to_non_negative_int(payload.get("returns_per_laser", 0))
                lidar_detection_ratio_total += _to_non_negative_float(payload.get("detection_ratio", 0.0))
                lidar_effective_max_range_m_total += _to_non_negative_float(
                    payload.get("effective_max_range_m", 0.0)
                )
            elif sensor_type == "radar":
                radar_frame_count += 1
                radar_target_total += _to_non_negative_int(payload.get("target_count", 0))
                radar_ghost_target_total += _to_non_negative_int(payload.get("ghost_target_count", 0))
                radar_false_positive_total += _to_non_negative_int(payload.get("false_positive_count", 0))
                radar_false_positive_rate_total += _to_non_negative_float(payload.get("radar_false_positive_rate", 0.0))
                radar_clutter_index_total += _to_non_negative_float(payload.get("radar_clutter_index", 0.0))
        sensor_quality_summary = {
            "camera_frame_count": int(camera_frame_count),
            "camera_noise_stddev_px_avg": (
                float(camera_noise_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_dynamic_range_stops_avg": (
                float(camera_dynamic_range_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_visibility_score_avg": (
                float(camera_visibility_score_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_motion_blur_level_avg": (
                float(camera_motion_blur_level_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_snr_db_avg": (
                float(camera_snr_db_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_exposure_time_ms_avg": (
                float(camera_exposure_time_ms_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_signal_saturation_ratio_avg": (
                float(camera_signal_saturation_ratio_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_exposure_range_avg": (
                float(camera_exposure_range_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_exposure_range_multiplier_avg": (
                float(camera_exposure_range_multiplier_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_auto_exposure_mode_counts": {
                key: camera_auto_exposure_mode_counts[key]
                for key in sorted(camera_auto_exposure_mode_counts.keys())
            },
            "camera_auto_exposure_mode_effective_counts": {
                key: camera_auto_exposure_mode_effective_counts[key]
                for key in sorted(camera_auto_exposure_mode_effective_counts.keys())
            },
            "camera_rolling_shutter_total_delay_ms_avg": (
                float(camera_rolling_shutter_total_delay_ms_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_normalized_total_noise_avg": (
                float(camera_normalized_total_noise_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_distortion_edge_shift_px_avg": (
                float(camera_distortion_edge_shift_px_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_principal_point_offset_norm_avg": (
                float(camera_principal_point_offset_norm_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_effective_focal_length_px_avg": (
                float(camera_effective_focal_length_px_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_projection_mode_counts": {
                key: camera_projection_mode_counts[key] for key in sorted(camera_projection_mode_counts.keys())
            },
            "camera_gain_db_avg": (
                float(camera_gain_db_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_gamma_avg": (
                float(camera_gamma_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_white_balance_kelvin_avg": (
                float(camera_white_balance_kelvin_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_vignetting_edge_darkening_avg": (
                float(camera_vignetting_edge_darkening_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_bloom_halo_strength_avg": (
                float(camera_bloom_halo_strength_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_chromatic_aberration_shift_px_avg": (
                float(camera_chromatic_aberration_shift_px_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_black_level_lift_norm_avg": (
                float(camera_black_level_lift_norm_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_auto_black_level_stddev_to_subtract_avg": (
                float(camera_auto_black_level_stddev_to_subtract_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_saturation_rgb_avg": (
                float(camera_saturation_rgb_avg_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_saturation_effective_scale_avg": (
                float(camera_saturation_effective_scale_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_tonemapper_disabled_frame_count": int(camera_tonemapper_disabled_frame_count),
            "camera_bloom_level_counts": {
                key: camera_bloom_level_counts[key] for key in sorted(camera_bloom_level_counts.keys())
            },
            "camera_depth_enabled_frame_count": int(camera_depth_enabled_frame_count),
            "camera_depth_min_m_avg": (
                float(camera_depth_min_m_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_depth_max_m_avg": (
                float(camera_depth_max_m_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_depth_bit_depth_avg": (
                float(camera_depth_bit_depth_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_depth_mode_counts": {
                key: camera_depth_mode_counts[key] for key in sorted(camera_depth_mode_counts.keys())
            },
            "camera_optical_flow_enabled_frame_count": int(camera_optical_flow_enabled_frame_count),
            "camera_optical_flow_magnitude_px_avg": (
                float(camera_optical_flow_magnitude_px_total / float(camera_frame_count))
                if camera_frame_count > 0
                else 0.0
            ),
            "camera_optical_flow_velocity_direction_counts": {
                key: camera_optical_flow_velocity_direction_counts[key]
                for key in sorted(camera_optical_flow_velocity_direction_counts.keys())
            },
            "camera_optical_flow_y_axis_direction_counts": {
                key: camera_optical_flow_y_axis_direction_counts[key]
                for key in sorted(camera_optical_flow_y_axis_direction_counts.keys())
            },
            "lidar_frame_count": int(lidar_frame_count),
            "lidar_point_count_total": int(lidar_point_total),
            "lidar_point_count_avg": (
                float(lidar_point_total / float(lidar_frame_count))
                if lidar_frame_count > 0
                else 0.0
            ),
            "lidar_returns_per_laser_avg": (
                float(lidar_returns_total / float(lidar_frame_count))
                if lidar_frame_count > 0
                else 0.0
            ),
            "lidar_detection_ratio_avg": (
                float(lidar_detection_ratio_total / float(lidar_frame_count))
                if lidar_frame_count > 0
                else 0.0
            ),
            "lidar_effective_max_range_m_avg": (
                float(lidar_effective_max_range_m_total / float(lidar_frame_count))
                if lidar_frame_count > 0
                else 0.0
            ),
            "radar_frame_count": int(radar_frame_count),
            "radar_target_count_total": int(radar_target_total),
            "radar_ghost_target_count_total": int(radar_ghost_target_total),
            "radar_false_positive_count_total": int(radar_false_positive_total),
            "radar_false_positive_count_avg": (
                float(radar_false_positive_total / float(radar_frame_count))
                if radar_frame_count > 0
                else 0.0
            ),
            "radar_false_positive_rate_avg": (
                float(radar_false_positive_rate_total / float(radar_frame_count))
                if radar_frame_count > 0
                else 0.0
            ),
            "radar_ghost_target_count_avg": (
                float(radar_ghost_target_total / float(radar_frame_count))
                if radar_frame_count > 0
                else 0.0
            ),
            "radar_clutter_index_avg": (
                float(radar_clutter_index_total / float(radar_frame_count))
                if radar_frame_count > 0
                else 0.0
            ),
        }
    if not sensor_quality_summary:
        sensor_quality_summary = {
            **sensor_quality_summary_defaults,
            "camera_projection_mode_counts": {},
            "camera_bloom_level_counts": {},
            "camera_depth_mode_counts": {},
            "camera_optical_flow_velocity_direction_counts": {},
            "camera_optical_flow_y_axis_direction_counts": {},
        }

    sensor_sweep_runner = Path(args.sensor_sweep_runner).resolve()
    sensor_sweep_candidates = Path(args.sensor_sweep_candidates).resolve()
    sensor_sweep_out = Path(args.sensor_sweep_out).resolve()
    sensor_sweep_out.parent.mkdir(parents=True, exist_ok=True)
    sensor_sweep_cmd = [
        args.python_bin,
        str(sensor_sweep_runner),
        "--world-state",
        str(sensor_world),
        "--rig-candidates",
        str(sensor_sweep_candidates),
        "--out",
        str(sensor_sweep_out),
        "--fidelity-tier",
        sensor_fidelity_tier_input,
    ]
    run_cmd(sensor_sweep_cmd)
    sensor_sweep_payload_raw = json.loads(sensor_sweep_out.read_text(encoding="utf-8"))
    sensor_sweep_payload = sensor_sweep_payload_raw if isinstance(sensor_sweep_payload_raw, dict) else {}
    sensor_sweep_fidelity_tier = str(sensor_sweep_payload.get("sensor_fidelity_tier", "")).strip().lower()
    if not sensor_sweep_fidelity_tier:
        sensor_sweep_fidelity_tier = sensor_fidelity_tier_input
    try:
        sensor_sweep_candidate_count = int(sensor_sweep_payload.get("candidate_count", 0) or 0)
    except (TypeError, ValueError):
        sensor_sweep_candidate_count = 0
    if sensor_sweep_candidate_count < 0:
        sensor_sweep_candidate_count = 0
    sensor_sweep_best_rig_id = str(sensor_sweep_payload.get("best_rig_id", "")).strip()
    sensor_sweep_rankings_raw = sensor_sweep_payload.get("rankings", [])
    sensor_sweep_rankings = sensor_sweep_rankings_raw if isinstance(sensor_sweep_rankings_raw, list) else []
    if sensor_sweep_candidate_count <= 0:
        sensor_sweep_candidate_count = len([row for row in sensor_sweep_rankings if isinstance(row, dict)])
    if not sensor_sweep_best_rig_id and sensor_sweep_rankings:
        for row in sensor_sweep_rankings:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("rig_id", "")).strip()
            if candidate_id:
                sensor_sweep_best_rig_id = candidate_id
                break
    sensor_sweep_best_heuristic_score = 0.0
    if sensor_sweep_rankings:
        first_row = sensor_sweep_rankings[0]
        if isinstance(first_row, dict):
            try:
                sensor_sweep_best_heuristic_score = float(first_row.get("heuristic_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                sensor_sweep_best_heuristic_score = 0.0
    if sensor_sweep_best_heuristic_score < 0.0:
        sensor_sweep_best_heuristic_score = 0.0

    log_scene = Path(args.log_scene).resolve()
    log_out_root = Path(args.log_replay_out_root).resolve()
    log_out_root.mkdir(parents=True, exist_ok=True)
    log_replay_run_id = f"{safe_name(args.release_id)}_LOG_REPLAY"
    log_cmd = [
        args.python_bin,
        str(Path(args.log_replay_runner).resolve()),
        "--python-bin",
        args.python_bin,
        "--log-scene",
        str(log_scene),
        "--run-id",
        log_replay_run_id,
        "--out",
        str(log_out_root),
    ]
    run_cmd(log_cmd)
    log_replay_manifest_path = log_out_root / log_replay_run_id / "log_replay_manifest.json"

    map_simple = Path(args.map_simple).resolve()
    map_canonical = Path(args.map_canonical_out).resolve()
    map_validate_report = Path(args.map_validate_report_out).resolve()
    map_canonical.parent.mkdir(parents=True, exist_ok=True)
    map_validate_report.parent.mkdir(parents=True, exist_ok=True)

    map_convert_cmd = [
        args.python_bin,
        str(Path(args.map_convert_runner).resolve()),
        "--input",
        str(map_simple),
        "--to-format",
        "canonical",
        "--out",
        str(map_canonical),
    ]
    run_cmd(map_convert_cmd)

    map_validate_cmd = [
        args.python_bin,
        str(Path(args.map_validate_runner).resolve()),
        "--map",
        str(map_canonical),
        "--report-out",
        str(map_validate_report),
    ]
    run_cmd(map_validate_cmd)
    map_validate_payload_raw = json.loads(map_validate_report.read_text(encoding="utf-8"))
    map_validate_payload = map_validate_payload_raw if isinstance(map_validate_payload_raw, dict) else {}
    map_validate_routing_summary_raw = map_validate_payload.get("routing_semantic_summary", {})
    map_validate_routing_summary = (
        map_validate_routing_summary_raw if isinstance(map_validate_routing_summary_raw, dict) else {}
    )

    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    non_reciprocal_predecessor_warning_count = _as_int(
        map_validate_routing_summary.get("non_reciprocal_predecessor_warning_count", 0)
    )
    non_reciprocal_successor_warning_count = _as_int(
        map_validate_routing_summary.get("non_reciprocal_successor_warning_count", 0)
    )
    non_reciprocal_link_warning_count = (
        non_reciprocal_predecessor_warning_count + non_reciprocal_successor_warning_count
    )
    map_route_runner = Path(args.map_route_runner).resolve()
    map_route_report = Path(args.map_route_report_out).resolve()
    map_route_report.parent.mkdir(parents=True, exist_ok=True)
    map_route_cmd = [
        args.python_bin,
        str(map_route_runner),
        "--map",
        str(map_canonical),
        "--cost-mode",
        str(args.map_route_cost_mode).strip() or "hops",
        "--report-out",
        str(map_route_report),
    ]
    map_route_entry_lane_id_input = str(args.map_route_entry_lane_id).strip()
    if map_route_entry_lane_id_input:
        map_route_cmd.extend(["--entry-lane-id", map_route_entry_lane_id_input])
    map_route_exit_lane_id_input = str(args.map_route_exit_lane_id).strip()
    if map_route_exit_lane_id_input:
        map_route_cmd.extend(["--exit-lane-id", map_route_exit_lane_id_input])
    map_route_via_lane_ids_input: list[str] = []
    for raw_via_lane_id in args.map_route_via_lane_id:
        via_lane_id = str(raw_via_lane_id).strip()
        if via_lane_id:
            map_route_via_lane_ids_input.append(via_lane_id)
            map_route_cmd.extend(["--via-lane-id", via_lane_id])
    run_cmd(map_route_cmd)
    map_route_payload_raw = json.loads(map_route_report.read_text(encoding="utf-8"))
    map_route_payload = map_route_payload_raw if isinstance(map_route_payload_raw, dict) else {}
    map_route_via_lane_ids_payload_raw = map_route_payload.get("via_lane_ids_input", [])
    map_route_via_lane_ids: list[str] = []
    if isinstance(map_route_via_lane_ids_payload_raw, list):
        for item in map_route_via_lane_ids_payload_raw:
            via_lane_id = str(item).strip()
            if via_lane_id:
                map_route_via_lane_ids.append(via_lane_id)

    return {
        "enabled": True,
        "sensor_bridge_world_state": str(sensor_world),
        "sensor_bridge_rig": str(sensor_rig),
        "sensor_bridge_out": str(sensor_out),
        "sensor_bridge_fidelity_tier_input": sensor_fidelity_tier_input,
        "sensor_fidelity_tier": sensor_fidelity_tier,
        "sensor_fidelity_tier_score": float(sensor_fidelity_tier_score),
        "sensor_frame_count": int(sensor_frame_count),
        "sensor_stream_modality_counts": {
            key: sensor_stream_modality_counts[key] for key in sorted(sensor_stream_modality_counts.keys())
        },
        "sensor_quality_summary": sensor_quality_summary,
        "sensor_sweep_runner": str(sensor_sweep_runner),
        "sensor_sweep_candidates": str(sensor_sweep_candidates),
        "sensor_sweep_out": str(sensor_sweep_out),
        "sensor_sweep_fidelity_tier": sensor_sweep_fidelity_tier,
        "sensor_sweep_candidate_count": int(sensor_sweep_candidate_count),
        "sensor_sweep_best_rig_id": sensor_sweep_best_rig_id,
        "sensor_sweep_best_heuristic_score": float(sensor_sweep_best_heuristic_score),
        "log_scene": str(log_scene),
        "log_replay_run_id": log_replay_run_id,
        "log_replay_out_root": str(log_out_root),
        "log_replay_manifest_path": str(log_replay_manifest_path.resolve()),
        "map_simple": str(map_simple),
        "map_canonical_out": str(map_canonical),
        "map_validate_report_out": str(map_validate_report),
        "map_validate_error_count": _as_int(map_validate_payload.get("error_count", 0)),
        "map_validate_warning_count": _as_int(map_validate_payload.get("warning_count", 0)),
        "map_validate_routing_semantic_status": str(
            map_validate_routing_summary.get("routing_semantic_status", "")
        ).strip(),
        "map_validate_routing_semantic_warning_count": _as_int(
            map_validate_routing_summary.get("routing_semantic_warning_count", 0)
        ),
        "map_validate_unreachable_lane_count": _as_int(
            map_validate_routing_summary.get("unreachable_lane_count", 0)
        ),
        "map_validate_non_reciprocal_link_warning_count": int(non_reciprocal_link_warning_count),
        "map_validate_continuity_gap_warning_count": _as_int(
            map_validate_routing_summary.get("continuity_gap_warning_count", 0)
        ),
        "map_validate_entry_lane_missing_warning_count": _as_int(
            map_validate_routing_summary.get("entry_lane_missing_warning_count", 0)
        ),
        "map_route_runner": str(map_route_runner),
        "map_route_report_out": str(map_route_report),
        "map_route_cost_mode_input": str(args.map_route_cost_mode).strip(),
        "map_route_entry_lane_id_input": map_route_entry_lane_id_input,
        "map_route_exit_lane_id_input": map_route_exit_lane_id_input,
        "map_route_via_lane_ids_input": map_route_via_lane_ids_input,
        "map_route_cost_mode": str(map_route_payload.get("route_cost_mode", "")).strip(),
        "map_route_cost_value": map_route_payload.get("route_cost_value", 0),
        "map_route_status": str(map_route_payload.get("route_status", "")).strip(),
        "map_route_lane_count": int(map_route_payload.get("route_lane_count", 0) or 0),
        "map_route_hop_count": int(map_route_payload.get("route_hop_count", 0) or 0),
        "map_route_total_length_m": float(map_route_payload.get("route_total_length_m", 0.0) or 0.0),
        "map_route_segment_count": int(map_route_payload.get("route_segment_count", 0) or 0),
        "map_route_entry_lane_id": str(map_route_payload.get("selected_entry_lane_id", "")).strip(),
        "map_route_exit_lane_id": str(map_route_payload.get("selected_exit_lane_id", "")).strip(),
        "map_route_via_lane_ids": map_route_via_lane_ids,
    }


def run_phase3_hooks(
    args: argparse.Namespace,
    *,
    batch_root: Path,
    report_summary_files: list[Path],
    db_path: Path,
) -> dict[str, Any]:
    log_scene = Path(args.log_scene).resolve()
    neural_scene_runner = Path(args.neural_scene_runner).resolve()
    neural_scene_out = Path(args.neural_scene_out).resolve()
    neural_scene_out.parent.mkdir(parents=True, exist_ok=True)
    neural_cmd = [
        args.python_bin,
        str(neural_scene_runner),
        "--log-scene",
        str(log_scene),
        "--out",
        str(neural_scene_out),
    ]
    run_cmd(neural_cmd)

    neural_render_runner = Path(args.neural_render_runner).resolve()
    neural_render_sensor_rig = Path(args.neural_render_sensor_rig).resolve()
    neural_render_out = Path(args.neural_render_out).resolve()
    neural_render_out.parent.mkdir(parents=True, exist_ok=True)
    neural_render_cmd = [
        args.python_bin,
        str(neural_render_runner),
        "--neural-scene",
        str(neural_scene_out),
        "--sensor-rig",
        str(neural_render_sensor_rig),
        "--out",
        str(neural_render_out),
    ]
    run_cmd(neural_render_cmd)

    sim_runtime = str(args.sim_runtime).strip().lower()
    sim_runtime_adapter: dict[str, Any] = {
        "enabled": False,
        "runtime": sim_runtime if sim_runtime else "none",
    }
    sim_runtime_probe: dict[str, Any] = {
        "enabled": False,
        "runtime": sim_runtime if sim_runtime else "none",
    }
    sim_runtime_scenario_contract: dict[str, Any] = {
        "enabled": False,
        "runtime": sim_runtime if sim_runtime else "none",
    }
    sim_runtime_scene_result: dict[str, Any] = {
        "enabled": False,
        "runtime": sim_runtime if sim_runtime else "none",
    }
    sim_runtime_interop_contract: dict[str, Any] = {
        "enabled": False,
        "runtime": sim_runtime if sim_runtime else "none",
    }
    if sim_runtime != "none":
        sim_runtime_adapter_runner = Path(args.sim_runtime_adapter_runner).resolve()
        sim_runtime_scene_text = str(args.sim_runtime_scene).strip()
        sim_runtime_scene = Path(sim_runtime_scene_text).resolve() if sim_runtime_scene_text else log_scene
        sim_runtime_sensor_rig_text = str(args.sim_runtime_sensor_rig).strip()
        sim_runtime_sensor_rig = (
            Path(sim_runtime_sensor_rig_text).resolve() if sim_runtime_sensor_rig_text else neural_render_sensor_rig
        )
        sim_runtime_out = Path(args.sim_runtime_out).resolve()
        sim_runtime_out.parent.mkdir(parents=True, exist_ok=True)
        sim_runtime_mode = str(args.sim_runtime_mode).strip().lower()
        sim_runtime_cmd = [
            args.python_bin,
            str(sim_runtime_adapter_runner),
            "--runtime",
            sim_runtime,
            "--scene",
            str(sim_runtime_scene),
            "--sensor-rig",
            str(sim_runtime_sensor_rig),
            "--mode",
            sim_runtime_mode,
            "--out",
            str(sim_runtime_out),
        ]
        run_cmd(sim_runtime_cmd)
        sim_runtime_payload_raw = json.loads(sim_runtime_out.read_text(encoding="utf-8"))
        sim_runtime_payload = sim_runtime_payload_raw if isinstance(sim_runtime_payload_raw, dict) else {}
        try:
            sim_runtime_render_frame_count = int(sim_runtime_payload.get("render_frame_count", 0) or 0)
        except (TypeError, ValueError):
            sim_runtime_render_frame_count = 0
        try:
            sim_runtime_simulated_fps = float(sim_runtime_payload.get("simulated_fps", 0.0) or 0.0)
        except (TypeError, ValueError):
            sim_runtime_simulated_fps = 0.0
        try:
            sim_runtime_sensor_count = int(sim_runtime_payload.get("sensor_count", 0) or 0)
        except (TypeError, ValueError):
            sim_runtime_sensor_count = 0
        try:
            sim_runtime_actor_count = int(sim_runtime_payload.get("actor_count", 0) or 0)
        except (TypeError, ValueError):
            sim_runtime_actor_count = 0
        try:
            sim_runtime_estimated_scene_frame_count = int(
                sim_runtime_payload.get("estimated_scene_frame_count", 0) or 0
            )
        except (TypeError, ValueError):
            sim_runtime_estimated_scene_frame_count = 0
        sim_runtime_launch_manifest_out = str(sim_runtime_payload.get("launch_manifest_out", "")).strip()
        runtime_contract_raw = sim_runtime_payload.get("runtime_contract", {})
        runtime_contract = runtime_contract_raw if isinstance(runtime_contract_raw, dict) else {}
        sim_runtime_adapter = {
            "enabled": True,
            "runtime": str(sim_runtime_payload.get("runtime", "")).strip() or sim_runtime,
            "mode": str(sim_runtime_payload.get("mode", "")).strip() or sim_runtime_mode,
            "sim_runtime_adapter_runner": str(sim_runtime_adapter_runner),
            "scene": str(sim_runtime_scene),
            "sensor_rig": str(sim_runtime_sensor_rig),
            "out": str(sim_runtime_out),
            "launch_manifest_out": sim_runtime_launch_manifest_out,
            "sensor_count": sim_runtime_sensor_count,
            "actor_count": sim_runtime_actor_count,
            "render_frame_count": sim_runtime_render_frame_count,
            "simulated_fps": sim_runtime_simulated_fps,
            "estimated_scene_frame_count": sim_runtime_estimated_scene_frame_count,
            "runtime_entrypoint": str(runtime_contract.get("runtime_entrypoint", "")).strip(),
            "runtime_reference_repo": str(runtime_contract.get("reference_repo", "")).strip(),
            "runtime_bridge_contract": str(runtime_contract.get("bridge_contract", "")).strip(),
        }
        if bool(args.sim_runtime_probe_enable):
            sim_runtime_probe_runner = Path(args.sim_runtime_probe_runner).resolve()
            sim_runtime_probe_out = Path(args.sim_runtime_probe_out).resolve()
            sim_runtime_probe_out.parent.mkdir(parents=True, exist_ok=True)
            if not sim_runtime_launch_manifest_out:
                raise ValueError("sim-runtime adapter report missing launch_manifest_out")
            sim_runtime_launch_manifest_path = Path(sim_runtime_launch_manifest_out).resolve()
            sim_runtime_probe_cmd = [
                args.python_bin,
                str(sim_runtime_probe_runner),
                "--runtime",
                sim_runtime,
                "--launch-manifest",
                str(sim_runtime_launch_manifest_path),
                "--out",
                str(sim_runtime_probe_out),
            ]
            sim_runtime_probe_runtime_bin = str(args.sim_runtime_probe_runtime_bin).strip()
            if sim_runtime_probe_runtime_bin:
                sim_runtime_probe_cmd.extend(["--runtime-bin", sim_runtime_probe_runtime_bin])
            sim_runtime_probe_flag_requested = ""
            sim_runtime_probe_args_requested: list[str] = []
            sim_runtime_probe_args_requested_source = ""
            sim_runtime_probe_args_shlex = str(args.sim_runtime_probe_args_shlex).strip()
            if sim_runtime_probe_args_shlex:
                try:
                    sim_runtime_probe_args = shlex.split(sim_runtime_probe_args_shlex)
                except ValueError as exc:
                    raise ValueError(
                        "sim-runtime-probe-args-shlex must be valid shell-like arguments: "
                        f"{sim_runtime_probe_args_shlex}"
                    ) from exc
                for probe_arg in sim_runtime_probe_args:
                    probe_arg_text = str(probe_arg).strip()
                    if probe_arg_text:
                        sim_runtime_probe_args_requested.append(probe_arg_text)
                        sim_runtime_probe_cmd.append(f"--probe-arg={probe_arg_text}")
                if sim_runtime_probe_args_requested:
                    sim_runtime_probe_args_requested_source = "sim_runtime_probe_args_shlex"
            else:
                sim_runtime_probe_flag = str(args.sim_runtime_probe_flag).strip()
                sim_runtime_probe_flag_requested = sim_runtime_probe_flag
                if sim_runtime_probe_flag:
                    sim_runtime_probe_args_requested = [sim_runtime_probe_flag]
                    sim_runtime_probe_args_requested_source = "sim_runtime_probe_flag"
                    sim_runtime_probe_cmd.extend(["--probe-flag", sim_runtime_probe_flag])
            if bool(args.sim_runtime_probe_execute):
                sim_runtime_probe_cmd.append("--execute-probe")
            if bool(args.sim_runtime_probe_require_availability):
                sim_runtime_probe_cmd.append("--require-availability")
            run_cmd(sim_runtime_probe_cmd)
            sim_runtime_probe_payload_raw = json.loads(sim_runtime_probe_out.read_text(encoding="utf-8"))
            sim_runtime_probe_payload = (
                sim_runtime_probe_payload_raw if isinstance(sim_runtime_probe_payload_raw, dict) else {}
            )
            sim_runtime_probe_payload_args_raw = sim_runtime_probe_payload.get("probe_args", [])
            sim_runtime_probe_payload_args: list[str] = []
            if isinstance(sim_runtime_probe_payload_args_raw, list):
                for item in sim_runtime_probe_payload_args_raw:
                    item_text = str(item).strip()
                    if item_text:
                        sim_runtime_probe_payload_args.append(item_text)
            sim_runtime_probe_payload_args_source = str(sim_runtime_probe_payload.get("probe_args_source", "")).strip()
            sim_runtime_probe_payload_flag = str(sim_runtime_probe_payload.get("probe_flag", "")).strip()
            try:
                sim_runtime_probe_payload_timeout_sec = float(
                    sim_runtime_probe_payload.get("probe_timeout_sec", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                sim_runtime_probe_payload_timeout_sec = 0.0
            sim_runtime_probe = {
                "enabled": True,
                "runtime": str(sim_runtime_probe_payload.get("runtime", "")).strip() or sim_runtime,
                "sim_runtime_probe_runner": str(sim_runtime_probe_runner),
                "launch_manifest_path": str(sim_runtime_launch_manifest_path),
                "out": str(sim_runtime_probe_out),
                "runtime_bin": str(sim_runtime_probe_payload.get("runtime_bin", "")).strip(),
                "runtime_bin_resolved": str(sim_runtime_probe_payload.get("runtime_bin_resolved", "")).strip(),
                "runtime_bin_size_bytes": int(sim_runtime_probe_payload.get("runtime_bin_size_bytes", 0) or 0),
                "runtime_bin_mtime_utc": str(sim_runtime_probe_payload.get("runtime_bin_mtime_utc", "")).strip(),
                "runtime_bin_sha256": str(sim_runtime_probe_payload.get("runtime_bin_sha256", "")).strip(),
                "runtime_available": bool(sim_runtime_probe_payload.get("runtime_available", False)),
                "probe_executed": bool(sim_runtime_probe_payload.get("probe_executed", False)),
                "probe_flag_requested": sim_runtime_probe_flag_requested,
                "probe_args_requested": sim_runtime_probe_args_requested,
                "probe_args_requested_source": sim_runtime_probe_args_requested_source,
                "probe_flag": sim_runtime_probe_payload_flag,
                "probe_args": sim_runtime_probe_payload_args,
                "probe_args_source": sim_runtime_probe_payload_args_source,
                "probe_timeout_sec": float(sim_runtime_probe_payload_timeout_sec),
                "probe_command": str(sim_runtime_probe_payload.get("probe_command", "")).strip(),
                "probe_duration_ms": int(sim_runtime_probe_payload.get("probe_duration_ms", 0) or 0),
                "probe_returncode": int(sim_runtime_probe_payload.get("probe_returncode", 0) or 0),
                "probe_returncode_acceptable": bool(
                    sim_runtime_probe_payload.get("probe_returncode_acceptable", False)
                ),
                "require_availability": bool(sim_runtime_probe_payload.get("require_availability", False)),
                "runner_host": str(sim_runtime_probe_payload.get("runner_host", "")).strip(),
                "runner_platform": str(sim_runtime_probe_payload.get("runner_platform", "")).strip(),
                "runner_python": str(sim_runtime_probe_payload.get("runner_python", "")).strip(),
            }
        if bool(args.sim_runtime_scenario_contract_enable):
            sim_runtime_scenario_contract_runner = Path(args.sim_runtime_scenario_contract_runner).resolve()
            sim_runtime_scenario_contract_out = Path(args.sim_runtime_scenario_contract_out).resolve()
            sim_runtime_scenario_contract_out.parent.mkdir(parents=True, exist_ok=True)
            if not sim_runtime_launch_manifest_out:
                raise ValueError("sim-runtime adapter report missing launch_manifest_out")
            sim_runtime_launch_manifest_path = Path(sim_runtime_launch_manifest_out).resolve()
            sim_runtime_scenario_contract_cmd = [
                args.python_bin,
                str(sim_runtime_scenario_contract_runner),
                "--runtime",
                sim_runtime,
                "--launch-manifest",
                str(sim_runtime_launch_manifest_path),
                "--out",
                str(sim_runtime_scenario_contract_out),
            ]
            if bool(args.sim_runtime_probe_enable):
                sim_runtime_scenario_contract_cmd.extend(["--probe-report", str(Path(args.sim_runtime_probe_out).resolve())])
            if bool(args.sim_runtime_scenario_contract_require_runtime_ready):
                sim_runtime_scenario_contract_cmd.append("--require-runtime-ready")
            run_cmd(sim_runtime_scenario_contract_cmd)
            sim_runtime_scenario_contract_payload_raw = json.loads(
                sim_runtime_scenario_contract_out.read_text(encoding="utf-8")
            )
            sim_runtime_scenario_contract_payload = (
                sim_runtime_scenario_contract_payload_raw
                if isinstance(sim_runtime_scenario_contract_payload_raw, dict)
                else {}
            )
            sim_runtime_scenario_contract = {
                "enabled": True,
                "runtime": str(sim_runtime_scenario_contract_payload.get("runtime", "")).strip() or sim_runtime,
                "sim_runtime_scenario_contract_runner": str(sim_runtime_scenario_contract_runner),
                "launch_manifest_path": str(sim_runtime_launch_manifest_path),
                "probe_report_path": str(sim_runtime_scenario_contract_payload.get("probe_report_path", "")).strip(),
                "out": str(sim_runtime_scenario_contract_out),
                "require_runtime_ready": bool(
                    sim_runtime_scenario_contract_payload.get("require_runtime_ready", False)
                ),
                "runtime_ready": bool(sim_runtime_scenario_contract_payload.get("runtime_ready", False)),
                "scenario_contract_status": str(
                    sim_runtime_scenario_contract_payload.get("scenario_contract_status", "")
                ).strip(),
                "actor_count": int(sim_runtime_scenario_contract_payload.get("actor_count", 0) or 0),
                "sensor_stream_count": int(
                    sim_runtime_scenario_contract_payload.get("sensor_stream_count", 0) or 0
                ),
                "estimated_scene_frame_count": int(
                    sim_runtime_scenario_contract_payload.get("estimated_scene_frame_count", 0) or 0
                ),
                "executed_step_count": int(
                    sim_runtime_scenario_contract_payload.get("executed_step_count", 0) or 0
                ),
                "sim_duration_sec": float(sim_runtime_scenario_contract_payload.get("sim_duration_sec", 0.0) or 0.0),
            }
        if bool(args.sim_runtime_scene_result_enable):
            sim_runtime_scene_result_runner = Path(args.sim_runtime_scene_result_runner).resolve()
            sim_runtime_scene_result_out = Path(args.sim_runtime_scene_result_out).resolve()
            sim_runtime_scene_result_out.parent.mkdir(parents=True, exist_ok=True)
            if not sim_runtime_launch_manifest_out:
                raise ValueError("sim-runtime adapter report missing launch_manifest_out")
            if not bool(args.sim_runtime_scenario_contract_enable):
                raise ValueError("--sim-runtime-scene-result-enable requires --sim-runtime-scenario-contract-enable")
            sim_runtime_scenario_contract_report_path = Path(args.sim_runtime_scenario_contract_out).resolve()
            sim_runtime_launch_manifest_path = Path(sim_runtime_launch_manifest_out).resolve()
            sim_runtime_scene_result_cmd = [
                args.python_bin,
                str(sim_runtime_scene_result_runner),
                "--runtime",
                sim_runtime,
                "--launch-manifest",
                str(sim_runtime_launch_manifest_path),
                "--scenario-contract-report",
                str(sim_runtime_scenario_contract_report_path),
                "--out",
                str(sim_runtime_scene_result_out),
            ]
            if bool(args.sim_runtime_probe_enable):
                sim_runtime_scene_result_cmd.extend(["--probe-report", str(Path(args.sim_runtime_probe_out).resolve())])
            if bool(args.sim_runtime_scene_result_require_runtime_ready):
                sim_runtime_scene_result_cmd.append("--require-runtime-ready")
            run_cmd(sim_runtime_scene_result_cmd)
            sim_runtime_scene_result_payload_raw = json.loads(
                sim_runtime_scene_result_out.read_text(encoding="utf-8")
            )
            sim_runtime_scene_result_payload = (
                sim_runtime_scene_result_payload_raw
                if isinstance(sim_runtime_scene_result_payload_raw, dict)
                else {}
            )
            sim_runtime_scene_result = {
                "enabled": True,
                "runtime": str(sim_runtime_scene_result_payload.get("runtime", "")).strip() or sim_runtime,
                "sim_runtime_scene_result_runner": str(sim_runtime_scene_result_runner),
                "launch_manifest_path": str(sim_runtime_launch_manifest_path),
                "scenario_contract_report_path": str(
                    sim_runtime_scene_result_payload.get("scenario_contract_report_path", "")
                ).strip()
                or str(sim_runtime_scenario_contract_report_path),
                "probe_report_path": str(sim_runtime_scene_result_payload.get("probe_report_path", "")).strip(),
                "out": str(sim_runtime_scene_result_out),
                "require_runtime_ready": bool(
                    sim_runtime_scene_result_payload.get("require_runtime_ready", False)
                ),
                "runtime_ready": bool(sim_runtime_scene_result_payload.get("runtime_ready", False)),
                "scene_result_status": str(sim_runtime_scene_result_payload.get("scene_result_status", "")).strip(),
                "actor_count": int(sim_runtime_scene_result_payload.get("actor_count", 0) or 0),
                "sensor_stream_count": int(sim_runtime_scene_result_payload.get("sensor_stream_count", 0) or 0),
                "estimated_scene_frame_count": int(
                    sim_runtime_scene_result_payload.get("estimated_scene_frame_count", 0) or 0
                ),
                "executed_step_count": int(sim_runtime_scene_result_payload.get("executed_step_count", 0) or 0),
                "sim_duration_sec": float(sim_runtime_scene_result_payload.get("sim_duration_sec", 0.0) or 0.0),
                "coverage_ratio": float(sim_runtime_scene_result_payload.get("coverage_ratio", 0.0) or 0.0),
                "ego_travel_distance_m": float(
                    sim_runtime_scene_result_payload.get("ego_travel_distance_m", 0.0) or 0.0
                ),
            }
        if bool(args.sim_runtime_interop_contract_enable):
            sim_runtime_interop_contract_runner = Path(args.sim_runtime_interop_contract_runner).resolve()
            sim_runtime_interop_contract_out = Path(args.sim_runtime_interop_contract_out).resolve()
            sim_runtime_interop_contract_out.parent.mkdir(parents=True, exist_ok=True)
            if not sim_runtime_launch_manifest_out:
                raise ValueError("sim-runtime adapter report missing launch_manifest_out")
            sim_runtime_launch_manifest_path = Path(sim_runtime_launch_manifest_out).resolve()

            sim_runtime_interop_export_runner = Path(args.sim_runtime_interop_export_runner).resolve()
            sim_runtime_interop_export_out = Path(args.sim_runtime_interop_export_out).resolve()
            sim_runtime_interop_export_xosc_out = Path(args.sim_runtime_interop_export_xosc_out).resolve()
            sim_runtime_interop_export_xodr_out = Path(args.sim_runtime_interop_export_xodr_out).resolve()
            sim_runtime_interop_export_out.parent.mkdir(parents=True, exist_ok=True)
            sim_runtime_interop_export_xosc_out.parent.mkdir(parents=True, exist_ok=True)
            sim_runtime_interop_export_xodr_out.parent.mkdir(parents=True, exist_ok=True)

            sim_runtime_interop_export_cmd = [
                args.python_bin,
                str(sim_runtime_interop_export_runner),
                "--runtime",
                sim_runtime,
                "--launch-manifest",
                str(sim_runtime_launch_manifest_path),
                "--xosc-out",
                str(sim_runtime_interop_export_xosc_out),
                "--xodr-out",
                str(sim_runtime_interop_export_xodr_out),
                "--road-length-scale",
                str(args.sim_runtime_interop_export_road_length_scale),
                "--out",
                str(sim_runtime_interop_export_out),
            ]
            run_cmd(sim_runtime_interop_export_cmd)
            sim_runtime_interop_export_payload_raw = json.loads(
                sim_runtime_interop_export_out.read_text(encoding="utf-8")
            )
            sim_runtime_interop_export_payload = (
                sim_runtime_interop_export_payload_raw
                if isinstance(sim_runtime_interop_export_payload_raw, dict)
                else {}
            )
            sim_runtime_interop_import_runner = Path(args.sim_runtime_interop_import_runner).resolve()
            sim_runtime_interop_import_out = Path(args.sim_runtime_interop_import_out).resolve()
            sim_runtime_interop_import_out.parent.mkdir(parents=True, exist_ok=True)
            sim_runtime_interop_import_manifest_consistency_mode = (
                str(args.sim_runtime_interop_import_manifest_consistency_mode).strip().lower() or "require"
            )
            if sim_runtime_interop_import_manifest_consistency_mode not in {"require", "allow"}:
                raise ValueError(
                    "sim-runtime-interop-import-manifest-consistency-mode must be one of: require, allow"
                )
            sim_runtime_interop_import_export_consistency_mode = (
                str(args.sim_runtime_interop_import_export_consistency_mode).strip().lower() or "require"
            )
            if sim_runtime_interop_import_export_consistency_mode not in {"require", "allow"}:
                raise ValueError(
                    "sim-runtime-interop-import-export-consistency-mode must be one of: require, allow"
                )
            sim_runtime_interop_import_cmd = [
                args.python_bin,
                str(sim_runtime_interop_import_runner),
                "--runtime",
                sim_runtime,
                "--launch-manifest",
                str(sim_runtime_launch_manifest_path),
                "--xosc",
                str(sim_runtime_interop_export_xosc_out),
                "--xodr",
                str(sim_runtime_interop_export_xodr_out),
                "--export-report",
                str(sim_runtime_interop_export_out),
                "--out",
                str(sim_runtime_interop_import_out),
            ]
            if sim_runtime_interop_import_manifest_consistency_mode == "require":
                sim_runtime_interop_import_cmd.append("--require-manifest-consistency")
            if sim_runtime_interop_import_export_consistency_mode == "require":
                sim_runtime_interop_import_cmd.append("--require-export-consistency")
            run_cmd(sim_runtime_interop_import_cmd)
            sim_runtime_interop_import_payload_raw = json.loads(
                sim_runtime_interop_import_out.read_text(encoding="utf-8")
            )
            sim_runtime_interop_import_payload = (
                sim_runtime_interop_import_payload_raw
                if isinstance(sim_runtime_interop_import_payload_raw, dict)
                else {}
            )

            sim_runtime_interop_contract_xosc = Path(args.sim_runtime_interop_contract_xosc).resolve()
            sim_runtime_interop_contract_xodr = Path(args.sim_runtime_interop_contract_xodr).resolve()
            sim_runtime_interop_contract_cmd = [
                args.python_bin,
                str(sim_runtime_interop_contract_runner),
                "--runtime",
                sim_runtime,
                "--launch-manifest",
                str(sim_runtime_launch_manifest_path),
                "--xosc",
                str(sim_runtime_interop_contract_xosc),
                "--xodr",
                str(sim_runtime_interop_contract_xodr),
                "--out",
                str(sim_runtime_interop_contract_out),
            ]
            if bool(args.sim_runtime_probe_enable):
                sim_runtime_interop_contract_cmd.extend(["--probe-report", str(Path(args.sim_runtime_probe_out).resolve())])
            if bool(args.sim_runtime_interop_contract_require_runtime_ready):
                sim_runtime_interop_contract_cmd.append("--require-runtime-ready")
            run_cmd(sim_runtime_interop_contract_cmd)
            sim_runtime_interop_contract_payload_raw = json.loads(
                sim_runtime_interop_contract_out.read_text(encoding="utf-8")
            )
            sim_runtime_interop_contract_payload = (
                sim_runtime_interop_contract_payload_raw
                if isinstance(sim_runtime_interop_contract_payload_raw, dict)
                else {}
            )
            sim_runtime_interop_contract = {
                "enabled": True,
                "runtime": str(sim_runtime_interop_contract_payload.get("runtime", "")).strip() or sim_runtime,
                "sim_runtime_interop_contract_runner": str(sim_runtime_interop_contract_runner),
                "launch_manifest_path": str(sim_runtime_launch_manifest_path),
                "probe_report_path": str(sim_runtime_interop_contract_payload.get("probe_report_path", "")).strip(),
                "xosc_path": str(sim_runtime_interop_contract_payload.get("xosc_path", "")).strip()
                or str(sim_runtime_interop_contract_xosc),
                "xodr_path": str(sim_runtime_interop_contract_payload.get("xodr_path", "")).strip()
                or str(sim_runtime_interop_contract_xodr),
                "interop_export_runner": str(sim_runtime_interop_export_runner),
                "interop_export_out": str(sim_runtime_interop_export_out),
                "interop_export_xosc_path": str(
                    sim_runtime_interop_export_payload.get("xosc_path", "")
                ).strip()
                or str(sim_runtime_interop_export_xosc_out),
                "interop_export_xodr_path": str(
                    sim_runtime_interop_export_payload.get("xodr_path", "")
                ).strip()
                or str(sim_runtime_interop_export_xodr_out),
                "interop_export_status": str(
                    sim_runtime_interop_export_payload.get("export_status", "")
                ).strip(),
                "interop_import_runner": str(sim_runtime_interop_import_runner),
                "interop_import_out": str(sim_runtime_interop_import_out),
                "interop_import_manifest_consistency_mode": sim_runtime_interop_import_manifest_consistency_mode,
                "interop_import_export_consistency_mode": sim_runtime_interop_import_export_consistency_mode,
                "interop_import_require_manifest_consistency_input": (
                    sim_runtime_interop_import_manifest_consistency_mode == "require"
                ),
                "interop_import_require_export_consistency_input": (
                    sim_runtime_interop_import_export_consistency_mode == "require"
                ),
                "interop_import_xosc_path": str(
                    sim_runtime_interop_import_payload.get("xosc_path", "")
                ).strip()
                or str(sim_runtime_interop_export_xosc_out),
                "interop_import_xodr_path": str(
                    sim_runtime_interop_import_payload.get("xodr_path", "")
                ).strip()
                or str(sim_runtime_interop_export_xodr_out),
                "interop_import_status": str(
                    sim_runtime_interop_import_payload.get("import_status", "")
                ).strip(),
                "interop_import_manifest_consistent": bool(
                    sim_runtime_interop_import_payload.get("manifest_consistent", False)
                ),
                "interop_import_export_report_path": str(
                    sim_runtime_interop_import_payload.get("export_report_path", "")
                ).strip(),
                "interop_import_export_report_checked": bool(
                    sim_runtime_interop_import_payload.get("export_report_checked", False)
                ),
                "interop_import_export_consistent": bool(
                    sim_runtime_interop_import_payload.get("export_consistent", False)
                ),
                "interop_import_export_consistency_mismatch_reasons": (
                    [
                        str(item).strip()
                        for item in sim_runtime_interop_import_payload.get(
                            "export_consistency_mismatch_reasons",
                            [],
                        )
                        if str(item).strip()
                    ]
                    if isinstance(
                        sim_runtime_interop_import_payload.get(
                            "export_consistency_mismatch_reasons",
                            [],
                        ),
                        list,
                    )
                    else []
                ),
                "interop_import_actor_count_manifest": int(
                    sim_runtime_interop_import_payload.get("actor_count_manifest", 0) or 0
                ),
                "interop_import_xosc_entity_count": int(
                    sim_runtime_interop_import_payload.get("xosc_entity_count", 0) or 0
                ),
                "interop_import_xodr_road_count": int(
                    sim_runtime_interop_import_payload.get("xodr_road_count", 0) or 0
                ),
                "interop_import_xodr_total_road_length_m": float(
                    sim_runtime_interop_import_payload.get("xodr_total_road_length_m", 0.0) or 0.0
                ),
                "out": str(sim_runtime_interop_contract_out),
                "require_runtime_ready": bool(
                    sim_runtime_interop_contract_payload.get("require_runtime_ready", False)
                ),
                "runtime_ready": bool(sim_runtime_interop_contract_payload.get("runtime_ready", False)),
                "interop_contract_status": str(
                    sim_runtime_interop_contract_payload.get("interop_contract_status", "")
                ).strip(),
                "imported_actor_count": int(sim_runtime_interop_contract_payload.get("imported_actor_count", 0) or 0),
                "xosc_entity_count": int(sim_runtime_interop_contract_payload.get("xosc_entity_count", 0) or 0),
                "xodr_road_count": int(sim_runtime_interop_contract_payload.get("xodr_road_count", 0) or 0),
                "executed_step_count": int(
                    sim_runtime_interop_contract_payload.get("executed_step_count", 0) or 0
                ),
                "sim_duration_sec": float(sim_runtime_interop_contract_payload.get("sim_duration_sec", 0.0) or 0.0),
            }

    vehicle_dynamics_runner = Path(args.vehicle_dynamics_runner).resolve()
    vehicle_profile = Path(args.vehicle_profile).resolve()
    control_sequence = Path(args.control_sequence).resolve()
    vehicle_dynamics_out = Path(args.vehicle_dynamics_out).resolve()
    vehicle_dynamics_out.parent.mkdir(parents=True, exist_ok=True)
    vehicle_cmd = [
        args.python_bin,
        str(vehicle_dynamics_runner),
        "--vehicle-profile",
        str(vehicle_profile),
        "--control-sequence",
        str(control_sequence),
        "--out",
        str(vehicle_dynamics_out),
    ]
    run_cmd(vehicle_cmd)
    vehicle_payload_raw = json.loads(vehicle_dynamics_out.read_text(encoding="utf-8"))
    vehicle_payload = vehicle_payload_raw if isinstance(vehicle_payload_raw, dict) else {}

    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    road_grade_values: list[float] = []
    grade_force_values: list[float] = []
    heading_values: list[float] = []
    lateral_position_values: list[float] = []
    yaw_rate_values: list[float] = []
    lateral_velocity_values: list[float] = []
    accel_values: list[float] = []
    lateral_accel_values: list[float] = []
    yaw_accel_values: list[float] = []
    jerk_values: list[float] = []
    lateral_jerk_values: list[float] = []
    yaw_jerk_values: list[float] = []
    speed_tracking_error_values: list[float] = []
    trace_raw = vehicle_payload.get("trace", [])
    if isinstance(trace_raw, list):
        for row_raw in trace_raw:
            if not isinstance(row_raw, dict):
                continue
            road_grade_values.append(_as_float(row_raw.get("road_grade_percent", 0.0)))
            grade_force_values.append(_as_float(row_raw.get("grade_force_n", 0.0)))
            heading_values.append(_as_float(row_raw.get("heading_deg", 0.0)))
            lateral_position_values.append(_as_float(row_raw.get("y_m", 0.0)))
            yaw_rate_values.append(_as_float(row_raw.get("yaw_rate_rps", 0.0)))
            lateral_velocity_values.append(_as_float(row_raw.get("lateral_velocity_mps", 0.0)))
            accel_values.append(_as_float(row_raw.get("accel_mps2", 0.0)))
            lateral_accel_values.append(_as_float(row_raw.get("lateral_accel_mps2", 0.0)))
            yaw_accel_values.append(_as_float(row_raw.get("yaw_accel_rps2", 0.0)))
            speed_tracking_error_raw = row_raw.get("speed_tracking_error_mps")
            if speed_tracking_error_raw is not None:
                speed_tracking_error_values.append(_as_float(speed_tracking_error_raw, 0.0))
            else:
                target_speed_raw = row_raw.get("target_speed_mps")
                if target_speed_raw is not None:
                    speed_tracking_error_values.append(
                        _as_float(row_raw.get("speed_mps", 0.0), 0.0) - _as_float(target_speed_raw, 0.0)
                    )
    control_dt_sec = 0.0
    control_commands: list[dict[str, Any]] = []
    try:
        control_payload_raw = json.loads(control_sequence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        control_payload_raw = {}
    if isinstance(control_payload_raw, dict):
        control_dt_sec = _as_float(control_payload_raw.get("dt_sec", 0.0))
        commands_raw = control_payload_raw.get("commands", [])
        if isinstance(commands_raw, list):
            for command_raw in commands_raw:
                if isinstance(command_raw, dict):
                    control_commands.append(command_raw)
    throttle_values: list[float] = []
    brake_values: list[float] = []
    steering_angle_values: list[float] = []
    for command in control_commands:
        throttle_values.append(max(0.0, min(1.0, _as_float(command.get("throttle", 0.0)))))
        brake_values.append(max(0.0, min(1.0, _as_float(command.get("brake", 0.0)))))
        steering_angle_values.append(_as_float(command.get("steering_angle_deg", 0.0)))
    control_command_step_count = len(control_commands)
    control_throttle_brake_overlap_step_count = sum(
        1
        for throttle, brake in zip(throttle_values, brake_values)
        if throttle > 0.0 and brake > 0.0
    )
    control_throttle_brake_overlap_ratio = (
        float(control_throttle_brake_overlap_step_count) / float(control_command_step_count)
        if control_command_step_count > 0
        else 0.0
    )
    control_max_throttle_plus_brake = max(
        (throttle + brake for throttle, brake in zip(throttle_values, brake_values)),
        default=0.0,
    )
    control_max_abs_steering_rate_degps = 0.0
    control_max_abs_throttle_rate_per_sec = 0.0
    control_max_abs_brake_rate_per_sec = 0.0
    if control_dt_sec > 0.0:
        for idx in range(1, len(steering_angle_values)):
            control_max_abs_steering_rate_degps = max(
                control_max_abs_steering_rate_degps,
                abs((steering_angle_values[idx] - steering_angle_values[idx - 1]) / control_dt_sec),
            )
        for idx in range(1, len(throttle_values)):
            control_max_abs_throttle_rate_per_sec = max(
                control_max_abs_throttle_rate_per_sec,
                abs((throttle_values[idx] - throttle_values[idx - 1]) / control_dt_sec),
            )
        for idx in range(1, len(brake_values)):
            control_max_abs_brake_rate_per_sec = max(
                control_max_abs_brake_rate_per_sec,
                abs((brake_values[idx] - brake_values[idx - 1]) / control_dt_sec),
            )
    if control_dt_sec > 0.0:
        for idx in range(1, len(accel_values)):
            jerk_values.append((accel_values[idx] - accel_values[idx - 1]) / control_dt_sec)
        for idx in range(1, len(lateral_accel_values)):
            lateral_jerk_values.append(
                (lateral_accel_values[idx] - lateral_accel_values[idx - 1]) / control_dt_sec
            )
        for idx in range(1, len(yaw_accel_values)):
            yaw_jerk_values.append((yaw_accel_values[idx] - yaw_accel_values[idx - 1]) / control_dt_sec)
    min_road_grade_percent = min(road_grade_values) if road_grade_values else 0.0
    avg_road_grade_percent = (
        sum(road_grade_values) / float(len(road_grade_values)) if road_grade_values else 0.0
    )
    max_road_grade_percent = max(road_grade_values) if road_grade_values else 0.0
    max_abs_grade_force_n = max((abs(value) for value in grade_force_values), default=0.0)
    min_heading_deg = min(heading_values) if heading_values else 0.0
    avg_heading_deg = sum(heading_values) / float(len(heading_values)) if heading_values else 0.0
    max_heading_deg = max(heading_values) if heading_values else 0.0
    min_lateral_position_m = min(lateral_position_values) if lateral_position_values else 0.0
    avg_lateral_position_m = (
        sum(lateral_position_values) / float(len(lateral_position_values))
        if lateral_position_values
        else 0.0
    )
    max_lateral_position_m = max(lateral_position_values) if lateral_position_values else 0.0
    max_abs_lateral_position_m = max((abs(value) for value in lateral_position_values), default=0.0)
    max_abs_yaw_rate_rps = max((abs(value) for value in yaw_rate_values), default=0.0)
    max_abs_lateral_velocity_mps = max((abs(value) for value in lateral_velocity_values), default=0.0)
    max_abs_accel_mps2 = max((abs(value) for value in accel_values), default=0.0)
    max_abs_lateral_accel_mps2 = max((abs(value) for value in lateral_accel_values), default=0.0)
    max_abs_yaw_accel_rps2 = max((abs(value) for value in yaw_accel_values), default=0.0)
    max_abs_jerk_mps3 = max((abs(value) for value in jerk_values), default=0.0)
    max_abs_lateral_jerk_mps3 = max((abs(value) for value in lateral_jerk_values), default=0.0)
    max_abs_yaw_jerk_rps3 = max((abs(value) for value in yaw_jerk_values), default=0.0)
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
        max((abs(value) for value in speed_tracking_error_values), default=0.0)
    )

    vehicle_dynamics_summary = {
        "vehicle_dynamics_model": str(vehicle_payload.get("vehicle_dynamics_model", "")).strip(),
        "planar_kinematics_enabled": bool(vehicle_payload.get("planar_kinematics_enabled", False)),
        "dynamic_bicycle_enabled": bool(vehicle_payload.get("dynamic_bicycle_enabled", False)),
        "step_count": _as_int(vehicle_payload.get("step_count", 0)),
        "initial_speed_mps": _as_float(vehicle_payload.get("initial_speed_mps", 0.0)),
        "initial_position_m": _as_float(vehicle_payload.get("initial_position_m", 0.0)),
        "initial_heading_deg": _as_float(vehicle_payload.get("initial_heading_deg", 0.0)),
        "initial_lateral_position_m": _as_float(vehicle_payload.get("initial_lateral_position_m", 0.0)),
        "initial_lateral_velocity_mps": _as_float(vehicle_payload.get("initial_lateral_velocity_mps", 0.0)),
        "initial_yaw_rate_rps": _as_float(vehicle_payload.get("initial_yaw_rate_rps", 0.0)),
        "final_speed_mps": _as_float(vehicle_payload.get("final_speed_mps", 0.0)),
        "final_position_m": _as_float(vehicle_payload.get("final_position_m", 0.0)),
        "final_heading_deg": _as_float(vehicle_payload.get("final_heading_deg", 0.0)),
        "final_lateral_position_m": _as_float(vehicle_payload.get("final_lateral_position_m", 0.0)),
        "final_lateral_velocity_mps": _as_float(vehicle_payload.get("final_lateral_velocity_mps", 0.0)),
        "final_yaw_rate_rps": _as_float(vehicle_payload.get("final_yaw_rate_rps", 0.0)),
        "min_heading_deg": float(min_heading_deg),
        "avg_heading_deg": float(avg_heading_deg),
        "max_heading_deg": float(max_heading_deg),
        "min_lateral_position_m": float(min_lateral_position_m),
        "avg_lateral_position_m": float(avg_lateral_position_m),
        "max_lateral_position_m": float(max_lateral_position_m),
        "max_abs_lateral_position_m": float(max_abs_lateral_position_m),
        "max_abs_yaw_rate_rps": float(max_abs_yaw_rate_rps),
        "max_abs_lateral_velocity_mps": float(max_abs_lateral_velocity_mps),
        "max_abs_accel_mps2": float(max_abs_accel_mps2),
        "max_abs_lateral_accel_mps2": float(max_abs_lateral_accel_mps2),
        "max_abs_yaw_accel_rps2": float(max_abs_yaw_accel_rps2),
        "max_abs_jerk_mps3": float(max_abs_jerk_mps3),
        "max_abs_lateral_jerk_mps3": float(max_abs_lateral_jerk_mps3),
        "max_abs_yaw_jerk_rps3": float(max_abs_yaw_jerk_rps3),
        "min_road_grade_percent": float(min_road_grade_percent),
        "avg_road_grade_percent": float(avg_road_grade_percent),
        "max_road_grade_percent": float(max_road_grade_percent),
        "max_abs_grade_force_n": float(max_abs_grade_force_n),
        "control_command_step_count": int(control_command_step_count),
        "control_throttle_brake_overlap_step_count": int(control_throttle_brake_overlap_step_count),
        "control_throttle_brake_overlap_ratio": float(control_throttle_brake_overlap_ratio),
        "control_max_abs_steering_rate_degps": float(control_max_abs_steering_rate_degps),
        "control_max_abs_throttle_rate_per_sec": float(control_max_abs_throttle_rate_per_sec),
        "control_max_abs_brake_rate_per_sec": float(control_max_abs_brake_rate_per_sec),
        "control_max_throttle_plus_brake": float(control_max_throttle_plus_brake),
        "speed_tracking_target_step_count": int(speed_tracking_target_step_count),
        "speed_tracking_error_mps_min": float(speed_tracking_error_mps_min),
        "speed_tracking_error_mps_avg": float(speed_tracking_error_mps_avg),
        "speed_tracking_error_mps_max": float(speed_tracking_error_mps_max),
        "speed_tracking_error_abs_mps_avg": float(speed_tracking_error_abs_mps_avg),
        "speed_tracking_error_abs_mps_max": float(speed_tracking_error_abs_mps_max),
    }

    dataset_manifest_out = Path(args.dataset_manifest_out).resolve()
    dataset_manifest_out.parent.mkdir(parents=True, exist_ok=True)
    dataset_manifest_runner = Path(args.dataset_manifest_runner).resolve()
    dataset_id = str(args.dataset_id).strip() or f"{safe_name(args.release_id)}_DATASET"

    build_cmd = [
        args.python_bin,
        str(dataset_manifest_runner),
        "--summary-root",
        str(batch_root.resolve()),
        "--dataset-id",
        dataset_id,
        "--out",
        str(dataset_manifest_out),
    ]
    for summary_path in report_summary_files:
        build_cmd.extend(["--release-summary-file", str(summary_path.resolve())])
    run_cmd(build_cmd)

    dataset_ingest_cmd = [
        args.python_bin,
        str(Path(args.ingest_runner).resolve()),
        "--dataset-manifest-file",
        str(dataset_manifest_out),
        "--db",
        str(db_path.resolve()),
    ]
    run_cmd(dataset_ingest_cmd)
    dataset_traffic_diversity = summarize_phase3_dataset_traffic_diversity(
        batch_root=batch_root,
        dataset_manifest_out=dataset_manifest_out,
    )
    lane_risk_summary = summarize_phase3_lane_risk_from_release_summaries(
        release_summary_files=report_summary_files,
    )
    phase3_core_sim_runner = Path(args.phase3_core_sim_runner).resolve()
    phase3_core_sim_scenario_text = str(args.phase3_core_sim_scenario).strip()
    phase3_core_sim_scenario_input = (
        Path(phase3_core_sim_scenario_text).resolve()
        if phase3_core_sim_scenario_text
        else log_scene
    )
    phase3_core_sim_out_root = Path(args.phase3_core_sim_out_root).resolve()
    phase3_core_sim_out_root.mkdir(parents=True, exist_ok=True)
    phase3_core_sim_run_id_input = str(args.phase3_core_sim_run_id).strip()
    phase3_core_sim_run_id = (
        phase3_core_sim_run_id_input
        if phase3_core_sim_run_id_input
        else f"{safe_name(batch_root.name)}_PHASE3_CORE_SIM"
    )
    phase3_core_sim_scenario = phase3_core_sim_scenario_input
    phase3_core_sim_scenario_generated = False
    try:
        phase3_core_sim_scenario_payload_raw = json.loads(
            phase3_core_sim_scenario_input.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        phase3_core_sim_scenario_payload_raw = {}
    if isinstance(phase3_core_sim_scenario_payload_raw, dict):
        phase3_core_sim_scenario_schema = str(
            phase3_core_sim_scenario_payload_raw.get("scenario_schema_version", "")
        ).strip()
        phase3_core_sim_log_scene_schema = str(
            phase3_core_sim_scenario_payload_raw.get("log_scene_schema_version", "")
        ).strip()
        if phase3_core_sim_scenario_schema != "scenario_definition_v0" and phase3_core_sim_log_scene_schema:
            phase3_core_sim_scenario = (
                phase3_core_sim_out_root
                / f"{safe_name(phase3_core_sim_run_id)}.scenario_definition_v0.json"
            ).resolve()
            phase3_core_sim_scenario_generated = True
            ego_initial_speed_mps = _as_float(
                phase3_core_sim_scenario_payload_raw.get("ego_initial_speed_mps", 10.0),
                10.0,
            )
            lead_vehicle_initial_gap_m = _as_float(
                phase3_core_sim_scenario_payload_raw.get("lead_vehicle_initial_gap_m", 40.0),
                40.0,
            )
            lead_vehicle_speed_mps = _as_float(
                phase3_core_sim_scenario_payload_raw.get(
                    "lead_vehicle_speed_mps",
                    max(0.0, ego_initial_speed_mps - 2.0),
                ),
                max(0.0, ego_initial_speed_mps - 2.0),
            )
            phase3_core_sim_scenario_payload = {
                "scenario_schema_version": "scenario_definition_v0",
                "scenario_id": str(
                    phase3_core_sim_scenario_payload_raw.get("log_id", phase3_core_sim_run_id)
                ).strip()
                or phase3_core_sim_run_id,
                "duration_sec": max(
                    0.1,
                    _as_float(phase3_core_sim_scenario_payload_raw.get("duration_sec", 5.0), 5.0),
                ),
                "dt_sec": max(0.01, _as_float(phase3_core_sim_scenario_payload_raw.get("dt_sec", 0.1), 0.1)),
                "ego": {
                    "actor_id": "ego",
                    "position_m": 0.0,
                    "speed_mps": max(0.0, ego_initial_speed_mps),
                },
                "npcs": [
                    {
                        "actor_id": "npc_1",
                        "position_m": max(1.0, lead_vehicle_initial_gap_m),
                        "speed_mps": max(0.0, lead_vehicle_speed_mps),
                    }
                ],
            }
            phase3_core_sim_scenario.write_text(
                json.dumps(phase3_core_sim_scenario_payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
    phase3_core_sim_cmd = [
        args.python_bin,
        str(phase3_core_sim_runner),
        "--scenario",
        str(phase3_core_sim_scenario),
        "--run-id",
        phase3_core_sim_run_id,
        "--out",
        str(phase3_core_sim_out_root),
    ]
    if bool(args.phase3_enable_ego_collision_avoidance):
        phase3_core_sim_cmd.extend(["--enable-ego-collision-avoidance", "true"])
    if float(args.phase3_avoidance_ttc_threshold_sec) > 0.0:
        phase3_core_sim_cmd.extend(
            ["--avoidance-ttc-threshold-sec", str(args.phase3_avoidance_ttc_threshold_sec)]
        )
    if float(args.phase3_ego_max_brake_mps2) > 0.0:
        phase3_core_sim_cmd.extend(["--ego-max-brake-mps2", str(args.phase3_ego_max_brake_mps2)])
    if float(args.phase3_tire_friction_coeff) > 0.0:
        phase3_core_sim_cmd.extend(["--tire-friction-coeff", str(args.phase3_tire_friction_coeff)])
    if float(args.phase3_surface_friction_scale) > 0.0:
        phase3_core_sim_cmd.extend(
            ["--surface-friction-scale", str(args.phase3_surface_friction_scale)]
        )
    run_cmd(phase3_core_sim_cmd)
    phase3_core_sim_summary_path = (
        phase3_core_sim_out_root / phase3_core_sim_run_id / "summary.json"
    )
    if not phase3_core_sim_summary_path.exists():
        raise FileNotFoundError(
            "phase3 core sim summary not found: "
            f"{phase3_core_sim_summary_path}"
        )
    phase3_core_sim_summary_raw = json.loads(
        phase3_core_sim_summary_path.read_text(encoding="utf-8")
    )
    phase3_core_sim_summary = (
        phase3_core_sim_summary_raw
        if isinstance(phase3_core_sim_summary_raw, dict)
        else {}
    )
    phase3_core_sim = {
        "enabled": True,
        "phase3_core_sim_runner": str(phase3_core_sim_runner),
        "phase3_core_sim_scenario_input": str(phase3_core_sim_scenario_input),
        "phase3_core_sim_scenario": str(phase3_core_sim_scenario),
        "phase3_core_sim_scenario_generated": bool(phase3_core_sim_scenario_generated),
        "phase3_core_sim_out_root": str(phase3_core_sim_out_root),
        "phase3_core_sim_run_id": phase3_core_sim_run_id,
        "phase3_core_sim_summary_path": str(phase3_core_sim_summary_path),
        "enable_ego_collision_avoidance": bool(
            phase3_core_sim_summary.get("enable_ego_collision_avoidance", False)
        ),
        "avoidance_ttc_threshold_sec": _as_float(
            phase3_core_sim_summary.get("avoidance_ttc_threshold_sec", 0.0)
        ),
        "ego_max_brake_mps2": _as_float(
            phase3_core_sim_summary.get("ego_max_brake_mps2", 0.0)
        ),
        "tire_friction_coeff": _as_float(
            phase3_core_sim_summary.get("tire_friction_coeff", 0.0)
        ),
        "surface_friction_scale": _as_float(
            phase3_core_sim_summary.get("surface_friction_scale", 0.0)
        ),
        "status": str(phase3_core_sim_summary.get("status", "")).strip(),
        "termination_reason": str(
            phase3_core_sim_summary.get("termination_reason", "")
        ).strip(),
        "collision": bool(phase3_core_sim_summary.get("collision", False)),
        "timeout": bool(phase3_core_sim_summary.get("timeout", False)),
        "min_ttc_same_lane_sec": phase3_core_sim_summary.get("min_ttc_same_lane_sec"),
        "min_ttc_adjacent_lane_sec": phase3_core_sim_summary.get("min_ttc_adjacent_lane_sec"),
        "min_ttc_any_lane_sec": phase3_core_sim_summary.get("min_ttc_any_lane_sec"),
        "ego_avoidance_brake_event_count": _as_int(
            phase3_core_sim_summary.get("ego_avoidance_brake_event_count", 0)
        ),
        "ego_avoidance_applied_brake_mps2_max": _as_float(
            phase3_core_sim_summary.get("ego_avoidance_applied_brake_mps2_max", 0.0)
        ),
    }
    phase3_core_sim_matrix: dict[str, Any] = {"enabled": False}
    if bool(args.phase3_core_sim_matrix_enable):
        phase3_core_sim_matrix_runner = Path(args.phase3_core_sim_matrix_runner).resolve()
        phase3_core_sim_matrix_out_root = Path(args.phase3_core_sim_matrix_out_root).resolve()
        phase3_core_sim_matrix_out_root.mkdir(parents=True, exist_ok=True)
        phase3_core_sim_matrix_report_out = Path(args.phase3_core_sim_matrix_report_out).resolve()
        phase3_core_sim_matrix_report_out.parent.mkdir(parents=True, exist_ok=True)
        phase3_core_sim_matrix_run_id_prefix = str(args.phase3_core_sim_matrix_run_id_prefix).strip()
        if not phase3_core_sim_matrix_run_id_prefix:
            phase3_core_sim_matrix_run_id_prefix = f"{safe_name(phase3_core_sim_run_id)}_MATRIX"
        phase3_core_sim_matrix_cmd = [
            args.python_bin,
            str(phase3_core_sim_matrix_runner),
            "--core-sim-runner",
            str(phase3_core_sim_runner),
            "--scenario",
            str(phase3_core_sim_scenario),
            "--out-root",
            str(phase3_core_sim_matrix_out_root),
            "--report-out",
            str(phase3_core_sim_matrix_report_out),
            "--run-id-prefix",
            phase3_core_sim_matrix_run_id_prefix,
            "--traffic-profile-ids",
            str(args.phase3_core_sim_matrix_traffic_profile_ids),
            "--traffic-actor-pattern-ids",
            str(args.phase3_core_sim_matrix_traffic_actor_pattern_ids),
            "--traffic-npc-speed-scale-values",
            str(args.phase3_core_sim_matrix_speed_scale_values),
            "--tire-friction-coeff-values",
            str(args.phase3_core_sim_matrix_tire_friction_values),
            "--surface-friction-scale-values",
            str(args.phase3_core_sim_matrix_surface_friction_values),
            "--max-cases",
            str(args.phase3_core_sim_matrix_limit),
            "--python-bin",
            str(args.python_bin),
        ]
        if bool(args.phase3_enable_ego_collision_avoidance):
            phase3_core_sim_matrix_cmd.extend(
                [
                    "--enable-ego-collision-avoidance",
                    "--avoidance-ttc-threshold-sec",
                    str(args.phase3_avoidance_ttc_threshold_sec),
                    "--ego-max-brake-mps2",
                    str(args.phase3_ego_max_brake_mps2),
                ]
            )
        run_cmd(phase3_core_sim_matrix_cmd)
        phase3_core_sim_matrix_payload_raw = json.loads(
            phase3_core_sim_matrix_report_out.read_text(encoding="utf-8")
        )
        phase3_core_sim_matrix_payload = (
            phase3_core_sim_matrix_payload_raw
            if isinstance(phase3_core_sim_matrix_payload_raw, dict)
            else {}
        )
        status_counts_raw = phase3_core_sim_matrix_payload.get("status_counts", {})
        status_counts = status_counts_raw if isinstance(status_counts_raw, dict) else {}
        returncode_counts_raw = phase3_core_sim_matrix_payload.get("returncode_counts", {})
        returncode_counts = returncode_counts_raw if isinstance(returncode_counts_raw, dict) else {}
        phase3_core_sim_matrix = {
            "enabled": True,
            "phase3_core_sim_matrix_runner": str(phase3_core_sim_matrix_runner),
            "phase3_core_sim_matrix_report_out": str(phase3_core_sim_matrix_report_out),
            "phase3_core_sim_matrix_out_root": str(phase3_core_sim_matrix_out_root),
            "phase3_core_sim_matrix_run_id_prefix": phase3_core_sim_matrix_run_id_prefix,
            "phase3_core_sim_matrix_schema_version": str(
                phase3_core_sim_matrix_payload.get("core_sim_matrix_sweep_schema_version", "")
            ).strip(),
            "case_count": _as_int(phase3_core_sim_matrix_payload.get("case_count", 0)),
            "success_case_count": _as_int(phase3_core_sim_matrix_payload.get("success_case_count", 0)),
            "failed_case_count": _as_int(phase3_core_sim_matrix_payload.get("failed_case_count", 0)),
            "all_cases_success": bool(phase3_core_sim_matrix_payload.get("all_cases_success", False)),
            "collision_case_count": _as_int(
                phase3_core_sim_matrix_payload.get("collision_case_count", 0)
            ),
            "timeout_case_count": _as_int(phase3_core_sim_matrix_payload.get("timeout_case_count", 0)),
            "min_ttc_same_lane_sec_min": phase3_core_sim_matrix_payload.get("min_ttc_same_lane_sec_min"),
            "lowest_ttc_same_lane_run_id": str(
                phase3_core_sim_matrix_payload.get("lowest_ttc_same_lane_run_id", "")
            ).strip(),
            "min_ttc_any_lane_sec_min": phase3_core_sim_matrix_payload.get("min_ttc_any_lane_sec_min"),
            "lowest_ttc_any_lane_run_id": str(
                phase3_core_sim_matrix_payload.get("lowest_ttc_any_lane_run_id", "")
            ).strip(),
            "status_counts": {
                str(key): _as_int(value)
                for key, value in status_counts.items()
                if str(key).strip()
            },
            "returncode_counts": {
                str(key): _as_int(value)
                for key, value in returncode_counts.items()
                if str(key).strip()
            },
        }

    return {
        "enabled": True,
        "log_scene": str(log_scene),
        "neural_scene_runner": str(neural_scene_runner),
        "neural_scene_out": str(neural_scene_out),
        "neural_render_runner": str(neural_render_runner),
        "neural_render_sensor_rig": str(neural_render_sensor_rig),
        "neural_render_out": str(neural_render_out),
        "sim_runtime_adapter": sim_runtime_adapter,
        "sim_runtime_probe": sim_runtime_probe,
        "sim_runtime_scenario_contract": sim_runtime_scenario_contract,
        "sim_runtime_scene_result": sim_runtime_scene_result,
        "sim_runtime_interop_contract": sim_runtime_interop_contract,
        "vehicle_dynamics_runner": str(vehicle_dynamics_runner),
        "vehicle_profile": str(vehicle_profile),
        "control_sequence": str(control_sequence),
        "vehicle_dynamics_out": str(vehicle_dynamics_out),
        "vehicle_dynamics": vehicle_dynamics_summary,
        "phase3_core_sim": phase3_core_sim,
        "phase3_core_sim_matrix": phase3_core_sim_matrix,
        "dataset_id": dataset_id,
        "dataset_manifest_runner": str(dataset_manifest_runner),
        "dataset_manifest_out": str(dataset_manifest_out),
        "dataset_manifest_ingested": True,
        "dataset_traffic_diversity": dataset_traffic_diversity,
        "lane_risk_summary": lane_risk_summary,
        "report_summary_file_count": len(report_summary_files),
    }


def run_phase4_hooks(
    args: argparse.Namespace,
    *,
    batch_root: Path,
    release_id: str,
    batch_id: str,
    overall_result: str,
    reports: list[dict[str, str]],
    report_summary_files: list[Path],
    phase2_hooks: dict[str, Any],
    phase3_hooks: dict[str, Any],
) -> dict[str, Any]:
    phase4_linkage_modules = resolve_phase4_linkage_modules(args.phase4_linkage_module)

    hil_sequence_runner = Path(args.hil_sequence_runner).resolve()
    hil_interface = Path(args.hil_interface).resolve()
    hil_sequence = Path(args.hil_sequence).resolve()
    hil_schedule_out = Path(args.hil_schedule_out).resolve()
    hil_schedule_out.parent.mkdir(parents=True, exist_ok=True)

    hil_cmd = [
        args.python_bin,
        str(hil_sequence_runner),
        "--interface",
        str(hil_interface),
        "--sequence",
        str(hil_sequence),
        "--max-runtime-sec",
        str(args.hil_max_runtime_sec),
        "--out",
        str(hil_schedule_out),
    ]
    run_cmd(hil_cmd)

    adp_trace_runner = Path(args.adp_trace_runner).resolve()
    adp_trace_out = Path(args.adp_trace_out).resolve()
    adp_trace_out.parent.mkdir(parents=True, exist_ok=True)
    adp_pipeline_context_path = (batch_root / "phase4_adp_pipeline_context.json").resolve()
    adp_pipeline_context_path.write_text(
        json.dumps(
            {
                "release_id": release_id,
                "batch_id": batch_id,
                "overall_result": overall_result,
                "phase2_hooks": {"enabled": bool(phase2_hooks.get("enabled"))},
                "phase3_hooks": {"enabled": bool(phase3_hooks.get("enabled"))},
                "phase4_hooks": {"enabled": True},
                "reports": [
                    {"sds_version": str(item.get("sds_version", "")).strip()}
                    for item in reports
                    if str(item.get("sds_version", "")).strip()
                ],
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    adp_cmd = [
        args.python_bin,
        str(adp_trace_runner),
        "--pipeline-manifest",
        str(adp_pipeline_context_path),
        "--out",
        str(adp_trace_out),
    ]
    for summary_path in report_summary_files:
        adp_cmd.extend(["--release-summary", str(summary_path.resolve())])
    run_cmd(adp_cmd)

    adp_payload = json.loads(adp_trace_out.read_text(encoding="utf-8"))
    user_responsibility = adp_payload.get("user_responsibility", {})
    adp_hooks: dict[str, Any] = {
        "enabled": True,
        "adp_trace_runner": str(adp_trace_runner),
        "adp_pipeline_context_path": str(adp_pipeline_context_path),
        "adp_trace_out": str(adp_trace_out),
        "release_summary_file_count": len(report_summary_files),
        "user_responsibility_requires_ack": bool(user_responsibility.get("requires_ack")),
        "user_responsibility_notice_count": int(user_responsibility.get("notice_count", 0)),
    }

    phase4_linkage_runner = Path(args.phase4_linkage_runner).resolve()
    phase4_linkage_matrix = Path(args.phase4_linkage_matrix).resolve()
    phase4_linkage_checklist = Path(args.phase4_linkage_checklist).resolve()
    phase4_linkage_reference_map = Path(args.phase4_linkage_reference_map).resolve()
    phase4_linkage_out = Path(args.phase4_linkage_out).resolve()
    phase4_linkage_out.parent.mkdir(parents=True, exist_ok=True)

    phase4_linkage_cmd = [
        args.python_bin,
        str(phase4_linkage_runner),
        "--matrix",
        str(phase4_linkage_matrix),
        "--checklist",
        str(phase4_linkage_checklist),
        "--reference-map",
        str(phase4_linkage_reference_map),
        "--out",
        str(phase4_linkage_out),
    ]
    for module in phase4_linkage_modules:
        phase4_linkage_cmd.extend(["--module", module])
    run_cmd(phase4_linkage_cmd)

    phase4_linkage_payload = json.loads(phase4_linkage_out.read_text(encoding="utf-8"))
    linkage_rows_raw = phase4_linkage_payload.get("modules", [])
    linkage_rows = linkage_rows_raw if isinstance(linkage_rows_raw, list) else []
    matrix_statuses: dict[str, str] = {}
    reference_priorities: dict[str, str] = {}
    reference_patterns_to_extract: dict[str, str] = {}
    reference_local_first_targets: dict[str, str] = {}
    reference_repository_count_total = 0
    ready_row_count_total = 0
    for row in linkage_rows:
        if not isinstance(row, dict):
            continue
        module_name = str(row.get("module", "")).strip()
        matrix_status = str(row.get("matrix_status", "")).strip()
        if module_name:
            matrix_statuses[module_name] = matrix_status
        ready_row_count_raw = row.get("ready_row_count", 0)
        try:
            ready_row_count_total += int(ready_row_count_raw)
        except (TypeError, ValueError):
            pass

        reference_priority = str(row.get("reference_priority", "")).strip()
        if module_name and reference_priority:
            reference_priorities[module_name] = reference_priority
        reference_pattern_to_extract = str(row.get("reference_pattern_to_extract", "")).strip()
        if module_name and reference_pattern_to_extract:
            reference_patterns_to_extract[module_name] = reference_pattern_to_extract
        reference_local_first_target = str(row.get("reference_local_first_target", "")).strip()
        if module_name and reference_local_first_target:
            reference_local_first_targets[module_name] = reference_local_first_target

        reference_repositories_raw = row.get("reference_repositories", [])
        if isinstance(reference_repositories_raw, list):
            reference_repository_count_total += sum(1 for item in reference_repositories_raw if str(item).strip())

    module_linkage: dict[str, Any] = {
        "enabled": True,
        "phase4_linkage_runner": str(phase4_linkage_runner),
        "phase4_linkage_matrix": str(phase4_linkage_matrix),
        "phase4_linkage_checklist": str(phase4_linkage_checklist),
        "phase4_linkage_reference_map": str(phase4_linkage_reference_map),
        "phase4_linkage_modules": phase4_linkage_modules,
        "phase4_linkage_module_count": len(phase4_linkage_modules),
        "phase4_linkage_out": str(phase4_linkage_out),
        "ready_row_count_total": ready_row_count_total,
        "reference_repository_count_total": reference_repository_count_total,
        "reference_priorities": reference_priorities,
        "reference_patterns_to_extract": reference_patterns_to_extract,
        "reference_local_first_targets": reference_local_first_targets,
        "matrix_statuses": matrix_statuses,
    }
    for module in phase4_linkage_modules:
        if module not in reference_priorities:
            raise ValueError(f"phase4 linkage report missing reference priority for module: {module}")
        if module not in reference_patterns_to_extract:
            raise ValueError(f"phase4 linkage report missing reference pattern to extract for module: {module}")
        if module not in reference_local_first_targets:
            raise ValueError(f"phase4 linkage report missing reference local-first target for module: {module}")
    phase4_done = bool(phase4_linkage_modules) and all(
        str(matrix_statuses.get(module, "")).strip() == "PHASE4_DONE" for module in phase4_linkage_modules
    )
    phase4_status = "PHASE4_DONE" if phase4_done else "PHASE4_IN_PROGRESS"
    module_linkage["phase4_status"] = phase4_status
    module_linkage["phase4_done"] = phase4_done
    if args.phase4_require_done and not phase4_done:
        raise ValueError(
            f"phase4 module linkage status must be PHASE4_DONE when --phase4-require-done is set: {phase4_status}"
        )

    phase4_reference_pattern_runner = Path(args.phase4_reference_pattern_runner).resolve()
    phase4_reference_index = Path(args.phase4_reference_index).resolve()
    phase4_reference_repo_root_text = str(args.phase4_reference_repo_root).strip()
    phase4_reference_repo_root = ""
    if phase4_reference_repo_root_text:
        phase4_reference_repo_root = str(Path(phase4_reference_repo_root_text).resolve())
    phase4_reference_repo_paths = [
        value for value in (str(item).strip() for item in args.phase4_reference_repo_path) if value
    ]
    phase4_reference_max_scan_files_per_repo = str(args.phase4_reference_max_scan_files_per_repo)
    phase4_reference_pattern_out = Path(args.phase4_reference_pattern_out).resolve()
    phase4_reference_pattern_out.parent.mkdir(parents=True, exist_ok=True)
    phase4_reference_pattern_min_coverage_ratio = str(args.phase4_reference_min_coverage_ratio)
    phase4_reference_pattern_secondary_min_coverage_ratio = str(
        args.phase4_reference_secondary_min_coverage_ratio
    ).strip()
    phase4_reference_pattern_cmd = [
        args.python_bin,
        str(phase4_reference_pattern_runner),
        "--reference-map",
        str(phase4_linkage_reference_map),
        "--reference-index",
        str(phase4_reference_index),
        "--min-coverage-ratio",
        phase4_reference_pattern_min_coverage_ratio,
        "--out",
        str(phase4_reference_pattern_out),
    ]
    if phase4_reference_pattern_secondary_min_coverage_ratio:
        phase4_reference_pattern_cmd.extend(
            [
                "--secondary-min-coverage-ratio",
                phase4_reference_pattern_secondary_min_coverage_ratio,
            ]
        )
    if phase4_reference_repo_root:
        phase4_reference_pattern_cmd.extend(["--reference-repo-root", phase4_reference_repo_root])
    for mapping in phase4_reference_repo_paths:
        phase4_reference_pattern_cmd.extend(["--reference-repo-path", mapping])
    if phase4_reference_max_scan_files_per_repo:
        phase4_reference_pattern_cmd.extend(
            ["--max-scan-files-per-repo", phase4_reference_max_scan_files_per_repo]
        )
    for module in args.phase4_reference_pattern_module:
        phase4_reference_pattern_cmd.extend(["--module", module])
    run_cmd(phase4_reference_pattern_cmd)

    phase4_reference_payload = json.loads(phase4_reference_pattern_out.read_text(encoding="utf-8"))
    phase4_reference_rows_raw = phase4_reference_payload.get("modules", [])
    phase4_reference_rows = phase4_reference_rows_raw if isinstance(phase4_reference_rows_raw, list) else []
    reference_pattern_modules: list[str] = []
    reference_pattern_module_coverage: dict[str, float] = {}
    reference_pattern_module_unmatched_counts: dict[str, int] = {}
    reference_pattern_secondary_module_coverage: dict[str, float] = {}
    reference_pattern_secondary_module_unmatched_counts: dict[str, int] = {}
    for row in phase4_reference_rows:
        if not isinstance(row, dict):
            continue
        module_name = str(row.get("module", "")).strip()
        if not module_name:
            continue
        reference_pattern_modules.append(module_name)
        coverage_value_raw = row.get("coverage_ratio", 0.0)
        unmatched_patterns_raw = row.get("unmatched_patterns", [])
        try:
            reference_pattern_module_coverage[module_name] = float(coverage_value_raw)
        except (TypeError, ValueError):
            reference_pattern_module_coverage[module_name] = 0.0
        if isinstance(unmatched_patterns_raw, list):
            reference_pattern_module_unmatched_counts[module_name] = sum(
                1 for item in unmatched_patterns_raw if str(item).strip()
            )
        else:
            reference_pattern_module_unmatched_counts[module_name] = 0

        secondary_coverage_value_raw = row.get("secondary_coverage_ratio", 0.0)
        secondary_unmatched_patterns_raw = row.get("secondary_unmatched_patterns", [])
        try:
            reference_pattern_secondary_module_coverage[module_name] = float(secondary_coverage_value_raw)
        except (TypeError, ValueError):
            reference_pattern_secondary_module_coverage[module_name] = 0.0
        if isinstance(secondary_unmatched_patterns_raw, list):
            reference_pattern_secondary_module_unmatched_counts[module_name] = sum(
                1 for item in secondary_unmatched_patterns_raw if str(item).strip()
            )
        else:
            reference_pattern_secondary_module_unmatched_counts[module_name] = 0

    reference_pattern_total_coverage_raw = phase4_reference_payload.get("total_coverage_ratio", 0.0)
    try:
        reference_pattern_total_coverage = float(reference_pattern_total_coverage_raw)
    except (TypeError, ValueError):
        reference_pattern_total_coverage = 0.0
    reference_pattern_secondary_total_coverage_raw = phase4_reference_payload.get("secondary_total_coverage_ratio", 0.0)
    try:
        reference_pattern_secondary_total_coverage = float(reference_pattern_secondary_total_coverage_raw)
    except (TypeError, ValueError):
        reference_pattern_secondary_total_coverage = 0.0
    reference_pattern_secondary_module_count_raw = phase4_reference_payload.get("secondary_module_count", 0)
    try:
        reference_pattern_secondary_module_count = int(reference_pattern_secondary_module_count_raw)
    except (TypeError, ValueError):
        reference_pattern_secondary_module_count = 0
    reference_repo_scanned_raw = phase4_reference_payload.get("reference_repo_scanned", [])
    reference_repo_scanned = []
    if isinstance(reference_repo_scanned_raw, list):
        reference_repo_scanned = [str(item).strip() for item in reference_repo_scanned_raw if str(item).strip()]
    secondary_reference_repo_scanned_raw = phase4_reference_payload.get("secondary_reference_repo_scanned", [])
    secondary_reference_repo_scanned = []
    if isinstance(secondary_reference_repo_scanned_raw, list):
        secondary_reference_repo_scanned = [
            str(item).strip() for item in secondary_reference_repo_scanned_raw if str(item).strip()
        ]
    reference_pattern_scan: dict[str, Any] = {
        "enabled": True,
        "phase4_reference_pattern_runner": str(phase4_reference_pattern_runner),
        "phase4_reference_index": str(phase4_reference_index),
        "phase4_reference_repo_root": phase4_reference_repo_root,
        "phase4_reference_repo_paths": phase4_reference_repo_paths,
        "phase4_reference_max_scan_files_per_repo": phase4_reference_max_scan_files_per_repo,
        "phase4_reference_pattern_modules": reference_pattern_modules,
        "phase4_reference_pattern_module_count": len(reference_pattern_modules),
        "phase4_reference_min_coverage_ratio": phase4_reference_pattern_min_coverage_ratio,
        "phase4_reference_secondary_min_coverage_ratio": phase4_reference_pattern_secondary_min_coverage_ratio,
        "phase4_reference_pattern_out": str(phase4_reference_pattern_out),
        "reference_pattern_total_coverage_ratio": reference_pattern_total_coverage,
        "reference_pattern_secondary_total_coverage_ratio": reference_pattern_secondary_total_coverage,
        "reference_pattern_secondary_module_count": reference_pattern_secondary_module_count,
        "reference_pattern_module_coverage": reference_pattern_module_coverage,
        "reference_pattern_module_unmatched_counts": reference_pattern_module_unmatched_counts,
        "reference_pattern_secondary_module_coverage": reference_pattern_secondary_module_coverage,
        "reference_pattern_secondary_module_unmatched_counts": reference_pattern_secondary_module_unmatched_counts,
        "reference_repo_scanned": reference_repo_scanned,
        "secondary_reference_repo_scanned": secondary_reference_repo_scanned,
    }

    copilot_hooks: dict[str, Any] = {"enabled": False}
    if args.phase4_enable_copilot_hooks:
        copilot_contract_runner = Path(args.copilot_contract_runner).resolve()
        copilot_release_assist_runner = Path(args.copilot_release_assist_runner).resolve()
        copilot_contract_out = Path(args.copilot_contract_out).resolve()
        copilot_release_assist_out = Path(args.copilot_release_assist_out).resolve()
        copilot_contract_out.parent.mkdir(parents=True, exist_ok=True)
        copilot_release_assist_out.parent.mkdir(parents=True, exist_ok=True)

        copilot_audit_log_text = str(args.copilot_audit_log).strip()
        copilot_audit_log = Path(copilot_audit_log_text).resolve() if copilot_audit_log_text else None
        if copilot_audit_log is not None:
            copilot_audit_log.parent.mkdir(parents=True, exist_ok=True)

        copilot_pipeline_context_path = (batch_root / "phase4_copilot_pipeline_context.json").resolve()
        copilot_pipeline_context_path.write_text(
            json.dumps(
                {
                    "release_id": release_id,
                    "batch_id": batch_id,
                    "overall_result": overall_result,
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        copilot_contract_cmd = [
            args.python_bin,
            str(copilot_contract_runner),
            "--mode",
            str(args.copilot_mode),
            "--prompt",
            str(args.copilot_prompt),
            "--pipeline-manifest",
            str(copilot_pipeline_context_path),
            "--guard-hold-threshold",
            str(args.copilot_guard_hold_threshold),
            "--out",
            str(copilot_contract_out),
        ]
        copilot_context_text = str(args.copilot_context_json).strip()
        if copilot_context_text:
            copilot_contract_cmd.extend(["--context-json", str(Path(copilot_context_text).resolve())])
        if copilot_audit_log is not None:
            copilot_contract_cmd.extend(["--audit-log", str(copilot_audit_log)])
        run_cmd(copilot_contract_cmd)
        copilot_contract_payload = json.loads(copilot_contract_out.read_text(encoding="utf-8"))

        copilot_release_assist_cmd = [
            args.python_bin,
            str(copilot_release_assist_runner),
            "--prompt-contract",
            str(copilot_contract_out),
            "--pipeline-manifest",
            str(copilot_pipeline_context_path),
            "--out",
            str(copilot_release_assist_out),
        ]
        run_cmd(copilot_release_assist_cmd)

        release_assist_payload = json.loads(copilot_release_assist_out.read_text(encoding="utf-8"))
        gating_payload = release_assist_payload.get("gating", {})
        recommended_action = str(release_assist_payload.get("recommended_action", "")).strip()
        release_gate_state = str(gating_payload.get("release_gate_state", "")).strip()
        guard_result = str(copilot_contract_payload.get("guard_result", "")).strip()
        guard_score_raw = copilot_contract_payload.get("guard_score", 0)
        try:
            guard_score = int(guard_score_raw)
        except (TypeError, ValueError):
            guard_score = 0
        recommended_output_payload = copilot_contract_payload.get("recommended_output", {})
        recommended_artifact_type = ""
        if isinstance(recommended_output_payload, dict):
            recommended_artifact_type = str(recommended_output_payload.get("artifact_type", "")).strip()
        copilot_hooks = {
            "enabled": True,
            "copilot_contract_runner": str(copilot_contract_runner),
            "copilot_release_assist_runner": str(copilot_release_assist_runner),
            "copilot_mode": str(args.copilot_mode),
            "copilot_contract_out": str(copilot_contract_out),
            "copilot_audit_log": str(copilot_audit_log) if copilot_audit_log is not None else "",
            "copilot_release_assist_out": str(copilot_release_assist_out),
            "copilot_pipeline_context_path": str(copilot_pipeline_context_path),
            "copilot_guard_hold_threshold": int(args.copilot_guard_hold_threshold),
            "copilot_guard_result": guard_result,
            "copilot_guard_score": guard_score,
            "copilot_recommended_artifact_type": recommended_artifact_type,
            "recommended_action": recommended_action,
            "release_gate_state": release_gate_state,
        }

    return {
        "enabled": True,
        "hil_sequence_runner": str(hil_sequence_runner),
        "hil_interface": str(hil_interface),
        "hil_sequence": str(hil_sequence),
        "hil_max_runtime_sec": float(args.hil_max_runtime_sec),
        "hil_schedule_out": str(hil_schedule_out),
        "phase4_status": phase4_status,
        "phase4_require_done": bool(args.phase4_require_done),
        "adp_hooks": adp_hooks,
        "module_linkage": module_linkage,
        "reference_pattern_scan": reference_pattern_scan,
        "copilot_hooks": copilot_hooks,
    }


def main() -> int:
    args = parse_args()
    args.hil_max_runtime_sec = parse_non_negative_float(
        str(args.hil_max_runtime_sec),
        default=0.0,
        field="hil-max-runtime-sec",
    )
    args.copilot_guard_hold_threshold = parse_positive_int(
        str(args.copilot_guard_hold_threshold),
        default=1,
        field="copilot-guard-hold-threshold",
    )
    args.trend_window = parse_int(str(args.trend_window), default=0, field="trend-window", minimum=0)
    args.trend_min_pass_rate = parse_float(
        str(args.trend_min_pass_rate),
        default=0.8,
        field="trend-min-pass-rate",
    )
    args.trend_min_samples = parse_int(
        str(args.trend_min_samples),
        default=3,
        field="trend-min-samples",
        minimum=1,
    )
    args.phase2_route_gate_min_lane_count = parse_int(
        str(args.phase2_route_gate_min_lane_count),
        default=0,
        field="phase2-route-gate-min-lane-count",
        minimum=0,
    )
    args.phase2_route_gate_min_total_length_m = parse_non_negative_float(
        str(args.phase2_route_gate_min_total_length_m),
        default=0.0,
        field="phase2-route-gate-min-total-length-m",
    )
    args.phase2_route_gate_max_routing_semantic_warning_count = parse_int(
        str(args.phase2_route_gate_max_routing_semantic_warning_count),
        default=0,
        field="phase2-route-gate-max-routing-semantic-warning-count",
        minimum=0,
    )
    args.phase2_route_gate_max_unreachable_lane_count = parse_int(
        str(args.phase2_route_gate_max_unreachable_lane_count),
        default=0,
        field="phase2-route-gate-max-unreachable-lane-count",
        minimum=0,
    )
    args.phase2_route_gate_max_non_reciprocal_link_warning_count = parse_int(
        str(args.phase2_route_gate_max_non_reciprocal_link_warning_count),
        default=0,
        field="phase2-route-gate-max-non-reciprocal-link-warning-count",
        minimum=0,
    )
    args.phase2_route_gate_max_continuity_gap_warning_count = parse_int(
        str(args.phase2_route_gate_max_continuity_gap_warning_count),
        default=0,
        field="phase2-route-gate-max-continuity-gap-warning-count",
        minimum=0,
    )
    args.phase3_control_gate_max_overlap_ratio = parse_float(
        str(args.phase3_control_gate_max_overlap_ratio),
        default=0.0,
        field="phase3-control-gate-max-overlap-ratio",
    )
    args.phase3_control_gate_max_steering_rate_degps = parse_non_negative_float(
        str(args.phase3_control_gate_max_steering_rate_degps),
        default=0.0,
        field="phase3-control-gate-max-steering-rate-degps",
    )
    args.phase3_control_gate_max_throttle_plus_brake = parse_non_negative_float(
        str(args.phase3_control_gate_max_throttle_plus_brake),
        default=0.0,
        field="phase3-control-gate-max-throttle-plus-brake",
    )
    args.phase3_control_gate_max_speed_tracking_error_abs_mps = parse_non_negative_float(
        str(args.phase3_control_gate_max_speed_tracking_error_abs_mps),
        default=0.0,
        field="phase3-control-gate-max-speed-tracking-error-abs-mps",
    )
    args.phase3_dataset_gate_min_run_summary_count = parse_int(
        str(args.phase3_dataset_gate_min_run_summary_count),
        default=0,
        field="phase3-dataset-gate-min-run-summary-count",
        minimum=0,
    )
    args.phase3_dataset_gate_min_traffic_profile_count = parse_int(
        str(args.phase3_dataset_gate_min_traffic_profile_count),
        default=0,
        field="phase3-dataset-gate-min-traffic-profile-count",
        minimum=0,
    )
    args.phase3_dataset_gate_min_actor_pattern_count = parse_int(
        str(args.phase3_dataset_gate_min_actor_pattern_count),
        default=0,
        field="phase3-dataset-gate-min-actor-pattern-count",
        minimum=0,
    )
    args.phase3_dataset_gate_min_avg_npc_count = parse_non_negative_float(
        str(args.phase3_dataset_gate_min_avg_npc_count),
        default=0.0,
        field="phase3-dataset-gate-min-avg-npc-count",
    )
    phase3_lane_risk_gate_min_ttc_same_lane_sec_input = str(
        args.phase3_lane_risk_gate_min_ttc_same_lane_sec
    ).strip()
    phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_input = str(
        args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec
    ).strip()
    phase3_lane_risk_gate_min_ttc_any_lane_sec_input = str(
        args.phase3_lane_risk_gate_min_ttc_any_lane_sec
    ).strip()
    args.phase3_lane_risk_gate_min_ttc_same_lane_sec = parse_non_negative_float(
        phase3_lane_risk_gate_min_ttc_same_lane_sec_input,
        default=0.0,
        field="phase3-lane-risk-gate-min-ttc-same-lane-sec",
    )
    args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec = parse_non_negative_float(
        phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_input,
        default=0.0,
        field="phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
    )
    args.phase3_lane_risk_gate_min_ttc_any_lane_sec = parse_non_negative_float(
        phase3_lane_risk_gate_min_ttc_any_lane_sec_input,
        default=0.0,
        field="phase3-lane-risk-gate-min-ttc-any-lane-sec",
    )
    args.phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total = parse_int(
        str(args.phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total),
        default=0,
        field="phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
        minimum=0,
    )
    args.phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total = parse_int(
        str(args.phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total),
        default=0,
        field="phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
        minimum=0,
    )
    args.phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total = parse_int(
        str(args.phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total),
        default=0,
        field="phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total",
        minimum=0,
    )
    if (
        phase3_lane_risk_gate_min_ttc_same_lane_sec_input
        and args.phase3_lane_risk_gate_min_ttc_same_lane_sec <= 0.0
    ):
        raise ValueError("phase3-lane-risk-gate-min-ttc-same-lane-sec must be > 0")
    if (
        phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_input
        and args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec <= 0.0
    ):
        raise ValueError("phase3-lane-risk-gate-min-ttc-adjacent-lane-sec must be > 0")
    if phase3_lane_risk_gate_min_ttc_any_lane_sec_input and args.phase3_lane_risk_gate_min_ttc_any_lane_sec <= 0.0:
        raise ValueError("phase3-lane-risk-gate-min-ttc-any-lane-sec must be > 0")
    phase3_core_sim_gate_min_ttc_same_lane_sec_input = str(
        args.phase3_core_sim_gate_min_ttc_same_lane_sec
    ).strip()
    phase3_core_sim_gate_min_ttc_any_lane_sec_input = str(
        args.phase3_core_sim_gate_min_ttc_any_lane_sec
    ).strip()
    args.phase3_core_sim_gate_min_ttc_same_lane_sec = parse_non_negative_float(
        phase3_core_sim_gate_min_ttc_same_lane_sec_input,
        default=0.0,
        field="phase3-core-sim-gate-min-ttc-same-lane-sec",
    )
    args.phase3_core_sim_gate_min_ttc_any_lane_sec = parse_non_negative_float(
        phase3_core_sim_gate_min_ttc_any_lane_sec_input,
        default=0.0,
        field="phase3-core-sim-gate-min-ttc-any-lane-sec",
    )
    if (
        phase3_core_sim_gate_min_ttc_same_lane_sec_input
        and args.phase3_core_sim_gate_min_ttc_same_lane_sec <= 0.0
    ):
        raise ValueError("phase3-core-sim-gate-min-ttc-same-lane-sec must be > 0")
    if phase3_core_sim_gate_min_ttc_any_lane_sec_input and args.phase3_core_sim_gate_min_ttc_any_lane_sec <= 0.0:
        raise ValueError("phase3-core-sim-gate-min-ttc-any-lane-sec must be > 0")
    phase3_core_sim_matrix_gate_min_ttc_same_lane_sec_input = str(
        args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec
    ).strip()
    phase3_core_sim_matrix_gate_min_ttc_any_lane_sec_input = str(
        args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec
    ).strip()
    args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec = parse_non_negative_float(
        phase3_core_sim_matrix_gate_min_ttc_same_lane_sec_input,
        default=0.0,
        field="phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
    )
    args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec = parse_non_negative_float(
        phase3_core_sim_matrix_gate_min_ttc_any_lane_sec_input,
        default=0.0,
        field="phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
    )
    args.phase3_core_sim_matrix_gate_max_failed_cases = parse_int(
        str(args.phase3_core_sim_matrix_gate_max_failed_cases),
        default=0,
        field="phase3-core-sim-matrix-gate-max-failed-cases",
        minimum=0,
    )
    args.phase3_core_sim_matrix_gate_max_collision_cases = parse_int(
        str(args.phase3_core_sim_matrix_gate_max_collision_cases),
        default=0,
        field="phase3-core-sim-matrix-gate-max-collision-cases",
        minimum=0,
    )
    args.phase3_core_sim_matrix_gate_max_timeout_cases = parse_int(
        str(args.phase3_core_sim_matrix_gate_max_timeout_cases),
        default=0,
        field="phase3-core-sim-matrix-gate-max-timeout-cases",
        minimum=0,
    )
    if (
        phase3_core_sim_matrix_gate_min_ttc_same_lane_sec_input
        and args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec <= 0.0
    ):
        raise ValueError("phase3-core-sim-matrix-gate-min-ttc-same-lane-sec must be > 0")
    if (
        phase3_core_sim_matrix_gate_min_ttc_any_lane_sec_input
        and args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec <= 0.0
    ):
        raise ValueError("phase3-core-sim-matrix-gate-min-ttc-any-lane-sec must be > 0")
    phase3_avoidance_ttc_threshold_sec_input = str(args.phase3_avoidance_ttc_threshold_sec).strip()
    phase3_ego_max_brake_mps2_input = str(args.phase3_ego_max_brake_mps2).strip()
    phase3_tire_friction_coeff_input = str(args.phase3_tire_friction_coeff).strip()
    phase3_surface_friction_scale_input = str(args.phase3_surface_friction_scale).strip()
    args.phase3_avoidance_ttc_threshold_sec = parse_non_negative_float(
        phase3_avoidance_ttc_threshold_sec_input,
        default=0.0,
        field="phase3-avoidance-ttc-threshold-sec",
    )
    args.phase3_ego_max_brake_mps2 = parse_non_negative_float(
        phase3_ego_max_brake_mps2_input,
        default=0.0,
        field="phase3-ego-max-brake-mps2",
    )
    args.phase3_tire_friction_coeff = parse_non_negative_float(
        phase3_tire_friction_coeff_input,
        default=0.0,
        field="phase3-tire-friction-coeff",
    )
    args.phase3_surface_friction_scale = parse_non_negative_float(
        phase3_surface_friction_scale_input,
        default=0.0,
        field="phase3-surface-friction-scale",
    )
    if phase3_avoidance_ttc_threshold_sec_input and args.phase3_avoidance_ttc_threshold_sec <= 0.0:
        raise ValueError("phase3-avoidance-ttc-threshold-sec must be > 0")
    if phase3_ego_max_brake_mps2_input and args.phase3_ego_max_brake_mps2 <= 0.0:
        raise ValueError("phase3-ego-max-brake-mps2 must be > 0")
    if phase3_tire_friction_coeff_input and args.phase3_tire_friction_coeff <= 0.0:
        raise ValueError("phase3-tire-friction-coeff must be > 0")
    if phase3_surface_friction_scale_input and args.phase3_surface_friction_scale <= 0.0:
        raise ValueError("phase3-surface-friction-scale must be > 0")
    if bool(args.phase3_enable_ego_collision_avoidance) and (
        args.phase3_avoidance_ttc_threshold_sec <= 0.0 or args.phase3_ego_max_brake_mps2 <= 0.0
    ):
        raise ValueError(
            "--phase3-enable-ego-collision-avoidance requires "
            "--phase3-avoidance-ttc-threshold-sec > 0 and --phase3-ego-max-brake-mps2 > 0"
        )
    args.phase3_core_sim_matrix_limit = parse_int(
        str(args.phase3_core_sim_matrix_limit),
        default=0,
        field="phase3-core-sim-matrix-limit",
        minimum=0,
    )
    args.phase4_reference_min_coverage_ratio = parse_non_negative_float(
        str(args.phase4_reference_min_coverage_ratio),
        default=1.0,
        field="phase4-reference-min-coverage-ratio",
    )
    if args.phase4_reference_min_coverage_ratio > 1.0:
        raise ValueError(
            "phase4-reference-min-coverage-ratio must be between 0 and 1, got: "
            f"{args.phase4_reference_min_coverage_ratio}"
        )
    phase4_reference_secondary_min_coverage_ratio_input = str(
        args.phase4_reference_secondary_min_coverage_ratio
    ).strip()
    if phase4_reference_secondary_min_coverage_ratio_input:
        phase4_reference_secondary_min_coverage_ratio = parse_non_negative_float(
            phase4_reference_secondary_min_coverage_ratio_input,
            default=0.0,
            field="phase4-reference-secondary-min-coverage-ratio",
        )
        if phase4_reference_secondary_min_coverage_ratio > 1.0:
            raise ValueError(
                "phase4-reference-secondary-min-coverage-ratio must be between 0 and 1, got: "
                f"{phase4_reference_secondary_min_coverage_ratio_input or phase4_reference_secondary_min_coverage_ratio}"
            )
        args.phase4_reference_secondary_min_coverage_ratio = str(phase4_reference_secondary_min_coverage_ratio)
    else:
        args.phase4_reference_secondary_min_coverage_ratio = ""
    phase4_reference_max_scan_files_per_repo_input = str(args.phase4_reference_max_scan_files_per_repo).strip()
    phase4_reference_max_scan_files_per_repo = parse_positive_int(
        phase4_reference_max_scan_files_per_repo_input,
        default=2000,
        field="phase4-reference-max-scan-files-per-repo",
    )
    args.phase4_reference_max_scan_files_per_repo = (
        str(phase4_reference_max_scan_files_per_repo)
        if phase4_reference_max_scan_files_per_repo_input
        else ""
    )
    args.phase4_reference_repo_path = [
        value for value in (str(item).strip() for item in args.phase4_reference_repo_path) if value
    ]
    args.phase4_reference_pattern_module = resolve_phase4_reference_pattern_modules(
        args.phase4_reference_pattern_module
    )
    sim_runtime = str(args.sim_runtime).strip().lower()
    if not sim_runtime:
        sim_runtime = "none"
    if sim_runtime not in {"none", "awsim", "carla"}:
        raise ValueError(f"sim-runtime must be one of: none, awsim, carla; got: {args.sim_runtime}")
    args.sim_runtime = sim_runtime
    sim_runtime_mode = str(args.sim_runtime_mode).strip().lower()
    if not sim_runtime_mode:
        sim_runtime_mode = "headless"
    if sim_runtime_mode not in {"headless", "interactive"}:
        raise ValueError(
            f"sim-runtime-mode must be one of: headless, interactive; got: {args.sim_runtime_mode}"
        )
    args.sim_runtime_mode = sim_runtime_mode
    if (
        bool(args.sim_runtime_probe_execute) or bool(args.sim_runtime_probe_require_availability)
    ) and not bool(args.sim_runtime_probe_enable):
        raise ValueError(
            "--sim-runtime-probe-execute/--sim-runtime-probe-require-availability "
            "requires --sim-runtime-probe-enable"
        )
    sim_runtime_probe_flag = str(args.sim_runtime_probe_flag).strip()
    sim_runtime_probe_args_shlex = str(args.sim_runtime_probe_args_shlex).strip()
    if (sim_runtime_probe_flag or sim_runtime_probe_args_shlex) and not bool(args.sim_runtime_probe_enable):
        raise ValueError(
            "--sim-runtime-probe-flag/--sim-runtime-probe-args-shlex requires --sim-runtime-probe-enable"
        )
    if sim_runtime == "none" and (
        bool(args.sim_runtime_probe_enable)
        or bool(args.sim_runtime_probe_execute)
        or bool(args.sim_runtime_probe_require_availability)
        or bool(sim_runtime_probe_flag)
        or bool(sim_runtime_probe_args_shlex)
    ):
        raise ValueError(
            "--sim-runtime-probe-* options require --sim-runtime to be one of: awsim, carla"
        )
    if bool(args.sim_runtime_scenario_contract_require_runtime_ready) and not bool(
        args.sim_runtime_scenario_contract_enable
    ):
        raise ValueError(
            "--sim-runtime-scenario-contract-require-runtime-ready requires "
            "--sim-runtime-scenario-contract-enable"
        )
    if sim_runtime == "none" and (
        bool(args.sim_runtime_scenario_contract_enable)
        or bool(args.sim_runtime_scenario_contract_require_runtime_ready)
    ):
        raise ValueError(
            "--sim-runtime-scenario-contract-* options require --sim-runtime to be one of: awsim, carla"
        )
    if bool(args.sim_runtime_scenario_contract_require_runtime_ready) and not bool(args.sim_runtime_probe_enable):
        raise ValueError(
            "--sim-runtime-scenario-contract-require-runtime-ready requires --sim-runtime-probe-enable"
        )
    if bool(args.sim_runtime_scene_result_require_runtime_ready) and not bool(args.sim_runtime_scene_result_enable):
        raise ValueError(
            "--sim-runtime-scene-result-require-runtime-ready requires --sim-runtime-scene-result-enable"
        )
    if bool(args.sim_runtime_scene_result_enable) and not bool(args.sim_runtime_scenario_contract_enable):
        raise ValueError(
            "--sim-runtime-scene-result-enable requires --sim-runtime-scenario-contract-enable"
        )
    if sim_runtime == "none" and (
        bool(args.sim_runtime_scene_result_enable)
        or bool(args.sim_runtime_scene_result_require_runtime_ready)
    ):
        raise ValueError(
            "--sim-runtime-scene-result-* options require --sim-runtime to be one of: awsim, carla"
        )
    if bool(args.sim_runtime_scene_result_require_runtime_ready) and not bool(args.sim_runtime_probe_enable):
        raise ValueError(
            "--sim-runtime-scene-result-require-runtime-ready requires --sim-runtime-probe-enable"
        )
    if bool(args.sim_runtime_interop_contract_require_runtime_ready) and not bool(
        args.sim_runtime_interop_contract_enable
    ):
        raise ValueError(
            "--sim-runtime-interop-contract-require-runtime-ready requires "
            "--sim-runtime-interop-contract-enable"
        )
    if sim_runtime == "none" and (
        bool(args.sim_runtime_interop_contract_enable)
        or bool(args.sim_runtime_interop_contract_require_runtime_ready)
    ):
        raise ValueError(
            "--sim-runtime-interop-contract-* options require --sim-runtime to be one of: awsim, carla"
        )
    if bool(args.sim_runtime_interop_contract_require_runtime_ready) and not bool(args.sim_runtime_probe_enable):
        raise ValueError(
            "--sim-runtime-interop-contract-require-runtime-ready requires --sim-runtime-probe-enable"
        )
    args.copilot_mode = resolve_phase4_copilot_mode(
        phase4_enable_hooks=bool(args.phase4_enable_hooks),
        phase4_enable_copilot_hooks=bool(args.phase4_enable_copilot_hooks),
        phase4_require_done=bool(args.phase4_require_done),
        raw_copilot_mode=str(args.copilot_mode),
        copilot_hooks_dependency_error="--phase4-enable-copilot-hooks requires --phase4-enable-hooks",
        require_done_dependency_error="--phase4-require-done requires --phase4-enable-hooks",
    )

    batch_spec_path = Path(args.batch_spec).resolve()
    batch_spec = _load_json_or_yaml(batch_spec_path)
    spec_dir = batch_spec_path.parent

    batch_id = str(batch_spec.get("batch_id", ""))
    if not batch_id:
        raise ValueError("batch_spec.batch_id is required")

    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    cloud_cmd = [
        args.python_bin,
        str(Path(args.cloud_runner).resolve()),
        "--batch-spec",
        str(batch_spec_path),
    ]

    if args.batch_out:
        cloud_cmd.extend(["--out", str(Path(args.batch_out).resolve())])

    if args.dry_run:
        cloud_cmd.append("--dry-run")
        run_cmd(cloud_cmd)
        print("[ok] dry-run completed")
        return 0

    cloud_stdout = run_cmd(cloud_cmd)
    batch_result_path = extract_result_path(cloud_stdout)

    if batch_result_path is None:
        output_root_value = args.batch_out if args.batch_out else str(batch_spec.get("output_root", "batch_runs"))
        output_root = resolve_ref(spec_dir, output_root_value)
        batch_result_path = output_root / batch_id / "batch_result.json"

    batch_result_path = batch_result_path.resolve()
    if not batch_result_path.exists():
        raise FileNotFoundError(f"batch result not found: {batch_result_path}")

    batch_root = batch_result_path.parent

    ingest_cmd = [
        args.python_bin,
        str(Path(args.ingest_runner).resolve()),
        "--summary-root",
        str(batch_root),
        "--db",
        str(db_path),
    ]
    run_cmd(ingest_cmd)

    versions = list(args.sds_version)
    if not versions:
        versions = discover_sds_versions(batch_root)

    if not versions:
        raise RuntimeError("no sds_version found to generate reports")

    reports: list[dict[str, str]] = []
    for version in versions:
        version_safe = safe_name(version)
        release_token = f"{args.release_id}_{version_safe}"
        report_path = report_dir / f"{release_token}.md"
        summary_path = report_dir / f"{release_token}.summary.json"

        report_cmd = [
            args.python_bin,
            str(Path(args.report_runner).resolve()),
            "--db",
            str(db_path),
            "--release-id",
            release_token,
            "--sds-version",
            version,
            "--out",
            str(report_path),
            "--summary-out",
            str(summary_path),
        ]

        gate_profile = str(args.gate_profile).strip()
        if gate_profile:
            report_cmd.extend(["--gate-profile", str(Path(gate_profile).resolve())])

        requirement_map = str(args.requirement_map).strip()
        if requirement_map:
            report_cmd.extend(["--requirement-map", str(Path(requirement_map).resolve())])

        run_cmd(report_cmd)
        summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        final_result = str(summary_payload.get("final_result", "unknown"))

        reports.append(
            {
                "sds_version": version,
                "report_path": str(report_path),
                "summary_path": str(summary_path),
                "final_result": final_result,
            }
        )

    release_ingest_cmd = [
        args.python_bin,
        str(Path(args.ingest_runner).resolve()),
    ]
    for item in reports:
        release_ingest_cmd.extend(["--report-summary-file", item["summary_path"]])
    release_ingest_cmd.extend(["--db", str(db_path)])
    run_cmd(release_ingest_cmd)

    report_summary_paths = [Path(item["summary_path"]).resolve() for item in reports]

    overall_result = "PASS"
    for item in reports:
        if item["final_result"] != "PASS":
            overall_result = "HOLD"
            break

    trend_result, trend_reasons, trend_details = evaluate_trend_gate(
        db_path=db_path,
        sds_versions=[item["sds_version"] for item in reports],
        window=args.trend_window,
        min_pass_rate=args.trend_min_pass_rate,
        min_samples=args.trend_min_samples,
    )

    base_overall_result = overall_result

    phase2_hooks: dict[str, Any] = {"enabled": False}
    if args.phase2_enable_hooks:
        phase2_hooks = run_phase2_hooks(args)

    phase3_hooks: dict[str, Any] = {"enabled": False}
    if args.phase3_enable_hooks:
        phase3_hooks = run_phase3_hooks(
            args,
            batch_root=batch_root,
            report_summary_files=report_summary_paths,
            db_path=db_path,
        )

    (
        phase2_route_gate_result,
        phase2_route_gate_reasons,
        phase2_route_gate_details,
    ) = evaluate_phase2_route_quality_gate(
        phase2_enable_hooks=args.phase2_enable_hooks,
        phase2_hooks=phase2_hooks,
        require_status_pass=bool(args.phase2_route_gate_require_status_pass),
        require_routing_semantic_pass=bool(args.phase2_route_gate_require_routing_semantic_pass),
        min_lane_count=int(args.phase2_route_gate_min_lane_count),
        min_total_length_m=float(args.phase2_route_gate_min_total_length_m),
        max_routing_semantic_warning_count=int(args.phase2_route_gate_max_routing_semantic_warning_count),
        max_unreachable_lane_count=int(args.phase2_route_gate_max_unreachable_lane_count),
        max_non_reciprocal_link_warning_count=int(
            args.phase2_route_gate_max_non_reciprocal_link_warning_count
        ),
        max_continuity_gap_warning_count=int(args.phase2_route_gate_max_continuity_gap_warning_count),
    )
    (
        phase3_control_gate_result,
        phase3_control_gate_reasons,
        phase3_control_gate_details,
    ) = evaluate_phase3_control_quality_gate(
        phase3_enable_hooks=args.phase3_enable_hooks,
        phase3_hooks=phase3_hooks,
        max_overlap_ratio=float(args.phase3_control_gate_max_overlap_ratio),
        max_steering_rate_degps=float(args.phase3_control_gate_max_steering_rate_degps),
        max_throttle_plus_brake=float(args.phase3_control_gate_max_throttle_plus_brake),
        max_speed_tracking_error_abs_mps=float(args.phase3_control_gate_max_speed_tracking_error_abs_mps),
    )
    (
        phase3_dataset_traffic_gate_result,
        phase3_dataset_traffic_gate_reasons,
        phase3_dataset_traffic_gate_details,
    ) = evaluate_phase3_dataset_traffic_gate(
        phase3_enable_hooks=args.phase3_enable_hooks,
        phase3_hooks=phase3_hooks,
        min_run_summary_count=int(args.phase3_dataset_gate_min_run_summary_count),
        min_traffic_profile_count=int(args.phase3_dataset_gate_min_traffic_profile_count),
        min_actor_pattern_count=int(args.phase3_dataset_gate_min_actor_pattern_count),
        min_avg_npc_count=float(args.phase3_dataset_gate_min_avg_npc_count),
    )
    (
        phase3_lane_risk_gate_result,
        phase3_lane_risk_gate_reasons,
        phase3_lane_risk_gate_details,
    ) = evaluate_phase3_lane_risk_gate(
        phase3_enable_hooks=args.phase3_enable_hooks,
        phase3_hooks=phase3_hooks,
        min_ttc_same_lane_sec=float(args.phase3_lane_risk_gate_min_ttc_same_lane_sec),
        min_ttc_adjacent_lane_sec=float(args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec),
        min_ttc_any_lane_sec=float(args.phase3_lane_risk_gate_min_ttc_any_lane_sec),
        max_ttc_under_3s_same_lane_total=int(args.phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total),
        max_ttc_under_3s_adjacent_lane_total=int(args.phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total),
        max_ttc_under_3s_any_lane_total=int(args.phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total),
    )
    (
        phase3_core_sim_gate_result,
        phase3_core_sim_gate_reasons,
        phase3_core_sim_gate_details,
    ) = evaluate_phase3_core_sim_gate(
        phase3_enable_hooks=args.phase3_enable_hooks,
        phase3_hooks=phase3_hooks,
        require_success=bool(args.phase3_core_sim_gate_require_success),
        min_ttc_same_lane_sec=float(args.phase3_core_sim_gate_min_ttc_same_lane_sec),
        min_ttc_any_lane_sec=float(args.phase3_core_sim_gate_min_ttc_any_lane_sec),
    )
    (
        phase3_core_sim_matrix_gate_result,
        phase3_core_sim_matrix_gate_reasons,
        phase3_core_sim_matrix_gate_details,
    ) = evaluate_phase3_core_sim_matrix_gate(
        phase3_enable_hooks=args.phase3_enable_hooks,
        phase3_hooks=phase3_hooks,
        require_all_cases_success=bool(args.phase3_core_sim_matrix_gate_require_all_cases_success),
        min_ttc_same_lane_sec=float(args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec),
        min_ttc_any_lane_sec=float(args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec),
        max_failed_cases=int(args.phase3_core_sim_matrix_gate_max_failed_cases),
        max_collision_cases=int(args.phase3_core_sim_matrix_gate_max_collision_cases),
        max_timeout_cases=int(args.phase3_core_sim_matrix_gate_max_timeout_cases),
    )
    functional_quality_gates = {
        "phase2_map_route_gate": {
            "result": phase2_route_gate_result,
            "reasons": phase2_route_gate_reasons,
            "details": phase2_route_gate_details,
        },
        "phase3_control_gate": {
            "result": phase3_control_gate_result,
            "reasons": phase3_control_gate_reasons,
            "details": phase3_control_gate_details,
        },
        "phase3_dataset_traffic_gate": {
            "result": phase3_dataset_traffic_gate_result,
            "reasons": phase3_dataset_traffic_gate_reasons,
            "details": phase3_dataset_traffic_gate_details,
        },
        "phase3_lane_risk_gate": {
            "result": phase3_lane_risk_gate_result,
            "reasons": phase3_lane_risk_gate_reasons,
            "details": phase3_lane_risk_gate_details,
        },
        "phase3_core_sim_gate": {
            "result": phase3_core_sim_gate_result,
            "reasons": phase3_core_sim_gate_reasons,
            "details": phase3_core_sim_gate_details,
        },
        "phase3_core_sim_matrix_gate": {
            "result": phase3_core_sim_matrix_gate_result,
            "reasons": phase3_core_sim_matrix_gate_reasons,
            "details": phase3_core_sim_matrix_gate_details,
        },
    }
    if (
        phase2_route_gate_result == "HOLD"
        or phase3_control_gate_result == "HOLD"
        or phase3_dataset_traffic_gate_result == "HOLD"
        or phase3_lane_risk_gate_result == "HOLD"
        or phase3_core_sim_gate_result == "HOLD"
        or phase3_core_sim_matrix_gate_result == "HOLD"
    ):
        overall_result = "HOLD"

    phase4_hooks: dict[str, Any] = {"enabled": False}
    if args.phase4_enable_hooks:
        phase4_hooks = run_phase4_hooks(
            args,
            batch_root=batch_root,
            release_id=args.release_id,
            batch_id=batch_id,
            overall_result=overall_result,
            reports=reports,
            report_summary_files=report_summary_paths,
            phase2_hooks=phase2_hooks,
            phase3_hooks=phase3_hooks,
        )

    decision_path = report_dir / f"{safe_name(args.release_id)}_release_decision.md"
    decision_lines: list[str] = []
    decision_lines.append(f"# Release Decision - {args.release_id}")
    decision_lines.append("")
    decision_lines.append(f"- generated_at: {utc_now_iso()}")
    decision_lines.append(f"- batch_id: `{batch_id}`")
    decision_lines.append(f"- report_result: **{base_overall_result}**")
    decision_lines.append(f"- overall_result: **{overall_result}**")
    decision_lines.append(f"- trend_result: **{trend_result}**")
    decision_lines.append("")
    decision_lines.append("| sds_version | final_result | report | summary |")
    decision_lines.append("| --- | --- | --- | --- |")
    for item in reports:
        decision_lines.append(
            f"| {item['sds_version']} | {item['final_result']} | "
            f"{item['report_path']} | {item['summary_path']} |"
        )
    decision_lines.append("")
    decision_lines.append("## Functional Quality Gates")
    decision_lines.append("")
    decision_lines.append(
        f"- phase2_map_route_gate: **{phase2_route_gate_result}** "
        f"(require_status_pass={int(bool(args.phase2_route_gate_require_status_pass))}, "
        f"require_routing_semantic_pass={int(bool(args.phase2_route_gate_require_routing_semantic_pass))}, "
        f"min_lane_count={int(args.phase2_route_gate_min_lane_count)}, "
        f"min_total_length_m={float(args.phase2_route_gate_min_total_length_m):.3f}, "
        "max_routing_semantic_warning_count="
        f"{int(args.phase2_route_gate_max_routing_semantic_warning_count)}, "
        f"max_unreachable_lane_count={int(args.phase2_route_gate_max_unreachable_lane_count)}, "
        "max_non_reciprocal_link_warning_count="
        f"{int(args.phase2_route_gate_max_non_reciprocal_link_warning_count)}, "
        "max_continuity_gap_warning_count="
        f"{int(args.phase2_route_gate_max_continuity_gap_warning_count)})"
    )
    decision_lines.append(
        f"- phase3_control_gate: **{phase3_control_gate_result}** "
        f"(max_overlap_ratio={float(args.phase3_control_gate_max_overlap_ratio):.6f}, "
        f"max_steering_rate_degps={float(args.phase3_control_gate_max_steering_rate_degps):.6f}, "
        f"max_throttle_plus_brake={float(args.phase3_control_gate_max_throttle_plus_brake):.6f}, "
        f"max_speed_tracking_error_abs_mps={float(args.phase3_control_gate_max_speed_tracking_error_abs_mps):.6f})"
    )
    decision_lines.append(
        f"- phase3_dataset_traffic_gate: **{phase3_dataset_traffic_gate_result}** "
        f"(min_run_summary_count={int(args.phase3_dataset_gate_min_run_summary_count)}, "
        f"min_traffic_profile_count={int(args.phase3_dataset_gate_min_traffic_profile_count)}, "
        f"min_actor_pattern_count={int(args.phase3_dataset_gate_min_actor_pattern_count)}, "
        f"min_avg_npc_count={float(args.phase3_dataset_gate_min_avg_npc_count):.6f})"
    )
    decision_lines.append(
        f"- phase3_lane_risk_gate: **{phase3_lane_risk_gate_result}** "
        f"(min_ttc_same_lane_sec={float(args.phase3_lane_risk_gate_min_ttc_same_lane_sec):.6f}, "
        f"min_ttc_adjacent_lane_sec={float(args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec):.6f}, "
        f"min_ttc_any_lane_sec={float(args.phase3_lane_risk_gate_min_ttc_any_lane_sec):.6f}, "
        f"max_ttc_under_3s_same_lane_total={int(args.phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total)}, "
        f"max_ttc_under_3s_adjacent_lane_total={int(args.phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total)}, "
        f"max_ttc_under_3s_any_lane_total={int(args.phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total)})"
    )
    decision_lines.append(
        f"- phase3_core_sim_gate: **{phase3_core_sim_gate_result}** "
        f"(require_success={int(bool(args.phase3_core_sim_gate_require_success))}, "
        f"min_ttc_same_lane_sec={float(args.phase3_core_sim_gate_min_ttc_same_lane_sec):.6f}, "
        f"min_ttc_any_lane_sec={float(args.phase3_core_sim_gate_min_ttc_any_lane_sec):.6f})"
    )
    decision_lines.append(
        f"- phase3_core_sim_matrix_gate: **{phase3_core_sim_matrix_gate_result}** "
        f"(require_all_cases_success={int(bool(args.phase3_core_sim_matrix_gate_require_all_cases_success))}, "
        f"min_ttc_same_lane_sec={float(args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec):.6f}, "
        f"min_ttc_any_lane_sec={float(args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec):.6f}, "
        f"max_failed_cases={int(args.phase3_core_sim_matrix_gate_max_failed_cases)}, "
        f"max_collision_cases={int(args.phase3_core_sim_matrix_gate_max_collision_cases)}, "
        f"max_timeout_cases={int(args.phase3_core_sim_matrix_gate_max_timeout_cases)})"
    )
    if (
        phase2_route_gate_reasons
        or phase3_control_gate_reasons
        or phase3_dataset_traffic_gate_reasons
        or phase3_lane_risk_gate_reasons
        or phase3_core_sim_gate_reasons
        or phase3_core_sim_matrix_gate_reasons
    ):
        decision_lines.append("")
        decision_lines.append("### Functional Quality Gate Reasons")
        decision_lines.append("")
        for reason in phase2_route_gate_reasons:
            decision_lines.append(f"- {reason}")
        for reason in phase3_control_gate_reasons:
            decision_lines.append(f"- {reason}")
        for reason in phase3_dataset_traffic_gate_reasons:
            decision_lines.append(f"- {reason}")
        for reason in phase3_lane_risk_gate_reasons:
            decision_lines.append(f"- {reason}")
        for reason in phase3_core_sim_gate_reasons:
            decision_lines.append(f"- {reason}")
        for reason in phase3_core_sim_matrix_gate_reasons:
            decision_lines.append(f"- {reason}")
    if args.trend_window > 0:
        decision_lines.append("")
        decision_lines.append("## Trend Gate")
        decision_lines.append("")
        decision_lines.append(
            f"- config: window={args.trend_window}, min_pass_rate={args.trend_min_pass_rate}, "
            f"min_samples={args.trend_min_samples}"
        )
        decision_lines.append(f"- result: **{trend_result}**")
        decision_lines.append("")
        decision_lines.append("| sds_version | sample_count | pass_count | hold_count | pass_rate |")
        decision_lines.append("| --- | ---: | ---: | ---: | ---: |")
        for item in trend_details:
            decision_lines.append(
                f"| {item['sds_version']} | {item['sample_count']} | "
                f"{item['pass_count']} | {item['hold_count']} | {item['pass_rate']:.4f} |"
            )
        decision_lines.append("")
        decision_lines.append("### Trend Reasons")
        decision_lines.append("")
        for reason in trend_reasons:
            decision_lines.append(f"- {reason}")
    decision_path.write_text("\n".join(decision_lines) + "\n", encoding="utf-8")

    manifest = {
        "generated_at": utc_now_iso(),
        "release_id": args.release_id,
        "batch_id": batch_id,
        "batch_result_path": str(batch_result_path),
        "db_path": str(db_path),
        "gate_profile": str(Path(args.gate_profile).resolve()) if str(args.gate_profile).strip() else "",
        "requirement_map": str(Path(args.requirement_map).resolve()) if str(args.requirement_map).strip() else "",
        "overall_result": overall_result,
        "strict_gate": bool(args.strict_gate),
        "trend_gate": {
            "window": args.trend_window,
            "min_pass_rate": args.trend_min_pass_rate,
            "min_samples": args.trend_min_samples,
            "result": trend_result,
            "reasons": trend_reasons,
            "details": trend_details,
        },
        "phase2_hooks": phase2_hooks,
        "phase3_hooks": phase3_hooks,
        "functional_quality_gates": functional_quality_gates,
        "phase4_hooks": phase4_hooks,
        "release_decision_path": str(decision_path),
        "reports": reports,
    }
    manifest_path = batch_root / "pipeline_result.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(f"[ok] pipeline_manifest={manifest_path}")
    print(
        f"[ok] release_decision={decision_path} overall_result={overall_result} trend_result={trend_result}"
    )
    for item in reports:
        print(f"[ok] report sds_version={item['sds_version']} path={item['report_path']}")

    strict_gate_failed = overall_result != "PASS"
    if args.trend_window > 0 and trend_result != "PASS":
        strict_gate_failed = True

    if args.strict_gate and strict_gate_failed:
        print("[error] strict_gate enabled and release/trend gate is HOLD", file=sys.stderr)
        return 3

    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="run_e2e_pipeline.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PIPELINE_PHASE_RUN_PIPELINE,
        )
    )
