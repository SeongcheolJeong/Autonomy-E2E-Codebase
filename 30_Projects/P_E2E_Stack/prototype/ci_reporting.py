#!/usr/bin/env python3
"""Shared CI reporting helpers for step summary and error output."""

from __future__ import annotations

import sys
from pathlib import Path

CI_ERROR_TITLE = "E2E CI Error"
CI_ERROR_DETAIL_ORDER = (
    "phase",
    "command",
    "exit_code",
    "log_path",
    "manifest_path",
)


def normalize_error_message(message: str) -> str:
    normalized = str(message).strip()
    return normalized or "unknown_error"


def normalize_exception_message(exc: BaseException) -> str:
    normalized = str(exc).strip()
    return normalized or exc.__class__.__name__


def append_step_summary(path: str, text: str, *, print_if_missing: bool = False) -> None:
    output_path = str(path).strip()
    if not output_path:
        if print_if_missing:
            print(text)
        return

    summary_path = Path(output_path).resolve()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    payload = text if text.endswith("\n") else text + "\n"
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def emit_ci_error(
    *,
    step_summary_file: str,
    source: str,
    message: str,
    details: dict[str, str] | None = None,
) -> None:
    normalized = normalize_error_message(message)
    print(f"[error] {source}: {normalized}", file=sys.stderr)
    lines = [f"- source: `{source}`"]
    if details:
        for key in CI_ERROR_DETAIL_ORDER:
            value = details.get(key, "")
            normalized_value = str(value).strip()
            if normalized_value:
                lines.append(f"- {key}: `{normalized_value}`")
    append_step_summary(
        step_summary_file,
        f"## {CI_ERROR_TITLE}\n\n"
        + "\n".join(lines)
        + "\n\n"
        f"```text\n{normalized}\n```\n",
    )
