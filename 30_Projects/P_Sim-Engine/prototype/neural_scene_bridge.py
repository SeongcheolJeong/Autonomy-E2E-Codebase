#!/usr/bin/env python3
"""Build a minimal neural-scene scaffold from log_scene_v0 input."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


LOG_SCENE_SCHEMA_VERSION_V0 = "log_scene_v0"
NEURAL_SCENE_SCHEMA_VERSION_V0 = "neural_scene_v0"
ERROR_SOURCE = "neural_scene_bridge.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate neural scene scaffold from log scene")
    parser.add_argument("--log-scene", required=True, help="Input log scene JSON path")
    parser.add_argument("--out", required=True, help="Output neural scene JSON path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _build_neural_scene(payload: dict[str, Any], source_path: Path) -> dict[str, Any]:
    if str(payload.get("log_scene_schema_version", "")) != LOG_SCENE_SCHEMA_VERSION_V0:
        raise ValueError(f"log_scene_schema_version must be {LOG_SCENE_SCHEMA_VERSION_V0}")
    required = [
        "log_id",
        "map_id",
        "ego_initial_speed_mps",
        "lead_vehicle_initial_gap_m",
        "lead_vehicle_speed_mps",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"missing required keys: {missing}")

    log_id = str(payload["log_id"])
    map_id = str(payload["map_id"])
    ego_speed = float(payload["ego_initial_speed_mps"])
    lead_gap = float(payload["lead_vehicle_initial_gap_m"])
    lead_speed = float(payload["lead_vehicle_speed_mps"])

    return {
        "neural_scene_schema_version": NEURAL_SCENE_SCHEMA_VERSION_V0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_log_scene_path": str(source_path),
        "source_log_id": log_id,
        "map_ref": {
            "map_id": map_id,
            "map_version": str(payload.get("map_version", "v0")),
        },
        "dynamic_agents": [
            {
                "agent_id": "ego",
                "initial_state": {
                    "position_m": [0.0, 0.0, 0.0],
                    "speed_mps": ego_speed,
                },
            },
            {
                "agent_id": "lead_vehicle",
                "initial_state": {
                    "position_m": [lead_gap, 0.0, 0.0],
                    "speed_mps": lead_speed,
                },
            },
        ],
        "render_hints": {
            "sensor_frame_source": "stub",
            "time_horizon_sec": float(payload.get("duration_sec", 0.0)),
            "time_step_sec": float(payload.get("dt_sec", 0.1)),
        },
    }


def main() -> int:
    try:
        args = parse_args()
        source_path = Path(args.log_scene).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _load_json_object(source_path, "log scene")
        neural_scene = _build_neural_scene(payload, source_path)
        out_path.write_text(json.dumps(neural_scene, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] source_log_id={neural_scene['source_log_id']}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] neural_scene_bridge.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
