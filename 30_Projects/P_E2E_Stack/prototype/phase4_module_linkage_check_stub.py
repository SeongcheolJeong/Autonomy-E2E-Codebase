#!/usr/bin/env python3
"""Validate Phase-4 module linkage consistency across matrix and checklist docs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_reporting import emit_ci_error
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import as_nonempty_text
from phase4_linkage_contract import (
    PHASE4_LINKAGE_ALLOWED_MODULES_CSV,
    PHASE4_LINKAGE_ALLOWED_MODULES_TEXT,
    resolve_phase4_linkage_modules,
)

PHASE4_MODULE_LINKAGE_REPORT_SCHEMA_VERSION_V0 = "phase4_module_linkage_report_v0"
MATRIX_ALLOWED_STATUSES = {"PHASE4_IN_PROGRESS", "PHASE4_DONE"}
CHECKLIST_READY_STATUSES = {"PARTIAL", "NATIVE"}
CHECKLIST_DONE_STATUS = "NATIVE"
CHECKLIST_ALLOWED_STATUSES = {"TO_AUDIT", "PARTIAL", "NATIVE"}
SOURCE_NAME = "phase4_module_linkage_check_stub.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Phase-4 module linkage checklist/matrix alignment")
    parser.add_argument("--matrix", required=True, help="STACK_MODULE_PARITY_MATRIX.md path")
    parser.add_argument("--checklist", required=True, help="PHASE4_MODULE_PARITY_CHECKLIST.md path")
    parser.add_argument(
        "--reference-map",
        default=str(Path(__file__).resolve().with_name("PHASE4_EXTERNAL_REFERENCE_MAP.md")),
        help="PHASE4_EXTERNAL_REFERENCE_MAP.md path",
    )
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        help=(
            "Module name to validate "
            f"(repeatable; allowed: {PHASE4_LINKAGE_ALLOWED_MODULES_TEXT}; "
            f"default: {PHASE4_LINKAGE_ALLOWED_MODULES_CSV})"
        ),
    )
    parser.add_argument("--out", required=True, help="Output linkage report JSON path")
    return parser.parse_args()


def _parse_matrix_status(matrix_text: str, *, module: str) -> str:
    pattern = re.compile(rf"^\|\s*`{re.escape(module)}`\s*\|.*$", re.MULTILINE)
    match = pattern.search(matrix_text)
    if match is None:
        raise ValueError(f"module row not found in matrix: {module}")
    columns = [column.strip() for column in match.group(0).strip().strip("|").split("|")]
    if len(columns) < 6:
        raise ValueError(f"matrix row has unexpected format for module: {module}")
    return as_nonempty_text(columns[5], field=f"matrix status for {module}")


def _extract_module_section(checklist_text: str, *, module: str) -> str:
    marker = f"## {module}"
    start = checklist_text.find(marker)
    if start < 0:
        raise ValueError(f"module section not found in checklist: {module}")
    remainder = checklist_text[start + len(marker) :]
    next_section_idx = remainder.find("\n## ")
    if next_section_idx < 0:
        return remainder
    return remainder[:next_section_idx]


def _parse_checklist_statuses(section_text: str, *, module: str) -> list[str]:
    statuses: list[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 3:
            continue
        status = columns[2]
        if not status or status.lower() == "status":
            continue
        if status not in CHECKLIST_ALLOWED_STATUSES:
            raise ValueError(
                f"checklist status for {module} must be one of {sorted(CHECKLIST_ALLOWED_STATUSES)}: {status}"
            )
        statuses.append(status)
    if not statuses:
        raise ValueError(f"checklist table status rows not found for module: {module}")
    return statuses


def _expected_matrix_status(checklist_statuses: list[str]) -> tuple[str, bool]:
    checklist_all_native = bool(checklist_statuses) and all(status == CHECKLIST_DONE_STATUS for status in checklist_statuses)
    if checklist_all_native:
        return "PHASE4_DONE", True
    return "PHASE4_IN_PROGRESS", False


def _strip_optional_backticks(cell_value: str) -> str:
    value = cell_value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1].strip()
    return value


def _parse_reference_map(references_text: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw_line in references_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 5:
            continue
        module_cell = _strip_optional_backticks(columns[0])
        if not module_cell or module_cell.lower() in {"phase-4 module", "module"}:
            continue
        module = as_nonempty_text(module_cell, field="reference-map module")
        if module in rows:
            raise ValueError(f"duplicate module row found in external reference map: {module}")
        priority = as_nonempty_text(
            _strip_optional_backticks(columns[1]),
            field=f"reference-map priority for {module}",
        )
        references_cell = columns[2].strip()
        references = [item.strip() for item in re.findall(r"`([^`]+)`", references_cell) if item.strip()]
        if not references:
            fallback_refs = [item.strip() for item in references_cell.split(",") if item.strip()]
            references = fallback_refs
        pattern_to_extract = as_nonempty_text(columns[3], field=f"reference-map pattern to extract for {module}")
        local_first_target = as_nonempty_text(columns[4], field=f"reference-map local first target for {module}")
        rows[module] = {
            "priority": priority,
            "reference_repositories": references,
            "pattern_to_extract": pattern_to_extract,
            "local_first_target": local_first_target,
        }
    return rows


def main() -> int:
    try:
        args = parse_args()
        matrix_path = Path(args.matrix).resolve()
        checklist_path = Path(args.checklist).resolve()
        reference_map_path = Path(args.reference_map).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        modules = resolve_phase4_linkage_modules(args.module, default_to_allowed_when_empty=True)

        matrix_text = matrix_path.read_text(encoding="utf-8")
        checklist_text = checklist_path.read_text(encoding="utf-8")
        reference_map_text = reference_map_path.read_text(encoding="utf-8")
        reference_map = _parse_reference_map(reference_map_text)
        linkage_rows: list[dict[str, Any]] = []

        for module in modules:
            reference_map_entry = reference_map.get(module)
            if reference_map_entry is None:
                raise ValueError(f"module row not found in external reference map: {module}")
            reference_priority = str(reference_map_entry.get("priority", ""))
            reference_repositories = list(reference_map_entry.get("reference_repositories", []))
            reference_pattern_to_extract = str(reference_map_entry.get("pattern_to_extract", ""))
            reference_local_first_target = str(reference_map_entry.get("local_first_target", ""))
            if not reference_repositories:
                raise ValueError(
                    f"external reference map must include at least one primary reference for module: {module}"
                )
            matrix_status = _parse_matrix_status(matrix_text, module=module)
            if matrix_status not in MATRIX_ALLOWED_STATUSES:
                raise ValueError(
                    f"matrix status for {module} must be one of {sorted(MATRIX_ALLOWED_STATUSES)}: {matrix_status}"
                )

            section_text = _extract_module_section(checklist_text, module=module)
            checklist_statuses = _parse_checklist_statuses(section_text, module=module)
            ready_row_count = sum(1 for status in checklist_statuses if status in CHECKLIST_READY_STATUSES)
            if ready_row_count <= 0:
                raise ValueError(
                    f"checklist must include at least one {sorted(CHECKLIST_READY_STATUSES)} row for module: {module}"
                )
            expected_matrix_status, checklist_all_native = _expected_matrix_status(checklist_statuses)
            if matrix_status != expected_matrix_status:
                raise ValueError(
                    f"matrix status for {module} must be {expected_matrix_status} when checklist_all_native={checklist_all_native}: {matrix_status}"
                )

            linkage_rows.append(
                {
                    "module": module,
                    "matrix_status": matrix_status,
                    "expected_matrix_status": expected_matrix_status,
                    "checklist_all_native": checklist_all_native,
                    "checklist_statuses": checklist_statuses,
                    "ready_row_count": ready_row_count,
                    "reference_priority": reference_priority,
                    "reference_repositories": reference_repositories,
                    "reference_repository_count": len(reference_repositories),
                    "reference_pattern_to_extract": reference_pattern_to_extract,
                    "reference_local_first_target": reference_local_first_target,
                }
            )
            print(f"[ok] module={module} matrix_status={matrix_status} ready_row_count={ready_row_count}")

        payload = {
            "phase4_module_linkage_report_schema_version": PHASE4_MODULE_LINKAGE_REPORT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "matrix_path": str(matrix_path),
            "checklist_path": str(checklist_path),
            "reference_map_path": str(reference_map_path),
            "modules": linkage_rows,
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
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
