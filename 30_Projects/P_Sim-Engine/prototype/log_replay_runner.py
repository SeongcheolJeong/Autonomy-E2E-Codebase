#!/usr/bin/env python3
"""Minimal closed-loop log replay runner scaffold."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


LOG_SCENE_SCHEMA_VERSION_V0 = "log_scene_v0"
SCENARIO_SCHEMA_VERSION_V0 = "scenario_definition_v0"
ERROR_SOURCE = "log_replay_runner.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay a log scene through core_sim_runner scaffold")
    parser.add_argument("--log-scene", required=True, help="Log scene JSON path")
    parser.add_argument("--run-id", required=True, help="Replay run ID")
    parser.add_argument("--out", required=True, help="Output root directory")
    parser.add_argument("--seed", default="", help="Deterministic seed for replay")
    parser.add_argument("--python-bin", default=sys.executable, help="Python executable to invoke core runner")
    parser.add_argument(
        "--core-runner",
        default=str(Path(__file__).resolve().parent / "core_sim_runner.py"),
        help="core_sim_runner.py path",
    )
    parser.add_argument("--sds-version", default="sds_unknown", help="SDS version identifier")
    parser.add_argument("--sim-version", default="sim_engine_v0_prototype", help="Sim version identifier")
    parser.add_argument("--fidelity-profile", default="dev-fast", help="Fidelity profile")
    return parser.parse_args()


def parse_int(raw: Any, *, default: int, field: str) -> int:
    value = str(raw).strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer, got: {raw}") from exc


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _require_keys(payload: dict[str, Any], keys: list[str]) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ValueError(f"missing required keys: {missing}")


def _build_scenario_payload(log_scene: dict[str, Any]) -> dict[str, Any]:
    if str(log_scene.get("log_scene_schema_version", "")) != LOG_SCENE_SCHEMA_VERSION_V0:
        raise ValueError(
            "log_scene_schema_version must be "
            f"{LOG_SCENE_SCHEMA_VERSION_V0}"
        )
    _require_keys(
        log_scene,
        [
            "log_id",
            "map_id",
            "ego_initial_speed_mps",
            "lead_vehicle_initial_gap_m",
            "lead_vehicle_speed_mps",
            "duration_sec",
            "dt_sec",
        ],
    )
    log_id = str(log_scene["log_id"])
    ego_speed = float(log_scene["ego_initial_speed_mps"])
    lead_gap = float(log_scene["lead_vehicle_initial_gap_m"])
    lead_speed = float(log_scene["lead_vehicle_speed_mps"])

    return {
        "scenario_schema_version": SCENARIO_SCHEMA_VERSION_V0,
        "scenario_id": f"log_replay_{log_id}",
        "duration_sec": float(log_scene["duration_sec"]),
        "dt_sec": float(log_scene["dt_sec"]),
        "ego": {
            "actor_id": "ego",
            "position_m": 0.0,
            "speed_mps": ego_speed,
        },
        "npcs": [
            {
                "actor_id": "lead_vehicle",
                "position_m": lead_gap,
                "speed_mps": lead_speed,
            }
        ],
    }


def _run_core_sim(
    *,
    python_bin: str,
    core_runner: Path,
    scenario_path: Path,
    run_id: str,
    out_root: Path,
    seed: int,
    map_id: str,
    map_version: str,
    sds_version: str,
    sim_version: str,
    fidelity_profile: str,
) -> None:
    cmd = [
        python_bin,
        str(core_runner),
        "--scenario",
        str(scenario_path),
        "--run-id",
        run_id,
        "--seed",
        str(seed),
        "--out",
        str(out_root),
        "--run-source",
        "log_replay_closed_loop",
        "--map-id",
        map_id,
        "--map-version",
        map_version,
        "--sds-version",
        sds_version,
        "--sim-version",
        sim_version,
        "--fidelity-profile",
        fidelity_profile,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "core runner failed: "
            f"returncode={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def main() -> int:
    try:
        args = parse_args()
        seed = parse_int(args.seed, default=42, field="seed")
        log_scene_path = Path(args.log_scene).resolve()
        out_root = Path(args.out).resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        run_dir = out_root / args.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        log_scene = _load_json_object(log_scene_path, "log scene")
        scenario_payload = _build_scenario_payload(log_scene)
        scenario_path = run_dir / "replay_scenario.json"
        scenario_path.write_text(json.dumps(scenario_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        map_id = str(log_scene.get("map_id", "map_unknown"))
        map_version = str(log_scene.get("map_version", "v0"))
        core_runner = Path(args.core_runner).resolve()
        _run_core_sim(
            python_bin=args.python_bin,
            core_runner=core_runner,
            scenario_path=scenario_path,
            run_id=args.run_id,
            out_root=out_root,
            seed=seed,
            map_id=map_id,
            map_version=map_version,
            sds_version=args.sds_version,
            sim_version=args.sim_version,
            fidelity_profile=args.fidelity_profile,
        )

        summary_path = out_root / args.run_id / "summary.json"
        summary_payload = _load_json_object(summary_path, "core replay summary")
        replay_manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "log_scene_path": str(log_scene_path),
            "log_id": str(log_scene.get("log_id", "")),
            "run_id": args.run_id,
            "scenario_path": str(scenario_path),
            "summary_path": str(summary_path),
            "status": str(summary_payload.get("status", "")),
            "termination_reason": str(summary_payload.get("termination_reason", "")),
        }
        manifest_path = run_dir / "log_replay_manifest.json"
        manifest_path.write_text(json.dumps(replay_manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        print(f"[ok] run_id={args.run_id}")
        print(f"[ok] scenario={scenario_path}")
        print(f"[ok] summary={summary_path}")
        print(f"[ok] manifest={manifest_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        message = str(exc)
        print(f"[error] log_replay_runner.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
