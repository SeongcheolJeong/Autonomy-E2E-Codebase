#!/usr/bin/env python3
"""Shared subprocess helpers for CI wrappers."""

from __future__ import annotations

import subprocess
import sys

from ci_commands import shell_join


def run_command(cmd: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    """Run command without raising, optionally capturing output."""
    return subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run command and capture text output without raising on non-zero."""
    return run_command(cmd, capture_output=True)


def emit_captured_output(
    proc: subprocess.CompletedProcess[str],
    *,
    print_stdout: bool = True,
    print_stderr: bool = True,
    flush: bool = False,
) -> None:
    """Emit captured subprocess output with stderr preserved on stderr."""
    if print_stdout and proc.stdout:
        print(proc.stdout.rstrip(), flush=flush)
    if print_stderr and proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr, flush=flush)


def compact_failure_detail(raw: str, *, max_chars: int = 400) -> str:
    """Normalize subprocess error detail to one line and cap excessive length."""
    compacted = " ".join(str(raw).split())
    if len(compacted) <= max_chars:
        return compacted
    return f"{compacted[:max_chars]}... (+{len(compacted) - max_chars} chars)"


def _select_failure_detail(stderr: str | None, stdout: str | None) -> str:
    stderr_lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    stdout_lines = [line.strip() for line in str(stdout or "").splitlines() if line.strip()]

    for lines in (stderr_lines, stdout_lines):
        for line in reversed(lines):
            if line.startswith("[error]"):
                return line

    if stderr_lines:
        return stderr_lines[-1]
    if stdout_lines:
        return stdout_lines[-1]
    return ""


def format_subprocess_failure(
    cmd: list[str],
    *,
    returncode: int,
    stdout: str | None,
    stderr: str | None,
    context: str = "command",
    display_cmd: str | None = None,
) -> str:
    """Render consistent subprocess failure messages with command and details."""
    prefix = f"{context} failed with exit code {returncode}: {display_cmd or shell_join(cmd)}"
    raw_detail = _select_failure_detail(stderr=stderr, stdout=stdout)
    detail = compact_failure_detail(raw_detail) if raw_detail else ""
    if detail:
        return f"{prefix}: {detail}"
    return prefix


def run_command_or_raise(
    cmd: list[str],
    *,
    capture_output: bool = False,
    context: str = "command",
    display_cmd: str | None = None,
    emit_output_on_success: bool = False,
    emit_output_on_failure: bool = True,
    flush: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run command and raise RuntimeError on non-zero, with optional output emission."""
    proc = run_command(cmd, capture_output=capture_output)
    if proc.returncode != 0:
        if capture_output and emit_output_on_failure:
            emit_captured_output(proc, flush=flush)
        raise RuntimeError(
            format_subprocess_failure(
                cmd,
                returncode=int(proc.returncode),
                stdout=proc.stdout,
                stderr=proc.stderr,
                context=context,
                display_cmd=display_cmd,
            )
        )
    if capture_output and emit_output_on_success:
        emit_captured_output(proc, flush=flush)
    return proc


def run_logged_command_or_raise(
    cmd: list[str],
    *,
    capture_output: bool = False,
    context: str = "command",
    display_cmd: str | None = None,
    emit_output_on_success: bool = False,
    emit_output_on_failure: bool = True,
    flush: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Log command then run it with shared subprocess failure handling."""
    print(f"[cmd] {display_cmd or shell_join(cmd)}", flush=flush)
    return run_command_or_raise(
        cmd,
        capture_output=capture_output,
        context=context,
        display_cmd=display_cmd,
        emit_output_on_success=emit_output_on_success,
        emit_output_on_failure=emit_output_on_failure,
        flush=flush,
    )


def run_capture_stdout_or_raise(
    cmd: list[str],
    *,
    context: str = "command",
    emit_output_on_success: bool = True,
    emit_output_on_failure: bool = True,
) -> str:
    """Run command with captured output and raise RuntimeError on non-zero exit."""
    proc = run_command_or_raise(
        cmd,
        capture_output=True,
        context=context,
        emit_output_on_success=emit_output_on_success,
        emit_output_on_failure=emit_output_on_failure,
    )
    return str(proc.stdout)


def run_logged_capture_stdout_or_raise(
    cmd: list[str],
    *,
    context: str = "command",
    emit_output_on_success: bool = True,
    emit_output_on_failure: bool = True,
) -> str:
    """Log command before delegating to captured subprocess runner."""
    proc = run_logged_command_or_raise(
        cmd,
        capture_output=True,
        context=context,
        emit_output_on_success=emit_output_on_success,
        emit_output_on_failure=emit_output_on_failure,
    )
    return str(proc.stdout)
