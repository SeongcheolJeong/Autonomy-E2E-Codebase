#!/usr/bin/env python3
"""Shared Phase-4 linkage module normalization/validation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


PHASE4_LINKAGE_ALLOWED_MODULES = ("adp", "copilot")
PHASE4_LINKAGE_ALLOWED_MODULES_CSV = ",".join(PHASE4_LINKAGE_ALLOWED_MODULES)
PHASE4_LINKAGE_ALLOWED_MODULES_TEXT = ", ".join(PHASE4_LINKAGE_ALLOWED_MODULES)
PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES = ("hil_sim", "adp", "copilot")
PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_CSV = ",".join(PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES)
PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT = ", ".join(PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES)


def _normalize_modules(
    values: Iterable[str], *, default_to_allowed_when_empty: bool, allowed_modules: Sequence[str]
) -> list[str]:
    modules: list[str] = []
    for value in values:
        module = str(value).strip().lower()
        if module:
            modules.append(module)
    if not modules and default_to_allowed_when_empty:
        return list(allowed_modules)
    return modules


def normalize_phase4_linkage_modules(
    values: Iterable[str], *, default_to_allowed_when_empty: bool
) -> list[str]:
    return _normalize_modules(
        values,
        default_to_allowed_when_empty=default_to_allowed_when_empty,
        allowed_modules=PHASE4_LINKAGE_ALLOWED_MODULES,
    )


def _analyze_modules(modules: Sequence[str], *, allowed_modules: Sequence[str]) -> tuple[list[str], list[str]]:
    unknown: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    unknown_seen: set[str] = set()
    duplicate_seen: set[str] = set()
    for module in modules:
        if module not in allowed_modules and module not in unknown_seen:
            unknown.append(module)
            unknown_seen.add(module)
        if module in seen and module not in duplicate_seen:
            duplicates.append(module)
            duplicate_seen.add(module)
        seen.add(module)
    return unknown, duplicates


def _analyze_phase4_linkage_modules(modules: Sequence[str]) -> tuple[list[str], list[str]]:
    return _analyze_modules(modules, allowed_modules=PHASE4_LINKAGE_ALLOWED_MODULES)


def find_phase4_linkage_unknown_modules(modules: Sequence[str]) -> list[str]:
    unknown, _ = _analyze_phase4_linkage_modules(modules)
    return unknown


def find_phase4_linkage_duplicate_modules(modules: Sequence[str]) -> list[str]:
    _, duplicates = _analyze_phase4_linkage_modules(modules)
    return duplicates


def format_phase4_linkage_unknown_modules_error(unknown_modules: Sequence[str]) -> str:
    return (
        "phase4-linkage-module must be one of: "
        f"{PHASE4_LINKAGE_ALLOWED_MODULES_TEXT}; got: {', '.join(unknown_modules)}"
    )


def format_phase4_linkage_duplicate_modules_error(duplicate_modules: Sequence[str]) -> str:
    return f"phase4-linkage-module contains duplicate entries: {', '.join(duplicate_modules)}"


def validate_phase4_linkage_modules(modules: Sequence[str]) -> None:
    unknown, duplicates = _analyze_phase4_linkage_modules(modules)
    if unknown:
        raise ValueError(format_phase4_linkage_unknown_modules_error(unknown))
    if duplicates:
        raise ValueError(format_phase4_linkage_duplicate_modules_error(duplicates))


def resolve_phase4_linkage_modules(
    values: Iterable[str], *, default_to_allowed_when_empty: bool
) -> list[str]:
    modules = normalize_phase4_linkage_modules(values, default_to_allowed_when_empty=default_to_allowed_when_empty)
    validate_phase4_linkage_modules(modules)
    return modules


def normalize_phase4_reference_pattern_modules(
    values: Iterable[str], *, default_to_allowed_when_empty: bool
) -> list[str]:
    return _normalize_modules(
        values,
        default_to_allowed_when_empty=default_to_allowed_when_empty,
        allowed_modules=PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES,
    )


def _analyze_phase4_reference_pattern_modules(modules: Sequence[str]) -> tuple[list[str], list[str]]:
    return _analyze_modules(modules, allowed_modules=PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES)


def find_phase4_reference_pattern_unknown_modules(modules: Sequence[str]) -> list[str]:
    unknown, _ = _analyze_phase4_reference_pattern_modules(modules)
    return unknown


def find_phase4_reference_pattern_duplicate_modules(modules: Sequence[str]) -> list[str]:
    _, duplicates = _analyze_phase4_reference_pattern_modules(modules)
    return duplicates


def format_phase4_reference_pattern_unknown_modules_error(unknown_modules: Sequence[str]) -> str:
    return (
        "phase4-reference-pattern-module must be one of: "
        f"{PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT}; got: {', '.join(unknown_modules)}"
    )


def format_phase4_reference_pattern_duplicate_modules_error(duplicate_modules: Sequence[str]) -> str:
    return f"phase4-reference-pattern-module contains duplicate entries: {', '.join(duplicate_modules)}"


def validate_phase4_reference_pattern_modules(modules: Sequence[str]) -> None:
    unknown, duplicates = _analyze_phase4_reference_pattern_modules(modules)
    if unknown:
        raise ValueError(format_phase4_reference_pattern_unknown_modules_error(unknown))
    if duplicates:
        raise ValueError(format_phase4_reference_pattern_duplicate_modules_error(duplicates))


def resolve_phase4_reference_pattern_modules(
    values: Iterable[str], *, default_to_allowed_when_empty: bool
) -> list[str]:
    modules = normalize_phase4_reference_pattern_modules(
        values, default_to_allowed_when_empty=default_to_allowed_when_empty
    )
    validate_phase4_reference_pattern_modules(modules)
    return modules
