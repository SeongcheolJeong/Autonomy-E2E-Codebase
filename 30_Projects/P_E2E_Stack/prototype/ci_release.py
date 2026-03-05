#!/usr/bin/env python3
"""Shared CI release identifier resolution helpers."""

from __future__ import annotations


def resolve_release_value(
    *,
    explicit_value: str,
    release_id_input: str,
    fallback_prefix: str,
    fallback_run_id: str,
    fallback_run_attempt: str,
    required_field: str,
) -> str:
    primary = str(explicit_value).strip()
    if primary:
        return primary

    input_release_id = str(release_id_input).strip()
    if input_release_id:
        return input_release_id

    prefix = str(fallback_prefix).strip()
    run_id = str(fallback_run_id).strip()
    run_attempt = str(fallback_run_attempt).strip()
    if prefix and run_id and run_attempt:
        return f"{prefix}_{run_id}_{run_attempt}"

    raise ValueError(
        f"{required_field} is required (provide {required_field}, release-id-input, "
        "or fallback prefix/run-id/run-attempt)"
    )
