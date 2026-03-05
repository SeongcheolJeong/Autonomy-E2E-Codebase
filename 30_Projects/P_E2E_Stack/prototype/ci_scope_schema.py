#!/usr/bin/env python3
"""Schema-style validation helpers for CI scope JSON configs."""

from __future__ import annotations

import re
from typing import Sequence


_PR_WORKFLOW_GLOB_RE = re.compile(r"^\.github/workflows/[A-Za-z0-9._*?-]+\.ya?ml$")
_PR_RUNTIME_ROOT_RE = re.compile(r"^30_Projects/[A-Za-z0-9._-]+/prototype$")
_PR_EXTENSION_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")

_NIGHTLY_PROFILE_ID_RE = re.compile(r"^[a-z0-9_]+$")
_NIGHTLY_BATCH_SPEC_RE = re.compile(r"^30_Projects/[A-Za-z0-9._-]+/prototype/.+\.json$")
_NIGHTLY_VERSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _field_path(prefix: str, field: str) -> str:
    if not prefix:
        return field
    return f"{prefix}.{field}"


def validate_pr_quick_scope(
    workflow_globs: Sequence[str],
    runtime_roots: Sequence[str],
    runtime_extensions: Sequence[str],
    *,
    path_prefix: str = "",
) -> None:
    workflow_globs_path = _field_path(path_prefix, "workflow_globs")
    runtime_roots_path = _field_path(path_prefix, "runtime_roots")
    runtime_extensions_path = _field_path(path_prefix, "runtime_extensions")

    for idx, value in enumerate(workflow_globs):
        if not _PR_WORKFLOW_GLOB_RE.match(value):
            raise ValueError(f"invalid {workflow_globs_path}[{idx}]: {value!r}")

    for idx, value in enumerate(runtime_roots):
        if not _PR_RUNTIME_ROOT_RE.match(value):
            raise ValueError(f"invalid {runtime_roots_path}[{idx}]: {value!r}")

    for idx, value in enumerate(runtime_extensions):
        if not _PR_EXTENSION_RE.match(value):
            raise ValueError(f"invalid {runtime_extensions_path}[{idx}]: {value!r}")


def validate_nightly_profile(
    profile_id: str,
    default_batch_spec: str,
    default_sds_versions: str,
    *,
    path_prefix: str = "",
) -> None:
    profile_id_path = _field_path(path_prefix, "profile_id")
    batch_spec_path = _field_path(path_prefix, "default_batch_spec")
    versions_path = _field_path(path_prefix, "default_sds_versions")

    if not _NIGHTLY_PROFILE_ID_RE.match(profile_id):
        raise ValueError(f"invalid {profile_id_path}: {profile_id!r}")

    if not _NIGHTLY_BATCH_SPEC_RE.match(default_batch_spec):
        raise ValueError(f"invalid {batch_spec_path}: {default_batch_spec!r}")

    tokens = [item.strip() for item in default_sds_versions.split(",") if item.strip()]
    if not tokens:
        raise ValueError(f"{versions_path} must include at least one token")
    for idx, token in enumerate(tokens):
        if not _NIGHTLY_VERSION_TOKEN_RE.match(token):
            raise ValueError(f"invalid {versions_path}[{idx}]: {token!r}")
