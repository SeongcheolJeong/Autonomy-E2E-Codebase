#!/usr/bin/env python3
"""Append a simple key-value section to GitHub Step Summary."""

from __future__ import annotations

import argparse
from pathlib import Path

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write key-value section to step summary markdown")
    parser.add_argument(
        "--summary-file",
        default="",
        help="Path to step summary file (defaults to STEP_SUMMARY_FILE or GITHUB_STEP_SUMMARY env)",
    )
    parser.add_argument("--title", required=True, help="Section title")
    parser.add_argument(
        "--item",
        action="append",
        default=[],
        help="One key=value item line (repeatable)",
    )
    return parser.parse_args()


def parse_item(raw: str) -> tuple[str, str]:
    key, sep, value = raw.partition("=")
    key = key.strip()
    if not sep or not key:
        raise ValueError(f"invalid --item format: {raw!r} (expected key=value)")
    return key, value.strip()


def sanitize_markdown_value(value: str) -> str:
    # Keep summary rendering stable if values include backticks.
    return value.replace("`", "\\`")


def main() -> int:
    args = parse_args()
    summary_path_raw = args.summary_file.strip() or resolve_step_summary_file_from_env()
    if not summary_path_raw:
        raise ValueError("--summary-file or STEP_SUMMARY_FILE or GITHUB_STEP_SUMMARY is required")

    title = args.title.strip()
    if not title:
        raise ValueError("--title must be non-empty")
    if not args.item:
        raise ValueError("at least one --item is required")

    lines: list[str] = [f"### {title}"]
    for raw in args.item:
        key, value = parse_item(str(raw))
        lines.append(f"- {key}: `{sanitize_markdown_value(value)}`")

    summary_path = Path(summary_path_raw)
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="write_ci_summary_section.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
