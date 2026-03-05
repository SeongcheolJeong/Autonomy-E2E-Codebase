#!/usr/bin/env python3
"""Build a minimal prompt contract and audit trail scaffold for Copilot-like flows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_input_parsing import parse_positive_int
from ci_phases import PHASE_RESOLVE_INPUTS
from ci_reporting import emit_ci_error
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import as_nonempty_text, load_labeled_json_object


COPILOT_PROMPT_CONTRACT_SCHEMA_VERSION_V0 = "copilot_prompt_contract_v0"
COPILOT_PROMPT_AUDIT_SCHEMA_VERSION_V0 = "copilot_prompt_audit_v0"
ALLOWED_MODES = {"scenario", "query"}
BLOCKLIST_RULE_WEIGHTS = {
    "disable safety": 1,
    "bypass safety": 1,
    "ignore validation": 1,
    "skip review gate": 1,
}
SOURCE_NAME = "copilot_prompt_contract_stub.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build prompt contract and audit log scaffold")
    parser.add_argument("--mode", required=True, help="Prompt mode: scenario|query")
    parser.add_argument("--prompt", required=True, help="Prompt text")
    parser.add_argument("--context-json", default="", help="Optional context JSON file path")
    parser.add_argument("--pipeline-manifest", default="", help="Optional pipeline_result.json path for trace linkage")
    parser.add_argument(
        "--guard-hold-threshold",
        default="",
        help="Risk score threshold to mark HOLD (default: 1)",
    )
    parser.add_argument("--out", required=True, help="Output prompt contract JSON path")
    parser.add_argument("--audit-log", default="", help="Optional JSONL audit log output path")
    return parser.parse_args()


def _load_optional_context(path_text: str) -> dict[str, Any]:
    normalized = str(path_text).strip()
    if not normalized:
        return {}
    context_path = Path(normalized).resolve()
    return load_labeled_json_object(context_path, label="context-json")


def _load_optional_pipeline_trace(path_text: str) -> dict[str, Any]:
    normalized = str(path_text).strip()
    if not normalized:
        return {}
    manifest_path = Path(normalized).resolve()
    payload = load_labeled_json_object(manifest_path, label="pipeline-manifest")
    release_id = as_nonempty_text(payload.get("release_id"), field="pipeline_manifest.release_id")
    batch_id = as_nonempty_text(payload.get("batch_id"), field="pipeline_manifest.batch_id")
    return {
        "pipeline_manifest_path": str(manifest_path),
        "release_id": release_id,
        "batch_id": batch_id,
        "overall_result": str(payload.get("overall_result", "")).strip(),
    }


def _resolve_mode(value: str) -> str:
    mode = as_nonempty_text(value, field="mode").lower()
    if mode not in ALLOWED_MODES:
        raise ValueError(f"mode must be one of {sorted(ALLOWED_MODES)}")
    return mode


def _evaluate_prompt_guard(prompt_text: str, *, hold_threshold: int) -> tuple[str, list[str], int]:
    lowered = prompt_text.lower()
    reasons = [phrase for phrase in BLOCKLIST_RULE_WEIGHTS if phrase in lowered]
    risk_score = sum(BLOCKLIST_RULE_WEIGHTS[phrase] for phrase in reasons)
    if risk_score >= hold_threshold and risk_score > 0:
        return "HOLD", reasons, risk_score
    return "PASS", reasons, risk_score


def _artifact_type_for_mode(mode: str) -> str:
    if mode == "scenario":
        return "scenario_yaml"
    return "sql_query"


def main() -> int:
    try:
        args = parse_args()
        mode = _resolve_mode(args.mode)
        prompt_text = as_nonempty_text(args.prompt, field="prompt")
        context_payload = _load_optional_context(args.context_json)
        pipeline_trace = _load_optional_pipeline_trace(args.pipeline_manifest)
        hold_threshold = parse_positive_int(
            str(args.guard_hold_threshold),
            default=1,
            field="guard-hold-threshold",
        )
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        prompt_hash_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        guard_result, guard_reasons, guard_score = _evaluate_prompt_guard(
            prompt_text,
            hold_threshold=hold_threshold,
        )
        contract = {
            "copilot_prompt_contract_schema_version": COPILOT_PROMPT_CONTRACT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "prompt_text": prompt_text,
            "prompt_hash_sha256": prompt_hash_sha256,
            "guard_result": guard_result,
            "guard_reasons": guard_reasons,
            "guard_score": guard_score,
            "guard_hold_threshold": hold_threshold,
            "context": context_payload,
            "recommended_output": {
                "artifact_type": _artifact_type_for_mode(mode),
                "requires_human_review": True,
            },
        }
        if pipeline_trace:
            contract["pipeline_trace"] = pipeline_trace
        out_path.write_text(json.dumps(contract, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        audit_log_path_text = str(args.audit_log).strip()
        if audit_log_path_text:
            audit_path = Path(audit_log_path_text).resolve()
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_entry = {
                "copilot_prompt_audit_schema_version": COPILOT_PROMPT_AUDIT_SCHEMA_VERSION_V0,
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "prompt_hash_sha256": prompt_hash_sha256,
                "guard_result": guard_result,
                "guard_reasons": guard_reasons,
                "guard_score": guard_score,
                "guard_hold_threshold": hold_threshold,
                "contract_path": str(out_path),
            }
            if pipeline_trace:
                audit_entry["pipeline_trace"] = pipeline_trace
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(audit_entry, ensure_ascii=True) + "\n")

        print(f"[ok] mode={mode}")
        print(f"[ok] guard_result={guard_result}")
        print(f"[ok] guard_score={guard_score}")
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
