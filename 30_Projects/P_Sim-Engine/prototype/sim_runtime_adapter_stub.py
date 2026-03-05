#!/usr/bin/env python3
"""Emit runtime-rendering adapter and launch-manifest contracts for AWSIM/CARLA."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


SIM_RUNTIME_ADAPTER_SCHEMA_VERSION_V0 = "sim_runtime_adapter_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
SENSOR_RIG_SCHEMA_VERSION_V0 = "sensor_rig_v0"
LOG_SCENE_SCHEMA_VERSION_V0 = "log_scene_v0"
SUPPORTED_SENSOR_TYPES = {"camera", "lidar", "radar"}
ALLOWED_RUNTIMES = {"awsim", "carla"}
ALLOWED_MODES = {"headless", "interactive"}
ERROR_SOURCE = "sim_runtime_adapter_stub.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build runtime-render adapter report from scene and sensor rig")
    parser.add_argument("--runtime", required=True, help="Runtime target: awsim|carla")
    parser.add_argument("--scene", required=True, help="Scene/log JSON path")
    parser.add_argument("--sensor-rig", required=True, help="Sensor rig JSON path")
    parser.add_argument("--mode", default="headless", help="Runtime mode: headless|interactive")
    parser.add_argument("--frame-count", default="30", help="Nominal frame count per sensor")
    parser.add_argument(
        "--launch-manifest-out",
        default="",
        help="Optional runtime launch-manifest output path (defaults next to --out)",
    )
    parser.add_argument("--out", required=True, help="Output adapter report JSON path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _normalize_runtime(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in ALLOWED_RUNTIMES:
        allowed = ", ".join(sorted(ALLOWED_RUNTIMES))
        raise ValueError(f"runtime must be one of: {allowed}; got: {value}")
    return normalized


def _normalize_mode(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in ALLOWED_MODES:
        allowed = ", ".join(sorted(ALLOWED_MODES))
        raise ValueError(f"mode must be one of: {allowed}; got: {value}")
    return normalized


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


def _parse_optional_non_negative_float(value: Any, *, field: str) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a non-negative float, got: {value}") from exc
    if parsed < 0.0:
        raise ValueError(f"{field} must be >= 0, got: {parsed}")
    return parsed


def _validate_sensor_rig(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if str(payload.get("rig_schema_version", "")).strip() != SENSOR_RIG_SCHEMA_VERSION_V0:
        raise ValueError(f"rig_schema_version must be {SENSOR_RIG_SCHEMA_VERSION_V0}")
    sensors_raw = payload.get("sensors", [])
    if not isinstance(sensors_raw, list) or not sensors_raw:
        raise ValueError("sensor rig sensors must be a non-empty list")
    sensors = [row for row in sensors_raw if isinstance(row, dict)]
    if not sensors:
        raise ValueError("sensor rig sensors must contain object rows")
    for row in sensors:
        sensor_id = str(row.get("sensor_id", "")).strip()
        sensor_type = str(row.get("sensor_type", "")).strip().lower()
        if not sensor_id:
            raise ValueError("sensor_id must be a non-empty string")
        if sensor_type not in SUPPORTED_SENSOR_TYPES:
            raise ValueError(f"unsupported sensor_type: {sensor_type}")
    return sensors


def _validate_scene(scene_payload: dict[str, Any]) -> dict[str, Any]:
    schema = str(scene_payload.get("log_scene_schema_version", "")).strip()
    if schema and schema != LOG_SCENE_SCHEMA_VERSION_V0:
        raise ValueError(f"log_scene_schema_version must be {LOG_SCENE_SCHEMA_VERSION_V0}")
    duration_sec = _parse_optional_non_negative_float(scene_payload.get("duration_sec"), field="duration_sec")
    dt_sec = _parse_optional_non_negative_float(scene_payload.get("dt_sec"), field="dt_sec")
    if duration_sec > 0.0 and dt_sec <= 0.0:
        raise ValueError("dt_sec must be > 0 when duration_sec > 0")
    log_id = str(scene_payload.get("log_id", "")).strip() or "unknown_log_scene"
    map_id = str(scene_payload.get("map_id", "")).strip()
    map_version = str(scene_payload.get("map_version", "")).strip()
    return {
        "log_id": log_id,
        "map_id": map_id,
        "map_version": map_version,
        "duration_sec": duration_sec,
        "dt_sec": dt_sec,
    }


def _extract_scene_actors(scene_payload: dict[str, Any]) -> list[dict[str, Any]]:
    actors_out: list[dict[str, Any]] = []
    raw_dynamic_agents = scene_payload.get("dynamic_agents", [])
    raw_actors = scene_payload.get("actors", [])
    source_rows: list[dict[str, Any]] = []
    if isinstance(raw_dynamic_agents, list):
        source_rows.extend([item for item in raw_dynamic_agents if isinstance(item, dict)])
    if isinstance(raw_actors, list):
        source_rows.extend([item for item in raw_actors if isinstance(item, dict)])
    seen_actor_ids: set[str] = set()
    for row in source_rows:
        actor_id = str(row.get("actor_id", "")).strip() or str(row.get("agent_id", "")).strip()
        if not actor_id:
            continue
        if actor_id in seen_actor_ids:
            continue
        seen_actor_ids.add(actor_id)
        actor_role = "ego" if actor_id.lower() == "ego" else "traffic"
        speed_value = row.get("speed_mps")
        speed_mps = _parse_optional_non_negative_float(speed_value, field=f"{actor_id}.speed_mps")
        position_raw = row.get("position_m", [])
        position_m = [0.0, 0.0]
        if isinstance(position_raw, list) and len(position_raw) >= 2:
            try:
                position_m = [float(position_raw[0]), float(position_raw[1])]
            except (TypeError, ValueError):
                position_m = [0.0, 0.0]
        actors_out.append(
            {
                "actor_id": actor_id,
                "role": actor_role,
                "initial_speed_mps": speed_mps,
                "initial_position_m": position_m,
            }
        )
    if actors_out:
        return actors_out

    ego_speed = _parse_optional_non_negative_float(scene_payload.get("ego_initial_speed_mps"), field="ego_initial_speed_mps")
    lead_gap = _parse_optional_non_negative_float(
        scene_payload.get("lead_vehicle_initial_gap_m"),
        field="lead_vehicle_initial_gap_m",
    )
    lead_speed = _parse_optional_non_negative_float(
        scene_payload.get("lead_vehicle_speed_mps"),
        field="lead_vehicle_speed_mps",
    )
    actors_out.append(
        {
            "actor_id": "ego",
            "role": "ego",
            "initial_speed_mps": ego_speed,
            "initial_position_m": [0.0, 0.0],
        }
    )
    if lead_gap > 0.0 or lead_speed > 0.0:
        actors_out.append(
            {
                "actor_id": "lead_vehicle",
                "role": "traffic",
                "initial_speed_mps": lead_speed,
                "initial_position_m": [lead_gap, 0.0],
            }
        )
    return actors_out


def _default_simulated_fps(runtime: str, mode: str) -> float:
    if runtime == "awsim":
        return 20.0 if mode == "headless" else 15.0
    return 18.0 if mode == "headless" else 12.0


def _runtime_contract(runtime: str) -> dict[str, str]:
    if runtime == "awsim":
        return {
            "runtime_entrypoint": "tier4_awsim_bridge",
            "reference_repo": "tier4/AWSIM",
            "bridge_contract": "ros2_bridge_stub_v0",
        }
    return {
        "runtime_entrypoint": "carla_python_api_bridge",
        "reference_repo": "carla-simulator/carla",
        "bridge_contract": "python_api_bridge_stub_v0",
    }


def _resolve_launch_manifest_path(runtime: str, out_path: Path, launch_manifest_out: str) -> Path:
    launch_manifest_text = str(launch_manifest_out).strip()
    if launch_manifest_text:
        return Path(launch_manifest_text).resolve()
    return out_path.with_name(f"{out_path.stem}.{runtime}.launch_manifest.json")


def _resolve_stream_channel(*, runtime: str, sensor_type: str, sensor_id: str) -> str:
    if runtime == "awsim":
        suffix = {
            "camera": "image_raw",
            "lidar": "pointcloud",
            "radar": "targets",
        }[sensor_type]
        return f"/sensing/{sensor_type}/{sensor_id}/{suffix}"
    suffix = {
        "camera": "image",
        "lidar": "pointcloud",
        "radar": "targets",
    }[sensor_type]
    return f"/carla/{sensor_id}/{suffix}"


def _build_sensor_streams(*, runtime: str, sensors: list[dict[str, Any]]) -> list[dict[str, str]]:
    streams: list[dict[str, str]] = []
    for row in sensors:
        sensor_id = str(row.get("sensor_id", "")).strip()
        sensor_type = str(row.get("sensor_type", "")).strip().lower()
        streams.append(
            {
                "sensor_id": sensor_id,
                "sensor_type": sensor_type,
                "frame_id": f"{sensor_id}_frame",
                "stream_channel": _resolve_stream_channel(
                    runtime=runtime,
                    sensor_type=sensor_type,
                    sensor_id=sensor_id,
                ),
            }
        )
    return streams


def main() -> int:
    try:
        args = parse_args()
        runtime = _normalize_runtime(args.runtime)
        mode = _normalize_mode(args.mode)
        frame_count = _parse_positive_int(args.frame_count, field="frame-count")
        scene_path = Path(args.scene).resolve()
        sensor_rig_path = Path(args.sensor_rig).resolve()
        out_path = Path(args.out).resolve()
        launch_manifest_path = _resolve_launch_manifest_path(runtime, out_path, args.launch_manifest_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        launch_manifest_path.parent.mkdir(parents=True, exist_ok=True)

        scene_payload = _load_json_object(scene_path, "scene")
        scene_metadata = _validate_scene(scene_payload)
        sensor_rig_payload = _load_json_object(sensor_rig_path, "sensor rig")
        sensors = _validate_sensor_rig(sensor_rig_payload)
        actors = _extract_scene_actors(scene_payload)
        actor_count = len(actors)
        sensor_count = len(sensors)
        render_frame_count = frame_count * sensor_count

        runtime_contract = _runtime_contract(runtime)
        simulated_fps = _default_simulated_fps(runtime, mode)
        scene_duration_sec = float(scene_metadata.get("duration_sec", 0.0) or 0.0)
        scene_dt_sec = float(scene_metadata.get("dt_sec", 0.0) or 0.0)
        estimated_scene_frame_count = frame_count
        if scene_duration_sec > 0.0 and scene_dt_sec > 0.0:
            estimated_scene_frame_count = max(1, int(round(scene_duration_sec / scene_dt_sec)))
        sensor_streams = _build_sensor_streams(runtime=runtime, sensors=sensors)

        launch_payload = {
            "sim_runtime_launch_manifest_schema_version": SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
            "mode": mode,
            "scene": {
                "scene_path": str(scene_path),
                "log_id": str(scene_metadata.get("log_id", "")).strip(),
                "map_id": str(scene_metadata.get("map_id", "")).strip(),
                "map_version": str(scene_metadata.get("map_version", "")).strip(),
                "duration_sec": scene_duration_sec,
                "dt_sec": scene_dt_sec,
                "estimated_scene_frame_count": estimated_scene_frame_count,
            },
            "actors": actors,
            "sensor_streams": sensor_streams,
            "execution": {
                "frame_count_per_sensor": frame_count,
                "render_frame_count": render_frame_count,
                "simulated_fps": simulated_fps,
            },
            "runtime_contract": runtime_contract,
        }
        launch_manifest_path.write_text(
            json.dumps(launch_payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

        output_payload = {
            "sim_runtime_adapter_schema_version": SIM_RUNTIME_ADAPTER_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
            "mode": mode,
            "scene_path": str(scene_path),
            "sensor_rig_path": str(sensor_rig_path),
            "scene_log_id": str(scene_metadata.get("log_id", "")).strip(),
            "scene_map_ref": {
                "map_id": str(scene_metadata.get("map_id", "")).strip(),
                "map_version": str(scene_metadata.get("map_version", "")).strip(),
            },
            "actor_count": actor_count,
            "sensor_count": sensor_count,
            "frame_count_per_sensor": frame_count,
            "render_frame_count": render_frame_count,
            "simulated_fps": simulated_fps,
            "estimated_scene_frame_count": estimated_scene_frame_count,
            "launch_manifest_out": str(launch_manifest_path),
            "runtime_contract": runtime_contract,
        }
        out_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] runtime={runtime} mode={mode}")
        print(f"[ok] render_frame_count={render_frame_count}")
        print(f"[ok] launch_manifest_out={launch_manifest_path}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] sim_runtime_adapter_stub.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
