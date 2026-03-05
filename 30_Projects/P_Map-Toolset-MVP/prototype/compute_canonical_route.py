#!/usr/bin/env python3
"""Compute an entry-to-exit route on canonical_lane_graph_v0 maps."""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0 = "canonical_lane_graph_v0"
CANONICAL_MAP_ROUTE_REPORT_SCHEMA_VERSION_V0 = "canonical_map_route_report_v0"
ERROR_SOURCE = "compute_canonical_route.py"
ERROR_PHASE = "resolve_inputs"
ROUTE_COST_MODE_HOPS = "hops"
ROUTE_COST_MODE_LENGTH = "length"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute shortest route on canonical_lane_graph_v0")
    parser.add_argument("--map", required=True, help="Canonical map JSON path")
    parser.add_argument("--entry-lane-id", default="", help="Optional explicit entry lane id")
    parser.add_argument("--exit-lane-id", default="", help="Optional explicit exit lane id")
    parser.add_argument(
        "--via-lane-id",
        action="append",
        default=[],
        help="Optional via lane id (repeatable) that route must pass through in order",
    )
    parser.add_argument(
        "--cost-mode",
        choices=[ROUTE_COST_MODE_HOPS, ROUTE_COST_MODE_LENGTH],
        default=ROUTE_COST_MODE_HOPS,
        help="Route optimization objective: hops or length",
    )
    parser.add_argument("--report-out", default="", help="Optional route report output path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _as_centerline_points(*, lane_id: str, value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError(f"lane {lane_id} centerline_m must have at least 2 points")
    points: list[tuple[float, float]] = []
    for point_index, point in enumerate(value):
        if not isinstance(point, dict):
            raise ValueError(f"lane {lane_id} centerline_m[{point_index}] must be an object")
        if "x_m" not in point or "y_m" not in point:
            raise ValueError(f"lane {lane_id} centerline_m[{point_index}] must include x_m/y_m")
        try:
            x_m = float(point["x_m"])
            y_m = float(point["y_m"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"lane {lane_id} centerline_m[{point_index}] x_m/y_m must be numeric") from exc
        points.append((x_m, y_m))
    return points


def _as_lane_refs(*, lane_id: str, field: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"lane {lane_id} {field} must be a list")
    refs: list[str] = []
    seen: set[str] = set()
    for ref_index, ref in enumerate(value):
        ref_id = str(ref).strip()
        if not ref_id:
            raise ValueError(f"lane {lane_id} {field}[{ref_index}] must be non-empty")
        if ref_id == lane_id:
            raise ValueError(f"lane {lane_id} {field} cannot include self")
        if ref_id in seen:
            continue
        seen.add(ref_id)
        refs.append(ref_id)
    return refs


def _distance_m(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    return math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1])


def _lane_length_m(centerline: list[tuple[float, float]]) -> float:
    if len(centerline) < 2:
        return 0.0
    total = 0.0
    for idx in range(1, len(centerline)):
        total += _distance_m(centerline[idx - 1], centerline[idx])
    return float(total)


def _resolve_lane_id(*, requested: str, candidates: list[str], field: str) -> str:
    requested_text = str(requested).strip()
    if requested_text:
        if requested_text not in candidates:
            raise ValueError(f"{field} not found in map lanes: {requested_text}")
        return requested_text
    if not candidates:
        raise ValueError(f"{field} candidates are empty")
    return str(candidates[0])


def _normalize_via_lane_ids(
    *,
    via_values: list[str],
    start_lane_id: str,
    end_lane_id: str,
    lane_id_set: set[str],
) -> list[str]:
    via_lane_ids: list[str] = []
    previous = start_lane_id
    for idx, raw in enumerate(via_values):
        lane_id = str(raw).strip()
        if not lane_id:
            raise ValueError(f"via-lane-id[{idx}] must be non-empty")
        if lane_id not in lane_id_set:
            raise ValueError(f"via-lane-id not found in map lanes: {lane_id}")
        if lane_id == previous:
            continue
        if lane_id == end_lane_id:
            continue
        via_lane_ids.append(lane_id)
        previous = lane_id
    return via_lane_ids


def _shortest_path_hops(
    *,
    start_lane: str,
    end_lane: str,
    successors_by_id: dict[str, list[str]],
) -> tuple[list[str], int]:
    if start_lane == end_lane:
        return [start_lane], 1

    queue: deque[tuple[str, list[str]]] = deque([(start_lane, [start_lane])])
    visited: set[str] = {start_lane}
    while queue:
        lane_id, path = queue.popleft()
        for successor in successors_by_id.get(lane_id, []):
            if successor in visited:
                continue
            next_path = [*path, successor]
            if successor == end_lane:
                return next_path, len(visited) + 1
            visited.add(successor)
            queue.append((successor, next_path))
    return [], len(visited)


def _shortest_path_length(
    *,
    start_lane: str,
    end_lane: str,
    successors_by_id: dict[str, list[str]],
    lane_length_by_id: dict[str, float],
) -> tuple[list[str], int]:
    if start_lane == end_lane:
        return [start_lane], 1

    # Use Dijkstra with successor lane length as edge weight.
    frontier: list[tuple[float, str, list[str]]] = [(0.0, start_lane, [start_lane])]
    best_cost_by_lane: dict[str, float] = {start_lane: 0.0}
    expanded_lanes: set[str] = set()
    while frontier:
        current_cost, lane_id, path = heapq.heappop(frontier)
        if lane_id in expanded_lanes:
            continue
        best_known_cost = best_cost_by_lane.get(lane_id, float("inf"))
        if current_cost > best_known_cost:
            continue
        expanded_lanes.add(lane_id)
        if lane_id == end_lane:
            return path, len(expanded_lanes)
        for successor in successors_by_id.get(lane_id, []):
            next_cost = current_cost + max(0.0, float(lane_length_by_id.get(successor, 0.0)))
            if next_cost >= best_cost_by_lane.get(successor, float("inf")):
                continue
            best_cost_by_lane[successor] = next_cost
            heapq.heappush(frontier, (next_cost, successor, [*path, successor]))
    return [], len(expanded_lanes)


def main() -> int:
    try:
        args = parse_args()
        map_path = Path(args.map).resolve()
        payload = _load_json_object(map_path, "canonical map")
        if str(payload.get("map_schema_version", "")) != CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0:
            raise ValueError(
                f"map_schema_version must be {CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0}"
            )

        lanes_raw = payload.get("lanes", [])
        if not isinstance(lanes_raw, list) or not lanes_raw:
            raise ValueError("lanes must be a non-empty list")

        lane_ids: list[str] = []
        lane_centerline_by_id: dict[str, list[tuple[float, float]]] = {}
        lane_length_by_id: dict[str, float] = {}
        lane_predecessors_by_id: dict[str, list[str]] = {}
        lane_successors_by_id: dict[str, list[str]] = {}

        for idx, lane in enumerate(lanes_raw):
            if not isinstance(lane, dict):
                raise ValueError(f"lane[{idx}] must be an object")
            lane_id = str(lane.get("lane_id", "")).strip()
            if not lane_id:
                raise ValueError(f"lane[{idx}] lane_id must be non-empty")
            if lane_id in lane_centerline_by_id:
                raise ValueError(f"duplicate lane_id: {lane_id}")
            lane_ids.append(lane_id)
            lane_centerline_by_id[lane_id] = _as_centerline_points(
                lane_id=lane_id,
                value=lane.get("centerline_m", []),
            )
            lane_length_by_id[lane_id] = _lane_length_m(lane_centerline_by_id[lane_id])
            lane_predecessors_by_id[lane_id] = _as_lane_refs(
                lane_id=lane_id,
                field="predecessor_lane_ids",
                value=lane.get("predecessor_lane_ids", []),
            )
            lane_successors_by_id[lane_id] = _as_lane_refs(
                lane_id=lane_id,
                field="successor_lane_ids",
                value=lane.get("successor_lane_ids", []),
            )

        lane_id_set = set(lane_ids)
        for lane_id in lane_ids:
            for predecessor in lane_predecessors_by_id.get(lane_id, []):
                if predecessor not in lane_id_set:
                    raise ValueError(f"lane {lane_id} predecessor not found: {predecessor}")
            for successor in lane_successors_by_id.get(lane_id, []):
                if successor not in lane_id_set:
                    raise ValueError(f"lane {lane_id} successor not found: {successor}")

        entry_lane_ids = sorted(lane_id for lane_id in lane_ids if not lane_predecessors_by_id.get(lane_id, []))
        exit_lane_ids = sorted(lane_id for lane_id in lane_ids if not lane_successors_by_id.get(lane_id, []))
        selected_entry_lane_id = _resolve_lane_id(
            requested=args.entry_lane_id,
            candidates=entry_lane_ids if entry_lane_ids else sorted(lane_ids),
            field="entry-lane-id",
        )
        selected_exit_lane_id = _resolve_lane_id(
            requested=args.exit_lane_id,
            candidates=exit_lane_ids if exit_lane_ids else sorted(lane_ids),
            field="exit-lane-id",
        )
        via_lane_ids = _normalize_via_lane_ids(
            via_values=[str(item) for item in args.via_lane_id],
            start_lane_id=selected_entry_lane_id,
            end_lane_id=selected_exit_lane_id,
            lane_id_set=lane_id_set,
        )

        route_cost_mode = str(args.cost_mode).strip().lower()
        segment_nodes = [selected_entry_lane_id, *via_lane_ids, selected_exit_lane_id]
        segment_count = max(0, len(segment_nodes) - 1)
        route_segments: list[dict[str, Any]] = []
        route_lane_ids: list[str] = []
        visited_lane_count = 0
        for segment_index in range(segment_count):
            segment_start_lane_id = segment_nodes[segment_index]
            segment_end_lane_id = segment_nodes[segment_index + 1]
            if route_cost_mode == ROUTE_COST_MODE_LENGTH:
                segment_route_lane_ids, segment_visited_lane_count = _shortest_path_length(
                    start_lane=segment_start_lane_id,
                    end_lane=segment_end_lane_id,
                    successors_by_id=lane_successors_by_id,
                    lane_length_by_id=lane_length_by_id,
                )
            else:
                segment_route_lane_ids, segment_visited_lane_count = _shortest_path_hops(
                    start_lane=segment_start_lane_id,
                    end_lane=segment_end_lane_id,
                    successors_by_id=lane_successors_by_id,
                )
            if not segment_route_lane_ids:
                if segment_count <= 1:
                    raise ValueError(
                        f"no route found from entry={selected_entry_lane_id} "
                        f"to exit={selected_exit_lane_id}"
                    )
                raise ValueError(
                    f"no route found for segment {segment_index + 1}/{segment_count}: "
                    f"{segment_start_lane_id}->{segment_end_lane_id}"
                )
            if route_lane_ids:
                route_lane_ids.extend(segment_route_lane_ids[1:])
            else:
                route_lane_ids.extend(segment_route_lane_ids)
            visited_lane_count += int(segment_visited_lane_count)
            segment_total_length_m = sum(
                float(lane_length_by_id.get(lane_id, 0.0))
                for lane_id in segment_route_lane_ids
            )
            route_segments.append(
                {
                    "segment_index": int(segment_index + 1),
                    "start_lane_id": segment_start_lane_id,
                    "end_lane_id": segment_end_lane_id,
                    "segment_lane_ids": segment_route_lane_ids,
                    "segment_lane_count": int(len(segment_route_lane_ids)),
                    "segment_hop_count": int(max(0, len(segment_route_lane_ids) - 1)),
                    "segment_total_length_m": float(round(segment_total_length_m, 6)),
                    "visited_lane_count": int(segment_visited_lane_count),
                }
            )

        route_total_length_m = sum(
            float(lane_length_by_id.get(lane_id, 0.0))
            for lane_id in route_lane_ids
        )
        route_lane_count = len(route_lane_ids)
        route_hop_count = max(0, route_lane_count - 1)
        route_avg_lane_length_m = (
            float(route_total_length_m) / float(route_lane_count)
            if route_lane_count > 0
            else 0.0
        )

        route_summary = {
            "route_status": "pass",
            "selected_entry_lane_id": selected_entry_lane_id,
            "selected_exit_lane_id": selected_exit_lane_id,
            "route_lane_ids": route_lane_ids,
            "route_lane_count": int(route_lane_count),
            "route_hop_count": int(route_hop_count),
            "route_total_length_m": float(round(route_total_length_m, 6)),
            "route_avg_lane_length_m": float(round(route_avg_lane_length_m, 6)),
            "route_cost_mode": route_cost_mode,
            "route_cost_value": (
                int(route_hop_count)
                if route_cost_mode == ROUTE_COST_MODE_HOPS
                else float(round(route_total_length_m, 6))
            ),
            "via_lane_ids_input": via_lane_ids,
            "route_segment_count": int(segment_count),
            "route_segments": route_segments,
            "visited_lane_count": int(visited_lane_count),
        }

        report = {
            "report_schema_version": CANONICAL_MAP_ROUTE_REPORT_SCHEMA_VERSION_V0,
            "map_schema_version": str(payload.get("map_schema_version", "")).strip(),
            "map_path": str(map_path),
            "map_id": str(payload.get("map_id", "")).strip(),
            "entry_lane_ids": entry_lane_ids,
            "exit_lane_ids": exit_lane_ids,
            **route_summary,
            "route_summary": route_summary,
        }

        report_out = str(args.report_out).strip()
        if report_out:
            report_path = Path(report_out).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
            print(f"[ok] report={report_path}")

        print("[ok] route_status=pass")
        print(
            f"[ok] route={selected_entry_lane_id}->{selected_exit_lane_id} "
            f"lanes={route_lane_count} hops={route_hop_count} cost_mode={route_cost_mode}"
        )
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] compute_canonical_route.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
