#!/usr/bin/env python3
"""Resolve runtime-specific matrix profile ids from runtime profile definitions."""

from __future__ import annotations

import argparse
from pathlib import Path

from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_sync_utils import load_json_object

SUPPORTED_SIM_RUNTIMES = {"awsim", "carla"}
SUPPORTED_PROFILE_RUNTIME_TOKENS = {"", "none", "awsim", "carla"}
DEFAULT_PROFILE_ID_BY_RUNTIME = {
    "awsim": "runtime_awsim_smoke_v0",
    "carla": "runtime_carla_smoke_v0",
}
LEGACY_DEFAULT_PROFILE_ID = "runtime_smoke_v0"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve runtime-specific matrix profile ids"
    )
    parser.add_argument(
        "--profiles-file",
        default="ci_profiles/runtime_matrix_profiles.json",
        help="runtime matrix profiles JSON path",
    )
    parser.add_argument(
        "--profile-ids-csv",
        default="",
        help="comma-separated profile ids to filter",
    )
    parser.add_argument(
        "--sim-runtime",
        required=True,
        choices=sorted(SUPPORTED_SIM_RUNTIMES),
        help="runtime target used for filtering",
    )
    return parser.parse_args()


def parse_profile_ids_csv(value: str) -> list[str]:
    seen: set[str] = set()
    resolved: list[str] = []
    for raw in value.split(","):
        token = str(raw).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        resolved.append(token)
    return resolved


def load_profile_runtime_map(profiles_file: Path) -> dict[str, str]:
    payload = load_json_object(profiles_file, subject="runtime profiles")
    rows_raw = payload.get("profiles")
    if not isinstance(rows_raw, list):
        raise ValueError("profiles file must contain list field: profiles")
    runtime_by_profile: dict[str, str] = {}
    for idx, row in enumerate(rows_raw):
        if not isinstance(row, dict):
            raise ValueError(f"invalid profiles[{idx}]: expected object")
        profile_id = str(row.get("profile_id", "")).strip()
        if not profile_id:
            continue
        sim_runtime_token = str(row.get("sim_runtime", "")).strip().lower()
        if sim_runtime_token not in SUPPORTED_PROFILE_RUNTIME_TOKENS:
            raise ValueError(
                "invalid profiles[{idx}].sim_runtime: expected one of none|awsim|carla, got: {value}".format(
                    idx=idx,
                    value=row.get("sim_runtime", ""),
                )
            )
        runtime_by_profile[profile_id] = sim_runtime_token
    return runtime_by_profile


def resolve_profile_ids(
    *,
    profile_ids_csv: str,
    sim_runtime: str,
    runtime_by_profile: dict[str, str],
) -> str:
    profile_ids = parse_profile_ids_csv(profile_ids_csv)
    if not profile_ids:
        fallback = DEFAULT_PROFILE_ID_BY_RUNTIME.get(sim_runtime, LEGACY_DEFAULT_PROFILE_ID)
        return fallback

    selected: list[str] = []
    for profile_id in profile_ids:
        runtime_token = str(runtime_by_profile.get(profile_id, "")).strip().lower()
        if runtime_token and runtime_token not in {"none", sim_runtime}:
            continue
        selected.append(profile_id)

    if selected:
        return ",".join(selected)
    return ",".join(profile_ids)


def main() -> int:
    args = parse_args()
    sim_runtime = str(args.sim_runtime).strip().lower()
    if sim_runtime not in SUPPORTED_SIM_RUNTIMES:
        raise ValueError(
            "sim-runtime must be one of awsim|carla, got: {value}".format(
                value=args.sim_runtime
            )
        )

    profiles_file = Path(args.profiles_file)
    runtime_by_profile = load_profile_runtime_map(profiles_file)
    resolved = resolve_profile_ids(
        profile_ids_csv=str(args.profile_ids_csv),
        sim_runtime=sim_runtime,
        runtime_by_profile=runtime_by_profile,
    )
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="resolve_runtime_matrix_profile_ids.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=ERROR_PHASE,
        )
    )
