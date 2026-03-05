#!/usr/bin/env python3
"""Minimal log-scene augmentation helper for adjacent scenario/fault sweeps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


LOG_SCENE_SCHEMA_VERSION_V0 = "log_scene_v0"
ERROR_SOURCE = "augment_log_scene.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Augment a log_scene_v0 payload")
    parser.add_argument("--input", required=True, help="Input log_scene_v0 JSON path")
    parser.add_argument("--out", required=True, help="Output augmented log scene JSON path")
    parser.add_argument(
        "--ego-speed-scale",
        type=float,
        default=1.0,
        help="Scale factor applied to ego_initial_speed_mps",
    )
    parser.add_argument(
        "--lead-gap-offset-m",
        type=float,
        default=0.0,
        help="Offset added to lead_vehicle_initial_gap_m",
    )
    parser.add_argument(
        "--lead-speed-offset-mps",
        type=float,
        default=0.0,
        help="Offset added to lead_vehicle_speed_mps",
    )
    parser.add_argument(
        "--suffix",
        default="aug",
        help="Suffix appended to log_id",
    )
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def augment_log_scene(
    payload: dict[str, Any],
    *,
    ego_speed_scale: float,
    lead_gap_offset_m: float,
    lead_speed_offset_mps: float,
    suffix: str,
) -> dict[str, Any]:
    if str(payload.get("log_scene_schema_version", "")) != LOG_SCENE_SCHEMA_VERSION_V0:
        raise ValueError(
            "log_scene_schema_version must be "
            f"{LOG_SCENE_SCHEMA_VERSION_V0}"
        )

    result = dict(payload)
    base_log_id = str(payload.get("log_id", "")).strip()
    if not base_log_id:
        raise ValueError("log_id must be non-empty")

    result["log_id"] = f"{base_log_id}_{suffix}"
    result["ego_initial_speed_mps"] = float(payload.get("ego_initial_speed_mps", 0.0)) * ego_speed_scale
    result["lead_vehicle_initial_gap_m"] = (
        float(payload.get("lead_vehicle_initial_gap_m", 0.0)) + lead_gap_offset_m
    )
    result["lead_vehicle_speed_mps"] = (
        float(payload.get("lead_vehicle_speed_mps", 0.0)) + lead_speed_offset_mps
    )
    result["augmentation"] = {
        "ego_speed_scale": ego_speed_scale,
        "lead_gap_offset_m": lead_gap_offset_m,
        "lead_speed_offset_mps": lead_speed_offset_mps,
        "source_log_id": base_log_id,
    }
    return result


def main() -> int:
    try:
        args = parse_args()
        input_path = Path(args.input).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _load_json_object(input_path, "log scene")

        augmented = augment_log_scene(
            payload,
            ego_speed_scale=float(args.ego_speed_scale),
            lead_gap_offset_m=float(args.lead_gap_offset_m),
            lead_speed_offset_mps=float(args.lead_speed_offset_mps),
            suffix=str(args.suffix),
        )
        out_path.write_text(json.dumps(augmented, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] input={input_path}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] augment_log_scene.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
