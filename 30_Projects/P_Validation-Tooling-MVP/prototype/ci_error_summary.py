#!/usr/bin/env python3
"""Shared CI step-summary error helpers for Validation-Tooling scripts."""

from __future__ import annotations

import os
from pathlib import Path


CI_ERROR_TITLE = "E2E CI Error"


def resolve_step_summary_file_from_env() -> str:
    return (
        os.environ.get("STEP_SUMMARY_FILE", "").strip()
        or os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    )


def append_step_summary(path: str, text: str) -> None:
    output_path = str(path).strip()
    if not output_path:
        return
    summary_path = Path(output_path).resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def write_ci_error_summary(*, source: str, phase: str, message: str) -> None:
    normalized = str(message).strip() or "unknown_error"
    step_summary_file = resolve_step_summary_file_from_env()
    if not step_summary_file:
        return
    append_step_summary(
        step_summary_file,
        f"## {CI_ERROR_TITLE}\n\n"
        f"- source: `{str(source).strip()}`\n"
        f"- phase: `{str(phase).strip()}`\n\n"
        f"```text\n{normalized}\n```\n",
    )
