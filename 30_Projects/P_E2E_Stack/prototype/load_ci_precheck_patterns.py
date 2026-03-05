#!/usr/bin/env python3
"""Load PR quick precheck regex patterns from JSON."""

from __future__ import annotations

import argparse
from pathlib import Path

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import load_json_object


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load PR quick precheck regex patterns")
    parser.add_argument("--rules-file", required=True, help="Path to precheck rules JSON file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rules_file = Path(args.rules_file).resolve()
    payload = load_json_object(rules_file, subject="rules file")

    raw_patterns = payload.get("include_patterns")
    if not isinstance(raw_patterns, list):
        raise ValueError("rules file must include an include_patterns list")

    patterns: list[str] = []
    seen: set[str] = set()
    for raw in raw_patterns:
        if not isinstance(raw, str):
            raise ValueError("include_patterns must contain only strings")
        pattern = raw.strip()
        if not pattern or pattern in seen:
            continue
        patterns.append(pattern)
        seen.add(pattern)

    if not patterns:
        raise ValueError("no precheck include patterns configured")

    print("\n".join(patterns))
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="load_ci_precheck_patterns.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
