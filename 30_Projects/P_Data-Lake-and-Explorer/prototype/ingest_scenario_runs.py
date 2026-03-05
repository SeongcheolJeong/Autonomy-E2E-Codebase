#!/usr/bin/env python3
"""Ingest ScenarioRun summaries into a minimal SQLite data lake."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scenario_run (
  run_id TEXT PRIMARY KEY,
  batch_id TEXT,
  scenario_id TEXT NOT NULL,
  run_timestamp TEXT,
  run_source TEXT,
  lifecycle_state TEXT,
  termination_reason TEXT,
  status TEXT,
  seed INTEGER,
  sds_version TEXT,
  sim_version TEXT,
  fidelity_profile TEXT,
  map_id TEXT,
  map_version TEXT,
  min_ttc_sec REAL,
  collision INTEGER,
  summary_path TEXT,
  ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metric_value (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  metric_id TEXT NOT NULL,
  value REAL,
  value_text TEXT,
  unit TEXT,
  FOREIGN KEY(run_id) REFERENCES scenario_run(run_id)
);

CREATE TABLE IF NOT EXISTS run_tag (
  run_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES scenario_run(run_id)
);

CREATE INDEX IF NOT EXISTS idx_scenario_run_status ON scenario_run(status);
CREATE INDEX IF NOT EXISTS idx_scenario_run_sds_version ON scenario_run(sds_version);
CREATE INDEX IF NOT EXISTS idx_metric_run_id ON metric_value(run_id);
CREATE INDEX IF NOT EXISTS idx_metric_id ON metric_value(metric_id);

CREATE TABLE IF NOT EXISTS release_assessment (
  release_id TEXT NOT NULL,
  sds_version TEXT NOT NULL,
  generated_at TEXT,
  gate_profile TEXT,
  requirement_map TEXT,
  run_count INTEGER,
  success_count INTEGER,
  fail_count INTEGER,
  timeout_count INTEGER,
  collision_count INTEGER,
  collision_rate REAL,
  min_ttc_p5_sec REAL,
  gate_result TEXT,
  requirement_result TEXT,
  final_result TEXT,
  gate_reasons_json TEXT,
  requirement_holds_json TEXT,
  hold_reasons_json TEXT,
  hold_reason_codes_json TEXT,
  report_path TEXT,
  summary_path TEXT,
  ingested_at TEXT NOT NULL,
  PRIMARY KEY (release_id, sds_version)
);

CREATE INDEX IF NOT EXISTS idx_release_assessment_final_result
  ON release_assessment(final_result);
CREATE INDEX IF NOT EXISTS idx_release_assessment_generated_at
  ON release_assessment(generated_at);

CREATE TABLE IF NOT EXISTS dataset_manifest (
  manifest_path TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL,
  dataset_manifest_schema_version TEXT,
  generated_at TEXT,
  run_summary_count INTEGER,
  release_summary_count INTEGER,
  sds_versions_json TEXT,
  map_ids_json TEXT,
  release_ids_json TEXT,
  ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dataset_manifest_dataset_id
  ON dataset_manifest(dataset_id);
CREATE INDEX IF NOT EXISTS idx_dataset_manifest_generated_at
  ON dataset_manifest(generated_at);
"""

ERROR_SOURCE = "ingest_scenario_runs.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest scenario/release summary JSON files into SQLite")
    parser.add_argument(
        "--summary-root",
        action="append",
        default=[],
        help="Directory to recursively scan for ScenarioRun summary.json (can repeat)",
    )
    parser.add_argument(
        "--report-summary-root",
        action="append",
        default=[],
        help="Directory to recursively scan for report *.summary.json (can repeat)",
    )
    parser.add_argument(
        "--report-summary-file",
        action="append",
        default=[],
        help="Explicit report *.summary.json file path (can repeat)",
    )
    parser.add_argument(
        "--dataset-manifest-root",
        action="append",
        default=[],
        help="Directory to recursively scan for dataset manifest *.json (can repeat)",
    )
    parser.add_argument(
        "--dataset-manifest-file",
        action="append",
        default=[],
        help="Explicit dataset manifest JSON file path (can repeat)",
    )
    parser.add_argument("--db", required=True, help="Path to SQLite DB file")
    return parser.parse_args()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def warn_skip(path: Path, kind: str, reason: str) -> None:
    print(f"[warn] skip {kind}: {path} ({reason})", file=sys.stderr)


def load_json_object(path: Path, *, kind: str) -> dict[str, Any] | None:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warn_skip(path, kind, f"read_error={exc}")
        return None

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        warn_skip(path, kind, f"invalid_json={exc.msg}")
        return None

    if not isinstance(payload, dict):
        warn_skip(path, kind, "expected_json_object")
        return None
    return payload


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _ensure_release_assessment_columns(conn)


def _ensure_release_assessment_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA table_info(release_assessment)").fetchall()
    existing = {str(row[1]) for row in rows}
    required_columns = [
        ("gate_reasons_json", "TEXT"),
        ("requirement_holds_json", "TEXT"),
        ("hold_reasons_json", "TEXT"),
        ("hold_reason_codes_json", "TEXT"),
    ]
    for column_name, column_type in required_columns:
        if column_name not in existing:
            conn.execute(f"ALTER TABLE release_assessment ADD COLUMN {column_name} {column_type}")


def _to_real_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int_bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _to_json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append(normalized)
    return rows


def _extract_release_ids(payload: dict[str, Any]) -> list[str]:
    release_summaries = payload.get("release_summaries", [])
    if not isinstance(release_summaries, list):
        return []
    release_ids: list[str] = []
    seen: set[str] = set()
    for row in release_summaries:
        if not isinstance(row, dict):
            continue
        release_id = str(row.get("release_id", "")).strip()
        if not release_id or release_id in seen:
            continue
        seen.add(release_id)
        release_ids.append(release_id)
    return release_ids


def upsert_summary(conn: sqlite3.Connection, payload: dict[str, Any], summary_path: Path) -> None:
    run_id = str(payload["run_id"])
    ingested_at = iso_now()

    conn.execute(
        """
        INSERT INTO scenario_run (
          run_id, batch_id, scenario_id, run_timestamp, run_source, lifecycle_state,
          termination_reason, status, seed, sds_version, sim_version, fidelity_profile,
          map_id, map_version, min_ttc_sec, collision, summary_path, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
          batch_id=excluded.batch_id,
          scenario_id=excluded.scenario_id,
          run_timestamp=excluded.run_timestamp,
          run_source=excluded.run_source,
          lifecycle_state=excluded.lifecycle_state,
          termination_reason=excluded.termination_reason,
          status=excluded.status,
          seed=excluded.seed,
          sds_version=excluded.sds_version,
          sim_version=excluded.sim_version,
          fidelity_profile=excluded.fidelity_profile,
          map_id=excluded.map_id,
          map_version=excluded.map_version,
          min_ttc_sec=excluded.min_ttc_sec,
          collision=excluded.collision,
          summary_path=excluded.summary_path,
          ingested_at=excluded.ingested_at
        """,
        (
            run_id,
            payload.get("batch_id"),
            payload.get("scenario_id"),
            payload.get("run_timestamp"),
            payload.get("run_source"),
            payload.get("lifecycle_state"),
            payload.get("termination_reason"),
            payload.get("status"),
            payload.get("seed"),
            payload.get("sds_version"),
            payload.get("sim_version"),
            payload.get("fidelity_profile"),
            payload.get("map_id"),
            payload.get("map_version"),
            _to_real_or_none(payload.get("min_ttc_sec")),
            _to_int_bool(payload.get("collision")),
            str(summary_path),
            ingested_at,
        ),
    )

    conn.execute("DELETE FROM metric_value WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM run_tag WHERE run_id = ?", (run_id,))

    for metric in payload.get("metric_values", []):
        if not isinstance(metric, dict):
            continue
        metric_id = metric.get("metric_id")
        if not metric_id:
            continue

        raw_value = metric.get("value")
        if isinstance(raw_value, (int, float)):
            value = float(raw_value)
            value_text = None
        else:
            value = None
            value_text = None if raw_value is None else str(raw_value)

        conn.execute(
            """
            INSERT INTO metric_value (run_id, metric_id, value, value_text, unit)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, str(metric_id), value, value_text, metric.get("unit")),
        )

    for tag in payload.get("odd_tags", []):
        conn.execute(
            "INSERT INTO run_tag (run_id, tag) VALUES (?, ?)",
            (run_id, str(tag)),
        )


def upsert_release_summary(conn: sqlite3.Connection, payload: dict[str, Any], summary_path: Path) -> None:
    release_id = str(payload["release_id"])
    sds_version = str(payload["sds_version"])
    ingested_at = iso_now()

    conn.execute(
        """
        INSERT INTO release_assessment (
          release_id, sds_version, generated_at, gate_profile, requirement_map,
          run_count, success_count, fail_count, timeout_count, collision_count,
          collision_rate, min_ttc_p5_sec, gate_result, requirement_result, final_result,
          gate_reasons_json, requirement_holds_json, hold_reasons_json, hold_reason_codes_json,
          report_path, summary_path, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(release_id, sds_version) DO UPDATE SET
          generated_at=excluded.generated_at,
          gate_profile=excluded.gate_profile,
          requirement_map=excluded.requirement_map,
          run_count=excluded.run_count,
          success_count=excluded.success_count,
          fail_count=excluded.fail_count,
          timeout_count=excluded.timeout_count,
          collision_count=excluded.collision_count,
          collision_rate=excluded.collision_rate,
          min_ttc_p5_sec=excluded.min_ttc_p5_sec,
          gate_result=excluded.gate_result,
          requirement_result=excluded.requirement_result,
          final_result=excluded.final_result,
          gate_reasons_json=excluded.gate_reasons_json,
          requirement_holds_json=excluded.requirement_holds_json,
          hold_reasons_json=excluded.hold_reasons_json,
          hold_reason_codes_json=excluded.hold_reason_codes_json,
          report_path=excluded.report_path,
          summary_path=excluded.summary_path,
          ingested_at=excluded.ingested_at
        """,
        (
            release_id,
            sds_version,
            payload.get("generated_at"),
            payload.get("gate_profile"),
            payload.get("requirement_map"),
            payload.get("run_count"),
            payload.get("success_count"),
            payload.get("fail_count"),
            payload.get("timeout_count"),
            payload.get("collision_count"),
            _to_real_or_none(payload.get("collision_rate")),
            _to_real_or_none(payload.get("min_ttc_p5_sec")),
            payload.get("gate_result"),
            payload.get("requirement_result"),
            payload.get("final_result"),
            _to_json_text(payload.get("gate_reasons")),
            _to_json_text(payload.get("requirement_hold_records")),
            _to_json_text(payload.get("hold_reasons")),
            _to_json_text(payload.get("hold_reason_codes")),
            payload.get("report_path"),
            str(summary_path),
            ingested_at,
        ),
    )


def upsert_dataset_manifest(conn: sqlite3.Connection, payload: dict[str, Any], manifest_path: Path) -> None:
    dataset_id = str(payload["dataset_id"])
    ingested_at = iso_now()
    release_ids = _extract_release_ids(payload)
    sds_versions = _normalize_text_list(payload.get("sds_versions"))
    map_ids = _normalize_text_list(payload.get("map_ids"))

    conn.execute(
        """
        INSERT INTO dataset_manifest (
          manifest_path, dataset_id, dataset_manifest_schema_version, generated_at,
          run_summary_count, release_summary_count, sds_versions_json, map_ids_json,
          release_ids_json, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(manifest_path) DO UPDATE SET
          dataset_id=excluded.dataset_id,
          dataset_manifest_schema_version=excluded.dataset_manifest_schema_version,
          generated_at=excluded.generated_at,
          run_summary_count=excluded.run_summary_count,
          release_summary_count=excluded.release_summary_count,
          sds_versions_json=excluded.sds_versions_json,
          map_ids_json=excluded.map_ids_json,
          release_ids_json=excluded.release_ids_json,
          ingested_at=excluded.ingested_at
        """,
        (
            str(manifest_path),
            dataset_id,
            payload.get("dataset_manifest_schema_version"),
            payload.get("generated_at"),
            _to_int_or_none(payload.get("run_summary_count")),
            _to_int_or_none(payload.get("release_summary_count")),
            _to_json_text(sds_versions),
            _to_json_text(map_ids),
            _to_json_text(release_ids),
            ingested_at,
        ),
    )


def ingest(
    summary_roots: list[Path],
    report_summary_roots: list[Path],
    report_summary_files: list[Path],
    dataset_manifest_roots: list[Path],
    dataset_manifest_files: list[Path],
    db_path: Path,
) -> tuple[int, int, int, int]:
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        run_ingested_count = 0
        release_ingested_count = 0
        dataset_manifest_ingested_count = 0
        skipped_input_count = 0

        for root in summary_roots:
            for summary_path in root.rglob("summary.json"):
                payload = load_json_object(summary_path, kind="run_summary")
                if payload is None:
                    skipped_input_count += 1
                    continue
                if "run_id" not in payload or "scenario_id" not in payload:
                    warn_skip(summary_path, "run_summary", "missing_run_id_or_scenario_id")
                    skipped_input_count += 1
                    continue
                upsert_summary(conn, payload, summary_path)
                run_ingested_count += 1

        release_paths: list[Path] = []
        for root in report_summary_roots:
            release_paths.extend(root.rglob("*.summary.json"))
        release_paths.extend(report_summary_files)

        seen_release_paths: set[Path] = set()
        for summary_path in release_paths:
            resolved = summary_path.resolve()
            if resolved in seen_release_paths:
                continue
            seen_release_paths.add(resolved)

            payload = load_json_object(resolved, kind="release_summary")
            if payload is None:
                skipped_input_count += 1
                continue
            if "release_id" not in payload or "sds_version" not in payload or "final_result" not in payload:
                warn_skip(resolved, "release_summary", "missing_release_id_or_sds_version_or_final_result")
                skipped_input_count += 1
                continue
            upsert_release_summary(conn, payload, resolved)
            release_ingested_count += 1

        dataset_manifest_paths: list[Path] = []
        for root in dataset_manifest_roots:
            dataset_manifest_paths.extend(root.rglob("*.json"))
        dataset_manifest_paths.extend(dataset_manifest_files)

        seen_manifest_paths: set[Path] = set()
        for manifest_path in dataset_manifest_paths:
            resolved = manifest_path.resolve()
            if resolved in seen_manifest_paths:
                continue
            seen_manifest_paths.add(resolved)

            payload = load_json_object(resolved, kind="dataset_manifest")
            if payload is None:
                skipped_input_count += 1
                continue
            if "dataset_manifest_schema_version" not in payload or "dataset_id" not in payload:
                warn_skip(resolved, "dataset_manifest", "missing_dataset_manifest_schema_version_or_dataset_id")
                skipped_input_count += 1
                continue
            upsert_dataset_manifest(conn, payload, resolved)
            dataset_manifest_ingested_count += 1

        conn.commit()
        return run_ingested_count, release_ingested_count, dataset_manifest_ingested_count, skipped_input_count
    finally:
        conn.close()


def main() -> int:
    try:
        args = parse_args()
        summary_roots = [Path(path).resolve() for path in args.summary_root]
        report_summary_roots = [Path(path).resolve() for path in args.report_summary_root]
        report_summary_files = [Path(path).resolve() for path in args.report_summary_file]
        dataset_manifest_roots = [Path(path).resolve() for path in args.dataset_manifest_root]
        dataset_manifest_files = [Path(path).resolve() for path in args.dataset_manifest_file]
        if (
            not summary_roots
            and not report_summary_roots
            and not report_summary_files
            and not dataset_manifest_roots
            and not dataset_manifest_files
        ):
            message = (
                "provide at least one of --summary-root, --report-summary-root, "
                "--report-summary-file, --dataset-manifest-root, or --dataset-manifest-file"
            )
            print(f"[error] {message}")
            write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
            return 2

        db_path = Path(args.db).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        run_count, release_count, dataset_manifest_count, skipped_count = ingest(
            summary_roots,
            report_summary_roots,
            report_summary_files,
            dataset_manifest_roots,
            dataset_manifest_files,
            db_path,
        )
        print(f"[ok] db={db_path}")
        print(f"[ok] ingested_run_summary_count={run_count}")
        print(f"[ok] ingested_release_summary_count={release_count}")
        print(f"[ok] ingested_dataset_manifest_count={dataset_manifest_count}")
        print(f"[ok] skipped_input_count={skipped_count}")
        return 0
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        print(f"[error] ingest_scenario_runs.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
