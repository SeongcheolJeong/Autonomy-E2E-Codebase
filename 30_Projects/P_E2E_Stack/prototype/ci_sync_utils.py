#!/usr/bin/env python3
"""Shared helpers for scope sync scripts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def add_check_flag(parser: argparse.ArgumentParser) -> None:
    """Register --check option with consistent semantics across sync scripts."""
    parser.add_argument("--check", action="store_true", help="Fail if generated outputs are out of sync")


def resolve_repo_root(script_path: str | Path) -> Path:
    """Resolve repository root from a prototype script path."""
    return Path(script_path).resolve().parents[3]


def resolve_sync_script_roots(script_path: str | Path) -> tuple[Path, Path]:
    """Resolve sync script's prototype directory and repository root."""
    prototype_dir = Path(script_path).resolve().parent
    return prototype_dir, resolve_repo_root(script_path)


def resolve_optional_path(path_value: str, *, default_path: Path) -> Path:
    """Resolve user-provided path value, or return default path when omitted."""
    if path_value:
        return Path(path_value).resolve()
    return default_path


def replace_marker_block(
    source_text: str,
    *,
    begin_marker: str,
    end_marker: str,
    generated_lines: Iterable[str],
    subject: str,
) -> str:
    """Replace lines between marker comments (inclusive boundaries preserved)."""
    lines = source_text.splitlines()
    start_idx = -1
    end_idx = -1

    for idx, line in enumerate(lines):
        if start_idx < 0 and begin_marker in line:
            start_idx = idx
            continue
        if start_idx >= 0 and end_marker in line:
            end_idx = idx
            break

    if start_idx < 0 or end_idx < 0 or end_idx <= start_idx:
        raise ValueError(f"{subject} missing valid markers: {begin_marker} / {end_marker}")

    merged = lines[: start_idx + 1] + list(generated_lines) + lines[end_idx:]
    return "\n".join(merged) + "\n"


def ensure_content(path: Path, desired: str, *, check: bool, stale_files: list[str]) -> None:
    """Write desired content or record stale path when check mode is enabled."""
    current = path.read_text(encoding="utf-8")
    if current == desired:
        return
    if check:
        stale_files.append(str(path))
        return
    path.write_text(desired, encoding="utf-8")


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def utc_now_compact() -> str:
    """Return current UTC timestamp in compact CI-friendly token format."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_json_object(path: Path, *, subject: str) -> dict[str, Any]:
    """Load and validate a JSON object payload from disk."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return payload


def load_labeled_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """Load and validate a JSON object payload with path-aware label errors."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def as_nonempty_text(value: Any, *, field: str) -> str:
    """Read required text-like value with consistent non-empty validation."""
    if value is None:
        raise ValueError(f"{field} must be a non-empty string")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def as_required_nonempty_str_with_path(value: Any, *, field_path: str) -> str:
    """Read required string field with explicit path-aware errors."""
    if not isinstance(value, str):
        raise ValueError(f"invalid {field_path}: expected string")
    text = value.strip()
    if not text:
        raise ValueError(f"invalid {field_path}: empty string")
    return text


def as_required_unique_str_list_with_paths(payload: Mapping[str, Any], *, key: str) -> list[str]:
    """Read required list field as strict, unique strings with indexed path errors."""
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list")
    if not raw:
        raise ValueError(f"{key} must include at least one string value")

    values: list[str] = []
    seen_indices: dict[str, int] = {}
    for idx, item in enumerate(raw):
        if not isinstance(item, str):
            raise ValueError(f"invalid {key}[{idx}]: expected string")

        value = item.strip()
        if not value:
            raise ValueError(f"invalid {key}[{idx}]: empty string")

        previous_idx = seen_indices.get(value)
        if previous_idx is not None:
            raise ValueError(
                f"duplicate {key}[{idx}]: {value!r} "
                f"(already used at {key}[{previous_idx}])"
            )
        seen_indices[value] = idx
        values.append(value)

    return values
