#!/usr/bin/env python3
"""Validate OpenSCENARIO/OpenDRIVE interop contract against runtime launch-manifest inputs."""

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


SIM_RUNTIME_INTEROP_CONTRACT_SCHEMA_VERSION_V0 = "sim_runtime_interop_contract_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0 = "sim_runtime_probe_v0"
ALLOWED_RUNTIMES = {"awsim", "carla"}
ERROR_SOURCE = "sim_runtime_interop_contract_runner.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate OpenSCENARIO/OpenDRIVE runtime interop contract")
    parser.add_argument("--runtime", required=True, help="Runtime target: awsim|carla")
    parser.add_argument("--launch-manifest", required=True, help="Runtime launch-manifest JSON path")
    parser.add_argument("--xosc", required=True, help="OpenSCENARIO (.xosc) path")
    parser.add_argument("--xodr", required=True, help="OpenDRIVE (.xodr) path")
    parser.add_argument("--probe-report", default="", help="Optional runtime probe report JSON path")
    parser.add_argument(
        "--require-runtime-ready",
        action="store_true",
        help="Fail when runtime probe report indicates runtime is not ready",
    )
    parser.add_argument("--step-dt-sec", default="0.1", help="Simulation step dt (seconds, > 0)")
    parser.add_argument("--max-steps", default="20", help="Maximum number of execution steps (> 0)")
    parser.add_argument("--out", required=True, help="Output contract report JSON path")
    return parser.parse_args()


def _normalize_runtime(value: str) -> str:
    runtime = str(value).strip().lower()
    if runtime not in ALLOWED_RUNTIMES:
        allowed = ", ".join(sorted(ALLOWED_RUNTIMES))
        raise ValueError(f"runtime must be one of: {allowed}; got: {value}")
    return runtime


def _load_json_object(path: Path, *, subject: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return payload


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


def _parse_positive_int(value: str, *, field: str) -> int:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a positive integer, got: {value}") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be > 0, got: {parsed}")
    return parsed


def _xml_local_name(tag: str) -> str:
    raw = str(tag).strip()
    if "}" in raw:
        return raw.rsplit("}", 1)[1].strip()
    return raw


def _parse_xml_root(path: Path, *, subject: str, expected_root: str) -> ET.Element:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"{subject} is not valid XML: {path}") from exc
    root = tree.getroot()
    root_name = _xml_local_name(root.tag)
    if root_name != expected_root:
        raise ValueError(f"{subject} root tag must be {expected_root}, got: {root_name or '<empty>'}")
    return root


def _count_xml_nodes(root: ET.Element, *, local_names: set[str]) -> int:
    target = {str(name).strip() for name in local_names if str(name).strip()}
    if not target:
        return 0
    count = 0
    for node in root.iter():
        if _xml_local_name(node.tag) in target:
            count += 1
    return count


def main() -> int:
    try:
        args = parse_args()
        runtime = _normalize_runtime(args.runtime)
        step_dt_sec = _parse_positive_float(args.step_dt_sec, field="step-dt-sec")
        max_steps = _parse_positive_int(args.max_steps, field="max-steps")
        launch_manifest_path = Path(args.launch_manifest).resolve()
        xosc_path = Path(args.xosc).resolve()
        xodr_path = Path(args.xodr).resolve()
        probe_report_text = str(args.probe_report).strip()
        probe_report_path = Path(probe_report_text).resolve() if probe_report_text else None
        out_path = Path(args.out).resolve()
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
        actor_count = int(len(actors))

        sensor_streams_raw = launch_manifest_payload.get("sensor_streams", [])
        if not isinstance(sensor_streams_raw, list) or not sensor_streams_raw:
            raise ValueError("launch manifest sensor_streams must be a non-empty list")
        sensor_streams = [row for row in sensor_streams_raw if isinstance(row, dict)]
        if not sensor_streams:
            raise ValueError("launch manifest sensor_streams must contain object rows")
        sensor_stream_count = int(len(sensor_streams))

        scene_block = launch_manifest_payload.get("scene", {})
        if not isinstance(scene_block, dict):
            raise ValueError("launch manifest scene must be an object")
        estimated_scene_frame_count = int(scene_block.get("estimated_scene_frame_count", 0) or 0)
        if estimated_scene_frame_count <= 0:
            execution_block = launch_manifest_payload.get("execution", {})
            if isinstance(execution_block, dict):
                estimated_scene_frame_count = int(execution_block.get("frame_count_per_sensor", 0) or 0)
        estimated_scene_frame_count = max(1, estimated_scene_frame_count)

        xosc_root = _parse_xml_root(xosc_path, subject="OpenSCENARIO", expected_root="OpenSCENARIO")
        xodr_root = _parse_xml_root(xodr_path, subject="OpenDRIVE", expected_root="OpenDRIVE")
        xosc_entity_count = _count_xml_nodes(xosc_root, local_names={"ScenarioObject"})
        xosc_story_count = _count_xml_nodes(xosc_root, local_names={"Story"})
        xodr_road_count = _count_xml_nodes(xodr_root, local_names={"road"})
        xodr_junction_count = _count_xml_nodes(xodr_root, local_names={"junction"})
        if xosc_entity_count <= 0:
            raise ValueError("OpenSCENARIO must contain at least one ScenarioObject")
        if xodr_road_count <= 0:
            raise ValueError("OpenDRIVE must contain at least one road")

        probe_report_schema = ""
        probe_report_loaded = False
        runtime_available = True
        probe_executed = False
        probe_returncode_acceptable = True
        if probe_report_path is not None:
            probe_report_payload = _load_json_object(probe_report_path, subject="probe report")
            probe_report_schema = str(
                probe_report_payload.get("sim_runtime_probe_schema_version", "")
            ).strip()
            if probe_report_schema and probe_report_schema != SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0:
                raise ValueError(
                    "probe report schema must be "
                    f"{SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0}, got: {probe_report_schema}"
                )
            probe_runtime = str(probe_report_payload.get("runtime", "")).strip().lower()
            if probe_runtime and probe_runtime != runtime:
                raise ValueError(
                    f"runtime mismatch between --runtime ({runtime}) and probe report ({probe_runtime})"
                )
            probe_report_loaded = True
            runtime_available = bool(probe_report_payload.get("runtime_available", False))
            probe_executed = bool(probe_report_payload.get("probe_executed", False))
            probe_returncode_acceptable = bool(probe_report_payload.get("probe_returncode_acceptable", False))

        runtime_ready = bool(runtime_available) and (not probe_executed or probe_returncode_acceptable)
        if bool(args.require_runtime_ready) and probe_report_path is None:
            raise ValueError("--require-runtime-ready requires --probe-report")
        if bool(args.require_runtime_ready) and not runtime_ready:
            raise ValueError(
                "runtime is not ready for interop contract execution"
                f" (runtime_available={str(runtime_available).lower()},"
                f" probe_executed={str(probe_executed).lower()},"
                f" probe_returncode_acceptable={str(probe_returncode_acceptable).lower()})"
            )

        imported_actor_count = min(actor_count, xosc_entity_count)
        imported_actor_count = max(1, imported_actor_count)
        executed_step_count = min(max_steps, max(1, int(estimated_scene_frame_count)))
        sim_duration_sec = round(executed_step_count * step_dt_sec, 6)

        output_payload = {
            "sim_runtime_interop_contract_schema_version": SIM_RUNTIME_INTEROP_CONTRACT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
            "launch_manifest_path": str(launch_manifest_path),
            "launch_manifest_schema_version": launch_schema,
            "xosc_path": str(xosc_path),
            "xosc_root_tag": "OpenSCENARIO",
            "xosc_entity_count": int(xosc_entity_count),
            "xosc_story_count": int(xosc_story_count),
            "xodr_path": str(xodr_path),
            "xodr_root_tag": "OpenDRIVE",
            "xodr_road_count": int(xodr_road_count),
            "xodr_junction_count": int(xodr_junction_count),
            "actor_count_manifest": int(actor_count),
            "sensor_stream_count_manifest": int(sensor_stream_count),
            "imported_actor_count": int(imported_actor_count),
            "probe_report_path": str(probe_report_path) if probe_report_path is not None else "",
            "probe_report_schema_version": probe_report_schema,
            "probe_report_loaded": bool(probe_report_loaded),
            "require_runtime_ready": bool(args.require_runtime_ready),
            "runtime_available": bool(runtime_available),
            "probe_executed": bool(probe_executed),
            "probe_returncode_acceptable": bool(probe_returncode_acceptable),
            "runtime_ready": bool(runtime_ready),
            "interop_contract_status": "pass",
            "estimated_scene_frame_count": int(estimated_scene_frame_count),
            "executed_step_count": int(executed_step_count),
            "step_dt_sec": float(step_dt_sec),
            "sim_duration_sec": float(sim_duration_sec),
            "runner_host": str(platform.node()).strip(),
            "runner_platform": str(platform.platform()).strip(),
            "runner_python": str(sys.version.split()[0]).strip(),
        }
        out_path.write_text(
            json.dumps(output_payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        print(f"[ok] runtime={runtime}")
        print(f"[ok] runtime_ready={str(runtime_ready).lower()}")
        print("[ok] interop_contract_status=pass")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError, ET.ParseError) as exc:
        message = str(exc)
        print(f"[error] sim_runtime_interop_contract_runner.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
