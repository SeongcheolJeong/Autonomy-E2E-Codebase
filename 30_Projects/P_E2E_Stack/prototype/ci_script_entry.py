#!/usr/bin/env python3
"""Shared top-level error handling helper for standalone CI scripts."""

from __future__ import annotations

import os
import sys
from typing import Callable

from ci_reporting import emit_ci_error, normalize_exception_message


def resolve_step_summary_file_from_env() -> str:
    return (
        os.environ.get("STEP_SUMMARY_FILE", "").strip()
        or os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    )


def resolve_github_output_file_from_env() -> str:
    return (
        os.environ.get("GITHUB_OUTPUT_PATH", "").strip()
        or os.environ.get("GITHUB_OUTPUT", "").strip()
    )


def run_with_error_handling(
    main_fn: Callable[[], int],
    *,
    source: str,
    step_summary_file: str = "",
    phase: str = "",
) -> int:
    details = {"phase": phase} if str(phase).strip() else None
    try:
        return int(main_fn())
    except KeyboardInterrupt:
        if str(step_summary_file).strip():
            emit_ci_error(
                step_summary_file=step_summary_file,
                source=source,
                message="interrupted",
                details=details,
            )
        else:
            print(f"[error] {source}: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        message = normalize_exception_message(exc)
        if str(step_summary_file).strip():
            emit_ci_error(
                step_summary_file=step_summary_file,
                source=source,
                message=message,
                details=details,
            )
        else:
            print(f"[error] {source}: {message}", file=sys.stderr)
        return 1
