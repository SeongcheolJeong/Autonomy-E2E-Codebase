#!/usr/bin/env python3
"""Generate minimal OpenSCENARIO/OpenDRIVE artifacts from runtime launch-manifest inputs."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


SIM_RUNTIME_INTEROP_EXPORT_SCHEMA_VERSION_V0 = "sim_runtime_interop_export_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
ALLOWED_RUNTIMES = {"awsim", "carla"}
ERROR_SOURCE = "sim_runtime_interop_export_runner.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate OpenSCENARIO/OpenDRIVE runtime interop references from launch manifest"
    )
    parser.add_argument("--runtime", required=True, help="Runtime target: awsim|carla")
    parser.add_argument("--launch-manifest", required=True, help="Runtime launch-manifest JSON path")
    parser.add_argument("--xosc-out", required=True, help="Output OpenSCENARIO (.xosc) path")
    parser.add_argument("--xodr-out", required=True, help="Output OpenDRIVE (.xodr) path")
    parser.add_argument(
        "--road-length-scale",
        default="1.0",
        help="Scale factor for generated OpenDRIVE road length (> 0)",
    )
    parser.add_argument("--out", required=True, help="Output export report JSON path")
    return parser.parse_args()


def _normalize_runtime(value: str) -> str:
    runtime = str(value).strip().lower()
    if runtime not in ALLOWED_RUNTIMES:
        allowed = ", ".join(sorted(ALLOWED_RUNTIMES))
        raise ValueError(f"runtime must be one of: {allowed}; got: {value}")
    return runtime


def _parse_positive_float(value: str, *, field: str) -> float:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} must be a positive number")
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a positive number, got: {value}") from exc
    if parsed <= 0.0:
        raise ValueError(f"{field} must be > 0, got: {parsed}")
    return parsed


def _load_json_object(path: Path, *, subject: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return payload


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _indent_tree(elem: ET.Element) -> None:
    # Keep generated XML readable for operator debugging and review.
    ET.indent(elem, space="  ")


def _as_actor_name(actor: dict[str, Any], *, index: int) -> str:
    actor_id = str(actor.get("actor_id", "")).strip()
    if actor_id:
        return actor_id
    role = str(actor.get("role", "")).strip()
    if role:
        return role
    return f"actor_{index:03d}"


def main() -> int:
    try:
        args = parse_args()
        runtime = _normalize_runtime(args.runtime)
        road_length_scale = _parse_positive_float(args.road_length_scale, field="road-length-scale")
        launch_manifest_path = Path(args.launch_manifest).resolve()
        xosc_out_path = Path(args.xosc_out).resolve()
        xodr_out_path = Path(args.xodr_out).resolve()
        out_path = Path(args.out).resolve()

        xosc_out_path.parent.mkdir(parents=True, exist_ok=True)
        xodr_out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        launch_manifest_payload = _load_json_object(launch_manifest_path, subject="launch manifest")
        launch_schema = str(
            launch_manifest_payload.get("sim_runtime_launch_manifest_schema_version", "")
        ).strip()
        if launch_schema and launch_schema != SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0:
            raise ValueError(
                "launch manifest schema must be "
                f"{SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0}, got: {launch_schema}"
            )
        launch_runtime = str(launch_manifest_payload.get("runtime", "")).strip().lower()
        if launch_runtime and launch_runtime != runtime:
            raise ValueError(
                f"runtime mismatch between --runtime ({runtime}) and launch manifest ({launch_runtime})"
            )

        actors_raw = launch_manifest_payload.get("actors", [])
        if not isinstance(actors_raw, list) or not actors_raw:
            raise ValueError("launch manifest actors must be a non-empty list")
        actors = [row for row in actors_raw if isinstance(row, dict)]
        if not actors:
            raise ValueError("launch manifest actors must contain object rows")

        sensor_streams_raw = launch_manifest_payload.get("sensor_streams", [])
        if not isinstance(sensor_streams_raw, list) or not sensor_streams_raw:
            raise ValueError("launch manifest sensor_streams must be a non-empty list")
        sensor_streams = [row for row in sensor_streams_raw if isinstance(row, dict)]
        if not sensor_streams:
            raise ValueError("launch manifest sensor_streams must contain object rows")

        scene_block = launch_manifest_payload.get("scene", {})
        if not isinstance(scene_block, dict):
            raise ValueError("launch manifest scene must be an object")
        estimated_scene_frame_count = _safe_int(scene_block.get("estimated_scene_frame_count"), default=0)
        if estimated_scene_frame_count <= 0:
            execution_block = launch_manifest_payload.get("execution", {})
            if isinstance(execution_block, dict):
                estimated_scene_frame_count = _safe_int(
                    execution_block.get("frame_count_per_sensor"),
                    default=0,
                )
        estimated_scene_frame_count = max(1, estimated_scene_frame_count)
        road_length_m = max(20.0, float(estimated_scene_frame_count) * float(road_length_scale))

        generated_at = datetime.now(timezone.utc).isoformat()

        xosc_root = ET.Element("OpenSCENARIO")
        xosc_header = ET.SubElement(
            xosc_root,
            "FileHeader",
            {
                "revMajor": "1",
                "revMinor": "1",
                "date": generated_at,
                "description": "runtime launch-manifest derived interop export",
            },
        )
        _ = xosc_header
        xosc_entities = ET.SubElement(xosc_root, "Entities")
        for index, actor in enumerate(actors, start=1):
            actor_name = _as_actor_name(actor, index=index)
            scenario_object = ET.SubElement(xosc_entities, "ScenarioObject", {"name": actor_name})
            vehicle_name = str(actor.get("actor_type", "")).strip() or str(actor.get("role", "")).strip() or "vehicle"
            ET.SubElement(scenario_object, "Vehicle", {"name": vehicle_name})
        storyboard = ET.SubElement(xosc_root, "Storyboard")
        ET.SubElement(storyboard, "Story", {"name": "story_main"})
        _indent_tree(xosc_root)
        xosc_tree = ET.ElementTree(xosc_root)
        xosc_tree.write(xosc_out_path, encoding="utf-8", xml_declaration=True)

        xodr_root = ET.Element("OpenDRIVE")
        ET.SubElement(
            xodr_root,
            "header",
            {
                "revMajor": "1",
                "revMinor": "6",
                "name": "runtime_generated",
                "version": "0.1",
                "date": generated_at,
            },
        )
        road = ET.SubElement(
            xodr_root,
            "road",
            {
                "name": "runtime_main",
                "length": f"{road_length_m:.3f}",
                "id": "1",
                "junction": "-1",
            },
        )
        ET.SubElement(road, "link")
        plan_view = ET.SubElement(road, "planView")
        geometry = ET.SubElement(
            plan_view,
            "geometry",
            {
                "s": "0.0",
                "x": "0.0",
                "y": "0.0",
                "hdg": "0.0",
                "length": f"{road_length_m:.3f}",
            },
        )
        ET.SubElement(geometry, "line")
        lanes = ET.SubElement(road, "lanes")
        lane_section = ET.SubElement(lanes, "laneSection", {"s": "0.0"})
        center = ET.SubElement(lane_section, "center")
        ET.SubElement(center, "lane", {"id": "0", "type": "none", "level": "false"})
        _indent_tree(xodr_root)
        xodr_tree = ET.ElementTree(xodr_root)
        xodr_tree.write(xodr_out_path, encoding="utf-8", xml_declaration=True)

        output_payload = {
            "sim_runtime_interop_export_schema_version": SIM_RUNTIME_INTEROP_EXPORT_SCHEMA_VERSION_V0,
            "generated_at": generated_at,
            "runtime": runtime,
            "launch_manifest_path": str(launch_manifest_path),
            "launch_manifest_schema_version": launch_schema,
            "xosc_path": str(xosc_out_path),
            "xodr_path": str(xodr_out_path),
            "export_status": "pass",
            "actor_count_manifest": int(len(actors)),
            "sensor_stream_count_manifest": int(len(sensor_streams)),
            "estimated_scene_frame_count": int(estimated_scene_frame_count),
            "generated_road_length_m": float(round(road_length_m, 6)),
            "xosc_entity_count": int(len(actors)),
            "xodr_road_count": 1,
            "runner_host": str(platform.node()).strip(),
            "runner_platform": str(platform.platform()).strip(),
            "runner_python": str(sys.version.split()[0]).strip(),
        }
        out_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        print(f"[ok] runtime={runtime}")
        print("[ok] interop_export_status=pass")
        print(f"[ok] xosc={xosc_out_path}")
        print(f"[ok] xodr={xodr_out_path}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] sim_runtime_interop_export_runner.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
