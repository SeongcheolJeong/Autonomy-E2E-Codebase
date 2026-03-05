#!/usr/bin/env python3
"""Publish runtime scene execution result artifact from launch/scenario contract reports."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


RUNTIME_SCENE_RESULT_SCHEMA_VERSION_V0 = "runtime_scene_result_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
SIM_RUNTIME_SCENARIO_CONTRACT_SCHEMA_VERSION_V0 = "sim_runtime_scenario_contract_v0"
SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0 = "sim_runtime_probe_v0"
ALLOWED_RUNTIMES = {"awsim", "carla"}
ERROR_SOURCE = "sim_runtime_scene_result_runner.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build runtime scene result artifact")
    parser.add_argument("--runtime", required=True, help="Runtime target: awsim|carla")
    parser.add_argument("--launch-manifest", required=True, help="Runtime launch-manifest JSON path")
    parser.add_argument(
        "--scenario-contract-report",
        required=True,
        help="sim_runtime_scenario_contract_runner.py report JSON path",
    )
    parser.add_argument("--probe-report", default="", help="Optional runtime probe report JSON path")
    parser.add_argument(
        "--require-runtime-ready",
        action="store_true",
        help="Fail when runtime probe report indicates runtime is not ready",
    )
    parser.add_argument("--step-dt-sec", default="0.1", help="Fallback simulation dt (seconds, > 0)")
    parser.add_argument("--out", required=True, help="Output runtime scene result JSON path")
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


def _parse_int_or_default(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _parse_float_or_default(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _extract_actor_x(actor: dict[str, Any]) -> float:
    position_raw = actor.get("initial_position_m")
    if isinstance(position_raw, (list, tuple)) and position_raw:
        return _parse_float_or_default(position_raw[0], default=0.0)
    return 0.0


def _find_ego_actor(actors: list[dict[str, Any]]) -> dict[str, Any]:
    for actor in actors:
        role = str(actor.get("role", "")).strip().lower()
        actor_id = str(actor.get("actor_id", "")).strip().lower()
        if role == "ego" or actor_id == "ego":
            return actor
    return actors[0] if actors else {}


def _extract_last_ego_position(
    execution_samples: Any,
    *,
    ego_actor_id: str,
) -> float | None:
    if not isinstance(execution_samples, list):
        return None
    matched: float | None = None
    for sample_raw in execution_samples:
        if not isinstance(sample_raw, dict):
            continue
        sample_actor_id = str(sample_raw.get("ego_actor_id", "")).strip()
        if sample_actor_id and sample_actor_id != ego_actor_id:
            continue
        if "ego_position_m" not in sample_raw:
            continue
        matched = _parse_float_or_default(sample_raw.get("ego_position_m"), default=0.0)
    return matched


def main() -> int:
    try:
        args = parse_args()
        runtime = _normalize_runtime(args.runtime)
        fallback_step_dt_sec = _parse_positive_float(args.step_dt_sec, field="step-dt-sec")
        launch_manifest_path = Path(args.launch_manifest).resolve()
        scenario_contract_report_path = Path(args.scenario_contract_report).resolve()
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
        actor_count_manifest = int(len(actors))

        sensor_streams_raw = launch_manifest_payload.get("sensor_streams", [])
        if not isinstance(sensor_streams_raw, list) or not sensor_streams_raw:
            raise ValueError("launch manifest sensor_streams must be a non-empty list")
        sensor_streams = [row for row in sensor_streams_raw if isinstance(row, dict)]
        if not sensor_streams:
            raise ValueError("launch manifest sensor_streams must contain object rows")
        sensor_stream_count_manifest = int(len(sensor_streams))

        scene_block = launch_manifest_payload.get("scene", {})
        if not isinstance(scene_block, dict):
            raise ValueError("launch manifest scene must be an object")
        estimated_scene_frame_count = int(scene_block.get("estimated_scene_frame_count", 0) or 0)
        if estimated_scene_frame_count <= 0:
            execution_block = launch_manifest_payload.get("execution", {})
            if isinstance(execution_block, dict):
                estimated_scene_frame_count = int(execution_block.get("frame_count_per_sensor", 0) or 0)
        estimated_scene_frame_count = max(1, estimated_scene_frame_count)

        scenario_contract_payload = _load_json_object(
            scenario_contract_report_path,
            subject="runtime scenario contract report",
        )
        scenario_contract_schema = str(
            scenario_contract_payload.get("sim_runtime_scenario_contract_schema_version", "")
        ).strip()
        if scenario_contract_schema != SIM_RUNTIME_SCENARIO_CONTRACT_SCHEMA_VERSION_V0:
            raise ValueError(
                "runtime scenario contract report schema must be "
                f"{SIM_RUNTIME_SCENARIO_CONTRACT_SCHEMA_VERSION_V0}, got: {scenario_contract_schema or '<empty>'}"
            )
        scenario_contract_runtime = str(scenario_contract_payload.get("runtime", "")).strip().lower()
        if scenario_contract_runtime and scenario_contract_runtime != runtime:
            raise ValueError(
                "runtime mismatch between --runtime "
                f"({runtime}) and scenario contract report ({scenario_contract_runtime})"
            )
        scenario_contract_status = str(scenario_contract_payload.get("scenario_contract_status", "")).strip().lower()
        if scenario_contract_status and scenario_contract_status != "pass":
            raise ValueError("scenario contract report scenario_contract_status must be pass")
        scenario_runtime_ready = bool(scenario_contract_payload.get("runtime_ready", False))
        actor_count = _parse_int_or_default(
            scenario_contract_payload.get("actor_count"),
            default=actor_count_manifest,
        )
        sensor_stream_count = _parse_int_or_default(
            scenario_contract_payload.get("sensor_stream_count"),
            default=sensor_stream_count_manifest,
        )
        estimated_scene_frame_count_report = _parse_int_or_default(
            scenario_contract_payload.get("estimated_scene_frame_count"),
            default=estimated_scene_frame_count,
        )
        executed_step_count = _parse_int_or_default(
            scenario_contract_payload.get("executed_step_count"),
            default=max(1, estimated_scene_frame_count_report),
        )
        step_dt_sec = _parse_float_or_default(
            scenario_contract_payload.get("step_dt_sec"),
            default=fallback_step_dt_sec,
        )
        if step_dt_sec <= 0.0:
            step_dt_sec = float(fallback_step_dt_sec)
        sim_duration_sec = _parse_float_or_default(
            scenario_contract_payload.get("sim_duration_sec"),
            default=float(executed_step_count) * float(step_dt_sec),
        )
        if sim_duration_sec <= 0.0:
            sim_duration_sec = float(executed_step_count) * float(step_dt_sec)

        if actor_count <= 0:
            raise ValueError("scenario contract report actor_count must be > 0")
        if sensor_stream_count <= 0:
            raise ValueError("scenario contract report sensor_stream_count must be > 0")
        if estimated_scene_frame_count_report <= 0:
            raise ValueError("scenario contract report estimated_scene_frame_count must be > 0")
        if executed_step_count <= 0:
            raise ValueError("scenario contract report executed_step_count must be > 0")
        if sim_duration_sec <= 0.0:
            raise ValueError("scenario contract report sim_duration_sec must be > 0")

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

        runtime_ready = bool(runtime_available) and (not probe_executed or probe_returncode_acceptable) and bool(
            scenario_runtime_ready
        )
        if bool(args.require_runtime_ready) and probe_report_path is None:
            raise ValueError("--require-runtime-ready requires --probe-report")
        if bool(args.require_runtime_ready) and not runtime_ready:
            raise ValueError(
                "runtime is not ready for scene result publication"
                f" (runtime_available={str(runtime_available).lower()},"
                f" probe_executed={str(probe_executed).lower()},"
                f" probe_returncode_acceptable={str(probe_returncode_acceptable).lower()},"
                f" scenario_runtime_ready={str(scenario_runtime_ready).lower()})"
            )

        ego_actor = _find_ego_actor(actors)
        ego_actor_id = str(
            scenario_contract_payload.get("ego_actor_id") or ego_actor.get("actor_id") or "ego"
        ).strip() or "ego"
        ego_initial_position_m = float(_extract_actor_x(ego_actor))
        ego_last_sample_position = _extract_last_ego_position(
            scenario_contract_payload.get("execution_samples"),
            ego_actor_id=ego_actor_id,
        )
        if ego_last_sample_position is None:
            ego_speed_mps = _parse_float_or_default(
                scenario_contract_payload.get("ego_speed_mps"),
                default=_parse_float_or_default(ego_actor.get("initial_speed_mps"), default=0.0),
            )
            ego_final_position_m = ego_initial_position_m + float(ego_speed_mps) * float(sim_duration_sec)
        else:
            ego_final_position_m = float(ego_last_sample_position)
        ego_travel_distance_m = max(0.0, float(ego_final_position_m) - float(ego_initial_position_m))

        coverage_ratio = float(executed_step_count) / float(max(1, estimated_scene_frame_count_report))
        if coverage_ratio > 1.0:
            coverage_ratio = 1.0

        output_payload = {
            "runtime_scene_result_schema_version": RUNTIME_SCENE_RESULT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
            "launch_manifest_path": str(launch_manifest_path),
            "launch_manifest_schema_version": launch_schema,
            "scenario_contract_report_path": str(scenario_contract_report_path),
            "scenario_contract_report_schema_version": scenario_contract_schema,
            "scenario_contract_status": "pass",
            "probe_report_path": str(probe_report_path) if probe_report_path is not None else "",
            "probe_report_schema_version": probe_report_schema,
            "probe_report_loaded": bool(probe_report_loaded),
            "require_runtime_ready": bool(args.require_runtime_ready),
            "runtime_available": bool(runtime_available),
            "probe_executed": bool(probe_executed),
            "probe_returncode_acceptable": bool(probe_returncode_acceptable),
            "runtime_ready": bool(runtime_ready),
            "scene_result_status": "pass",
            "actor_count": int(actor_count),
            "sensor_stream_count": int(sensor_stream_count),
            "estimated_scene_frame_count": int(estimated_scene_frame_count_report),
            "executed_step_count": int(executed_step_count),
            "step_dt_sec": float(step_dt_sec),
            "sim_duration_sec": float(round(sim_duration_sec, 6)),
            "coverage_ratio": float(round(coverage_ratio, 6)),
            "ego_actor_id": ego_actor_id,
            "ego_initial_position_m": float(round(ego_initial_position_m, 6)),
            "ego_final_position_m": float(round(ego_final_position_m, 6)),
            "ego_travel_distance_m": float(round(ego_travel_distance_m, 6)),
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
        print("[ok] scene_result_status=pass")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError, KeyError, IndexError) as exc:
        message = str(exc)
        print(f"[error] sim_runtime_scene_result_runner.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
