#!/usr/bin/env python3
"""Execute a minimal runtime scenario contract check from launch-manifest inputs."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


SIM_RUNTIME_SCENARIO_CONTRACT_SCHEMA_VERSION_V0 = "sim_runtime_scenario_contract_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0 = "sim_runtime_probe_v0"
ALLOWED_RUNTIMES = {"awsim", "carla"}
ERROR_SOURCE = "sim_runtime_scenario_contract_runner.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run minimal runtime scenario contract checks")
    parser.add_argument("--runtime", required=True, help="Runtime target: awsim|carla")
    parser.add_argument("--launch-manifest", required=True, help="Runtime launch-manifest JSON path")
    parser.add_argument("--probe-report", default="", help="Optional runtime probe report JSON path")
    parser.add_argument(
        "--require-runtime-ready",
        action="store_true",
        help="Fail when runtime probe report indicates runtime is not ready",
    )
    parser.add_argument(
        "--step-dt-sec",
        default="0.1",
        help="Simulation step dt (seconds, > 0)",
    )
    parser.add_argument(
        "--max-steps",
        default="20",
        help="Maximum number of contract execution steps (> 0)",
    )
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


def _safe_float(value: Any, *, field: str, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    text = str(value).strip()
    if not text:
        return float(default)
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be numeric, got: {value}") from exc


def _extract_initial_x(actor: dict[str, Any]) -> float:
    position_value = actor.get("initial_position_m")
    if isinstance(position_value, list) and position_value:
        return _safe_float(position_value[0], field="ego.initial_position_m[0]", default=0.0)
    if isinstance(position_value, tuple) and position_value:
        return _safe_float(position_value[0], field="ego.initial_position_m[0]", default=0.0)
    return 0.0


def main() -> int:
    try:
        args = parse_args()
        runtime = _normalize_runtime(args.runtime)
        step_dt_sec = _parse_positive_float(args.step_dt_sec, field="step-dt-sec")
        max_steps = _parse_positive_int(args.max_steps, field="max-steps")
        launch_manifest_path = Path(args.launch_manifest).resolve()
        probe_report_text = str(args.probe_report).strip()
        probe_report_path = Path(probe_report_text).resolve() if probe_report_text else None
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        launch_manifest_payload = _load_json_object(
            launch_manifest_path,
            subject="launch manifest",
        )
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
        estimated_scene_frame_count = int(scene_block.get("estimated_scene_frame_count", 0) or 0)
        if estimated_scene_frame_count <= 0:
            execution_block = launch_manifest_payload.get("execution", {})
            if isinstance(execution_block, dict):
                estimated_scene_frame_count = int(execution_block.get("frame_count_per_sensor", 0) or 0)
        estimated_scene_frame_count = max(1, estimated_scene_frame_count)

        ego_actor = next(
            (
                actor
                for actor in actors
                if str(actor.get("role", "")).strip().lower() == "ego"
                or str(actor.get("actor_id", "")).strip().lower() == "ego"
            ),
            None,
        )
        ego_actor_id = str((ego_actor or {}).get("actor_id", "")).strip() or "ego"
        ego_speed_mps = _safe_float((ego_actor or {}).get("initial_speed_mps"), field="ego.initial_speed_mps")

        probe_report_payload: dict[str, Any] = {}
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
                "runtime is not ready for scenario contract execution"
                f" (runtime_available={str(runtime_available).lower()},"
                f" probe_executed={str(probe_executed).lower()},"
                f" probe_returncode_acceptable={str(probe_returncode_acceptable).lower()})"
            )

        executed_step_count = min(max_steps, max(1, int(estimated_scene_frame_count)))
        ego_position_m = _extract_initial_x(ego_actor or {})
        execution_samples: list[dict[str, Any]] = []
        for index in range(executed_step_count):
            time_sec = (index + 1) * step_dt_sec
            ego_position_m += ego_speed_mps * step_dt_sec
            execution_samples.append(
                {
                    "step_index": int(index + 1),
                    "time_sec": round(time_sec, 6),
                    "ego_actor_id": ego_actor_id,
                    "ego_position_m": round(ego_position_m, 6),
                    "actor_count": int(len(actors)),
                    "sensor_stream_count": int(len(sensor_streams)),
                }
            )

        output_payload = {
            "sim_runtime_scenario_contract_schema_version": SIM_RUNTIME_SCENARIO_CONTRACT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
            "launch_manifest_path": str(launch_manifest_path),
            "launch_manifest_schema_version": launch_schema,
            "probe_report_path": str(probe_report_path) if probe_report_path is not None else "",
            "probe_report_schema_version": probe_report_schema,
            "probe_report_loaded": bool(probe_report_loaded),
            "require_runtime_ready": bool(args.require_runtime_ready),
            "runtime_available": bool(runtime_available),
            "probe_executed": bool(probe_executed),
            "probe_returncode_acceptable": bool(probe_returncode_acceptable),
            "runtime_ready": bool(runtime_ready),
            "scenario_contract_status": "pass",
            "actor_count": int(len(actors)),
            "sensor_stream_count": int(len(sensor_streams)),
            "ego_actor_id": ego_actor_id,
            "ego_speed_mps": float(ego_speed_mps),
            "estimated_scene_frame_count": int(estimated_scene_frame_count),
            "executed_step_count": int(executed_step_count),
            "step_dt_sec": float(step_dt_sec),
            "sim_duration_sec": round(executed_step_count * step_dt_sec, 6),
            "execution_samples": execution_samples,
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
        print("[ok] scenario_contract_status=pass")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError, IndexError) as exc:
        message = str(exc)
        print(f"[error] sim_runtime_scenario_contract_runner.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
