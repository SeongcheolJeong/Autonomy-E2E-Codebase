#!/usr/bin/env python3
"""Compare two runtime evidence artifacts and emit a compact diff report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import load_labeled_json_object

SCHEMA_VERSION = "runtime_evidence_compare_v0"

PROFILE_COMPARE_FIELD_KEYS: tuple[str, ...] = (
    "status",
    "runtime",
    "validated",
    "runtime_available",
    "probe_checked",
    "probe_executed",
    "scenario_contract_status",
    "scene_result_status",
    "interop_contract_status",
    "interop_import_status",
    "interop_import_manifest_consistent",
    "interop_import_manifest_consistency_mode",
    "interop_import_export_consistency_mode",
    "interop_import_require_manifest_consistency_input",
    "interop_import_require_export_consistency_input",
)

PROFILE_COMPARE_NUMERIC_KEYS: tuple[str, ...] = (
    "scene_result_coverage_ratio",
    "scene_result_ego_travel_distance_m",
    "scenario_executed_step_count",
    "scene_result_executed_step_count",
    "interop_executed_step_count",
    "interop_imported_actor_count",
    "interop_import_actor_count_manifest",
    "interop_import_xosc_entity_count",
    "interop_import_xodr_road_count",
    "interop_import_xodr_total_road_length_m",
)

TOP_LEVEL_COMPARE_KEYS: tuple[str, ...] = (
    "release_prefix",
    "sim_runtime",
    "dry_run",
    "profile_count",
    "failure_count",
    "runtime_evidence_count",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two runtime evidence JSON artifacts")
    parser.add_argument("--left", required=True, help="Left runtime evidence JSON path")
    parser.add_argument("--right", required=True, help="Right runtime evidence JSON path")
    parser.add_argument("--left-label", default="left", help="Label for left artifact")
    parser.add_argument("--right-label", default="right", help="Label for right artifact")
    parser.add_argument("--out-json", required=True, help="Output JSON report path")
    parser.add_argument("--out-text", default="", help="Optional output text summary path")
    return parser.parse_args()


def _to_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return None


def _to_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        try:
            return float(token)
        except ValueError:
            return None
    return None


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_runtime_artifacts(record: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime_artifacts = record.get("runtime_artifacts")
    if isinstance(runtime_artifacts, dict):
        return runtime_artifacts
    return {}


def _build_profile_row(record: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = _extract_runtime_artifacts(record)
    return {
        "profile_id": _to_text(record.get("profile_id")),
        "release_id": _to_text(record.get("release_id")),
        "status": _to_text(record.get("status")),
        "runtime": _to_text(artifacts.get("runtime")),
        "validated": _to_optional_bool(artifacts.get("validated")),
        "runtime_available": _to_optional_bool(artifacts.get("runtime_available")),
        "probe_checked": _to_optional_bool(artifacts.get("probe_checked")),
        "probe_executed": _to_optional_bool(artifacts.get("probe_executed")),
        "scenario_contract_status": _to_text(artifacts.get("scenario_contract_status")),
        "scene_result_status": _to_text(artifacts.get("scene_result_status")),
        "interop_contract_status": _to_text(artifacts.get("interop_contract_status")),
        "interop_import_status": _to_text(artifacts.get("interop_import_status")),
        "interop_import_manifest_consistent": _to_optional_bool(artifacts.get("interop_import_manifest_consistent")),
        "interop_import_manifest_consistency_mode": _to_text(
            artifacts.get("interop_import_manifest_consistency_mode")
        ).lower(),
        "interop_import_export_consistency_mode": _to_text(
            artifacts.get("interop_import_export_consistency_mode")
        ).lower(),
        "interop_import_require_manifest_consistency_input": _to_optional_bool(
            artifacts.get("interop_import_require_manifest_consistency_input")
        ),
        "interop_import_require_export_consistency_input": _to_optional_bool(
            artifacts.get("interop_import_require_export_consistency_input")
        ),
        "scene_result_coverage_ratio": _to_optional_float(artifacts.get("scene_result_coverage_ratio")),
        "scene_result_ego_travel_distance_m": _to_optional_float(
            artifacts.get("scene_result_ego_travel_distance_m")
        ),
        "scenario_executed_step_count": _to_optional_float(artifacts.get("scenario_executed_step_count")),
        "scene_result_executed_step_count": _to_optional_float(artifacts.get("scene_result_executed_step_count")),
        "interop_executed_step_count": _to_optional_float(artifacts.get("interop_executed_step_count")),
        "interop_imported_actor_count": _to_optional_float(artifacts.get("interop_imported_actor_count")),
        "interop_import_actor_count_manifest": _to_optional_float(artifacts.get("interop_import_actor_count_manifest")),
        "interop_import_xosc_entity_count": _to_optional_float(artifacts.get("interop_import_xosc_entity_count")),
        "interop_import_xodr_road_count": _to_optional_float(artifacts.get("interop_import_xodr_road_count")),
        "interop_import_xodr_total_road_length_m": _to_optional_float(
            artifacts.get("interop_import_xodr_total_road_length_m")
        ),
    }


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter()
    for row in rows:
        status = _to_text(row.get("status"))
        if status:
            counter[status] += 1
    return {key: counter[key] for key in sorted(counter)}


def _runtime_counts(rows: list[dict[str, Any]], *, fallback_runtime: str) -> dict[str, int]:
    counter = Counter()
    for row in rows:
        runtime = _to_text(row.get("runtime")) or fallback_runtime
        if runtime:
            counter[runtime] += 1
    return {key: counter[key] for key in sorted(counter)}


def _interop_import_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter()
    for row in rows:
        interop_import_status = _to_text(row.get("interop_import_status"))
        if interop_import_status:
            counter[interop_import_status] += 1
    return {key: counter[key] for key in sorted(counter)}


def _interop_import_manifest_consistency_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter()
    for row in rows:
        consistency = _to_optional_bool(row.get("interop_import_manifest_consistent"))
        if consistency is True:
            counter["true"] += 1
        elif consistency is False:
            counter["false"] += 1
        else:
            counter["unknown"] += 1
    return {key: counter[key] for key in sorted(counter)}


def _interop_import_mode_counts(rows: list[dict[str, Any]], *, key: str) -> dict[str, int]:
    counter = Counter()
    for row in rows:
        mode = _to_text(row.get(key)).lower()
        if mode in {"require", "allow"}:
            counter[mode] += 1
    return {mode: counter[mode] for mode in sorted(counter)}


def _interop_import_require_input_counts(rows: list[dict[str, Any]], *, key: str) -> dict[str, int]:
    counter = Counter()
    for row in rows:
        value = _to_optional_bool(row.get(key))
        if value is True:
            counter["true"] += 1
        elif value is False:
            counter["false"] += 1
        else:
            counter["unknown"] += 1
    return {token: counter[token] for token in sorted(counter)}


def _build_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    raw_records = payload.get("runtime_evidence_records")
    if isinstance(raw_records, list):
        for index, raw_record in enumerate(raw_records):
            if not isinstance(raw_record, dict):
                raise ValueError(f"runtime_evidence_records[{index}] must be a JSON object")
            rows.append(_build_profile_row(raw_record))
    else:
        raise ValueError("runtime_evidence_records must be a JSON array")

    records_by_profile: dict[str, dict[str, Any]] = {}
    fallback_ids = 0
    for row in rows:
        profile_id = _to_text(row.get("profile_id"))
        if not profile_id:
            fallback_ids += 1
            profile_id = f"__index_{fallback_ids:03d}"
            row["profile_id"] = profile_id
        if profile_id in records_by_profile:
            raise ValueError(f"duplicate profile_id in runtime_evidence_records: {profile_id}")
        records_by_profile[profile_id] = row

    sim_runtime = _to_text(payload.get("sim_runtime"))
    return {
        "release_prefix": _to_text(payload.get("release_prefix")),
        "sim_runtime": sim_runtime,
        "dry_run": _to_optional_bool(payload.get("dry_run")),
        "profile_count": payload.get("profile_count"),
        "failure_count": payload.get("failure_count"),
        "runtime_evidence_count": payload.get("runtime_evidence_count"),
        "profile_count_observed": len(rows),
        "status_counts": _status_counts(rows),
        "runtime_counts": _runtime_counts(rows, fallback_runtime=sim_runtime),
        "interop_import_status_counts": _interop_import_status_counts(rows),
        "interop_import_manifest_consistency_counts": _interop_import_manifest_consistency_counts(rows),
        "interop_import_manifest_mode_counts": _interop_import_mode_counts(
            rows,
            key="interop_import_manifest_consistency_mode",
        ),
        "interop_import_export_mode_counts": _interop_import_mode_counts(
            rows,
            key="interop_import_export_consistency_mode",
        ),
        "interop_import_require_manifest_input_counts": _interop_import_require_input_counts(
            rows,
            key="interop_import_require_manifest_consistency_input",
        ),
        "interop_import_require_export_input_counts": _interop_import_require_input_counts(
            rows,
            key="interop_import_require_export_consistency_input",
        ),
        "records_by_profile": records_by_profile,
    }


def _compare_top_level(left_summary: Mapping[str, Any], right_summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    mismatches: dict[str, dict[str, Any]] = {}
    for key in TOP_LEVEL_COMPARE_KEYS:
        left_value = left_summary.get(key)
        right_value = right_summary.get(key)
        if left_value != right_value:
            mismatches[key] = {"left": left_value, "right": right_value}
    return mismatches


def _compare_counter_dicts(
    left_counts: Mapping[str, int],
    right_counts: Mapping[str, int],
) -> dict[str, dict[str, int]]:
    diffs: dict[str, dict[str, int]] = {}
    keys = sorted(set(left_counts) | set(right_counts))
    for key in keys:
        left_value = int(left_counts.get(key, 0))
        right_value = int(right_counts.get(key, 0))
        if left_value != right_value:
            diffs[key] = {
                "left": left_value,
                "right": right_value,
                "delta": right_value - left_value,
            }
    return diffs


def _compare_profile_rows(
    left_rows: Mapping[str, dict[str, Any]],
    right_rows: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    left_ids = set(left_rows)
    right_ids = set(right_rows)
    shared_ids = sorted(left_ids & right_ids)

    diffs: list[dict[str, Any]] = []
    for profile_id in shared_ids:
        left_row = left_rows[profile_id]
        right_row = right_rows[profile_id]
        field_mismatches: dict[str, dict[str, Any]] = {}
        for key in PROFILE_COMPARE_FIELD_KEYS:
            left_value = left_row.get(key)
            right_value = right_row.get(key)
            if left_value != right_value:
                field_mismatches[key] = {"left": left_value, "right": right_value}

        numeric_deltas: dict[str, dict[str, float]] = {}
        for key in PROFILE_COMPARE_NUMERIC_KEYS:
            left_value = _to_optional_float(left_row.get(key))
            right_value = _to_optional_float(right_row.get(key))
            if left_value is None and right_value is None:
                continue
            if left_value is None or right_value is None:
                numeric_deltas[key] = {
                    "left": left_value,
                    "right": right_value,
                    "delta": None,
                }
                continue
            if left_value != right_value:
                numeric_deltas[key] = {
                    "left": left_value,
                    "right": right_value,
                    "delta": right_value - left_value,
                }

        if field_mismatches or numeric_deltas:
            diffs.append(
                {
                    "profile_id": profile_id,
                    "field_mismatches": field_mismatches,
                    "numeric_deltas": numeric_deltas,
                }
            )
    return diffs


def _extract_interop_import_profile_diffs(profile_diffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    for row in profile_diffs:
        if not isinstance(row, dict):
            continue
        field_mismatches_raw = row.get("field_mismatches", {})
        numeric_deltas_raw = row.get("numeric_deltas", {})
        field_mismatches = (
            {
                str(key): value
                for key, value in field_mismatches_raw.items()
                if str(key).startswith("interop_import_")
            }
            if isinstance(field_mismatches_raw, dict)
            else {}
        )
        numeric_deltas = (
            {
                str(key): value
                for key, value in numeric_deltas_raw.items()
                if str(key).startswith("interop_import_")
            }
            if isinstance(numeric_deltas_raw, dict)
            else {}
        )
        if not field_mismatches and not numeric_deltas:
            continue
        extracted.append(
            {
                "profile_id": _to_text(row.get("profile_id")) or "profile_unknown",
                "field_mismatches": field_mismatches,
                "numeric_deltas": numeric_deltas,
            }
        )
    extracted.sort(key=lambda item: str(item.get("profile_id", "")))
    return extracted


def _build_compare_payload(
    *,
    left_label: str,
    right_label: str,
    left_path: Path,
    right_path: Path,
    left_payload: Mapping[str, Any],
    right_payload: Mapping[str, Any],
) -> dict[str, Any]:
    left_summary = _build_summary(left_payload)
    right_summary = _build_summary(right_payload)

    left_rows = left_summary.get("records_by_profile", {})
    right_rows = right_summary.get("records_by_profile", {})
    if not isinstance(left_rows, dict) or not isinstance(right_rows, dict):
        raise ValueError("invalid records_by_profile summary state")

    left_ids = set(left_rows)
    right_ids = set(right_rows)
    shared_ids = sorted(left_ids & right_ids)

    profile_diffs = _compare_profile_rows(left_rows, right_rows)
    interop_import_profile_diffs = _extract_interop_import_profile_diffs(profile_diffs)

    diff = {
        "top_level_mismatches": _compare_top_level(left_summary, right_summary),
        "status_count_diffs": _compare_counter_dicts(
            left_summary.get("status_counts", {}),
            right_summary.get("status_counts", {}),
        ),
        "runtime_count_diffs": _compare_counter_dicts(
            left_summary.get("runtime_counts", {}),
            right_summary.get("runtime_counts", {}),
        ),
        "interop_import_status_count_diffs": _compare_counter_dicts(
            left_summary.get("interop_import_status_counts", {}),
            right_summary.get("interop_import_status_counts", {}),
        ),
        "interop_import_manifest_consistency_diffs": _compare_counter_dicts(
            left_summary.get("interop_import_manifest_consistency_counts", {}),
            right_summary.get("interop_import_manifest_consistency_counts", {}),
        ),
        "interop_import_manifest_mode_count_diffs": _compare_counter_dicts(
            left_summary.get("interop_import_manifest_mode_counts", {}),
            right_summary.get("interop_import_manifest_mode_counts", {}),
        ),
        "interop_import_export_mode_count_diffs": _compare_counter_dicts(
            left_summary.get("interop_import_export_mode_counts", {}),
            right_summary.get("interop_import_export_mode_counts", {}),
        ),
        "interop_import_require_manifest_input_count_diffs": _compare_counter_dicts(
            left_summary.get("interop_import_require_manifest_input_counts", {}),
            right_summary.get("interop_import_require_manifest_input_counts", {}),
        ),
        "interop_import_require_export_input_count_diffs": _compare_counter_dicts(
            left_summary.get("interop_import_require_export_input_counts", {}),
            right_summary.get("interop_import_require_export_input_counts", {}),
        ),
        "profile_presence": {
            "left_only": sorted(left_ids - right_ids),
            "right_only": sorted(right_ids - left_ids),
            "shared_count": len(shared_ids),
        },
        "profile_diffs": profile_diffs,
        "interop_import_profile_diffs": interop_import_profile_diffs,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "left": {
            "label": left_label,
            "path": str(left_path),
            "summary": left_summary,
        },
        "right": {
            "label": right_label,
            "path": str(right_path),
            "summary": right_summary,
        },
        "diff": diff,
    }


def _render_text_report(payload: Mapping[str, Any]) -> str:
    left = payload.get("left", {}) if isinstance(payload.get("left"), dict) else {}
    right = payload.get("right", {}) if isinstance(payload.get("right"), dict) else {}
    diff = payload.get("diff", {}) if isinstance(payload.get("diff"), dict) else {}

    left_label = _to_text(left.get("label")) or "left"
    right_label = _to_text(right.get("label")) or "right"
    left_path = _to_text(left.get("path"))
    right_path = _to_text(right.get("path"))
    top_level_mismatches = diff.get("top_level_mismatches", {})
    status_count_diffs = diff.get("status_count_diffs", {})
    runtime_count_diffs = diff.get("runtime_count_diffs", {})
    interop_import_status_count_diffs = diff.get("interop_import_status_count_diffs", {})
    interop_import_manifest_consistency_diffs = diff.get("interop_import_manifest_consistency_diffs", {})
    interop_import_manifest_mode_count_diffs = diff.get("interop_import_manifest_mode_count_diffs", {})
    interop_import_export_mode_count_diffs = diff.get("interop_import_export_mode_count_diffs", {})
    interop_import_require_manifest_input_count_diffs = diff.get(
        "interop_import_require_manifest_input_count_diffs",
        {},
    )
    interop_import_require_export_input_count_diffs = diff.get(
        "interop_import_require_export_input_count_diffs",
        {},
    )
    profile_presence = diff.get("profile_presence", {})
    profile_diffs = diff.get("profile_diffs", [])
    interop_import_profile_diffs = diff.get("interop_import_profile_diffs", [])

    lines = [
        f"schema_version={SCHEMA_VERSION}",
        f"{left_label}_path={left_path}",
        f"{right_label}_path={right_path}",
        "top_level_mismatches={count}".format(count=len(top_level_mismatches) if isinstance(top_level_mismatches, dict) else 0),
        "status_count_diffs={count}".format(count=len(status_count_diffs) if isinstance(status_count_diffs, dict) else 0),
        "runtime_count_diffs={count}".format(count=len(runtime_count_diffs) if isinstance(runtime_count_diffs, dict) else 0),
        "interop_import_status_count_diffs={count}".format(
            count=len(interop_import_status_count_diffs) if isinstance(interop_import_status_count_diffs, dict) else 0
        ),
        "interop_import_manifest_consistency_diffs={count}".format(
            count=len(interop_import_manifest_consistency_diffs)
            if isinstance(interop_import_manifest_consistency_diffs, dict)
            else 0
        ),
        "interop_import_manifest_mode_count_diffs={count}".format(
            count=len(interop_import_manifest_mode_count_diffs)
            if isinstance(interop_import_manifest_mode_count_diffs, dict)
            else 0
        ),
        "interop_import_export_mode_count_diffs={count}".format(
            count=len(interop_import_export_mode_count_diffs)
            if isinstance(interop_import_export_mode_count_diffs, dict)
            else 0
        ),
        "interop_import_require_manifest_input_count_diffs={count}".format(
            count=len(interop_import_require_manifest_input_count_diffs)
            if isinstance(interop_import_require_manifest_input_count_diffs, dict)
            else 0
        ),
        "interop_import_require_export_input_count_diffs={count}".format(
            count=len(interop_import_require_export_input_count_diffs)
            if isinstance(interop_import_require_export_input_count_diffs, dict)
            else 0
        ),
        "interop_import_profile_diffs={count}".format(
            count=len(interop_import_profile_diffs) if isinstance(interop_import_profile_diffs, list) else 0
        ),
    ]

    if isinstance(profile_presence, dict):
        left_only = profile_presence.get("left_only", [])
        right_only = profile_presence.get("right_only", [])
        shared_count = profile_presence.get("shared_count", 0)
        lines.append(
            "profile_presence=shared:{shared},left_only:{left},right_only:{right}".format(
                shared=shared_count,
                left=len(left_only) if isinstance(left_only, list) else 0,
                right=len(right_only) if isinstance(right_only, list) else 0,
            )
        )
    lines.append(
        "profile_diffs={count}".format(count=len(profile_diffs) if isinstance(profile_diffs, list) else 0)
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    left_path = Path(args.left).resolve()
    right_path = Path(args.right).resolve()
    if not left_path.exists():
        raise FileNotFoundError(f"left runtime evidence artifact not found: {left_path}")
    if not right_path.exists():
        raise FileNotFoundError(f"right runtime evidence artifact not found: {right_path}")

    left_payload = load_labeled_json_object(left_path, label="left runtime evidence")
    right_payload = load_labeled_json_object(right_path, label="right runtime evidence")
    compare_payload = _build_compare_payload(
        left_label=_to_text(args.left_label) or "left",
        right_label=_to_text(args.right_label) or "right",
        left_path=left_path,
        right_path=right_path,
        left_payload=left_payload,
        right_payload=right_payload,
    )

    out_json_path = Path(args.out_json).resolve()
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json.dumps(compare_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"[ok] runtime_evidence_compare_out={out_json_path}")

    out_text_raw = _to_text(args.out_text)
    if out_text_raw:
        out_text_path = Path(out_text_raw).resolve()
        out_text_path.parent.mkdir(parents=True, exist_ok=True)
        out_text_path.write_text(_render_text_report(compare_payload), encoding="utf-8")
        print(f"[ok] runtime_evidence_compare_text_out={out_text_path}")

    diff = compare_payload.get("diff", {})
    if isinstance(diff, dict):
        top_level_mismatches = diff.get("top_level_mismatches", {})
        profile_presence = diff.get("profile_presence", {})
        profile_diffs = diff.get("profile_diffs", [])
        interop_import_profile_diffs = diff.get("interop_import_profile_diffs", [])
        left_only = profile_presence.get("left_only", []) if isinstance(profile_presence, dict) else []
        right_only = profile_presence.get("right_only", []) if isinstance(profile_presence, dict) else []
        print(
            "[ok] runtime_evidence_compare_summary "
            f"top_level_mismatches={len(top_level_mismatches) if isinstance(top_level_mismatches, dict) else 0} "
            f"profile_left_only={len(left_only) if isinstance(left_only, list) else 0} "
            f"profile_right_only={len(right_only) if isinstance(right_only, list) else 0} "
            f"profile_diffs={len(profile_diffs) if isinstance(profile_diffs, list) else 0} "
            "interop_import_profile_diffs="
            f"{len(interop_import_profile_diffs) if isinstance(interop_import_profile_diffs, list) else 0}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="compare_runtime_evidence.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
