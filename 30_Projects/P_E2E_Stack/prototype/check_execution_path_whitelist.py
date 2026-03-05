#!/usr/bin/env python3
"""Fail when execution-path references escape the declared project whitelist."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import (
    as_required_unique_str_list_with_paths,
    load_json_object,
    resolve_optional_path,
    resolve_repo_root,
)


_PROJECT_RE = re.compile(r"^P_[A-Za-z0-9._-]+$")
_CANONICAL_RE = re.compile(r"30_Projects/(?P<project>[A-Za-z0-9._-]+)/prototype(?:/[A-Za-z0-9._/@%+=:,-]*)?")
_RELATIVE_RE = re.compile(r"(?:\.\./)+(?P<project>P_[A-Za-z0-9._-]+)/prototype(?:/[A-Za-z0-9._/@%+=:,-]*)?")
_MAX_VIOLATION_RENDER = 20


@dataclass(frozen=True)
class ScopeConfig:
    allowed_projects: list[str]
    scan_globs: list[str]


@dataclass(frozen=True)
class PathReference:
    file_path: Path
    line_no: int
    column_no: int
    matched_text: str
    project: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check execution-path references against allowed project whitelist")
    parser.add_argument("--scope-file", default="", help="Execution-path scope JSON file")
    parser.add_argument("--repo-root", default="", help="Repository root (defaults to inferred root)")
    return parser.parse_args()


def load_scope(scope_file: Path) -> ScopeConfig:
    payload = load_json_object(scope_file, subject="execution path scope")
    allowed_projects = as_required_unique_str_list_with_paths(payload, key="allowed_projects")
    for idx, project in enumerate(allowed_projects):
        if not _PROJECT_RE.match(project):
            raise ValueError(f"invalid allowed_projects[{idx}]: {project!r}")
    scan_globs = as_required_unique_str_list_with_paths(payload, key="scan_globs")
    return ScopeConfig(allowed_projects=allowed_projects, scan_globs=scan_globs)


def collect_scan_files(*, repo_root: Path, scan_globs: Iterable[str]) -> list[Path]:
    files: dict[str, Path] = {}
    for pattern in scan_globs:
        for candidate in repo_root.glob(pattern):
            if candidate.is_file():
                files[candidate.resolve().as_posix()] = candidate.resolve()
    if not files:
        raise ValueError("scan_globs matched no files")
    return [files[key] for key in sorted(files.keys())]


def collect_references(*, file_path: Path) -> list[PathReference]:
    references: list[PathReference] = []
    lines = file_path.read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        for matcher in (_CANONICAL_RE, _RELATIVE_RE):
            for match in matcher.finditer(line):
                project = match.group("project") or ""
                references.append(
                    PathReference(
                        file_path=file_path,
                        line_no=line_no,
                        column_no=match.start() + 1,
                        matched_text=match.group(0),
                        project=project,
                    )
                )
    return references


def render_reference(ref: PathReference, *, repo_root: Path) -> str:
    rel_path = ref.file_path.resolve().relative_to(repo_root.resolve()).as_posix()
    return f"{rel_path}:{ref.line_no}:{ref.column_no} -> {ref.matched_text} (project={ref.project})"


def main() -> int:
    args = parse_args()
    script_path = Path(__file__).resolve()
    prototype_dir = script_path.parent
    repo_root = Path(args.repo_root).resolve() if args.repo_root else resolve_repo_root(script_path)
    scope_file = resolve_optional_path(
        args.scope_file,
        default_path=prototype_dir / "ci_profiles" / "execution_path_scope.json",
    )
    scope = load_scope(scope_file)
    scan_files = collect_scan_files(repo_root=repo_root, scan_globs=scope.scan_globs)
    references: list[PathReference] = []
    for file_path in scan_files:
        references.extend(collect_references(file_path=file_path))

    allowed_projects = set(scope.allowed_projects)
    violations = [ref for ref in references if ref.project not in allowed_projects]
    if violations:
        rendered = [
            f"- {render_reference(ref, repo_root=repo_root)}"
            for ref in violations[:_MAX_VIOLATION_RENDER]
        ]
        truncated = len(violations) - len(rendered)
        if truncated > 0:
            rendered.append(f"- ... (+{truncated} more)")
        raise ValueError(
            "disallowed execution-path references found:\n"
            + "\n".join(rendered)
        )

    print(
        "[ok] execution-path whitelist check passed: "
        f"files={len(scan_files)}, references={len(references)}, allowed_projects={len(scope.allowed_projects)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="check_execution_path_whitelist.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
