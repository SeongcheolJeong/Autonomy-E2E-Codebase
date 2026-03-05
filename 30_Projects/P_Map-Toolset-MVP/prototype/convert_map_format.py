#!/usr/bin/env python3
"""Minimal map format converter between simple_map_v0 and canonical_lane_graph_v0."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


SIMPLE_MAP_SCHEMA_VERSION_V0 = "simple_map_v0"
CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0 = "canonical_lane_graph_v0"
ERROR_SOURCE = "convert_map_format.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert map formats for Map Toolset v0 prototype")
    parser.add_argument("--input", required=True, help="Input JSON path")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument(
        "--to-format",
        required=True,
        choices=["canonical", "simple"],
        help="Target format",
    )
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _as_point_list(value: Any, label: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError(f"{label} must be a list with at least 2 points")
    result: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{label} point must be [x, y]")
        result.append([float(point[0]), float(point[1])])
    return result


def _simple_to_canonical(payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("map_schema_version", "")) != SIMPLE_MAP_SCHEMA_VERSION_V0:
        raise ValueError(
            "simple map map_schema_version must be "
            f"{SIMPLE_MAP_SCHEMA_VERSION_V0}"
        )
    roads = payload.get("roads", [])
    if not isinstance(roads, list) or len(roads) == 0:
        raise ValueError("simple map roads must be a non-empty list")

    lanes: list[dict[str, Any]] = []
    for road in roads:
        if not isinstance(road, dict):
            raise ValueError("each road entry must be an object")
        road_id = str(road.get("road_id", "")).strip()
        if not road_id:
            raise ValueError("road_id must be a non-empty string")
        centerline = _as_point_list(road.get("centerline", []), f"road {road_id} centerline")
        lanes.append(
            {
                "lane_id": road_id,
                "lane_type": str(road.get("lane_type", "driving")),
                "speed_limit_kph": float(road.get("speed_limit_kph", 50.0)),
                "centerline_m": [{"x_m": point[0], "y_m": point[1]} for point in centerline],
                "predecessor_lane_ids": [str(item) for item in road.get("predecessor_lane_ids", [])],
                "successor_lane_ids": [str(item) for item in road.get("successor_lane_ids", [])],
            }
        )
    return {
        "map_schema_version": CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0,
        "map_id": str(payload.get("map_id", "map_unknown")),
        "lanes": lanes,
    }


def _canonical_to_simple(payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("map_schema_version", "")) != CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0:
        raise ValueError(
            "canonical map map_schema_version must be "
            f"{CANONICAL_LANE_GRAPH_SCHEMA_VERSION_V0}"
        )
    lanes = payload.get("lanes", [])
    if not isinstance(lanes, list) or len(lanes) == 0:
        raise ValueError("canonical map lanes must be a non-empty list")

    roads: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            raise ValueError("each lane entry must be an object")
        lane_id = str(lane.get("lane_id", "")).strip()
        if not lane_id:
            raise ValueError("lane_id must be a non-empty string")
        centerline_m = lane.get("centerline_m", [])
        if not isinstance(centerline_m, list) or len(centerline_m) < 2:
            raise ValueError(f"lane {lane_id} centerline_m must contain at least 2 points")
        centerline: list[list[float]] = []
        for point in centerline_m:
            if not isinstance(point, dict) or "x_m" not in point or "y_m" not in point:
                raise ValueError(f"lane {lane_id} centerline_m point must include x_m/y_m")
            centerline.append([float(point["x_m"]), float(point["y_m"])])
        roads.append(
            {
                "road_id": lane_id,
                "lane_type": str(lane.get("lane_type", "driving")),
                "speed_limit_kph": float(lane.get("speed_limit_kph", 50.0)),
                "centerline": centerline,
                "predecessor_lane_ids": [str(item) for item in lane.get("predecessor_lane_ids", [])],
                "successor_lane_ids": [str(item) for item in lane.get("successor_lane_ids", [])],
            }
        )
    return {
        "map_schema_version": SIMPLE_MAP_SCHEMA_VERSION_V0,
        "map_id": str(payload.get("map_id", "map_unknown")),
        "roads": roads,
    }


def main() -> int:
    try:
        args = parse_args()
        input_path = Path(args.input).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _load_json_object(input_path, "input")

        if args.to_format == "canonical":
            converted = _simple_to_canonical(payload)
        else:
            converted = _canonical_to_simple(payload)

        out_path.write_text(json.dumps(converted, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] to_format={args.to_format}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] convert_map_format.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
