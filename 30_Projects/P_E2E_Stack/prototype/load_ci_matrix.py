#!/usr/bin/env python3
"""Load CI matrix profiles JSON and emit GitHub Actions matrix JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import as_required_nonempty_str_with_path, load_json_object

PROFILE_OPTIONAL_OVERRIDE_FIELDS = (
    "sim_runtime",
    "sim_runtime_scene",
    "sim_runtime_sensor_rig",
    "sim_runtime_mode",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load CI matrix from profiles JSON file")
    parser.add_argument("--profiles-file", required=True, help="Path to profile JSON file")
    parser.add_argument("--profile-id", action="append", default=[], help="Optional profile ID filter")
    parser.add_argument(
        "--output",
        choices=["matrix", "first-profile", "field", "profile-ids"],
        default="matrix",
        help="Output mode: full matrix JSON, first selected profile JSON, selected field values, or profile IDs CSV",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        help="Field name to emit when --output field is selected (repeatable)",
    )
    return parser.parse_args()


def _as_profile_field(profile_payload: dict[str, object], *, index: int, key: str) -> str:
    return as_required_nonempty_str_with_path(
        profile_payload.get(key),
        field_path=f"profiles[{index}].{key}",
    )


def main() -> int:
    args = parse_args()
    profile_file = Path(args.profiles_file).resolve()
    payload = load_json_object(profile_file, subject="profiles file")

    profile_items = payload.get("profiles")
    if not isinstance(profile_items, list):
        raise ValueError("profiles file must include a profiles list")

    filter_ids = {str(item).strip() for item in args.profile_id if str(item).strip()}
    matrix_items: list[dict[str, str]] = []
    for idx, raw in enumerate(profile_items):
        if not isinstance(raw, dict):
            raise ValueError(f"invalid profiles[{idx}]: expected object")
        item = {
            "profile_id": _as_profile_field(raw, index=idx, key="profile_id"),
            "default_batch_spec": _as_profile_field(raw, index=idx, key="default_batch_spec"),
            "default_sds_versions": _as_profile_field(raw, index=idx, key="default_sds_versions"),
        }
        for key in PROFILE_OPTIONAL_OVERRIDE_FIELDS:
            if key not in raw:
                continue
            item[key] = _as_profile_field(raw, index=idx, key=key)
        if filter_ids and item["profile_id"] not in filter_ids:
            continue
        matrix_items.append(item)

    if not matrix_items:
        raise ValueError("no CI matrix profiles selected")

    if args.output == "first-profile":
        print(json.dumps(matrix_items[0], ensure_ascii=True, separators=(",", ":")))
        return 0

    if args.output == "field":
        if not args.field:
            raise ValueError("at least one --field is required when --output field is used")
        first = matrix_items[0]
        values: list[str] = []
        for field in args.field:
            key = str(field).strip()
            if not key:
                continue
            values.append(
                as_required_nonempty_str_with_path(
                    first.get(key),
                    field_path=f"profiles[0].{key}",
                )
            )
        if not values:
            raise ValueError("no valid --field values requested")
        print("\n".join(values))
        return 0

    if args.output == "profile-ids":
        print(",".join(item["profile_id"] for item in matrix_items))
        return 0

    matrix_payload = {"include": matrix_items}
    print(json.dumps(matrix_payload, ensure_ascii=True, separators=(",", ":")))
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="load_ci_matrix.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
