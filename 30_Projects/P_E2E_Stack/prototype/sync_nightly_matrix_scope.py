#!/usr/bin/env python3
"""Sync nightly matrix artifacts from one scope config."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_scope_schema import validate_nightly_profile
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import (
    add_check_flag,
    as_required_nonempty_str_with_path,
    ensure_content,
    load_json_object,
    replace_marker_block,
    resolve_optional_path,
    resolve_sync_script_roots,
)


README_BEGIN_MARKER = "<!-- BEGIN AUTO-GENERATED NIGHTLY MATRIX PROFILES -->"
README_END_MARKER = "<!-- END AUTO-GENERATED NIGHTLY MATRIX PROFILES -->"
WORKFLOW_BEGIN_MARKER = "# BEGIN AUTO-GENERATED NIGHTLY MATRIX PROFILE FILE"
WORKFLOW_END_MARKER = "# END AUTO-GENERATED NIGHTLY MATRIX PROFILE FILE"


@dataclass(frozen=True)
class MatrixProfile:
    profile_id: str
    default_batch_spec: str
    default_sds_versions: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync nightly matrix profiles, README section, and workflow default")
    add_check_flag(parser)
    parser.add_argument("--scope-file", default="", help="Nightly matrix scope JSON path")
    parser.add_argument("--profiles-file", default="", help="Generated nightly matrix profiles JSON path")
    parser.add_argument("--readme-file", default="", help="README file path with nightly profile markers")
    parser.add_argument("--workflow-file", default="", help="Nightly workflow YAML path with profile file markers")
    parser.add_argument(
        "--workflow-profile-file-default",
        default="",
        help="Override workflow default value for matrix_profile_file input",
    )
    return parser.parse_args()


def load_profiles(scope_file: Path) -> list[MatrixProfile]:
    payload = load_json_object(scope_file, subject="scope config")

    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ValueError("scope config must include a profiles list")

    profiles: list[MatrixProfile] = []
    seen_profile_indices: dict[str, int] = {}
    for idx, raw in enumerate(raw_profiles):
        if not isinstance(raw, dict):
            raise ValueError(f"invalid profiles[{idx}]: expected object")
        profile_id = as_required_nonempty_str_with_path(
            raw.get("profile_id"),
            field_path=f"profiles[{idx}].profile_id",
        )
        previous_idx = seen_profile_indices.get(profile_id)
        if previous_idx is not None:
            raise ValueError(
                f"duplicate profiles[{idx}].profile_id: {profile_id!r} "
                f"(already used at profiles[{previous_idx}].profile_id)"
            )
        seen_profile_indices[profile_id] = idx
        profile = MatrixProfile(
            profile_id=profile_id,
            default_batch_spec=as_required_nonempty_str_with_path(
                raw.get("default_batch_spec"),
                field_path=f"profiles[{idx}].default_batch_spec",
            ),
            default_sds_versions=as_required_nonempty_str_with_path(
                raw.get("default_sds_versions"),
                field_path=f"profiles[{idx}].default_sds_versions",
            ),
        )
        validate_nightly_profile(
            profile_id=profile.profile_id,
            default_batch_spec=profile.default_batch_spec,
            default_sds_versions=profile.default_sds_versions,
            path_prefix=f"profiles[{idx}]",
        )
        profiles.append(profile)

    if not profiles:
        raise ValueError("scope config must include at least one profile")
    return profiles


def render_profiles_json(profiles: list[MatrixProfile]) -> str:
    payload = {
        "profiles": [
            {
                "profile_id": item.profile_id,
                "default_batch_spec": item.default_batch_spec,
                "default_sds_versions": item.default_sds_versions,
            }
            for item in profiles
        ]
    }
    return json.dumps(payload, ensure_ascii=True, indent=2) + "\n"


def render_readme_lines(profiles: list[MatrixProfile]) -> list[str]:
    return [
        f"- `{item.profile_id}` -> `{item.default_batch_spec}`"
        for item in profiles
    ]


def render_readme_with_generated_section(readme_text: str, profiles: list[MatrixProfile]) -> str:
    return replace_marker_block(
        readme_text,
        begin_marker=README_BEGIN_MARKER,
        end_marker=README_END_MARKER,
        generated_lines=render_readme_lines(profiles),
        subject="README",
    )


def render_workflow_with_generated_profile_file_default(
    workflow_text: str,
    profile_file_default: str,
) -> str:
    generated = [f'        default: "{profile_file_default}"']
    return replace_marker_block(
        workflow_text,
        begin_marker=WORKFLOW_BEGIN_MARKER,
        end_marker=WORKFLOW_END_MARKER,
        generated_lines=generated,
        subject="workflow",
    )


def main() -> int:
    args = parse_args()
    prototype_dir, repo_root = resolve_sync_script_roots(__file__)

    scope_file = resolve_optional_path(
        args.scope_file,
        default_path=prototype_dir / "ci_profiles" / "nightly_matrix_scope.json",
    )
    profiles_file = resolve_optional_path(
        args.profiles_file,
        default_path=prototype_dir / "ci_profiles" / "nightly_matrix_profiles.json",
    )
    readme_file = resolve_optional_path(
        args.readme_file,
        default_path=prototype_dir / "README.md",
    )
    workflow_file = resolve_optional_path(
        args.workflow_file,
        default_path=repo_root / ".github" / "workflows" / "e2e-nightly.yml",
    )

    profiles = load_profiles(scope_file)
    if args.workflow_profile_file_default:
        workflow_profile_file_default = args.workflow_profile_file_default.strip()
    else:
        try:
            workflow_profile_file_default = profiles_file.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(
                "--workflow-profile-file-default is required when profiles file is outside repository root"
            ) from exc

    stale_files: list[str] = []
    ensure_content(profiles_file, render_profiles_json(profiles), check=args.check, stale_files=stale_files)
    readme_text = readme_file.read_text(encoding="utf-8")
    ensure_content(
        readme_file,
        render_readme_with_generated_section(readme_text, profiles),
        check=args.check,
        stale_files=stale_files,
    )
    workflow_text = workflow_file.read_text(encoding="utf-8")
    ensure_content(
        workflow_file,
        render_workflow_with_generated_profile_file_default(workflow_text, workflow_profile_file_default),
        check=args.check,
        stale_files=stale_files,
    )

    if stale_files:
        raise ValueError(f"out-of-sync generated files: {', '.join(stale_files)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="sync_nightly_matrix_scope.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
