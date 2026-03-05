#!/usr/bin/env python3
"""Check Cloud batch run statuses against scenario catalog expectations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary

ERROR_SOURCE = "check_batch_against_catalog.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare batch_result statuses with catalog manifest")
    parser.add_argument("--batch-result", required=True, help="Path to batch_result.json")
    parser.add_argument("--catalog-manifest", required=True, help="Path to catalog_manifest.json")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def main() -> int:
    try:
        args = parse_args()
        batch_result_path = Path(args.batch_result).resolve()
        catalog_manifest_path = Path(args.catalog_manifest).resolve()

        batch_payload = load_json(batch_result_path)
        catalog_payload = load_json(catalog_manifest_path)

        runs = batch_payload.get("runs", [])
        catalog_scenarios = catalog_payload.get("scenarios", [])
        if not isinstance(runs, list):
            raise ValueError("batch_result.runs must be a list")
        if not isinstance(catalog_scenarios, list):
            raise ValueError("catalog_manifest.scenarios must be a list")

        expected_by_file: dict[str, str] = {}
        for item in catalog_scenarios:
            if not isinstance(item, dict):
                continue
            scenario_file = str(item.get("scenario_file", ""))
            expected_status = str(item.get("expected_status", ""))
            if not scenario_file or not expected_status:
                continue
            expected_by_file[scenario_file] = expected_status

        actual_by_file: dict[str, str] = {}
        run_id_by_file: dict[str, str] = {}
        for run in runs:
            if not isinstance(run, dict):
                continue
            scenario_path = str(run.get("scenario", ""))
            if not scenario_path:
                continue
            scenario_file = Path(scenario_path).name
            actual_by_file[scenario_file] = str(run.get("status", "unknown"))
            run_id_by_file[scenario_file] = str(run.get("run_id", ""))

        mismatches: list[str] = []
        missing_in_batch: list[str] = []
        unexpected_in_batch: list[str] = []

        for scenario_file, expected_status in expected_by_file.items():
            if scenario_file not in actual_by_file:
                missing_in_batch.append(scenario_file)
                continue
            actual_status = actual_by_file[scenario_file]
            if actual_status != expected_status:
                run_id = run_id_by_file.get(scenario_file, "unknown_run")
                mismatches.append(
                    f"{scenario_file} run_id={run_id} expected={expected_status} actual={actual_status}"
                )

        for scenario_file in sorted(actual_by_file.keys()):
            if scenario_file not in expected_by_file:
                unexpected_in_batch.append(scenario_file)

        print(f"[info] catalog={catalog_manifest_path}")
        print(f"[info] batch_result={batch_result_path}")
        print(f"[info] expected_scenarios={len(expected_by_file)} actual_scenarios={len(actual_by_file)}")

        if missing_in_batch:
            print("[error] scenarios missing in batch result:")
            for scenario_file in missing_in_batch:
                print(f"  - {scenario_file}")

        if unexpected_in_batch:
            print("[error] unexpected scenarios in batch result:")
            for scenario_file in unexpected_in_batch:
                print(f"  - {scenario_file}")

        if mismatches:
            print("[error] status mismatches:")
            for mismatch in mismatches:
                print(f"  - {mismatch}")

        if missing_in_batch or unexpected_in_batch or mismatches:
            return 1

        print("[ok] all scenario statuses match expected catalog outcomes")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] check_batch_against_catalog.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
