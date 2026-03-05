#!/usr/bin/env python3
"""Minimal validation checks for canonical_lane_graph_v0 maps."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0 = "canonical_lane_graph_v0"
CANONICAL_MAP_VALIDATION_REPORT_SCHEMA_VERSION_V0 = "canonical_map_validation_report_v0"
CENTERLINE_CONTINUITY_WARN_THRESHOLD_M = 2.0
ERROR_SOURCE = "validate_canonical_map.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate canonical_lane_graph_v0 map")
    parser.add_argument("--map", required=True, help="Canonical map JSON path")
    parser.add_argument("--report-out", default="", help="Optional report JSON path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _parse_centerline_points(*, lane_id: str, value: Any, errors: list[str]) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < 2:
        errors.append(f"lane {lane_id} centerline_m must have at least 2 points")
        return []

    points: list[tuple[float, float]] = []
    for point_index, point in enumerate(value):
        if not isinstance(point, dict):
            errors.append(f"lane {lane_id} centerline_m[{point_index}] must be an object with x_m/y_m")
            continue
        if "x_m" not in point or "y_m" not in point:
            errors.append(f"lane {lane_id} centerline_m[{point_index}] must include x_m/y_m")
            continue
        try:
            x_m = float(point["x_m"])
            y_m = float(point["y_m"])
        except (TypeError, ValueError):
            errors.append(f"lane {lane_id} centerline_m[{point_index}] x_m/y_m must be numeric")
            continue
        points.append((x_m, y_m))
    if len(points) < 2:
        errors.append(f"lane {lane_id} centerline_m must contain at least 2 valid points")
    return points


def _parse_lane_refs(*, lane_id: str, field: str, value: Any, errors: list[str], warnings: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"lane {lane_id} {field} must be a list")
        return []

    refs: list[str] = []
    seen: set[str] = set()
    for ref_index, ref in enumerate(value):
        ref_id = str(ref).strip()
        if not ref_id:
            errors.append(f"lane {lane_id} {field}[{ref_index}] must be a non-empty lane_id")
            continue
        if ref_id == lane_id:
            errors.append(f"lane {lane_id} {field} cannot include self")
            continue
        if ref_id in seen:
            warnings.append(f"lane {lane_id} {field} has duplicate reference: {ref_id}")
            continue
        seen.add(ref_id)
        refs.append(ref_id)
    return refs


def _distance_m(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    dx = point_a[0] - point_b[0]
    dy = point_a[1] - point_b[1]
    return math.hypot(dx, dy)


def validate_map(payload: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    semantic_summary: dict[str, Any] = {
        "lane_count": 0,
        "successor_edge_count": 0,
        "entry_lane_count": 0,
        "exit_lane_count": 0,
        "continuity_gap_warning_count": 0,
        "non_reciprocal_predecessor_warning_count": 0,
        "non_reciprocal_successor_warning_count": 0,
        "unreachable_lane_count": 0,
        "unreachable_lane_ids": [],
        "entry_lane_missing_warning_count": 0,
        "routing_semantic_warning_count": 0,
        "routing_semantic_status": "fail",
    }
    if str(payload.get("map_schema_version", "")) != CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0:
        errors.append(
            "map_schema_version must be "
            f"{CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0}"
        )
        return errors, warnings, semantic_summary

    lanes = payload.get("lanes", [])
    if not isinstance(lanes, list) or len(lanes) == 0:
        errors.append("lanes must be a non-empty list")
        return errors, warnings, semantic_summary

    lane_ids: list[str] = []
    lane_by_id: dict[str, dict[str, Any]] = {}
    lane_centerline_by_id: dict[str, list[tuple[float, float]]] = {}
    lane_predecessors_by_id: dict[str, list[str]] = {}
    lane_successors_by_id: dict[str, list[str]] = {}
    for idx, lane in enumerate(lanes):
        if not isinstance(lane, dict):
            errors.append(f"lane[{idx}] must be an object")
            continue
        lane_id = str(lane.get("lane_id", "")).strip()
        if not lane_id:
            errors.append(f"lane[{idx}] lane_id must be non-empty")
            continue
        if lane_id in lane_by_id:
            errors.append(f"duplicate lane_id: {lane_id}")
            continue

        centerline_points = _parse_centerline_points(
            lane_id=lane_id,
            value=lane.get("centerline_m", []),
            errors=errors,
        )
        predecessors = _parse_lane_refs(
            lane_id=lane_id,
            field="predecessor_lane_ids",
            value=lane.get("predecessor_lane_ids", []),
            errors=errors,
            warnings=warnings,
        )
        successors = _parse_lane_refs(
            lane_id=lane_id,
            field="successor_lane_ids",
            value=lane.get("successor_lane_ids", []),
            errors=errors,
            warnings=warnings,
        )
        lane_ids.append(lane_id)
        lane_by_id[lane_id] = lane
        lane_centerline_by_id[lane_id] = centerline_points
        lane_predecessors_by_id[lane_id] = predecessors
        lane_successors_by_id[lane_id] = successors

    lane_id_set = set(lane_ids)
    continuity_gap_warning_count = 0
    non_reciprocal_predecessor_warning_count = 0
    non_reciprocal_successor_warning_count = 0
    for lane_id in lane_by_id:
        predecessors = lane_predecessors_by_id.get(lane_id, [])
        successors = lane_successors_by_id.get(lane_id, [])
        lane_centerline = lane_centerline_by_id.get(lane_id, [])
        lane_end = lane_centerline[-1] if lane_centerline else None

        for pred in predecessors:
            if pred not in lane_id_set:
                errors.append(f"lane {lane_id} predecessor not found: {pred}")
                continue
            if lane_id not in lane_successors_by_id.get(pred, []):
                warnings.append(f"lane {lane_id} predecessor linkage not reciprocal: {pred}")
                non_reciprocal_predecessor_warning_count += 1
        for succ in successors:
            if succ not in lane_id_set:
                errors.append(f"lane {lane_id} successor not found: {succ}")
                continue
            if lane_id not in lane_predecessors_by_id.get(succ, []):
                warnings.append(f"lane {lane_id} successor linkage not reciprocal: {succ}")
                non_reciprocal_successor_warning_count += 1

            succ_centerline = lane_centerline_by_id.get(succ, [])
            succ_start = succ_centerline[0] if succ_centerline else None
            if lane_end is not None and succ_start is not None:
                centerline_gap_m = _distance_m(lane_end, succ_start)
                if centerline_gap_m > CENTERLINE_CONTINUITY_WARN_THRESHOLD_M:
                    warnings.append(
                        "lane "
                        f"{lane_id} -> {succ} centerline gap {centerline_gap_m:.3f}m "
                        f"exceeds {CENTERLINE_CONTINUITY_WARN_THRESHOLD_M:.1f}m"
                    )
                    continuity_gap_warning_count += 1

    entry_lane_ids = sorted(
        lane_id for lane_id in lane_ids if not lane_predecessors_by_id.get(lane_id, [])
    )
    entry_lane_missing_warning_count = 0
    unreachable_lane_ids: list[str] = []
    if not entry_lane_ids:
        warnings.append("no entry lanes found (all lanes have predecessors)")
        entry_lane_missing_warning_count = 1
    else:
        visited: set[str] = set(entry_lane_ids)
        queue: list[str] = list(entry_lane_ids)
        while queue:
            current = queue.pop(0)
            for succ in lane_successors_by_id.get(current, []):
                if succ in lane_id_set and succ not in visited:
                    visited.add(succ)
                    queue.append(succ)
        unreachable = sorted(lane_id_set.difference(visited))
        if unreachable:
            warnings.append(
                "unreachable lanes from entry graph: " + ", ".join(unreachable)
            )
            unreachable_lane_ids = unreachable

    successor_edge_count = sum(len(lane_successors_by_id.get(lane_id, [])) for lane_id in lane_ids)
    exit_lane_count = sum(1 for lane_id in lane_ids if not lane_successors_by_id.get(lane_id, []))
    routing_semantic_warning_count = (
        continuity_gap_warning_count
        + non_reciprocal_predecessor_warning_count
        + non_reciprocal_successor_warning_count
        + len(unreachable_lane_ids)
        + entry_lane_missing_warning_count
    )
    routing_semantic_status = "pass"
    if errors:
        routing_semantic_status = "fail"
    elif routing_semantic_warning_count > 0:
        routing_semantic_status = "warn"

    semantic_summary = {
        "lane_count": len(lane_ids),
        "successor_edge_count": successor_edge_count,
        "entry_lane_count": len(entry_lane_ids),
        "exit_lane_count": exit_lane_count,
        "continuity_gap_warning_count": continuity_gap_warning_count,
        "non_reciprocal_predecessor_warning_count": non_reciprocal_predecessor_warning_count,
        "non_reciprocal_successor_warning_count": non_reciprocal_successor_warning_count,
        "unreachable_lane_count": len(unreachable_lane_ids),
        "unreachable_lane_ids": unreachable_lane_ids,
        "entry_lane_missing_warning_count": entry_lane_missing_warning_count,
        "routing_semantic_warning_count": routing_semantic_warning_count,
        "routing_semantic_status": routing_semantic_status,
    }
    return errors, warnings, semantic_summary


def main() -> int:
    try:
        args = parse_args()
        map_path = Path(args.map).resolve()
        payload = _load_json_object(map_path, "canonical map")
        errors, warnings, semantic_summary = validate_map(payload)

        report = {
            "report_schema_version": CANONICAL_MAP_VALIDATION_REPORT_SCHEMA_VERSION_V0,
            "map_schema_version": str(payload.get("map_schema_version", "")).strip(),
            "map_path": str(map_path),
            "map_id": str(payload.get("map_id", "")).strip(),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
            "routing_semantic_summary": semantic_summary,
        }
        if args.report_out:
            report_path = Path(args.report_out).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            print(f"[ok] report={report_path}")

        if errors:
            for item in errors:
                print(f"[error] {item}")
            for item in warnings:
                print(f"[warn] {item}")
            return 1

        for item in warnings:
            print(f"[warn] {item}")
        print("[ok] canonical map validation passed")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] validate_canonical_map.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
