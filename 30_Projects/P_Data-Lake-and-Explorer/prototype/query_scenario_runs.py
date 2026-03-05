#!/usr/bin/env python3
"""Query helper for minimal ScenarioRun SQLite lake."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary

OUTPUT_FORMAT = "text"
ERROR_SOURCE = "query_scenario_runs.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query ScenarioRun SQLite lake")
    parser.add_argument("--db", required=True, help="Path to SQLite DB")
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    failures = sub.add_parser("failures", help="List failed runs")
    failures.add_argument("--limit", default="")

    near_miss = sub.add_parser("near-miss", help="List runs below minTTC threshold")
    near_miss.add_argument("--ttc-threshold", default="")
    near_miss.add_argument("--limit", default="")

    compare = sub.add_parser("compare", help="Compare metric by SDS version")
    compare.add_argument("--metric-id", default="collision_flag")
    compare.add_argument("--version-a", required=True)
    compare.add_argument("--version-b", required=True)

    release_latest = sub.add_parser("release-latest", help="List latest release assessments")
    release_latest.add_argument("--limit", default="")
    release_latest.add_argument("--release-prefix", default="", help="Optional release_id prefix filter")
    release_latest.add_argument("--sds-version", default="", help="Optional SDS version filter")

    release_holds = sub.add_parser("release-holds", help="List HOLD release assessments")
    release_holds.add_argument("--limit", default="")
    release_holds.add_argument("--release-prefix", default="", help="Optional release_id prefix filter")
    release_holds.add_argument("--sds-version", default="", help="Optional SDS version filter")

    hold_reasons = sub.add_parser("release-hold-reasons", help="Aggregate HOLD reasons")
    hold_reasons.add_argument("--limit", default="")
    hold_reasons.add_argument(
        "--mode",
        choices=["code", "raw"],
        default="code",
        help="Aggregate by normalized code or raw reason text",
    )

    release_trend = sub.add_parser("release-trend", help="Trend summary by SDS version")
    release_trend.add_argument("--window", default="", help="Recent samples per SDS version")

    release_compare = sub.add_parser("release-compare", help="Compare release trend between two SDS versions")
    release_compare.add_argument("--version-a", required=True)
    release_compare.add_argument("--version-b", required=True)
    release_compare.add_argument("--window", default="")

    release_diff = sub.add_parser("release-diff", help="Compare two SDS versions for one release prefix")
    release_diff.add_argument("--release-prefix", required=True)
    release_diff.add_argument("--version-a", required=True)
    release_diff.add_argument("--version-b", required=True)

    dataset_latest = sub.add_parser("dataset-latest", help="List latest ingested dataset manifests")
    dataset_latest.add_argument("--limit", default="")

    dataset_release_links = sub.add_parser(
        "dataset-release-links",
        help="List dataset-to-release linkage from ingested dataset manifests",
    )
    dataset_release_links.add_argument("--release-id", default="", help="Optional release_id filter")
    dataset_release_links.add_argument("--dataset-id", default="", help="Optional dataset_id filter")
    dataset_release_links.add_argument("--limit", default="")

    return parser.parse_args()


def parse_int(raw: Any, *, default: int, field: str, minimum: int | None = None) -> int:
    value = str(raw).strip()
    if not value:
        parsed = default
    else:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer, got: {raw}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed


def parse_float(raw: Any, *, default: float, field: str, minimum: float | None = None) -> float:
    value = str(raw).strip()
    if not value:
        parsed = default
    else:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be a number, got: {raw}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed


def normalize_numeric_args(args: argparse.Namespace) -> None:
    if args.command in {
        "failures",
        "release-latest",
        "release-holds",
        "release-hold-reasons",
        "dataset-latest",
        "dataset-release-links",
    }:
        args.limit = parse_int(args.limit, default=20, field="limit", minimum=1)
        return

    if args.command == "near-miss":
        args.ttc_threshold = parse_float(
            args.ttc_threshold,
            default=2.0,
            field="ttc-threshold",
            minimum=0.0,
        )
        args.limit = parse_int(args.limit, default=20, field="limit", minimum=1)
        return

    if args.command in {"release-trend", "release-compare"}:
        args.window = parse_int(args.window, default=10, field="window", minimum=1)


def _print_rows(columns: list[str], rows: list[tuple]) -> None:
    if OUTPUT_FORMAT == "json":
        json_rows: list[dict[str, Any]] = []
        for row in rows:
            json_rows.append(
                {
                    column: (None if idx >= len(row) else row[idx])
                    for idx, column in enumerate(columns)
                }
            )
        print(
            json.dumps(
                {
                    "columns": columns,
                    "row_count": len(rows),
                    "rows": json_rows,
                },
                ensure_ascii=True,
            )
        )
        return
    print(" | ".join(columns))
    print("-" * 80)
    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))


def cmd_failures(conn: sqlite3.Connection, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT run_id, scenario_id, sds_version, termination_reason, min_ttc_sec, summary_path
        FROM scenario_run
        WHERE status = 'failed' OR collision = 1
        ORDER BY run_timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    _print_rows(
        ["run_id", "scenario_id", "sds_version", "termination_reason", "min_ttc_sec", "summary_path"],
        rows,
    )


def cmd_near_miss(conn: sqlite3.Connection, threshold: float, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT run_id, scenario_id, sds_version, min_ttc_sec, status, summary_path
        FROM scenario_run
        WHERE min_ttc_sec IS NOT NULL AND min_ttc_sec < ?
        ORDER BY min_ttc_sec ASC
        LIMIT ?
        """,
        (threshold, limit),
    ).fetchall()
    _print_rows(
        ["run_id", "scenario_id", "sds_version", "min_ttc_sec", "status", "summary_path"],
        rows,
    )


def cmd_compare(conn: sqlite3.Connection, metric_id: str, version_a: str, version_b: str) -> None:
    rows = conn.execute(
        """
        SELECT sr.sds_version, COUNT(*) AS run_count, AVG(mv.value) AS metric_avg
        FROM metric_value mv
        JOIN scenario_run sr ON sr.run_id = mv.run_id
        WHERE mv.metric_id = ?
          AND sr.sds_version IN (?, ?)
        GROUP BY sr.sds_version
        ORDER BY sr.sds_version
        """,
        (metric_id, version_a, version_b),
    ).fetchall()
    _print_rows(["sds_version", "run_count", f"avg({metric_id})"], rows)


def cmd_release_latest(
    conn: sqlite3.Connection,
    limit: int,
    release_prefix: str,
    sds_version: str,
) -> None:
    clauses: list[str] = []
    params: list[Any] = []
    normalized_prefix = release_prefix.strip()
    normalized_version = sds_version.strip()
    if normalized_prefix:
        clauses.append("release_id LIKE ?")
        params.append(f"{normalized_prefix}%")
    if normalized_version:
        clauses.append("sds_version = ?")
        params.append(normalized_version)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    rows = conn.execute(
        f"""
        SELECT release_id, sds_version, final_result, gate_result, requirement_result,
               run_count, fail_count, timeout_count, collision_count, generated_at, hold_reasons_json
        FROM release_assessment
        {where_sql}
        ORDER BY generated_at DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    ).fetchall()
    _print_rows(
        [
            "release_id",
            "sds_version",
            "final_result",
            "gate_result",
            "requirement_result",
            "run_count",
            "fail_count",
            "timeout_count",
            "collision_count",
            "generated_at",
            "hold_reasons_json",
        ],
        rows,
    )


def cmd_release_holds(
    conn: sqlite3.Connection,
    limit: int,
    release_prefix: str,
    sds_version: str,
) -> None:
    clauses = ["final_result = 'HOLD'"]
    params: list[Any] = []
    normalized_prefix = release_prefix.strip()
    normalized_version = sds_version.strip()
    if normalized_prefix:
        clauses.append("release_id LIKE ?")
        params.append(f"{normalized_prefix}%")
    if normalized_version:
        clauses.append("sds_version = ?")
        params.append(normalized_version)

    where_sql = "WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT release_id, sds_version, final_result, gate_result, requirement_result,
               run_count, fail_count, timeout_count, collision_count, generated_at, hold_reasons_json
        FROM release_assessment
        {where_sql}
        ORDER BY generated_at DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    ).fetchall()
    _print_rows(
        [
            "release_id",
            "sds_version",
            "final_result",
            "gate_result",
            "requirement_result",
            "run_count",
            "fail_count",
            "timeout_count",
            "collision_count",
            "generated_at",
            "hold_reasons_json",
        ],
        rows,
    )


def cmd_release_hold_reasons(conn: sqlite3.Connection, limit: int, mode: str) -> None:
    reason_column = "hold_reason_codes_json" if mode == "code" else "hold_reasons_json"
    rows = conn.execute(
        f"""
        SELECT {reason_column}
        FROM release_assessment
        WHERE final_result = 'HOLD'
          AND {reason_column} IS NOT NULL
        """
    ).fetchall()

    counts: dict[str, int] = {}
    for (payload_text,) in rows:
        if payload_text is None:
            continue
        try:
            payload = json.loads(str(payload_text))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            reason = str(item)
            counts[reason] = counts.get(reason, 0) + 1

    sorted_items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    result_rows = [(reason, count) for reason, count in sorted_items]
    label = "hold_reason_code" if mode == "code" else "hold_reason"
    _print_rows([label, "count"], result_rows)


def cmd_release_trend(conn: sqlite3.Connection, window: int) -> None:
    rows = conn.execute(
        """
        SELECT release_id, sds_version, final_result, generated_at
        FROM release_assessment
        WHERE generated_at IS NOT NULL
        ORDER BY generated_at DESC
        """
    ).fetchall()

    stats: dict[str, dict[str, str | int | float]] = {}
    for release_id, sds_version, final_result, generated_at in rows:
        version = str(sds_version)
        entry = stats.setdefault(
            version,
            {
                "sample_count": 0,
                "pass_count": 0,
                "hold_count": 0,
                "latest_release_id": "",
                "latest_generated_at": "",
                "latest_result": "",
            },
        )

        if int(entry["sample_count"]) >= window:
            continue

        if int(entry["sample_count"]) == 0:
            entry["latest_release_id"] = str(release_id)
            entry["latest_generated_at"] = str(generated_at)
            entry["latest_result"] = str(final_result)

        entry["sample_count"] = int(entry["sample_count"]) + 1
        if str(final_result) == "PASS":
            entry["pass_count"] = int(entry["pass_count"]) + 1
        elif str(final_result) == "HOLD":
            entry["hold_count"] = int(entry["hold_count"]) + 1

    result_rows: list[tuple] = []
    for version in sorted(stats.keys()):
        entry = stats[version]
        sample_count = int(entry["sample_count"])
        pass_count = int(entry["pass_count"])
        hold_count = int(entry["hold_count"])
        pass_rate = (pass_count / sample_count) if sample_count > 0 else 0.0
        result_rows.append(
            (
                version,
                sample_count,
                pass_count,
                hold_count,
                f"{pass_rate:.4f}",
                entry["latest_result"],
                entry["latest_release_id"],
                entry["latest_generated_at"],
            )
        )

    _print_rows(
        [
            "sds_version",
            "sample_count",
            "pass_count",
            "hold_count",
            "pass_rate",
            "latest_result",
            "latest_release_id",
            "latest_generated_at",
        ],
        result_rows,
    )


def _trend_stats_for_version(conn: sqlite3.Connection, sds_version: str, window: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT release_id, final_result, generated_at
        FROM release_assessment
        WHERE sds_version = ?
          AND generated_at IS NOT NULL
        ORDER BY generated_at DESC
        LIMIT ?
        """,
        (sds_version, window),
    ).fetchall()

    sample_count = len(rows)
    pass_count = sum(1 for row in rows if str(row[1]) == "PASS")
    hold_count = sum(1 for row in rows if str(row[1]) == "HOLD")
    pass_rate = (pass_count / sample_count) if sample_count > 0 else 0.0

    latest_release_id = str(rows[0][0]) if rows else ""
    latest_result = str(rows[0][1]) if rows else ""
    latest_generated_at = str(rows[0][2]) if rows else ""

    return {
        "sds_version": sds_version,
        "sample_count": sample_count,
        "pass_count": pass_count,
        "hold_count": hold_count,
        "pass_rate": pass_rate,
        "latest_result": latest_result,
        "latest_release_id": latest_release_id,
        "latest_generated_at": latest_generated_at,
    }


def cmd_release_compare(conn: sqlite3.Connection, version_a: str, version_b: str, window: int) -> None:
    stats_a = _trend_stats_for_version(conn, version_a, window)
    stats_b = _trend_stats_for_version(conn, version_b, window)

    rows = [
        (
            stats_a["sds_version"],
            stats_a["sample_count"],
            stats_a["pass_count"],
            stats_a["hold_count"],
            f"{float(stats_a['pass_rate']):.4f}",
            stats_a["latest_result"],
            stats_a["latest_release_id"],
            stats_a["latest_generated_at"],
        ),
        (
            stats_b["sds_version"],
            stats_b["sample_count"],
            stats_b["pass_count"],
            stats_b["hold_count"],
            f"{float(stats_b['pass_rate']):.4f}",
            stats_b["latest_result"],
            stats_b["latest_release_id"],
            stats_b["latest_generated_at"],
        ),
    ]
    _print_rows(
        [
            "sds_version",
            "sample_count",
            "pass_count",
            "hold_count",
            "pass_rate",
            "latest_result",
            "latest_release_id",
            "latest_generated_at",
        ],
        rows,
    )

    if OUTPUT_FORMAT == "json":
        return
    delta = float(stats_b["pass_rate"]) - float(stats_a["pass_rate"])
    print("")
    print(f"delta_pass_rate({version_b} - {version_a}) = {delta:.4f}")


def _release_row_for_version(
    conn: sqlite3.Connection, release_prefix: str, sds_version: str
) -> tuple | None:
    release_id = f"{release_prefix}_{sds_version}"
    return conn.execute(
        """
        SELECT release_id, sds_version, final_result, gate_result, requirement_result,
               run_count, fail_count, timeout_count, collision_count, hold_reason_codes_json,
               generated_at
        FROM release_assessment
        WHERE release_id = ?
        """,
        (release_id,),
    ).fetchone()


def cmd_release_diff(
    conn: sqlite3.Connection, release_prefix: str, version_a: str, version_b: str
) -> None:
    row_a = _release_row_for_version(conn, release_prefix, version_a)
    row_b = _release_row_for_version(conn, release_prefix, version_b)

    if row_a is None and row_b is None:
        if OUTPUT_FORMAT == "json":
            print(
                json.dumps(
                    {
                        "error": f"no release assessment found for prefix={release_prefix}",
                        "columns": [],
                        "row_count": 0,
                        "rows": [],
                    },
                    ensure_ascii=True,
                )
            )
        else:
            print(f"[error] no release assessment found for prefix={release_prefix}")
        return

    rows: list[tuple] = []
    if row_a is not None:
        rows.append(row_a)
    if row_b is not None:
        rows.append(row_b)

    _print_rows(
        [
            "release_id",
            "sds_version",
            "final_result",
            "gate_result",
            "requirement_result",
            "run_count",
            "fail_count",
            "timeout_count",
            "collision_count",
            "hold_reason_codes_json",
            "generated_at",
        ],
        rows,
    )

    if row_a is None or row_b is None:
        return

    fail_delta = int(row_b[6]) - int(row_a[6])
    timeout_delta = int(row_b[7]) - int(row_a[7])
    collision_delta = int(row_b[8]) - int(row_a[8])
    if OUTPUT_FORMAT == "json":
        return
    print("")
    print(
        f"delta_fail_count({version_b} - {version_a}) = {fail_delta}, "
        f"delta_timeout_count = {timeout_delta}, delta_collision_count = {collision_delta}"
    )


def _parse_json_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    rows: list[str] = []
    for item in payload:
        normalized = str(item).strip()
        if normalized:
            rows.append(normalized)
    return rows


def cmd_dataset_latest(conn: sqlite3.Connection, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT dataset_id, dataset_manifest_schema_version, generated_at,
               run_summary_count, release_summary_count, release_ids_json,
               manifest_path, ingested_at
        FROM dataset_manifest
        ORDER BY COALESCE(generated_at, ingested_at) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    _print_rows(
        [
            "dataset_id",
            "dataset_manifest_schema_version",
            "generated_at",
            "run_summary_count",
            "release_summary_count",
            "release_ids_json",
            "manifest_path",
            "ingested_at",
        ],
        rows,
    )


def cmd_dataset_release_links(
    conn: sqlite3.Connection,
    release_id_filter: str,
    dataset_id_filter: str,
    limit: int,
) -> None:
    rows = conn.execute(
        """
        SELECT dataset_id, generated_at, release_ids_json, manifest_path, ingested_at
        FROM dataset_manifest
        ORDER BY COALESCE(generated_at, ingested_at) DESC
        """
    ).fetchall()

    release_id_filter_normalized = release_id_filter.strip()
    dataset_id_filter_normalized = dataset_id_filter.strip()

    result_rows: list[tuple[str, str, str, str]] = []
    for dataset_id, generated_at, release_ids_json, manifest_path, _ingested_at in rows:
        dataset_id_text = str(dataset_id)
        if dataset_id_filter_normalized and dataset_id_text != dataset_id_filter_normalized:
            continue

        for release_id in _parse_json_text_list(release_ids_json):
            if release_id_filter_normalized and release_id != release_id_filter_normalized:
                continue
            result_rows.append((dataset_id_text, release_id, str(generated_at), str(manifest_path)))
            if len(result_rows) >= limit:
                _print_rows(["dataset_id", "release_id", "generated_at", "manifest_path"], result_rows)
                return

    _print_rows(["dataset_id", "release_id", "generated_at", "manifest_path"], result_rows)


def main() -> int:
    global OUTPUT_FORMAT
    try:
        args = parse_args()
        OUTPUT_FORMAT = str(args.output_format)
        normalize_numeric_args(args)
        db_path = Path(args.db).resolve()
        conn = sqlite3.connect(db_path)

        if args.command == "failures":
            cmd_failures(conn, args.limit)
        elif args.command == "near-miss":
            cmd_near_miss(conn, args.ttc_threshold, args.limit)
        elif args.command == "compare":
            cmd_compare(conn, args.metric_id, args.version_a, args.version_b)
        elif args.command == "release-latest":
            cmd_release_latest(conn, args.limit, args.release_prefix, args.sds_version)
        elif args.command == "release-holds":
            cmd_release_holds(conn, args.limit, args.release_prefix, args.sds_version)
        elif args.command == "release-hold-reasons":
            cmd_release_hold_reasons(conn, args.limit, args.mode)
        elif args.command == "release-trend":
            cmd_release_trend(conn, args.window)
        elif args.command == "release-compare":
            cmd_release_compare(conn, args.version_a, args.version_b, args.window)
        elif args.command == "release-diff":
            cmd_release_diff(conn, args.release_prefix, args.version_a, args.version_b)
        elif args.command == "dataset-latest":
            cmd_dataset_latest(conn, args.limit)
        elif args.command == "dataset-release-links":
            cmd_dataset_release_links(conn, args.release_id, args.dataset_id, args.limit)
        return 0
    except Exception as exc:
        message = str(exc)
        print(f"[error] query_scenario_runs.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 1
    finally:
        if "conn" in locals():
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
