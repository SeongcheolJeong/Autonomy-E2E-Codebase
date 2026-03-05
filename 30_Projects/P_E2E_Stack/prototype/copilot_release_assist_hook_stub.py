#!/usr/bin/env python3
"""Build a minimal Copilot release-assist hook artifact from prompt/pipeline contracts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_reporting import emit_ci_error
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import as_nonempty_text, load_labeled_json_object


COPILOT_RELEASE_ASSIST_HOOK_SCHEMA_VERSION_V0 = "copilot_release_assist_hook_v0"
COPILOT_PROMPT_CONTRACT_SCHEMA_VERSION_V0 = "copilot_prompt_contract_v0"
PIPELINE_GATED_RESULTS = {"WARN", "HOLD"}
SOURCE_NAME = "copilot_release_assist_hook_stub.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Copilot release-assist hook scaffold")
    parser.add_argument("--prompt-contract", required=True, help="copilot_prompt_contract_v0 JSON path")
    parser.add_argument("--pipeline-manifest", required=True, help="run_e2e_pipeline.py pipeline_result.json path")
    parser.add_argument("--out", required=True, help="Output release-assist hook JSON path")
    return parser.parse_args()


def _validate_prompt_contract(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = as_nonempty_text(
        payload.get("copilot_prompt_contract_schema_version"),
        field="copilot_prompt_contract_schema_version",
    )
    if schema_version != COPILOT_PROMPT_CONTRACT_SCHEMA_VERSION_V0:
        raise ValueError(
            f"copilot_prompt_contract_schema_version must be {COPILOT_PROMPT_CONTRACT_SCHEMA_VERSION_V0}: "
            f"{schema_version}"
        )
    recommended_output = payload.get("recommended_output")
    if not isinstance(recommended_output, dict):
        raise ValueError("recommended_output must be an object")
    return {
        "mode": as_nonempty_text(payload.get("mode"), field="mode"),
        "prompt_hash_sha256": as_nonempty_text(payload.get("prompt_hash_sha256"), field="prompt_hash_sha256"),
        "guard_result": as_nonempty_text(payload.get("guard_result"), field="guard_result"),
        "guard_score": int(payload.get("guard_score", 0)),
        "guard_hold_threshold": int(payload.get("guard_hold_threshold", 1)),
        "recommended_artifact_type": as_nonempty_text(
            recommended_output.get("artifact_type"),
            field="recommended_output.artifact_type",
        ),
    }


def _validate_pipeline_manifest(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "release_id": as_nonempty_text(payload.get("release_id"), field="release_id"),
        "batch_id": as_nonempty_text(payload.get("batch_id"), field="batch_id"),
        "overall_result": as_nonempty_text(payload.get("overall_result"), field="overall_result").upper(),
    }


def _resolve_recommended_action(*, guard_result: str, overall_result: str) -> tuple[str, str]:
    guard = guard_result.upper()
    if guard == "HOLD":
        return "BLOCK_AUTOMATION_AND_REQUIRE_HUMAN_REVIEW", "HOLD"
    if overall_result in PIPELINE_GATED_RESULTS:
        return "REVIEW_BEFORE_RELEASE", overall_result
    return "ALLOW_ASSISTED_DRAFT_ONLY", "PASS"


def main() -> int:
    try:
        args = parse_args()
        prompt_contract_path = Path(args.prompt_contract).resolve()
        pipeline_manifest_path = Path(args.pipeline_manifest).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        prompt_contract_payload = load_labeled_json_object(prompt_contract_path, label="prompt contract")
        pipeline_manifest_payload = load_labeled_json_object(pipeline_manifest_path, label="pipeline manifest")
        prompt_contract = _validate_prompt_contract(prompt_contract_payload)
        pipeline_manifest = _validate_pipeline_manifest(pipeline_manifest_payload)
        recommended_action, gate_state = _resolve_recommended_action(
            guard_result=str(prompt_contract["guard_result"]),
            overall_result=str(pipeline_manifest["overall_result"]),
        )

        artifact = {
            "copilot_release_assist_hook_schema_version": COPILOT_RELEASE_ASSIST_HOOK_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "prompt_contract_path": str(prompt_contract_path),
            "pipeline_manifest_path": str(pipeline_manifest_path),
            "release_id": pipeline_manifest["release_id"],
            "batch_id": pipeline_manifest["batch_id"],
            "overall_result": pipeline_manifest["overall_result"],
            "prompt_contract": prompt_contract,
            "recommended_action": recommended_action,
            "gating": {
                "allow_auto_apply": False,
                "requires_human_review": True,
                "release_gate_state": gate_state,
            },
        }
        out_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] release_id={pipeline_manifest['release_id']}")
        print(f"[ok] recommended_action={recommended_action}")
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
