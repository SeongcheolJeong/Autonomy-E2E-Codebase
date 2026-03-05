#!/usr/bin/env python3
"""Run a traffic-parameter matrix sweep on top of core_sim_runner.py."""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


CORE_SIM_MATRIX_SWEEP_SCHEMA_VERSION_V0 = "core_sim_matrix_sweep_report_v0"
ERROR_SOURCE = "core_sim_matrix_sweep_runner.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run core-sim traffic parameter matrix sweep")
    parser.add_argument(
        "--core-sim-runner",
        default=str(Path(__file__).resolve().with_name("core_sim_runner.py")),
        help="Path to core_sim_runner.py",
    )
    parser.add_argument("--scenario", required=True, help="Scenario JSON path")
    parser.add_argument("--out-root", required=True, help="Output root for per-case run directories")
    parser.add_argument("--report-out", required=True, help="Output JSON report path")
    parser.add_argument("--run-id-prefix", default="RUN_CORE_SIM_SWEEP", help="Run ID prefix for matrix cases")
    parser.add_argument(
        "--traffic-profile-ids",
        default="sumo_highway_aggressive_v0,sumo_highway_balanced_v0",
        help="Comma-separated traffic profile IDs",
    )
    parser.add_argument(
        "--traffic-actor-pattern-ids",
        default="sumo_platoon_sparse_v0,sumo_platoon_balanced_v0,sumo_dense_aggressive_v0",
        help="Comma-separated traffic actor-pattern IDs",
    )
    parser.add_argument(
        "--traffic-npc-speed-scale-values",
        default="0.9,1.0,1.1",
        help="Comma-separated positive traffic_npc_speed_scale values",
    )
    parser.add_argument(
        "--tire-friction-coeff-values",
        default="0.4,0.7,1.0",
        help="Comma-separated positive tire_friction_coeff values",
    )
    parser.add_argument(
        "--surface-friction-scale-values",
        default="0.8,1.0",
        help="Comma-separated positive surface_friction_scale values",
    )
    parser.add_argument(
        "--enable-ego-collision-avoidance",
        action="store_true",
        help="Enable ego collision-avoidance for every matrix case",
    )
    parser.add_argument(
        "--avoidance-ttc-threshold-sec",
        default="2.5",
        help="TTC trigger threshold when collision avoidance is enabled (>0)",
    )
    parser.add_argument(
        "--ego-max-brake-mps2",
        default="6.0",
        help="Ego max brake when collision avoidance is enabled (>0)",
    )
    parser.add_argument(
        "--max-cases",
        default="0",
        help="Optional cap for executed matrix cases (0 means all)",
    )
    parser.add_argument("--python-bin", default="python3", help="Python executable for core-sim runs")
    return parser.parse_args()


def _emit_error(message: str) -> int:
    normalized = str(message).strip() or "unknown_error"
    print(f"[error] core_sim_matrix_sweep_runner.py: {normalized}", file=sys.stderr)
    write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=normalized)
    return 2


def _parse_csv_text_items(raw: str, *, field: str) -> list[str]:
    items = [item.strip() for item in str(raw).split(",") if item.strip()]
    if not items:
        raise ValueError(f"{field} must contain at least one non-empty item")
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _parse_csv_positive_floats(raw: str, *, field: str) -> list[float]:
    values: list[float] = []
    seen: set[float] = set()
    for token_raw in str(raw).split(","):
        token = token_raw.strip()
        if not token:
            continue
        try:
            value = float(token)
        except ValueError as exc:
            raise ValueError(f"{field} must contain only numbers, got: {token_raw}") from exc
        if value <= 0.0:
            raise ValueError(f"{field} values must be > 0, got: {value}")
        rounded = round(value, 9)
        if rounded in seen:
            continue
        seen.add(rounded)
        values.append(float(rounded))
    if not values:
        raise ValueError(f"{field} must contain at least one positive value")
    return values


def _parse_non_negative_int(raw: str, *, field: str) -> int:
    value = str(raw).strip()
    if not value:
        return 0
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer, got: {raw}") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be >= 0, got: {parsed}")
    return parsed


def _parse_positive_float(raw: str, *, field: str) -> float:
    value = str(raw).strip()
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number, got: {raw}") from exc
    if parsed <= 0.0:
        raise ValueError(f"{field} must be > 0, got: {parsed}")
    return parsed


def _fmt_float(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _load_summary(summary_path: Path) -> dict[str, Any]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"summary payload must be JSON object: {summary_path}")
    return payload


def main() -> int:
    try:
        args = parse_args()
        core_sim_runner_path = Path(args.core_sim_runner).resolve()
        scenario_path = Path(args.scenario).resolve()
        out_root = Path(args.out_root).resolve()
        report_out = Path(args.report_out).resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        report_out.parent.mkdir(parents=True, exist_ok=True)

        if not core_sim_runner_path.exists():
            raise FileNotFoundError(f"core-sim runner not found: {core_sim_runner_path}")
        if not scenario_path.exists():
            raise FileNotFoundError(f"scenario file not found: {scenario_path}")

        run_id_prefix = str(args.run_id_prefix).strip() or "RUN_CORE_SIM_SWEEP"
        traffic_profile_ids = _parse_csv_text_items(
            args.traffic_profile_ids,
            field="traffic-profile-ids",
        )
        traffic_actor_pattern_ids = _parse_csv_text_items(
            args.traffic_actor_pattern_ids,
            field="traffic-actor-pattern-ids",
        )
        traffic_npc_speed_scale_values = _parse_csv_positive_floats(
            args.traffic_npc_speed_scale_values,
            field="traffic-npc-speed-scale-values",
        )
        tire_friction_coeff_values = _parse_csv_positive_floats(
            args.tire_friction_coeff_values,
            field="tire-friction-coeff-values",
        )
        surface_friction_scale_values = _parse_csv_positive_floats(
            args.surface_friction_scale_values,
            field="surface-friction-scale-values",
        )
        max_cases = _parse_non_negative_int(args.max_cases, field="max-cases")

        avoidance_ttc_threshold_sec = _parse_positive_float(
            args.avoidance_ttc_threshold_sec,
            field="avoidance-ttc-threshold-sec",
        )
        ego_max_brake_mps2 = _parse_positive_float(
            args.ego_max_brake_mps2,
            field="ego-max-brake-mps2",
        )

        case_grid = list(
            itertools.product(
                traffic_profile_ids,
                traffic_actor_pattern_ids,
                traffic_npc_speed_scale_values,
                tire_friction_coeff_values,
                surface_friction_scale_values,
            )
        )
        if max_cases > 0:
            case_grid = case_grid[:max_cases]
        if not case_grid:
            raise ValueError("matrix case grid resolved to empty set")

        status_counts: dict[str, int] = {}
        returncode_counts: dict[str, int] = {}
        case_rows: list[dict[str, Any]] = []
        success_case_count = 0
        collision_case_count = 0
        timeout_case_count = 0
        min_ttc_same_lane_sec_min: float | None = None
        min_ttc_any_lane_sec_min: float | None = None
        lowest_ttc_same_lane_run_id = ""
        lowest_ttc_any_lane_run_id = ""

        for index, case in enumerate(case_grid, start=1):
            (
                traffic_profile_id,
                traffic_actor_pattern_id,
                traffic_npc_speed_scale,
                tire_friction_coeff,
                surface_friction_scale,
            ) = case
            run_id = f"{run_id_prefix}_{index:04d}"
            cmd = [
                str(args.python_bin),
                str(core_sim_runner_path),
                "--scenario",
                str(scenario_path),
                "--run-id",
                run_id,
                "--out",
                str(out_root),
                "--traffic-profile-id",
                traffic_profile_id,
                "--traffic-actor-pattern-id",
                traffic_actor_pattern_id,
                "--traffic-npc-speed-scale",
                _fmt_float(traffic_npc_speed_scale),
                "--tire-friction-coeff",
                _fmt_float(tire_friction_coeff),
                "--surface-friction-scale",
                _fmt_float(surface_friction_scale),
            ]
            if args.enable_ego_collision_avoidance:
                cmd.extend(
                    [
                        "--enable-ego-collision-avoidance",
                        "true",
                        "--avoidance-ttc-threshold-sec",
                        _fmt_float(avoidance_ttc_threshold_sec),
                        "--ego-max-brake-mps2",
                        _fmt_float(ego_max_brake_mps2),
                    ]
                )

            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            summary_path = (out_root / run_id / "summary.json").resolve()
            summary_exists = summary_path.exists()
            summary_payload: dict[str, Any] = {}
            if summary_exists:
                try:
                    summary_payload = _load_summary(summary_path)
                except (OSError, json.JSONDecodeError, ValueError):
                    summary_payload = {}

            status = str(summary_payload.get("status", "")).strip().lower() if summary_payload else ""
            if status:
                status_counts[status] = status_counts.get(status, 0) + 1
            returncode_key = str(int(proc.returncode))
            returncode_counts[returncode_key] = returncode_counts.get(returncode_key, 0) + 1

            collision = bool(summary_payload.get("collision", False))
            timeout = bool(summary_payload.get("timeout", False))
            if collision:
                collision_case_count += 1
            if timeout:
                timeout_case_count += 1

            min_ttc_same_lane_sec: float | None = None
            min_ttc_any_lane_sec: float | None = None
            try:
                raw_same = summary_payload.get("min_ttc_same_lane_sec")
                if raw_same is not None:
                    min_ttc_same_lane_sec = float(raw_same)
            except (TypeError, ValueError):
                min_ttc_same_lane_sec = None
            try:
                raw_any = summary_payload.get("min_ttc_any_lane_sec")
                if raw_any is not None:
                    min_ttc_any_lane_sec = float(raw_any)
            except (TypeError, ValueError):
                min_ttc_any_lane_sec = None
            if min_ttc_same_lane_sec is not None and (
                min_ttc_same_lane_sec_min is None or min_ttc_same_lane_sec < min_ttc_same_lane_sec_min
            ):
                min_ttc_same_lane_sec_min = float(min_ttc_same_lane_sec)
                lowest_ttc_same_lane_run_id = run_id
            if min_ttc_any_lane_sec is not None and (
                min_ttc_any_lane_sec_min is None or min_ttc_any_lane_sec < min_ttc_any_lane_sec_min
            ):
                min_ttc_any_lane_sec_min = float(min_ttc_any_lane_sec)
                lowest_ttc_any_lane_run_id = run_id

            if proc.returncode == 0 and summary_exists:
                success_case_count += 1

            case_rows.append(
                {
                    "run_id": run_id,
                    "traffic_profile_id": traffic_profile_id,
                    "traffic_actor_pattern_id": traffic_actor_pattern_id,
                    "traffic_npc_speed_scale": float(traffic_npc_speed_scale),
                    "tire_friction_coeff": float(tire_friction_coeff),
                    "surface_friction_scale": float(surface_friction_scale),
                    "returncode": int(proc.returncode),
                    "summary_path": str(summary_path),
                    "summary_exists": bool(summary_exists),
                    "status": status,
                    "collision": bool(collision),
                    "timeout": bool(timeout),
                    "min_ttc_same_lane_sec": min_ttc_same_lane_sec,
                    "min_ttc_any_lane_sec": min_ttc_any_lane_sec,
                    "stderr_tail": "\n".join(
                        [line for line in str(proc.stderr).splitlines()[-5:] if line.strip()]
                    ),
                }
            )

        case_count = len(case_rows)
        failed_case_count = case_count - success_case_count
        report_payload = {
            "core_sim_matrix_sweep_schema_version": CORE_SIM_MATRIX_SWEEP_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "core_sim_runner": str(core_sim_runner_path),
            "scenario_path": str(scenario_path),
            "out_root": str(out_root),
            "run_id_prefix": run_id_prefix,
            "enable_ego_collision_avoidance": bool(args.enable_ego_collision_avoidance),
            "avoidance_ttc_threshold_sec": float(avoidance_ttc_threshold_sec),
            "ego_max_brake_mps2": float(ego_max_brake_mps2),
            "max_cases": int(max_cases),
            "input_grid": {
                "traffic_profile_ids": traffic_profile_ids,
                "traffic_actor_pattern_ids": traffic_actor_pattern_ids,
                "traffic_npc_speed_scale_values": [float(value) for value in traffic_npc_speed_scale_values],
                "tire_friction_coeff_values": [float(value) for value in tire_friction_coeff_values],
                "surface_friction_scale_values": [float(value) for value in surface_friction_scale_values],
            },
            "case_count": int(case_count),
            "success_case_count": int(success_case_count),
            "failed_case_count": int(failed_case_count),
            "all_cases_success": bool(failed_case_count == 0),
            "status_counts": {key: int(status_counts[key]) for key in sorted(status_counts.keys())},
            "returncode_counts": {
                key: int(returncode_counts[key]) for key in sorted(returncode_counts.keys(), key=int)
            },
            "collision_case_count": int(collision_case_count),
            "timeout_case_count": int(timeout_case_count),
            "min_ttc_same_lane_sec_min": min_ttc_same_lane_sec_min,
            "lowest_ttc_same_lane_run_id": lowest_ttc_same_lane_run_id,
            "min_ttc_any_lane_sec_min": min_ttc_any_lane_sec_min,
            "lowest_ttc_any_lane_run_id": lowest_ttc_any_lane_run_id,
            "cases": case_rows,
        }
        report_out.write_text(json.dumps(report_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        print(f"[ok] case_count={case_count}")
        print(f"[ok] success_case_count={success_case_count}")
        print(f"[ok] failed_case_count={failed_case_count}")
        print(f"[ok] report_out={report_out}")
        if success_case_count <= 0:
            print("[error] core_sim_matrix_sweep_runner.py: no successful core-sim matrix case", file=sys.stderr)
            write_ci_error_summary(
                source=ERROR_SOURCE,
                phase=ERROR_PHASE,
                message="no successful core-sim matrix case",
            )
            return 2
        return 0
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        return _emit_error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

