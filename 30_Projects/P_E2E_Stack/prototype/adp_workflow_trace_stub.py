#!/usr/bin/env python3
"""Build a minimal ADP workflow trace artifact from pipeline/report manifests."""

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


ADP_WORKFLOW_TRACE_SCHEMA_VERSION_V0 = "adp_workflow_trace_v0"
RESULTS_REQUIRING_RESPONSIBILITY_NOTICE = {"WARN", "HOLD"}
SOURCE_NAME = "adp_workflow_trace_stub.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ADP workflow trace scaffold")
    parser.add_argument("--pipeline-manifest", required=True, help="run_e2e_pipeline.py pipeline_result.json path")
    parser.add_argument(
        "--release-summary",
        action="append",
        default=[],
        help="Optional release summary JSON path (repeatable)",
    )
    parser.add_argument("--out", required=True, help="Output trace JSON path")
    return parser.parse_args()


def _collect_release_summaries(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = load_labeled_json_object(resolved, label="release summary")
        release_id = str(payload.get("release_id", "")).strip()
        sds_version = str(payload.get("sds_version", "")).strip()
        final_result = str(payload.get("final_result", "")).strip()
        if not release_id or not sds_version or not final_result:
            raise ValueError(
                "release summary must include non-empty release_id, sds_version, and final_result: "
                f"{resolved}"
            )
        rows.append(
            {
                "release_id": release_id,
                "sds_version": sds_version,
                "final_result": final_result,
                "summary_path": str(resolved),
                "user_responsibility_notice": str(payload.get("user_responsibility_notice", "")).strip(),
            }
        )
    rows.sort(key=lambda row: (str(row["release_id"]), str(row["sds_version"])))
    return rows


def _resolve_module_hooks(payload: dict[str, Any]) -> dict[str, bool]:
    def enabled(name: str) -> bool:
        section = payload.get(name)
        return isinstance(section, dict) and bool(section.get("enabled"))

    return {
        "phase2_enabled": enabled("phase2_hooks"),
        "phase3_enabled": enabled("phase3_hooks"),
        "phase4_enabled": enabled("phase4_hooks"),
    }


def _build_user_responsibility_contract(
    *,
    manifest_payload: dict[str, Any],
    release_summaries: list[dict[str, Any]],
    overall_result: str,
) -> dict[str, Any]:
    notices: list[str] = []
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_notice(*, source: str, text: str) -> None:
        normalized = str(text).strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        notices.append(normalized)
        sources.append({"source": source, "notice": normalized})

    add_notice(
        source="pipeline_manifest",
        text=str(manifest_payload.get("user_responsibility_notice", "")).strip(),
    )

    for row in release_summaries:
        add_notice(
            source=f"release_summary:{row['release_id']}:{row['sds_version']}",
            text=str(row.get("user_responsibility_notice", "")).strip(),
        )

    requires_ack = str(overall_result).strip().upper() in RESULTS_REQUIRING_RESPONSIBILITY_NOTICE
    if requires_ack and not notices:
        raise ValueError(
            "user_responsibility_notice is required when overall_result is WARN or HOLD"
        )
    return {
        "requires_ack": requires_ack,
        "notice_count": len(notices),
        "notices": notices,
        "sources": sources,
    }


def main() -> int:
    try:
        args = parse_args()
        pipeline_manifest = Path(args.pipeline_manifest).resolve()
        release_summary_paths = [Path(path).resolve() for path in args.release_summary]
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        manifest_payload = load_labeled_json_object(pipeline_manifest, label="pipeline manifest")
        release_id = as_nonempty_text(manifest_payload.get("release_id"), field="release_id")
        batch_id = as_nonempty_text(manifest_payload.get("batch_id"), field="batch_id")
        overall_result = as_nonempty_text(manifest_payload.get("overall_result"), field="overall_result")
        reports = manifest_payload.get("reports", [])
        if not isinstance(reports, list):
            raise ValueError("reports must be a list")
        sds_versions = sorted(
            {
                str(report.get("sds_version", "")).strip()
                for report in reports
                if isinstance(report, dict) and str(report.get("sds_version", "")).strip()
            }
        )
        release_summaries = _collect_release_summaries(release_summary_paths)
        module_hooks = _resolve_module_hooks(manifest_payload)
        user_responsibility = _build_user_responsibility_contract(
            manifest_payload=manifest_payload,
            release_summaries=release_summaries,
            overall_result=overall_result,
        )

        trace = {
            "adp_workflow_trace_schema_version": ADP_WORKFLOW_TRACE_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "release_id": release_id,
            "batch_id": batch_id,
            "overall_result": overall_result,
            "pipeline_manifest_path": str(pipeline_manifest),
            "module_hooks": module_hooks,
            "report_count": len(reports),
            "sds_versions": sds_versions,
            "release_summary_count": len(release_summaries),
            "release_summaries": release_summaries,
            "user_responsibility": user_responsibility,
        }
        out_path.write_text(json.dumps(trace, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] report_count={len(reports)}")
        print(f"[ok] release_summary_count={len(release_summaries)}")
        print(f"[ok] responsibility_notice_count={user_responsibility['notice_count']}")
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
