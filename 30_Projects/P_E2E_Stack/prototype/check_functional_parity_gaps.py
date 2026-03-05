#!/usr/bin/env python3
"""Build periodic functional parity gap report from matrix/plan/checklist docs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_input_parsing import parse_bool
from ci_phases import PHASE_RESOLVE_INPUTS
from ci_reporting import emit_ci_error
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import as_nonempty_text

SOURCE_NAME = "check_functional_parity_gaps.py"
FUNCTIONAL_PARITY_GAP_REPORT_SCHEMA_VERSION_V0 = "functional_parity_gap_report_v0"
MATRIX_CONTRACT_ALLOWED = {"CONTRACT_NATIVE", "CONTRACT_PARTIAL", "CONTRACT_MISSING"}
MATRIX_RUNTIME_ALLOWED = {"RUNTIME_NATIVE", "RUNTIME_PARTIAL", "RUNTIME_MISSING"}
CHECKLIST_ALLOWED = {"TO_AUDIT", "PARTIAL", "NATIVE"}
MILESTONE_DONE_STATUS = "DONE"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Applied Intuition functional parity gaps from matrix + plan + checklists"
    )
    parser.add_argument(
        "--matrix",
        default="STACK_MODULE_PARITY_MATRIX.md",
        help="STACK_MODULE_PARITY_MATRIX.md path",
    )
    parser.add_argument(
        "--master-plan",
        default="STACK_MASTER_PLAN.md",
        help="STACK_MASTER_PLAN.md path",
    )
    parser.add_argument(
        "--checklist",
        action="append",
        default=[],
        help=(
            "Phase checklist markdown path (repeatable). "
            "Default: PHASE1_MODULE_PARITY_CHECKLIST.md .. PHASE4_MODULE_PARITY_CHECKLIST.md"
        ),
    )
    parser.add_argument(
        "--out-json",
        default="reports/functional_parity_gap_report_v0.json",
        help="Output JSON report path",
    )
    parser.add_argument(
        "--out-markdown",
        default="reports/functional_parity_gap_report_v0.md",
        help="Output Markdown report path",
    )
    parser.add_argument(
        "--fail-on-open-gaps",
        default="0",
        help="Fail (exit 3) when open gaps exist (true/false compatible)",
    )
    return parser.parse_args()


def _strip_optional_backticks(value: str) -> str:
    cleaned = str(value).strip()
    if len(cleaned) >= 2 and cleaned.startswith("`") and cleaned.endswith("`"):
        return cleaned[1:-1].strip()
    return cleaned


def _is_table_separator_line(line: str) -> bool:
    reduced = (
        line.strip()
        .strip("|")
        .replace("|", "")
        .replace("-", "")
        .replace(":", "")
        .replace(" ", "")
    )
    return reduced == ""


def _iter_markdown_table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        if _is_table_separator_line(line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(cells)
    return rows


def parse_matrix_rows(matrix_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for cells in _iter_markdown_table_rows(matrix_text):
        if len(cells) < 8:
            continue
        module = _strip_optional_backticks(cells[0])
        if not module or module.lower() == "module":
            continue
        contract_status = as_nonempty_text(cells[6], field=f"contract status for {module}")
        runtime_status = as_nonempty_text(cells[7], field=f"runtime status for {module}")
        if contract_status not in MATRIX_CONTRACT_ALLOWED:
            raise ValueError(
                f"matrix contract status for {module} must be one of {sorted(MATRIX_CONTRACT_ALLOWED)}: {contract_status}"
            )
        if runtime_status not in MATRIX_RUNTIME_ALLOWED:
            raise ValueError(
                f"matrix runtime status for {module} must be one of {sorted(MATRIX_RUNTIME_ALLOWED)}: {runtime_status}"
            )
        rows.append(
            {
                "module": module,
                "parity_phase": _strip_optional_backticks(cells[4]) if len(cells) > 4 else "",
                "phase_gate": _strip_optional_backticks(cells[5]) if len(cells) > 5 else "",
                "contract_status": contract_status,
                "runtime_status": runtime_status,
            }
        )
    if not rows:
        raise ValueError("matrix table rows not found")
    return rows


def _extract_section(markdown_text: str, *, heading: str) -> str:
    marker = f"## {heading}"
    start = markdown_text.find(marker)
    if start < 0:
        raise ValueError(f"section not found: {heading}")
    remainder = markdown_text[start + len(marker) :]
    match = re.search(r"\n## ", remainder)
    if match is None:
        return remainder
    return remainder[: match.start()]


def parse_master_plan_milestones(master_plan_text: str) -> list[dict[str, str]]:
    section = _extract_section(master_plan_text, heading="Milestones")
    rows: list[dict[str, str]] = []
    for cells in _iter_markdown_table_rows(section):
        if len(cells) < 4:
            continue
        milestone_id = cells[0].strip()
        if not milestone_id or milestone_id.lower() == "id":
            continue
        rows.append(
            {
                "id": milestone_id,
                "milestone": cells[1].strip() if len(cells) > 1 else "",
                "priority": cells[2].strip() if len(cells) > 2 else "",
                "status": as_nonempty_text(cells[3], field=f"milestone status for {milestone_id}"),
            }
        )
    if not rows:
        raise ValueError("master-plan milestone rows not found")
    return rows


def _iter_h2_sections(markdown_text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown_text, flags=re.MULTILINE))
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        sections.append((title, markdown_text[body_start:body_end]))
    return sections


def parse_checklist_rows(checklist_text: str, *, checklist_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for section_title, section_body in _iter_h2_sections(checklist_text):
        module = _strip_optional_backticks(section_title)
        if not module:
            continue
        for cells in _iter_markdown_table_rows(section_body):
            if len(cells) < 3:
                continue
            feature = cells[0].strip()
            if not feature or feature.lower() == "feature":
                continue
            status = as_nonempty_text(cells[2], field=f"checklist status for {module}/{feature}")
            if status not in CHECKLIST_ALLOWED:
                raise ValueError(
                    f"checklist status for {module}/{feature} must be one of {sorted(CHECKLIST_ALLOWED)}: {status}"
                )
            rows.append(
                {
                    "module": module,
                    "feature": feature,
                    "status": status,
                    "verification_command": cells[3].strip() if len(cells) > 3 else "",
                    "checklist_path": str(checklist_path),
                }
            )
    if not rows:
        raise ValueError(f"checklist rows not found: {checklist_path}")
    return rows


def build_consistency_issues(
    matrix_rows: list[dict[str, str]],
    checklist_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    checklist_by_module: dict[str, list[str]] = {}
    checklist_modules: set[str] = set()
    for row in checklist_rows:
        module = row["module"]
        checklist_modules.add(module)
        checklist_by_module.setdefault(module, []).append(row["status"])

    issues: list[dict[str, str]] = []
    matrix_modules = {row["module"] for row in matrix_rows}
    for matrix_row in matrix_rows:
        module = matrix_row["module"]
        statuses = checklist_by_module.get(module, [])
        if not statuses:
            issues.append(
                {
                    "type": "checklist_module_missing",
                    "module": module,
                    "detail": "module exists in matrix but no checklist feature rows were found",
                }
            )
            continue
        checklist_all_native = all(status == "NATIVE" for status in statuses)
        runtime_native = matrix_row["runtime_status"] == "RUNTIME_NATIVE"
        if runtime_native and not checklist_all_native:
            issues.append(
                {
                    "type": "runtime_native_but_checklist_not_all_native",
                    "module": module,
                    "detail": "matrix runtime status is RUNTIME_NATIVE but checklist has PARTIAL/TO_AUDIT rows",
                }
            )
        if (not runtime_native) and checklist_all_native:
            issues.append(
                {
                    "type": "runtime_not_native_but_checklist_all_native",
                    "module": module,
                    "detail": "matrix runtime status is not RUNTIME_NATIVE but checklist rows are all NATIVE",
                }
            )

    for module in sorted(checklist_modules - matrix_modules):
        issues.append(
            {
                "type": "checklist_module_not_in_matrix",
                "module": module,
                "detail": "module exists in checklist but not in matrix",
            }
        )
    return issues


def render_markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    matrix = payload["matrix"]
    milestones = payload["milestones"]
    checklists = payload["checklists"]
    consistency_issues = payload["consistency_issues"]

    lines: list[str] = []
    lines.append("# Functional Parity Gap Report (v0)")
    lines.append("")
    lines.append(f"- Generated at: `{payload['generated_at']}`")
    lines.append("")
    lines.append("## Snapshot")
    lines.append("")
    lines.append(
        f"- Runtime native modules: `{summary['runtime_native_module_count']}/{summary['total_module_count']}`"
    )
    lines.append(f"- Runtime open modules: `{summary['runtime_open_module_count']}`")
    lines.append(f"- Contract open modules: `{summary['contract_open_module_count']}`")
    lines.append(f"- Open milestones: `{summary['open_milestone_count']}`")
    lines.append(f"- Checklist non-native rows: `{summary['checklist_non_native_row_count']}`")
    lines.append(f"- Matrix/checklist consistency issues: `{summary['consistency_issue_count']}`")
    lines.append("")
    lines.append("## Runtime Open Modules")
    lines.append("")
    lines.append("| Module | Runtime Status | Contract Status | Phase |")
    lines.append("|---|---|---|---|")
    runtime_open_rows = [
        row for row in matrix["modules"] if row["runtime_status"] != "RUNTIME_NATIVE"
    ]
    if runtime_open_rows:
        for row in runtime_open_rows:
            lines.append(
                f"| `{row['module']}` | {row['runtime_status']} | {row['contract_status']} | {row['parity_phase']} |"
            )
    else:
        lines.append("| `none` | - | - | - |")
    lines.append("")
    lines.append("## Open Milestones")
    lines.append("")
    lines.append("| ID | Milestone | Priority | Status |")
    lines.append("|---|---|---|---|")
    open_milestones = [row for row in milestones["rows"] if row["status"] != MILESTONE_DONE_STATUS]
    if open_milestones:
        for row in open_milestones:
            lines.append(
                f"| `{row['id']}` | {row['milestone']} | {row['priority']} | {row['status']} |"
            )
    else:
        lines.append("| `none` | - | - | - |")
    lines.append("")
    lines.append("## Checklist Non-Native Rows")
    lines.append("")
    lines.append("| Module | Feature | Status | Checklist |")
    lines.append("|---|---|---|---|")
    non_native_rows = [row for row in checklists["rows"] if row["status"] != "NATIVE"]
    if non_native_rows:
        for row in non_native_rows:
            lines.append(
                f"| `{row['module']}` | {row['feature']} | {row['status']} | {Path(row['checklist_path']).name} |"
            )
    else:
        lines.append("| `none` | - | - | - |")
    lines.append("")
    lines.append("## Consistency Issues")
    lines.append("")
    lines.append("| Type | Module | Detail |")
    lines.append("|---|---|---|")
    if consistency_issues:
        for issue in consistency_issues:
            lines.append(
                f"| `{issue['type']}` | `{issue['module']}` | {issue['detail']} |"
            )
    else:
        lines.append("| `none` | - | - |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    try:
        args = parse_args()
        matrix_path = Path(args.matrix).resolve()
        master_plan_path = Path(args.master_plan).resolve()
        checklist_paths = [Path(raw).resolve() for raw in args.checklist if str(raw).strip()]
        if not checklist_paths:
            checklist_paths = [
                Path("PHASE1_MODULE_PARITY_CHECKLIST.md").resolve(),
                Path("PHASE2_MODULE_PARITY_CHECKLIST.md").resolve(),
                Path("PHASE3_MODULE_PARITY_CHECKLIST.md").resolve(),
                Path("PHASE4_MODULE_PARITY_CHECKLIST.md").resolve(),
            ]
        out_json_path = Path(args.out_json).resolve()
        out_markdown_path = Path(args.out_markdown).resolve()
        fail_on_open_gaps = parse_bool(
            args.fail_on_open_gaps,
            default=False,
            field="fail-on-open-gaps",
        )

        matrix_rows = parse_matrix_rows(matrix_path.read_text(encoding="utf-8"))
        milestone_rows = parse_master_plan_milestones(master_plan_path.read_text(encoding="utf-8"))
        checklist_rows: list[dict[str, str]] = []
        for checklist_path in checklist_paths:
            checklist_rows.extend(
                parse_checklist_rows(
                    checklist_path.read_text(encoding="utf-8"),
                    checklist_path=checklist_path,
                )
            )

        consistency_issues = build_consistency_issues(matrix_rows, checklist_rows)
        runtime_open_modules = [row for row in matrix_rows if row["runtime_status"] != "RUNTIME_NATIVE"]
        runtime_native_modules = [row for row in matrix_rows if row["runtime_status"] == "RUNTIME_NATIVE"]
        contract_open_modules = [row for row in matrix_rows if row["contract_status"] != "CONTRACT_NATIVE"]
        open_milestones = [row for row in milestone_rows if row["status"] != MILESTONE_DONE_STATUS]
        checklist_non_native_rows = [row for row in checklist_rows if row["status"] != "NATIVE"]

        total_module_count = len(matrix_rows)
        runtime_native_ratio = 0.0
        if total_module_count > 0:
            runtime_native_ratio = len(runtime_native_modules) / float(total_module_count)

        payload: dict[str, Any] = {
            "functional_parity_gap_report_schema_version": FUNCTIONAL_PARITY_GAP_REPORT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inputs": {
                "matrix_path": str(matrix_path),
                "master_plan_path": str(master_plan_path),
                "checklist_paths": [str(path) for path in checklist_paths],
            },
            "summary": {
                "total_module_count": total_module_count,
                "runtime_native_module_count": len(runtime_native_modules),
                "runtime_open_module_count": len(runtime_open_modules),
                "runtime_native_ratio": round(runtime_native_ratio, 6),
                "contract_open_module_count": len(contract_open_modules),
                "open_milestone_count": len(open_milestones),
                "checklist_non_native_row_count": len(checklist_non_native_rows),
                "consistency_issue_count": len(consistency_issues),
                "open_gap_count_total": (
                    len(runtime_open_modules)
                    + len(contract_open_modules)
                    + len(open_milestones)
                    + len(checklist_non_native_rows)
                ),
            },
            "matrix": {
                "modules": matrix_rows,
            },
            "milestones": {
                "rows": milestone_rows,
            },
            "checklists": {
                "rows": checklist_rows,
            },
            "consistency_issues": consistency_issues,
        }

        out_json_path.parent.mkdir(parents=True, exist_ok=True)
        out_markdown_path.parent.mkdir(parents=True, exist_ok=True)
        out_json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        out_markdown_path.write_text(render_markdown_report(payload), encoding="utf-8")

        summary = payload["summary"]
        print(
            "[summary] "
            f"runtime_native={summary['runtime_native_module_count']}/{summary['total_module_count']} "
            f"runtime_open={summary['runtime_open_module_count']} "
            f"contract_open={summary['contract_open_module_count']} "
            f"milestone_open={summary['open_milestone_count']} "
            f"checklist_non_native_rows={summary['checklist_non_native_row_count']} "
            f"consistency_issues={summary['consistency_issue_count']}"
        )
        print(f"[ok] out_json={out_json_path}")
        print(f"[ok] out_markdown={out_markdown_path}")

        if fail_on_open_gaps and int(summary["open_gap_count_total"]) > 0:
            print(
                f"[error] open gaps remain: {summary['open_gap_count_total']} (fail-on-open-gaps enabled)",
                file=sys.stderr,
            )
            return 3
        return 0
    except (FileNotFoundError, ValueError) as exc:
        message = str(exc)
        step_summary_file = resolve_step_summary_file_from_env()
        if step_summary_file:
            emit_ci_error(
                step_summary_file=step_summary_file,
                source=SOURCE_NAME,
                message=message,
                details={"phase": PHASE_RESOLVE_INPUTS},
            )
        else:
            print(f"[error] {SOURCE_NAME}: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source=SOURCE_NAME,
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
