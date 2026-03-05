#!/usr/bin/env python3
"""Compare runtime-native release summary artifacts across two runtimes."""

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

SCHEMA_VERSION = "runtime_native_summary_compare_v0"

COMPARE_FIELD_KEYS: tuple[str, ...] = (
    "final_result",
    "gate_result",
    "requirement_result",
    "run_count",
    "success_count",
    "fail_count",
    "timeout_count",
    "collision_count",
    "collision_rate",
    "min_ttc_p5_sec",
    "hold_reason_codes",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare runtime-native release summary JSON artifacts for two runtimes"
    )
    parser.add_argument("--report-dir", required=True, help="Directory containing *_<version>.summary.json artifacts")
    parser.add_argument("--left-release-prefix", required=True, help="Left release prefix (for example: REL_..._awsim)")
    parser.add_argument("--right-release-prefix", required=True, help="Right release prefix (for example: REL_..._carla)")
    parser.add_argument(
        "--versions",
        required=True,
        help="Space/comma separated SDS versions (for example: 'sds_v0.1.0 sds_v0.2.0')",
    )
    parser.add_argument("--left-label", default="left", help="Label for left runtime")
    parser.add_argument("--right-label", default="right", help="Label for right runtime")
    parser.add_argument("--out-json", required=True, help="Output JSON report path")
    parser.add_argument("--out-text", default="", help="Optional output text report path")
    return parser.parse_args()


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    token = _to_text(value)
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _normalize_hold_reason_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        token = _to_text(item)
        if token:
            normalized.append(token)
    return sorted(set(normalized))


def _parse_versions(raw: str) -> list[str]:
    tokens = [token.strip() for token in raw.replace(",", " ").split() if token.strip()]
    if not tokens:
        raise ValueError("versions must include at least one SDS version")
    return tokens


def _load_summary_row(path: Path) -> dict[str, Any]:
    payload = load_labeled_json_object(path, label="runtime-native summary")
    return {
        "release_id": _to_text(payload.get("release_id")),
        "sds_version": _to_text(payload.get("sds_version")),
        "final_result": _to_text(payload.get("final_result")),
        "gate_result": _to_text(payload.get("gate_result")),
        "requirement_result": _to_text(payload.get("requirement_result")),
        "run_count": _to_int(payload.get("run_count")),
        "success_count": _to_int(payload.get("success_count")),
        "fail_count": _to_int(payload.get("fail_count")),
        "timeout_count": _to_int(payload.get("timeout_count")),
        "collision_count": _to_int(payload.get("collision_count")),
        "collision_rate": _to_optional_float(payload.get("collision_rate")),
        "min_ttc_p5_sec": _to_optional_float(payload.get("min_ttc_p5_sec")),
        "hold_reason_codes": _normalize_hold_reason_codes(payload.get("hold_reason_codes")),
    }


def _compare_rows(
    left_row: Mapping[str, Any],
    right_row: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    mismatches: dict[str, dict[str, Any]] = {}
    for key in COMPARE_FIELD_KEYS:
        left_value = left_row.get(key)
        right_value = right_row.get(key)
        if left_value != right_value:
            mismatches[key] = {"left": left_value, "right": right_value}
    return mismatches


def _build_compare_payload(
    *,
    report_dir: Path,
    left_release_prefix: str,
    right_release_prefix: str,
    versions: list[str],
    left_label: str,
    right_label: str,
) -> dict[str, Any]:
    missing_paths: list[str] = []
    comparisons: list[dict[str, Any]] = []
    field_diff_counter: Counter[str] = Counter()
    versions_with_diffs: list[str] = []

    for version in versions:
        left_path = (report_dir / f"{left_release_prefix}_{version}.summary.json").resolve()
        right_path = (report_dir / f"{right_release_prefix}_{version}.summary.json").resolve()
        missing_for_version: list[str] = []
        if not left_path.exists():
            missing_for_version.append(str(left_path))
        if not right_path.exists():
            missing_for_version.append(str(right_path))
        if missing_for_version:
            missing_paths.extend(missing_for_version)
            continue

        left_row = _load_summary_row(left_path)
        right_row = _load_summary_row(right_path)
        field_mismatches = _compare_rows(left_row, right_row)
        has_diff = bool(field_mismatches)
        if has_diff:
            versions_with_diffs.append(version)
            for field_key in field_mismatches:
                field_diff_counter[field_key] += 1
        comparisons.append(
            {
                "version": version,
                "left_summary_path": str(left_path),
                "right_summary_path": str(right_path),
                "left": left_row,
                "right": right_row,
                "field_mismatches": field_mismatches,
                "has_diff": has_diff,
            }
        )

    if missing_paths:
        preview = "\n".join(missing_paths[:8])
        if len(missing_paths) > 8:
            preview = preview + "\n..."
        raise FileNotFoundError(
            "runtime-native summary artifact not found:\n" + preview
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "left_label": left_label,
        "right_label": right_label,
        "report_dir": str(report_dir),
        "left_release_prefix": left_release_prefix,
        "right_release_prefix": right_release_prefix,
        "versions": versions,
        "summary": {
            "version_count": len(versions),
            "versions_with_diffs_count": len(versions_with_diffs),
            "versions_with_diffs": versions_with_diffs,
            "field_diff_counts": {key: field_diff_counter[key] for key in sorted(field_diff_counter)},
            "comparison_count": len(comparisons),
        },
        "comparisons": comparisons,
    }


def _render_text_report(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        f"schema_version={_to_text(payload.get('schema_version'))}",
        f"left_label={_to_text(payload.get('left_label'))}",
        f"right_label={_to_text(payload.get('right_label'))}",
        f"left_release_prefix={_to_text(payload.get('left_release_prefix'))}",
        f"right_release_prefix={_to_text(payload.get('right_release_prefix'))}",
    ]
    if isinstance(summary, dict):
        lines.append(f"version_count={_to_int(summary.get('version_count'))}")
        lines.append(f"comparison_count={_to_int(summary.get('comparison_count'))}")
        lines.append(f"versions_with_diffs_count={_to_int(summary.get('versions_with_diffs_count'))}")
        versions_with_diffs = summary.get("versions_with_diffs", [])
        if isinstance(versions_with_diffs, list) and versions_with_diffs:
            lines.append(
                "versions_with_diffs="
                + ",".join(_to_text(token) for token in versions_with_diffs if _to_text(token))
            )
        field_diff_counts = summary.get("field_diff_counts", {})
        if isinstance(field_diff_counts, dict) and field_diff_counts:
            parts: list[str] = []
            for key in sorted(field_diff_counts):
                parts.append(f"{key}:{_to_int(field_diff_counts.get(key))}")
            lines.append("field_diff_counts=" + ",".join(parts))
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    report_dir = Path(args.report_dir).resolve()
    versions = _parse_versions(_to_text(args.versions))
    compare_payload = _build_compare_payload(
        report_dir=report_dir,
        left_release_prefix=_to_text(args.left_release_prefix),
        right_release_prefix=_to_text(args.right_release_prefix),
        versions=versions,
        left_label=_to_text(args.left_label) or "left",
        right_label=_to_text(args.right_label) or "right",
    )

    out_json_path = Path(args.out_json).resolve()
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json.dumps(compare_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"[ok] runtime_native_summary_compare_out={out_json_path}")

    out_text_raw = _to_text(args.out_text)
    if out_text_raw:
        out_text_path = Path(out_text_raw).resolve()
        out_text_path.parent.mkdir(parents=True, exist_ok=True)
        out_text_path.write_text(_render_text_report(compare_payload), encoding="utf-8")
        print(f"[ok] runtime_native_summary_compare_text_out={out_text_path}")

    summary = compare_payload.get("summary", {})
    if isinstance(summary, dict):
        print(
            "[ok] runtime_native_summary_compare_summary "
            f"version_count={_to_int(summary.get('version_count'))} "
            f"comparison_count={_to_int(summary.get('comparison_count'))} "
            f"versions_with_diffs_count={_to_int(summary.get('versions_with_diffs_count'))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="compare_runtime_native_summaries.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
