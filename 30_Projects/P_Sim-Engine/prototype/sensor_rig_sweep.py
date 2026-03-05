#!/usr/bin/env python3
"""Minimal sensor rig sweep evaluator based on stub sensor frame metrics."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary
from sensor_sim_bridge import FIDELITY_TIERS, generate_sensor_frames


WORLD_STATE_SCHEMA_VERSION_V0 = "world_state_v0"
RIG_SWEEP_SCHEMA_VERSION_V0 = "sensor_rig_sweep_v0"
ERROR_SOURCE = "sensor_rig_sweep.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate sensor rig candidates with stub sensor sim metrics")
    parser.add_argument("--world-state", required=True, help="World state JSON path")
    parser.add_argument("--rig-candidates", required=True, help="Rig sweep JSON path")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument(
        "--fidelity-tier",
        choices=list(FIDELITY_TIERS),
        default="contract",
        help="Sensor fidelity tier (contract|basic|high)",
    )
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _validate_world_state(world_state: dict[str, Any]) -> None:
    if str(world_state.get("world_state_schema_version", "")) != WORLD_STATE_SCHEMA_VERSION_V0:
        raise ValueError(
            "world_state_schema_version must be "
            f"{WORLD_STATE_SCHEMA_VERSION_V0}"
        )


def _validate_rig_candidates(payload: dict[str, Any]) -> None:
    if str(payload.get("rig_sweep_schema_version", "")) != RIG_SWEEP_SCHEMA_VERSION_V0:
        raise ValueError(
            "rig_sweep_schema_version must be "
            f"{RIG_SWEEP_SCHEMA_VERSION_V0}"
        )
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) == 0:
        raise ValueError("candidates must be a non-empty list")


def _score_frames(frames: list[dict[str, Any]]) -> tuple[float, dict[str, int]]:
    camera_visible_actor_total = 0
    lidar_point_count_total = 0
    radar_target_count_total = 0

    for frame in frames:
        payload = frame.get("payload", {})
        if not isinstance(payload, dict):
            continue
        modality = str(payload.get("modality", ""))
        if modality == "camera":
            camera_visible_actor_total += int(payload.get("visible_actor_count", 0))
        elif modality == "lidar":
            lidar_point_count_total += int(payload.get("point_count", 0))
        elif modality == "radar":
            radar_target_count_total += int(payload.get("target_count", 0))

    heuristic_score = (
        float(camera_visible_actor_total)
        + float(lidar_point_count_total) / 100.0
        + float(radar_target_count_total) * 2.0
    )
    metrics = {
        "camera_visible_actor_total": camera_visible_actor_total,
        "lidar_point_count_total": lidar_point_count_total,
        "radar_target_count_total": radar_target_count_total,
    }
    return heuristic_score, metrics


def main() -> int:
    try:
        args = parse_args()
        world_state_path = Path(args.world_state).resolve()
        rig_candidates_path = Path(args.rig_candidates).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        world_state = _load_json_object(world_state_path, "world state")
        rig_candidates_payload = _load_json_object(rig_candidates_path, "rig candidates")
        _validate_world_state(world_state)
        _validate_rig_candidates(rig_candidates_payload)

        rankings: list[dict[str, Any]] = []
        fidelity_tier = str(args.fidelity_tier).strip().lower()
        candidates = rig_candidates_payload.get("candidates", [])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            rig_id = str(candidate.get("rig_id", "")).strip()
            if not rig_id:
                raise ValueError("each candidate requires rig_id")
            sensors = candidate.get("sensors", [])
            if not isinstance(sensors, list) or len(sensors) == 0:
                raise ValueError(f"rig {rig_id} sensors must be a non-empty list")
            frames = generate_sensor_frames(
                world_state,
                {"rig_schema_version": "sensor_rig_v0", "sensors": sensors},
                fidelity_tier=fidelity_tier,
            )
            heuristic_score, metrics = _score_frames(frames)
            rankings.append(
                {
                    "rig_id": rig_id,
                    "sensor_count": len(sensors),
                    "heuristic_score": round(heuristic_score, 6),
                    "metrics": metrics,
                }
            )

        rankings_sorted = sorted(
            rankings,
            key=lambda row: (-float(row.get("heuristic_score", 0.0)), str(row.get("rig_id", ""))),
        )
        best_rig_id = str(rankings_sorted[0]["rig_id"]) if rankings_sorted else ""
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "world_state_path": str(world_state_path),
            "rig_candidates_path": str(rig_candidates_path),
            "sensor_fidelity_tier": fidelity_tier,
            "candidate_count": len(rankings_sorted),
            "best_rig_id": best_rig_id,
            "rankings": rankings_sorted,
        }
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] candidate_count={len(rankings_sorted)}")
        print(f"[ok] best_rig_id={best_rig_id}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] sensor_rig_sweep.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
