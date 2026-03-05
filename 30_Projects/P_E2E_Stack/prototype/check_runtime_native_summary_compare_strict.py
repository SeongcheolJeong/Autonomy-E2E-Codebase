#!/usr/bin/env python3
"""Validate runtime-native summary-compare policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling

SCHEMA_VERSION = "runtime_native_summary_compare_policy_check_v0"


def _as_int_flag(value: int) -> int:
    return 1 if int(value) == 1 else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate runtime-native both summary-compare policy"
    )
    parser.add_argument(
        "--summary-json",
        required=True,
        help="Path to runtime-native both compare e2e summary JSON",
    )
    parser.add_argument(
        "--fail-on-missing",
        type=int,
        choices=(0, 1),
        default=1,
        help="Fail when compare summary status is not ok (0/1, default: 1)",
    )
    parser.add_argument(
        "--fail-on-diffs",
        type=int,
        choices=(0, 1),
        default=1,
        help="Fail when compare summary reports with_diffs>0 (0/1, default: 1)",
    )
    parser.add_argument(
        "--out-json",
        default="",
        help="Optional output JSON path for evaluated policy payload",
    )
    return parser.parse_args()


def _to_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _evaluate_summary_payload(summary_json_path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "summary_json_path": str(summary_json_path),
        "artifact_count": 0,
        "with_diffs": 0,
        "without_diffs": 0,
        "status": "missing",
        "issue": "summary_compare_file_missing",
    }
    if not summary_json_path.exists():
        return payload

    try:
        raw = json.loads(summary_json_path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}

    compare_summary: dict[str, Any] = {}
    if isinstance(raw, dict):
        candidate = raw.get("runtime_native_summary_compare_summary")
        if isinstance(candidate, dict):
            compare_summary = candidate

    artifact_count_raw = compare_summary.get("artifact_count", 0)
    with_diffs_raw = compare_summary.get("artifacts_with_diffs_count", 0)
    without_diffs_raw = compare_summary.get("artifacts_without_diffs_count", 0)

    artifact_count = _to_int_or_none(artifact_count_raw)
    with_diffs = _to_int_or_none(with_diffs_raw)
    without_diffs = _to_int_or_none(without_diffs_raw)

    if artifact_count is None:
        payload.update(
            {
                "artifact_count": -1,
                "status": "invalid",
                "issue": "summary_compare_artifact_count_not_integer",
            }
        )
        return payload
    if with_diffs is None:
        payload.update(
            {
                "artifact_count": artifact_count,
                "with_diffs": -1,
                "status": "invalid",
                "issue": "summary_compare_with_diffs_not_integer",
            }
        )
        return payload
    if without_diffs is None:
        without_diffs = 0

    payload.update(
        {
            "artifact_count": artifact_count,
            "with_diffs": with_diffs,
            "without_diffs": without_diffs,
        }
    )
    if artifact_count == 0:
        payload.update({"status": "empty", "issue": "summary_compare_artifact_count_zero"})
        return payload

    payload.update({"status": "ok", "issue": ""})
    return payload


def _apply_policy(
    *,
    eval_payload: dict[str, Any],
    fail_on_missing: int,
    fail_on_diffs: int,
) -> dict[str, Any]:
    status = str(eval_payload.get("status", "invalid")).strip() or "invalid"
    issue = str(eval_payload.get("issue", "")).strip()
    with_diffs = int(eval_payload.get("with_diffs", 0) or 0)
    fail_on_missing_int = _as_int_flag(fail_on_missing)
    fail_on_diffs_int = _as_int_flag(fail_on_diffs)

    enforcement_failed = 0
    policy_issue = issue
    if fail_on_missing_int == 1 and status != "ok":
        enforcement_failed = 1
    if fail_on_diffs_int == 1 and status == "ok" and with_diffs > 0:
        enforcement_failed = 1
        policy_issue = "summary_compare_with_diffs_above_zero"
    if enforcement_failed == 1 and not policy_issue:
        policy_issue = "summary_compare_policy_failed"

    return {
        **eval_payload,
        "fail_on_missing": fail_on_missing_int,
        "fail_on_diffs": fail_on_diffs_int,
        "enforcement_failed": enforcement_failed,
        "issue": policy_issue if enforcement_failed == 1 or policy_issue else "",
    }


def _write_optional_json(out_json_raw: str, payload: dict[str, Any]) -> None:
    out_json = str(out_json_raw).strip()
    if not out_json:
        return
    out_path = Path(out_json).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    summary_json_path = Path(args.summary_json).resolve()
    eval_payload = _evaluate_summary_payload(summary_json_path)
    policy_payload = _apply_policy(
        eval_payload=eval_payload,
        fail_on_missing=args.fail_on_missing,
        fail_on_diffs=args.fail_on_diffs,
    )
    _write_optional_json(args.out_json, policy_payload)

    status = str(policy_payload.get("status", "invalid")).strip() or "invalid"
    issue = str(policy_payload.get("issue", "")).strip()
    artifact_count = int(policy_payload.get("artifact_count", 0) or 0)
    with_diffs = int(policy_payload.get("with_diffs", 0) or 0)
    fail_on_missing = int(policy_payload.get("fail_on_missing", 0) or 0)
    fail_on_diffs = int(policy_payload.get("fail_on_diffs", 0) or 0)
    enforcement_failed = int(policy_payload.get("enforcement_failed", 0) or 0)

    if enforcement_failed == 0:
        if fail_on_missing == 1 and fail_on_diffs == 1:
            print(
                f"[ok] strict_runtime_native_summary_compare=artifact_count:{artifact_count},with_diffs:{with_diffs}"
            )
        else:
            print(
                "[ok] runtime_native_summary_compare_policy_check "
                f"status={status} issue={issue or 'n/a'} "
                f"artifact_count={artifact_count} with_diffs={with_diffs}"
            )
        return 0

    if fail_on_missing == 1 and fail_on_diffs == 1:
        if status == "missing":
            print(
                f"[error] strict runtime-native summary compare policy: summary json missing: {summary_json_path}",
                file=sys.stderr,
            )
        elif issue == "summary_compare_artifact_count_not_integer":
            print(
                f"[error] strict runtime-native summary compare policy: invalid artifact_count={artifact_count}",
                file=sys.stderr,
            )
        elif issue == "summary_compare_with_diffs_not_integer":
            print(
                f"[error] strict runtime-native summary compare policy: invalid with_diffs={with_diffs}",
                file=sys.stderr,
            )
        elif issue == "summary_compare_artifact_count_zero":
            print(
                f"[error] strict runtime-native summary compare policy: artifact_count must be > 0 (got {artifact_count})",
                file=sys.stderr,
            )
        elif issue == "summary_compare_with_diffs_above_zero":
            print(
                f"[error] strict runtime-native summary compare policy: with_diffs must be 0 (got {with_diffs})",
                file=sys.stderr,
            )
        else:
            print(
                "[error] strict runtime-native summary compare policy: "
                f"status={status} issue={issue or 'n/a'}",
                file=sys.stderr,
            )
        return 1

    if issue == "summary_compare_with_diffs_above_zero":
        print(
            f"[error] runtime-native summary-compare diff enforcement failed: with_diffs={with_diffs}",
            file=sys.stderr,
        )
    else:
        print(
            f"[error] runtime-native summary-compare validation failed: status={status} issue={issue or 'n/a'}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="check_runtime_native_summary_compare_strict.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
