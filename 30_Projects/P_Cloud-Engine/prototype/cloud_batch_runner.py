#!/usr/bin/env python3
"""Minimal single-node Cloud Engine batch runner (v0 prototype)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


class BatchSpecError(Exception):
    pass


ERROR_SOURCE = "cloud_batch_runner.py"
ERROR_PHASE = "resolve_inputs"


def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)

    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise BatchSpecError("YAML batch spec requires PyYAML; use JSON or install PyYAML") from exc
        payload = yaml.safe_load(text)
        if not isinstance(payload, dict):
            raise BatchSpecError("YAML payload must be an object")
        return payload

    raise BatchSpecError(f"unsupported batch spec extension: {path.suffix}")


def _require_keys(payload: dict[str, Any], keys: list[str]) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise BatchSpecError(f"missing required keys: {missing}")


def load_batch_spec(path: Path) -> dict[str, Any]:
    payload = _load_json_or_yaml(path)
    _require_keys(payload, ["batch_id", "sim_runner", "runs"])

    sim_runner = payload["sim_runner"]
    if not isinstance(sim_runner, dict):
        raise BatchSpecError("sim_runner must be an object")
    _require_keys(sim_runner, ["script_path"])

    runs = payload["runs"]
    if not isinstance(runs, list) or len(runs) == 0:
        raise BatchSpecError("runs must be a non-empty list")

    seen_run_ids: dict[str, int] = {}
    for idx, run in enumerate(runs):
        if not isinstance(run, dict):
            raise BatchSpecError(f"runs[{idx}] must be an object")
        _require_keys(run, ["run_id", "scenario"])
        run_id = str(run["run_id"]).strip()
        if not run_id:
            raise BatchSpecError(f"runs[{idx}].run_id must be non-empty")
        first_index = seen_run_ids.get(run_id)
        if first_index is not None:
            raise BatchSpecError(
                f"duplicate run_id '{run_id}' in runs[{idx}] (already used by runs[{first_index}])"
            )
        seen_run_ids[run_id] = idx

    payload.setdefault("execution", {})
    payload.setdefault("defaults", {})

    return payload


def resolve_ref(base_dir: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_of_payload(payload: Any) -> str:
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def _parse_optional_positive_float(raw: Any, *, field: str) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise BatchSpecError(f"{field} must be a positive number, got: {raw}") from exc
    if parsed <= 0.0:
        raise BatchSpecError(f"{field} must be > 0, got: {parsed}")
    return parsed


def _parse_optional_non_negative_float(raw: Any, *, field: str) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise BatchSpecError(f"{field} must be a non-negative number, got: {raw}") from exc
    if parsed < 0.0:
        raise BatchSpecError(f"{field} must be >= 0, got: {parsed}")
    return parsed


def _parse_optional_positive_int(raw: Any, *, field: str) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise BatchSpecError(f"{field} must be a positive integer, got: {raw}") from exc
    if parsed <= 0:
        raise BatchSpecError(f"{field} must be > 0, got: {parsed}")
    return parsed


def _parse_optional_float(raw: Any, *, field: str) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise BatchSpecError(f"{field} must be a number, got: {raw}") from exc


def _parse_optional_int_list(raw: Any, *, field: str) -> list[int] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        out: list[int] = []
        for item in raw:
            try:
                out.append(int(item))
            except (TypeError, ValueError) as exc:
                raise BatchSpecError(f"{field} must be a list of integers, got: {raw}") from exc
        return out if out else None
    text = str(raw).strip()
    if not text:
        return None
    parts = [token.strip() for token in text.split(",")]
    filtered = [token for token in parts if token]
    if not filtered:
        return None
    out: list[int] = []
    for token in filtered:
        try:
            out.append(int(token))
        except ValueError as exc:
            raise BatchSpecError(f"{field} must be a comma-separated integer list, got: {raw}") from exc
    return out


def _coerce_optional_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _coerce_int(raw: Any, *, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def build_run_input(
    *,
    run_spec: dict[str, Any],
    spec_dir: Path,
    batch_id: str,
    sim_runner: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    run_id = str(run_spec["run_id"])
    scenario_path = resolve_ref(spec_dir, str(run_spec["scenario"]))
    runner_script = resolve_ref(spec_dir, str(sim_runner["script_path"]))
    python_bin = str(sim_runner.get("python_bin", "python3"))
    seed = int(run_spec.get("seed", defaults.get("seed", 42)))
    run_source = str(run_spec.get("run_source", defaults.get("run_source", "sim_closed_loop")))
    sds_version = str(run_spec.get("sds_version", defaults.get("sds_version", "sds_unknown")))
    sim_version = str(run_spec.get("sim_version", defaults.get("sim_version", "sim_engine_v0_prototype")))
    fidelity_profile = str(run_spec.get("fidelity_profile", defaults.get("fidelity_profile", "dev-fast")))
    map_id = str(run_spec.get("map_id", defaults.get("map_id", "map_unknown")))
    map_version = str(run_spec.get("map_version", defaults.get("map_version", "v0")))
    odd_tags = str(run_spec.get("odd_tags", defaults.get("odd_tags", "")))
    traffic_profile_id = str(run_spec.get("traffic_profile_id", defaults.get("traffic_profile_id", ""))).strip()
    traffic_profile_source = str(
        run_spec.get("traffic_profile_source", defaults.get("traffic_profile_source", ""))
    ).strip()
    traffic_actor_pattern_id = str(
        run_spec.get("traffic_actor_pattern_id", defaults.get("traffic_actor_pattern_id", ""))
    ).strip()
    traffic_npc_count = _parse_optional_positive_int(
        run_spec.get("traffic_npc_count", defaults.get("traffic_npc_count", None)),
        field="traffic_npc_count",
    )
    traffic_npc_initial_gap_m = _parse_optional_positive_float(
        run_spec.get("traffic_npc_initial_gap_m", defaults.get("traffic_npc_initial_gap_m", None)),
        field="traffic_npc_initial_gap_m",
    )
    traffic_npc_gap_step_m = _parse_optional_positive_float(
        run_spec.get("traffic_npc_gap_step_m", defaults.get("traffic_npc_gap_step_m", None)),
        field="traffic_npc_gap_step_m",
    )
    traffic_npc_speed_offset_mps = _parse_optional_float(
        run_spec.get("traffic_npc_speed_offset_mps", defaults.get("traffic_npc_speed_offset_mps", None)),
        field="traffic_npc_speed_offset_mps",
    )
    traffic_npc_lane_profile = _parse_optional_int_list(
        run_spec.get("traffic_npc_lane_profile", defaults.get("traffic_npc_lane_profile", None)),
        field="traffic_npc_lane_profile",
    )
    traffic_npc_speed_scale = _parse_optional_positive_float(
        run_spec.get("traffic_npc_speed_scale", defaults.get("traffic_npc_speed_scale", None)),
        field="traffic_npc_speed_scale",
    )
    traffic_npc_speed_jitter_mps = _parse_optional_non_negative_float(
        run_spec.get("traffic_npc_speed_jitter_mps", defaults.get("traffic_npc_speed_jitter_mps", None)),
        field="traffic_npc_speed_jitter_mps",
    )
    return {
        "run_id": run_id,
        "scenario": str(scenario_path),
        "seed": seed,
        "run_source": run_source,
        "sds_version": sds_version,
        "sim_version": sim_version,
        "fidelity_profile": fidelity_profile,
        "map_id": map_id,
        "map_version": map_version,
        "odd_tags": odd_tags,
        "traffic_profile_id": traffic_profile_id,
        "traffic_profile_source": traffic_profile_source,
        "traffic_actor_pattern_id": traffic_actor_pattern_id,
        "traffic_npc_count": traffic_npc_count,
        "traffic_npc_initial_gap_m": traffic_npc_initial_gap_m,
        "traffic_npc_gap_step_m": traffic_npc_gap_step_m,
        "traffic_npc_speed_offset_mps": traffic_npc_speed_offset_mps,
        "traffic_npc_lane_profile": traffic_npc_lane_profile,
        "traffic_npc_speed_scale": traffic_npc_speed_scale,
        "traffic_npc_speed_jitter_mps": traffic_npc_speed_jitter_mps,
        "batch_id": batch_id,
        "runner_script": str(runner_script),
        "python_bin": python_bin,
    }


def build_run_plan(
    *,
    runs: list[dict[str, Any]],
    spec_dir: Path,
    batch_id: str,
    sim_runner: dict[str, Any],
    defaults: dict[str, Any],
) -> list[dict[str, Any]]:
    planned_runs = [
        build_run_input(
            run_spec=run,
            spec_dir=spec_dir,
            batch_id=batch_id,
            sim_runner=sim_runner,
            defaults=defaults,
        )
        for run in runs
    ]
    planned_runs.sort(key=lambda item: str(item["run_id"]))
    return planned_runs


def run_one(
    *,
    run_spec: dict[str, Any],
    spec_dir: Path,
    batch_id: str,
    batch_root: Path,
    sim_runner: dict[str, Any],
    defaults: dict[str, Any],
    timeout_sec: int | None,
) -> dict[str, Any]:
    run_input = build_run_input(
        run_spec=run_spec,
        spec_dir=spec_dir,
        batch_id=batch_id,
        sim_runner=sim_runner,
        defaults=defaults,
    )
    run_id = str(run_input["run_id"])
    scenario_path = Path(str(run_input["scenario"]))
    runner_script = Path(str(run_input["runner_script"]))
    python_bin = str(run_input["python_bin"])
    seed = int(run_input["seed"])
    run_source = str(run_input["run_source"])
    sds_version = str(run_input["sds_version"])
    sim_version = str(run_input["sim_version"])
    fidelity_profile = str(run_input["fidelity_profile"])
    map_id = str(run_input["map_id"])
    map_version = str(run_input["map_version"])
    odd_tags = str(run_input["odd_tags"])
    traffic_profile_id = str(run_input.get("traffic_profile_id", "")).strip()
    traffic_profile_source = str(run_input.get("traffic_profile_source", "")).strip()
    traffic_actor_pattern_id = str(run_input.get("traffic_actor_pattern_id", "")).strip()
    traffic_npc_count = run_input.get("traffic_npc_count", None)
    traffic_npc_initial_gap_m = run_input.get("traffic_npc_initial_gap_m", None)
    traffic_npc_gap_step_m = run_input.get("traffic_npc_gap_step_m", None)
    traffic_npc_speed_offset_mps = run_input.get("traffic_npc_speed_offset_mps", None)
    traffic_npc_lane_profile = run_input.get("traffic_npc_lane_profile", None)
    traffic_npc_speed_scale = run_input.get("traffic_npc_speed_scale", None)
    traffic_npc_speed_jitter_mps = run_input.get("traffic_npc_speed_jitter_mps", None)
    run_signature = sha256_of_payload(run_input)

    started_at = iso_now()
    started_wall = time.perf_counter()

    cmd = [
        python_bin,
        str(runner_script),
        "--scenario",
        str(scenario_path),
        "--run-id",
        run_id,
        "--seed",
        str(seed),
        "--out",
        str(batch_root),
        "--run-source",
        run_source,
        "--sds-version",
        sds_version,
        "--sim-version",
        sim_version,
        "--fidelity-profile",
        fidelity_profile,
        "--map-id",
        map_id,
        "--map-version",
        map_version,
        "--odd-tags",
        odd_tags,
        "--batch-id",
        batch_id,
    ]
    if traffic_profile_id:
        cmd.extend(["--traffic-profile-id", traffic_profile_id])
    if traffic_actor_pattern_id:
        cmd.extend(["--traffic-actor-pattern-id", traffic_actor_pattern_id])
    if traffic_npc_count is not None:
        cmd.extend(["--traffic-npc-count", str(int(traffic_npc_count))])
    if traffic_npc_initial_gap_m is not None:
        cmd.extend(["--traffic-npc-initial-gap-m", str(float(traffic_npc_initial_gap_m))])
    if traffic_npc_gap_step_m is not None:
        cmd.extend(["--traffic-npc-gap-step-m", str(float(traffic_npc_gap_step_m))])
    if traffic_npc_speed_offset_mps is not None:
        cmd.extend(["--traffic-npc-speed-offset-mps", str(float(traffic_npc_speed_offset_mps))])
    if traffic_npc_lane_profile:
        cmd.extend(["--traffic-npc-lane-profile", ",".join(str(int(value)) for value in traffic_npc_lane_profile)])
    if traffic_npc_speed_scale is not None:
        cmd.extend(["--traffic-npc-speed-scale", str(float(traffic_npc_speed_scale))])
    if traffic_npc_speed_jitter_mps is not None:
        cmd.extend(["--traffic-npc-speed-jitter-mps", str(float(traffic_npc_speed_jitter_mps))])

    run_dir = batch_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "runner_stdout.log"
    stderr_path = run_dir / "runner_stderr.log"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")

        finished_at = iso_now()
        wall_time = round(time.perf_counter() - started_wall, 6)
        summary_path = run_dir / "summary.json"
        lane_risk_summary_path: str | None = None
        lane_risk_summary: dict[str, Any] | None = None
        min_ttc_same_lane_sec: float | None = None
        min_ttc_adjacent_lane_sec: float | None = None
        min_ttc_any_lane_sec: float | None = None
        ttc_under_3s_same_lane_count = 0
        ttc_under_3s_adjacent_lane_count = 0
        same_lane_rows = 0
        adjacent_lane_rows = 0
        other_lane_rows = 0

        if summary_path.exists():
            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            status = str(summary_payload.get("status", "unknown"))
            termination_reason = str(summary_payload.get("termination_reason", "unknown"))
            min_ttc_same_lane_sec = _coerce_optional_float(summary_payload.get("min_ttc_same_lane_sec"))
            min_ttc_adjacent_lane_sec = _coerce_optional_float(summary_payload.get("min_ttc_adjacent_lane_sec"))
            min_ttc_any_lane_sec = _coerce_optional_float(summary_payload.get("min_ttc_any_lane_sec"))
            lane_risk_summary_raw = summary_payload.get("lane_risk_summary")
            if isinstance(lane_risk_summary_raw, dict):
                lane_risk_summary = lane_risk_summary_raw
                ttc_under_3s_same_lane_count = _coerce_int(
                    lane_risk_summary.get("ttc_under_3s_same_lane_count"),
                    default=0,
                )
                ttc_under_3s_adjacent_lane_count = _coerce_int(
                    lane_risk_summary.get("ttc_under_3s_adjacent_lane_count"),
                    default=0,
                )
                same_lane_rows = _coerce_int(lane_risk_summary.get("same_lane_rows"), default=0)
                adjacent_lane_rows = _coerce_int(lane_risk_summary.get("adjacent_lane_rows"), default=0)
                other_lane_rows = _coerce_int(lane_risk_summary.get("other_lane_rows"), default=0)
            lane_risk_summary_path_raw = str(summary_payload.get("lane_risk_summary_path", "")).strip()
            if lane_risk_summary_path_raw:
                lane_risk_summary_path = lane_risk_summary_path_raw
        else:
            status = "failed" if proc.returncode != 0 else "success"
            termination_reason = "runner_error" if proc.returncode != 0 else "completed"

        return {
            "run_id": run_id,
            "scenario": str(scenario_path),
            "seed": seed,
            "traffic_profile_id": traffic_profile_id,
            "traffic_profile_source": traffic_profile_source,
            "traffic_actor_pattern_id": traffic_actor_pattern_id,
            "traffic_npc_count": traffic_npc_count,
            "traffic_npc_initial_gap_m": traffic_npc_initial_gap_m,
            "traffic_npc_gap_step_m": traffic_npc_gap_step_m,
            "traffic_npc_speed_offset_mps": traffic_npc_speed_offset_mps,
            "traffic_npc_lane_profile": traffic_npc_lane_profile,
            "traffic_npc_speed_scale": traffic_npc_speed_scale,
            "traffic_npc_speed_jitter_mps": traffic_npc_speed_jitter_mps,
            "run_signature": run_signature,
            "status": status,
            "termination_reason": termination_reason,
            "exit_code": proc.returncode,
            "started_at": started_at,
            "finished_at": finished_at,
            "wall_time_sec": wall_time,
            "summary_path": str(summary_path) if summary_path.exists() else None,
            "lane_risk_summary_path": lane_risk_summary_path,
            "lane_risk_summary": lane_risk_summary,
            "min_ttc_same_lane_sec": min_ttc_same_lane_sec,
            "min_ttc_adjacent_lane_sec": min_ttc_adjacent_lane_sec,
            "min_ttc_any_lane_sec": min_ttc_any_lane_sec,
            "ttc_under_3s_same_lane_count": ttc_under_3s_same_lane_count,
            "ttc_under_3s_adjacent_lane_count": ttc_under_3s_adjacent_lane_count,
            "same_lane_rows": same_lane_rows,
            "adjacent_lane_rows": adjacent_lane_rows,
            "other_lane_rows": other_lane_rows,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
    except subprocess.TimeoutExpired as exc:
        (stdout_path).write_text((exc.stdout or "") if isinstance(exc.stdout, str) else "", encoding="utf-8")
        (stderr_path).write_text((exc.stderr or "") if isinstance(exc.stderr, str) else "", encoding="utf-8")
        finished_at = iso_now()
        wall_time = round(time.perf_counter() - started_wall, 6)
        return {
            "run_id": run_id,
            "scenario": str(scenario_path),
            "seed": seed,
            "traffic_profile_id": traffic_profile_id,
            "traffic_profile_source": traffic_profile_source,
            "traffic_actor_pattern_id": traffic_actor_pattern_id,
            "traffic_npc_count": traffic_npc_count,
            "traffic_npc_initial_gap_m": traffic_npc_initial_gap_m,
            "traffic_npc_gap_step_m": traffic_npc_gap_step_m,
            "traffic_npc_speed_offset_mps": traffic_npc_speed_offset_mps,
            "traffic_npc_lane_profile": traffic_npc_lane_profile,
            "traffic_npc_speed_scale": traffic_npc_speed_scale,
            "traffic_npc_speed_jitter_mps": traffic_npc_speed_jitter_mps,
            "run_signature": run_signature,
            "status": "timeout",
            "termination_reason": "timeout",
            "exit_code": None,
            "started_at": started_at,
            "finished_at": finished_at,
            "wall_time_sec": wall_time,
            "summary_path": None,
            "lane_risk_summary_path": None,
            "lane_risk_summary": None,
            "min_ttc_same_lane_sec": None,
            "min_ttc_adjacent_lane_sec": None,
            "min_ttc_any_lane_sec": None,
            "ttc_under_3s_same_lane_count": 0,
            "ttc_under_3s_adjacent_lane_count": 0,
            "same_lane_rows": 0,
            "adjacent_lane_rows": 0,
            "other_lane_rows": 0,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Cloud Engine v0 local batch")
    parser.add_argument("--batch-spec", required=True, help="Path to batch spec JSON/YAML")
    parser.add_argument("--out", default="", help="Optional output root override")
    parser.add_argument("--dry-run", action="store_true", help="Only print expanded run plan")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_spec_path = Path(args.batch_spec).resolve()

    try:
        spec = load_batch_spec(batch_spec_path)
        spec_dir = batch_spec_path.parent
        batch_id = str(spec["batch_id"])
        sim_runner = spec["sim_runner"]
        defaults = spec.get("defaults", {})

        execution = spec.get("execution", {})
        max_concurrency = int(execution.get("max_concurrency", 1))
        timeout_sec = execution.get("timeout_sec_per_run")
        timeout_val = int(timeout_sec) if timeout_sec is not None else None
        run_plan = build_run_plan(
            runs=spec["runs"],
            spec_dir=spec_dir,
            batch_id=batch_id,
            sim_runner=sim_runner,
            defaults=defaults,
        )
    except (BatchSpecError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[error] invalid batch spec: {exc}")
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=str(exc))
        return 2
    run_plan_payload = [
        {
            "run_id": str(item["run_id"]),
            "run_signature": sha256_of_payload(item),
        }
        for item in run_plan
    ]
    batch_spec_sha256 = sha256_of_payload(spec)
    run_plan_sha256 = sha256_of_payload(run_plan_payload)

    output_root = Path(args.out).resolve() if args.out else resolve_ref(spec_dir, str(spec.get("output_root", "batch_runs")))
    batch_root = output_root / batch_id

    if args.dry_run:
        print(f"[dry-run] batch_id={batch_id}")
        print(f"[dry-run] output={batch_root}")
        print(f"[dry-run] runs={len(spec['runs'])} max_concurrency={max_concurrency}")
        print(f"[dry-run] batch_spec_sha256={batch_spec_sha256}")
        print(f"[dry-run] run_plan_sha256={run_plan_sha256}")
        for run in run_plan:
            traffic_profile_id = str(run.get("traffic_profile_id", "")).strip()
            traffic_actor_pattern_id = str(run.get("traffic_actor_pattern_id", "")).strip()
            traffic_npc_count = run.get("traffic_npc_count", None)
            traffic_npc_lane_profile = run.get("traffic_npc_lane_profile", None)
            traffic_npc_speed_scale = run.get("traffic_npc_speed_scale", None)
            traffic_npc_speed_jitter_mps = run.get("traffic_npc_speed_jitter_mps", None)
            traffic_suffix = ""
            if traffic_profile_id:
                lane_profile_text = "n/a"
                if isinstance(traffic_npc_lane_profile, list) and traffic_npc_lane_profile:
                    lane_profile_text = ",".join(str(int(value)) for value in traffic_npc_lane_profile)
                traffic_suffix = (
                    f" traffic_profile_id={traffic_profile_id}"
                    f" actor_pattern={traffic_actor_pattern_id or 'n/a'}"
                    f" npc_count={traffic_npc_count if traffic_npc_count is not None else 'n/a'}"
                    f" lane_profile={lane_profile_text}"
                    f" speed_scale={traffic_npc_speed_scale if traffic_npc_speed_scale is not None else 'n/a'}"
                    f" speed_jitter={traffic_npc_speed_jitter_mps if traffic_npc_speed_jitter_mps is not None else 'n/a'}"
                )
            print(
                "[dry-run] "
                f"run_id={run['run_id']} scenario={run['scenario']} seed={run['seed']} "
                f"run_signature={sha256_of_payload(run)}"
                f"{traffic_suffix}"
            )
        return 0

    batch_root.mkdir(parents=True, exist_ok=True)

    batch_started_at = iso_now()
    batch_started_wall = time.perf_counter()

    run_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures = [
            executor.submit(
                run_one,
                run_spec=run,
                spec_dir=spec_dir,
                batch_id=batch_id,
                batch_root=batch_root,
                sim_runner=sim_runner,
                defaults=defaults,
                timeout_sec=timeout_val,
            )
            for run in spec["runs"]
        ]

        for future in as_completed(futures):
            result = future.result()
            run_results.append(result)
            print(
                f"[run] run_id={result['run_id']} status={result['status']} "
                f"termination={result['termination_reason']}"
            )

    run_results.sort(key=lambda item: item["run_id"])

    batch_finished_at = iso_now()
    batch_wall_time = round(time.perf_counter() - batch_started_wall, 6)

    success_count = sum(1 for r in run_results if r["status"] == "success")
    fail_count = sum(1 for r in run_results if r["status"] == "failed")
    timeout_count = sum(1 for r in run_results if r["status"] == "timeout")
    ttc_same_lane_values = [
        value
        for value in (_coerce_optional_float(row.get("min_ttc_same_lane_sec")) for row in run_results)
        if value is not None
    ]
    ttc_adjacent_lane_values = [
        value
        for value in (_coerce_optional_float(row.get("min_ttc_adjacent_lane_sec")) for row in run_results)
        if value is not None
    ]
    ttc_any_lane_values = [
        value
        for value in (_coerce_optional_float(row.get("min_ttc_any_lane_sec")) for row in run_results)
        if value is not None
    ]
    lane_risk_summary_runs = sum(
        1 for row in run_results if isinstance(row.get("lane_risk_summary"), dict)
    )
    lane_risk_batch_summary = {
        "lane_risk_batch_summary_schema_version": "lane_risk_batch_summary_v0",
        "lane_risk_summary_run_count": lane_risk_summary_runs,
        "min_ttc_same_lane_sec": None if not ttc_same_lane_values else round(min(ttc_same_lane_values), 6),
        "min_ttc_adjacent_lane_sec": (
            None if not ttc_adjacent_lane_values else round(min(ttc_adjacent_lane_values), 6)
        ),
        "min_ttc_any_lane_sec": None if not ttc_any_lane_values else round(min(ttc_any_lane_values), 6),
        "ttc_under_3s_same_lane_total": sum(
            _coerce_int(row.get("ttc_under_3s_same_lane_count"), default=0) for row in run_results
        ),
        "ttc_under_3s_adjacent_lane_total": sum(
            _coerce_int(row.get("ttc_under_3s_adjacent_lane_count"), default=0) for row in run_results
        ),
        "same_lane_rows_total": sum(_coerce_int(row.get("same_lane_rows"), default=0) for row in run_results),
        "adjacent_lane_rows_total": sum(
            _coerce_int(row.get("adjacent_lane_rows"), default=0) for row in run_results
        ),
        "other_lane_rows_total": sum(_coerce_int(row.get("other_lane_rows"), default=0) for row in run_results),
    }

    batch_result = {
        "batch_id": batch_id,
        "spec_path": str(batch_spec_path),
        "batch_spec_sha256": batch_spec_sha256,
        "run_plan_sha256": run_plan_sha256,
        "run_plan": run_plan_payload,
        "started_at": batch_started_at,
        "finished_at": batch_finished_at,
        "wall_time_sec": batch_wall_time,
        "run_count": len(run_results),
        "success_count": success_count,
        "fail_count": fail_count,
        "timeout_count": timeout_count,
        "lane_risk_batch_summary": lane_risk_batch_summary,
        "runs": run_results,
    }

    batch_result_path = batch_root / "batch_result.json"
    batch_result_path.write_text(
        json.dumps(batch_result, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    print(f"[ok] batch_id={batch_id}")
    print(f"[ok] result={batch_result_path}")
    print(f"[ok] success={success_count} fail={fail_count} timeout={timeout_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
