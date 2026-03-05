#!/usr/bin/env python3
"""Sync PR quick workflow paths and precheck patterns from one scope config."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_scope_schema import validate_pr_quick_scope
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import (
    add_check_flag,
    as_required_unique_str_list_with_paths,
    ensure_content,
    load_json_object,
    replace_marker_block,
    resolve_optional_path,
    resolve_sync_script_roots,
)


BEGIN_MARKER = "# BEGIN AUTO-GENERATED PR QUICK PATHS"
END_MARKER = "# END AUTO-GENERATED PR QUICK PATHS"


@dataclass(frozen=True)
class ScopeConfig:
    workflow_globs: list[str]
    runtime_roots: list[str]
    runtime_extensions: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync PR quick workflow paths and precheck rules")
    add_check_flag(parser)
    parser.add_argument("--scope-file", default="", help="Scope JSON file path")
    parser.add_argument("--rules-file", default="", help="Generated precheck rules JSON file path")
    parser.add_argument("--workflow-file", default="", help="PR quick workflow YAML file path")
    return parser.parse_args()


def load_scope_config(scope_file: Path) -> ScopeConfig:
    payload = load_json_object(scope_file, subject="scope config")
    config = ScopeConfig(
        workflow_globs=as_required_unique_str_list_with_paths(payload, key="workflow_globs"),
        runtime_roots=as_required_unique_str_list_with_paths(payload, key="runtime_roots"),
        runtime_extensions=as_required_unique_str_list_with_paths(payload, key="runtime_extensions"),
    )
    validate_pr_quick_scope(config.workflow_globs, config.runtime_roots, config.runtime_extensions)
    return config


def glob_to_regex(glob_value: str) -> str:
    escaped = re.escape(glob_value)
    escaped = escaped.replace(r"\*", ".*")
    escaped = escaped.replace(r"\?", ".")
    escaped = escaped.replace(r"\-", "-")
    return f"^{escaped}$"


def build_workflow_paths(scope: ScopeConfig) -> list[str]:
    return [*scope.workflow_globs, *(f"{root}/**" for root in scope.runtime_roots)]


def build_precheck_patterns(scope: ScopeConfig) -> list[str]:
    ext_group = "|".join(re.escape(ext) for ext in scope.runtime_extensions)
    patterns = [glob_to_regex(item) for item in scope.workflow_globs]
    for root in scope.runtime_roots:
        root_escaped = re.escape(root).replace(r"\/", "/").replace(r"\-", "-")
        patterns.append(f"^{root_escaped}/.*\\.({ext_group})$")
    return patterns


def render_rules_json(patterns: list[str]) -> str:
    return json.dumps({"include_patterns": patterns}, ensure_ascii=True, indent=2) + "\n"


def render_workflow_with_generated_paths(workflow_text: str, workflow_paths: list[str]) -> str:
    generated = [f"      - '{item}'" for item in workflow_paths]
    return replace_marker_block(
        workflow_text,
        begin_marker=BEGIN_MARKER,
        end_marker=END_MARKER,
        generated_lines=generated,
        subject="workflow",
    )


def main() -> int:
    args = parse_args()
    prototype_dir, repo_root = resolve_sync_script_roots(__file__)

    scope_file = resolve_optional_path(
        args.scope_file,
        default_path=prototype_dir / "ci_profiles" / "pr_quick_scope.json",
    )
    rules_file = resolve_optional_path(
        args.rules_file,
        default_path=prototype_dir / "ci_profiles" / "pr_quick_precheck_rules.json",
    )
    workflow_file = resolve_optional_path(
        args.workflow_file,
        default_path=repo_root / ".github" / "workflows" / "e2e-pr-quick.yml",
    )

    scope = load_scope_config(scope_file)
    workflow_paths = build_workflow_paths(scope)
    patterns = build_precheck_patterns(scope)

    stale_files: list[str] = []
    ensure_content(rules_file, render_rules_json(patterns), check=args.check, stale_files=stale_files)

    workflow_text = workflow_file.read_text(encoding="utf-8")
    rendered_workflow = render_workflow_with_generated_paths(workflow_text, workflow_paths)
    ensure_content(workflow_file, rendered_workflow, check=args.check, stale_files=stale_files)

    if stale_files:
        raise ValueError(f"out-of-sync generated files: {', '.join(stale_files)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="sync_pr_quick_scope.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
