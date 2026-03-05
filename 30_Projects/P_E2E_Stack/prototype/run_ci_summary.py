#!/usr/bin/env python3
"""Build CI release summary, notification payload, and GitHub step summary text."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from ci_commands import render_cmd, shell_join
from ci_input_parsing import (
    normalize_enum,
    parse_csv_pair,
    parse_non_negative_float,
    parse_non_negative_int,
    parse_phase4_secondary_module_warn_thresholds,
    parse_positive_int,
    parse_positive_float,
)
from ci_phases import (
    PHASE_RESOLVE_INPUTS,
    SUMMARY_PHASE_BUILD_NOTIFICATION_PAYLOAD,
    SUMMARY_PHASE_BUILD_SUMMARY,
    SUMMARY_PHASE_PUBLISH_SUMMARY,
    SUMMARY_PHASE_SEND_NOTIFICATION,
)
from ci_release import resolve_release_value
from ci_reporting import append_step_summary, emit_ci_error, normalize_exception_message
from ci_script_entry import resolve_step_summary_file_from_env
from ci_subprocess import run_logged_command_or_raise

ALLOWED_NOTIFY_ON = {"always", "hold", "warn", "hold_warn", "pass", "never"}
ALLOWED_NOTIFY_FORMAT = {"slack", "raw"}

RUNTIME_THRESHOLD_DRIFT_MISMATCH_FIELDS = (
    "runtime_lane_execution_exec_lane_warn_min_rows_mismatch",
    "runtime_lane_execution_exec_lane_hold_min_rows_mismatch",
    "runtime_evidence_compare_warn_min_artifacts_with_diffs_mismatch",
    "runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch",
    "runtime_lane_execution_phase2_sensor_fidelity_score_avg_warn_min_mismatch",
    "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch",
    "runtime_lane_execution_phase2_sensor_frame_count_avg_warn_min_mismatch",
    "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch",
    "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_warn_max_mismatch",
    "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch",
    "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_warn_min_mismatch",
    "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch",
    "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_warn_max_mismatch",
    "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch",
)

RUNTIME_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS = (
    "runtime_lane_execution_exec_lane_hold_min_rows_mismatch",
    "runtime_evidence_compare_hold_min_artifacts_with_diffs_mismatch",
    "runtime_lane_execution_phase2_sensor_fidelity_score_avg_hold_min_mismatch",
    "runtime_lane_execution_phase2_sensor_frame_count_avg_hold_min_mismatch",
    "runtime_lane_execution_phase2_sensor_camera_noise_stddev_px_avg_hold_max_mismatch",
    "runtime_lane_execution_phase2_sensor_lidar_point_count_avg_hold_min_mismatch",
    "runtime_lane_execution_phase2_sensor_radar_false_positive_rate_avg_hold_max_mismatch",
)

PHASE3_CORE_SIM_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS = (
    "phase3_core_sim_min_ttc_same_lane_hold_min_mismatch",
    "phase3_core_sim_min_ttc_any_lane_hold_min_mismatch",
)

PHASE3_LANE_RISK_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS = (
    "phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch",
    "phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch",
    "phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch",
    "phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch",
    "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch",
    "phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch",
)

PHASE3_DATASET_TRAFFIC_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS = (
    "phase3_dataset_traffic_run_summary_hold_min_mismatch",
    "phase3_dataset_traffic_profile_count_hold_min_mismatch",
    "phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch",
    "phase3_dataset_traffic_avg_npc_count_hold_min_mismatch",
)

PHASE2_LOG_REPLAY_HOLD_WARNING_REASON_FIELDS = (
    "phase2_log_replay_fail_count_above_hold_max",
    "phase2_log_replay_missing_summary_count_above_hold_max",
)

RUNTIME_NATIVE_SMOKE_HOLD_WARNING_REASON_FIELDS = (
    "runtime_native_smoke_fail_count_above_hold_max",
    "runtime_native_smoke_partial_count_above_hold_max",
)

RUNTIME_NATIVE_EVIDENCE_COMPARE_HOLD_WARNING_REASON_FIELDS = (
    "runtime_native_evidence_compare_with_diffs_above_hold_min",
    "runtime_native_evidence_compare_interop_import_mode_diff_count_above_hold_min",
)

PHASE3_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS: dict[str, tuple[str, ...]] = {
    "phase3_core_sim": PHASE3_CORE_SIM_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS,
    "phase3_lane_risk": PHASE3_LANE_RISK_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS,
    "phase3_dataset_traffic": PHASE3_DATASET_TRAFFIC_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS,
}

PHASE3_CORE_SIM_THRESHOLD_DRIFT_DETAIL_FIELDS = (
    "phase3_core_sim_min_ttc_same_lane_warn_min",
    "phase3_core_sim_min_ttc_same_lane_hold_min",
    "phase3_core_sim_min_ttc_any_lane_warn_min",
    "phase3_core_sim_min_ttc_any_lane_hold_min",
    "phase3_core_sim_gate_min_ttc_same_lane_sec_counts",
    "phase3_core_sim_gate_min_ttc_same_lane_sec_counts_text",
    "phase3_core_sim_gate_min_ttc_any_lane_sec_counts",
    "phase3_core_sim_gate_min_ttc_any_lane_sec_counts_text",
    "phase3_core_sim_min_ttc_same_lane_warn_min_mismatch",
    "phase3_core_sim_min_ttc_same_lane_hold_min_mismatch",
    "phase3_core_sim_min_ttc_any_lane_warn_min_mismatch",
    "phase3_core_sim_min_ttc_any_lane_hold_min_mismatch",
)

PHASE3_LANE_RISK_THRESHOLD_DRIFT_DETAIL_FIELDS = (
    "phase3_lane_risk_min_ttc_same_lane_warn_min",
    "phase3_lane_risk_min_ttc_same_lane_hold_min",
    "phase3_lane_risk_min_ttc_adjacent_lane_warn_min",
    "phase3_lane_risk_min_ttc_adjacent_lane_hold_min",
    "phase3_lane_risk_min_ttc_any_lane_warn_min",
    "phase3_lane_risk_min_ttc_any_lane_hold_min",
    "phase3_lane_risk_ttc_under_3s_same_lane_warn_max",
    "phase3_lane_risk_ttc_under_3s_same_lane_hold_max",
    "phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max",
    "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max",
    "phase3_lane_risk_ttc_under_3s_any_lane_warn_max",
    "phase3_lane_risk_ttc_under_3s_any_lane_hold_max",
    "phase3_lane_risk_gate_min_ttc_same_lane_sec_counts",
    "phase3_lane_risk_gate_min_ttc_same_lane_sec_counts_text",
    "phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts",
    "phase3_lane_risk_gate_min_ttc_adjacent_lane_sec_counts_text",
    "phase3_lane_risk_gate_min_ttc_any_lane_sec_counts",
    "phase3_lane_risk_gate_min_ttc_any_lane_sec_counts_text",
    "phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts",
    "phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total_counts_text",
    "phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts",
    "phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total_counts_text",
    "phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts",
    "phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total_counts_text",
    "phase3_lane_risk_min_ttc_same_lane_warn_min_mismatch",
    "phase3_lane_risk_min_ttc_same_lane_hold_min_mismatch",
    "phase3_lane_risk_min_ttc_adjacent_lane_warn_min_mismatch",
    "phase3_lane_risk_min_ttc_adjacent_lane_hold_min_mismatch",
    "phase3_lane_risk_min_ttc_any_lane_warn_min_mismatch",
    "phase3_lane_risk_min_ttc_any_lane_hold_min_mismatch",
    "phase3_lane_risk_ttc_under_3s_same_lane_warn_max_mismatch",
    "phase3_lane_risk_ttc_under_3s_same_lane_hold_max_mismatch",
    "phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max_mismatch",
    "phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max_mismatch",
    "phase3_lane_risk_ttc_under_3s_any_lane_warn_max_mismatch",
    "phase3_lane_risk_ttc_under_3s_any_lane_hold_max_mismatch",
)

PHASE3_DATASET_TRAFFIC_THRESHOLD_DRIFT_DETAIL_FIELDS = (
    "phase3_dataset_traffic_run_summary_warn_min",
    "phase3_dataset_traffic_run_summary_hold_min",
    "phase3_dataset_traffic_profile_count_warn_min",
    "phase3_dataset_traffic_profile_count_hold_min",
    "phase3_dataset_traffic_actor_pattern_count_warn_min",
    "phase3_dataset_traffic_actor_pattern_count_hold_min",
    "phase3_dataset_traffic_avg_npc_count_warn_min",
    "phase3_dataset_traffic_avg_npc_count_hold_min",
    "phase3_dataset_traffic_gate_min_run_summary_count_counts",
    "phase3_dataset_traffic_gate_min_run_summary_count_counts_text",
    "phase3_dataset_traffic_gate_min_traffic_profile_count_counts",
    "phase3_dataset_traffic_gate_min_traffic_profile_count_counts_text",
    "phase3_dataset_traffic_gate_min_actor_pattern_count_counts",
    "phase3_dataset_traffic_gate_min_actor_pattern_count_counts_text",
    "phase3_dataset_traffic_gate_min_avg_npc_count_counts",
    "phase3_dataset_traffic_gate_min_avg_npc_count_counts_text",
    "phase3_dataset_traffic_run_summary_warn_min_mismatch",
    "phase3_dataset_traffic_run_summary_hold_min_mismatch",
    "phase3_dataset_traffic_profile_count_warn_min_mismatch",
    "phase3_dataset_traffic_profile_count_hold_min_mismatch",
    "phase3_dataset_traffic_actor_pattern_count_warn_min_mismatch",
    "phase3_dataset_traffic_actor_pattern_count_hold_min_mismatch",
    "phase3_dataset_traffic_avg_npc_count_warn_min_mismatch",
    "phase3_dataset_traffic_avg_npc_count_hold_min_mismatch",
)


def _coerce_optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _coerce_reason_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _normalize_reason_keys(value: object) -> list[str]:
    return list(dict.fromkeys(_coerce_reason_list(value)))


def _count_reason_keys(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw in values:
        key = str(raw).strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _format_reason_key_counts_text(counts: dict[str, int]) -> str:
    if not counts:
        return "n/a"
    parts: list[str] = []
    for key in sorted(counts.keys()):
        count = int(counts.get(key, 0) or 0)
        if count <= 0:
            continue
        parts.append(f"{key}:{count}")
    return ",".join(parts) if parts else "n/a"


def _extract_threshold_drift_hold_policy_failure_scope(value: object) -> str:
    text = str(value).strip()
    if not text:
        return "unknown"
    marker = " threshold drift hold policy failed"
    lower_text = text.lower()
    marker_idx = lower_text.find(marker)
    if marker_idx <= 0:
        return "unknown"
    scope_text = text[:marker_idx].strip().lower()
    if not scope_text:
        return "unknown"
    return scope_text.replace(" ", "_")


def _count_threshold_drift_hold_policy_failure_scopes(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw in values:
        scope_key = _extract_threshold_drift_hold_policy_failure_scope(raw)
        counts[scope_key] = counts.get(scope_key, 0) + 1
    return counts


def _extract_threshold_drift_hold_policy_failure_reason_keys(value: object) -> list[str]:
    text = str(value).strip()
    if "reason_keys=" not in text:
        return []
    tail = text.split("reason_keys=", 1)[1].strip()
    if not tail or tail.lower() == "n/a":
        return []
    return [part.strip() for part in tail.split(",") if part.strip()]


def _count_threshold_drift_hold_policy_failure_scope_reason_keys(values: list[str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for raw in values:
        scope_key = _extract_threshold_drift_hold_policy_failure_scope(raw)
        reason_keys = _extract_threshold_drift_hold_policy_failure_reason_keys(raw)
        if not reason_keys:
            continue
        scope_reason_counts = counts.setdefault(scope_key, {})
        for reason_key in reason_keys:
            scope_reason_counts[reason_key] = scope_reason_counts.get(reason_key, 0) + 1
    return counts


def _format_reason_key_counts_by_scope_text(counts: dict[str, dict[str, int]]) -> str:
    if not counts:
        return "n/a"
    parts: list[str] = []
    for scope_key in sorted(counts.keys()):
        scope_reason_counts = counts.get(scope_key, {})
        if not isinstance(scope_reason_counts, dict):
            continue
        for reason_key in sorted(scope_reason_counts.keys()):
            count = int(scope_reason_counts.get(reason_key, 0) or 0)
            if count <= 0:
                continue
            parts.append(f"{scope_key}|{reason_key}:{count}")
    return ",".join(parts) if parts else "n/a"


def resolve_threshold_drift_hold_signal_from_payload(
    notification_payload: dict[str, object],
    *,
    prefix: str,
    hold_reason_fields: tuple[str, ...] = (),
) -> tuple[bool, str, str, list[str]]:
    drift_severity = str(notification_payload.get(f"{prefix}_threshold_drift_severity", "")).strip().upper()
    drift_summary = str(notification_payload.get(f"{prefix}_threshold_drift_summary_text", "")).strip() or "none"
    drift_reasons = _coerce_reason_list(notification_payload.get(f"{prefix}_threshold_drift_reasons"))

    hold_reasons: list[str] = []
    for field in hold_reason_fields:
        if _coerce_optional_bool(notification_payload.get(field)) is True:
            hold_reasons.append(field)
    hold_reasons = list(dict.fromkeys(hold_reasons))

    explicit_hold_detected = _coerce_optional_bool(
        notification_payload.get(f"{prefix}_threshold_drift_hold_detected")
    )
    hold_detected = bool(hold_reasons)
    if not hold_detected and explicit_hold_detected is not None:
        hold_detected = explicit_hold_detected
    if not hold_detected and drift_severity:
        hold_detected = drift_severity == "HOLD"
    if hold_detected and not hold_reasons:
        hold_reasons = [reason for reason in drift_reasons if "_hold_" in reason]
        if not hold_reasons and drift_reasons:
            hold_reasons = drift_reasons

    return hold_detected, drift_severity, drift_summary, hold_reasons


def resolve_warning_hold_signal_from_payload(
    notification_payload: dict[str, object],
    *,
    warning_field: str,
    warning_reason_field: str,
    hold_reason_fields: tuple[str, ...] = (),
) -> tuple[bool, str, str, list[str]]:
    warning_summary = str(notification_payload.get(warning_field, "")).strip() or "none"
    warning_reasons = _coerce_reason_list(notification_payload.get(warning_reason_field))
    hold_reasons = [reason for reason in warning_reasons if reason in hold_reason_fields]
    hold_reasons = list(dict.fromkeys(hold_reasons))
    hold_detected = bool(hold_reasons)
    if not hold_detected:
        inferred_hold_reasons = [reason for reason in warning_reasons if "_hold_" in reason]
        if inferred_hold_reasons:
            hold_detected = True
            hold_reasons = list(dict.fromkeys(inferred_hold_reasons))
    if not hold_detected and warning_summary != "none":
        warning_summary_lower = warning_summary.lower()
        hold_detected = "hold_max=" in warning_summary_lower or "hold_min=" in warning_summary_lower
    if hold_detected and not hold_reasons and warning_reasons:
        hold_reasons = warning_reasons
    severity = "HOLD" if hold_detected else ("WARN" if warning_summary != "none" else "NONE")
    return hold_detected, severity, warning_summary, hold_reasons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CI summary + notification helper")
    parser.add_argument("--artifacts-root", required=True, help="Root directory to scan for artifacts")
    parser.add_argument(
        "--summary-files-root",
        default="",
        help="Optional root directory to scan for *_*.summary.json",
    )
    parser.add_argument(
        "--summary-files-subpath",
        default="",
        help="Optional relative subpath to scan under summary-files-root",
    )
    parser.add_argument(
        "--pipeline-manifests-root",
        default="",
        help="Optional root directory to scan for pipeline_result.json",
    )
    parser.add_argument(
        "--pipeline-manifests-subpath",
        default="",
        help="Optional relative subpath to scan under pipeline-manifests-root",
    )
    parser.add_argument("--release-prefix", default="", help="Release prefix without SDS suffix")
    parser.add_argument("--release-id-input", default="", help="Release ID input override")
    parser.add_argument("--release-id-fallback-prefix", default="", help="Fallback release ID prefix")
    parser.add_argument("--release-id-fallback-run-id", default="", help="Fallback run ID token")
    parser.add_argument("--release-id-fallback-run-attempt", default="", help="Fallback run attempt token")
    parser.add_argument("--out-text", required=True, help="Output summary text path")
    parser.add_argument("--out-json", required=True, help="Output summary JSON path")
    parser.add_argument("--out-db", required=True, help="Output summary SQLite path")
    parser.add_argument("--summary-title", required=True, help="Markdown title for step summary")
    parser.add_argument("--workflow-name", required=True, help="Workflow name for notification payload")
    parser.add_argument("--notification-out-json", default="", help="Notification payload output path")
    parser.add_argument("--run-url", default="", help="Run URL to include in notification payload")
    parser.add_argument(
        "--notify-on",
        default="hold_warn",
        help="Notification policy always/hold/warn/hold_warn/pass/never",
    )
    parser.add_argument("--notify-format", default="slack", help="Notification payload format slack/raw")
    parser.add_argument("--webhook-url", default="", help="Webhook URL for notification sender")
    parser.add_argument("--notify-timeout-sec", default="10", help="Notification sender timeout seconds")
    parser.add_argument("--notify-max-retries", default="2", help="Notification sender retry count")
    parser.add_argument(
        "--notify-retry-backoff-sec",
        default="2",
        help="Notification sender retry backoff seconds",
    )
    parser.add_argument(
        "--notify-timing-total-warn-ms",
        default="0",
        help="Warn threshold for summary timing_ms.total (0 disables threshold)",
    )
    parser.add_argument(
        "--notify-timing-regression-baseline-ms",
        default="0",
        help="Optional baseline ms for timing regression checks (0 disables)",
    )
    parser.add_argument(
        "--notify-timing-regression-warn-ratio",
        default="0",
        help="Warn when timing regression ratio >= value (0 disables)",
    )
    parser.add_argument(
        "--notify-timing-regression-history-window",
        default="0",
        help="Use latest N *_release_summary.json files to derive regression baseline median (0 disables)",
    )
    parser.add_argument(
        "--notify-timing-regression-history-dir",
        default="",
        help="Optional directory for timing regression history scan",
    )
    parser.add_argument(
        "--notify-timing-regression-history-outlier-method",
        default="none",
        help="History outlier filter for regression baseline: none/iqr",
    )
    parser.add_argument(
        "--notify-timing-regression-history-trim-ratio",
        default="0",
        help="History symmetric trim ratio for regression baseline [0, 0.5)",
    )
    parser.add_argument(
        "--notify-phase4-primary-warn-ratio",
        default="0",
        help="Warn when phase4 primary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--notify-phase4-primary-hold-ratio",
        default="0",
        help="Hold when phase4 primary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--notify-phase4-primary-module-warn-thresholds",
        default="",
        help="Optional per-module primary coverage warn thresholds in module=ratio CSV",
    )
    parser.add_argument(
        "--notify-phase4-primary-module-hold-thresholds",
        default="",
        help="Optional per-module primary coverage hold thresholds in module=ratio CSV",
    )
    parser.add_argument(
        "--notify-phase4-secondary-warn-ratio",
        default="0",
        help="Warn when phase4 secondary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--notify-phase4-secondary-hold-ratio",
        default="0",
        help="Hold when phase4 secondary coverage ratio is below this value in range [0, 1] (0 disables)",
    )
    parser.add_argument(
        "--notify-phase4-secondary-warn-min-modules",
        default="1",
        help="Minimum secondary-module count required to evaluate phase4 secondary coverage warning (>0)",
    )
    parser.add_argument(
        "--notify-phase4-secondary-module-warn-thresholds",
        default="",
        help="Optional per-module secondary coverage warn thresholds in module=ratio CSV",
    )
    parser.add_argument(
        "--notify-phase4-secondary-module-hold-thresholds",
        default="",
        help="Optional per-module secondary coverage hold thresholds in module=ratio CSV",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-speed-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max final speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-speed-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max final speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-position-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max final position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-position-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max final position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-speed-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max delta speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-speed-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max delta speed exceeds this value in m/s (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-position-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max delta position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-position-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max delta position exceeds this value in m (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-heading-abs-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute final heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-heading-abs-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute final heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-lateral-position-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute final lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-final-lateral-position-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute final lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-heading-abs-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute delta heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-heading-abs-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute delta heading exceeds this value in deg (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-lateral-position-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute delta lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-lateral-position-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute delta lateral position exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-yaw-rate-abs-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute yaw rate exceeds this value in rad/s (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-yaw-rate-abs-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute yaw rate exceeds this value in rad/s (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-yaw-rate-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute delta yaw rate exceeds this value in rad/s "
            "(computed from final-initial yaw rate, 0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-delta-yaw-rate-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute delta yaw rate exceeds this value in rad/s "
            "(computed from final-initial yaw rate, 0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-velocity-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral velocity (trace peak) exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-velocity-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral velocity (trace peak) exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-accel-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute longitudinal acceleration (trace peak) exceeds this "
            "value in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-accel-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute longitudinal acceleration (trace peak) exceeds this "
            "value in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-accel-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral acceleration (trace peak) exceeds this value "
            "in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-accel-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral acceleration (trace peak) exceeds this value "
            "in m/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-yaw-accel-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute yaw acceleration (trace peak) exceeds this value in "
            "rad/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-yaw-accel-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute yaw acceleration (trace peak) exceeds this value in "
            "rad/s^2 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-jerk-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute longitudinal jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-jerk-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute longitudinal jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-jerk-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-jerk-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral jerk (trace peak) exceeds this value in "
            "m/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-yaw-jerk-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute yaw jerk (trace peak) exceeds this value in "
            "rad/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-yaw-jerk-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute yaw jerk (trace peak) exceeds this value in "
            "rad/s^3 (0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-position-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute lateral position (trace peak) exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-lateral-position-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute lateral position (trace peak) exceeds this value in m "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-road-grade-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute road grade exceeds this value in percent "
            "(computed from min/max road grade, 0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-road-grade-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute road grade exceeds this value in percent "
            "(computed from min/max road grade, 0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-grade-force-warn-max",
        default="0",
        help="Warn when phase3 vehicle dynamics max absolute grade force exceeds this value in N (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-grade-force-hold-max",
        default="0",
        help="Hold when phase3 vehicle dynamics max absolute grade force exceeds this value in N (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-vehicle-control-overlap-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics throttle-brake overlap ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-control-overlap-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics throttle-brake overlap ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-control-steering-rate-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute steering rate exceeds this value in deg/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-control-steering-rate-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute steering rate exceeds this value in deg/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-control-throttle-plus-brake-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max throttle+brake command sum exceeds this value "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-control-throttle-plus-brake-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max throttle+brake command sum exceeds this value "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-speed-tracking-error-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-speed-tracking-error-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-speed-tracking-error-abs-warn-max",
        default="0",
        help=(
            "Warn when phase3 vehicle dynamics max absolute speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-vehicle-speed-tracking-error-abs-hold-max",
        default="0",
        help=(
            "Hold when phase3 vehicle dynamics max absolute speed-tracking error exceeds this value in m/s "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-min-ttc-same-lane-warn-min",
        default="0",
        help="Warn when phase3 lane risk min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-min-ttc-same-lane-hold-min",
        default="0",
        help="Hold when phase3 lane risk min TTC (same lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-min-ttc-adjacent-lane-warn-min",
        default="0",
        help="Warn when phase3 lane risk min TTC (adjacent lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-min-ttc-adjacent-lane-hold-min",
        default="0",
        help="Hold when phase3 lane risk min TTC (adjacent lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-min-ttc-any-lane-warn-min",
        default="0",
        help="Warn when phase3 lane risk min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-min-ttc-any-lane-hold-min",
        default="0",
        help="Hold when phase3 lane risk min TTC (any lane) is below/equal this value in sec (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-same-lane-warn-max",
        default="0",
        help="Warn when phase3 lane risk ttc_under_3s same-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-same-lane-hold-max",
        default="0",
        help="Hold when phase3 lane risk ttc_under_3s same-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-warn-max",
        default="0",
        help="Warn when phase3 lane risk ttc_under_3s adjacent-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-hold-max",
        default="0",
        help="Hold when phase3 lane risk ttc_under_3s adjacent-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-any-lane-warn-max",
        default="0",
        help="Warn when phase3 lane risk ttc_under_3s any-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-any-lane-hold-max",
        default="0",
        help="Hold when phase3 lane risk ttc_under_3s any-lane total exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-same-lane-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 lane risk ttc_under_3s same-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-same-lane-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 lane risk ttc_under_3s same-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 lane risk ttc_under_3s adjacent-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 lane risk ttc_under_3s adjacent-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-any-lane-ratio-warn-max",
        default="0",
        help=(
            "Warn when phase3 lane risk ttc_under_3s any-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-lane-risk-ttc-under-3s-any-lane-ratio-hold-max",
        default="0",
        help=(
            "Hold when phase3 lane risk ttc_under_3s any-lane ratio exceeds this value in range [0, 1] "
            "(0 disables)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-run-summary-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic run-summary count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-run-summary-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic run-summary count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-profile-count-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic unique profile count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-profile-count-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic unique profile count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-actor-pattern-count-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic unique actor-pattern count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-actor-pattern-count-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic unique actor-pattern count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-avg-npc-count-warn-min",
        default="0",
        help=(
            "Warn when phase3 dataset traffic average NPC count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-dataset-traffic-avg-npc-count-hold-min",
        default="0",
        help=(
            "Hold when phase3 dataset traffic average NPC count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-min-ttc-same-lane-warn-min",
        default="0",
        help=(
            "Warn when phase3 core sim min TTC (same lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-min-ttc-same-lane-hold-min",
        default="0",
        help=(
            "Hold when phase3 core sim min TTC (same lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-min-ttc-any-lane-warn-min",
        default="0",
        help=(
            "Warn when phase3 core sim min TTC (any lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-min-ttc-any-lane-hold-min",
        default="0",
        help=(
            "Hold when phase3 core sim min TTC (any lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-collision-warn-max",
        default="0",
        help=(
            "Warn when phase3 core sim collision count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-collision-hold-max",
        default="0",
        help=(
            "Hold when phase3 core sim collision count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-timeout-warn-max",
        default="0",
        help=(
            "Warn when phase3 core sim timeout count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-timeout-hold-max",
        default="0",
        help=(
            "Hold when phase3 core sim timeout count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-gate-hold-warn-max",
        default="0",
        help=(
            "Warn when phase3 core sim gate-hold count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-gate-hold-hold-max",
        default="0",
        help=(
            "Hold when phase3 core sim gate-hold count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-min-ttc-same-lane-warn-min",
        default="0",
        help=(
            "Warn when phase3 core sim matrix min TTC (same lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-min-ttc-same-lane-hold-min",
        default="0",
        help=(
            "Hold when phase3 core sim matrix min TTC (same lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-min-ttc-any-lane-warn-min",
        default="0",
        help=(
            "Warn when phase3 core sim matrix min TTC (any lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-min-ttc-any-lane-hold-min",
        default="0",
        help=(
            "Hold when phase3 core sim matrix min TTC (any lane) is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-failed-cases-warn-max",
        default="0",
        help=(
            "Warn when phase3 core sim matrix failed-case count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-failed-cases-hold-max",
        default="0",
        help=(
            "Hold when phase3 core sim matrix failed-case count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-collision-cases-warn-max",
        default="0",
        help=(
            "Warn when phase3 core sim matrix collision-case count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-collision-cases-hold-max",
        default="0",
        help=(
            "Hold when phase3 core sim matrix collision-case count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-timeout-cases-warn-max",
        default="0",
        help=(
            "Warn when phase3 core sim matrix timeout-case count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-phase3-core-sim-matrix-timeout-cases-hold-max",
        default="0",
        help=(
            "Hold when phase3 core sim matrix timeout-case count exceeds this value "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-lane-execution-warn-min-exec-rows",
        default="0",
        help=(
            "Warn when runtime lane execution exec lane row count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-lane-execution-hold-min-exec-rows",
        default="0",
        help=(
            "Hold when runtime lane execution exec lane row count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-lane-phase2-rig-sweep-radar-alignment-degraded-drop-min",
        default="0.05",
        help=(
            "Warn/Hold when runtime-lane vs phase2 rig-sweep radar pass-minus-fail metric delta drops "
            "below this absolute amount (metric_delta <= -value)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-lane-phase2-rig-sweep-radar-alignment-hold-effective-drop-min",
        default="0.10",
        help=(
            "Hold when runtime-lane vs phase2 rig-sweep radar effective-quality pass-minus-fail "
            "delta drops below this absolute amount (effective_delta <= -value)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-lane-phase2-rig-sweep-radar-alignment-hold-degraded-metric-min-count",
        default="2",
        help=(
            "Hold when degraded runtime-lane vs phase2 rig-sweep radar pass-minus-fail metric count "
            "is at or above this minimum (0 disables this hold path)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-lane-phase2-rig-sweep-radar-alignment-non-positive-warn-max-delta",
        default="0",
        help=(
            "Warn when runtime-lane vs phase2 rig-sweep radar pass-minus-fail metric delta is at or "
            "below this max value when degraded-drop condition did not trigger"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-compare-warn-min-artifacts-with-diffs",
        default="0",
        help=(
            "Warn when runtime evidence compare artifacts_with_diffs_count is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-compare-hold-min-artifacts-with-diffs",
        default="0",
        help=(
            "Hold when runtime evidence compare artifacts_with_diffs_count is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-compare-warn-min-interop-import-mode-diff-count",
        default="0",
        help=(
            "Warn when runtime evidence compare interop import mode diff count total is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-compare-hold-min-interop-import-mode-diff-count",
        default="0",
        help=(
            "Hold when runtime evidence compare interop import mode diff count total is at or above this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-interop-contract-checked-warn-min",
        default="0",
        help=(
            "Warn when runtime evidence interop contract checked count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-interop-contract-checked-hold-min",
        default="0",
        help=(
            "Hold when runtime evidence interop contract checked count is below this minimum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-interop-contract-fail-warn-max",
        default="0",
        help=(
            "Warn when runtime evidence interop contract fail/non-pass count exceeds this maximum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--notify-runtime-evidence-interop-contract-fail-hold-max",
        default="0",
        help=(
            "Hold when runtime evidence interop contract fail/non-pass count exceeds this maximum "
            "(0 disables threshold)"
        ),
    )
    parser.add_argument(
        "--fail-on-runtime-threshold-drift-hold",
        action="store_true",
        help=(
            "Fail when runtime threshold drift severity resolves to HOLD after notification payload generation"
        ),
    )
    parser.add_argument(
        "--fail-on-phase3-core-sim-threshold-drift-hold",
        action="store_true",
        help=(
            "Fail when phase3 core sim threshold drift severity resolves to HOLD after notification payload generation"
        ),
    )
    parser.add_argument(
        "--fail-on-phase3-lane-risk-threshold-drift-hold",
        action="store_true",
        help=(
            "Fail when phase3 lane risk threshold drift severity resolves to HOLD after notification payload generation"
        ),
    )
    parser.add_argument(
        "--fail-on-phase3-dataset-traffic-threshold-drift-hold",
        action="store_true",
        help=(
            "Fail when phase3 dataset traffic threshold drift severity resolves to HOLD after notification payload generation"
        ),
    )
    parser.add_argument(
        "--fail-on-phase2-log-replay-threshold-hold",
        action="store_true",
        help=(
            "Fail when phase2 log replay threshold warning resolves to HOLD after notification payload generation"
        ),
    )
    parser.add_argument(
        "--fail-on-runtime-native-smoke-threshold-hold",
        action="store_true",
        help=(
            "Fail when runtime native smoke threshold warning resolves to HOLD after notification payload generation"
        ),
    )
    parser.add_argument(
        "--fail-on-runtime-native-evidence-compare-threshold-hold",
        action="store_true",
        help=(
            "Fail when runtime native evidence compare threshold warning resolves to HOLD after "
            "notification payload generation"
        ),
    )
    parser.add_argument(
        "--notify-phase2-map-routing-unreachable-lanes-warn-max",
        default="0",
        help="Warn when phase2 map routing unreachable lane count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-map-routing-unreachable-lanes-hold-max",
        default="0",
        help="Hold when phase2 map routing unreachable lane count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-map-routing-non-reciprocal-links-warn-max",
        default="0",
        help="Warn when phase2 map routing non-reciprocal link count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-map-routing-non-reciprocal-links-hold-max",
        default="0",
        help="Hold when phase2 map routing non-reciprocal link count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-map-routing-continuity-gap-warn-max",
        default="0",
        help="Warn when phase2 map routing continuity-gap warning count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-map-routing-continuity-gap-hold-max",
        default="0",
        help="Hold when phase2 map routing continuity-gap warning count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-fidelity-score-avg-warn-min",
        default="0",
        help="Warn when phase2 sensor fidelity score average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-fidelity-score-avg-hold-min",
        default="0",
        help="Hold when phase2 sensor fidelity score average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-frame-count-avg-warn-min",
        default="0",
        help="Warn when phase2 sensor frame-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-frame-count-avg-hold-min",
        default="0",
        help="Hold when phase2 sensor frame-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-camera-noise-stddev-px-avg-warn-max",
        default="0",
        help="Warn when phase2 camera noise average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-camera-noise-stddev-px-avg-hold-max",
        default="0",
        help="Hold when phase2 camera noise average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-lidar-point-count-avg-warn-min",
        default="0",
        help="Warn when phase2 lidar point-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-lidar-point-count-avg-hold-min",
        default="0",
        help="Hold when phase2 lidar point-count average is below this minimum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-radar-false-positive-rate-avg-warn-max",
        default="0",
        help="Warn when phase2 radar false-positive-rate average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-sensor-radar-false-positive-rate-avg-hold-max",
        default="0",
        help="Hold when phase2 radar false-positive-rate average exceeds this maximum (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-log-replay-fail-warn-max",
        default="0",
        help="Warn when phase2 log replay fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-log-replay-fail-hold-max",
        default="0",
        help="Hold when phase2 log replay fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-log-replay-missing-summary-warn-max",
        default="0",
        help="Warn when phase2 log replay missing-summary count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-phase2-log-replay-missing-summary-hold-max",
        default="0",
        help="Hold when phase2 log replay missing-summary count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-runtime-native-smoke-fail-warn-max",
        default="0",
        help="Warn when runtime native smoke fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-runtime-native-smoke-fail-hold-max",
        default="0",
        help="Hold when runtime native smoke fail-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-runtime-native-smoke-partial-warn-max",
        default="0",
        help="Warn when runtime native smoke partial-count exceeds this value (0 disables)",
    )
    parser.add_argument(
        "--notify-runtime-native-smoke-partial-hold-max",
        default="0",
        help="Hold when runtime native smoke partial-count exceeds this value (0 disables)",
    )
    parser.add_argument("--sds-versions-csv", default="", help="Comma separated versions (first two used for diff)")
    parser.add_argument("--version-a", default="", help="Optional explicit version A for reason code diff")
    parser.add_argument("--version-b", default="", help="Optional explicit version B for reason code diff")
    parser.add_argument(
        "--hold-reason-limit",
        default="",
        help="Hold reason output limit (>0)",
    )
    parser.add_argument(
        "--step-summary-file",
        default="",
        help="GitHub step summary output file path (defaults to STEP_SUMMARY_FILE or GITHUB_STEP_SUMMARY env)",
    )
    parser.add_argument("--python-bin", default="python3", help="Python executable")
    parser.add_argument(
        "--summary-builder",
        default=str(Path(__file__).resolve().with_name("build_release_summary_artifact.py")),
        help="build_release_summary_artifact.py path",
    )
    parser.add_argument(
        "--notification-builder",
        default=str(Path(__file__).resolve().with_name("build_release_notification_payload.py")),
        help="build_release_notification_payload.py path",
    )
    parser.add_argument(
        "--notification-sender",
        default=str(Path(__file__).resolve().with_name("send_release_notification.py")),
        help="send_release_notification.py path",
    )
    parser.add_argument(
        "--markdown-renderer",
        default=str(Path(__file__).resolve().with_name("render_release_summary_markdown.py")),
        help="render_release_summary_markdown.py path",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    return parser.parse_args()


def run_cmd(
    cmd: list[str],
    *,
    capture_output: bool = False,
    echo_captured_output: bool = True,
    sensitive_flags: set[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    display_cmd = render_cmd(cmd, sensitive_flags=sensitive_flags)
    return run_logged_command_or_raise(
        cmd,
        capture_output=capture_output,
        context="command",
        display_cmd=display_cmd,
        emit_output_on_success=capture_output and echo_captured_output,
        emit_output_on_failure=capture_output and echo_captured_output,
        flush=True,
    )


def publish_summary(
    *,
    python_bin: str,
    renderer_path: str,
    summary_json_path: Path,
    summary_text_path: Path,
    title: str,
    step_summary_file: str,
) -> None:
    if summary_json_path.exists():
        proc = run_cmd(
            [
                python_bin,
                str(Path(renderer_path).resolve()),
                "--summary-json",
                str(summary_json_path),
                "--title",
                title,
            ],
            capture_output=True,
            echo_captured_output=False,
        )
        markdown = (proc.stdout or "").rstrip() + "\n"
        append_step_summary(step_summary_file, markdown, print_if_missing=True)
        return

    if summary_text_path.exists():
        block = (
            f"## {title}\n\n"
            "```text\n"
            f"{summary_text_path.read_text(encoding='utf-8').rstrip()}\n"
            "```\n"
        )
        append_step_summary(step_summary_file, block, print_if_missing=True)
        return

    append_step_summary(step_summary_file, f"## {title}\n\n_summary file not found_\n", print_if_missing=True)


def merge_runtime_threshold_drift_into_summary(
    *,
    summary_json_path: Path,
    notification_json_path: Path,
) -> None:
    if not summary_json_path.exists() or not notification_json_path.exists():
        return
    summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
    notification_payload = json.loads(notification_json_path.read_text(encoding="utf-8"))
    if not isinstance(summary_payload, dict) or not isinstance(notification_payload, dict):
        return
    for field in (
        "runtime_threshold_drift_detected",
        "runtime_threshold_drift_severity",
        "runtime_threshold_drift_summary_text",
        "runtime_threshold_drift_reasons",
        "runtime_threshold_drift_hold_detected",
        *RUNTIME_THRESHOLD_DRIFT_MISMATCH_FIELDS,
        "phase3_core_sim_threshold_drift_detected",
        "phase3_core_sim_threshold_drift_severity",
        "phase3_core_sim_threshold_drift_summary_text",
        "phase3_core_sim_threshold_drift_reasons",
        "phase3_lane_risk_threshold_drift_detected",
        "phase3_lane_risk_threshold_drift_severity",
        "phase3_lane_risk_threshold_drift_summary_text",
        "phase3_lane_risk_threshold_drift_reasons",
        "phase3_dataset_traffic_threshold_drift_detected",
        "phase3_dataset_traffic_threshold_drift_severity",
        "phase3_dataset_traffic_threshold_drift_summary_text",
        "phase3_dataset_traffic_threshold_drift_reasons",
        *PHASE3_CORE_SIM_THRESHOLD_DRIFT_DETAIL_FIELDS,
        *PHASE3_LANE_RISK_THRESHOLD_DRIFT_DETAIL_FIELDS,
        *PHASE3_DATASET_TRAFFIC_THRESHOLD_DRIFT_DETAIL_FIELDS,
        "runtime_evidence_warning",
        "runtime_evidence_warning_messages",
        "runtime_evidence_warning_reasons",
        "runtime_evidence_interop_contract_warning",
        "runtime_evidence_interop_contract_warning_messages",
        "runtime_evidence_interop_contract_warning_reasons",
        "runtime_evidence_interop_export_summary_text",
        "runtime_evidence_compare_warning",
        "runtime_evidence_compare_warning_messages",
        "runtime_evidence_compare_warning_reasons",
        "runtime_native_evidence_compare_warning",
        "runtime_native_evidence_compare_warning_messages",
        "runtime_native_evidence_compare_warning_reasons",
        "runtime_native_evidence_compare_interop_import_mode_diff_count_total",
        "phase2_sensor_fidelity_warning",
        "phase2_sensor_fidelity_warning_messages",
        "phase2_sensor_fidelity_warning_reasons",
    ):
        if field in notification_payload:
            summary_payload[field] = notification_payload[field]

    drift_detected = _coerce_optional_bool(
        notification_payload.get("runtime_threshold_drift_detected")
    )
    if drift_detected is None:
        drift_severity = str(notification_payload.get("runtime_threshold_drift_severity", "")).strip().upper()
        if drift_severity:
            drift_detected = drift_severity != "NONE"
    if drift_detected is not None:
        summary_payload["runtime_threshold_drift_detected"] = drift_detected

    drift_hold_detected = _coerce_optional_bool(
        notification_payload.get("runtime_threshold_drift_hold_detected")
    )
    if drift_hold_detected is None:
        drift_severity = str(notification_payload.get("runtime_threshold_drift_severity", "")).strip().upper()
        if drift_severity:
            drift_hold_detected = drift_severity == "HOLD"
    if drift_hold_detected is not None:
        summary_payload["runtime_threshold_drift_hold_detected"] = drift_hold_detected

    for threshold_prefix in (
        "phase3_core_sim",
        "phase3_lane_risk",
        "phase3_dataset_traffic",
    ):
        detected_field = f"{threshold_prefix}_threshold_drift_detected"
        detected_flag = _coerce_optional_bool(notification_payload.get(detected_field))
        if detected_flag is None:
            severity_value = str(
                notification_payload.get(f"{threshold_prefix}_threshold_drift_severity", "")
            ).strip().upper()
            if severity_value:
                detected_flag = severity_value != "NONE"
        if detected_flag is not None:
            summary_payload[detected_field] = detected_flag
        hold_detected_field = f"{threshold_prefix}_threshold_drift_hold_detected"
        hold_detected_flag = _coerce_optional_bool(notification_payload.get(hold_detected_field))
        if hold_detected_flag is None:
            hold_detected_flag = any(
                _coerce_optional_bool(notification_payload.get(reason_field)) is True
                for reason_field in PHASE3_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS.get(threshold_prefix, ())
            )
        if not hold_detected_flag:
            severity_value = str(
                notification_payload.get(f"{threshold_prefix}_threshold_drift_severity", "")
            ).strip().upper()
            if severity_value:
                hold_detected_flag = severity_value == "HOLD"
        summary_payload[hold_detected_field] = bool(hold_detected_flag)

    summary_json_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def merge_runtime_threshold_drift_into_summary_text(
    *,
    summary_text_path: Path,
    notification_json_path: Path,
) -> None:
    if not summary_text_path.exists() or not notification_json_path.exists():
        return
    notification_payload = json.loads(notification_json_path.read_text(encoding="utf-8"))
    if not isinstance(notification_payload, dict):
        return
    updates: dict[str, str] = {}

    drift_summary = str(notification_payload.get("runtime_threshold_drift_summary_text", "")).strip()
    drift_severity = str(notification_payload.get("runtime_threshold_drift_severity", "")).strip()
    drift_reasons = notification_payload.get("runtime_threshold_drift_reasons")
    if drift_summary and drift_severity:
        if isinstance(drift_reasons, list):
            drift_reasons_text = ",".join(str(item).strip() for item in drift_reasons if str(item).strip())
        else:
            drift_reasons_text = str(drift_reasons or "").strip()
        if not drift_reasons_text:
            drift_reasons_text = "n/a"
        updates.update(
            {
                "runtime_threshold_drift_severity": drift_severity,
                "runtime_threshold_drift_summary": drift_summary,
                "runtime_threshold_drift_reasons": drift_reasons_text,
            }
        )
    drift_detected = _coerce_optional_bool(
        notification_payload.get("runtime_threshold_drift_detected")
    )
    if drift_detected is None and drift_severity:
        drift_detected = drift_severity.upper() != "NONE"
    if drift_detected is not None:
        updates["runtime_threshold_drift_detected"] = "1" if drift_detected else "0"
    drift_hold_detected = _coerce_optional_bool(
        notification_payload.get("runtime_threshold_drift_hold_detected")
    )
    if drift_hold_detected is None and drift_severity:
        drift_hold_detected = drift_severity.upper() == "HOLD"
    if drift_hold_detected is not None:
        updates["runtime_threshold_drift_hold_detected"] = "1" if drift_hold_detected else "0"
    for mismatch_field in RUNTIME_THRESHOLD_DRIFT_MISMATCH_FIELDS:
        if mismatch_field not in notification_payload:
            continue
        mismatch_flag = _coerce_optional_bool(notification_payload.get(mismatch_field))
        if mismatch_flag is None:
            continue
        updates[mismatch_field] = "1" if mismatch_flag else "0"

    def _append_threshold_drift_update(prefix: str) -> None:
        summary_text = str(notification_payload.get(f"{prefix}_threshold_drift_summary_text", "")).strip()
        severity_text = str(notification_payload.get(f"{prefix}_threshold_drift_severity", "")).strip()
        reasons_raw = notification_payload.get(f"{prefix}_threshold_drift_reasons")
        detected_raw = notification_payload.get(f"{prefix}_threshold_drift_detected")
        hold_detected_raw = notification_payload.get(f"{prefix}_threshold_drift_hold_detected")
        if not summary_text or not severity_text:
            return
        if isinstance(reasons_raw, list):
            reasons_text = ",".join(str(item).strip() for item in reasons_raw if str(item).strip())
        else:
            reasons_text = str(reasons_raw or "").strip()
        if not reasons_text:
            reasons_text = "n/a"
        detected_flag = _coerce_optional_bool(detected_raw)
        if detected_flag is None:
            detected_flag = severity_text.strip().upper() != "NONE"
        hold_detected_flag = _coerce_optional_bool(hold_detected_raw)
        if hold_detected_flag is None:
            hold_detected_flag = any(
                _coerce_optional_bool(notification_payload.get(reason_field)) is True
                for reason_field in PHASE3_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS.get(prefix, ())
            )
        if not hold_detected_flag:
            hold_detected_flag = severity_text.strip().upper() == "HOLD"
        updates.update(
            {
                f"{prefix}_threshold_drift_detected": "1" if detected_flag else "0",
                f"{prefix}_threshold_drift_hold_detected": "1" if hold_detected_flag else "0",
                f"{prefix}_threshold_drift_severity": severity_text,
                f"{prefix}_threshold_drift_summary": summary_text,
                f"{prefix}_threshold_drift_reasons": reasons_text,
            }
        )

    for threshold_prefix in (
        "phase3_core_sim",
        "phase3_lane_risk",
        "phase3_dataset_traffic",
    ):
        _append_threshold_drift_update(threshold_prefix)

    runtime_evidence_warning = str(notification_payload.get("runtime_evidence_warning", "")).strip()
    if runtime_evidence_warning:
        runtime_evidence_warning_reasons_raw = notification_payload.get("runtime_evidence_warning_reasons")
        if isinstance(runtime_evidence_warning_reasons_raw, list):
            runtime_evidence_warning_reasons_text = ",".join(
                str(item).strip() for item in runtime_evidence_warning_reasons_raw if str(item).strip()
            )
        else:
            runtime_evidence_warning_reasons_text = str(runtime_evidence_warning_reasons_raw or "").strip()
        if not runtime_evidence_warning_reasons_text:
            runtime_evidence_warning_reasons_text = "n/a"
        updates.update(
            {
                "runtime_evidence_warning": runtime_evidence_warning,
                "runtime_evidence_warning_reasons": runtime_evidence_warning_reasons_text,
            }
        )

    runtime_evidence_interop_contract_warning = str(
        notification_payload.get("runtime_evidence_interop_contract_warning", "")
    ).strip()
    if runtime_evidence_interop_contract_warning:
        runtime_evidence_interop_contract_warning_reasons_raw = notification_payload.get(
            "runtime_evidence_interop_contract_warning_reasons"
        )
        if isinstance(runtime_evidence_interop_contract_warning_reasons_raw, list):
            runtime_evidence_interop_contract_warning_reasons_text = ",".join(
                str(item).strip()
                for item in runtime_evidence_interop_contract_warning_reasons_raw
                if str(item).strip()
            )
        else:
            runtime_evidence_interop_contract_warning_reasons_text = str(
                runtime_evidence_interop_contract_warning_reasons_raw or ""
            ).strip()
        if not runtime_evidence_interop_contract_warning_reasons_text:
            runtime_evidence_interop_contract_warning_reasons_text = "n/a"
        updates.update(
            {
                "runtime_evidence_interop_contract_warning": runtime_evidence_interop_contract_warning,
                "runtime_evidence_interop_contract_warning_reasons": (
                    runtime_evidence_interop_contract_warning_reasons_text
                ),
            }
        )

    runtime_evidence_interop_export_summary = str(
        notification_payload.get("runtime_evidence_interop_export_summary_text", "")
    ).strip()
    if runtime_evidence_interop_export_summary:
        updates["runtime_evidence_interop_export_summary"] = runtime_evidence_interop_export_summary

    runtime_evidence_compare_warning = str(
        notification_payload.get("runtime_evidence_compare_warning", "")
    ).strip()
    if runtime_evidence_compare_warning:
        runtime_evidence_compare_warning_reasons_raw = notification_payload.get(
            "runtime_evidence_compare_warning_reasons"
        )
        if isinstance(runtime_evidence_compare_warning_reasons_raw, list):
            runtime_evidence_compare_warning_reasons_text = ",".join(
                str(item).strip()
                for item in runtime_evidence_compare_warning_reasons_raw
                if str(item).strip()
            )
        else:
            runtime_evidence_compare_warning_reasons_text = str(
                runtime_evidence_compare_warning_reasons_raw or ""
            ).strip()
        if not runtime_evidence_compare_warning_reasons_text:
            runtime_evidence_compare_warning_reasons_text = "n/a"
        updates.update(
            {
                "runtime_evidence_compare_warning": runtime_evidence_compare_warning,
                "runtime_evidence_compare_warning_reasons": runtime_evidence_compare_warning_reasons_text,
            }
        )

    runtime_native_evidence_compare_warning = str(
        notification_payload.get("runtime_native_evidence_compare_warning", "")
    ).strip()
    if runtime_native_evidence_compare_warning:
        runtime_native_evidence_compare_warning_reasons_raw = notification_payload.get(
            "runtime_native_evidence_compare_warning_reasons"
        )
        if isinstance(runtime_native_evidence_compare_warning_reasons_raw, list):
            runtime_native_evidence_compare_warning_reasons_text = ",".join(
                str(item).strip()
                for item in runtime_native_evidence_compare_warning_reasons_raw
                if str(item).strip()
            )
        else:
            runtime_native_evidence_compare_warning_reasons_text = str(
                runtime_native_evidence_compare_warning_reasons_raw or ""
            ).strip()
        if not runtime_native_evidence_compare_warning_reasons_text:
            runtime_native_evidence_compare_warning_reasons_text = "n/a"
        updates.update(
            {
                "runtime_native_evidence_compare_warning": runtime_native_evidence_compare_warning,
                "runtime_native_evidence_compare_warning_reasons": (
                    runtime_native_evidence_compare_warning_reasons_text
                ),
            }
        )

    phase2_sensor_fidelity_warning = str(notification_payload.get("phase2_sensor_fidelity_warning", "")).strip()
    if phase2_sensor_fidelity_warning:
        phase2_sensor_fidelity_warning_reasons_raw = notification_payload.get("phase2_sensor_fidelity_warning_reasons")
        if isinstance(phase2_sensor_fidelity_warning_reasons_raw, list):
            phase2_sensor_fidelity_warning_reasons_text = ",".join(
                str(item).strip() for item in phase2_sensor_fidelity_warning_reasons_raw if str(item).strip()
            )
        else:
            phase2_sensor_fidelity_warning_reasons_text = str(phase2_sensor_fidelity_warning_reasons_raw or "").strip()
        if not phase2_sensor_fidelity_warning_reasons_text:
            phase2_sensor_fidelity_warning_reasons_text = "n/a"
        updates.update(
            {
                "phase2_sensor_fidelity_warning": phase2_sensor_fidelity_warning,
                "phase2_sensor_fidelity_warning_reasons": phase2_sensor_fidelity_warning_reasons_text,
            }
        )

    if not updates:
        return

    lines = summary_text_path.read_text(encoding="utf-8").splitlines()
    for key, value in updates.items():
        rendered = f"{key}={value}"
        replaced = False
        for idx, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[idx] = rendered
                replaced = True
                break
        if not replaced:
            lines.append(rendered)
    summary_text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_threshold_drift_fields(
    notification_json_path: Path,
    *,
    prefix: str,
) -> tuple[str, str]:
    notification_payload = json.loads(notification_json_path.read_text(encoding="utf-8"))
    if not isinstance(notification_payload, dict):
        raise ValueError("notification payload must be a JSON object")
    drift_severity = str(notification_payload.get(f"{prefix}_threshold_drift_severity", "")).strip().upper()
    drift_summary = str(notification_payload.get(f"{prefix}_threshold_drift_summary_text", "")).strip() or "none"
    return drift_severity, drift_summary


def resolve_threshold_drift_hold_signal(
    notification_json_path: Path,
    *,
    prefix: str,
    hold_reason_fields: tuple[str, ...] = (),
) -> tuple[bool, str, str, list[str]]:
    notification_payload = json.loads(notification_json_path.read_text(encoding="utf-8"))
    if not isinstance(notification_payload, dict):
        raise ValueError("notification payload must be a JSON object")
    return resolve_threshold_drift_hold_signal_from_payload(
        notification_payload,
        prefix=prefix,
        hold_reason_fields=hold_reason_fields,
    )


def resolve_warning_hold_signal(
    notification_json_path: Path,
    *,
    warning_field: str,
    warning_reason_field: str,
    hold_reason_fields: tuple[str, ...] = (),
) -> tuple[bool, str, str, list[str]]:
    notification_payload = json.loads(notification_json_path.read_text(encoding="utf-8"))
    if not isinstance(notification_payload, dict):
        raise ValueError("notification payload must be a JSON object")
    return resolve_warning_hold_signal_from_payload(
        notification_payload,
        warning_field=warning_field,
        warning_reason_field=warning_reason_field,
        hold_reason_fields=hold_reason_fields,
    )


def _format_threshold_drift_hold_policy_failure(
    *,
    scope: str,
    drift_severity: str,
    drift_summary: str,
    hold_reason_keys: list[str],
) -> str:
    reason_keys_text = ",".join(hold_reason_keys) if hold_reason_keys else "n/a"
    return (
        f"{scope} threshold drift hold policy failed: "
        f"severity={drift_severity}, summary={drift_summary}, reason_keys={reason_keys_text}"
    )


def resolve_runtime_threshold_drift_fields(notification_json_path: Path) -> tuple[str, str]:
    return resolve_threshold_drift_fields(notification_json_path, prefix="runtime")


def persist_threshold_drift_hold_policy_failures(
    *,
    summary_json_path: Path,
    summary_text_path: Path,
    policy_failures: list[str],
    policy_failure_reason_keys: list[str] | None = None,
) -> None:
    if not policy_failures:
        return
    failure_count = len(policy_failures)
    failure_summary_text = "; ".join(policy_failures)
    scope_counts = _count_threshold_drift_hold_policy_failure_scopes(policy_failures)
    scope_counts_text = _format_reason_key_counts_text(scope_counts)
    scope_reason_key_counts = _count_threshold_drift_hold_policy_failure_scope_reason_keys(policy_failures)
    scope_reason_key_counts_text = _format_reason_key_counts_by_scope_text(scope_reason_key_counts)
    reason_keys_raw = [str(item).strip() for item in (policy_failure_reason_keys or []) if str(item).strip()]
    reason_key_counts = _count_reason_keys(reason_keys_raw)
    reason_keys = _normalize_reason_keys(reason_keys_raw)
    reason_keys_text = ",".join(reason_keys) if reason_keys else "n/a"
    reason_key_counts_text = _format_reason_key_counts_text(reason_key_counts)

    if summary_json_path.exists():
        summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
        if isinstance(summary_payload, dict):
            summary_payload["threshold_drift_hold_policy_failure_detected"] = True
            summary_payload["threshold_drift_hold_policy_failure_count"] = failure_count
            summary_payload["threshold_drift_hold_policy_failures"] = list(policy_failures)
            summary_payload["threshold_drift_hold_policy_failure_summary_text"] = failure_summary_text
            summary_payload["threshold_drift_hold_policy_failure_scope_counts"] = scope_counts
            summary_payload["threshold_drift_hold_policy_failure_scope_reason_key_counts"] = scope_reason_key_counts
            summary_payload["threshold_drift_hold_policy_failure_reason_keys"] = reason_keys
            summary_payload["threshold_drift_hold_policy_failure_reason_key_counts"] = reason_key_counts
            summary_json_path.write_text(
                json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    if summary_text_path.exists():
        updates = {
            "threshold_drift_hold_policy_failure_detected": "1",
            "threshold_drift_hold_policy_failure_count": str(failure_count),
            "threshold_drift_hold_policy_failure_summary": failure_summary_text,
            "threshold_drift_hold_policy_failures": " || ".join(policy_failures),
            "threshold_drift_hold_policy_failure_scope_counts": scope_counts_text,
            "threshold_drift_hold_policy_failure_scope_reason_key_counts": scope_reason_key_counts_text,
            "threshold_drift_hold_policy_failure_reason_keys": reason_keys_text,
            "threshold_drift_hold_policy_failure_reason_key_counts": reason_key_counts_text,
        }
        lines = summary_text_path.read_text(encoding="utf-8").splitlines()
        for key, value in updates.items():
            rendered = f"{key}={value}"
            replaced = False
            for idx, line in enumerate(lines):
                if line.startswith(f"{key}="):
                    lines[idx] = rendered
                    replaced = True
                    break
            if not replaced:
                lines.append(rendered)
        summary_text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _upsert_key_value_lines(text: str, updates: dict[str, str]) -> str:
    lines = str(text).splitlines()
    for key, value in updates.items():
        rendered = f"{key}={value}"
        replaced = False
        for idx, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[idx] = rendered
                replaced = True
                break
        if not replaced:
            lines.append(rendered)
    return "\n".join(lines)


def persist_threshold_drift_hold_policy_failures_to_notification(
    *,
    notification_json_path: Path,
    policy_failures: list[str],
    policy_failure_reason_keys: list[str] | None = None,
) -> None:
    if not policy_failures or not notification_json_path.exists():
        return
    notification_payload = json.loads(notification_json_path.read_text(encoding="utf-8"))
    if not isinstance(notification_payload, dict):
        return

    failure_count = len(policy_failures)
    failure_summary_text = "; ".join(policy_failures)
    failures_text = ",".join(policy_failures) if policy_failures else "n/a"
    scope_counts = _count_threshold_drift_hold_policy_failure_scopes(policy_failures)
    scope_counts_text = _format_reason_key_counts_text(scope_counts)
    scope_reason_key_counts = _count_threshold_drift_hold_policy_failure_scope_reason_keys(policy_failures)
    scope_reason_key_counts_text = _format_reason_key_counts_by_scope_text(scope_reason_key_counts)
    reason_keys_raw = [str(item).strip() for item in (policy_failure_reason_keys or []) if str(item).strip()]
    reason_key_counts = _count_reason_keys(reason_keys_raw)
    reason_keys = _normalize_reason_keys(reason_keys_raw)
    reason_keys_text = ",".join(reason_keys) if reason_keys else "n/a"
    reason_key_counts_text = _format_reason_key_counts_text(reason_key_counts)
    updates = {
        "threshold_drift_hold_policy_failure_detected": "1",
        "threshold_drift_hold_policy_failure_count": str(failure_count),
        "threshold_drift_hold_policy_failure_summary": failure_summary_text,
        "threshold_drift_hold_policy_failures": failures_text,
        "threshold_drift_hold_policy_failure_scope_counts": scope_counts_text,
        "threshold_drift_hold_policy_failure_scope_reason_key_counts": scope_reason_key_counts_text,
        "threshold_drift_hold_policy_failure_reason_keys": reason_keys_text,
        "threshold_drift_hold_policy_failure_reason_key_counts": reason_key_counts_text,
    }

    notification_payload["threshold_drift_hold_policy_failure_detected"] = True
    notification_payload["threshold_drift_hold_policy_failure_count"] = failure_count
    notification_payload["threshold_drift_hold_policy_failure_summary_text"] = failure_summary_text
    notification_payload["threshold_drift_hold_policy_failures"] = list(policy_failures)
    notification_payload["threshold_drift_hold_policy_failure_scope_counts"] = scope_counts
    notification_payload["threshold_drift_hold_policy_failure_scope_reason_key_counts"] = scope_reason_key_counts
    notification_payload["threshold_drift_hold_policy_failure_reason_keys"] = reason_keys
    notification_payload["threshold_drift_hold_policy_failure_reason_key_counts"] = reason_key_counts

    message_text = _upsert_key_value_lines(str(notification_payload.get("message_text", "")), updates)
    notification_payload["message_text"] = message_text

    slack_payload_raw = notification_payload.get("slack")
    slack_payload = slack_payload_raw if isinstance(slack_payload_raw, dict) else {}
    slack_payload["text"] = message_text
    blocks_raw = slack_payload.get("blocks")
    blocks = blocks_raw if isinstance(blocks_raw, list) else []
    section_text = "\n".join(
        [
            "*threshold drift hold policy*",
            "- detected: 1",
            f"- count: {failure_count}",
            f"- summary: {failure_summary_text}",
            f"- failures: {failures_text}",
            f"- scope_counts: {scope_counts_text}",
            f"- scope_reason_key_counts: {scope_reason_key_counts_text}",
            f"- reason_keys: {reason_keys_text}",
            f"- reason_key_counts: {reason_key_counts_text}",
        ]
    )
    section_block = {"type": "section", "text": {"type": "mrkdwn", "text": section_text}}
    replaced = False
    for idx, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        if str(block.get("type", "")).strip() != "section":
            continue
        text_obj = block.get("text")
        if not isinstance(text_obj, dict):
            continue
        if str(text_obj.get("type", "")).strip() != "mrkdwn":
            continue
        block_text = str(text_obj.get("text", "")).strip()
        if block_text.startswith("*threshold drift hold policy*"):
            blocks[idx] = section_block
            replaced = True
            break
    if not replaced:
        blocks.append(section_block)
    slack_payload["blocks"] = blocks
    notification_payload["slack"] = slack_payload

    notification_json_path.write_text(
        json.dumps(notification_payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    failure_phase = PHASE_RESOLVE_INPUTS
    failure_command = ""
    step_summary_file = str(args.step_summary_file).strip() or resolve_step_summary_file_from_env()
    threshold_drift_hold_policy_failures: list[str] = []
    threshold_drift_hold_policy_failure_reason_keys: list[str] = []
    try:
        release_prefix = resolve_release_value(
            explicit_value=str(args.release_prefix),
            release_id_input=str(args.release_id_input),
            fallback_prefix=str(args.release_id_fallback_prefix),
            fallback_run_id=str(args.release_id_fallback_run_id),
            fallback_run_attempt=str(args.release_id_fallback_run_attempt),
            required_field="release-prefix",
        )
        notify_on = normalize_enum(
            raw=str(args.notify_on),
            default="hold_warn",
            field="notify-on",
            allowed=ALLOWED_NOTIFY_ON,
        )
        notify_format = normalize_enum(
            raw=str(args.notify_format),
            default="slack",
            field="notify-format",
            allowed=ALLOWED_NOTIFY_FORMAT,
        )
        fail_on_runtime_threshold_drift_hold = bool(args.fail_on_runtime_threshold_drift_hold)
        fail_on_phase3_core_sim_threshold_drift_hold = bool(
            args.fail_on_phase3_core_sim_threshold_drift_hold
        )
        fail_on_phase3_lane_risk_threshold_drift_hold = bool(
            args.fail_on_phase3_lane_risk_threshold_drift_hold
        )
        fail_on_phase3_dataset_traffic_threshold_drift_hold = bool(
            args.fail_on_phase3_dataset_traffic_threshold_drift_hold
        )
        fail_on_phase2_log_replay_threshold_hold = bool(args.fail_on_phase2_log_replay_threshold_hold)
        fail_on_runtime_native_smoke_threshold_hold = bool(args.fail_on_runtime_native_smoke_threshold_hold)
        fail_on_runtime_native_evidence_compare_threshold_hold = bool(
            args.fail_on_runtime_native_evidence_compare_threshold_hold
        )
        notify_timeout_sec = parse_positive_float(
            str(args.notify_timeout_sec),
            default=10.0,
            field="notify-timeout-sec",
        )
        notify_max_retries = parse_non_negative_int(
            str(args.notify_max_retries),
            default=2,
            field="notify-max-retries",
        )
        notify_retry_backoff_sec = parse_non_negative_float(
            str(args.notify_retry_backoff_sec),
            default=2.0,
            field="notify-retry-backoff-sec",
        )
        notify_timing_total_warn_ms = parse_non_negative_int(
            str(args.notify_timing_total_warn_ms),
            default=0,
            field="notify-timing-total-warn-ms",
        )
        notify_timing_regression_baseline_ms = parse_non_negative_int(
            str(args.notify_timing_regression_baseline_ms),
            default=0,
            field="notify-timing-regression-baseline-ms",
        )
        notify_timing_regression_warn_ratio = parse_non_negative_float(
            str(args.notify_timing_regression_warn_ratio),
            default=0.0,
            field="notify-timing-regression-warn-ratio",
        )
        notify_timing_regression_history_window = parse_non_negative_int(
            str(args.notify_timing_regression_history_window),
            default=0,
            field="notify-timing-regression-history-window",
        )
        notify_timing_regression_history_dir = str(args.notify_timing_regression_history_dir).strip()
        notify_timing_regression_history_outlier_method = normalize_enum(
            raw=str(args.notify_timing_regression_history_outlier_method),
            default="none",
            field="notify-timing-regression-history-outlier-method",
            allowed={"none", "iqr"},
        )
        notify_timing_regression_history_trim_ratio = parse_non_negative_float(
            str(args.notify_timing_regression_history_trim_ratio),
            default=0.0,
            field="notify-timing-regression-history-trim-ratio",
        )
        if notify_timing_regression_history_trim_ratio >= 0.5:
            raise ValueError("notify-timing-regression-history-trim-ratio must be < 0.5")
        notify_phase4_primary_warn_ratio = parse_non_negative_float(
            str(args.notify_phase4_primary_warn_ratio),
            default=0.0,
            field="notify-phase4-primary-warn-ratio",
        )
        if notify_phase4_primary_warn_ratio > 1.0:
            raise ValueError("notify-phase4-primary-warn-ratio must be <= 1")
        notify_phase4_primary_hold_ratio = parse_non_negative_float(
            str(args.notify_phase4_primary_hold_ratio),
            default=0.0,
            field="notify-phase4-primary-hold-ratio",
        )
        if notify_phase4_primary_hold_ratio > 1.0:
            raise ValueError("notify-phase4-primary-hold-ratio must be <= 1")
        notify_phase4_primary_module_warn_thresholds_map = parse_phase4_secondary_module_warn_thresholds(
            str(args.notify_phase4_primary_module_warn_thresholds),
            field="notify-phase4-primary-module-warn-thresholds",
        )
        notify_phase4_primary_module_warn_thresholds = ",".join(
            f"{module}={notify_phase4_primary_module_warn_thresholds_map[module]:g}"
            for module in sorted(notify_phase4_primary_module_warn_thresholds_map.keys())
        )
        notify_phase4_primary_module_hold_thresholds_map = parse_phase4_secondary_module_warn_thresholds(
            str(args.notify_phase4_primary_module_hold_thresholds),
            field="notify-phase4-primary-module-hold-thresholds",
        )
        notify_phase4_primary_module_hold_thresholds = ",".join(
            f"{module}={notify_phase4_primary_module_hold_thresholds_map[module]:g}"
            for module in sorted(notify_phase4_primary_module_hold_thresholds_map.keys())
        )
        notify_phase4_secondary_warn_ratio = parse_non_negative_float(
            str(args.notify_phase4_secondary_warn_ratio),
            default=0.0,
            field="notify-phase4-secondary-warn-ratio",
        )
        if notify_phase4_secondary_warn_ratio > 1.0:
            raise ValueError("notify-phase4-secondary-warn-ratio must be <= 1")
        notify_phase4_secondary_hold_ratio = parse_non_negative_float(
            str(args.notify_phase4_secondary_hold_ratio),
            default=0.0,
            field="notify-phase4-secondary-hold-ratio",
        )
        if notify_phase4_secondary_hold_ratio > 1.0:
            raise ValueError("notify-phase4-secondary-hold-ratio must be <= 1")
        notify_phase4_secondary_warn_min_modules = parse_positive_int(
            str(args.notify_phase4_secondary_warn_min_modules),
            default=1,
            field="notify-phase4-secondary-warn-min-modules",
        )
        notify_phase4_secondary_module_warn_thresholds_map = parse_phase4_secondary_module_warn_thresholds(
            str(args.notify_phase4_secondary_module_warn_thresholds),
            field="notify-phase4-secondary-module-warn-thresholds",
        )
        notify_phase4_secondary_module_warn_thresholds = ",".join(
            f"{module}={notify_phase4_secondary_module_warn_thresholds_map[module]:g}"
            for module in sorted(notify_phase4_secondary_module_warn_thresholds_map.keys())
        )
        notify_phase4_secondary_module_hold_thresholds_map = parse_phase4_secondary_module_warn_thresholds(
            str(args.notify_phase4_secondary_module_hold_thresholds),
            field="notify-phase4-secondary-module-hold-thresholds",
        )
        notify_phase4_secondary_module_hold_thresholds = ",".join(
            f"{module}={notify_phase4_secondary_module_hold_thresholds_map[module]:g}"
            for module in sorted(notify_phase4_secondary_module_hold_thresholds_map.keys())
        )
        notify_phase3_vehicle_final_speed_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_speed_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-final-speed-warn-max",
        )
        notify_phase3_vehicle_final_speed_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_speed_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-final-speed-hold-max",
        )
        notify_phase3_vehicle_final_position_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_position_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-final-position-warn-max",
        )
        notify_phase3_vehicle_final_position_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_position_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-final-position-hold-max",
        )
        notify_phase3_vehicle_delta_speed_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_speed_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-speed-warn-max",
        )
        notify_phase3_vehicle_delta_speed_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_speed_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-speed-hold-max",
        )
        notify_phase3_vehicle_delta_position_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_position_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-position-warn-max",
        )
        notify_phase3_vehicle_delta_position_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_position_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-position-hold-max",
        )
        notify_phase3_vehicle_final_heading_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_heading_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-final-heading-abs-warn-max",
        )
        notify_phase3_vehicle_final_heading_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_heading_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-final-heading-abs-hold-max",
        )
        notify_phase3_vehicle_final_lateral_position_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_lateral_position_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-final-lateral-position-abs-warn-max",
        )
        notify_phase3_vehicle_final_lateral_position_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_final_lateral_position_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-final-lateral-position-abs-hold-max",
        )
        notify_phase3_vehicle_delta_heading_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_heading_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-heading-abs-warn-max",
        )
        notify_phase3_vehicle_delta_heading_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_heading_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-heading-abs-hold-max",
        )
        notify_phase3_vehicle_delta_lateral_position_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_lateral_position_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-lateral-position-abs-warn-max",
        )
        notify_phase3_vehicle_delta_lateral_position_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_lateral_position_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-lateral-position-abs-hold-max",
        )
        notify_phase3_vehicle_yaw_rate_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_yaw_rate_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-yaw-rate-abs-warn-max",
        )
        notify_phase3_vehicle_yaw_rate_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_yaw_rate_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-yaw-rate-abs-hold-max",
        )
        notify_phase3_vehicle_delta_yaw_rate_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_yaw_rate_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-yaw-rate-abs-warn-max",
        )
        notify_phase3_vehicle_delta_yaw_rate_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_delta_yaw_rate_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-delta-yaw-rate-abs-hold-max",
        )
        notify_phase3_vehicle_lateral_velocity_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_velocity_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-velocity-abs-warn-max",
        )
        notify_phase3_vehicle_lateral_velocity_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_velocity_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-velocity-abs-hold-max",
        )
        notify_phase3_vehicle_accel_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_accel_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-accel-abs-warn-max",
        )
        notify_phase3_vehicle_accel_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_accel_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-accel-abs-hold-max",
        )
        notify_phase3_vehicle_lateral_accel_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_accel_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-accel-abs-warn-max",
        )
        notify_phase3_vehicle_lateral_accel_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_accel_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-accel-abs-hold-max",
        )
        notify_phase3_vehicle_yaw_accel_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_yaw_accel_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-yaw-accel-abs-warn-max",
        )
        notify_phase3_vehicle_yaw_accel_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_yaw_accel_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-yaw-accel-abs-hold-max",
        )
        notify_phase3_vehicle_jerk_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_jerk_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-jerk-abs-warn-max",
        )
        notify_phase3_vehicle_jerk_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_jerk_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-jerk-abs-hold-max",
        )
        notify_phase3_vehicle_lateral_jerk_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_jerk_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-jerk-abs-warn-max",
        )
        notify_phase3_vehicle_lateral_jerk_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_jerk_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-jerk-abs-hold-max",
        )
        notify_phase3_vehicle_yaw_jerk_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_yaw_jerk_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-yaw-jerk-abs-warn-max",
        )
        notify_phase3_vehicle_yaw_jerk_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_yaw_jerk_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-yaw-jerk-abs-hold-max",
        )
        notify_phase3_vehicle_lateral_position_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_position_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-position-abs-warn-max",
        )
        notify_phase3_vehicle_lateral_position_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_lateral_position_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-lateral-position-abs-hold-max",
        )
        notify_phase3_vehicle_road_grade_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_road_grade_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-road-grade-abs-warn-max",
        )
        notify_phase3_vehicle_road_grade_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_road_grade_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-road-grade-abs-hold-max",
        )
        notify_phase3_vehicle_grade_force_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_grade_force_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-grade-force-warn-max",
        )
        notify_phase3_vehicle_grade_force_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_grade_force_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-grade-force-hold-max",
        )
        notify_phase3_vehicle_control_overlap_ratio_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_control_overlap_ratio_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-control-overlap-ratio-warn-max",
        )
        if notify_phase3_vehicle_control_overlap_ratio_warn_max > 1.0:
            raise ValueError("notify-phase3-vehicle-control-overlap-ratio-warn-max must be <= 1")
        notify_phase3_vehicle_control_overlap_ratio_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_control_overlap_ratio_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-control-overlap-ratio-hold-max",
        )
        if notify_phase3_vehicle_control_overlap_ratio_hold_max > 1.0:
            raise ValueError("notify-phase3-vehicle-control-overlap-ratio-hold-max must be <= 1")
        notify_phase3_vehicle_control_steering_rate_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_control_steering_rate_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-control-steering-rate-warn-max",
        )
        notify_phase3_vehicle_control_steering_rate_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_control_steering_rate_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-control-steering-rate-hold-max",
        )
        notify_phase3_vehicle_control_throttle_plus_brake_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_control_throttle_plus_brake_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-control-throttle-plus-brake-warn-max",
        )
        notify_phase3_vehicle_control_throttle_plus_brake_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_control_throttle_plus_brake_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-control-throttle-plus-brake-hold-max",
        )
        notify_phase3_vehicle_speed_tracking_error_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_speed_tracking_error_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-speed-tracking-error-warn-max",
        )
        notify_phase3_vehicle_speed_tracking_error_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_speed_tracking_error_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-speed-tracking-error-hold-max",
        )
        notify_phase3_vehicle_speed_tracking_error_abs_warn_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_speed_tracking_error_abs_warn_max),
            default=0.0,
            field="notify-phase3-vehicle-speed-tracking-error-abs-warn-max",
        )
        notify_phase3_vehicle_speed_tracking_error_abs_hold_max = parse_non_negative_float(
            str(args.notify_phase3_vehicle_speed_tracking_error_abs_hold_max),
            default=0.0,
            field="notify-phase3-vehicle-speed-tracking-error-abs-hold-max",
        )
        notify_phase3_lane_risk_min_ttc_same_lane_warn_min = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_min_ttc_same_lane_warn_min),
            default=0.0,
            field="notify-phase3-lane-risk-min-ttc-same-lane-warn-min",
        )
        notify_phase3_lane_risk_min_ttc_same_lane_hold_min = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_min_ttc_same_lane_hold_min),
            default=0.0,
            field="notify-phase3-lane-risk-min-ttc-same-lane-hold-min",
        )
        notify_phase3_lane_risk_min_ttc_adjacent_lane_warn_min = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_min_ttc_adjacent_lane_warn_min),
            default=0.0,
            field="notify-phase3-lane-risk-min-ttc-adjacent-lane-warn-min",
        )
        notify_phase3_lane_risk_min_ttc_adjacent_lane_hold_min = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_min_ttc_adjacent_lane_hold_min),
            default=0.0,
            field="notify-phase3-lane-risk-min-ttc-adjacent-lane-hold-min",
        )
        notify_phase3_lane_risk_min_ttc_any_lane_warn_min = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_min_ttc_any_lane_warn_min),
            default=0.0,
            field="notify-phase3-lane-risk-min-ttc-any-lane-warn-min",
        )
        notify_phase3_lane_risk_min_ttc_any_lane_hold_min = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_min_ttc_any_lane_hold_min),
            default=0.0,
            field="notify-phase3-lane-risk-min-ttc-any-lane-hold-min",
        )
        notify_phase3_lane_risk_ttc_under_3s_same_lane_warn_max = parse_non_negative_int(
            str(args.notify_phase3_lane_risk_ttc_under_3s_same_lane_warn_max),
            default=0,
            field="notify-phase3-lane-risk-ttc-under-3s-same-lane-warn-max",
        )
        notify_phase3_lane_risk_ttc_under_3s_same_lane_hold_max = parse_non_negative_int(
            str(args.notify_phase3_lane_risk_ttc_under_3s_same_lane_hold_max),
            default=0,
            field="notify-phase3-lane-risk-ttc-under-3s-same-lane-hold-max",
        )
        notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max = parse_non_negative_int(
            str(args.notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max),
            default=0,
            field="notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-warn-max",
        )
        notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max = parse_non_negative_int(
            str(args.notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max),
            default=0,
            field="notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-hold-max",
        )
        notify_phase3_lane_risk_ttc_under_3s_any_lane_warn_max = parse_non_negative_int(
            str(args.notify_phase3_lane_risk_ttc_under_3s_any_lane_warn_max),
            default=0,
            field="notify-phase3-lane-risk-ttc-under-3s-any-lane-warn-max",
        )
        notify_phase3_lane_risk_ttc_under_3s_any_lane_hold_max = parse_non_negative_int(
            str(args.notify_phase3_lane_risk_ttc_under_3s_any_lane_hold_max),
            default=0,
            field="notify-phase3-lane-risk-ttc-under-3s-any-lane-hold-max",
        )
        notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max),
            default=0.0,
            field="notify-phase3-lane-risk-ttc-under-3s-same-lane-ratio-warn-max",
        )
        if notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max > 1.0:
            raise ValueError("notify-phase3-lane-risk-ttc-under-3s-same-lane-ratio-warn-max must be <= 1")
        notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max),
            default=0.0,
            field="notify-phase3-lane-risk-ttc-under-3s-same-lane-ratio-hold-max",
        )
        if notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max > 1.0:
            raise ValueError("notify-phase3-lane-risk-ttc-under-3s-same-lane-ratio-hold-max must be <= 1")
        notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max),
            default=0.0,
            field="notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-warn-max",
        )
        if notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max > 1.0:
            raise ValueError("notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-warn-max must be <= 1")
        notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max),
            default=0.0,
            field="notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-hold-max",
        )
        if notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max > 1.0:
            raise ValueError("notify-phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-hold-max must be <= 1")
        notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max),
            default=0.0,
            field="notify-phase3-lane-risk-ttc-under-3s-any-lane-ratio-warn-max",
        )
        if notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max > 1.0:
            raise ValueError("notify-phase3-lane-risk-ttc-under-3s-any-lane-ratio-warn-max must be <= 1")
        notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max = parse_non_negative_float(
            str(args.notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max),
            default=0.0,
            field="notify-phase3-lane-risk-ttc-under-3s-any-lane-ratio-hold-max",
        )
        if notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max > 1.0:
            raise ValueError("notify-phase3-lane-risk-ttc-under-3s-any-lane-ratio-hold-max must be <= 1")
        notify_phase3_dataset_traffic_run_summary_warn_min = parse_non_negative_int(
            str(args.notify_phase3_dataset_traffic_run_summary_warn_min),
            default=0,
            field="notify-phase3-dataset-traffic-run-summary-warn-min",
        )
        notify_phase3_dataset_traffic_run_summary_hold_min = parse_non_negative_int(
            str(args.notify_phase3_dataset_traffic_run_summary_hold_min),
            default=0,
            field="notify-phase3-dataset-traffic-run-summary-hold-min",
        )
        notify_phase3_dataset_traffic_profile_count_warn_min = parse_non_negative_int(
            str(args.notify_phase3_dataset_traffic_profile_count_warn_min),
            default=0,
            field="notify-phase3-dataset-traffic-profile-count-warn-min",
        )
        notify_phase3_dataset_traffic_profile_count_hold_min = parse_non_negative_int(
            str(args.notify_phase3_dataset_traffic_profile_count_hold_min),
            default=0,
            field="notify-phase3-dataset-traffic-profile-count-hold-min",
        )
        notify_phase3_dataset_traffic_actor_pattern_count_warn_min = parse_non_negative_int(
            str(args.notify_phase3_dataset_traffic_actor_pattern_count_warn_min),
            default=0,
            field="notify-phase3-dataset-traffic-actor-pattern-count-warn-min",
        )
        notify_phase3_dataset_traffic_actor_pattern_count_hold_min = parse_non_negative_int(
            str(args.notify_phase3_dataset_traffic_actor_pattern_count_hold_min),
            default=0,
            field="notify-phase3-dataset-traffic-actor-pattern-count-hold-min",
        )
        notify_phase3_dataset_traffic_avg_npc_count_warn_min = parse_non_negative_float(
            str(args.notify_phase3_dataset_traffic_avg_npc_count_warn_min),
            default=0.0,
            field="notify-phase3-dataset-traffic-avg-npc-count-warn-min",
        )
        notify_phase3_dataset_traffic_avg_npc_count_hold_min = parse_non_negative_float(
            str(args.notify_phase3_dataset_traffic_avg_npc_count_hold_min),
            default=0.0,
            field="notify-phase3-dataset-traffic-avg-npc-count-hold-min",
        )
        notify_phase3_core_sim_min_ttc_same_lane_warn_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_min_ttc_same_lane_warn_min),
            default=0.0,
            field="notify-phase3-core-sim-min-ttc-same-lane-warn-min",
        )
        notify_phase3_core_sim_min_ttc_same_lane_hold_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_min_ttc_same_lane_hold_min),
            default=0.0,
            field="notify-phase3-core-sim-min-ttc-same-lane-hold-min",
        )
        notify_phase3_core_sim_min_ttc_any_lane_warn_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_min_ttc_any_lane_warn_min),
            default=0.0,
            field="notify-phase3-core-sim-min-ttc-any-lane-warn-min",
        )
        notify_phase3_core_sim_min_ttc_any_lane_hold_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_min_ttc_any_lane_hold_min),
            default=0.0,
            field="notify-phase3-core-sim-min-ttc-any-lane-hold-min",
        )
        notify_phase3_core_sim_collision_warn_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_collision_warn_max),
            default=0,
            field="notify-phase3-core-sim-collision-warn-max",
        )
        notify_phase3_core_sim_collision_hold_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_collision_hold_max),
            default=0,
            field="notify-phase3-core-sim-collision-hold-max",
        )
        notify_phase3_core_sim_timeout_warn_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_timeout_warn_max),
            default=0,
            field="notify-phase3-core-sim-timeout-warn-max",
        )
        notify_phase3_core_sim_timeout_hold_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_timeout_hold_max),
            default=0,
            field="notify-phase3-core-sim-timeout-hold-max",
        )
        notify_phase3_core_sim_gate_hold_warn_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_gate_hold_warn_max),
            default=0,
            field="notify-phase3-core-sim-gate-hold-warn-max",
        )
        notify_phase3_core_sim_gate_hold_hold_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_gate_hold_hold_max),
            default=0,
            field="notify-phase3-core-sim-gate-hold-hold-max",
        )
        notify_phase3_core_sim_matrix_min_ttc_same_lane_warn_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_matrix_min_ttc_same_lane_warn_min),
            default=0.0,
            field="notify-phase3-core-sim-matrix-min-ttc-same-lane-warn-min",
        )
        notify_phase3_core_sim_matrix_min_ttc_same_lane_hold_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_matrix_min_ttc_same_lane_hold_min),
            default=0.0,
            field="notify-phase3-core-sim-matrix-min-ttc-same-lane-hold-min",
        )
        notify_phase3_core_sim_matrix_min_ttc_any_lane_warn_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_matrix_min_ttc_any_lane_warn_min),
            default=0.0,
            field="notify-phase3-core-sim-matrix-min-ttc-any-lane-warn-min",
        )
        notify_phase3_core_sim_matrix_min_ttc_any_lane_hold_min = parse_non_negative_float(
            str(args.notify_phase3_core_sim_matrix_min_ttc_any_lane_hold_min),
            default=0.0,
            field="notify-phase3-core-sim-matrix-min-ttc-any-lane-hold-min",
        )
        notify_phase3_core_sim_matrix_failed_cases_warn_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_matrix_failed_cases_warn_max),
            default=0,
            field="notify-phase3-core-sim-matrix-failed-cases-warn-max",
        )
        notify_phase3_core_sim_matrix_failed_cases_hold_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_matrix_failed_cases_hold_max),
            default=0,
            field="notify-phase3-core-sim-matrix-failed-cases-hold-max",
        )
        notify_phase3_core_sim_matrix_collision_cases_warn_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_matrix_collision_cases_warn_max),
            default=0,
            field="notify-phase3-core-sim-matrix-collision-cases-warn-max",
        )
        notify_phase3_core_sim_matrix_collision_cases_hold_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_matrix_collision_cases_hold_max),
            default=0,
            field="notify-phase3-core-sim-matrix-collision-cases-hold-max",
        )
        notify_phase3_core_sim_matrix_timeout_cases_warn_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_matrix_timeout_cases_warn_max),
            default=0,
            field="notify-phase3-core-sim-matrix-timeout-cases-warn-max",
        )
        notify_phase3_core_sim_matrix_timeout_cases_hold_max = parse_non_negative_int(
            str(args.notify_phase3_core_sim_matrix_timeout_cases_hold_max),
            default=0,
            field="notify-phase3-core-sim-matrix-timeout-cases-hold-max",
        )
        notify_runtime_lane_execution_warn_min_exec_rows = parse_non_negative_int(
            str(args.notify_runtime_lane_execution_warn_min_exec_rows),
            default=0,
            field="notify-runtime-lane-execution-warn-min-exec-rows",
        )
        notify_runtime_lane_execution_hold_min_exec_rows = parse_non_negative_int(
            str(args.notify_runtime_lane_execution_hold_min_exec_rows),
            default=0,
            field="notify-runtime-lane-execution-hold-min-exec-rows",
        )
        notify_runtime_lane_phase2_rig_sweep_radar_alignment_degraded_drop_min = parse_non_negative_float(
            str(args.notify_runtime_lane_phase2_rig_sweep_radar_alignment_degraded_drop_min),
            default=0.05,
            field="notify-runtime-lane-phase2-rig-sweep-radar-alignment-degraded-drop-min",
        )
        notify_runtime_lane_phase2_rig_sweep_radar_alignment_hold_effective_drop_min = (
            parse_non_negative_float(
                str(args.notify_runtime_lane_phase2_rig_sweep_radar_alignment_hold_effective_drop_min),
                default=0.10,
                field="notify-runtime-lane-phase2-rig-sweep-radar-alignment-hold-effective-drop-min",
            )
        )
        notify_runtime_lane_phase2_rig_sweep_radar_alignment_hold_degraded_metric_min_count = (
            parse_non_negative_int(
                str(args.notify_runtime_lane_phase2_rig_sweep_radar_alignment_hold_degraded_metric_min_count),
                default=2,
                field=(
                    "notify-runtime-lane-phase2-rig-sweep-radar-alignment-hold-degraded-metric-min-count"
                ),
            )
        )
        notify_runtime_lane_phase2_rig_sweep_radar_alignment_non_positive_warn_max_delta = (
            parse_non_negative_float(
                str(args.notify_runtime_lane_phase2_rig_sweep_radar_alignment_non_positive_warn_max_delta),
                default=0.0,
                field=(
                    "notify-runtime-lane-phase2-rig-sweep-radar-alignment-non-positive-warn-max-delta"
                ),
            )
        )
        notify_runtime_evidence_compare_warn_min_artifacts_with_diffs = parse_non_negative_int(
            str(args.notify_runtime_evidence_compare_warn_min_artifacts_with_diffs),
            default=0,
            field="notify-runtime-evidence-compare-warn-min-artifacts-with-diffs",
        )
        notify_runtime_evidence_compare_hold_min_artifacts_with_diffs = parse_non_negative_int(
            str(args.notify_runtime_evidence_compare_hold_min_artifacts_with_diffs),
            default=0,
            field="notify-runtime-evidence-compare-hold-min-artifacts-with-diffs",
        )
        notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count = parse_non_negative_int(
            str(args.notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count),
            default=0,
            field="notify-runtime-evidence-compare-warn-min-interop-import-mode-diff-count",
        )
        notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count = parse_non_negative_int(
            str(args.notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count),
            default=0,
            field="notify-runtime-evidence-compare-hold-min-interop-import-mode-diff-count",
        )
        notify_runtime_evidence_interop_contract_checked_warn_min = parse_non_negative_int(
            str(args.notify_runtime_evidence_interop_contract_checked_warn_min),
            default=0,
            field="notify-runtime-evidence-interop-contract-checked-warn-min",
        )
        notify_runtime_evidence_interop_contract_checked_hold_min = parse_non_negative_int(
            str(args.notify_runtime_evidence_interop_contract_checked_hold_min),
            default=0,
            field="notify-runtime-evidence-interop-contract-checked-hold-min",
        )
        notify_runtime_evidence_interop_contract_fail_warn_max = parse_non_negative_int(
            str(args.notify_runtime_evidence_interop_contract_fail_warn_max),
            default=0,
            field="notify-runtime-evidence-interop-contract-fail-warn-max",
        )
        notify_runtime_evidence_interop_contract_fail_hold_max = parse_non_negative_int(
            str(args.notify_runtime_evidence_interop_contract_fail_hold_max),
            default=0,
            field="notify-runtime-evidence-interop-contract-fail-hold-max",
        )
        notify_phase2_map_routing_unreachable_lanes_warn_max = parse_non_negative_int(
            str(args.notify_phase2_map_routing_unreachable_lanes_warn_max),
            default=0,
            field="notify-phase2-map-routing-unreachable-lanes-warn-max",
        )
        notify_phase2_map_routing_unreachable_lanes_hold_max = parse_non_negative_int(
            str(args.notify_phase2_map_routing_unreachable_lanes_hold_max),
            default=0,
            field="notify-phase2-map-routing-unreachable-lanes-hold-max",
        )
        notify_phase2_map_routing_non_reciprocal_links_warn_max = parse_non_negative_int(
            str(args.notify_phase2_map_routing_non_reciprocal_links_warn_max),
            default=0,
            field="notify-phase2-map-routing-non-reciprocal-links-warn-max",
        )
        notify_phase2_map_routing_non_reciprocal_links_hold_max = parse_non_negative_int(
            str(args.notify_phase2_map_routing_non_reciprocal_links_hold_max),
            default=0,
            field="notify-phase2-map-routing-non-reciprocal-links-hold-max",
        )
        notify_phase2_map_routing_continuity_gap_warn_max = parse_non_negative_int(
            str(args.notify_phase2_map_routing_continuity_gap_warn_max),
            default=0,
            field="notify-phase2-map-routing-continuity-gap-warn-max",
        )
        notify_phase2_map_routing_continuity_gap_hold_max = parse_non_negative_int(
            str(args.notify_phase2_map_routing_continuity_gap_hold_max),
            default=0,
            field="notify-phase2-map-routing-continuity-gap-hold-max",
        )
        notify_phase2_sensor_fidelity_score_avg_warn_min = parse_non_negative_float(
            str(args.notify_phase2_sensor_fidelity_score_avg_warn_min),
            default=0.0,
            field="notify-phase2-sensor-fidelity-score-avg-warn-min",
        )
        notify_phase2_sensor_fidelity_score_avg_hold_min = parse_non_negative_float(
            str(args.notify_phase2_sensor_fidelity_score_avg_hold_min),
            default=0.0,
            field="notify-phase2-sensor-fidelity-score-avg-hold-min",
        )
        notify_phase2_sensor_frame_count_avg_warn_min = parse_non_negative_float(
            str(args.notify_phase2_sensor_frame_count_avg_warn_min),
            default=0.0,
            field="notify-phase2-sensor-frame-count-avg-warn-min",
        )
        notify_phase2_sensor_frame_count_avg_hold_min = parse_non_negative_float(
            str(args.notify_phase2_sensor_frame_count_avg_hold_min),
            default=0.0,
            field="notify-phase2-sensor-frame-count-avg-hold-min",
        )
        notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max = parse_non_negative_float(
            str(args.notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max),
            default=0.0,
            field="notify-phase2-sensor-camera-noise-stddev-px-avg-warn-max",
        )
        notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max = parse_non_negative_float(
            str(args.notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max),
            default=0.0,
            field="notify-phase2-sensor-camera-noise-stddev-px-avg-hold-max",
        )
        notify_phase2_sensor_lidar_point_count_avg_warn_min = parse_non_negative_float(
            str(args.notify_phase2_sensor_lidar_point_count_avg_warn_min),
            default=0.0,
            field="notify-phase2-sensor-lidar-point-count-avg-warn-min",
        )
        notify_phase2_sensor_lidar_point_count_avg_hold_min = parse_non_negative_float(
            str(args.notify_phase2_sensor_lidar_point_count_avg_hold_min),
            default=0.0,
            field="notify-phase2-sensor-lidar-point-count-avg-hold-min",
        )
        notify_phase2_sensor_radar_false_positive_rate_avg_warn_max = parse_non_negative_float(
            str(args.notify_phase2_sensor_radar_false_positive_rate_avg_warn_max),
            default=0.0,
            field="notify-phase2-sensor-radar-false-positive-rate-avg-warn-max",
        )
        notify_phase2_sensor_radar_false_positive_rate_avg_hold_max = parse_non_negative_float(
            str(args.notify_phase2_sensor_radar_false_positive_rate_avg_hold_max),
            default=0.0,
            field="notify-phase2-sensor-radar-false-positive-rate-avg-hold-max",
        )
        notify_phase2_log_replay_fail_warn_max = parse_non_negative_int(
            str(args.notify_phase2_log_replay_fail_warn_max),
            default=0,
            field="notify-phase2-log-replay-fail-warn-max",
        )
        notify_phase2_log_replay_fail_hold_max = parse_non_negative_int(
            str(args.notify_phase2_log_replay_fail_hold_max),
            default=0,
            field="notify-phase2-log-replay-fail-hold-max",
        )
        notify_phase2_log_replay_missing_summary_warn_max = parse_non_negative_int(
            str(args.notify_phase2_log_replay_missing_summary_warn_max),
            default=0,
            field="notify-phase2-log-replay-missing-summary-warn-max",
        )
        notify_phase2_log_replay_missing_summary_hold_max = parse_non_negative_int(
            str(args.notify_phase2_log_replay_missing_summary_hold_max),
            default=0,
            field="notify-phase2-log-replay-missing-summary-hold-max",
        )
        notify_runtime_native_smoke_fail_warn_max = parse_non_negative_int(
            str(args.notify_runtime_native_smoke_fail_warn_max),
            default=0,
            field="notify-runtime-native-smoke-fail-warn-max",
        )
        notify_runtime_native_smoke_fail_hold_max = parse_non_negative_int(
            str(args.notify_runtime_native_smoke_fail_hold_max),
            default=0,
            field="notify-runtime-native-smoke-fail-hold-max",
        )
        notify_runtime_native_smoke_partial_warn_max = parse_non_negative_int(
            str(args.notify_runtime_native_smoke_partial_warn_max),
            default=0,
            field="notify-runtime-native-smoke-partial-warn-max",
        )
        notify_runtime_native_smoke_partial_hold_max = parse_non_negative_int(
            str(args.notify_runtime_native_smoke_partial_hold_max),
            default=0,
            field="notify-runtime-native-smoke-partial-hold-max",
        )
        hold_reason_limit = parse_positive_int(
            str(args.hold_reason_limit),
            default=20,
            field="hold-reason-limit",
        )
        out_text = Path(args.out_text).resolve()
        out_json = Path(args.out_json).resolve()
        out_db = Path(args.out_db).resolve()
        notification_out = Path(args.notification_out_json).resolve() if str(args.notification_out_json).strip() else None

        version_a = str(args.version_a).strip()
        version_b = str(args.version_b).strip()
        if not version_a or not version_b:
            csv_a, csv_b = parse_csv_pair(args.sds_versions_csv)
            if not version_a:
                version_a = csv_a
            if not version_b:
                version_b = csv_b

        summary_cmd = [
            args.python_bin,
            str(Path(args.summary_builder).resolve()),
            "--artifacts-root",
            str(Path(args.artifacts_root).resolve()),
            "--release-prefix",
            release_prefix,
            "--out-text",
            str(out_text),
            "--out-json",
            str(out_json),
            "--out-db",
            str(out_db),
            "--hold-reason-limit",
            str(hold_reason_limit),
        ]
        summary_files_root = str(args.summary_files_root).strip()
        if summary_files_root:
            summary_cmd.extend(["--summary-files-root", str(Path(summary_files_root).resolve())])
        summary_files_subpath = str(args.summary_files_subpath).strip()
        if summary_files_subpath:
            summary_cmd.extend(["--summary-files-subpath", summary_files_subpath])
        pipeline_manifests_root = str(args.pipeline_manifests_root).strip()
        if pipeline_manifests_root:
            summary_cmd.extend(["--pipeline-manifests-root", str(Path(pipeline_manifests_root).resolve())])
        pipeline_manifests_subpath = str(args.pipeline_manifests_subpath).strip()
        if pipeline_manifests_subpath:
            summary_cmd.extend(["--pipeline-manifests-subpath", pipeline_manifests_subpath])
        if version_a and version_b:
            summary_cmd.extend(["--version-a", version_a, "--version-b", version_b])

        notification_cmd: list[str] | None = None
        notify_cmd: list[str] | None = None
        if notification_out is not None:
            notification_cmd = [
                args.python_bin,
                str(Path(args.notification_builder).resolve()),
                "--summary-json",
                str(out_json),
                "--out-json",
                str(notification_out),
                "--workflow-name",
                str(args.workflow_name),
            ]
            run_url = str(args.run_url).strip()
            if run_url:
                notification_cmd.extend(["--run-url", run_url])
            if notify_timing_total_warn_ms > 0:
                notification_cmd.extend(["--timing-total-warn-ms", str(notify_timing_total_warn_ms)])
            if notify_timing_regression_baseline_ms > 0:
                notification_cmd.extend(
                    ["--timing-regression-baseline-ms", str(notify_timing_regression_baseline_ms)]
                )
            if notify_timing_regression_warn_ratio > 0:
                notification_cmd.extend(
                    ["--timing-regression-warn-ratio", str(notify_timing_regression_warn_ratio)]
                )
            if notify_timing_regression_history_window > 0:
                notification_cmd.extend(
                    ["--timing-regression-history-window", str(notify_timing_regression_history_window)]
                )
                if notify_timing_regression_history_dir:
                    notification_cmd.extend(
                        [
                            "--timing-regression-history-dir",
                            str(Path(notify_timing_regression_history_dir).resolve()),
                        ]
                    )
                if notify_timing_regression_history_outlier_method != "none":
                    notification_cmd.extend(
                        [
                            "--timing-regression-history-outlier-method",
                            notify_timing_regression_history_outlier_method,
                        ]
                    )
            if notify_timing_regression_history_trim_ratio > 0:
                notification_cmd.extend(
                    [
                        "--timing-regression-history-trim-ratio",
                        str(notify_timing_regression_history_trim_ratio),
                    ]
                )
            if notify_phase4_primary_warn_ratio > 0:
                notification_cmd.extend(
                    ["--phase4-primary-warn-ratio", str(notify_phase4_primary_warn_ratio)]
                )
            if notify_phase4_primary_hold_ratio > 0:
                notification_cmd.extend(
                    ["--phase4-primary-hold-ratio", str(notify_phase4_primary_hold_ratio)]
                )
            if notify_phase4_primary_module_warn_thresholds:
                notification_cmd.extend(
                    [
                        "--phase4-primary-module-warn-thresholds",
                        notify_phase4_primary_module_warn_thresholds,
                    ]
                )
            if notify_phase4_primary_module_hold_thresholds:
                notification_cmd.extend(
                    [
                        "--phase4-primary-module-hold-thresholds",
                        notify_phase4_primary_module_hold_thresholds,
                    ]
                )
            if notify_phase4_secondary_warn_ratio > 0:
                notification_cmd.extend(
                    ["--phase4-secondary-warn-ratio", str(notify_phase4_secondary_warn_ratio)]
                )
            if notify_phase4_secondary_hold_ratio > 0:
                notification_cmd.extend(
                    ["--phase4-secondary-hold-ratio", str(notify_phase4_secondary_hold_ratio)]
                )
            if notify_phase4_secondary_warn_min_modules != 1:
                notification_cmd.extend(
                    ["--phase4-secondary-warn-min-modules", str(notify_phase4_secondary_warn_min_modules)]
                )
            if notify_phase4_secondary_module_warn_thresholds:
                notification_cmd.extend(
                    [
                        "--phase4-secondary-module-warn-thresholds",
                        notify_phase4_secondary_module_warn_thresholds,
                    ]
                )
            if notify_phase4_secondary_module_hold_thresholds:
                notification_cmd.extend(
                    [
                        "--phase4-secondary-module-hold-thresholds",
                        notify_phase4_secondary_module_hold_thresholds,
                    ]
                )
            if notify_phase3_vehicle_final_speed_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-speed-warn-max",
                        str(notify_phase3_vehicle_final_speed_warn_max),
                    ]
                )
            if notify_phase3_vehicle_final_speed_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-speed-hold-max",
                        str(notify_phase3_vehicle_final_speed_hold_max),
                    ]
                )
            if notify_phase3_vehicle_final_position_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-position-warn-max",
                        str(notify_phase3_vehicle_final_position_warn_max),
                    ]
                )
            if notify_phase3_vehicle_final_position_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-position-hold-max",
                        str(notify_phase3_vehicle_final_position_hold_max),
                    ]
                )
            if notify_phase3_vehicle_delta_speed_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-speed-warn-max",
                        str(notify_phase3_vehicle_delta_speed_warn_max),
                    ]
                )
            if notify_phase3_vehicle_delta_speed_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-speed-hold-max",
                        str(notify_phase3_vehicle_delta_speed_hold_max),
                    ]
                )
            if notify_phase3_vehicle_delta_position_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-position-warn-max",
                        str(notify_phase3_vehicle_delta_position_warn_max),
                    ]
                )
            if notify_phase3_vehicle_delta_position_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-position-hold-max",
                        str(notify_phase3_vehicle_delta_position_hold_max),
                    ]
                )
            if notify_phase3_vehicle_final_heading_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-heading-abs-warn-max",
                        str(notify_phase3_vehicle_final_heading_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_final_heading_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-heading-abs-hold-max",
                        str(notify_phase3_vehicle_final_heading_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_final_lateral_position_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-lateral-position-abs-warn-max",
                        str(notify_phase3_vehicle_final_lateral_position_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_final_lateral_position_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-final-lateral-position-abs-hold-max",
                        str(notify_phase3_vehicle_final_lateral_position_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_delta_heading_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-heading-abs-warn-max",
                        str(notify_phase3_vehicle_delta_heading_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_delta_heading_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-heading-abs-hold-max",
                        str(notify_phase3_vehicle_delta_heading_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_delta_lateral_position_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-lateral-position-abs-warn-max",
                        str(notify_phase3_vehicle_delta_lateral_position_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_delta_lateral_position_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-lateral-position-abs-hold-max",
                        str(notify_phase3_vehicle_delta_lateral_position_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_yaw_rate_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-yaw-rate-abs-warn-max",
                        str(notify_phase3_vehicle_yaw_rate_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_yaw_rate_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-yaw-rate-abs-hold-max",
                        str(notify_phase3_vehicle_yaw_rate_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_delta_yaw_rate_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-yaw-rate-abs-warn-max",
                        str(notify_phase3_vehicle_delta_yaw_rate_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_delta_yaw_rate_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-delta-yaw-rate-abs-hold-max",
                        str(notify_phase3_vehicle_delta_yaw_rate_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_velocity_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-velocity-abs-warn-max",
                        str(notify_phase3_vehicle_lateral_velocity_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_velocity_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-velocity-abs-hold-max",
                        str(notify_phase3_vehicle_lateral_velocity_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_accel_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-accel-abs-warn-max",
                        str(notify_phase3_vehicle_accel_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_accel_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-accel-abs-hold-max",
                        str(notify_phase3_vehicle_accel_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_accel_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-accel-abs-warn-max",
                        str(notify_phase3_vehicle_lateral_accel_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_accel_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-accel-abs-hold-max",
                        str(notify_phase3_vehicle_lateral_accel_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_yaw_accel_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-yaw-accel-abs-warn-max",
                        str(notify_phase3_vehicle_yaw_accel_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_yaw_accel_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-yaw-accel-abs-hold-max",
                        str(notify_phase3_vehicle_yaw_accel_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_jerk_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-jerk-abs-warn-max",
                        str(notify_phase3_vehicle_jerk_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_jerk_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-jerk-abs-hold-max",
                        str(notify_phase3_vehicle_jerk_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_jerk_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-jerk-abs-warn-max",
                        str(notify_phase3_vehicle_lateral_jerk_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_jerk_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-jerk-abs-hold-max",
                        str(notify_phase3_vehicle_lateral_jerk_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_yaw_jerk_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-yaw-jerk-abs-warn-max",
                        str(notify_phase3_vehicle_yaw_jerk_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_yaw_jerk_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-yaw-jerk-abs-hold-max",
                        str(notify_phase3_vehicle_yaw_jerk_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_position_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-position-abs-warn-max",
                        str(notify_phase3_vehicle_lateral_position_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_lateral_position_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-lateral-position-abs-hold-max",
                        str(notify_phase3_vehicle_lateral_position_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_road_grade_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-road-grade-abs-warn-max",
                        str(notify_phase3_vehicle_road_grade_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_road_grade_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-road-grade-abs-hold-max",
                        str(notify_phase3_vehicle_road_grade_abs_hold_max),
                    ]
                )
            if notify_phase3_vehicle_grade_force_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-grade-force-warn-max",
                        str(notify_phase3_vehicle_grade_force_warn_max),
                    ]
                )
            if notify_phase3_vehicle_grade_force_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-grade-force-hold-max",
                        str(notify_phase3_vehicle_grade_force_hold_max),
                    ]
                )
            if notify_phase3_vehicle_control_overlap_ratio_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-control-overlap-ratio-warn-max",
                        str(notify_phase3_vehicle_control_overlap_ratio_warn_max),
                    ]
                )
            if notify_phase3_vehicle_control_overlap_ratio_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-control-overlap-ratio-hold-max",
                        str(notify_phase3_vehicle_control_overlap_ratio_hold_max),
                    ]
                )
            if notify_phase3_vehicle_control_steering_rate_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-control-steering-rate-warn-max",
                        str(notify_phase3_vehicle_control_steering_rate_warn_max),
                    ]
                )
            if notify_phase3_vehicle_control_steering_rate_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-control-steering-rate-hold-max",
                        str(notify_phase3_vehicle_control_steering_rate_hold_max),
                    ]
                )
            if notify_phase3_vehicle_control_throttle_plus_brake_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-control-throttle-plus-brake-warn-max",
                        str(notify_phase3_vehicle_control_throttle_plus_brake_warn_max),
                    ]
                )
            if notify_phase3_vehicle_control_throttle_plus_brake_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-control-throttle-plus-brake-hold-max",
                        str(notify_phase3_vehicle_control_throttle_plus_brake_hold_max),
                    ]
                )
            if notify_phase3_vehicle_speed_tracking_error_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-speed-tracking-error-warn-max",
                        str(notify_phase3_vehicle_speed_tracking_error_warn_max),
                    ]
                )
            if notify_phase3_vehicle_speed_tracking_error_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-speed-tracking-error-hold-max",
                        str(notify_phase3_vehicle_speed_tracking_error_hold_max),
                    ]
                )
            if notify_phase3_vehicle_speed_tracking_error_abs_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-speed-tracking-error-abs-warn-max",
                        str(notify_phase3_vehicle_speed_tracking_error_abs_warn_max),
                    ]
                )
            if notify_phase3_vehicle_speed_tracking_error_abs_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-vehicle-speed-tracking-error-abs-hold-max",
                        str(notify_phase3_vehicle_speed_tracking_error_abs_hold_max),
                    ]
                )
            if notify_phase3_lane_risk_min_ttc_same_lane_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-min-ttc-same-lane-warn-min",
                        str(notify_phase3_lane_risk_min_ttc_same_lane_warn_min),
                    ]
                )
            if notify_phase3_lane_risk_min_ttc_same_lane_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-min-ttc-same-lane-hold-min",
                        str(notify_phase3_lane_risk_min_ttc_same_lane_hold_min),
                    ]
                )
            if notify_phase3_lane_risk_min_ttc_adjacent_lane_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-min-ttc-adjacent-lane-warn-min",
                        str(notify_phase3_lane_risk_min_ttc_adjacent_lane_warn_min),
                    ]
                )
            if notify_phase3_lane_risk_min_ttc_adjacent_lane_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-min-ttc-adjacent-lane-hold-min",
                        str(notify_phase3_lane_risk_min_ttc_adjacent_lane_hold_min),
                    ]
                )
            if notify_phase3_lane_risk_min_ttc_any_lane_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-min-ttc-any-lane-warn-min",
                        str(notify_phase3_lane_risk_min_ttc_any_lane_warn_min),
                    ]
                )
            if notify_phase3_lane_risk_min_ttc_any_lane_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-min-ttc-any-lane-hold-min",
                        str(notify_phase3_lane_risk_min_ttc_any_lane_hold_min),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_same_lane_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-same-lane-warn-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_same_lane_warn_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_same_lane_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-same-lane-hold-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_same_lane_hold_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-warn-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_warn_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-hold-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_hold_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_any_lane_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-any-lane-warn-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_any_lane_warn_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_any_lane_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-any-lane-hold-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_any_lane_hold_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-same-lane-ratio-warn-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_warn_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-same-lane-ratio-hold-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_same_lane_ratio_hold_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-warn-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_warn_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-adjacent-lane-ratio-hold-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_adjacent_lane_ratio_hold_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-any-lane-ratio-warn-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_warn_max),
                    ]
                )
            if notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-lane-risk-ttc-under-3s-any-lane-ratio-hold-max",
                        str(notify_phase3_lane_risk_ttc_under_3s_any_lane_ratio_hold_max),
                    ]
                )
            if notify_phase3_dataset_traffic_run_summary_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-run-summary-warn-min",
                        str(notify_phase3_dataset_traffic_run_summary_warn_min),
                    ]
                )
            if notify_phase3_dataset_traffic_run_summary_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-run-summary-hold-min",
                        str(notify_phase3_dataset_traffic_run_summary_hold_min),
                    ]
                )
            if notify_phase3_dataset_traffic_profile_count_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-profile-count-warn-min",
                        str(notify_phase3_dataset_traffic_profile_count_warn_min),
                    ]
                )
            if notify_phase3_dataset_traffic_profile_count_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-profile-count-hold-min",
                        str(notify_phase3_dataset_traffic_profile_count_hold_min),
                    ]
                )
            if notify_phase3_dataset_traffic_actor_pattern_count_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-actor-pattern-count-warn-min",
                        str(notify_phase3_dataset_traffic_actor_pattern_count_warn_min),
                    ]
                )
            if notify_phase3_dataset_traffic_actor_pattern_count_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-actor-pattern-count-hold-min",
                        str(notify_phase3_dataset_traffic_actor_pattern_count_hold_min),
                    ]
                )
            if notify_phase3_dataset_traffic_avg_npc_count_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-avg-npc-count-warn-min",
                        str(notify_phase3_dataset_traffic_avg_npc_count_warn_min),
                    ]
                )
            if notify_phase3_dataset_traffic_avg_npc_count_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-dataset-traffic-avg-npc-count-hold-min",
                        str(notify_phase3_dataset_traffic_avg_npc_count_hold_min),
                    ]
                )
            if notify_phase3_core_sim_min_ttc_same_lane_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-min-ttc-same-lane-warn-min",
                        str(notify_phase3_core_sim_min_ttc_same_lane_warn_min),
                    ]
                )
            if notify_phase3_core_sim_min_ttc_same_lane_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-min-ttc-same-lane-hold-min",
                        str(notify_phase3_core_sim_min_ttc_same_lane_hold_min),
                    ]
                )
            if notify_phase3_core_sim_min_ttc_any_lane_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-min-ttc-any-lane-warn-min",
                        str(notify_phase3_core_sim_min_ttc_any_lane_warn_min),
                    ]
                )
            if notify_phase3_core_sim_min_ttc_any_lane_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-min-ttc-any-lane-hold-min",
                        str(notify_phase3_core_sim_min_ttc_any_lane_hold_min),
                    ]
                )
            if notify_phase3_core_sim_collision_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-collision-warn-max",
                        str(notify_phase3_core_sim_collision_warn_max),
                    ]
                )
            if notify_phase3_core_sim_collision_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-collision-hold-max",
                        str(notify_phase3_core_sim_collision_hold_max),
                    ]
                )
            if notify_phase3_core_sim_timeout_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-timeout-warn-max",
                        str(notify_phase3_core_sim_timeout_warn_max),
                    ]
                )
            if notify_phase3_core_sim_timeout_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-timeout-hold-max",
                        str(notify_phase3_core_sim_timeout_hold_max),
                    ]
                )
            if notify_phase3_core_sim_gate_hold_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-gate-hold-warn-max",
                        str(notify_phase3_core_sim_gate_hold_warn_max),
                    ]
                )
            if notify_phase3_core_sim_gate_hold_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-gate-hold-hold-max",
                        str(notify_phase3_core_sim_gate_hold_hold_max),
                    ]
                )
            if notify_phase3_core_sim_matrix_min_ttc_same_lane_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-min-ttc-same-lane-warn-min",
                        str(notify_phase3_core_sim_matrix_min_ttc_same_lane_warn_min),
                    ]
                )
            if notify_phase3_core_sim_matrix_min_ttc_same_lane_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-min-ttc-same-lane-hold-min",
                        str(notify_phase3_core_sim_matrix_min_ttc_same_lane_hold_min),
                    ]
                )
            if notify_phase3_core_sim_matrix_min_ttc_any_lane_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-min-ttc-any-lane-warn-min",
                        str(notify_phase3_core_sim_matrix_min_ttc_any_lane_warn_min),
                    ]
                )
            if notify_phase3_core_sim_matrix_min_ttc_any_lane_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-min-ttc-any-lane-hold-min",
                        str(notify_phase3_core_sim_matrix_min_ttc_any_lane_hold_min),
                    ]
                )
            if notify_phase3_core_sim_matrix_failed_cases_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-failed-cases-warn-max",
                        str(notify_phase3_core_sim_matrix_failed_cases_warn_max),
                    ]
                )
            if notify_phase3_core_sim_matrix_failed_cases_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-failed-cases-hold-max",
                        str(notify_phase3_core_sim_matrix_failed_cases_hold_max),
                    ]
                )
            if notify_phase3_core_sim_matrix_collision_cases_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-collision-cases-warn-max",
                        str(notify_phase3_core_sim_matrix_collision_cases_warn_max),
                    ]
                )
            if notify_phase3_core_sim_matrix_collision_cases_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-collision-cases-hold-max",
                        str(notify_phase3_core_sim_matrix_collision_cases_hold_max),
                    ]
                )
            if notify_phase3_core_sim_matrix_timeout_cases_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-timeout-cases-warn-max",
                        str(notify_phase3_core_sim_matrix_timeout_cases_warn_max),
                    ]
                )
            if notify_phase3_core_sim_matrix_timeout_cases_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase3-core-sim-matrix-timeout-cases-hold-max",
                        str(notify_phase3_core_sim_matrix_timeout_cases_hold_max),
                    ]
                )
            if notify_runtime_lane_execution_warn_min_exec_rows > 0:
                notification_cmd.extend(
                    [
                        "--runtime-lane-execution-warn-min-exec-rows",
                        str(notify_runtime_lane_execution_warn_min_exec_rows),
                    ]
                )
            if notify_runtime_lane_execution_hold_min_exec_rows > 0:
                notification_cmd.extend(
                    [
                        "--runtime-lane-execution-hold-min-exec-rows",
                        str(notify_runtime_lane_execution_hold_min_exec_rows),
                    ]
                )
            notification_cmd.extend(
                [
                    "--runtime-lane-phase2-rig-sweep-radar-alignment-degraded-drop-min",
                    str(notify_runtime_lane_phase2_rig_sweep_radar_alignment_degraded_drop_min),
                    "--runtime-lane-phase2-rig-sweep-radar-alignment-hold-effective-drop-min",
                    str(notify_runtime_lane_phase2_rig_sweep_radar_alignment_hold_effective_drop_min),
                    "--runtime-lane-phase2-rig-sweep-radar-alignment-hold-degraded-metric-min-count",
                    str(notify_runtime_lane_phase2_rig_sweep_radar_alignment_hold_degraded_metric_min_count),
                    "--runtime-lane-phase2-rig-sweep-radar-alignment-non-positive-warn-max-delta",
                    str(notify_runtime_lane_phase2_rig_sweep_radar_alignment_non_positive_warn_max_delta),
                ]
            )
            if notify_runtime_evidence_compare_warn_min_artifacts_with_diffs > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-compare-warn-min-artifacts-with-diffs",
                        str(notify_runtime_evidence_compare_warn_min_artifacts_with_diffs),
                    ]
                )
            if notify_runtime_evidence_compare_hold_min_artifacts_with_diffs > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-compare-hold-min-artifacts-with-diffs",
                        str(notify_runtime_evidence_compare_hold_min_artifacts_with_diffs),
                    ]
                )
            if notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-compare-warn-min-interop-import-mode-diff-count",
                        str(notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count),
                    ]
                )
            if notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-compare-hold-min-interop-import-mode-diff-count",
                        str(notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count),
                    ]
                )
            if notify_runtime_evidence_interop_contract_checked_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-interop-contract-checked-warn-min",
                        str(notify_runtime_evidence_interop_contract_checked_warn_min),
                    ]
                )
            if notify_runtime_evidence_interop_contract_checked_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-interop-contract-checked-hold-min",
                        str(notify_runtime_evidence_interop_contract_checked_hold_min),
                    ]
                )
            if notify_runtime_evidence_interop_contract_fail_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-interop-contract-fail-warn-max",
                        str(notify_runtime_evidence_interop_contract_fail_warn_max),
                    ]
                )
            if notify_runtime_evidence_interop_contract_fail_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--runtime-evidence-interop-contract-fail-hold-max",
                        str(notify_runtime_evidence_interop_contract_fail_hold_max),
                    ]
                )
            if notify_phase2_map_routing_unreachable_lanes_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-map-routing-unreachable-lanes-warn-max",
                        str(notify_phase2_map_routing_unreachable_lanes_warn_max),
                    ]
                )
            if notify_phase2_map_routing_unreachable_lanes_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-map-routing-unreachable-lanes-hold-max",
                        str(notify_phase2_map_routing_unreachable_lanes_hold_max),
                    ]
                )
            if notify_phase2_map_routing_non_reciprocal_links_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-map-routing-non-reciprocal-links-warn-max",
                        str(notify_phase2_map_routing_non_reciprocal_links_warn_max),
                    ]
                )
            if notify_phase2_map_routing_non_reciprocal_links_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-map-routing-non-reciprocal-links-hold-max",
                        str(notify_phase2_map_routing_non_reciprocal_links_hold_max),
                    ]
                )
            if notify_phase2_map_routing_continuity_gap_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-map-routing-continuity-gap-warn-max",
                        str(notify_phase2_map_routing_continuity_gap_warn_max),
                    ]
                )
            if notify_phase2_map_routing_continuity_gap_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-map-routing-continuity-gap-hold-max",
                        str(notify_phase2_map_routing_continuity_gap_hold_max),
                    ]
                )
            if notify_phase2_sensor_fidelity_score_avg_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-fidelity-score-avg-warn-min",
                        str(notify_phase2_sensor_fidelity_score_avg_warn_min),
                    ]
                )
            if notify_phase2_sensor_fidelity_score_avg_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-fidelity-score-avg-hold-min",
                        str(notify_phase2_sensor_fidelity_score_avg_hold_min),
                    ]
                )
            if notify_phase2_sensor_frame_count_avg_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-frame-count-avg-warn-min",
                        str(notify_phase2_sensor_frame_count_avg_warn_min),
                    ]
                )
            if notify_phase2_sensor_frame_count_avg_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-frame-count-avg-hold-min",
                        str(notify_phase2_sensor_frame_count_avg_hold_min),
                    ]
                )
            if notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-camera-noise-stddev-px-avg-warn-max",
                        str(notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max),
                    ]
                )
            if notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-camera-noise-stddev-px-avg-hold-max",
                        str(notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max),
                    ]
                )
            if notify_phase2_sensor_lidar_point_count_avg_warn_min > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-lidar-point-count-avg-warn-min",
                        str(notify_phase2_sensor_lidar_point_count_avg_warn_min),
                    ]
                )
            if notify_phase2_sensor_lidar_point_count_avg_hold_min > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-lidar-point-count-avg-hold-min",
                        str(notify_phase2_sensor_lidar_point_count_avg_hold_min),
                    ]
                )
            if notify_phase2_sensor_radar_false_positive_rate_avg_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-radar-false-positive-rate-avg-warn-max",
                        str(notify_phase2_sensor_radar_false_positive_rate_avg_warn_max),
                    ]
                )
            if notify_phase2_sensor_radar_false_positive_rate_avg_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-sensor-radar-false-positive-rate-avg-hold-max",
                        str(notify_phase2_sensor_radar_false_positive_rate_avg_hold_max),
                    ]
                )
            if notify_phase2_log_replay_fail_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-log-replay-fail-warn-max",
                        str(notify_phase2_log_replay_fail_warn_max),
                    ]
                )
            if notify_phase2_log_replay_fail_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-log-replay-fail-hold-max",
                        str(notify_phase2_log_replay_fail_hold_max),
                    ]
                )
            if notify_phase2_log_replay_missing_summary_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-log-replay-missing-summary-warn-max",
                        str(notify_phase2_log_replay_missing_summary_warn_max),
                    ]
                )
            if notify_phase2_log_replay_missing_summary_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--phase2-log-replay-missing-summary-hold-max",
                        str(notify_phase2_log_replay_missing_summary_hold_max),
                    ]
                )
            if notify_runtime_native_smoke_fail_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--runtime-native-smoke-fail-warn-max",
                        str(notify_runtime_native_smoke_fail_warn_max),
                    ]
                )
            if notify_runtime_native_smoke_fail_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--runtime-native-smoke-fail-hold-max",
                        str(notify_runtime_native_smoke_fail_hold_max),
                    ]
                )
            if notify_runtime_native_smoke_partial_warn_max > 0:
                notification_cmd.extend(
                    [
                        "--runtime-native-smoke-partial-warn-max",
                        str(notify_runtime_native_smoke_partial_warn_max),
                    ]
                )
            if notify_runtime_native_smoke_partial_hold_max > 0:
                notification_cmd.extend(
                    [
                        "--runtime-native-smoke-partial-hold-max",
                        str(notify_runtime_native_smoke_partial_hold_max),
                    ]
                )

            notify_cmd = [
                args.python_bin,
                str(Path(args.notification_sender).resolve()),
                "--payload-json",
                str(notification_out),
                "--webhook-url",
                str(args.webhook_url),
                "--notify-on",
                notify_on,
                "--format",
                notify_format,
                "--timeout-sec",
                str(notify_timeout_sec),
                "--max-retries",
                str(notify_max_retries),
                "--retry-backoff-sec",
                str(notify_retry_backoff_sec),
            ]

        if args.dry_run:
            print(f"[cmd] {shell_join(summary_cmd)}")
            if notification_cmd is not None and notify_cmd is not None:
                print(f"[cmd] {shell_join(notification_cmd)}")
                print(f"[cmd] {render_cmd(notify_cmd, sensitive_flags={'--webhook-url'})}")
            print("[ok] dry-run=true")
            return 0

        failure_phase = SUMMARY_PHASE_BUILD_SUMMARY
        failure_command = render_cmd(summary_cmd)
        run_cmd(summary_cmd)

        if notification_cmd is not None and out_json.exists():
            failure_phase = SUMMARY_PHASE_BUILD_NOTIFICATION_PAYLOAD
            failure_command = render_cmd(notification_cmd)
            run_cmd(notification_cmd)
            if notification_out is not None and notification_out.exists():
                merge_runtime_threshold_drift_into_summary(
                    summary_json_path=out_json,
                    notification_json_path=notification_out,
                )
                merge_runtime_threshold_drift_into_summary_text(
                    summary_text_path=out_text,
                    notification_json_path=notification_out,
                )
                if fail_on_runtime_threshold_drift_hold:
                    hold_detected, drift_severity, drift_summary, hold_reason_keys = resolve_threshold_drift_hold_signal(
                        notification_out,
                        prefix="runtime",
                        hold_reason_fields=RUNTIME_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS,
                    )
                    if hold_detected:
                        threshold_drift_hold_policy_failures.append(
                            _format_threshold_drift_hold_policy_failure(
                                scope="runtime",
                                drift_severity=drift_severity,
                                drift_summary=drift_summary,
                                hold_reason_keys=hold_reason_keys,
                            )
                        )
                        threshold_drift_hold_policy_failure_reason_keys.extend(hold_reason_keys)
                if fail_on_phase3_core_sim_threshold_drift_hold:
                    hold_detected, drift_severity, drift_summary, hold_reason_keys = resolve_threshold_drift_hold_signal(
                        notification_out,
                        prefix="phase3_core_sim",
                        hold_reason_fields=PHASE3_CORE_SIM_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS,
                    )
                    if hold_detected:
                        threshold_drift_hold_policy_failures.append(
                            _format_threshold_drift_hold_policy_failure(
                                scope="phase3 core sim",
                                drift_severity=drift_severity,
                                drift_summary=drift_summary,
                                hold_reason_keys=hold_reason_keys,
                            )
                        )
                        threshold_drift_hold_policy_failure_reason_keys.extend(hold_reason_keys)
                if fail_on_phase3_lane_risk_threshold_drift_hold:
                    hold_detected, drift_severity, drift_summary, hold_reason_keys = resolve_threshold_drift_hold_signal(
                        notification_out,
                        prefix="phase3_lane_risk",
                        hold_reason_fields=PHASE3_LANE_RISK_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS,
                    )
                    if hold_detected:
                        threshold_drift_hold_policy_failures.append(
                            _format_threshold_drift_hold_policy_failure(
                                scope="phase3 lane risk",
                                drift_severity=drift_severity,
                                drift_summary=drift_summary,
                                hold_reason_keys=hold_reason_keys,
                            )
                        )
                        threshold_drift_hold_policy_failure_reason_keys.extend(hold_reason_keys)
                if fail_on_phase3_dataset_traffic_threshold_drift_hold:
                    hold_detected, drift_severity, drift_summary, hold_reason_keys = resolve_threshold_drift_hold_signal(
                        notification_out,
                        prefix="phase3_dataset_traffic",
                        hold_reason_fields=PHASE3_DATASET_TRAFFIC_THRESHOLD_DRIFT_HOLD_MISMATCH_FIELDS,
                    )
                    if hold_detected:
                        threshold_drift_hold_policy_failures.append(
                            _format_threshold_drift_hold_policy_failure(
                                scope="phase3 dataset traffic",
                                drift_severity=drift_severity,
                                drift_summary=drift_summary,
                                hold_reason_keys=hold_reason_keys,
                            )
                        )
                        threshold_drift_hold_policy_failure_reason_keys.extend(hold_reason_keys)
                if fail_on_phase2_log_replay_threshold_hold:
                    hold_detected, warning_severity, warning_summary, hold_reason_keys = resolve_warning_hold_signal(
                        notification_out,
                        warning_field="phase2_log_replay_warning",
                        warning_reason_field="phase2_log_replay_warning_reasons",
                        hold_reason_fields=PHASE2_LOG_REPLAY_HOLD_WARNING_REASON_FIELDS,
                    )
                    if hold_detected:
                        threshold_drift_hold_policy_failures.append(
                            _format_threshold_drift_hold_policy_failure(
                                scope="phase2 log replay",
                                drift_severity=warning_severity,
                                drift_summary=warning_summary,
                                hold_reason_keys=hold_reason_keys,
                            )
                        )
                        threshold_drift_hold_policy_failure_reason_keys.extend(hold_reason_keys)
                if fail_on_runtime_native_smoke_threshold_hold:
                    hold_detected, warning_severity, warning_summary, hold_reason_keys = resolve_warning_hold_signal(
                        notification_out,
                        warning_field="runtime_native_smoke_warning",
                        warning_reason_field="runtime_native_smoke_warning_reasons",
                        hold_reason_fields=RUNTIME_NATIVE_SMOKE_HOLD_WARNING_REASON_FIELDS,
                    )
                    if hold_detected:
                        threshold_drift_hold_policy_failures.append(
                            _format_threshold_drift_hold_policy_failure(
                                scope="runtime native smoke",
                                drift_severity=warning_severity,
                                drift_summary=warning_summary,
                                hold_reason_keys=hold_reason_keys,
                            )
                        )
                        threshold_drift_hold_policy_failure_reason_keys.extend(hold_reason_keys)
                if fail_on_runtime_native_evidence_compare_threshold_hold:
                    hold_detected, warning_severity, warning_summary, hold_reason_keys = resolve_warning_hold_signal(
                        notification_out,
                        warning_field="runtime_native_evidence_compare_warning",
                        warning_reason_field="runtime_native_evidence_compare_warning_reasons",
                        hold_reason_fields=RUNTIME_NATIVE_EVIDENCE_COMPARE_HOLD_WARNING_REASON_FIELDS,
                    )
                    if hold_detected:
                        threshold_drift_hold_policy_failures.append(
                            _format_threshold_drift_hold_policy_failure(
                                scope="runtime native evidence compare",
                                drift_severity=warning_severity,
                                drift_summary=warning_summary,
                                hold_reason_keys=hold_reason_keys,
                            )
                        )
                        threshold_drift_hold_policy_failure_reason_keys.extend(hold_reason_keys)
                if threshold_drift_hold_policy_failures:
                    persist_threshold_drift_hold_policy_failures_to_notification(
                        notification_json_path=notification_out,
                        policy_failures=threshold_drift_hold_policy_failures,
                        policy_failure_reason_keys=threshold_drift_hold_policy_failure_reason_keys,
                    )

        if notify_cmd is not None and notification_out is not None and notification_out.exists():
            failure_phase = SUMMARY_PHASE_SEND_NOTIFICATION
            failure_command = render_cmd(notify_cmd, sensitive_flags={"--webhook-url"})
            run_cmd(notify_cmd, sensitive_flags={"--webhook-url"})

        failure_phase = SUMMARY_PHASE_PUBLISH_SUMMARY
        failure_command = ""
        if threshold_drift_hold_policy_failures:
            persist_threshold_drift_hold_policy_failures(
                summary_json_path=out_json,
                summary_text_path=out_text,
                policy_failures=threshold_drift_hold_policy_failures,
                policy_failure_reason_keys=threshold_drift_hold_policy_failure_reason_keys,
            )
        publish_summary(
            python_bin=str(args.python_bin),
            renderer_path=str(args.markdown_renderer),
            summary_json_path=out_json,
            summary_text_path=out_text,
            title=str(args.summary_title),
            step_summary_file=step_summary_file,
        )
        if threshold_drift_hold_policy_failures:
            failure_phase = SUMMARY_PHASE_BUILD_NOTIFICATION_PAYLOAD
            failure_command = ""
            raise RuntimeError("; ".join(threshold_drift_hold_policy_failures))
        print(f"[ok] summary_text={out_text}")
        print(f"[ok] summary_json={out_json}")
        if notification_out is not None:
            print(f"[ok] notification_json={notification_out}")
        return 0
    except Exception as exc:
        message = normalize_exception_message(exc)
        details = {"phase": failure_phase}
        if failure_command:
            details["command"] = failure_command
        emit_ci_error(
            step_summary_file=step_summary_file,
            source="run_ci_summary.py",
            message=message,
            details=details,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
