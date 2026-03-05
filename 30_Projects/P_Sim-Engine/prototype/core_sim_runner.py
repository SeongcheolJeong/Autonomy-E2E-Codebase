#!/usr/bin/env python3
"""Minimal deterministic Object-Sim runner (v0 prototype)."""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


SCENARIO_SCHEMA_VERSION_V0 = "scenario_definition_v0"
ERROR_SOURCE = "core_sim_runner.py"
ERROR_PHASE = "resolve_inputs"
TRAFFIC_ACTOR_PATTERN_LIBRARY_V0: dict[str, dict[str, Any]] = {
    "sumo_platoon_sparse_v0": {
        "traffic_npc_count": 2,
        "traffic_npc_initial_gap_m": 45.0,
        "traffic_npc_gap_step_m": 28.0,
        "traffic_npc_speed_offset_mps": -2.0,
        "traffic_npc_lane_profile": [0, 1],
        "gap_step_multipliers": [0.0, 1.8],
        "speed_slot_offsets_mps": [0.0, -0.5],
    },
    "sumo_platoon_balanced_v0": {
        "traffic_npc_count": 3,
        "traffic_npc_initial_gap_m": 34.0,
        "traffic_npc_gap_step_m": 22.0,
        "traffic_npc_speed_offset_mps": 0.0,
        "traffic_npc_lane_profile": [0, 1, -1],
        "gap_step_multipliers": [0.0, 1.1, 2.4],
        "speed_slot_offsets_mps": [0.4, 0.0, -0.3],
    },
    "sumo_dense_aggressive_v0": {
        "traffic_npc_count": 4,
        "traffic_npc_initial_gap_m": 24.0,
        "traffic_npc_gap_step_m": 16.0,
        "traffic_npc_speed_offset_mps": 3.0,
        "traffic_npc_lane_profile": [0, 1, 0, -1],
        "gap_step_multipliers": [0.0, 0.9, 2.1, 3.4],
        "speed_slot_offsets_mps": [1.0, 0.7, 0.3, -0.1],
    },
}


@dataclass
class ActorState:
    actor_id: str
    position_m: float
    speed_mps: float
    length_m: float = 4.8
    lane_index: int = 0


@dataclass
class ScenarioConfig:
    scenario_schema_version: str
    scenario_id: str
    duration_sec: float
    dt_sec: float
    ego: ActorState
    npcs: list[ActorState]
    npc_speed_jitter_mps: float = 0.0
    enable_ego_collision_avoidance: bool = False
    avoidance_ttc_threshold_sec: float = 0.0
    ego_max_brake_mps2: float = 0.0
    tire_friction_coeff: float = 1.0
    surface_friction_scale: float = 1.0
    wall_timeout_sec: float | None = None


class ScenarioValidationError(Exception):
    pass


def _emit_error(message: str) -> int:
    normalized = str(message).strip() or "unknown_error"
    print(f"[error] {normalized}")
    write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=normalized)
    return 2


def _require_keys(payload: dict[str, Any], keys: list[str]) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ScenarioValidationError(f"missing required keys: {missing}")


def _as_actor(payload: dict[str, Any], fallback_id: str) -> ActorState:
    _require_keys(payload, ["position_m", "speed_mps"])
    return ActorState(
        actor_id=str(payload.get("actor_id", fallback_id)),
        position_m=float(payload["position_m"]),
        speed_mps=float(payload["speed_mps"]),
        length_m=float(payload.get("length_m", 4.8)),
        lane_index=0,
    )


def _parse_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    raise ScenarioValidationError(f"{field} must be a boolean")


def load_scenario(path: Path) -> ScenarioConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require_keys(
        payload,
        ["scenario_schema_version", "scenario_id", "duration_sec", "dt_sec", "ego", "npcs"],
    )

    scenario_schema_version = str(payload["scenario_schema_version"])
    if scenario_schema_version != SCENARIO_SCHEMA_VERSION_V0:
        raise ScenarioValidationError(
            "unsupported scenario_schema_version: "
            f"{scenario_schema_version}; expected {SCENARIO_SCHEMA_VERSION_V0}"
        )

    ego = _as_actor(payload["ego"], "ego")
    npcs_raw = payload["npcs"]
    if not isinstance(npcs_raw, list) or len(npcs_raw) == 0:
        raise ScenarioValidationError("npcs must be a non-empty list")

    npcs = [_as_actor(npc, f"npc_{idx + 1}") for idx, npc in enumerate(npcs_raw)]
    duration_sec = float(payload["duration_sec"])
    dt_sec = float(payload["dt_sec"])
    if duration_sec <= 0:
        raise ScenarioValidationError("duration_sec must be > 0")
    if dt_sec <= 0:
        raise ScenarioValidationError("dt_sec must be > 0")

    wall_timeout_raw = payload.get("wall_timeout_sec")
    wall_timeout_sec = None if wall_timeout_raw is None else float(wall_timeout_raw)
    if wall_timeout_sec is not None and wall_timeout_sec <= 0:
        raise ScenarioValidationError("wall_timeout_sec must be > 0 when provided")
    enable_ego_collision_avoidance = _parse_bool(
        payload.get("enable_ego_collision_avoidance", False),
        field="enable_ego_collision_avoidance",
    )
    avoidance_ttc_threshold_sec = float(payload.get("avoidance_ttc_threshold_sec", 0.0))
    ego_max_brake_mps2 = float(payload.get("ego_max_brake_mps2", 0.0))
    tire_friction_coeff = float(payload.get("tire_friction_coeff", 1.0))
    surface_friction_scale = float(payload.get("surface_friction_scale", 1.0))
    if avoidance_ttc_threshold_sec < 0:
        raise ScenarioValidationError("avoidance_ttc_threshold_sec must be >= 0")
    if ego_max_brake_mps2 < 0:
        raise ScenarioValidationError("ego_max_brake_mps2 must be >= 0")
    if tire_friction_coeff <= 0:
        raise ScenarioValidationError("tire_friction_coeff must be > 0")
    if surface_friction_scale <= 0:
        raise ScenarioValidationError("surface_friction_scale must be > 0")
    if enable_ego_collision_avoidance and (
        avoidance_ttc_threshold_sec <= 0 or ego_max_brake_mps2 <= 0
    ):
        raise ScenarioValidationError(
            "enable_ego_collision_avoidance requires avoidance_ttc_threshold_sec > 0 and ego_max_brake_mps2 > 0"
        )

    return ScenarioConfig(
        scenario_schema_version=scenario_schema_version,
        scenario_id=str(payload["scenario_id"]),
        duration_sec=duration_sec,
        dt_sec=dt_sec,
        ego=ego,
        npcs=npcs,
        npc_speed_jitter_mps=float(payload.get("npc_speed_jitter_mps", 0.0)),
        enable_ego_collision_avoidance=enable_ego_collision_avoidance,
        avoidance_ttc_threshold_sec=avoidance_ttc_threshold_sec,
        ego_max_brake_mps2=ego_max_brake_mps2,
        tire_friction_coeff=tire_friction_coeff,
        surface_friction_scale=surface_friction_scale,
        wall_timeout_sec=wall_timeout_sec,
    )


class CoreSimRunner:
    def __init__(self, scenario: ScenarioConfig, seed: int) -> None:
        self.scenario = scenario
        self.seed = seed
        self.rng = random.Random(seed)
        self.time_sec = 0.0
        self.step_count = 0
        self.min_ttc_same_lane_sec = float("inf")
        self.min_ttc_adjacent_lane_sec = float("inf")
        self.min_ttc_sec = float("inf")
        self.collision = False
        self.timeout = False

        self.ego = ActorState(**vars(scenario.ego))
        self.npcs = [ActorState(**vars(npc)) for npc in scenario.npcs]

        if scenario.npc_speed_jitter_mps > 0:
            for npc in self.npcs:
                jitter = self.rng.uniform(-scenario.npc_speed_jitter_mps, scenario.npc_speed_jitter_mps)
                npc.speed_mps += jitter

        self.trace_rows: list[dict[str, Any]] = []
        self.ego_avoidance_brake_event_count = 0
        self.ego_avoidance_applied_brake_mps2_max = 0.0

    def run(self) -> dict[str, Any]:
        started_wall = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()

        while (
            self.time_sec < self.scenario.duration_sec
            and not self.collision
            and not self.timeout
        ):
            if self.scenario.wall_timeout_sec is not None:
                elapsed_wall = time.perf_counter() - started_wall
                if elapsed_wall > self.scenario.wall_timeout_sec:
                    self.timeout = True
                    break
            self._step()

        finished_wall = time.perf_counter()
        finished_at = datetime.now(timezone.utc).isoformat()

        if self.collision:
            status = "failed"
            termination_reason = "collision"
        elif self.timeout:
            status = "timeout"
            termination_reason = "timeout"
        else:
            status = "success"
            termination_reason = "completed"
        min_ttc_same_lane = (
            None if self.min_ttc_same_lane_sec == float("inf") else round(self.min_ttc_same_lane_sec, 6)
        )
        min_ttc_adjacent_lane = (
            None
            if self.min_ttc_adjacent_lane_sec == float("inf")
            else round(self.min_ttc_adjacent_lane_sec, 6)
        )
        finite_ttc_values = [
            value
            for value in (self.min_ttc_same_lane_sec, self.min_ttc_adjacent_lane_sec)
            if value != float("inf")
        ]
        min_ttc_any_lane = None if not finite_ttc_values else round(min(finite_ttc_values), 6)

        return {
            "scenario_schema_version": self.scenario.scenario_schema_version,
            "scenario_id": self.scenario.scenario_id,
            "status": status,
            "termination_reason": termination_reason,
            "seed": self.seed,
            "step_count": self.step_count,
            "sim_duration_sec": round(self.time_sec, 6),
            "wall_time_sec": round(finished_wall - started_wall, 6),
            "min_ttc_sec": min_ttc_same_lane,
            "min_ttc_same_lane_sec": min_ttc_same_lane,
            "min_ttc_adjacent_lane_sec": min_ttc_adjacent_lane,
            "min_ttc_any_lane_sec": min_ttc_any_lane,
            "collision": self.collision,
            "timeout": self.timeout,
            "started_at": started_at,
            "finished_at": finished_at,
        }

    def _step(self) -> None:
        dt = self.scenario.dt_sec
        self.time_sec += dt
        self.step_count += 1
        avoidance_action = self._apply_ego_collision_avoidance(dt)
        self.ego.position_m += self.ego.speed_mps * dt
        for npc in self.npcs:
            npc.position_m += npc.speed_mps * dt

        for npc in self.npcs:
            gap_m = npc.position_m - self.ego.position_m - 0.5 * (npc.length_m + self.ego.length_m)
            rel_speed_mps = self.ego.speed_mps - npc.speed_mps
            ttc_sec = None
            ttc_same_lane_sec = None
            ttc_adjacent_lane_sec = None
            lane_delta = abs(int(npc.lane_index) - int(self.ego.lane_index))
            same_lane = bool(npc.lane_index == self.ego.lane_index)
            adjacent_lane = bool(lane_delta == 1)

            if same_lane and gap_m <= 0:
                self.collision = True
            elif rel_speed_mps > 0 and (same_lane or adjacent_lane):
                ttc_value = gap_m / rel_speed_mps
                if same_lane:
                    self.min_ttc_same_lane_sec = min(self.min_ttc_same_lane_sec, ttc_value)
                    self.min_ttc_sec = min(self.min_ttc_sec, ttc_value)
                    ttc_same_lane_sec = round(ttc_value, 6)
                    ttc_sec = ttc_same_lane_sec
                elif adjacent_lane:
                    self.min_ttc_adjacent_lane_sec = min(self.min_ttc_adjacent_lane_sec, ttc_value)
                    ttc_adjacent_lane_sec = round(ttc_value, 6)

            self.trace_rows.append(
                {
                    "time_sec": round(self.time_sec, 6),
                    "ego_position_m": round(self.ego.position_m, 6),
                    "ego_speed_mps": round(self.ego.speed_mps, 6),
                    "ego_lane_index": int(self.ego.lane_index),
                    "npc_id": npc.actor_id,
                    "npc_position_m": round(npc.position_m, 6),
                    "npc_lane_index": int(npc.lane_index),
                    "lane_delta": lane_delta,
                    "same_lane": same_lane,
                    "adjacent_lane": adjacent_lane,
                    "gap_m": round(gap_m, 6),
                    "relative_speed_mps": round(rel_speed_mps, 6),
                    "ttc_sec": ttc_sec,
                    "ttc_same_lane_sec": ttc_same_lane_sec,
                    "ttc_adjacent_lane_sec": ttc_adjacent_lane_sec,
                    "ego_avoidance_brake_applied": bool(
                        avoidance_action.get("brake_applied", False)
                    ),
                    "ego_avoidance_ttc_sec": avoidance_action.get("ttc_sec"),
                    "ego_avoidance_applied_brake_mps2": avoidance_action.get("applied_brake_mps2"),
                    "ego_avoidance_effective_brake_limit_mps2": avoidance_action.get(
                        "effective_brake_limit_mps2"
                    ),
                    "ego_surface_friction_scale": round(self.scenario.surface_friction_scale, 6),
                    "collision": self.collision,
                }
            )

    def _apply_ego_collision_avoidance(self, dt_sec: float) -> dict[str, Any]:
        result: dict[str, Any] = {
            "brake_applied": False,
            "ttc_sec": None,
            "applied_brake_mps2": None,
            "effective_brake_limit_mps2": None,
        }
        if not self.scenario.enable_ego_collision_avoidance:
            return result
        if self.scenario.avoidance_ttc_threshold_sec <= 0 or self.scenario.ego_max_brake_mps2 <= 0:
            return result
        same_lane_leads = [
            npc
            for npc in self.npcs
            if int(npc.lane_index) == int(self.ego.lane_index) and npc.position_m > self.ego.position_m
        ]
        if not same_lane_leads:
            return result
        lead = min(same_lane_leads, key=lambda row: row.position_m)
        gap_m = lead.position_m - self.ego.position_m - 0.5 * (lead.length_m + self.ego.length_m)
        rel_speed_mps = self.ego.speed_mps - lead.speed_mps
        if gap_m <= 0 or rel_speed_mps <= 0:
            return result
        ttc_sec = gap_m / rel_speed_mps
        result["ttc_sec"] = round(ttc_sec, 6)
        if ttc_sec > self.scenario.avoidance_ttc_threshold_sec:
            return result
        friction_brake_limit_mps2 = (
            self.scenario.tire_friction_coeff * self.scenario.surface_friction_scale * 9.80665
        )
        effective_brake_limit_mps2 = min(
            max(0.0, self.scenario.ego_max_brake_mps2),
            max(0.0, friction_brake_limit_mps2),
        )
        if effective_brake_limit_mps2 <= 0:
            result["effective_brake_limit_mps2"] = 0.0
            return result
        self.ego.speed_mps = max(0.0, self.ego.speed_mps - (effective_brake_limit_mps2 * dt_sec))
        self.ego_avoidance_brake_event_count += 1
        self.ego_avoidance_applied_brake_mps2_max = max(
            self.ego_avoidance_applied_brake_mps2_max,
            effective_brake_limit_mps2,
        )
        result["brake_applied"] = True
        result["applied_brake_mps2"] = round(effective_brake_limit_mps2, 6)
        result["effective_brake_limit_mps2"] = round(effective_brake_limit_mps2, 6)
        return result


def write_artifacts(
    out_root: Path,
    run_id: str,
    summary: dict[str, Any],
    trace_rows: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    trace_path = run_dir / "trace.csv"
    with trace_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "time_sec",
                "ego_position_m",
                "ego_speed_mps",
                "ego_lane_index",
                "npc_id",
                "npc_position_m",
                "npc_lane_index",
                "lane_delta",
                "same_lane",
                "adjacent_lane",
                "gap_m",
                "relative_speed_mps",
                "ttc_sec",
                "ttc_same_lane_sec",
                "ttc_adjacent_lane_sec",
                "ego_avoidance_brake_applied",
                "ego_avoidance_ttc_sec",
                "ego_avoidance_applied_brake_mps2",
                "ego_avoidance_effective_brake_limit_mps2",
                "ego_surface_friction_scale",
                "collision",
            ],
        )
        writer.writeheader()
        writer.writerows(trace_rows)

    same_lane_rows = [row for row in trace_rows if bool(row.get("same_lane", False))]
    adjacent_lane_rows = [row for row in trace_rows if bool(row.get("adjacent_lane", False))]
    other_lane_rows = [
        row
        for row in trace_rows
        if not bool(row.get("same_lane", False)) and not bool(row.get("adjacent_lane", False))
    ]

    def _collect_numeric(rows: list[dict[str, Any]], key: str) -> list[float]:
        values: list[float] = []
        for row in rows:
            raw = row.get(key)
            if raw is None:
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        return values

    min_gap_same_lane = _collect_numeric(same_lane_rows, "gap_m")
    min_gap_adjacent_lane = _collect_numeric(adjacent_lane_rows, "gap_m")
    ttc_same_lane = _collect_numeric(same_lane_rows, "ttc_same_lane_sec")
    ttc_adjacent_lane = _collect_numeric(adjacent_lane_rows, "ttc_adjacent_lane_sec")
    lane_risk_summary_payload = {
        "lane_risk_summary_schema_version": "lane_risk_summary_v0",
        "run_id": run_id,
        "step_rows_total": len(trace_rows),
        "same_lane_rows": len(same_lane_rows),
        "adjacent_lane_rows": len(adjacent_lane_rows),
        "other_lane_rows": len(other_lane_rows),
        "collision_flag": bool(summary.get("collision", False)),
        "same_lane_collision_rows": sum(1 for row in same_lane_rows if bool(row.get("collision", False))),
        "adjacent_lane_collision_rows": sum(1 for row in adjacent_lane_rows if bool(row.get("collision", False))),
        "min_gap_same_lane_m": None if not min_gap_same_lane else round(min(min_gap_same_lane), 6),
        "min_gap_adjacent_lane_m": None if not min_gap_adjacent_lane else round(min(min_gap_adjacent_lane), 6),
        "min_ttc_same_lane_sec": summary.get("min_ttc_same_lane_sec"),
        "min_ttc_adjacent_lane_sec": summary.get("min_ttc_adjacent_lane_sec"),
        "min_ttc_any_lane_sec": summary.get("min_ttc_any_lane_sec"),
        "ttc_under_3s_same_lane_count": sum(1 for value in ttc_same_lane if value <= 3.0),
        "ttc_under_3s_adjacent_lane_count": sum(1 for value in ttc_adjacent_lane if value <= 3.0),
    }
    lane_risk_summary_path = run_dir / "lane_risk_summary.json"
    lane_risk_summary_path.write_text(
        json.dumps(lane_risk_summary_payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    summary_payload = dict(summary)
    summary_payload["run_id"] = run_id
    summary_payload["trace_path"] = str(trace_path)
    summary_payload["lane_risk_summary_path"] = str(lane_risk_summary_path)
    summary_payload["lane_risk_summary"] = lane_risk_summary_payload

    summary_path = run_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    return summary_path, trace_path, lane_risk_summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run minimal deterministic Object-Sim scenario")
    parser.add_argument("--scenario", required=True, help="Path to scenario JSON file")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--seed", default="", help="Seed for deterministic behavior")
    parser.add_argument("--out", required=True, help="Output root directory for run artifacts")
    parser.add_argument(
        "--wall-timeout-sec",
        default="",
        help="Optional wall-clock timeout (seconds); overrides scenario value",
    )
    parser.add_argument("--run-source", default="sim_closed_loop", help="Run source type")
    parser.add_argument("--sds-version", default="sds_unknown", help="SDS version identifier")
    parser.add_argument("--sim-version", default="sim_engine_v0_prototype", help="Simulation version identifier")
    parser.add_argument("--fidelity-profile", default="dev-fast", help="Fidelity profile for this run")
    parser.add_argument("--map-id", default="map_unknown", help="Map identifier")
    parser.add_argument("--map-version", default="v0", help="Map version identifier")
    parser.add_argument("--odd-tags", default="", help="Comma-separated ODD tags")
    parser.add_argument("--batch-id", default="", help="Optional batch identifier")
    parser.add_argument("--traffic-profile-id", default="", help="Optional traffic actor profile identifier")
    parser.add_argument("--traffic-actor-pattern-id", default="", help="Optional traffic actor pattern identifier")
    parser.add_argument("--traffic-npc-count", default="", help="Optional override for NPC actor count (>0)")
    parser.add_argument(
        "--traffic-npc-initial-gap-m",
        default="",
        help="Optional override for first NPC gap from ego in meters (>0)",
    )
    parser.add_argument(
        "--traffic-npc-gap-step-m",
        default="",
        help="Optional override for per-NPC gap step in meters (>0)",
    )
    parser.add_argument(
        "--traffic-npc-speed-offset-mps",
        default="",
        help="Optional override for NPC base speed offset relative to ego",
    )
    parser.add_argument(
        "--traffic-npc-lane-profile",
        default="",
        help="Optional comma-separated lane slot profile for generated NPCs (for example: 0,1,-1)",
    )
    parser.add_argument(
        "--traffic-npc-speed-scale",
        default="",
        help="Optional multiplier for NPC initial speeds (>0)",
    )
    parser.add_argument(
        "--traffic-npc-speed-jitter-mps",
        default="",
        help="Optional override for NPC speed jitter (m/s, >=0)",
    )
    parser.add_argument(
        "--enable-ego-collision-avoidance",
        default="",
        help="Optional override for ego collision-avoidance braking (true/false)",
    )
    parser.add_argument(
        "--avoidance-ttc-threshold-sec",
        default="",
        help="Optional override for ego collision-avoidance TTC trigger threshold in seconds (>0)",
    )
    parser.add_argument(
        "--ego-max-brake-mps2",
        default="",
        help="Optional override for ego max braking deceleration in m/s^2 (>0)",
    )
    parser.add_argument(
        "--tire-friction-coeff",
        default="",
        help="Optional override for tire friction coefficient (>0)",
    )
    parser.add_argument(
        "--surface-friction-scale",
        default="",
        help="Optional override for surface friction scale multiplier (>0)",
    )
    return parser.parse_args()


def parse_int(raw: Any, *, default: int, field: str) -> int:
    value = str(raw).strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer, got: {raw}") from exc


def parse_optional_float(raw: Any, *, field: str) -> float | None:
    value = str(raw).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number, got: {raw}") from exc


def parse_optional_positive_float(raw: Any, *, field: str) -> float | None:
    value = str(raw).strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a positive number, got: {raw}") from exc
    if parsed <= 0.0:
        raise ValueError(f"{field} must be > 0, got: {parsed}")
    return parsed


def parse_optional_non_negative_float(raw: Any, *, field: str) -> float | None:
    value = str(raw).strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a non-negative number, got: {raw}") from exc
    if parsed < 0.0:
        raise ValueError(f"{field} must be >= 0, got: {parsed}")
    return parsed


def parse_optional_positive_int(raw: Any, *, field: str) -> int | None:
    value = str(raw).strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a positive integer, got: {raw}") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be > 0, got: {parsed}")
    return parsed


def parse_optional_int_csv(raw: Any, *, field: str) -> list[int] | None:
    value = str(raw).strip()
    if not value:
        return None
    parts = [token.strip() for token in value.split(",")]
    filtered = [token for token in parts if token]
    if not filtered:
        return None
    out: list[int] = []
    for token in filtered:
        try:
            out.append(int(token))
        except ValueError as exc:
            raise ValueError(f"{field} must be a comma-separated integer list, got: {raw}") from exc
    return out


def parse_optional_bool(raw: Any, *, field: str) -> bool | None:
    value = str(raw).strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered in {"true", "1", "yes", "y", "on"}:
        return True
    if lowered in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError(f"{field} must be a boolean, got: {raw}")


def _infer_first_npc_gap_m(scenario: ScenarioConfig) -> float:
    if scenario.npcs:
        first_npc = min(scenario.npcs, key=lambda row: row.position_m)
        return max(1.0, float(first_npc.position_m - scenario.ego.position_m))
    return 30.0


def _coerce_float_list(raw: Any) -> list[float]:
    if not isinstance(raw, list):
        return []
    out: list[float] = []
    for item in raw:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _coerce_int_list(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _resolve_profile_slot(
    profile: list[float],
    index: int,
    *,
    fallback: float,
    extension_step: float,
) -> float:
    if index < len(profile):
        return float(profile[index])
    if not profile:
        return float(fallback)
    overflow_index = index - len(profile) + 1
    return float(profile[-1]) + (extension_step * float(overflow_index))


def _resolve_cyclic_int_slot(profile: list[int], index: int, *, fallback: int) -> int:
    if not profile:
        return int(fallback)
    return int(profile[index % len(profile)])


def resolve_traffic_actor_pattern_defaults(
    *,
    traffic_actor_pattern_id: str,
    traffic_npc_count: int | None,
    traffic_npc_initial_gap_m: float | None,
    traffic_npc_gap_step_m: float | None,
    traffic_npc_speed_offset_mps: float | None,
    traffic_npc_lane_profile: list[int] | None,
) -> tuple[int | None, float | None, float | None, float | None, list[int] | None]:
    defaults: dict[str, Any] = {}
    if traffic_actor_pattern_id:
        defaults = TRAFFIC_ACTOR_PATTERN_LIBRARY_V0.get(traffic_actor_pattern_id, {})
        if not defaults:
            allowed = ", ".join(sorted(TRAFFIC_ACTOR_PATTERN_LIBRARY_V0))
            raise ValueError(
                f"traffic-actor-pattern-id must be one of: {allowed}; got: {traffic_actor_pattern_id}"
            )

    resolved_npc_count = traffic_npc_count
    if resolved_npc_count is None and "traffic_npc_count" in defaults:
        resolved_npc_count = int(defaults["traffic_npc_count"])

    resolved_npc_initial_gap_m = traffic_npc_initial_gap_m
    if resolved_npc_initial_gap_m is None and "traffic_npc_initial_gap_m" in defaults:
        resolved_npc_initial_gap_m = float(defaults["traffic_npc_initial_gap_m"])

    resolved_npc_gap_step_m = traffic_npc_gap_step_m
    if resolved_npc_gap_step_m is None and "traffic_npc_gap_step_m" in defaults:
        resolved_npc_gap_step_m = float(defaults["traffic_npc_gap_step_m"])

    resolved_npc_speed_offset_mps = traffic_npc_speed_offset_mps
    if resolved_npc_speed_offset_mps is None and "traffic_npc_speed_offset_mps" in defaults:
        resolved_npc_speed_offset_mps = float(defaults["traffic_npc_speed_offset_mps"])

    resolved_npc_lane_profile = traffic_npc_lane_profile
    if resolved_npc_lane_profile is None and "traffic_npc_lane_profile" in defaults:
        resolved_npc_lane_profile = _coerce_int_list(defaults["traffic_npc_lane_profile"])

    return (
        resolved_npc_count,
        resolved_npc_initial_gap_m,
        resolved_npc_gap_step_m,
        resolved_npc_speed_offset_mps,
        resolved_npc_lane_profile,
    )


def apply_traffic_actor_pattern(
    scenario: ScenarioConfig,
    *,
    traffic_actor_pattern_id: str,
    traffic_npc_count: int | None,
    traffic_npc_initial_gap_m: float | None,
    traffic_npc_gap_step_m: float | None,
    traffic_npc_speed_offset_mps: float | None,
    traffic_npc_lane_profile: list[int] | None,
) -> ScenarioConfig:
    if (
        traffic_npc_count is None
        and traffic_npc_initial_gap_m is None
        and traffic_npc_gap_step_m is None
        and traffic_npc_speed_offset_mps is None
        and traffic_npc_lane_profile is None
    ):
        return scenario

    effective_npc_count = int(traffic_npc_count) if traffic_npc_count is not None else max(1, len(scenario.npcs))
    base_gap_m = (
        float(traffic_npc_initial_gap_m)
        if traffic_npc_initial_gap_m is not None
        else _infer_first_npc_gap_m(scenario)
    )
    gap_step_m = (
        float(traffic_npc_gap_step_m)
        if traffic_npc_gap_step_m is not None
        else max(8.0, base_gap_m * 0.6)
    )
    speed_offset_mps = float(traffic_npc_speed_offset_mps) if traffic_npc_speed_offset_mps is not None else 0.0
    base_speed_mps = max(0.0, float(scenario.ego.speed_mps) + speed_offset_mps)
    actor_pattern_payload = TRAFFIC_ACTOR_PATTERN_LIBRARY_V0.get(traffic_actor_pattern_id, {})
    gap_step_multipliers = _coerce_float_list(actor_pattern_payload.get("gap_step_multipliers"))
    speed_slot_offsets_mps = _coerce_float_list(actor_pattern_payload.get("speed_slot_offsets_mps"))
    lane_slot_profile = list(traffic_npc_lane_profile or [])

    synthesized_npcs: list[ActorState] = []
    for idx in range(effective_npc_count):
        gap_multiplier = _resolve_profile_slot(
            gap_step_multipliers,
            idx,
            fallback=float(idx),
            extension_step=1.0,
        )
        speed_slot_offset_mps = _resolve_profile_slot(
            speed_slot_offsets_mps,
            idx,
            fallback=-0.2 * float(idx),
            extension_step=-0.2,
        )
        lane_index = _resolve_cyclic_int_slot(lane_slot_profile, idx, fallback=0)
        synthesized_npcs.append(
            ActorState(
                actor_id=f"traffic_{idx + 1:03d}",
                position_m=float(scenario.ego.position_m) + base_gap_m + (gap_step_m * gap_multiplier),
                speed_mps=max(0.0, base_speed_mps + speed_slot_offset_mps),
                length_m=4.8,
                lane_index=lane_index,
            )
        )

    return ScenarioConfig(
        scenario_schema_version=scenario.scenario_schema_version,
        scenario_id=scenario.scenario_id,
        duration_sec=scenario.duration_sec,
        dt_sec=scenario.dt_sec,
        ego=scenario.ego,
        npcs=synthesized_npcs,
        npc_speed_jitter_mps=scenario.npc_speed_jitter_mps,
        wall_timeout_sec=scenario.wall_timeout_sec,
    )


def main() -> int:
    args = parse_args()
    try:
        seed = parse_int(args.seed, default=42, field="seed")
        wall_timeout_override = parse_optional_float(args.wall_timeout_sec, field="wall-timeout-sec")
        traffic_actor_pattern_id = str(args.traffic_actor_pattern_id).strip()
        traffic_npc_count = parse_optional_positive_int(
            args.traffic_npc_count,
            field="traffic-npc-count",
        )
        traffic_npc_initial_gap_m = parse_optional_positive_float(
            args.traffic_npc_initial_gap_m,
            field="traffic-npc-initial-gap-m",
        )
        traffic_npc_gap_step_m = parse_optional_positive_float(
            args.traffic_npc_gap_step_m,
            field="traffic-npc-gap-step-m",
        )
        traffic_npc_speed_offset_mps = parse_optional_float(
            args.traffic_npc_speed_offset_mps,
            field="traffic-npc-speed-offset-mps",
        )
        traffic_npc_lane_profile = parse_optional_int_csv(
            args.traffic_npc_lane_profile,
            field="traffic-npc-lane-profile",
        )
        (
            traffic_npc_count,
            traffic_npc_initial_gap_m,
            traffic_npc_gap_step_m,
            traffic_npc_speed_offset_mps,
            traffic_npc_lane_profile,
        ) = resolve_traffic_actor_pattern_defaults(
            traffic_actor_pattern_id=traffic_actor_pattern_id,
            traffic_npc_count=traffic_npc_count,
            traffic_npc_initial_gap_m=traffic_npc_initial_gap_m,
            traffic_npc_gap_step_m=traffic_npc_gap_step_m,
            traffic_npc_speed_offset_mps=traffic_npc_speed_offset_mps,
            traffic_npc_lane_profile=traffic_npc_lane_profile,
        )
        traffic_npc_speed_scale = parse_optional_positive_float(
            args.traffic_npc_speed_scale,
            field="traffic-npc-speed-scale",
        )
        traffic_npc_speed_jitter_mps = parse_optional_non_negative_float(
            args.traffic_npc_speed_jitter_mps,
            field="traffic-npc-speed-jitter-mps",
        )
        enable_ego_collision_avoidance_override = parse_optional_bool(
            args.enable_ego_collision_avoidance,
            field="enable-ego-collision-avoidance",
        )
        avoidance_ttc_threshold_sec_override = parse_optional_positive_float(
            args.avoidance_ttc_threshold_sec,
            field="avoidance-ttc-threshold-sec",
        )
        ego_max_brake_mps2_override = parse_optional_positive_float(
            args.ego_max_brake_mps2,
            field="ego-max-brake-mps2",
        )
        tire_friction_coeff_override = parse_optional_positive_float(
            args.tire_friction_coeff,
            field="tire-friction-coeff",
        )
        surface_friction_scale_override = parse_optional_positive_float(
            args.surface_friction_scale,
            field="surface-friction-scale",
        )
    except ValueError as exc:
        return _emit_error(str(exc))

    scenario_path = Path(args.scenario).resolve()
    out_root = Path(args.out)

    try:
        scenario = load_scenario(scenario_path)
    except ScenarioValidationError as exc:
        return _emit_error(f"invalid scenario: {exc}")
    except FileNotFoundError:
        return _emit_error(f"scenario file not found: {scenario_path}")

    effective_wall_timeout = wall_timeout_override
    if effective_wall_timeout is None:
        effective_wall_timeout = scenario.wall_timeout_sec
    if effective_wall_timeout is not None and effective_wall_timeout <= 0:
        return _emit_error("invalid wall-timeout-sec; must be > 0")

    scenario = apply_traffic_actor_pattern(
        scenario,
        traffic_actor_pattern_id=traffic_actor_pattern_id,
        traffic_npc_count=traffic_npc_count,
        traffic_npc_initial_gap_m=traffic_npc_initial_gap_m,
        traffic_npc_gap_step_m=traffic_npc_gap_step_m,
        traffic_npc_speed_offset_mps=traffic_npc_speed_offset_mps,
        traffic_npc_lane_profile=traffic_npc_lane_profile,
    )

    scenario = ScenarioConfig(
        scenario_schema_version=scenario.scenario_schema_version,
        scenario_id=scenario.scenario_id,
        duration_sec=scenario.duration_sec,
        dt_sec=scenario.dt_sec,
        ego=scenario.ego,
        npcs=[
            ActorState(
                actor_id=npc.actor_id,
                position_m=npc.position_m,
                speed_mps=npc.speed_mps * (traffic_npc_speed_scale if traffic_npc_speed_scale is not None else 1.0),
                length_m=npc.length_m,
                lane_index=npc.lane_index,
            )
            for npc in scenario.npcs
        ],
        npc_speed_jitter_mps=(
            float(traffic_npc_speed_jitter_mps)
            if traffic_npc_speed_jitter_mps is not None
            else scenario.npc_speed_jitter_mps
        ),
        enable_ego_collision_avoidance=(
            bool(enable_ego_collision_avoidance_override)
            if enable_ego_collision_avoidance_override is not None
            else bool(scenario.enable_ego_collision_avoidance)
        ),
        avoidance_ttc_threshold_sec=(
            float(avoidance_ttc_threshold_sec_override)
            if avoidance_ttc_threshold_sec_override is not None
            else float(scenario.avoidance_ttc_threshold_sec)
        ),
        ego_max_brake_mps2=(
            float(ego_max_brake_mps2_override)
            if ego_max_brake_mps2_override is not None
            else float(scenario.ego_max_brake_mps2)
        ),
        tire_friction_coeff=(
            float(tire_friction_coeff_override)
            if tire_friction_coeff_override is not None
            else float(scenario.tire_friction_coeff)
        ),
        surface_friction_scale=(
            float(surface_friction_scale_override)
            if surface_friction_scale_override is not None
            else float(scenario.surface_friction_scale)
        ),
        wall_timeout_sec=effective_wall_timeout,
    )
    if scenario.enable_ego_collision_avoidance and (
        scenario.avoidance_ttc_threshold_sec <= 0 or scenario.ego_max_brake_mps2 <= 0
    ):
        return _emit_error(
            "enable_ego_collision_avoidance requires avoidance_ttc_threshold_sec > 0 and ego_max_brake_mps2 > 0"
        )

    runner = CoreSimRunner(scenario=scenario, seed=seed)
    summary = runner.run()
    odd_tags = [tag.strip() for tag in args.odd_tags.split(",") if tag.strip()]
    lifecycle_state = "FAILED" if summary["status"] in {"failed", "timeout"} else "LOGGED"
    summary.update(
        {
            "scenario_path": str(scenario_path),
            "run_timestamp": summary["started_at"],
            "run_source": args.run_source,
            "sds_version": args.sds_version,
            "sim_version": args.sim_version,
            "fidelity_profile": args.fidelity_profile,
            "map_id": args.map_id,
            "map_version": args.map_version,
            "odd_tags": odd_tags,
            "lifecycle_state": lifecycle_state,
            "batch_id": args.batch_id if args.batch_id else None,
            "traffic_profile_id": str(args.traffic_profile_id).strip() or None,
            "traffic_actor_pattern_id": traffic_actor_pattern_id or None,
            "traffic_npc_count": int(len(scenario.npcs)),
            "traffic_npc_initial_gap_m": (
                float(traffic_npc_initial_gap_m)
                if traffic_npc_initial_gap_m is not None
                else _infer_first_npc_gap_m(scenario)
            ),
            "traffic_npc_gap_step_m": (
                float(traffic_npc_gap_step_m) if traffic_npc_gap_step_m is not None else None
            ),
            "traffic_npc_speed_offset_mps": (
                float(traffic_npc_speed_offset_mps) if traffic_npc_speed_offset_mps is not None else 0.0
            ),
            "traffic_npc_speed_scale": (
                float(traffic_npc_speed_scale) if traffic_npc_speed_scale is not None else 1.0
            ),
            "traffic_npc_speed_jitter_mps": float(scenario.npc_speed_jitter_mps),
            "enable_ego_collision_avoidance": bool(scenario.enable_ego_collision_avoidance),
            "avoidance_ttc_threshold_sec": float(scenario.avoidance_ttc_threshold_sec),
            "ego_max_brake_mps2": float(scenario.ego_max_brake_mps2),
            "tire_friction_coeff": float(scenario.tire_friction_coeff),
            "surface_friction_scale": float(scenario.surface_friction_scale),
            "ego_avoidance_brake_event_count": int(runner.ego_avoidance_brake_event_count),
            "ego_avoidance_applied_brake_mps2_max": round(
                float(runner.ego_avoidance_applied_brake_mps2_max),
                6,
            ),
            "traffic_npc_lane_profile": [int(npc.lane_index) for npc in scenario.npcs],
            "traffic_npc_gap_profile_m": [
                round(float(npc.position_m - scenario.ego.position_m), 6) for npc in scenario.npcs
            ],
            "traffic_npc_initial_speed_profile_mps": [round(float(npc.speed_mps), 6) for npc in scenario.npcs],
            "metric_values": [
                {
                    "metric_id": "collision_flag",
                    "value": 1 if summary["collision"] else 0,
                    "unit": "bool",
                },
                {
                    "metric_id": "timeout_flag",
                    "value": 1 if summary["timeout"] else 0,
                    "unit": "bool",
                },
                {
                    "metric_id": "min_ttc_sec",
                    "value": summary["min_ttc_sec"],
                    "unit": "sec",
                },
                {
                    "metric_id": "min_ttc_same_lane_sec",
                    "value": summary.get("min_ttc_same_lane_sec"),
                    "unit": "sec",
                },
                {
                    "metric_id": "min_ttc_adjacent_lane_sec",
                    "value": summary.get("min_ttc_adjacent_lane_sec"),
                    "unit": "sec",
                },
                {
                    "metric_id": "min_ttc_any_lane_sec",
                    "value": summary.get("min_ttc_any_lane_sec"),
                    "unit": "sec",
                },
            ],
        }
    )
    summary_path, trace_path, lane_risk_summary_path = write_artifacts(
        out_root=out_root,
        run_id=args.run_id,
        summary=summary,
        trace_rows=runner.trace_rows,
    )

    print(f"[ok] run_id={args.run_id}")
    print(f"[ok] summary={summary_path}")
    print(f"[ok] trace={trace_path}")
    print(f"[ok] lane_risk_summary={lane_risk_summary_path}")
    print(f"[ok] status={summary['status']} termination={summary['termination_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
