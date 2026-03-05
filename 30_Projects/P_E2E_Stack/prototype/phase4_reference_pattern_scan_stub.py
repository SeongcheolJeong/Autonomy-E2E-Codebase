#!/usr/bin/env python3
"""Validate Phase-4 reference extraction patterns against a scanned repo index."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_input_parsing import parse_positive_int
from ci_phases import PHASE_RESOLVE_INPUTS
from ci_reporting import emit_ci_error
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import as_nonempty_text

SOURCE_NAME = "phase4_reference_pattern_scan_stub.py"
PHASE4_REFERENCE_PATTERN_SCAN_REPORT_SCHEMA_VERSION_V0 = "phase4_reference_pattern_scan_report_v0"
PHASE4_REFERENCE_INDEX_SCHEMA_VERSION_V0 = "phase4_reference_index_v0"
SECONDARY_REFERENCE_SECTION_HEADING = "## Secondary Module-to-Reference Mapping"
SCAN_MAX_FILE_BYTES = 2 * 1024 * 1024
SCAN_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "out",
}
SCAN_TEXT_FILE_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Phase-4 pattern-to-reference-index coverage")
    parser.add_argument(
        "--reference-map",
        default=str(Path(__file__).resolve().with_name("PHASE4_EXTERNAL_REFERENCE_MAP.md")),
        help="PHASE4_EXTERNAL_REFERENCE_MAP.md path",
    )
    parser.add_argument(
        "--reference-index",
        default=str(Path(__file__).resolve().with_name("PHASE4_REFERENCE_SCAN_INDEX_STUB.json")),
        help="Reference repository scan index JSON path",
    )
    parser.add_argument(
        "--reference-repo-root",
        default="",
        help=(
            "Optional local repository root used to resolve missing reference-index repositories "
            "(supports <owner>/<repo>, <owner>__<repo>, or <repo> paths)"
        ),
    )
    parser.add_argument(
        "--reference-repo-path",
        action="append",
        default=[],
        help="Optional explicit repository mapping in repo_id=path format (repeatable)",
    )
    parser.add_argument(
        "--max-scan-files-per-repo",
        default="2000",
        help="Maximum number of text files scanned per repository when local repo scan is enabled",
    )
    parser.add_argument("--module", action="append", default=[], help="Module name to validate (repeatable)")
    parser.add_argument(
        "--min-coverage-ratio",
        default="1.0",
        help="Minimum required expected-pattern coverage ratio in range [0, 1]",
    )
    parser.add_argument(
        "--secondary-min-coverage-ratio",
        default="",
        help="Optional minimum required secondary expected-pattern coverage ratio in range [0, 1]",
    )
    parser.add_argument("--out", required=True, help="Output pattern scan report JSON path")
    return parser.parse_args()


def _strip_optional_backticks(cell_value: str) -> str:
    value = cell_value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1].strip()
    return value


def _split_patterns(pattern_cell: str) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[;,]", pattern_cell):
        pattern = token.strip()
        if not pattern:
            continue
        normalized = pattern.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        patterns.append(pattern)
    return patterns


def _extract_markdown_section(markdown_text: str, *, heading: str) -> str:
    start = markdown_text.find(heading)
    if start < 0:
        return ""
    remainder = markdown_text[start + len(heading) :]
    next_section_idx = remainder.find("\n## ")
    if next_section_idx < 0:
        return remainder
    return remainder[:next_section_idx]


def _parse_reference_map(reference_map_text: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw_line in reference_map_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 5:
            continue
        module_cell = _strip_optional_backticks(columns[0])
        if not module_cell or module_cell.lower() in {"phase-4 module", "module"}:
            continue
        module = as_nonempty_text(module_cell, field="reference-map module")
        if module in rows:
            raise ValueError(f"duplicate module row found in external reference map: {module}")

        priority = as_nonempty_text(_strip_optional_backticks(columns[1]), field=f"reference-map priority for {module}")
        references_cell = str(columns[2]).strip()
        references = [item.strip() for item in re.findall(r"`([^`]+)`", references_cell) if item.strip()]
        if not references:
            references = [item.strip() for item in references_cell.split(",") if item.strip()]
        if not references:
            raise ValueError(f"external reference map must include at least one primary reference for module: {module}")

        pattern_cell = as_nonempty_text(columns[3], field=f"reference-map pattern to extract for {module}")
        expected_patterns = _split_patterns(pattern_cell)
        if not expected_patterns:
            raise ValueError(f"reference-map pattern to extract for {module} must include at least one pattern token")

        local_first_target = as_nonempty_text(columns[4], field=f"reference-map local first target for {module}")
        rows[module] = {
            "priority": priority,
            "primary_references": references,
            "expected_patterns": expected_patterns,
            "local_first_target": local_first_target,
        }
    if not rows:
        raise ValueError("reference-map module rows not found")
    return rows


def _parse_secondary_reference_map(reference_map_text: str) -> dict[str, dict[str, Any]]:
    section_text = _extract_markdown_section(reference_map_text, heading=SECONDARY_REFERENCE_SECTION_HEADING)
    if not section_text.strip():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) < 3:
            continue
        module_cell = _strip_optional_backticks(columns[0])
        if not module_cell or module_cell.lower() in {"phase-4 module", "module"}:
            continue
        module = as_nonempty_text(module_cell, field="secondary reference-map module")
        if module in rows:
            raise ValueError(f"duplicate module row found in secondary reference map: {module}")

        references_cell = str(columns[1]).strip()
        secondary_references = [item.strip() for item in re.findall(r"`([^`]+)`", references_cell) if item.strip()]
        if not secondary_references:
            secondary_references = [item.strip() for item in references_cell.split(",") if item.strip()]
        if not secondary_references:
            raise ValueError(
                f"secondary reference map must include at least one secondary reference for module: {module}"
            )

        pattern_cell = as_nonempty_text(columns[2], field=f"secondary reference-map pattern to extract for {module}")
        expected_patterns = _split_patterns(pattern_cell)
        if not expected_patterns:
            raise ValueError(
                f"secondary reference-map pattern to extract for {module} must include at least one pattern token"
            )
        rows[module] = {
            "secondary_references": secondary_references,
            "expected_patterns": expected_patterns,
        }
    return rows


def _normalize_pattern_token(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _patterns_match(*, expected: str, observed: str) -> bool:
    expected_norm = _normalize_pattern_token(expected)
    observed_norm = _normalize_pattern_token(observed)
    if not expected_norm or not observed_norm:
        return False
    return expected_norm in observed_norm or observed_norm in expected_norm


def _parse_reference_index(index_path: Path) -> tuple[str, dict[str, list[str]]]:
    payload_raw = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(payload_raw, dict):
        raise ValueError(f"reference index must be a JSON object: {index_path}")

    schema_version = as_nonempty_text(
        payload_raw.get("phase4_reference_index_schema_version"),
        field="reference index schema version",
    )
    repositories_raw = payload_raw.get("repositories")
    if not isinstance(repositories_raw, list):
        raise ValueError("reference index repositories must be a list")

    repository_patterns: dict[str, list[str]] = {}
    for idx, item in enumerate(repositories_raw):
        if not isinstance(item, dict):
            raise ValueError(f"reference index repositories[{idx}] must be an object")
        repository = as_nonempty_text(item.get("repository"), field=f"reference index repository[{idx}]")
        observed_patterns_raw = item.get("observed_patterns")
        if not isinstance(observed_patterns_raw, list):
            raise ValueError(f"reference index observed_patterns must be a list for repository: {repository}")
        observed_patterns = [str(pattern).strip() for pattern in observed_patterns_raw if str(pattern).strip()]
        if not observed_patterns:
            raise ValueError(f"reference index observed_patterns must include at least one item for repository: {repository}")
        if repository in repository_patterns:
            raise ValueError(f"duplicate repository entry in reference index: {repository}")
        repository_patterns[repository] = observed_patterns

    if not repository_patterns:
        raise ValueError("reference index repositories must include at least one repository")
    return schema_version, repository_patterns


def _parse_reference_repo_paths(values: list[str]) -> dict[str, Path]:
    repo_paths: dict[str, Path] = {}
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        repo_raw, sep, path_raw = value.partition("=")
        if not sep:
            raise ValueError(f"reference-repo-path must be repo_id=path: {value}")
        repo_id = repo_raw.strip()
        path_value = path_raw.strip()
        if not repo_id:
            raise ValueError(f"reference-repo-path has empty repo_id: {value}")
        if not path_value:
            raise ValueError(f"reference-repo-path has empty path: {value}")
        if repo_id in repo_paths:
            raise ValueError(f"reference-repo-path contains duplicate repo_id: {repo_id}")
        path = Path(path_value).resolve()
        if not path.exists():
            raise FileNotFoundError(f"reference-repo-path not found for {repo_id}: {path}")
        if not path.is_dir():
            raise ValueError(f"reference-repo-path must point to a directory for {repo_id}: {path}")
        repo_paths[repo_id] = path
    return repo_paths


def _collect_expected_patterns_by_repository(
    *,
    reference_rows: dict[str, dict[str, Any]],
    modules: list[str],
    references_key: str,
) -> dict[str, list[str]]:
    expected_by_repo: dict[str, list[str]] = {}
    seen_by_repo: dict[str, set[str]] = {}
    for module in modules:
        row = reference_rows.get(module)
        if row is None:
            continue
        repository_references = list(row[references_key])
        expected_patterns = list(row["expected_patterns"])
        for repository in repository_references:
            if repository not in expected_by_repo:
                expected_by_repo[repository] = []
                seen_by_repo[repository] = set()
            repo_expected = expected_by_repo[repository]
            repo_seen = seen_by_repo[repository]
            for pattern in expected_patterns:
                if pattern in repo_seen:
                    continue
                repo_expected.append(pattern)
                repo_seen.add(pattern)
    return expected_by_repo


def _resolve_repository_snapshot_path(
    *,
    repository: str,
    repo_root: Path | None,
    explicit_repo_paths: dict[str, Path],
) -> Path | None:
    direct = explicit_repo_paths.get(repository)
    if direct is not None:
        return direct
    if repo_root is None:
        return None

    repo_parts = [part for part in repository.split("/") if part.strip()]
    if not repo_parts:
        return None
    owner = repo_parts[-2] if len(repo_parts) >= 2 else ""
    repo_name = repo_parts[-1]
    candidates: list[Path] = [
        repo_root.joinpath(*repo_parts),
        repo_root / f"{owner}__{repo_name}" if owner else repo_root / repo_name,
        repo_root / repo_name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def _iter_repository_text_files(repo_path: Path, *, max_files: int) -> list[Path]:
    files: list[Path] = []
    for root, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [name for name in dirnames if name not in SCAN_SKIP_DIRS]
        for filename in filenames:
            file_path = Path(root) / filename
            if file_path.suffix.lower() not in SCAN_TEXT_FILE_SUFFIXES:
                continue
            try:
                if file_path.stat().st_size > SCAN_MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(file_path)
            if len(files) >= max_files:
                return files
    return files


def _scan_repository_expected_patterns(repo_path: Path, expected_patterns: list[str], *, max_files: int) -> list[str]:
    remaining: list[tuple[str, str]] = []
    for pattern in expected_patterns:
        normalized = _normalize_pattern_token(pattern)
        if normalized:
            remaining.append((pattern, normalized))
    if not remaining:
        return []

    matched: list[str] = []
    for file_path in _iter_repository_text_files(repo_path, max_files=max_files):
        if not remaining:
            break
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        normalized_content = _normalize_pattern_token(content)
        if not normalized_content:
            continue
        next_remaining: list[tuple[str, str]] = []
        for pattern, normalized_pattern in remaining:
            if normalized_pattern in normalized_content:
                matched.append(pattern)
            else:
                next_remaining.append((pattern, normalized_pattern))
        remaining = next_remaining
    return matched


def _augment_repository_patterns_from_local_repos(
    *,
    repository_patterns: dict[str, list[str]],
    reference_rows: dict[str, dict[str, Any]],
    modules: list[str],
    references_key: str,
    repo_root: Path | None,
    explicit_repo_paths: dict[str, Path],
    max_scan_files_per_repo: int,
) -> dict[str, str]:
    expected_by_repo = _collect_expected_patterns_by_repository(
        reference_rows=reference_rows,
        modules=modules,
        references_key=references_key,
    )
    resolved_paths: dict[str, str] = {}
    for repository, expected_patterns in expected_by_repo.items():
        if repository in repository_patterns:
            continue
        repo_path = _resolve_repository_snapshot_path(
            repository=repository,
            repo_root=repo_root,
            explicit_repo_paths=explicit_repo_paths,
        )
        if repo_path is None:
            continue
        repository_patterns[repository] = _scan_repository_expected_patterns(
            repo_path,
            expected_patterns,
            max_files=max_scan_files_per_repo,
        )
        resolved_paths[repository] = str(repo_path)
    return resolved_paths


def _build_repository_observed_pairs(
    *,
    repositories: list[str],
    repository_patterns: dict[str, list[str]],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for repository in repositories:
        for observed_pattern in repository_patterns.get(repository, []):
            pairs.append((repository, observed_pattern))
    return pairs


def _resolve_pattern_coverage(
    *,
    expected_patterns: list[str],
    repository_observed_pairs: list[tuple[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    matched_patterns: list[dict[str, str]] = []
    unmatched_patterns: list[str] = []
    for expected_pattern in expected_patterns:
        match_pair: tuple[str, str] | None = None
        for repository, observed_pattern in repository_observed_pairs:
            if _patterns_match(expected=expected_pattern, observed=observed_pattern):
                match_pair = (repository, observed_pattern)
                break
        if match_pair is None:
            unmatched_patterns.append(expected_pattern)
            continue
        matched_patterns.append(
            {
                "expected_pattern": expected_pattern,
                "matched_repository": match_pair[0],
                "matched_observed_pattern": match_pair[1],
            }
        )
    return matched_patterns, unmatched_patterns


def _resolve_modules(
    values: list[str],
    *,
    reference_map_rows: dict[str, dict[str, Any]],
) -> list[str]:
    if not values:
        return list(reference_map_rows.keys())

    modules: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        module = str(value).strip().lower()
        if not module:
            continue
        if module in seen and module not in duplicates:
            duplicates.append(module)
        seen.add(module)
        modules.append(module)

    if duplicates:
        raise ValueError(f"module contains duplicate entries: {', '.join(duplicates)}")
    unknown_modules = [module for module in modules if module not in reference_map_rows]
    if unknown_modules:
        allowed = ", ".join(reference_map_rows.keys())
        raise ValueError(f"module must be one of: {allowed}; got: {', '.join(unknown_modules)}")
    return modules


def _resolve_min_coverage_ratio(token: str) -> float:
    text = as_nonempty_text(token, field="min-coverage-ratio")
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"min-coverage-ratio must be a number, got: {text}") from exc
    if value < 0.0 or value > 1.0:
        raise ValueError(f"min-coverage-ratio must be between 0 and 1: {text}")
    return value


def _resolve_optional_secondary_min_coverage_ratio(token: str) -> float | None:
    text = str(token).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"secondary-min-coverage-ratio must be a number, got: {text}") from exc
    if value < 0.0 or value > 1.0:
        raise ValueError(f"secondary-min-coverage-ratio must be between 0 and 1: {text}")
    return value


def main() -> int:
    try:
        args = parse_args()
        reference_map_path = Path(args.reference_map).resolve()
        reference_index_path = Path(args.reference_index).resolve()
        max_scan_files_per_repo = parse_positive_int(
            str(args.max_scan_files_per_repo),
            default=2000,
            field="max-scan-files-per-repo",
        )
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        min_coverage_ratio = _resolve_min_coverage_ratio(str(args.min_coverage_ratio))
        secondary_min_coverage_ratio = _resolve_optional_secondary_min_coverage_ratio(
            str(args.secondary_min_coverage_ratio)
        )
        explicit_repo_paths = _parse_reference_repo_paths(list(args.reference_repo_path))
        reference_repo_root_text = str(args.reference_repo_root).strip()
        reference_repo_root = Path(reference_repo_root_text).resolve() if reference_repo_root_text else None
        if reference_repo_root is not None:
            if not reference_repo_root.exists():
                raise FileNotFoundError(f"reference-repo-root not found: {reference_repo_root}")
            if not reference_repo_root.is_dir():
                raise ValueError(f"reference-repo-root must be a directory: {reference_repo_root}")

        reference_map_text = reference_map_path.read_text(encoding="utf-8")
        reference_map_rows = _parse_reference_map(reference_map_text)
        secondary_reference_rows = _parse_secondary_reference_map(reference_map_text)
        modules = _resolve_modules(args.module, reference_map_rows=reference_map_rows)
        if reference_index_path.exists():
            index_schema_version, repository_patterns = _parse_reference_index(reference_index_path)
        else:
            if reference_repo_root is None and not explicit_repo_paths:
                raise FileNotFoundError(f"reference index not found: {reference_index_path}")
            index_schema_version = PHASE4_REFERENCE_INDEX_SCHEMA_VERSION_V0
            repository_patterns = {}

        resolved_repo_paths = _augment_repository_patterns_from_local_repos(
            repository_patterns=repository_patterns,
            reference_rows=reference_map_rows,
            modules=modules,
            references_key="primary_references",
            repo_root=reference_repo_root,
            explicit_repo_paths=explicit_repo_paths,
            max_scan_files_per_repo=max_scan_files_per_repo,
        )
        secondary_resolved_repo_paths = _augment_repository_patterns_from_local_repos(
            repository_patterns=repository_patterns,
            reference_rows=secondary_reference_rows,
            modules=modules,
            references_key="secondary_references",
            repo_root=reference_repo_root,
            explicit_repo_paths=explicit_repo_paths,
            max_scan_files_per_repo=max_scan_files_per_repo,
        )
        resolved_repo_paths.update(secondary_resolved_repo_paths)

        module_rows: list[dict[str, Any]] = []
        for module in modules:
            row = reference_map_rows[module]
            expected_patterns = list(row["expected_patterns"])
            primary_references = list(row["primary_references"])

            missing_references = [repository for repository in primary_references if repository not in repository_patterns]
            if missing_references:
                raise ValueError(
                    f"reference index missing repositories for module {module}: {', '.join(missing_references)}"
                )

            repository_observed_pairs = _build_repository_observed_pairs(
                repositories=primary_references,
                repository_patterns=repository_patterns,
            )
            matched_patterns, unmatched_patterns = _resolve_pattern_coverage(
                expected_patterns=expected_patterns,
                repository_observed_pairs=repository_observed_pairs,
            )

            expected_pattern_count = len(expected_patterns)
            matched_pattern_count = len(matched_patterns)
            coverage_ratio = float(matched_pattern_count / expected_pattern_count) if expected_pattern_count else 0.0
            if coverage_ratio < min_coverage_ratio:
                raise ValueError(
                    "reference pattern coverage below threshold for module "
                    f"{module}: matched={matched_pattern_count}/{expected_pattern_count} "
                    f"coverage={coverage_ratio:.3f} min={min_coverage_ratio:.3f} "
                    f"missing={', '.join(unmatched_patterns)}"
                )

            secondary_references: list[str] = []
            secondary_expected_patterns: list[str] = []
            secondary_matched_patterns: list[dict[str, str]] = []
            secondary_unmatched_patterns: list[str] = []
            secondary_missing_repositories: list[str] = []
            secondary_coverage_ratio = 0.0
            secondary_row = secondary_reference_rows.get(module)
            if secondary_row is not None:
                secondary_references = list(secondary_row["secondary_references"])
                secondary_expected_patterns = list(secondary_row["expected_patterns"])
                secondary_missing_repositories = [
                    repository for repository in secondary_references if repository not in repository_patterns
                ]
                secondary_repository_observed_pairs = _build_repository_observed_pairs(
                    repositories=[repository for repository in secondary_references if repository in repository_patterns],
                    repository_patterns=repository_patterns,
                )
                secondary_matched_patterns, secondary_unmatched_patterns = _resolve_pattern_coverage(
                    expected_patterns=secondary_expected_patterns,
                    repository_observed_pairs=secondary_repository_observed_pairs,
                )
                secondary_expected_pattern_count = len(secondary_expected_patterns)
                secondary_matched_pattern_count = len(secondary_matched_patterns)
                secondary_coverage_ratio = (
                    float(secondary_matched_pattern_count / secondary_expected_pattern_count)
                    if secondary_expected_pattern_count
                    else 0.0
                )
                if (
                    secondary_min_coverage_ratio is not None
                    and secondary_expected_pattern_count > 0
                    and secondary_coverage_ratio < secondary_min_coverage_ratio
                ):
                    raise ValueError(
                        "secondary reference pattern coverage below threshold for module "
                        f"{module}: matched={secondary_matched_pattern_count}/{secondary_expected_pattern_count} "
                        f"coverage={secondary_coverage_ratio:.3f} min={secondary_min_coverage_ratio:.3f} "
                        f"missing={', '.join(secondary_unmatched_patterns)}"
                    )
            else:
                secondary_expected_pattern_count = 0
                secondary_matched_pattern_count = 0

            module_rows.append(
                {
                    "module": module,
                    "priority": row["priority"],
                    "local_first_target": row["local_first_target"],
                    "primary_references": primary_references,
                    "reference_repository_count": len(primary_references),
                    "expected_patterns": expected_patterns,
                    "expected_pattern_count": expected_pattern_count,
                    "matched_patterns": matched_patterns,
                    "matched_pattern_count": matched_pattern_count,
                    "unmatched_patterns": unmatched_patterns,
                    "coverage_ratio": coverage_ratio,
                    "secondary_reference_enabled": secondary_row is not None,
                    "secondary_references": secondary_references,
                    "secondary_reference_repository_count": len(secondary_references),
                    "secondary_expected_patterns": secondary_expected_patterns,
                    "secondary_expected_pattern_count": secondary_expected_pattern_count,
                    "secondary_matched_patterns": secondary_matched_patterns,
                    "secondary_matched_pattern_count": secondary_matched_pattern_count,
                    "secondary_unmatched_patterns": secondary_unmatched_patterns,
                    "secondary_missing_repositories": secondary_missing_repositories,
                    "secondary_coverage_ratio": secondary_coverage_ratio,
                }
            )
            print(
                "[ok] "
                f"module={module} matched={matched_pattern_count}/{expected_pattern_count} "
                f"coverage={coverage_ratio:.3f}"
            )

        total_expected_pattern_count = sum(int(row["expected_pattern_count"]) for row in module_rows)
        total_matched_pattern_count = sum(int(row["matched_pattern_count"]) for row in module_rows)
        total_coverage_ratio = (
            float(total_matched_pattern_count / total_expected_pattern_count) if total_expected_pattern_count else 0.0
        )
        secondary_module_count = sum(1 for row in module_rows if bool(row["secondary_reference_enabled"]))
        secondary_total_expected_pattern_count = sum(int(row["secondary_expected_pattern_count"]) for row in module_rows)
        secondary_total_matched_pattern_count = sum(int(row["secondary_matched_pattern_count"]) for row in module_rows)
        secondary_total_coverage_ratio = (
            float(secondary_total_matched_pattern_count / secondary_total_expected_pattern_count)
            if secondary_total_expected_pattern_count
            else 0.0
        )
        payload = {
            "phase4_reference_pattern_scan_report_schema_version": PHASE4_REFERENCE_PATTERN_SCAN_REPORT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reference_map_path": str(reference_map_path),
            "reference_index_path": str(reference_index_path),
            "reference_index_schema_version": index_schema_version,
            "reference_repo_root": str(reference_repo_root) if reference_repo_root is not None else "",
            "reference_repo_paths": resolved_repo_paths,
            "reference_repo_scanned": sorted(resolved_repo_paths.keys()),
            "max_scan_files_per_repo": max_scan_files_per_repo,
            "min_coverage_ratio": min_coverage_ratio,
            "secondary_min_coverage_ratio": secondary_min_coverage_ratio,
            "module_count": len(module_rows),
            "total_expected_pattern_count": total_expected_pattern_count,
            "total_matched_pattern_count": total_matched_pattern_count,
            "total_coverage_ratio": total_coverage_ratio,
            "secondary_module_count": secondary_module_count,
            "secondary_total_expected_pattern_count": secondary_total_expected_pattern_count,
            "secondary_total_matched_pattern_count": secondary_total_matched_pattern_count,
            "secondary_total_coverage_ratio": secondary_total_coverage_ratio,
            "secondary_reference_repo_paths": secondary_resolved_repo_paths,
            "secondary_reference_repo_scanned": sorted(secondary_resolved_repo_paths.keys()),
            "modules": module_rows,
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        step_summary_file = resolve_step_summary_file_from_env()
        if step_summary_file:
            emit_ci_error(
                step_summary_file=step_summary_file,
                source=SOURCE_NAME,
                message=message,
                details={"phase": PHASE_RESOLVE_INPUTS},
            )
        else:
            print(f"[error] {SOURCE_NAME}: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source=SOURCE_NAME,
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
