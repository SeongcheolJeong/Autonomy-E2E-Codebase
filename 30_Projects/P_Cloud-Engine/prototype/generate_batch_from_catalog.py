#!/usr/bin/env python3
"""Generate Cloud batch spec from scenario catalog manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary

ERROR_SOURCE = "generate_batch_from_catalog.py"
ERROR_PHASE = "resolve_inputs"
SUMO_ACTOR_PROFILE_LIBRARY_V0: dict[str, dict[str, Any]] = {
    "sumo_highway_calm_v0": {
        "traffic_profile_id": "sumo_highway_calm_v0",
        "traffic_actor_pattern_id": "sumo_platoon_sparse_v0",
        "traffic_npc_count": 2,
        "traffic_npc_initial_gap_m": 45.0,
        "traffic_npc_gap_step_m": 28.0,
        "traffic_npc_speed_offset_mps": -2.0,
        "traffic_npc_lane_profile": [0, 1],
        "traffic_npc_speed_scale": 0.9,
        "traffic_npc_speed_jitter_mps": 0.2,
        "traffic_profile_source": "sumo_stub_builtin_v0",
    },
    "sumo_highway_balanced_v0": {
        "traffic_profile_id": "sumo_highway_balanced_v0",
        "traffic_actor_pattern_id": "sumo_platoon_balanced_v0",
        "traffic_npc_count": 3,
        "traffic_npc_initial_gap_m": 34.0,
        "traffic_npc_gap_step_m": 22.0,
        "traffic_npc_speed_offset_mps": 0.0,
        "traffic_npc_lane_profile": [0, 1, -1],
        "traffic_npc_speed_scale": 1.0,
        "traffic_npc_speed_jitter_mps": 0.5,
        "traffic_profile_source": "sumo_stub_builtin_v0",
    },
    "sumo_highway_aggressive_v0": {
        "traffic_profile_id": "sumo_highway_aggressive_v0",
        "traffic_actor_pattern_id": "sumo_dense_aggressive_v0",
        "traffic_npc_count": 4,
        "traffic_npc_initial_gap_m": 24.0,
        "traffic_npc_gap_step_m": 16.0,
        "traffic_npc_speed_offset_mps": 3.0,
        "traffic_npc_lane_profile": [0, 1, 0, -1],
        "traffic_npc_speed_scale": 1.2,
        "traffic_npc_speed_jitter_mps": 1.2,
        "traffic_profile_source": "sumo_stub_builtin_v0",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate batch spec JSON from catalog manifest")
    parser.add_argument("--catalog-manifest", required=True, help="Path to catalog_manifest.json")
    parser.add_argument("--batch-id", required=True, help="Batch identifier")
    parser.add_argument("--sds-version", required=True, help="SDS version for defaults")
    parser.add_argument("--out", required=True, help="Output batch spec path")
    parser.add_argument("--run-id-prefix", default="RUN_RG", help="Run ID prefix")
    parser.add_argument("--run-id-start", default="", help="Run ID start index")
    parser.add_argument("--seed-base", default="", help="Seed base value")
    parser.add_argument("--seed-step", default="", help="Seed increment per scenario")
    parser.add_argument("--max-concurrency", default="")
    parser.add_argument("--timeout-sec-per-run", default="")
    parser.add_argument("--output-root", default="../batch_runs")
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--sim-runner-script", default="../../../P_Sim-Engine/prototype/core_sim_runner.py")
    parser.add_argument("--run-source", default="sim_closed_loop")
    parser.add_argument("--sim-version", default="sim_engine_v0_prototype")
    parser.add_argument("--fidelity-profile", default="dev-fast")
    parser.add_argument("--map-id", default="map_demo_highway")
    parser.add_argument("--map-version", default="v0")
    parser.add_argument("--odd-tags", default="highway,regression,v0")
    parser.add_argument(
        "--sumo-actor-profile-id",
        default="",
        help="Optional SUMO actor profile id (for example: sumo_highway_balanced_v0)",
    )
    parser.add_argument(
        "--sumo-npc-speed-scale",
        default="",
        help="Optional override for traffic NPC speed scale (>0)",
    )
    parser.add_argument(
        "--sumo-npc-speed-jitter-mps",
        default="",
        help="Optional override for traffic NPC speed jitter in m/s (>=0)",
    )
    parser.add_argument(
        "--sumo-actor-pattern-id",
        default="",
        help="Optional override for traffic actor pattern id",
    )
    parser.add_argument(
        "--sumo-npc-count",
        default="",
        help="Optional override for traffic NPC count (>0)",
    )
    parser.add_argument(
        "--sumo-npc-initial-gap-m",
        default="",
        help="Optional override for first traffic NPC gap in meters (>0)",
    )
    parser.add_argument(
        "--sumo-npc-gap-step-m",
        default="",
        help="Optional override for traffic NPC gap step in meters (>0)",
    )
    parser.add_argument(
        "--sumo-npc-speed-offset-mps",
        default="",
        help="Optional override for traffic NPC base speed offset in m/s",
    )
    parser.add_argument(
        "--sumo-npc-lane-profile",
        default="",
        help="Optional comma-separated lane profile override (for example: 0,1,-1)",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catalog manifest must be a JSON object")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) == 0:
        raise ValueError("catalog manifest must include non-empty scenarios list")
    return payload


def parse_int(raw: Any, *, default: int, field: str) -> int:
    value = str(raw).strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer, got: {raw}") from exc


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


def parse_optional_float(raw: Any, *, field: str) -> float | None:
    value = str(raw).strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number, got: {raw}") from exc


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


def main() -> int:
    try:
        args = parse_args()
        args.run_id_start = parse_int(args.run_id_start, default=1, field="run-id-start")
        args.seed_base = parse_int(args.seed_base, default=1001, field="seed-base")
        args.seed_step = parse_int(args.seed_step, default=1, field="seed-step")
        args.max_concurrency = parse_int(args.max_concurrency, default=4, field="max-concurrency")
        args.timeout_sec_per_run = parse_int(
            args.timeout_sec_per_run,
            default=30,
            field="timeout-sec-per-run",
        )
        sumo_actor_profile_id = str(args.sumo_actor_profile_id).strip()
        sumo_npc_speed_scale = parse_optional_positive_float(
            args.sumo_npc_speed_scale,
            field="sumo-npc-speed-scale",
        )
        sumo_npc_speed_jitter_mps = parse_optional_non_negative_float(
            args.sumo_npc_speed_jitter_mps,
            field="sumo-npc-speed-jitter-mps",
        )
        sumo_actor_pattern_id = str(args.sumo_actor_pattern_id).strip()
        sumo_npc_count = parse_optional_positive_int(
            args.sumo_npc_count,
            field="sumo-npc-count",
        )
        sumo_npc_initial_gap_m = parse_optional_positive_float(
            args.sumo_npc_initial_gap_m,
            field="sumo-npc-initial-gap-m",
        )
        sumo_npc_gap_step_m = parse_optional_positive_float(
            args.sumo_npc_gap_step_m,
            field="sumo-npc-gap-step-m",
        )
        sumo_npc_speed_offset_mps = parse_optional_float(
            args.sumo_npc_speed_offset_mps,
            field="sumo-npc-speed-offset-mps",
        )
        sumo_npc_lane_profile = parse_optional_int_csv(
            args.sumo_npc_lane_profile,
            field="sumo-npc-lane-profile",
        )
        traffic_profile: dict[str, Any] = {}
        if sumo_actor_profile_id:
            if sumo_actor_profile_id not in SUMO_ACTOR_PROFILE_LIBRARY_V0:
                allowed = ", ".join(sorted(SUMO_ACTOR_PROFILE_LIBRARY_V0.keys()))
                raise ValueError(
                    "sumo-actor-profile-id must be one of: "
                    f"{allowed}; got: {sumo_actor_profile_id}"
                )
            traffic_profile = dict(SUMO_ACTOR_PROFILE_LIBRARY_V0[sumo_actor_profile_id])
        if sumo_npc_speed_scale is not None:
            traffic_profile["traffic_npc_speed_scale"] = float(sumo_npc_speed_scale)
        if sumo_npc_speed_jitter_mps is not None:
            traffic_profile["traffic_npc_speed_jitter_mps"] = float(sumo_npc_speed_jitter_mps)
        if sumo_actor_pattern_id:
            traffic_profile["traffic_actor_pattern_id"] = sumo_actor_pattern_id
        if sumo_npc_count is not None:
            traffic_profile["traffic_npc_count"] = int(sumo_npc_count)
        if sumo_npc_initial_gap_m is not None:
            traffic_profile["traffic_npc_initial_gap_m"] = float(sumo_npc_initial_gap_m)
        if sumo_npc_gap_step_m is not None:
            traffic_profile["traffic_npc_gap_step_m"] = float(sumo_npc_gap_step_m)
        if sumo_npc_speed_offset_mps is not None:
            traffic_profile["traffic_npc_speed_offset_mps"] = float(sumo_npc_speed_offset_mps)
        if sumo_npc_lane_profile is not None:
            traffic_profile["traffic_npc_lane_profile"] = [int(value) for value in sumo_npc_lane_profile]
        if traffic_profile and "traffic_profile_id" not in traffic_profile:
            traffic_profile["traffic_profile_id"] = "sumo_profile_custom_v0"
            traffic_profile["traffic_profile_source"] = "sumo_stub_custom_v0"

        manifest_path = Path(args.catalog_manifest).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        manifest = load_manifest(manifest_path)
        catalog_dir = manifest_path.parent

        runs: list[dict[str, Any]] = []
        idx = args.run_id_start
        seed = args.seed_base
        for scenario_item in manifest["scenarios"]:
            if not isinstance(scenario_item, dict):
                continue
            scenario_file = str(scenario_item.get("scenario_file", "")).strip()
            if not scenario_file:
                continue

            scenario_abs = (catalog_dir / scenario_file).resolve()
            scenario_rel = os.path.relpath(scenario_abs, out_path.parent)
            run_id = f"{args.run_id_prefix}_{idx:04d}"

            runs.append(
                {
                    "run_id": run_id,
                    "scenario": scenario_rel,
                    "seed": seed,
                }
            )
            idx += 1
            seed += args.seed_step

        if not runs:
            raise ValueError("no valid scenarios found in catalog manifest")

        payload = {
            "batch_id": args.batch_id,
            "output_root": args.output_root,
            "sim_runner": {
                "python_bin": args.python_bin,
                "script_path": args.sim_runner_script,
            },
            "defaults": {
                "seed": args.seed_base,
                "run_source": args.run_source,
                "sds_version": args.sds_version,
                "sim_version": args.sim_version,
                "fidelity_profile": args.fidelity_profile,
                "map_id": args.map_id,
                "map_version": args.map_version,
                "odd_tags": args.odd_tags,
                "traffic_profile_id": str(traffic_profile.get("traffic_profile_id", "")).strip(),
                "traffic_actor_pattern_id": str(traffic_profile.get("traffic_actor_pattern_id", "")).strip(),
                "traffic_npc_count": traffic_profile.get("traffic_npc_count", 0),
                "traffic_npc_initial_gap_m": traffic_profile.get("traffic_npc_initial_gap_m", 0.0),
                "traffic_npc_gap_step_m": traffic_profile.get("traffic_npc_gap_step_m", 0.0),
                "traffic_npc_speed_offset_mps": traffic_profile.get("traffic_npc_speed_offset_mps", 0.0),
                "traffic_npc_lane_profile": traffic_profile.get("traffic_npc_lane_profile", []),
                "traffic_npc_speed_scale": traffic_profile.get("traffic_npc_speed_scale", 1.0),
                "traffic_npc_speed_jitter_mps": traffic_profile.get("traffic_npc_speed_jitter_mps", 0.0),
                "traffic_profile_source": str(traffic_profile.get("traffic_profile_source", "")).strip(),
            },
            "execution": {
                "max_concurrency": args.max_concurrency,
                "timeout_sec_per_run": args.timeout_sec_per_run,
            },
            "runs": runs,
        }

        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] out={out_path}")
        print(f"[ok] run_count={len(runs)}")
        return 0
    except Exception as exc:
        message = str(exc)
        print(f"[error] generate_batch_from_catalog.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
