#!/usr/bin/env python3
"""Shared input normalization helpers for CI command wrappers."""

from __future__ import annotations

from phase4_linkage_contract import (
    PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES,
    PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT,
)


def parse_bool(raw: str, *, default: bool, field: str) -> bool:
    value = str(raw).strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{field} must be true/false compatible, got: {raw}")


def parse_int(raw: str, *, default: int, field: str, minimum: int | None = None) -> int:
    value = str(raw).strip()
    if not value:
        parsed = default
    else:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer, got: {raw}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed


def parse_non_negative_int(raw: str, *, default: int, field: str) -> int:
    value = str(raw).strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer, got: {raw}") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be >= 0")
    return parsed


def parse_positive_int(raw: str, *, default: int, field: str) -> int:
    value = str(raw).strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer, got: {raw}") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be > 0")
    return parsed


def parse_float(raw: str, *, default: float, field: str, minimum: float = 0.0, maximum: float = 1.0) -> float:
    value = str(raw).strip()
    if not value:
        parsed = default
    else:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be a number, got: {raw}") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be within [{minimum}, {maximum}]")
    return parsed


def parse_positive_float(raw: str, *, default: float, field: str) -> float:
    value = str(raw).strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number, got: {raw}") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be > 0")
    return parsed


def parse_non_negative_float(raw: str, *, default: float, field: str) -> float:
    value = str(raw).strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number, got: {raw}") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be >= 0")
    return parsed


def normalize_enum(*, raw: str, default: str, field: str, allowed: set[str]) -> str:
    value = str(raw).strip().lower()
    if not value:
        return default
    if value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"{field} must be one of: {allowed_text}")
    return value


def resolve_phase4_copilot_mode(
    *,
    phase4_enable_hooks: bool,
    phase4_enable_copilot_hooks: bool,
    phase4_require_done: bool,
    raw_copilot_mode: str,
    copilot_hooks_dependency_error: str,
    require_done_dependency_error: str,
) -> str:
    if phase4_enable_copilot_hooks and not phase4_enable_hooks:
        raise ValueError(copilot_hooks_dependency_error)
    if phase4_require_done and not phase4_enable_hooks:
        raise ValueError(require_done_dependency_error)
    if not phase4_enable_copilot_hooks:
        return str(raw_copilot_mode)
    return normalize_enum(
        raw=str(raw_copilot_mode),
        default="scenario",
        field="copilot-mode",
        allowed={"scenario", "query"},
    )


def parse_csv_items(raw_csv: str) -> list[str]:
    return [item.strip() for item in str(raw_csv).split(",") if item.strip()]


def parse_csv_items_with_fallback(primary_csv: str, fallback_csv: str) -> list[str]:
    source = str(primary_csv).strip()
    if not source:
        source = str(fallback_csv).strip()
    if not source:
        return []
    return parse_csv_items(source)


def parse_csv_pair(raw_csv: str) -> tuple[str, str]:
    items = parse_csv_items(raw_csv)
    if len(items) >= 2:
        return items[0], items[1]
    return "", ""


def parse_phase4_secondary_module_warn_thresholds(raw_value: str, *, field: str) -> dict[str, float]:
    value = str(raw_value).strip()
    if not value:
        return {}
    allowed_modules = set(PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES)
    parsed: dict[str, float] = {}
    for item_raw in value.split(","):
        item = str(item_raw).strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"{field} items must use module=ratio format; got: {item_raw}")
        module_raw, ratio_raw = item.split("=", 1)
        module = str(module_raw).strip().lower()
        if not module:
            raise ValueError(f"{field} module must be non-empty")
        if module not in allowed_modules:
            raise ValueError(
                f"{field} module must be one of: {PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT}; got: {module}"
            )
        if module in parsed:
            raise ValueError(f"{field} contains duplicate module entry: {module}")
        ratio = parse_non_negative_float(
            str(ratio_raw),
            default=0.0,
            field=f"{field}[{module}]",
        )
        if ratio > 1.0:
            raise ValueError(f"{field} ratio must be between 0 and 1; got {ratio_raw} for module {module}")
        parsed[module] = ratio
    return parsed
