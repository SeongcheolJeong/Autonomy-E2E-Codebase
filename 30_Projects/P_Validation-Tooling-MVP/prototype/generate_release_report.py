#!/usr/bin/env python3
"""Generate a minimal validation release report from scenario run lake."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary

ERROR_SOURCE = "generate_release_report.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate release validation markdown report")
    parser.add_argument("--db", required=True, help="SQLite DB path")
    parser.add_argument("--release-id", required=True, help="Release identifier")
    parser.add_argument("--sds-version", required=True, help="SDS version to summarize")
    parser.add_argument("--out", required=True, help="Output markdown path")
    parser.add_argument(
        "--gate-profile",
        default="",
        help="Optional JSON file path containing capability gate rules",
    )
    parser.add_argument(
        "--requirement-map",
        default="",
        help="Optional JSON file path containing requirement-to-metric checks",
    )
    parser.add_argument(
        "--summary-out",
        default="",
        help="Optional JSON summary output path",
    )
    return parser.parse_args()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values_sorted = sorted(values)
    index = int(round((p / 100.0) * (len(values_sorted) - 1)))
    return values_sorted[index]


def fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def load_json_object(path_text: str, label: str) -> dict[str, Any]:
    if not path_text:
        return {}
    json_path = Path(path_text).resolve()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def load_gate_profile(path_text: str) -> dict[str, Any]:
    return load_json_object(path_text, "gate profile")


def load_requirement_map(path_text: str) -> dict[str, Any]:
    return load_json_object(path_text, "requirement map")


def evaluate_requirement_map(
    *,
    metric_snapshot: dict[str, float | None],
    requirement_map: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    requirements = requirement_map.get("requirements", [])
    if not isinstance(requirements, list):
        raise ValueError("requirement map 'requirements' must be a list")

    records: list[dict[str, str]] = []
    has_hold = False

    for item in requirements:
        if not isinstance(item, dict):
            continue

        requirement_id = str(item.get("requirement_id", "")).strip()
        metric_key = str(item.get("metric_key", "")).strip()
        operator = str(item.get("operator", "")).strip()
        description = str(item.get("description", "")).strip()
        allow_missing = bool(item.get("allow_missing", False))

        if not requirement_id or not metric_key or not operator:
            raise ValueError("each requirement must include requirement_id, metric_key, operator")
        if "threshold" not in item:
            raise ValueError(f"requirement {requirement_id} missing threshold")
        threshold = float(item["threshold"])

        observed = metric_snapshot.get(metric_key)
        if observed is None:
            result = "PASS" if allow_missing else "HOLD"
            reason = "metric is n/a"
        else:
            compare_ops = {
                "<": observed < threshold,
                "<=": observed <= threshold,
                ">": observed > threshold,
                ">=": observed >= threshold,
                "==": observed == threshold,
                "!=": observed != threshold,
            }
            if operator not in compare_ops:
                raise ValueError(
                    f"unsupported operator for requirement {requirement_id}: {operator}"
                )
            passed = compare_ops[operator]
            result = "PASS" if passed else "HOLD"
            reason = f"{observed:.4f} {operator} {threshold:.4f}"

        if result != "PASS":
            has_hold = True

        records.append(
            {
                "requirement_id": requirement_id,
                "description": description,
                "metric_key": metric_key,
                "observed": "n/a" if observed is None else f"{observed:.4f}",
                "rule": f"{operator} {threshold:.4f}",
                "result": result,
                "reason": reason,
            }
        )

    return ("HOLD" if has_hold else "PASS"), records


def evaluate_gate(
    *,
    run_count: int,
    fail_count: int,
    timeout_count: int,
    collision_count: int,
    collision_rate: float | None,
    min_ttc_p5: float | None,
    gate_profile: dict[str, Any],
) -> tuple[str, list[str]]:
    if not gate_profile:
        if run_count == 0:
            return "HOLD", ["no runs"]
        if timeout_count > 0:
            return "HOLD", [f"timeout observed ({timeout_count})"]
        if collision_count > 0:
            return "HOLD", ["collision observed"]
        return "PASS", ["no collision in current sample"]

    rules = gate_profile.get("rules", {})
    if not isinstance(rules, dict):
        raise ValueError("gate profile 'rules' must be an object")

    failures: list[str] = []

    min_run_count = rules.get("min_run_count")
    if min_run_count is not None and run_count < int(min_run_count):
        failures.append(f"run_count {run_count} < min_run_count {int(min_run_count)}")

    max_fail_count = rules.get("max_fail_count")
    if max_fail_count is not None and fail_count > int(max_fail_count):
        failures.append(f"fail_count {fail_count} > max_fail_count {int(max_fail_count)}")

    max_timeout_count = rules.get("max_timeout_count", 0)
    if timeout_count > int(max_timeout_count):
        failures.append(f"timeout_count {timeout_count} > max_timeout_count {int(max_timeout_count)}")

    max_collision_count = rules.get("max_collision_count")
    if max_collision_count is not None and collision_count > int(max_collision_count):
        failures.append(
            f"collision_count {collision_count} > max_collision_count {int(max_collision_count)}"
        )

    max_collision_rate = rules.get("max_collision_rate")
    if max_collision_rate is not None and collision_rate is not None:
        if collision_rate > float(max_collision_rate):
            failures.append(
                f"collision_rate {collision_rate:.4f} > max_collision_rate {float(max_collision_rate):.4f}"
            )

    min_ttc_threshold = rules.get("min_ttc_p5_sec")
    min_ttc_required = bool(rules.get("min_ttc_p5_required", False))
    if min_ttc_threshold is not None:
        if min_ttc_p5 is None:
            if min_ttc_required:
                failures.append("min_ttc_p5_sec is n/a but required")
        elif min_ttc_p5 < float(min_ttc_threshold):
            failures.append(
                f"min_ttc_p5_sec {min_ttc_p5:.4f} < min_ttc_p5_sec {float(min_ttc_threshold):.4f}"
            )

    if failures:
        return "HOLD", failures
    return "PASS", ["all gate rules satisfied"]


def gate_reason_code(reason: str) -> str:
    if "run_count" in reason and "min_run_count" in reason:
        return "gate.min_run_count"
    if "fail_count" in reason and "max_fail_count" in reason:
        return "gate.max_fail_count"
    if "timeout_count" in reason and "max_timeout_count" in reason:
        return "gate.max_timeout_count"
    if "collision_count" in reason and "max_collision_count" in reason:
        return "gate.max_collision_count"
    if "collision_rate" in reason and "max_collision_rate" in reason:
        return "gate.max_collision_rate"
    if "min_ttc_p5_sec" in reason:
        return "gate.min_ttc_p5_sec"
    return "gate.unknown"


def main() -> int:
    args = parse_args()

    db_path = Path(args.db).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary_out_path = Path(args.summary_out).resolve() if args.summary_out else None
    if summary_out_path is not None:
        summary_out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        gate_profile = load_gate_profile(args.gate_profile)
        requirement_map = load_requirement_map(args.requirement_map)
        run_rows = conn.execute(
            """
            SELECT run_id, scenario_id, status, termination_reason, min_ttc_sec, collision, summary_path
            FROM scenario_run
            WHERE sds_version = ?
            ORDER BY run_timestamp DESC
            """,
            (args.sds_version,),
        ).fetchall()

        run_count = len(run_rows)
        success_count = sum(1 for row in run_rows if row[2] == "success")
        fail_count = sum(1 for row in run_rows if row[2] == "failed")
        timeout_count = sum(1 for row in run_rows if row[2] == "timeout")
        collision_count = sum(1 for row in run_rows if row[5] == 1)

        collision_rate = (collision_count / run_count) if run_count > 0 else None

        ttc_values = [float(row[4]) for row in run_rows if row[4] is not None]
        min_ttc_p5 = percentile(ttc_values, 5.0)

        non_success_rows = [row for row in run_rows if row[2] != "success"]
        gate_result, gate_reasons = evaluate_gate(
            run_count=run_count,
            fail_count=fail_count,
            timeout_count=timeout_count,
            collision_count=collision_count,
            collision_rate=collision_rate,
            min_ttc_p5=min_ttc_p5,
            gate_profile=gate_profile,
        )

        requirement_snapshot: dict[str, float | None] = {
            "run_count": float(run_count),
            "success_count": float(success_count),
            "fail_count": float(fail_count),
            "timeout_count": float(timeout_count),
            "collision_count": float(collision_count),
            "collision_rate": collision_rate,
            "min_ttc_p5_sec": min_ttc_p5,
        }
        requirement_result, requirement_records = evaluate_requirement_map(
            metric_snapshot=requirement_snapshot,
            requirement_map=requirement_map,
        )
        if not requirement_map:
            requirement_result = "N/A"

        final_result = "HOLD" if gate_result == "HOLD" or requirement_result == "HOLD" else "PASS"
        requirement_hold_records = [record for record in requirement_records if record["result"] == "HOLD"]
        hold_reasons: list[str] = []
        if gate_result == "HOLD":
            hold_reasons.extend([f"gate: {reason}" for reason in gate_reasons])
        if requirement_result == "HOLD":
            hold_reasons.extend(
                [
                    f"requirement: {record['requirement_id']} ({record['metric_key']}) "
                    f"{record['reason']}"
                    for record in requirement_hold_records
                ]
            )
        hold_reason_codes: list[str] = []
        if gate_result == "HOLD":
            hold_reason_codes.extend([gate_reason_code(reason) for reason in gate_reasons])
        if requirement_result == "HOLD":
            hold_reason_codes.extend(
                [f"requirement.{record['requirement_id']}" for record in requirement_hold_records]
            )

        now = datetime.now(timezone.utc).isoformat()

        lines: list[str] = []
        lines.append(f"# Validation Release Report - {args.release_id}")
        lines.append("")
        lines.append(f"- Generated at: {now}")
        lines.append(f"- SDS version: `{args.sds_version}`")
        lines.append(f"- Data source: `{db_path}`")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- run_count: {run_count}")
        lines.append(f"- success_count: {success_count}")
        lines.append(f"- fail_count: {fail_count}")
        lines.append(f"- timeout_count: {timeout_count}")
        lines.append(f"- collision_count: {collision_count}")
        lines.append(f"- collision_rate: {fmt(collision_rate)}")
        lines.append(f"- min_ttc_p5_sec: {fmt(min_ttc_p5)}")
        lines.append("")
        lines.append("## Non-success Runs (failed + timeout)")
        lines.append("")

        if not non_success_rows:
            lines.append("- none")
        else:
            for row in non_success_rows:
                run_id, scenario_id, status, termination_reason, min_ttc_sec, collision, summary_path = row
                lines.append(
                    "- "
                    f"run_id={run_id}, scenario_id={scenario_id}, status={status}, "
                    f"termination={termination_reason}, min_ttc_sec={min_ttc_sec}, "
                    f"collision={collision}, summary={summary_path}"
                )

        lines.append("")
        lines.append("## Requirement Traceability (v0)")
        lines.append("")
        if requirement_map:
            lines.append(f"- requirement_map: `{requirement_map.get('profile_id', 'custom')}`")
            lines.append(f"- requirement_trace_result: {requirement_result}")
            lines.append("")
            lines.append("| requirement_id | metric_key | observed | rule | result |")
            lines.append("| --- | --- | ---: | --- | --- |")
            for record in requirement_records:
                lines.append(
                    f"| {record['requirement_id']} | {record['metric_key']} | "
                    f"{record['observed']} | {record['rule']} | {record['result']} |"
                )
            lines.append("")
            lines.append("### Requirement Notes")
            lines.append("")
            for record in requirement_records:
                note = record["description"] if record["description"] else "n/a"
                lines.append(
                    f"- {record['requirement_id']}: {note} "
                    f"(metric={record['metric_key']}, observed={record['observed']}, rule={record['rule']})"
                )
        else:
            lines.append("- requirement map not provided")

        lines.append("")
        lines.append("## Gate Recommendation (v0 heuristic)")
        lines.append("")
        if gate_profile:
            lines.append(f"- gate_profile: `{gate_profile.get('profile_id', 'custom')}`")
            lines.append(f"- capability_level: `{gate_profile.get('capability_level', 'unknown')}`")
            lines.append("")
            lines.append("### Gate Rule Checks")
            lines.append("")
            for reason in gate_reasons:
                lines.append(f"- {reason}")
            lines.append("")
            lines.append(f"- RESULT: {gate_result}")
        else:
            for reason in gate_reasons:
                lines.append(f"- {reason}")
            lines.append("")
            lines.append(f"- RESULT: {gate_result}")

        lines.append("")
        lines.append("## Final Decision (v0)")
        lines.append("")
        lines.append(f"- gate_result: {gate_result}")
        lines.append(f"- requirement_trace_result: {requirement_result}")
        lines.append(f"- FINAL_RESULT: {final_result}")
        if hold_reasons:
            lines.append("")
            lines.append("### HOLD Reasons")
            lines.append("")
            for reason in hold_reasons:
                lines.append(f"- {reason}")

        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        summary_payload = {
            "generated_at": now,
            "release_id": args.release_id,
            "sds_version": args.sds_version,
            "db_path": str(db_path),
            "report_path": str(out_path),
            "gate_profile": gate_profile.get("profile_id", "") if gate_profile else "",
            "requirement_map": requirement_map.get("profile_id", "") if requirement_map else "",
            "run_count": run_count,
            "success_count": success_count,
            "fail_count": fail_count,
            "timeout_count": timeout_count,
            "collision_count": collision_count,
            "collision_rate": collision_rate,
            "min_ttc_p5_sec": min_ttc_p5,
            "gate_result": gate_result,
            "requirement_result": requirement_result,
            "final_result": final_result,
            "gate_reasons": gate_reasons,
            "requirement_hold_records": requirement_hold_records,
            "hold_reasons": hold_reasons,
            "hold_reason_codes": hold_reason_codes,
        }
        if summary_out_path is not None:
            summary_out_path.write_text(
                json.dumps(summary_payload, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )

        print(f"[ok] report={out_path}")
        if summary_out_path is not None:
            print(f"[ok] summary={summary_out_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message="interrupted")
        print("[error] generate_release_report.py: interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # pragma: no cover - exercised via script tests
        message = str(exc).strip() or exc.__class__.__name__
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        print(f"[error] generate_release_report.py: {message}", file=sys.stderr)
        raise SystemExit(1)
