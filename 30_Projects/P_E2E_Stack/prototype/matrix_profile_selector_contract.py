#!/usr/bin/env python3
"""Shared matrix profile selector error templates."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ci_input_parsing import parse_csv_items


def matrix_profile_id_argument_empty_error(index: int) -> str:
    return f"MATRIX_PROFILE_IDS must include a non-empty profile id for each --profile-id value: profile-id[{index}]"


def matrix_profile_ids_empty_error() -> str:
    return "MATRIX_PROFILE_IDS must include at least one non-empty profile id when provided"


def matrix_profile_ids_duplicates_error(duplicate_ids: Sequence[str]) -> str:
    return f"MATRIX_PROFILE_IDS contains duplicate entries: {', '.join(duplicate_ids)}"


def collect_unique_profile_ids(values: Iterable[str]) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    duplicates_seen: set[str] = set()
    for raw in values:
        profile_id = str(raw).strip()
        if not profile_id:
            continue
        if profile_id in seen:
            if profile_id not in duplicates_seen:
                duplicates.append(profile_id)
                duplicates_seen.add(profile_id)
            continue
        seen.add(profile_id)
        selected.append(profile_id)
    return selected, duplicates


def resolve_selected_profile_ids(profile_id_values: Iterable[str], profile_ids_csv: str) -> list[str]:
    candidate_ids: list[str] = []

    for idx, raw in enumerate(profile_id_values):
        profile_id = str(raw).strip()
        if not profile_id:
            raise ValueError(matrix_profile_id_argument_empty_error(idx))
        candidate_ids.append(profile_id)

    raw_csv = str(profile_ids_csv)
    parsed_csv_ids = parse_csv_items(raw_csv)
    if raw_csv.strip() and not parsed_csv_ids:
        raise ValueError(matrix_profile_ids_empty_error())

    candidate_ids.extend(parsed_csv_ids)
    selected, duplicates = collect_unique_profile_ids(candidate_ids)
    if duplicates:
        raise ValueError(matrix_profile_ids_duplicates_error(duplicates))
    return selected
