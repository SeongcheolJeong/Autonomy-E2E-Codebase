#!/usr/bin/env python3
"""Insert one progress-log entry under a date section in STACK_PROGRESS_LOG.md."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import as_nonempty_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update STACK_PROGRESS_LOG.md with one commit evidence entry")
    parser.add_argument("--log-file", default="STACK_PROGRESS_LOG.md", help="Target progress log markdown file path")
    parser.add_argument(
        "--date",
        default="",
        help="Date section in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--entry",
        required=True,
        help="Entry text to insert (leading '- ' is optional)",
    )
    return parser.parse_args()


def normalize_entry_line(value: str) -> str:
    entry = as_nonempty_text(value, field="entry")
    if entry.startswith("- "):
        return entry
    if entry.startswith("-"):
        return f"- {entry[1:].lstrip()}"
    return f"- {entry}"


def resolve_entry_date(raw_date: str) -> str:
    if not str(raw_date).strip():
        return datetime.now().strftime("%Y-%m-%d")
    date_text = as_nonempty_text(raw_date, field="date")
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError as exc:  # pragma: no cover - exercised via tests
        raise ValueError("date must be YYYY-MM-DD") from exc
    return date_text


def _find_section_start(lines: list[str], heading: str) -> int:
    for idx, line in enumerate(lines):
        if line == heading:
            return idx
    return -1


def _find_next_section(lines: list[str], start_idx: int) -> int:
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx].startswith("## "):
            return idx
    return len(lines)


def _render_lines(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def insert_progress_entry(log_text: str, *, date_text: str, entry_line: str) -> tuple[str, bool]:
    lines = log_text.splitlines()
    heading = f"## {date_text}"
    section_start = _find_section_start(lines, heading)

    if section_start >= 0:
        section_end = _find_next_section(lines, section_start)
        if entry_line in lines[section_start + 1 : section_end]:
            return _render_lines(lines), False
        insert_at = section_start + 1
        if insert_at >= len(lines) or lines[insert_at] != "":
            lines.insert(insert_at, "")
        insert_at += 1
        lines.insert(insert_at, entry_line)
        return _render_lines(lines), True

    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = idx
            break
    lines[insert_at:insert_at] = [heading, "", entry_line, ""]
    return _render_lines(lines), True


def main() -> int:
    args = parse_args()
    log_path = Path(args.log_file).resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"log file not found: {log_path}")

    date_text = resolve_entry_date(args.date)
    entry_line = normalize_entry_line(args.entry)
    current_text = log_path.read_text(encoding="utf-8")
    rendered_text, changed = insert_progress_entry(
        current_text,
        date_text=date_text,
        entry_line=entry_line,
    )
    if changed:
        log_path.write_text(rendered_text, encoding="utf-8")
        print(f"[ok] updated {log_path} date={date_text}")
    else:
        print(f"[skip] entry already exists in {log_path} date={date_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="update_stack_progress_log.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
