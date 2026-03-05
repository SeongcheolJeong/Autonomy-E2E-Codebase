#!/usr/bin/env python3
"""Render a minimal neural sensor frame bundle from neural_scene_v0 and sensor_rig_v0."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


NEURAL_SCENE_SCHEMA_VERSION_V0 = "neural_scene_v0"
SENSOR_RIG_SCHEMA_VERSION_V0 = "sensor_rig_v0"
ERROR_SOURCE = "render_neural_sensor_stub.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render minimal sensor frames from neural scene scaffold")
    parser.add_argument("--neural-scene", required=True, help="Input neural scene JSON path")
    parser.add_argument("--sensor-rig", required=True, help="Input sensor rig JSON path")
    parser.add_argument("--out", required=True, help="Output rendered sensor frame JSON path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _validate_neural_scene(payload: dict[str, Any]) -> None:
    if str(payload.get("neural_scene_schema_version", "")) != NEURAL_SCENE_SCHEMA_VERSION_V0:
        raise ValueError(f"neural_scene_schema_version must be {NEURAL_SCENE_SCHEMA_VERSION_V0}")
    agents = payload.get("dynamic_agents", [])
    if not isinstance(agents, list):
        raise ValueError("dynamic_agents must be a list")


def _validate_sensor_rig(payload: dict[str, Any]) -> None:
    if str(payload.get("rig_schema_version", "")) != SENSOR_RIG_SCHEMA_VERSION_V0:
        raise ValueError(f"rig_schema_version must be {SENSOR_RIG_SCHEMA_VERSION_V0}")
    sensors = payload.get("sensors", [])
    if not isinstance(sensors, list) or len(sensors) == 0:
        raise ValueError("sensor rig sensors must be a non-empty list")


def _render_payload_for_sensor(
    *,
    sensor_type: str,
    sensor_config: dict[str, Any],
    actor_count: int,
    source_log_id: str,
    time_horizon_sec: float,
) -> dict[str, Any]:
    if sensor_type == "camera":
        return {
            "modality": "camera",
            "render_backend": "neural_stub",
            "source_log_id": source_log_id,
            "actor_count": actor_count,
            "time_horizon_sec": time_horizon_sec,
            "image_width_px": int(sensor_config.get("image_width_px", 1920)),
            "image_height_px": int(sensor_config.get("image_height_px", 1080)),
        }
    if sensor_type == "lidar":
        points_per_actor = int(sensor_config.get("points_per_actor", 50))
        return {
            "modality": "lidar",
            "render_backend": "neural_stub",
            "source_log_id": source_log_id,
            "actor_count": actor_count,
            "time_horizon_sec": time_horizon_sec,
            "point_count": actor_count * points_per_actor,
            "channel_count": int(sensor_config.get("channel_count", 64)),
        }
    if sensor_type == "radar":
        return {
            "modality": "radar",
            "render_backend": "neural_stub",
            "source_log_id": source_log_id,
            "actor_count": actor_count,
            "time_horizon_sec": time_horizon_sec,
            "target_count": actor_count,
            "max_range_m": float(sensor_config.get("max_range_m", 180.0)),
        }
    raise ValueError(f"unsupported sensor_type: {sensor_type}")


def render_frames(neural_scene: dict[str, Any], sensor_rig: dict[str, Any]) -> list[dict[str, Any]]:
    source_log_id = str(neural_scene.get("source_log_id", ""))
    dynamic_agents = neural_scene.get("dynamic_agents", [])
    actor_count = len(dynamic_agents) if isinstance(dynamic_agents, list) else 0
    render_hints = neural_scene.get("render_hints", {})
    time_horizon_sec = float(render_hints.get("time_horizon_sec", 0.0))

    sensors = sensor_rig.get("sensors", [])
    sorted_sensors = sorted(
        [sensor for sensor in sensors if isinstance(sensor, dict)],
        key=lambda sensor: str(sensor.get("sensor_id", "")),
    )
    frames: list[dict[str, Any]] = []
    for sensor in sorted_sensors:
        sensor_id = str(sensor.get("sensor_id", "")).strip()
        sensor_type = str(sensor.get("sensor_type", "")).strip().lower()
        if not sensor_id:
            raise ValueError("sensor_id must be a non-empty string")
        payload = _render_payload_for_sensor(
            sensor_type=sensor_type,
            sensor_config=sensor,
            actor_count=actor_count,
            source_log_id=source_log_id,
            time_horizon_sec=time_horizon_sec,
        )
        frames.append(
            {
                "sensor_id": sensor_id,
                "sensor_type": sensor_type,
                "payload": payload,
            }
        )
    return frames


def main() -> int:
    try:
        args = parse_args()
        neural_scene_path = Path(args.neural_scene).resolve()
        sensor_rig_path = Path(args.sensor_rig).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        neural_scene = _load_json_object(neural_scene_path, "neural scene")
        sensor_rig = _load_json_object(sensor_rig_path, "sensor rig")
        _validate_neural_scene(neural_scene)
        _validate_sensor_rig(sensor_rig)
        frames = render_frames(neural_scene, sensor_rig)

        output_payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "neural_scene_path": str(neural_scene_path),
            "sensor_rig_path": str(sensor_rig_path),
            "render_frame_count": len(frames),
            "frames": frames,
        }
        out_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] render_frame_count={len(frames)}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] render_neural_sensor_stub.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
