#!/usr/bin/env python3
"""Build a minimal synthetic dataset manifest from run/release summaries."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


DATASET_MANIFEST_SCHEMA_VERSION_V0 = "dataset_manifest_v0"
ERROR_SOURCE = "build_dataset_manifest.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build dataset manifest from summary artifacts")
    parser.add_argument(
        "--summary-root",
        action="append",
        default=[],
        help="Root directory to scan for run summary.json (repeatable)",
    )
    parser.add_argument(
        "--summary-file",
        action="append",
        default=[],
        help="Explicit run summary.json path (repeatable)",
    )
    parser.add_argument(
        "--release-summary-root",
        action="append",
        default=[],
        help="Root directory to scan for release *.summary.json (repeatable)",
    )
    parser.add_argument(
        "--release-summary-file",
        action="append",
        default=[],
        help="Explicit release *.summary.json path (repeatable)",
    )
    parser.add_argument("--dataset-id", required=True, help="Dataset identifier")
    parser.add_argument("--out", required=True, help="Output manifest JSON path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _normalize_nonempty_text(value: Any, *, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _collect_paths(roots: list[Path], files: list[Path], pattern: str) -> tuple[list[Path], set[Path]]:
    paths: list[Path] = []
    for root in roots:
        paths.extend(sorted(root.rglob(pattern)))
    paths.extend(files)

    explicit_paths = {path.resolve() for path in files}
    seen: set[Path] = set()
    ordered_paths: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered_paths.append(resolved)
    return ordered_paths, explicit_paths


def _collect_run_summaries(summary_roots: list[Path], summary_files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    paths, explicit_paths = _collect_paths(summary_roots, summary_files, "summary.json")
    for resolved in paths:
        payload = _load_json_object(resolved, "run summary")
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            if resolved in explicit_paths:
                raise ValueError(f"explicit run summary missing run_id: {resolved}")
            continue
        rows.append(
            {
                "run_id": run_id,
                "sds_version": str(payload.get("sds_version", "")),
                "map_id": str(payload.get("map_id", "")),
                "status": str(payload.get("status", "")),
                "summary_path": str(resolved),
            }
        )
    rows.sort(key=lambda row: (str(row.get("run_id", "")), str(row.get("summary_path", ""))))
    return rows


def _collect_release_summaries(release_roots: list[Path], release_files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    paths, explicit_paths = _collect_paths(release_roots, release_files, "*.summary.json")
    for resolved in paths:
        payload = _load_json_object(resolved, "release summary")
        release_id = str(payload.get("release_id", "")).strip()
        if not release_id:
            if resolved in explicit_paths:
                raise ValueError(f"explicit release summary missing release_id: {resolved}")
            continue
        rows.append(
            {
                "release_id": release_id,
                "sds_version": str(payload.get("sds_version", "")),
                "final_result": str(payload.get("final_result", "")),
                "summary_path": str(resolved),
            }
        )
    rows.sort(key=lambda row: (str(row.get("release_id", "")), str(row.get("summary_path", ""))))
    return rows


def _extract_unique_ids(rows: list[dict[str, Any]], key: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        value = str(row.get(key, "")).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _validate_manifest_contract(manifest: dict[str, Any]) -> None:
    if manifest.get("dataset_manifest_schema_version") != DATASET_MANIFEST_SCHEMA_VERSION_V0:
        raise ValueError(
            "dataset_manifest_schema_version must be "
            f"{DATASET_MANIFEST_SCHEMA_VERSION_V0}"
        )

    _normalize_nonempty_text(manifest.get("dataset_id"), field="dataset_id")

    run_summaries = manifest.get("run_summaries")
    release_summaries = manifest.get("release_summaries")
    if not isinstance(run_summaries, list):
        raise ValueError("run_summaries must be a list")
    if not isinstance(release_summaries, list):
        raise ValueError("release_summaries must be a list")

    run_count = int(manifest.get("run_summary_count", -1))
    release_count = int(manifest.get("release_summary_count", -1))
    if run_count != len(run_summaries):
        raise ValueError("run_summary_count must match len(run_summaries)")
    if release_count != len(release_summaries):
        raise ValueError("release_summary_count must match len(release_summaries)")

    run_ids = _extract_unique_ids(run_summaries, "run_id")
    release_ids = _extract_unique_ids(release_summaries, "release_id")
    if manifest.get("run_ids") != run_ids:
        raise ValueError("run_ids must match unique run_summaries.run_id values")
    if manifest.get("release_ids") != release_ids:
        raise ValueError("release_ids must match unique release_summaries.release_id values")

    for row in run_summaries:
        if not isinstance(row, dict):
            raise ValueError("run_summaries rows must be objects")
        _normalize_nonempty_text(row.get("run_id"), field="run_summaries[].run_id")
        _normalize_nonempty_text(row.get("summary_path"), field="run_summaries[].summary_path")
    for row in release_summaries:
        if not isinstance(row, dict):
            raise ValueError("release_summaries rows must be objects")
        _normalize_nonempty_text(row.get("release_id"), field="release_summaries[].release_id")
        _normalize_nonempty_text(row.get("summary_path"), field="release_summaries[].summary_path")


def main() -> int:
    try:
        args = parse_args()
        summary_roots = [Path(path).resolve() for path in args.summary_root]
        summary_files = [Path(path).resolve() for path in args.summary_file]
        release_roots = [Path(path).resolve() for path in args.release_summary_root]
        release_files = [Path(path).resolve() for path in args.release_summary_file]
        if not summary_roots and not summary_files and not release_roots and not release_files:
            raise ValueError(
                "provide at least one of --summary-root, --summary-file, --release-summary-root, or --release-summary-file"
            )
        dataset_id = _normalize_nonempty_text(args.dataset_id, field="dataset_id")

        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_rows = _collect_run_summaries(summary_roots, summary_files)
        release_rows = _collect_release_summaries(release_roots, release_files)

        sds_versions = sorted(
            {
                item["sds_version"]
                for item in [*run_rows, *release_rows]
                if str(item.get("sds_version", "")).strip()
            }
        )
        map_ids = sorted({item["map_id"] for item in run_rows if str(item.get("map_id", "")).strip()})
        manifest = {
            "dataset_manifest_schema_version": DATASET_MANIFEST_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset_id": dataset_id,
            "run_summary_count": len(run_rows),
            "release_summary_count": len(release_rows),
            "sds_versions": sds_versions,
            "map_ids": map_ids,
            "run_ids": _extract_unique_ids(run_rows, "run_id"),
            "release_ids": _extract_unique_ids(release_rows, "release_id"),
            "run_summaries": run_rows,
            "release_summaries": release_rows,
        }
        _validate_manifest_contract(manifest)
        out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] run_summary_count={len(run_rows)}")
        print(f"[ok] release_summary_count={len(release_rows)}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] build_dataset_manifest.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
