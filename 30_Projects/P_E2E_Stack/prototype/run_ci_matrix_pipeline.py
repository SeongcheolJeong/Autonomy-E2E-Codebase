#!/usr/bin/env python3
"""Run CI pipeline sequentially for selected matrix profiles."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from ci_commands import shell_join
from ci_input_parsing import (
    parse_bool,
    parse_float,
    parse_int,
    parse_non_negative_float,
    parse_non_negative_int,
)
from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_subprocess import (
    compact_failure_detail,
    emit_captured_output,
    format_subprocess_failure,
    run_capture,
    run_capture_stdout_or_raise,
)
from ci_sync_utils import utc_now_compact, utc_now_iso
from matrix_profile_selector_contract import (
    resolve_selected_profile_ids as resolve_selected_profile_ids_contract,
)


SIM_RUNTIME_ADAPTER_SCHEMA_VERSION_V0 = "sim_runtime_adapter_v0"
SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0 = "sim_runtime_probe_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
SIM_RUNTIME_SCENARIO_CONTRACT_SCHEMA_VERSION_V0 = "sim_runtime_scenario_contract_v0"
RUNTIME_SCENE_RESULT_SCHEMA_VERSION_V0 = "runtime_scene_result_v0"
SIM_RUNTIME_INTEROP_CONTRACT_SCHEMA_VERSION_V0 = "sim_runtime_interop_contract_v0"
SIM_RUNTIME_INTEROP_EXPORT_SCHEMA_VERSION_V0 = "sim_runtime_interop_export_v0"
SIM_RUNTIME_INTEROP_IMPORT_SCHEMA_VERSION_V0 = "sim_runtime_interop_import_v0"
PROFILE_RUNTIME_OVERRIDE_KEYS = (
    "sim_runtime",
    "sim_runtime_scene",
    "sim_runtime_sensor_rig",
    "sim_runtime_mode",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run run_ci_pipeline.py for matrix profiles")
    parser.add_argument(
        "--profiles-file",
        default=str(Path(__file__).resolve().with_name("ci_profiles") / "nightly_matrix_profiles.json"),
        help="Path to CI matrix profiles JSON",
    )
    parser.add_argument("--profile-id", action="append", default=[], help="Selected profile ID (repeatable)")
    parser.add_argument("--profile-ids-csv", default="", help="Selected profile IDs CSV")
    parser.add_argument(
        "--profile-loader",
        default=str(Path(__file__).resolve().with_name("load_ci_matrix.py")),
        help="Path to load_ci_matrix.py",
    )
    parser.add_argument(
        "--pipeline-runner",
        default=str(Path(__file__).resolve().with_name("run_ci_pipeline.py")),
        help="Path to run_ci_pipeline.py",
    )
    parser.add_argument("--python-bin", default="python3", help="Python executable")
    parser.add_argument(
        "--release-prefix",
        default="",
        help="Release ID prefix (default: REL_MATRIX_<utc timestamp>)",
    )
    parser.add_argument("--gate-profile", default="", help="Gate profile path (optional)")
    parser.add_argument("--requirement-map", default="", help="Requirement map path (optional)")
    parser.add_argument("--strict-gate-input", default="", help="Strict gate input (true/false, optional)")
    parser.add_argument(
        "--strict-gate-default",
        choices=["true", "false"],
        default="false",
        help="Strict gate default when strict-gate-input is empty",
    )
    parser.add_argument("--trend-window", default="", help="Trend window input (optional)")
    parser.add_argument("--trend-min-pass-rate", default="", help="Trend pass rate input (optional)")
    parser.add_argument("--trend-min-samples", default="", help="Trend sample count input (optional)")
    parser.add_argument(
        "--phase3-enable-hooks-input",
        default="",
        help="Phase-3 hooks enable input (true/false, optional)",
    )
    parser.add_argument(
        "--phase3-enable-hooks-default",
        choices=["true", "false"],
        default="false",
        help="Phase-3 hooks default when input is empty",
    )
    parser.add_argument(
        "--phase2-route-gate-require-status-pass-input",
        default="",
        help="Phase-2 route gate status-pass requirement input (true/false, optional)",
    )
    parser.add_argument(
        "--phase2-route-gate-require-status-pass-default",
        choices=["true", "false"],
        default="false",
        help="Phase-2 route gate status-pass requirement default when input is empty",
    )
    parser.add_argument(
        "--phase2-route-gate-min-lane-count",
        default="",
        help="Optional Phase-2 route gate minimum lane count threshold",
    )
    parser.add_argument(
        "--phase2-route-gate-min-total-length-m",
        default="",
        help="Optional Phase-2 route gate minimum route total length threshold in meters",
    )
    parser.add_argument(
        "--phase2-route-gate-require-routing-semantic-pass-input",
        default="",
        help="Phase-2 route gate routing semantic status-pass requirement input (true/false, optional)",
    )
    parser.add_argument(
        "--phase2-route-gate-require-routing-semantic-pass-default",
        choices=["true", "false"],
        default="false",
        help="Phase-2 route gate routing semantic status-pass requirement default when input is empty",
    )
    parser.add_argument(
        "--phase2-route-gate-max-routing-semantic-warning-count",
        default="",
        help="Optional Phase-2 route gate maximum routing semantic warning count threshold",
    )
    parser.add_argument(
        "--phase2-route-gate-max-unreachable-lane-count",
        default="",
        help="Optional Phase-2 route gate maximum unreachable lane count threshold",
    )
    parser.add_argument(
        "--phase2-route-gate-max-non-reciprocal-link-warning-count",
        default="",
        help="Optional Phase-2 route gate maximum non-reciprocal link warning count threshold",
    )
    parser.add_argument(
        "--phase2-route-gate-max-continuity-gap-warning-count",
        default="",
        help="Optional Phase-2 route gate maximum continuity gap warning count threshold",
    )
    parser.add_argument(
        "--phase3-control-gate-max-overlap-ratio",
        default="",
        help="Optional Phase-3 control gate max throttle/brake overlap ratio",
    )
    parser.add_argument(
        "--phase3-control-gate-max-steering-rate-degps",
        default="",
        help="Optional Phase-3 control gate max abs steering rate in deg/s",
    )
    parser.add_argument(
        "--phase3-control-gate-max-throttle-plus-brake",
        default="",
        help="Optional Phase-3 control gate max throttle+brake command sum",
    )
    parser.add_argument(
        "--phase3-control-gate-max-speed-tracking-error-abs-mps",
        default="",
        help="Optional Phase-3 control gate max abs speed tracking error in m/s",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-run-summary-count",
        default="",
        help="Optional Phase-3 dataset gate minimum run summary count",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-traffic-profile-count",
        default="",
        help="Optional Phase-3 dataset gate minimum traffic profile count",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-actor-pattern-count",
        default="",
        help="Optional Phase-3 dataset gate minimum traffic actor-pattern count",
    )
    parser.add_argument(
        "--phase3-dataset-gate-min-avg-npc-count",
        default="",
        help="Optional Phase-3 dataset gate minimum average traffic NPC count",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-min-ttc-same-lane-sec",
        default="",
        help="Optional Phase-3 lane-risk gate minimum same-lane TTC threshold (>0)",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
        default="",
        help="Optional Phase-3 lane-risk gate minimum adjacent-lane TTC threshold (>0)",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-min-ttc-any-lane-sec",
        default="",
        help="Optional Phase-3 lane-risk gate minimum any-lane TTC threshold (>0)",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
        default="",
        help="Optional Phase-3 lane-risk gate maximum ttc_under_3s same-lane total",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
        default="",
        help="Optional Phase-3 lane-risk gate maximum ttc_under_3s adjacent-lane total",
    )
    parser.add_argument(
        "--phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total",
        default="",
        help="Optional Phase-3 lane-risk gate maximum ttc_under_3s any-lane total",
    )
    parser.add_argument(
        "--phase3-enable-ego-collision-avoidance-input",
        default="",
        help="Phase-3 core-sim ego collision-avoidance enable input (true/false, optional)",
    )
    parser.add_argument(
        "--phase3-enable-ego-collision-avoidance-default",
        choices=["true", "false"],
        default="false",
        help="Phase-3 core-sim ego collision-avoidance default when input is empty",
    )
    parser.add_argument(
        "--phase3-avoidance-ttc-threshold-sec",
        default="",
        help="Optional Phase-3 core-sim TTC threshold override (>0)",
    )
    parser.add_argument(
        "--phase3-ego-max-brake-mps2",
        default="",
        help="Optional Phase-3 core-sim max brake override (>0)",
    )
    parser.add_argument(
        "--phase3-tire-friction-coeff",
        default="",
        help="Optional Phase-3 core-sim tire friction coefficient override (>0)",
    )
    parser.add_argument(
        "--phase3-surface-friction-scale",
        default="",
        help="Optional Phase-3 core-sim surface friction scale override (>0)",
    )
    parser.add_argument(
        "--phase3-core-sim-runner",
        default="",
        help="Optional core_sim_runner.py path for Phase-3 hook",
    )
    parser.add_argument(
        "--phase3-core-sim-scenario",
        default="",
        help="Optional scenario path for Phase-3 core-sim hook",
    )
    parser.add_argument(
        "--phase3-core-sim-run-id",
        default="",
        help="Optional run ID override for Phase-3 core-sim hook",
    )
    parser.add_argument(
        "--phase3-core-sim-out-root",
        default="",
        help="Optional output root for Phase-3 core-sim hook",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-require-success-input",
        default="",
        help="Phase-3 core-sim gate require-success input (true/false, optional)",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-require-success-default",
        choices=["true", "false"],
        default="false",
        help="Phase-3 core-sim gate require-success default when input is empty",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-min-ttc-same-lane-sec",
        default="",
        help="Optional Phase-3 core-sim gate minimum same-lane TTC threshold (>0)",
    )
    parser.add_argument(
        "--phase3-core-sim-gate-min-ttc-any-lane-sec",
        default="",
        help="Optional Phase-3 core-sim gate minimum any-lane TTC threshold (>0)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-require-all-cases-success-input",
        default="",
        help="Phase-3 core-sim matrix gate require-all-cases-success input (true/false, optional)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-require-all-cases-success-default",
        choices=["true", "false"],
        default="false",
        help="Phase-3 core-sim matrix gate require-all-cases-success default when input is empty",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
        default="",
        help="Optional Phase-3 core-sim matrix gate minimum same-lane TTC threshold (>0)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
        default="",
        help="Optional Phase-3 core-sim matrix gate minimum any-lane TTC threshold (>0)",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-max-failed-cases",
        default="",
        help="Optional Phase-3 core-sim matrix gate maximum failed case count",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-max-collision-cases",
        default="",
        help="Optional Phase-3 core-sim matrix gate maximum collision case count",
    )
    parser.add_argument(
        "--phase3-core-sim-matrix-gate-max-timeout-cases",
        default="",
        help="Optional Phase-3 core-sim matrix gate maximum timeout case count",
    )
    parser.add_argument(
        "--sim-runtime-adapter-runner",
        default="",
        help="Optional sim_runtime_adapter_stub.py path for matrix profiles",
    )
    parser.add_argument("--sim-runtime", default="", help="Optional runtime adapter target (none|awsim|carla)")
    parser.add_argument("--sim-runtime-scene", default="", help="Optional scene path for runtime adapter hook")
    parser.add_argument("--sim-runtime-sensor-rig", default="", help="Optional sensor rig path for runtime adapter hook")
    parser.add_argument("--sim-runtime-mode", default="", help="Optional runtime adapter mode (headless|interactive)")
    parser.add_argument("--sim-runtime-out", default="", help="Optional runtime adapter report output path")
    parser.add_argument(
        "--sim-runtime-probe-enable-input",
        default="",
        help="Runtime probe hook enable input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-probe-enable-default",
        choices=["true", "false"],
        default="false",
        help="Runtime probe hook enable default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-probe-execute-input",
        default="",
        help="Runtime probe command execution input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-probe-execute-default",
        choices=["true", "false"],
        default="false",
        help="Runtime probe command execution default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-probe-require-availability-input",
        default="",
        help="Runtime probe availability requirement input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-probe-require-availability-default",
        choices=["true", "false"],
        default="false",
        help="Runtime probe availability requirement default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-enable-input",
        default="",
        help="Runtime scenario-contract hook enable input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-enable-default",
        choices=["true", "false"],
        default="false",
        help="Runtime scenario-contract hook enable default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-require-runtime-ready-input",
        default="",
        help="Runtime scenario-contract runtime-ready requirement input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-require-runtime-ready-default",
        choices=["true", "false"],
        default="false",
        help="Runtime scenario-contract runtime-ready requirement default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-enable-input",
        default="",
        help="Runtime scene-result hook enable input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-enable-default",
        choices=["true", "false"],
        default="false",
        help="Runtime scene-result hook enable default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-require-runtime-ready-input",
        default="",
        help="Runtime scene-result runtime-ready requirement input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-require-runtime-ready-default",
        choices=["true", "false"],
        default="false",
        help="Runtime scene-result runtime-ready requirement default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-enable-input",
        default="",
        help="Runtime interop-contract hook enable input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-enable-default",
        choices=["true", "false"],
        default="false",
        help="Runtime interop-contract hook enable default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-require-runtime-ready-input",
        default="",
        help="Runtime interop-contract runtime-ready requirement input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-require-runtime-ready-default",
        choices=["true", "false"],
        default="false",
        help="Runtime interop-contract runtime-ready requirement default when input is empty",
    )
    parser.add_argument(
        "--sim-runtime-probe-runner",
        default="",
        help="Optional sim_runtime_probe_runner.py path for runtime availability probe",
    )
    parser.add_argument(
        "--sim-runtime-probe-runtime-bin",
        default="",
        help="Optional runtime executable override for runtime probe hook",
    )
    parser.add_argument(
        "--sim-runtime-probe-flag",
        default="",
        help="Optional legacy probe flag forwarded to runtime probe hook",
    )
    parser.add_argument(
        "--sim-runtime-probe-args-shlex",
        default="",
        help=(
            "Optional shell-like probe args forwarded to runtime probe hook as repeated --probe-arg "
            "(takes precedence over --sim-runtime-probe-flag)"
        ),
    )
    parser.add_argument(
        "--sim-runtime-probe-out",
        default="",
        help="Optional runtime probe report output path",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-runner",
        default="",
        help="Optional sim_runtime_scenario_contract_runner.py path for runtime scenario contract",
    )
    parser.add_argument(
        "--sim-runtime-scenario-contract-out",
        default="",
        help="Optional runtime scenario contract report output path",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-runner",
        default="",
        help="Optional sim_runtime_scene_result_runner.py path for runtime scene result",
    )
    parser.add_argument(
        "--sim-runtime-scene-result-out",
        default="",
        help="Optional runtime scene result report output path",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-runner",
        default="",
        help="Optional sim_runtime_interop_contract_runner.py path for runtime interop contract",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-runner",
        default="",
        help="Optional sim_runtime_interop_export_runner.py path for runtime interop export",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-road-length-scale",
        default="",
        help="Optional road length scale for runtime interop export",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-xosc-out",
        default="",
        help="Optional OpenSCENARIO output path for runtime interop export",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-xodr-out",
        default="",
        help="Optional OpenDRIVE output path for runtime interop export",
    )
    parser.add_argument(
        "--sim-runtime-interop-export-out",
        default="",
        help="Optional runtime interop export report output path",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-runner",
        default="",
        help="Optional sim_runtime_interop_import_runner.py path for runtime interop import verification",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-out",
        default="",
        help="Optional runtime interop import report output path",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-manifest-consistency-mode",
        default="",
        help="Optional runtime interop import manifest consistency mode (require|allow)",
    )
    parser.add_argument(
        "--sim-runtime-interop-import-export-consistency-mode",
        default="",
        help="Optional runtime interop import/export consistency mode (require|allow)",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-xosc",
        default="",
        help="Optional OpenSCENARIO input path for runtime interop contract",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-xodr",
        default="",
        help="Optional OpenDRIVE input path for runtime interop contract",
    )
    parser.add_argument(
        "--sim-runtime-interop-contract-out",
        default="",
        help="Optional runtime interop contract report output path",
    )
    parser.add_argument(
        "--sim-runtime-assert-artifacts-input",
        default="",
        help="Runtime artifact assertion enable input (true/false, optional)",
    )
    parser.add_argument(
        "--sim-runtime-assert-artifacts-default",
        choices=["true", "false"],
        default="false",
        help="Runtime artifact assertion default when input is empty",
    )
    parser.add_argument("--sds-versions-csv", default="", help="Optional SDS versions CSV override")
    parser.add_argument("--default-sds-versions", default="", help="Fallback SDS versions CSV")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue remaining profiles on failure")
    parser.add_argument(
        "--max-failures",
        default="",
        help="Stop matrix execution after this many failures (0 disables limit)",
    )
    parser.add_argument("--summary-out", default="", help="Optional JSON summary output path")
    parser.add_argument(
        "--runtime-evidence-out",
        default="",
        help="Optional JSON output path for runtime artifact evidence rows",
    )
    parser.add_argument("--dry-run", action="store_true", help="Forward dry-run to run_ci_pipeline.py")
    return parser.parse_args()


def safe_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value).strip())
    token = token.strip("._-")
    return token or "profile"


def resolve_selected_profile_ids(args: argparse.Namespace) -> list[str]:
    return resolve_selected_profile_ids_contract(args.profile_id, str(args.profile_ids_csv))


def load_matrix_profiles(
    *,
    python_bin: str,
    profile_loader: str,
    profiles_file: str,
    selected_profile_ids: list[str],
) -> list[dict[str, str]]:
    cmd = [
        python_bin,
        str(Path(profile_loader).resolve()),
        "--profiles-file",
        str(Path(profiles_file).resolve()),
    ]
    for profile_id in selected_profile_ids:
        cmd.extend(["--profile-id", profile_id])

    stdout_text = run_capture_stdout_or_raise(
        cmd,
        context="load matrix profiles",
        emit_output_on_success=False,
    )

    try:
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise ValueError("load_ci_matrix.py did not return valid JSON matrix payload") from exc
    include = payload.get("include")
    if not isinstance(include, list) or not include:
        raise ValueError("matrix payload must include non-empty include list")

    profiles: list[dict[str, str]] = []
    for idx, item in enumerate(include):
        if not isinstance(item, dict):
            raise ValueError(f"invalid matrix include[{idx}]: expected object")
        profile_id = str(item.get("profile_id", "")).strip()
        default_batch_spec = str(item.get("default_batch_spec", "")).strip()
        default_sds_versions = str(item.get("default_sds_versions", "")).strip()
        if not profile_id or not default_batch_spec or not default_sds_versions:
            raise ValueError(
                f"invalid matrix include[{idx}]: profile_id/default_batch_spec/default_sds_versions are required"
            )
        profile: dict[str, str] = {
            "profile_id": profile_id,
            "default_batch_spec": default_batch_spec,
            "default_sds_versions": default_sds_versions,
        }
        for key in PROFILE_RUNTIME_OVERRIDE_KEYS:
            if key not in item:
                continue
            value_raw = item.get(key)
            if not isinstance(value_raw, str):
                raise ValueError(f"invalid matrix include[{idx}].{key}: expected string")
            value = value_raw.strip()
            if not value:
                raise ValueError(f"invalid matrix include[{idx}].{key}: empty string")
            profile[key] = value

        sim_runtime_override = str(profile.get("sim_runtime", "")).strip().lower()
        if sim_runtime_override and sim_runtime_override not in {"none", "awsim", "carla"}:
            raise ValueError(
                "invalid matrix include[{idx}].sim_runtime: expected one of none|awsim|carla, got: {value}".format(
                    idx=idx,
                    value=profile.get("sim_runtime", ""),
                )
            )
        if sim_runtime_override:
            profile["sim_runtime"] = sim_runtime_override

        sim_runtime_mode_override = str(profile.get("sim_runtime_mode", "")).strip().lower()
        if sim_runtime_mode_override and sim_runtime_mode_override not in {"headless", "interactive"}:
            raise ValueError(
                "invalid matrix include[{idx}].sim_runtime_mode: expected one of headless|interactive, got: {value}".format(
                    idx=idx,
                    value=profile.get("sim_runtime_mode", ""),
                )
            )
        if sim_runtime_mode_override:
            profile["sim_runtime_mode"] = sim_runtime_mode_override

        profiles.append(profile)
    return profiles


def build_profile_cmd(
    *,
    args: argparse.Namespace,
    profile: dict[str, str],
    release_id: str,
) -> list[str]:
    profile_id = profile["profile_id"]
    cmd = [
        str(args.python_bin),
        str(Path(args.pipeline_runner).resolve()),
        "--python-bin",
        str(args.python_bin),
        "--profile-file",
        str(Path(args.profiles_file).resolve()),
        "--profile-id",
        profile_id,
        "--profile-loader",
        str(Path(args.profile_loader).resolve()),
        "--release-id",
        release_id,
        "--strict-gate-default",
        str(args.strict_gate_default),
        "--phase3-enable-hooks-default",
        str(args.phase3_enable_hooks_default),
        "--phase3-enable-ego-collision-avoidance-default",
        str(args.phase3_enable_ego_collision_avoidance_default),
        "--phase3-core-sim-gate-require-success-default",
        str(args.phase3_core_sim_gate_require_success_default),
        "--phase3-core-sim-matrix-gate-require-all-cases-success-default",
        str(args.phase3_core_sim_matrix_gate_require_all_cases_success_default),
        "--phase2-route-gate-require-status-pass-default",
        str(args.phase2_route_gate_require_status_pass_default),
        "--phase2-route-gate-require-routing-semantic-pass-default",
        str(args.phase2_route_gate_require_routing_semantic_pass_default),
        "--sim-runtime-probe-enable-default",
        str(args.sim_runtime_probe_enable_default),
        "--sim-runtime-probe-execute-default",
        str(args.sim_runtime_probe_execute_default),
        "--sim-runtime-probe-require-availability-default",
        str(args.sim_runtime_probe_require_availability_default),
        "--sim-runtime-scenario-contract-enable-default",
        str(args.sim_runtime_scenario_contract_enable_default),
        "--sim-runtime-scenario-contract-require-runtime-ready-default",
        str(args.sim_runtime_scenario_contract_require_runtime_ready_default),
        "--sim-runtime-scene-result-enable-default",
        str(args.sim_runtime_scene_result_enable_default),
        "--sim-runtime-scene-result-require-runtime-ready-default",
        str(args.sim_runtime_scene_result_require_runtime_ready_default),
        "--sim-runtime-interop-contract-enable-default",
        str(args.sim_runtime_interop_contract_enable_default),
        "--sim-runtime-interop-contract-require-runtime-ready-default",
        str(args.sim_runtime_interop_contract_require_runtime_ready_default),
    ]

    gate_profile = str(args.gate_profile).strip()
    if gate_profile:
        cmd.extend(["--gate-profile", gate_profile])

    requirement_map = str(args.requirement_map).strip()
    if requirement_map:
        cmd.extend(["--requirement-map", requirement_map])

    strict_gate_input = str(args.strict_gate_input).strip()
    if strict_gate_input:
        cmd.extend(["--strict-gate-input", strict_gate_input])

    trend_window = str(args.trend_window).strip()
    if trend_window:
        cmd.extend(["--trend-window", trend_window])

    trend_min_pass_rate = str(args.trend_min_pass_rate).strip()
    if trend_min_pass_rate:
        cmd.extend(["--trend-min-pass-rate", trend_min_pass_rate])

    trend_min_samples = str(args.trend_min_samples).strip()
    if trend_min_samples:
        cmd.extend(["--trend-min-samples", trend_min_samples])

    phase3_enable_hooks_input = str(args.phase3_enable_hooks_input).strip()
    sim_runtime = str(profile.get("sim_runtime", args.sim_runtime)).strip().lower()
    if phase3_enable_hooks_input:
        cmd.extend(["--phase3-enable-hooks-input", phase3_enable_hooks_input])
    elif sim_runtime and sim_runtime != "none":
        # Runtime integration should implicitly opt-in to Phase-3 hooks for matrix smoke runs.
        cmd.extend(["--phase3-enable-hooks-input", "true"])

    phase2_route_gate_require_status_pass_input = str(
        args.phase2_route_gate_require_status_pass_input
    ).strip()
    if phase2_route_gate_require_status_pass_input:
        cmd.extend(
            [
                "--phase2-route-gate-require-status-pass-input",
                phase2_route_gate_require_status_pass_input,
            ]
        )
    phase2_route_gate_min_lane_count = str(args.phase2_route_gate_min_lane_count).strip()
    if phase2_route_gate_min_lane_count:
        cmd.extend(["--phase2-route-gate-min-lane-count", phase2_route_gate_min_lane_count])
    phase2_route_gate_min_total_length_m = str(args.phase2_route_gate_min_total_length_m).strip()
    if phase2_route_gate_min_total_length_m:
        cmd.extend(["--phase2-route-gate-min-total-length-m", phase2_route_gate_min_total_length_m])
    phase2_route_gate_require_routing_semantic_pass_input = str(
        args.phase2_route_gate_require_routing_semantic_pass_input
    ).strip()
    if phase2_route_gate_require_routing_semantic_pass_input:
        cmd.extend(
            [
                "--phase2-route-gate-require-routing-semantic-pass-input",
                phase2_route_gate_require_routing_semantic_pass_input,
            ]
        )
    phase2_route_gate_max_routing_semantic_warning_count = str(
        args.phase2_route_gate_max_routing_semantic_warning_count
    ).strip()
    if phase2_route_gate_max_routing_semantic_warning_count:
        cmd.extend(
            [
                "--phase2-route-gate-max-routing-semantic-warning-count",
                phase2_route_gate_max_routing_semantic_warning_count,
            ]
        )
    phase2_route_gate_max_unreachable_lane_count = str(
        args.phase2_route_gate_max_unreachable_lane_count
    ).strip()
    if phase2_route_gate_max_unreachable_lane_count:
        cmd.extend(
            [
                "--phase2-route-gate-max-unreachable-lane-count",
                phase2_route_gate_max_unreachable_lane_count,
            ]
        )
    phase2_route_gate_max_non_reciprocal_link_warning_count = str(
        args.phase2_route_gate_max_non_reciprocal_link_warning_count
    ).strip()
    if phase2_route_gate_max_non_reciprocal_link_warning_count:
        cmd.extend(
            [
                "--phase2-route-gate-max-non-reciprocal-link-warning-count",
                phase2_route_gate_max_non_reciprocal_link_warning_count,
            ]
        )
    phase2_route_gate_max_continuity_gap_warning_count = str(
        args.phase2_route_gate_max_continuity_gap_warning_count
    ).strip()
    if phase2_route_gate_max_continuity_gap_warning_count:
        cmd.extend(
            [
                "--phase2-route-gate-max-continuity-gap-warning-count",
                phase2_route_gate_max_continuity_gap_warning_count,
            ]
        )
    phase3_control_gate_max_overlap_ratio = str(args.phase3_control_gate_max_overlap_ratio).strip()
    if phase3_control_gate_max_overlap_ratio:
        cmd.extend(["--phase3-control-gate-max-overlap-ratio", phase3_control_gate_max_overlap_ratio])
    phase3_control_gate_max_steering_rate_degps = str(args.phase3_control_gate_max_steering_rate_degps).strip()
    if phase3_control_gate_max_steering_rate_degps:
        cmd.extend(
            [
                "--phase3-control-gate-max-steering-rate-degps",
                phase3_control_gate_max_steering_rate_degps,
            ]
        )
    phase3_control_gate_max_throttle_plus_brake = str(args.phase3_control_gate_max_throttle_plus_brake).strip()
    if phase3_control_gate_max_throttle_plus_brake:
        cmd.extend(
            [
                "--phase3-control-gate-max-throttle-plus-brake",
                phase3_control_gate_max_throttle_plus_brake,
            ]
        )
    phase3_control_gate_max_speed_tracking_error_abs_mps = str(
        args.phase3_control_gate_max_speed_tracking_error_abs_mps
    ).strip()
    if phase3_control_gate_max_speed_tracking_error_abs_mps:
        cmd.extend(
            [
                "--phase3-control-gate-max-speed-tracking-error-abs-mps",
                phase3_control_gate_max_speed_tracking_error_abs_mps,
            ]
        )
    phase3_dataset_gate_min_run_summary_count = str(args.phase3_dataset_gate_min_run_summary_count).strip()
    if phase3_dataset_gate_min_run_summary_count:
        cmd.extend(
            [
                "--phase3-dataset-gate-min-run-summary-count",
                phase3_dataset_gate_min_run_summary_count,
            ]
        )
    phase3_dataset_gate_min_traffic_profile_count = str(
        args.phase3_dataset_gate_min_traffic_profile_count
    ).strip()
    if phase3_dataset_gate_min_traffic_profile_count:
        cmd.extend(
            [
                "--phase3-dataset-gate-min-traffic-profile-count",
                phase3_dataset_gate_min_traffic_profile_count,
            ]
        )
    phase3_dataset_gate_min_actor_pattern_count = str(
        args.phase3_dataset_gate_min_actor_pattern_count
    ).strip()
    if phase3_dataset_gate_min_actor_pattern_count:
        cmd.extend(
            [
                "--phase3-dataset-gate-min-actor-pattern-count",
                phase3_dataset_gate_min_actor_pattern_count,
            ]
        )
    phase3_dataset_gate_min_avg_npc_count = str(args.phase3_dataset_gate_min_avg_npc_count).strip()
    if phase3_dataset_gate_min_avg_npc_count:
        cmd.extend(
            [
                "--phase3-dataset-gate-min-avg-npc-count",
                phase3_dataset_gate_min_avg_npc_count,
            ]
        )
    phase3_lane_risk_gate_min_ttc_same_lane_sec = str(args.phase3_lane_risk_gate_min_ttc_same_lane_sec).strip()
    if phase3_lane_risk_gate_min_ttc_same_lane_sec:
        cmd.extend(
            [
                "--phase3-lane-risk-gate-min-ttc-same-lane-sec",
                phase3_lane_risk_gate_min_ttc_same_lane_sec,
            ]
        )
    phase3_lane_risk_gate_min_ttc_adjacent_lane_sec = str(
        args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec
    ).strip()
    if phase3_lane_risk_gate_min_ttc_adjacent_lane_sec:
        cmd.extend(
            [
                "--phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
                phase3_lane_risk_gate_min_ttc_adjacent_lane_sec,
            ]
        )
    phase3_lane_risk_gate_min_ttc_any_lane_sec = str(args.phase3_lane_risk_gate_min_ttc_any_lane_sec).strip()
    if phase3_lane_risk_gate_min_ttc_any_lane_sec:
        cmd.extend(
            [
                "--phase3-lane-risk-gate-min-ttc-any-lane-sec",
                phase3_lane_risk_gate_min_ttc_any_lane_sec,
            ]
        )
    phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total = str(
        args.phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total
    ).strip()
    if phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total:
        cmd.extend(
            [
                "--phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
                phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total,
            ]
        )
    phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total = str(
        args.phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total
    ).strip()
    if phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total:
        cmd.extend(
            [
                "--phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
                phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total,
            ]
        )
    phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total = str(
        args.phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total
    ).strip()
    if phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total:
        cmd.extend(
            [
                "--phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total",
                phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total,
            ]
        )
    phase3_enable_ego_collision_avoidance_input = str(
        args.phase3_enable_ego_collision_avoidance_input
    ).strip()
    if phase3_enable_ego_collision_avoidance_input:
        cmd.extend(
            [
                "--phase3-enable-ego-collision-avoidance-input",
                phase3_enable_ego_collision_avoidance_input,
            ]
        )
    phase3_avoidance_ttc_threshold_sec = str(args.phase3_avoidance_ttc_threshold_sec).strip()
    if phase3_avoidance_ttc_threshold_sec:
        cmd.extend(
            [
                "--phase3-avoidance-ttc-threshold-sec",
                phase3_avoidance_ttc_threshold_sec,
            ]
        )
    phase3_ego_max_brake_mps2 = str(args.phase3_ego_max_brake_mps2).strip()
    if phase3_ego_max_brake_mps2:
        cmd.extend(
            [
                "--phase3-ego-max-brake-mps2",
                phase3_ego_max_brake_mps2,
            ]
        )
    phase3_tire_friction_coeff = str(args.phase3_tire_friction_coeff).strip()
    if phase3_tire_friction_coeff:
        cmd.extend(
            [
                "--phase3-tire-friction-coeff",
                phase3_tire_friction_coeff,
            ]
        )
    phase3_surface_friction_scale = str(args.phase3_surface_friction_scale).strip()
    if phase3_surface_friction_scale:
        cmd.extend(
            [
                "--phase3-surface-friction-scale",
                phase3_surface_friction_scale,
            ]
        )
    phase3_core_sim_runner = str(args.phase3_core_sim_runner).strip()
    if phase3_core_sim_runner:
        cmd.extend(["--phase3-core-sim-runner", phase3_core_sim_runner])
    phase3_core_sim_scenario = str(args.phase3_core_sim_scenario).strip()
    if phase3_core_sim_scenario:
        cmd.extend(["--phase3-core-sim-scenario", phase3_core_sim_scenario])
    phase3_core_sim_run_id = str(args.phase3_core_sim_run_id).strip()
    if phase3_core_sim_run_id:
        cmd.extend(["--phase3-core-sim-run-id", phase3_core_sim_run_id])
    phase3_core_sim_out_root = str(args.phase3_core_sim_out_root).strip()
    if phase3_core_sim_out_root:
        cmd.extend(["--phase3-core-sim-out-root", phase3_core_sim_out_root])
    phase3_core_sim_gate_require_success_input = str(args.phase3_core_sim_gate_require_success_input).strip()
    if phase3_core_sim_gate_require_success_input:
        cmd.extend(
            [
                "--phase3-core-sim-gate-require-success-input",
                phase3_core_sim_gate_require_success_input,
            ]
        )
    phase3_core_sim_gate_min_ttc_same_lane_sec = str(args.phase3_core_sim_gate_min_ttc_same_lane_sec).strip()
    if phase3_core_sim_gate_min_ttc_same_lane_sec:
        cmd.extend(
            [
                "--phase3-core-sim-gate-min-ttc-same-lane-sec",
                phase3_core_sim_gate_min_ttc_same_lane_sec,
            ]
        )
    phase3_core_sim_gate_min_ttc_any_lane_sec = str(args.phase3_core_sim_gate_min_ttc_any_lane_sec).strip()
    if phase3_core_sim_gate_min_ttc_any_lane_sec:
        cmd.extend(
            [
                "--phase3-core-sim-gate-min-ttc-any-lane-sec",
                phase3_core_sim_gate_min_ttc_any_lane_sec,
            ]
        )
    phase3_core_sim_matrix_gate_require_all_cases_success_input = str(
        args.phase3_core_sim_matrix_gate_require_all_cases_success_input
    ).strip()
    if phase3_core_sim_matrix_gate_require_all_cases_success_input:
        cmd.extend(
            [
                "--phase3-core-sim-matrix-gate-require-all-cases-success-input",
                phase3_core_sim_matrix_gate_require_all_cases_success_input,
            ]
        )
    phase3_core_sim_matrix_gate_min_ttc_same_lane_sec = str(
        args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec
    ).strip()
    if phase3_core_sim_matrix_gate_min_ttc_same_lane_sec:
        cmd.extend(
            [
                "--phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
                phase3_core_sim_matrix_gate_min_ttc_same_lane_sec,
            ]
        )
    phase3_core_sim_matrix_gate_min_ttc_any_lane_sec = str(
        args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec
    ).strip()
    if phase3_core_sim_matrix_gate_min_ttc_any_lane_sec:
        cmd.extend(
            [
                "--phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
                phase3_core_sim_matrix_gate_min_ttc_any_lane_sec,
            ]
        )
    phase3_core_sim_matrix_gate_max_failed_cases = str(args.phase3_core_sim_matrix_gate_max_failed_cases).strip()
    if phase3_core_sim_matrix_gate_max_failed_cases:
        cmd.extend(
            [
                "--phase3-core-sim-matrix-gate-max-failed-cases",
                phase3_core_sim_matrix_gate_max_failed_cases,
            ]
        )
    phase3_core_sim_matrix_gate_max_collision_cases = str(
        args.phase3_core_sim_matrix_gate_max_collision_cases
    ).strip()
    if phase3_core_sim_matrix_gate_max_collision_cases:
        cmd.extend(
            [
                "--phase3-core-sim-matrix-gate-max-collision-cases",
                phase3_core_sim_matrix_gate_max_collision_cases,
            ]
        )
    phase3_core_sim_matrix_gate_max_timeout_cases = str(
        args.phase3_core_sim_matrix_gate_max_timeout_cases
    ).strip()
    if phase3_core_sim_matrix_gate_max_timeout_cases:
        cmd.extend(
            [
                "--phase3-core-sim-matrix-gate-max-timeout-cases",
                phase3_core_sim_matrix_gate_max_timeout_cases,
            ]
        )

    sim_runtime_adapter_runner = str(args.sim_runtime_adapter_runner).strip()
    if sim_runtime_adapter_runner:
        cmd.extend(["--sim-runtime-adapter-runner", sim_runtime_adapter_runner])

    if sim_runtime:
        cmd.extend(["--sim-runtime", sim_runtime])

    sim_runtime_scene = str(profile.get("sim_runtime_scene", args.sim_runtime_scene)).strip()
    if sim_runtime_scene:
        cmd.extend(["--sim-runtime-scene", sim_runtime_scene])

    sim_runtime_sensor_rig = str(profile.get("sim_runtime_sensor_rig", args.sim_runtime_sensor_rig)).strip()
    if sim_runtime_sensor_rig:
        cmd.extend(["--sim-runtime-sensor-rig", sim_runtime_sensor_rig])

    sim_runtime_mode = str(profile.get("sim_runtime_mode", args.sim_runtime_mode)).strip().lower()
    if sim_runtime_mode:
        cmd.extend(["--sim-runtime-mode", sim_runtime_mode])

    sim_runtime_out = str(args.sim_runtime_out).strip()
    if sim_runtime_out:
        cmd.extend(["--sim-runtime-out", sim_runtime_out])
    sim_runtime_probe_enable_input = str(args.sim_runtime_probe_enable_input).strip()
    if sim_runtime_probe_enable_input:
        cmd.extend(["--sim-runtime-probe-enable-input", sim_runtime_probe_enable_input])

    sim_runtime_probe_execute_input = str(args.sim_runtime_probe_execute_input).strip()
    if sim_runtime_probe_execute_input:
        cmd.extend(["--sim-runtime-probe-execute-input", sim_runtime_probe_execute_input])

    sim_runtime_probe_require_availability_input = str(
        args.sim_runtime_probe_require_availability_input
    ).strip()
    if sim_runtime_probe_require_availability_input:
        cmd.extend(
            [
                "--sim-runtime-probe-require-availability-input",
                sim_runtime_probe_require_availability_input,
            ]
        )

    sim_runtime_probe_runner = str(args.sim_runtime_probe_runner).strip()
    if sim_runtime_probe_runner:
        cmd.extend(["--sim-runtime-probe-runner", sim_runtime_probe_runner])

    sim_runtime_probe_runtime_bin = str(args.sim_runtime_probe_runtime_bin).strip()
    if sim_runtime_probe_runtime_bin:
        cmd.extend(["--sim-runtime-probe-runtime-bin", sim_runtime_probe_runtime_bin])

    sim_runtime_probe_flag = str(args.sim_runtime_probe_flag).strip()
    if sim_runtime_probe_flag:
        cmd.append(f"--sim-runtime-probe-flag={sim_runtime_probe_flag}")

    sim_runtime_probe_args_shlex = str(args.sim_runtime_probe_args_shlex).strip()
    if sim_runtime_probe_args_shlex:
        cmd.append(f"--sim-runtime-probe-args-shlex={sim_runtime_probe_args_shlex}")

    sim_runtime_probe_out = str(args.sim_runtime_probe_out).strip()
    if sim_runtime_probe_out:
        cmd.extend(["--sim-runtime-probe-out", sim_runtime_probe_out])
    sim_runtime_scenario_contract_enable_input = str(args.sim_runtime_scenario_contract_enable_input).strip()
    if sim_runtime_scenario_contract_enable_input:
        cmd.extend(
            [
                "--sim-runtime-scenario-contract-enable-input",
                sim_runtime_scenario_contract_enable_input,
            ]
        )
    sim_runtime_scenario_contract_require_runtime_ready_input = str(
        args.sim_runtime_scenario_contract_require_runtime_ready_input
    ).strip()
    if sim_runtime_scenario_contract_require_runtime_ready_input:
        cmd.extend(
            [
                "--sim-runtime-scenario-contract-require-runtime-ready-input",
                sim_runtime_scenario_contract_require_runtime_ready_input,
            ]
        )
    sim_runtime_scenario_contract_runner = str(args.sim_runtime_scenario_contract_runner).strip()
    if sim_runtime_scenario_contract_runner:
        cmd.extend(["--sim-runtime-scenario-contract-runner", sim_runtime_scenario_contract_runner])
    sim_runtime_scenario_contract_out = str(args.sim_runtime_scenario_contract_out).strip()
    if sim_runtime_scenario_contract_out:
        cmd.extend(["--sim-runtime-scenario-contract-out", sim_runtime_scenario_contract_out])
    sim_runtime_scene_result_enable_input = str(args.sim_runtime_scene_result_enable_input).strip()
    if sim_runtime_scene_result_enable_input:
        cmd.extend(
            [
                "--sim-runtime-scene-result-enable-input",
                sim_runtime_scene_result_enable_input,
            ]
        )
    sim_runtime_scene_result_require_runtime_ready_input = str(
        args.sim_runtime_scene_result_require_runtime_ready_input
    ).strip()
    if sim_runtime_scene_result_require_runtime_ready_input:
        cmd.extend(
            [
                "--sim-runtime-scene-result-require-runtime-ready-input",
                sim_runtime_scene_result_require_runtime_ready_input,
            ]
        )
    sim_runtime_scene_result_runner = str(args.sim_runtime_scene_result_runner).strip()
    if sim_runtime_scene_result_runner:
        cmd.extend(["--sim-runtime-scene-result-runner", sim_runtime_scene_result_runner])
    sim_runtime_scene_result_out = str(args.sim_runtime_scene_result_out).strip()
    if sim_runtime_scene_result_out:
        cmd.extend(["--sim-runtime-scene-result-out", sim_runtime_scene_result_out])
    sim_runtime_interop_contract_enable_input = str(args.sim_runtime_interop_contract_enable_input).strip()
    if sim_runtime_interop_contract_enable_input:
        cmd.extend(
            [
                "--sim-runtime-interop-contract-enable-input",
                sim_runtime_interop_contract_enable_input,
            ]
        )
    sim_runtime_interop_contract_require_runtime_ready_input = str(
        args.sim_runtime_interop_contract_require_runtime_ready_input
    ).strip()
    if sim_runtime_interop_contract_require_runtime_ready_input:
        cmd.extend(
            [
                "--sim-runtime-interop-contract-require-runtime-ready-input",
                sim_runtime_interop_contract_require_runtime_ready_input,
            ]
        )
    sim_runtime_interop_contract_runner = str(args.sim_runtime_interop_contract_runner).strip()
    if sim_runtime_interop_contract_runner:
        cmd.extend(["--sim-runtime-interop-contract-runner", sim_runtime_interop_contract_runner])
    sim_runtime_interop_export_runner = str(args.sim_runtime_interop_export_runner).strip()
    if sim_runtime_interop_export_runner:
        cmd.extend(["--sim-runtime-interop-export-runner", sim_runtime_interop_export_runner])
    sim_runtime_interop_export_road_length_scale = str(args.sim_runtime_interop_export_road_length_scale).strip()
    if sim_runtime_interop_export_road_length_scale:
        cmd.extend(
            [
                "--sim-runtime-interop-export-road-length-scale",
                sim_runtime_interop_export_road_length_scale,
            ]
        )
    sim_runtime_interop_export_xosc_out = str(args.sim_runtime_interop_export_xosc_out).strip()
    if sim_runtime_interop_export_xosc_out:
        cmd.extend(["--sim-runtime-interop-export-xosc-out", sim_runtime_interop_export_xosc_out])
    sim_runtime_interop_export_xodr_out = str(args.sim_runtime_interop_export_xodr_out).strip()
    if sim_runtime_interop_export_xodr_out:
        cmd.extend(["--sim-runtime-interop-export-xodr-out", sim_runtime_interop_export_xodr_out])
    sim_runtime_interop_export_out = str(args.sim_runtime_interop_export_out).strip()
    if sim_runtime_interop_export_out:
        cmd.extend(["--sim-runtime-interop-export-out", sim_runtime_interop_export_out])
    sim_runtime_interop_import_runner = str(args.sim_runtime_interop_import_runner).strip()
    if sim_runtime_interop_import_runner:
        cmd.extend(["--sim-runtime-interop-import-runner", sim_runtime_interop_import_runner])
    sim_runtime_interop_import_out = str(args.sim_runtime_interop_import_out).strip()
    if sim_runtime_interop_import_out:
        cmd.extend(["--sim-runtime-interop-import-out", sim_runtime_interop_import_out])
    sim_runtime_interop_import_manifest_consistency_mode = (
        str(args.sim_runtime_interop_import_manifest_consistency_mode).strip().lower()
    )
    if sim_runtime_interop_import_manifest_consistency_mode:
        cmd.extend(
            [
                "--sim-runtime-interop-import-manifest-consistency-mode",
                sim_runtime_interop_import_manifest_consistency_mode,
            ]
        )
    sim_runtime_interop_import_export_consistency_mode = (
        str(args.sim_runtime_interop_import_export_consistency_mode).strip().lower()
    )
    if sim_runtime_interop_import_export_consistency_mode:
        cmd.extend(
            [
                "--sim-runtime-interop-import-export-consistency-mode",
                sim_runtime_interop_import_export_consistency_mode,
            ]
        )
    sim_runtime_interop_contract_xosc = str(args.sim_runtime_interop_contract_xosc).strip()
    if sim_runtime_interop_contract_xosc:
        cmd.extend(["--sim-runtime-interop-contract-xosc", sim_runtime_interop_contract_xosc])
    sim_runtime_interop_contract_xodr = str(args.sim_runtime_interop_contract_xodr).strip()
    if sim_runtime_interop_contract_xodr:
        cmd.extend(["--sim-runtime-interop-contract-xodr", sim_runtime_interop_contract_xodr])
    sim_runtime_interop_contract_out = str(args.sim_runtime_interop_contract_out).strip()
    if sim_runtime_interop_contract_out:
        cmd.extend(["--sim-runtime-interop-contract-out", sim_runtime_interop_contract_out])

    sds_versions_csv = str(args.sds_versions_csv).strip()
    if sds_versions_csv:
        cmd.extend(["--sds-versions-csv", sds_versions_csv])

    default_sds_versions = str(args.default_sds_versions).strip()
    if default_sds_versions:
        cmd.extend(["--default-sds-versions", default_sds_versions])

    if args.dry_run:
        cmd.append("--dry-run")
    return cmd


def validate_trend_inputs(args: argparse.Namespace) -> None:
    trend_window = str(args.trend_window).strip()
    if trend_window:
        parse_int(trend_window, default=0, field="trend-window", minimum=0)

    trend_min_pass_rate = str(args.trend_min_pass_rate).strip()
    if trend_min_pass_rate:
        parse_float(
            trend_min_pass_rate,
            default=0.8,
            field="trend-min-pass-rate",
        )

    trend_min_samples = str(args.trend_min_samples).strip()
    if trend_min_samples:
        parse_int(
            trend_min_samples,
            default=3,
            field="trend-min-samples",
            minimum=1,
        )


def validate_functional_quality_gate_inputs(args: argparse.Namespace) -> None:
    parse_bool(
        str(args.phase2_route_gate_require_status_pass_input),
        default=(str(args.phase2_route_gate_require_status_pass_default).strip() == "true"),
        field="phase2-route-gate-require-status-pass-input",
    )
    parse_bool(
        str(args.phase2_route_gate_require_routing_semantic_pass_input),
        default=(str(args.phase2_route_gate_require_routing_semantic_pass_default).strip() == "true"),
        field="phase2-route-gate-require-routing-semantic-pass-input",
    )
    parse_int(
        str(args.phase2_route_gate_min_lane_count),
        default=0,
        field="phase2-route-gate-min-lane-count",
        minimum=0,
    )
    parse_non_negative_float(
        str(args.phase2_route_gate_min_total_length_m),
        default=0.0,
        field="phase2-route-gate-min-total-length-m",
    )
    parse_int(
        str(args.phase2_route_gate_max_routing_semantic_warning_count),
        default=0,
        field="phase2-route-gate-max-routing-semantic-warning-count",
        minimum=0,
    )
    parse_int(
        str(args.phase2_route_gate_max_unreachable_lane_count),
        default=0,
        field="phase2-route-gate-max-unreachable-lane-count",
        minimum=0,
    )
    parse_int(
        str(args.phase2_route_gate_max_non_reciprocal_link_warning_count),
        default=0,
        field="phase2-route-gate-max-non-reciprocal-link-warning-count",
        minimum=0,
    )
    parse_int(
        str(args.phase2_route_gate_max_continuity_gap_warning_count),
        default=0,
        field="phase2-route-gate-max-continuity-gap-warning-count",
        minimum=0,
    )
    phase3_overlap = parse_float(
        str(args.phase3_control_gate_max_overlap_ratio),
        default=0.0,
        field="phase3-control-gate-max-overlap-ratio",
    )
    phase3_steering_rate = parse_non_negative_float(
        str(args.phase3_control_gate_max_steering_rate_degps),
        default=0.0,
        field="phase3-control-gate-max-steering-rate-degps",
    )
    phase3_throttle_plus_brake = parse_non_negative_float(
        str(args.phase3_control_gate_max_throttle_plus_brake),
        default=0.0,
        field="phase3-control-gate-max-throttle-plus-brake",
    )
    phase3_speed_tracking_abs = parse_non_negative_float(
        str(args.phase3_control_gate_max_speed_tracking_error_abs_mps),
        default=0.0,
        field="phase3-control-gate-max-speed-tracking-error-abs-mps",
    )
    phase3_dataset_min_run_summary_count = parse_int(
        str(args.phase3_dataset_gate_min_run_summary_count),
        default=0,
        field="phase3-dataset-gate-min-run-summary-count",
        minimum=0,
    )
    phase3_dataset_min_traffic_profile_count = parse_int(
        str(args.phase3_dataset_gate_min_traffic_profile_count),
        default=0,
        field="phase3-dataset-gate-min-traffic-profile-count",
        minimum=0,
    )
    phase3_dataset_min_actor_pattern_count = parse_int(
        str(args.phase3_dataset_gate_min_actor_pattern_count),
        default=0,
        field="phase3-dataset-gate-min-actor-pattern-count",
        minimum=0,
    )
    phase3_dataset_min_avg_npc_count = parse_non_negative_float(
        str(args.phase3_dataset_gate_min_avg_npc_count),
        default=0.0,
        field="phase3-dataset-gate-min-avg-npc-count",
    )
    phase3_lane_risk_gate_min_ttc_same_lane_sec = parse_non_negative_float(
        str(args.phase3_lane_risk_gate_min_ttc_same_lane_sec),
        default=0.0,
        field="phase3-lane-risk-gate-min-ttc-same-lane-sec",
    )
    phase3_lane_risk_gate_min_ttc_adjacent_lane_sec = parse_non_negative_float(
        str(args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec),
        default=0.0,
        field="phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
    )
    phase3_lane_risk_gate_min_ttc_any_lane_sec = parse_non_negative_float(
        str(args.phase3_lane_risk_gate_min_ttc_any_lane_sec),
        default=0.0,
        field="phase3-lane-risk-gate-min-ttc-any-lane-sec",
    )
    phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total = parse_int(
        str(args.phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total),
        default=0,
        field="phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
        minimum=0,
    )
    phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total = parse_int(
        str(args.phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total),
        default=0,
        field="phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
        minimum=0,
    )
    phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total = parse_int(
        str(args.phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total),
        default=0,
        field="phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total",
        minimum=0,
    )
    if (
        str(args.phase3_lane_risk_gate_min_ttc_same_lane_sec).strip()
        and phase3_lane_risk_gate_min_ttc_same_lane_sec <= 0.0
    ):
        raise ValueError("phase3-lane-risk-gate-min-ttc-same-lane-sec must be > 0")
    if (
        str(args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec).strip()
        and phase3_lane_risk_gate_min_ttc_adjacent_lane_sec <= 0.0
    ):
        raise ValueError("phase3-lane-risk-gate-min-ttc-adjacent-lane-sec must be > 0")
    if (
        str(args.phase3_lane_risk_gate_min_ttc_any_lane_sec).strip()
        and phase3_lane_risk_gate_min_ttc_any_lane_sec <= 0.0
    ):
        raise ValueError("phase3-lane-risk-gate-min-ttc-any-lane-sec must be > 0")
    phase3_enable_ego_collision_avoidance = parse_bool(
        str(args.phase3_enable_ego_collision_avoidance_input),
        default=(str(args.phase3_enable_ego_collision_avoidance_default).strip() == "true"),
        field="phase3-enable-ego-collision-avoidance-input",
    )
    phase3_avoidance_ttc_threshold_sec = parse_non_negative_float(
        str(args.phase3_avoidance_ttc_threshold_sec),
        default=0.0,
        field="phase3-avoidance-ttc-threshold-sec",
    )
    phase3_ego_max_brake_mps2 = parse_non_negative_float(
        str(args.phase3_ego_max_brake_mps2),
        default=0.0,
        field="phase3-ego-max-brake-mps2",
    )
    phase3_tire_friction_coeff = parse_non_negative_float(
        str(args.phase3_tire_friction_coeff),
        default=0.0,
        field="phase3-tire-friction-coeff",
    )
    phase3_surface_friction_scale = parse_non_negative_float(
        str(args.phase3_surface_friction_scale),
        default=0.0,
        field="phase3-surface-friction-scale",
    )
    phase3_core_sim_gate_require_success = parse_bool(
        str(args.phase3_core_sim_gate_require_success_input),
        default=(str(args.phase3_core_sim_gate_require_success_default).strip() == "true"),
        field="phase3-core-sim-gate-require-success-input",
    )
    phase3_core_sim_gate_min_ttc_same_lane_sec = parse_non_negative_float(
        str(args.phase3_core_sim_gate_min_ttc_same_lane_sec),
        default=0.0,
        field="phase3-core-sim-gate-min-ttc-same-lane-sec",
    )
    phase3_core_sim_gate_min_ttc_any_lane_sec = parse_non_negative_float(
        str(args.phase3_core_sim_gate_min_ttc_any_lane_sec),
        default=0.0,
        field="phase3-core-sim-gate-min-ttc-any-lane-sec",
    )
    phase3_core_sim_matrix_gate_require_all_cases_success = parse_bool(
        str(args.phase3_core_sim_matrix_gate_require_all_cases_success_input),
        default=(str(args.phase3_core_sim_matrix_gate_require_all_cases_success_default).strip() == "true"),
        field="phase3-core-sim-matrix-gate-require-all-cases-success-input",
    )
    phase3_core_sim_matrix_gate_min_ttc_same_lane_sec = parse_non_negative_float(
        str(args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec),
        default=0.0,
        field="phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
    )
    phase3_core_sim_matrix_gate_min_ttc_any_lane_sec = parse_non_negative_float(
        str(args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec),
        default=0.0,
        field="phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
    )
    phase3_core_sim_matrix_gate_max_failed_cases = parse_int(
        str(args.phase3_core_sim_matrix_gate_max_failed_cases),
        default=0,
        field="phase3-core-sim-matrix-gate-max-failed-cases",
        minimum=0,
    )
    phase3_core_sim_matrix_gate_max_collision_cases = parse_int(
        str(args.phase3_core_sim_matrix_gate_max_collision_cases),
        default=0,
        field="phase3-core-sim-matrix-gate-max-collision-cases",
        minimum=0,
    )
    phase3_core_sim_matrix_gate_max_timeout_cases = parse_int(
        str(args.phase3_core_sim_matrix_gate_max_timeout_cases),
        default=0,
        field="phase3-core-sim-matrix-gate-max-timeout-cases",
        minimum=0,
    )
    if str(args.phase3_avoidance_ttc_threshold_sec).strip() and phase3_avoidance_ttc_threshold_sec <= 0.0:
        raise ValueError("phase3-avoidance-ttc-threshold-sec must be > 0")
    if str(args.phase3_ego_max_brake_mps2).strip() and phase3_ego_max_brake_mps2 <= 0.0:
        raise ValueError("phase3-ego-max-brake-mps2 must be > 0")
    if str(args.phase3_tire_friction_coeff).strip() and phase3_tire_friction_coeff <= 0.0:
        raise ValueError("phase3-tire-friction-coeff must be > 0")
    if str(args.phase3_surface_friction_scale).strip() and phase3_surface_friction_scale <= 0.0:
        raise ValueError("phase3-surface-friction-scale must be > 0")
    if (
        str(args.phase3_core_sim_gate_min_ttc_same_lane_sec).strip()
        and phase3_core_sim_gate_min_ttc_same_lane_sec <= 0.0
    ):
        raise ValueError("phase3-core-sim-gate-min-ttc-same-lane-sec must be > 0")
    if (
        str(args.phase3_core_sim_gate_min_ttc_any_lane_sec).strip()
        and phase3_core_sim_gate_min_ttc_any_lane_sec <= 0.0
    ):
        raise ValueError("phase3-core-sim-gate-min-ttc-any-lane-sec must be > 0")
    if (
        str(args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec).strip()
        and phase3_core_sim_matrix_gate_min_ttc_same_lane_sec <= 0.0
    ):
        raise ValueError("phase3-core-sim-matrix-gate-min-ttc-same-lane-sec must be > 0")
    if (
        str(args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec).strip()
        and phase3_core_sim_matrix_gate_min_ttc_any_lane_sec <= 0.0
    ):
        raise ValueError("phase3-core-sim-matrix-gate-min-ttc-any-lane-sec must be > 0")
    if phase3_enable_ego_collision_avoidance and (
        phase3_avoidance_ttc_threshold_sec <= 0.0 or phase3_ego_max_brake_mps2 <= 0.0
    ):
        raise ValueError(
            "phase3-enable-ego-collision-avoidance-input=true requires "
            "phase3-avoidance-ttc-threshold-sec>0 and phase3-ego-max-brake-mps2>0"
        )
    if (
        phase3_overlap > 0.0
        or phase3_steering_rate > 0.0
        or phase3_throttle_plus_brake > 0.0
        or phase3_speed_tracking_abs > 0.0
        or phase3_dataset_min_run_summary_count > 0
        or phase3_dataset_min_traffic_profile_count > 0
        or phase3_dataset_min_actor_pattern_count > 0
        or phase3_dataset_min_avg_npc_count > 0.0
        or phase3_lane_risk_gate_min_ttc_same_lane_sec > 0.0
        or phase3_lane_risk_gate_min_ttc_adjacent_lane_sec > 0.0
        or phase3_lane_risk_gate_min_ttc_any_lane_sec > 0.0
        or phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total > 0
        or phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total > 0
        or phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total > 0
        or phase3_enable_ego_collision_avoidance
        or phase3_avoidance_ttc_threshold_sec > 0.0
        or phase3_ego_max_brake_mps2 > 0.0
        or phase3_tire_friction_coeff > 0.0
        or phase3_surface_friction_scale > 0.0
        or phase3_core_sim_gate_require_success
        or phase3_core_sim_gate_min_ttc_same_lane_sec > 0.0
        or phase3_core_sim_gate_min_ttc_any_lane_sec > 0.0
        or phase3_core_sim_matrix_gate_require_all_cases_success
        or phase3_core_sim_matrix_gate_min_ttc_same_lane_sec > 0.0
        or phase3_core_sim_matrix_gate_min_ttc_any_lane_sec > 0.0
        or phase3_core_sim_matrix_gate_max_failed_cases > 0
        or phase3_core_sim_matrix_gate_max_collision_cases > 0
        or phase3_core_sim_matrix_gate_max_timeout_cases > 0
        or bool(str(args.phase3_core_sim_runner).strip())
        or bool(str(args.phase3_core_sim_scenario).strip())
        or bool(str(args.phase3_core_sim_run_id).strip())
        or bool(str(args.phase3_core_sim_out_root).strip())
    ):
        phase3_enable_hooks = parse_bool(
            str(args.phase3_enable_hooks_input),
            default=(str(args.phase3_enable_hooks_default).strip() == "true"),
            field="phase3-enable-hooks-input",
        )
        if not phase3_enable_hooks:
            raise ValueError(
                "phase3-control-gate-*/phase3-dataset-gate-* inputs require phase3-enable-hooks-input=true"
            )


def validate_phase3_runtime_inputs(args: argparse.Namespace) -> None:
    sim_runtime = str(args.sim_runtime).strip().lower()
    if sim_runtime and sim_runtime not in {"none", "awsim", "carla"}:
        raise ValueError(f"sim-runtime must be one of: none, awsim, carla; got: {args.sim_runtime}")
    args.sim_runtime = sim_runtime
    sim_runtime_for_policy = sim_runtime if sim_runtime else "none"

    sim_runtime_mode = str(args.sim_runtime_mode).strip().lower()
    if sim_runtime_mode and sim_runtime_mode not in {"headless", "interactive"}:
        raise ValueError(
            f"sim-runtime-mode must be one of: headless, interactive; got: {args.sim_runtime_mode}"
        )
    args.sim_runtime_mode = sim_runtime_mode

    sim_runtime_probe_enable = parse_bool(
        args.sim_runtime_probe_enable_input,
        default=(args.sim_runtime_probe_enable_default == "true"),
        field="sim-runtime-probe-enable-input",
    )
    sim_runtime_probe_execute = parse_bool(
        args.sim_runtime_probe_execute_input,
        default=(args.sim_runtime_probe_execute_default == "true"),
        field="sim-runtime-probe-execute-input",
    )
    sim_runtime_probe_require_availability = parse_bool(
        args.sim_runtime_probe_require_availability_input,
        default=(args.sim_runtime_probe_require_availability_default == "true"),
        field="sim-runtime-probe-require-availability-input",
    )
    sim_runtime_probe_flag = str(args.sim_runtime_probe_flag).strip()
    sim_runtime_probe_args_shlex = str(args.sim_runtime_probe_args_shlex).strip()
    sim_runtime_scenario_contract_enable = parse_bool(
        args.sim_runtime_scenario_contract_enable_input,
        default=(args.sim_runtime_scenario_contract_enable_default == "true"),
        field="sim-runtime-scenario-contract-enable-input",
    )
    sim_runtime_scenario_contract_require_runtime_ready = parse_bool(
        args.sim_runtime_scenario_contract_require_runtime_ready_input,
        default=(args.sim_runtime_scenario_contract_require_runtime_ready_default == "true"),
        field="sim-runtime-scenario-contract-require-runtime-ready-input",
    )
    sim_runtime_scene_result_enable = parse_bool(
        args.sim_runtime_scene_result_enable_input,
        default=(args.sim_runtime_scene_result_enable_default == "true"),
        field="sim-runtime-scene-result-enable-input",
    )
    sim_runtime_scene_result_require_runtime_ready = parse_bool(
        args.sim_runtime_scene_result_require_runtime_ready_input,
        default=(args.sim_runtime_scene_result_require_runtime_ready_default == "true"),
        field="sim-runtime-scene-result-require-runtime-ready-input",
    )
    sim_runtime_interop_contract_enable = parse_bool(
        args.sim_runtime_interop_contract_enable_input,
        default=(args.sim_runtime_interop_contract_enable_default == "true"),
        field="sim-runtime-interop-contract-enable-input",
    )
    sim_runtime_interop_contract_require_runtime_ready = parse_bool(
        args.sim_runtime_interop_contract_require_runtime_ready_input,
        default=(args.sim_runtime_interop_contract_require_runtime_ready_default == "true"),
        field="sim-runtime-interop-contract-require-runtime-ready-input",
    )
    sim_runtime_interop_import_manifest_consistency_mode = (
        str(args.sim_runtime_interop_import_manifest_consistency_mode).strip().lower()
    )
    if sim_runtime_interop_import_manifest_consistency_mode not in {"", "require", "allow"}:
        raise ValueError(
            "sim-runtime-interop-import-manifest-consistency-mode must be one of: require, allow"
        )
    sim_runtime_interop_import_export_consistency_mode = (
        str(args.sim_runtime_interop_import_export_consistency_mode).strip().lower()
    )
    if sim_runtime_interop_import_export_consistency_mode not in {"", "require", "allow"}:
        raise ValueError(
            "sim-runtime-interop-import-export-consistency-mode must be one of: require, allow"
        )
    if (sim_runtime_probe_execute or sim_runtime_probe_require_availability) and not sim_runtime_probe_enable:
        raise ValueError(
            "sim-runtime-probe-execute-input/sim-runtime-probe-require-availability-input "
            "requires sim-runtime-probe-enable-input=true"
        )
    if (sim_runtime_probe_flag or sim_runtime_probe_args_shlex) and not sim_runtime_probe_enable:
        raise ValueError(
            "sim-runtime-probe-flag/sim-runtime-probe-args-shlex "
            "requires sim-runtime-probe-enable-input=true"
        )
    if sim_runtime_scenario_contract_require_runtime_ready and not sim_runtime_scenario_contract_enable:
        raise ValueError(
            "sim-runtime-scenario-contract-require-runtime-ready-input "
            "requires sim-runtime-scenario-contract-enable-input=true"
        )
    if sim_runtime_interop_contract_require_runtime_ready and not sim_runtime_interop_contract_enable:
        raise ValueError(
            "sim-runtime-interop-contract-require-runtime-ready-input "
            "requires sim-runtime-interop-contract-enable-input=true"
        )
    if (
        sim_runtime_interop_import_manifest_consistency_mode
        or sim_runtime_interop_import_export_consistency_mode
    ) and not sim_runtime_interop_contract_enable:
        raise ValueError(
            "sim-runtime-interop-import-*-consistency-mode requires "
            "sim-runtime-interop-contract-enable-input=true"
        )
    if sim_runtime_scene_result_require_runtime_ready and not sim_runtime_scene_result_enable:
        raise ValueError(
            "sim-runtime-scene-result-require-runtime-ready-input "
            "requires sim-runtime-scene-result-enable-input=true"
        )
    if sim_runtime_scene_result_enable and not sim_runtime_scenario_contract_enable:
        raise ValueError(
            "sim-runtime-scene-result-enable-input "
            "requires sim-runtime-scenario-contract-enable-input=true"
        )
    if sim_runtime_for_policy == "none" and (
        sim_runtime_probe_enable
        or sim_runtime_probe_execute
        or sim_runtime_probe_require_availability
        or bool(sim_runtime_probe_flag)
        or bool(sim_runtime_probe_args_shlex)
    ):
        raise ValueError(
            "sim-runtime-probe-* inputs require sim-runtime to be one of: awsim, carla"
        )
    if sim_runtime_for_policy == "none" and (
        sim_runtime_scenario_contract_enable or sim_runtime_scenario_contract_require_runtime_ready
    ):
        raise ValueError(
            "sim-runtime-scenario-contract-* inputs require sim-runtime to be one of: awsim, carla"
        )
    if sim_runtime_scenario_contract_require_runtime_ready and not sim_runtime_probe_enable:
        raise ValueError(
            "sim-runtime-scenario-contract-require-runtime-ready-input "
            "requires sim-runtime-probe-enable-input=true"
        )
    if sim_runtime_for_policy == "none" and (
        sim_runtime_scene_result_enable or sim_runtime_scene_result_require_runtime_ready
    ):
        raise ValueError(
            "sim-runtime-scene-result-* inputs require sim-runtime to be one of: awsim, carla"
        )
    if sim_runtime_scene_result_require_runtime_ready and not sim_runtime_probe_enable:
        raise ValueError(
            "sim-runtime-scene-result-require-runtime-ready-input "
            "requires sim-runtime-probe-enable-input=true"
        )
    if sim_runtime_for_policy == "none" and (
        sim_runtime_interop_contract_enable or sim_runtime_interop_contract_require_runtime_ready
    ):
        raise ValueError(
            "sim-runtime-interop-contract-* inputs require sim-runtime to be one of: awsim, carla"
        )
    if sim_runtime_for_policy == "none" and (
        bool(sim_runtime_interop_import_manifest_consistency_mode)
        or bool(sim_runtime_interop_import_export_consistency_mode)
    ):
        raise ValueError(
            "sim-runtime-interop-contract-* inputs require sim-runtime to be one of: awsim, carla"
        )
    if sim_runtime_interop_contract_require_runtime_ready and not sim_runtime_probe_enable:
        raise ValueError(
            "sim-runtime-interop-contract-require-runtime-ready-input "
            "requires sim-runtime-probe-enable-input=true"
        )
    sim_runtime_assert_artifacts = parse_bool(
        args.sim_runtime_assert_artifacts_input,
        default=(args.sim_runtime_assert_artifacts_default == "true"),
        field="sim-runtime-assert-artifacts-input",
    )
    if sim_runtime_for_policy == "none" and sim_runtime_assert_artifacts:
        raise ValueError(
            "sim-runtime-assert-artifacts-input requires sim-runtime to be one of: awsim, carla"
        )
    args.sim_runtime_probe_enable = bool(sim_runtime_probe_enable)
    args.sim_runtime_probe_execute = bool(sim_runtime_probe_execute)
    args.sim_runtime_probe_require_availability = bool(sim_runtime_probe_require_availability)
    args.sim_runtime_scenario_contract_enable = bool(sim_runtime_scenario_contract_enable)
    args.sim_runtime_scenario_contract_require_runtime_ready = bool(
        sim_runtime_scenario_contract_require_runtime_ready
    )
    args.sim_runtime_scene_result_enable = bool(sim_runtime_scene_result_enable)
    args.sim_runtime_scene_result_require_runtime_ready = bool(
        sim_runtime_scene_result_require_runtime_ready
    )
    args.sim_runtime_interop_contract_enable = bool(sim_runtime_interop_contract_enable)
    args.sim_runtime_interop_contract_require_runtime_ready = bool(
        sim_runtime_interop_contract_require_runtime_ready
    )
    args.sim_runtime_interop_import_manifest_consistency_mode = sim_runtime_interop_import_manifest_consistency_mode
    args.sim_runtime_interop_import_export_consistency_mode = sim_runtime_interop_import_export_consistency_mode
    args.sim_runtime_assert_artifacts = bool(sim_runtime_assert_artifacts)


def _extract_ci_manifest_path(stdout_text: str) -> str:
    manifest_path = ""
    for raw_line in str(stdout_text).splitlines():
        line = raw_line.strip()
        if line.startswith("[ok] ci_manifest_path="):
            manifest_path = line.split("=", 1)[1].strip()
    return manifest_path


def _load_json_object(path: Path, *, subject: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return payload


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


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _ensure_schema_version(payload: dict[str, Any], *, field: str, expected: str, subject: str) -> None:
    actual = str(payload.get(field, "")).strip()
    if actual != expected:
        raise ValueError(f"{subject} {field} must be {expected}, got: {actual or '<empty>'}")


def _load_report_object(path: Path, *, subject: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{subject} not found: {path}")
    return _load_json_object(path, subject=subject)


def validate_runtime_artifacts(
    *,
    manifest_path: Path,
    runtime: str,
    probe_enabled: bool,
    probe_execute: bool,
    probe_require_availability: bool,
    scenario_contract_enabled: bool,
    scenario_contract_require_runtime_ready: bool,
    scene_result_enabled: bool,
    scene_result_require_runtime_ready: bool,
    interop_contract_enabled: bool,
    interop_contract_require_runtime_ready: bool,
) -> dict[str, Any]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"pipeline manifest not found: {manifest_path}")
    manifest_payload = _load_json_object(manifest_path, subject="pipeline manifest")

    phase3_hooks = manifest_payload.get("phase3_hooks")
    if not isinstance(phase3_hooks, dict) or not bool(phase3_hooks.get("enabled")):
        raise ValueError("pipeline manifest phase3_hooks.enabled must be true")

    adapter_payload = phase3_hooks.get("sim_runtime_adapter")
    if not isinstance(adapter_payload, dict) or not bool(adapter_payload.get("enabled")):
        raise ValueError("pipeline manifest phase3_hooks.sim_runtime_adapter.enabled must be true")

    adapter_runtime = str(adapter_payload.get("runtime", "")).strip().lower()
    if adapter_runtime and adapter_runtime != runtime:
        raise ValueError(
            f"pipeline manifest sim_runtime_adapter.runtime mismatch: expected {runtime}, got {adapter_runtime}"
        )

    adapter_out_text = str(adapter_payload.get("out", "")).strip()
    if not adapter_out_text:
        raise ValueError("pipeline manifest sim_runtime_adapter.out must be a non-empty string")
    adapter_out_path = Path(adapter_out_text).resolve()
    adapter_report_payload = _load_report_object(adapter_out_path, subject="runtime adapter report")
    _ensure_schema_version(
        adapter_report_payload,
        field="sim_runtime_adapter_schema_version",
        expected=SIM_RUNTIME_ADAPTER_SCHEMA_VERSION_V0,
        subject="runtime adapter report",
    )
    adapter_report_runtime = str(adapter_report_payload.get("runtime", "")).strip().lower()
    if adapter_report_runtime and adapter_report_runtime != runtime:
        raise ValueError(
            f"runtime adapter report runtime mismatch: expected {runtime}, got {adapter_report_runtime}"
        )
    adapter_runtime_contract = adapter_report_payload.get("runtime_contract")
    if not isinstance(adapter_runtime_contract, dict):
        raise ValueError("runtime adapter report runtime_contract must be a JSON object")
    adapter_runtime_entrypoint = str(adapter_runtime_contract.get("runtime_entrypoint", "")).strip()
    if not adapter_runtime_entrypoint:
        raise ValueError("runtime adapter report runtime_contract.runtime_entrypoint must be non-empty")
    adapter_runtime_reference_repo = str(adapter_runtime_contract.get("reference_repo", "")).strip()
    if not adapter_runtime_reference_repo:
        raise ValueError("runtime adapter report runtime_contract.reference_repo must be non-empty")
    adapter_runtime_bridge_contract = str(adapter_runtime_contract.get("bridge_contract", "")).strip()
    if not adapter_runtime_bridge_contract:
        raise ValueError("runtime adapter report runtime_contract.bridge_contract must be non-empty")

    launch_manifest_out_text = str(adapter_payload.get("launch_manifest_out", "")).strip()
    if not launch_manifest_out_text:
        raise ValueError("pipeline manifest sim_runtime_adapter.launch_manifest_out must be a non-empty string")
    launch_manifest_out_path = Path(launch_manifest_out_text).resolve()
    launch_manifest_payload = _load_report_object(launch_manifest_out_path, subject="runtime launch manifest")
    _ensure_schema_version(
        launch_manifest_payload,
        field="sim_runtime_launch_manifest_schema_version",
        expected=SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0,
        subject="runtime launch manifest",
    )
    launch_runtime = str(launch_manifest_payload.get("runtime", "")).strip().lower()
    if launch_runtime and launch_runtime != runtime:
        raise ValueError(
            f"runtime launch manifest runtime mismatch: expected {runtime}, got {launch_runtime}"
        )
    launch_runtime_contract = launch_manifest_payload.get("runtime_contract")
    if not isinstance(launch_runtime_contract, dict):
        raise ValueError("runtime launch manifest runtime_contract must be a JSON object")
    launch_runtime_entrypoint = str(launch_runtime_contract.get("runtime_entrypoint", "")).strip()
    if launch_runtime_entrypoint != adapter_runtime_entrypoint:
        raise ValueError(
            "runtime launch manifest runtime_contract.runtime_entrypoint does not match adapter report"
        )
    launch_runtime_reference_repo = str(launch_runtime_contract.get("reference_repo", "")).strip()
    if launch_runtime_reference_repo != adapter_runtime_reference_repo:
        raise ValueError(
            "runtime launch manifest runtime_contract.reference_repo does not match adapter report"
        )

    report: dict[str, Any] = {
        "validated": True,
        "manifest_path": str(manifest_path),
        "runtime": runtime,
        "adapter_out": str(adapter_out_path),
        "launch_manifest_out": str(launch_manifest_out_path),
        "runtime_entrypoint": adapter_runtime_entrypoint,
        "runtime_reference_repo": adapter_runtime_reference_repo,
        "runtime_bridge_contract": adapter_runtime_bridge_contract,
    }

    if not probe_enabled:
        report["probe_checked"] = False
        report["scenario_contract_checked"] = False
        report["scene_result_checked"] = False
        report["interop_contract_checked"] = False
        return report

    probe_payload = phase3_hooks.get("sim_runtime_probe")
    if not isinstance(probe_payload, dict) or not bool(probe_payload.get("enabled")):
        raise ValueError("pipeline manifest phase3_hooks.sim_runtime_probe.enabled must be true")

    probe_runtime = str(probe_payload.get("runtime", "")).strip().lower()
    if probe_runtime and probe_runtime != runtime:
        raise ValueError(
            f"pipeline manifest sim_runtime_probe.runtime mismatch: expected {runtime}, got {probe_runtime}"
        )

    probe_out_text = str(probe_payload.get("out", "")).strip()
    if not probe_out_text:
        raise ValueError("pipeline manifest sim_runtime_probe.out must be a non-empty string")
    probe_out_path = Path(probe_out_text).resolve()
    probe_report_payload = _load_report_object(probe_out_path, subject="runtime probe report")
    _ensure_schema_version(
        probe_report_payload,
        field="sim_runtime_probe_schema_version",
        expected=SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0,
        subject="runtime probe report",
    )
    probe_report_runtime = str(probe_report_payload.get("runtime", "")).strip().lower()
    if probe_report_runtime and probe_report_runtime != runtime:
        raise ValueError(
            f"runtime probe report runtime mismatch: expected {runtime}, got {probe_report_runtime}"
        )
    probe_report_runtime_available = bool(probe_report_payload.get("runtime_available", False))
    probe_report_probe_executed = bool(probe_report_payload.get("probe_executed", False))
    probe_report_probe_returncode = _parse_int_or_default(
        probe_report_payload.get("probe_returncode"),
        default=0,
    )
    probe_report_probe_returncode_acceptable = bool(
        probe_report_payload.get("probe_returncode_acceptable", probe_report_probe_returncode == 0)
    )
    probe_report_probe_command = str(probe_report_payload.get("probe_command", "")).strip()
    probe_report_probe_flag = str(probe_report_payload.get("probe_flag", "")).strip()
    probe_report_probe_args = _normalize_text_list(probe_report_payload.get("probe_args"))
    probe_report_probe_args_source = str(probe_report_payload.get("probe_args_source", "")).strip()
    probe_report_probe_timeout_sec = _parse_float_or_default(
        probe_report_payload.get("probe_timeout_sec"),
        default=0.0,
    )
    probe_report_runtime_bin_sha256 = str(probe_report_payload.get("runtime_bin_sha256", "")).strip()
    probe_report_runner_host = str(probe_report_payload.get("runner_host", "")).strip()
    probe_report_runner_platform = str(probe_report_payload.get("runner_platform", "")).strip()
    probe_report_runner_python = str(probe_report_payload.get("runner_python", "")).strip()
    probe_report_runtime_bin_size_bytes = _parse_int_or_default(
        probe_report_payload.get("runtime_bin_size_bytes"),
        default=0,
    )
    probe_report_runtime_bin_mtime_utc = str(probe_report_payload.get("runtime_bin_mtime_utc", "")).strip()
    probe_report_probe_duration_ms = _parse_int_or_default(
        probe_report_payload.get("probe_duration_ms"),
        default=0,
    )

    runtime_available = bool(probe_payload.get("runtime_available", False))
    probe_executed = bool(probe_payload.get("probe_executed", False))
    probe_returncode = _parse_int_or_default(probe_payload.get("probe_returncode"), default=0)
    probe_returncode_acceptable = bool(
        probe_payload.get("probe_returncode_acceptable", probe_returncode == 0)
    )
    probe_flag_requested = str(probe_payload.get("probe_flag_requested", "")).strip()
    probe_args_requested = _normalize_text_list(probe_payload.get("probe_args_requested"))
    probe_args_requested_source = str(probe_payload.get("probe_args_requested_source", "")).strip()
    probe_flag_payload = str(probe_payload.get("probe_flag", "")).strip()
    probe_args_payload = _normalize_text_list(probe_payload.get("probe_args"))
    probe_args_source_payload = str(probe_payload.get("probe_args_source", "")).strip()
    probe_timeout_sec = _parse_float_or_default(
        probe_payload.get("probe_timeout_sec"),
        default=probe_report_probe_timeout_sec,
    )
    probe_flag = probe_flag_payload or probe_report_probe_flag
    probe_args = probe_args_payload or probe_report_probe_args
    probe_args_source = probe_args_source_payload or probe_report_probe_args_source
    runtime_bin = str(probe_payload.get("runtime_bin", "")).strip()
    runtime_bin_resolved = str(probe_payload.get("runtime_bin_resolved", "")).strip()
    runtime_bin_resolved_exists = bool(runtime_bin_resolved) and Path(runtime_bin_resolved).exists()
    runtime_bin_sha256 = str(probe_payload.get("runtime_bin_sha256", "")).strip() or probe_report_runtime_bin_sha256
    runner_host = str(probe_payload.get("runner_host", "")).strip() or probe_report_runner_host
    runner_platform = str(probe_payload.get("runner_platform", "")).strip() or probe_report_runner_platform
    runner_python = str(probe_payload.get("runner_python", "")).strip() or probe_report_runner_python
    probe_command = str(probe_payload.get("probe_command", "")).strip() or probe_report_probe_command
    runtime_bin_size_bytes = _parse_int_or_default(
        probe_payload.get("runtime_bin_size_bytes"),
        default=probe_report_runtime_bin_size_bytes,
    )
    runtime_bin_mtime_utc = (
        str(probe_payload.get("runtime_bin_mtime_utc", "")).strip() or probe_report_runtime_bin_mtime_utc
    )
    probe_duration_ms = _parse_int_or_default(
        probe_payload.get("probe_duration_ms"),
        default=probe_report_probe_duration_ms,
    )

    if runtime_available != probe_report_runtime_available:
        raise ValueError("pipeline manifest sim_runtime_probe.runtime_available must match runtime probe report")
    if probe_executed != probe_report_probe_executed:
        raise ValueError("pipeline manifest sim_runtime_probe.probe_executed must match runtime probe report")
    if probe_returncode != probe_report_probe_returncode:
        raise ValueError("pipeline manifest sim_runtime_probe.probe_returncode must match runtime probe report")
    if probe_returncode_acceptable != probe_report_probe_returncode_acceptable:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.probe_returncode_acceptable must match runtime probe report"
        )
    if probe_flag_payload and probe_report_probe_flag and probe_flag_payload != probe_report_probe_flag:
        raise ValueError("pipeline manifest sim_runtime_probe.probe_flag must match runtime probe report")
    if probe_args_payload and probe_report_probe_args and probe_args_payload != probe_report_probe_args:
        raise ValueError("pipeline manifest sim_runtime_probe.probe_args must match runtime probe report")
    if (
        probe_args_source_payload
        and probe_report_probe_args_source
        and probe_args_source_payload != probe_report_probe_args_source
    ):
        raise ValueError("pipeline manifest sim_runtime_probe.probe_args_source must match runtime probe report")

    effective_probe_values_present = bool(probe_flag) or bool(probe_args)
    requested_probe_values_present = bool(probe_flag_requested) or bool(probe_args_requested)
    if probe_execute and probe_executed and not effective_probe_values_present:
        raise ValueError(
            "runtime probe report must include probe_flag/probe_args when probe_executed is true"
        )
    if probe_execute and probe_executed and not probe_args_source:
        raise ValueError(
            "runtime probe report probe_args_source must be non-empty when probe_executed is true"
        )
    if effective_probe_values_present and not probe_args_source:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.probe_args_source must be non-empty when probe values are present"
        )
    if probe_args_source and not effective_probe_values_present:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.probe_args_source requires probe_flag/probe_args"
        )
    if requested_probe_values_present and not probe_args_requested_source:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.probe_args_requested_source must be non-empty when requested probe values are present"
        )
    if probe_args_requested_source and not requested_probe_values_present:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.probe_args_requested_source requires probe_flag_requested/probe_args_requested"
        )

    if not runner_host:
        raise ValueError("runtime probe report runner_host must be non-empty")
    if not runner_platform:
        raise ValueError("runtime probe report runner_platform must be non-empty")
    if not runner_python:
        raise ValueError("runtime probe report runner_python must be non-empty")

    if probe_require_availability and not runtime_available:
        raise ValueError("pipeline manifest sim_runtime_probe.runtime_available must be true")
    if probe_require_availability and not runtime_bin_resolved:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.runtime_bin_resolved must be non-empty when availability is required"
        )
    if probe_require_availability and not runtime_bin_resolved_exists:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.runtime_bin_resolved must point to an existing path when availability is required"
        )
    if probe_execute and not probe_executed:
        raise ValueError("pipeline manifest sim_runtime_probe.probe_executed must be true")
    if probe_execute and not probe_returncode_acceptable:
        raise ValueError(
            "pipeline manifest sim_runtime_probe.probe_returncode must be acceptable when probe execution is required"
        )
    if probe_execute and not probe_command:
        raise ValueError(
            "runtime probe report probe_command must be non-empty when probe execution is required"
        )
    if probe_require_availability and not runtime_bin_sha256:
        raise ValueError(
            "runtime probe report runtime_bin_sha256 must be non-empty when availability is required"
        )
    if probe_require_availability and runtime_bin_size_bytes <= 0:
        raise ValueError(
            "runtime probe report runtime_bin_size_bytes must be > 0 when availability is required"
        )

    report.update(
        {
            "probe_checked": True,
            "probe_out": str(probe_out_path),
            "runtime_available": runtime_available,
            "probe_executed": probe_executed,
            "probe_returncode": probe_returncode,
            "probe_returncode_acceptable": probe_returncode_acceptable,
            "runtime_bin": runtime_bin,
            "runtime_bin_resolved": runtime_bin_resolved,
            "runtime_bin_resolved_exists": runtime_bin_resolved_exists,
            "runtime_bin_sha256": runtime_bin_sha256,
            "runtime_bin_size_bytes": int(runtime_bin_size_bytes),
            "runtime_bin_mtime_utc": runtime_bin_mtime_utc,
            "runner_host": runner_host,
            "runner_platform": runner_platform,
            "runner_python": runner_python,
            "probe_flag_requested": probe_flag_requested,
            "probe_args_requested": probe_args_requested,
            "probe_args_requested_source": probe_args_requested_source,
            "probe_flag": probe_flag,
            "probe_args": probe_args,
            "probe_args_source": probe_args_source,
            "probe_timeout_sec": float(probe_timeout_sec),
            "probe_command": probe_command,
            "probe_duration_ms": int(probe_duration_ms),
        }
    )

    scenario_contract_out_path: Optional[Path] = None
    if not scenario_contract_enabled:
        report["scenario_contract_checked"] = False
    else:
        scenario_contract_payload = phase3_hooks.get("sim_runtime_scenario_contract")
        if not isinstance(scenario_contract_payload, dict) or not bool(scenario_contract_payload.get("enabled")):
            raise ValueError("pipeline manifest phase3_hooks.sim_runtime_scenario_contract.enabled must be true")
        scenario_contract_runtime = str(scenario_contract_payload.get("runtime", "")).strip().lower()
        if scenario_contract_runtime and scenario_contract_runtime != runtime:
            raise ValueError(
                "pipeline manifest sim_runtime_scenario_contract.runtime mismatch: "
                f"expected {runtime}, got {scenario_contract_runtime}"
            )
        scenario_contract_out_text = str(scenario_contract_payload.get("out", "")).strip()
        if not scenario_contract_out_text:
            raise ValueError("pipeline manifest sim_runtime_scenario_contract.out must be a non-empty string")
        scenario_contract_out_path = Path(scenario_contract_out_text).resolve()
        scenario_contract_report_payload = _load_report_object(
            scenario_contract_out_path,
            subject="runtime scenario contract report",
        )
        _ensure_schema_version(
            scenario_contract_report_payload,
            field="sim_runtime_scenario_contract_schema_version",
            expected=SIM_RUNTIME_SCENARIO_CONTRACT_SCHEMA_VERSION_V0,
            subject="runtime scenario contract report",
        )
        scenario_report_runtime = str(scenario_contract_report_payload.get("runtime", "")).strip().lower()
        if scenario_report_runtime and scenario_report_runtime != runtime:
            raise ValueError(
                f"runtime scenario contract report runtime mismatch: expected {runtime}, got {scenario_report_runtime}"
            )
        scenario_runtime_ready_report = bool(scenario_contract_report_payload.get("runtime_ready", False))
        scenario_runtime_ready_payload = bool(scenario_contract_payload.get("runtime_ready", False))
        if scenario_runtime_ready_payload != scenario_runtime_ready_report:
            raise ValueError(
                "pipeline manifest sim_runtime_scenario_contract.runtime_ready must match runtime scenario contract report"
            )
        scenario_status_payload = str(scenario_contract_payload.get("scenario_contract_status", "")).strip()
        scenario_status_report = str(scenario_contract_report_payload.get("scenario_contract_status", "")).strip()
        if scenario_status_payload and scenario_status_report and scenario_status_payload != scenario_status_report:
            raise ValueError(
                "pipeline manifest sim_runtime_scenario_contract.scenario_contract_status "
                "must match runtime scenario contract report"
            )
        if scenario_contract_require_runtime_ready and not scenario_runtime_ready_report:
            raise ValueError("pipeline manifest sim_runtime_scenario_contract.runtime_ready must be true")
        if scenario_contract_require_runtime_ready and scenario_status_report and scenario_status_report.lower() != "pass":
            raise ValueError(
                "runtime scenario contract report scenario_contract_status must be pass when runtime-ready is required"
            )

        scenario_actor_count = _parse_int_or_default(scenario_contract_report_payload.get("actor_count"), default=0)
        scenario_sensor_stream_count = _parse_int_or_default(
            scenario_contract_report_payload.get("sensor_stream_count"),
            default=0,
        )
        scenario_estimated_scene_frame_count = _parse_int_or_default(
            scenario_contract_report_payload.get("estimated_scene_frame_count"),
            default=0,
        )
        scenario_executed_step_count = _parse_int_or_default(
            scenario_contract_report_payload.get("executed_step_count"),
            default=0,
        )
        scenario_sim_duration_sec = _parse_float_or_default(
            scenario_contract_report_payload.get("sim_duration_sec"),
            default=0.0,
        )
        if scenario_actor_count <= 0:
            raise ValueError("runtime scenario contract report actor_count must be > 0")
        if scenario_sensor_stream_count <= 0:
            raise ValueError("runtime scenario contract report sensor_stream_count must be > 0")
        if scenario_estimated_scene_frame_count <= 0:
            raise ValueError("runtime scenario contract report estimated_scene_frame_count must be > 0")
        if scenario_executed_step_count <= 0:
            raise ValueError("runtime scenario contract report executed_step_count must be > 0")
        if scenario_sim_duration_sec <= 0.0:
            raise ValueError("runtime scenario contract report sim_duration_sec must be > 0")

        report.update(
            {
                "scenario_contract_checked": True,
                "scenario_contract_out": str(scenario_contract_out_path),
                "scenario_contract_status": scenario_status_report or scenario_status_payload,
                "scenario_runtime_ready": bool(scenario_runtime_ready_report),
                "scenario_actor_count": int(scenario_actor_count),
                "scenario_sensor_stream_count": int(scenario_sensor_stream_count),
                "scenario_estimated_scene_frame_count": int(scenario_estimated_scene_frame_count),
                "scenario_executed_step_count": int(scenario_executed_step_count),
                "scenario_sim_duration_sec": float(scenario_sim_duration_sec),
            }
        )
    if not scene_result_enabled:
        report["scene_result_checked"] = False
    else:
        scene_result_payload = phase3_hooks.get("sim_runtime_scene_result")
        if not isinstance(scene_result_payload, dict) or not bool(scene_result_payload.get("enabled")):
            raise ValueError("pipeline manifest phase3_hooks.sim_runtime_scene_result.enabled must be true")
        scene_result_runtime = str(scene_result_payload.get("runtime", "")).strip().lower()
        if scene_result_runtime and scene_result_runtime != runtime:
            raise ValueError(
                "pipeline manifest sim_runtime_scene_result.runtime mismatch: "
                f"expected {runtime}, got {scene_result_runtime}"
            )
        scene_result_out_text = str(scene_result_payload.get("out", "")).strip()
        if not scene_result_out_text:
            raise ValueError("pipeline manifest sim_runtime_scene_result.out must be a non-empty string")
        scene_result_out_path = Path(scene_result_out_text).resolve()
        scene_result_report_payload = _load_report_object(
            scene_result_out_path,
            subject="runtime scene result report",
        )
        _ensure_schema_version(
            scene_result_report_payload,
            field="runtime_scene_result_schema_version",
            expected=RUNTIME_SCENE_RESULT_SCHEMA_VERSION_V0,
            subject="runtime scene result report",
        )
        scene_result_report_runtime = str(scene_result_report_payload.get("runtime", "")).strip().lower()
        if scene_result_report_runtime and scene_result_report_runtime != runtime:
            raise ValueError(
                f"runtime scene result report runtime mismatch: expected {runtime}, got {scene_result_report_runtime}"
            )
        scene_result_runtime_ready_report = bool(scene_result_report_payload.get("runtime_ready", False))
        scene_result_runtime_ready_payload = bool(scene_result_payload.get("runtime_ready", False))
        if scene_result_runtime_ready_payload != scene_result_runtime_ready_report:
            raise ValueError(
                "pipeline manifest sim_runtime_scene_result.runtime_ready must match runtime scene result report"
            )
        scene_result_status_payload = str(scene_result_payload.get("scene_result_status", "")).strip()
        scene_result_status_report = str(scene_result_report_payload.get("scene_result_status", "")).strip()
        if (
            scene_result_status_payload
            and scene_result_status_report
            and scene_result_status_payload != scene_result_status_report
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_scene_result.scene_result_status "
                "must match runtime scene result report"
            )
        if scene_result_require_runtime_ready and not scene_result_runtime_ready_report:
            raise ValueError("pipeline manifest sim_runtime_scene_result.runtime_ready must be true")
        if (
            scene_result_require_runtime_ready
            and scene_result_status_report
            and scene_result_status_report.lower() != "pass"
        ):
            raise ValueError(
                "runtime scene result report scene_result_status must be pass when runtime-ready is required"
            )
        scene_result_report_scenario_contract_path = str(
            scene_result_report_payload.get("scenario_contract_report_path", "")
        ).strip()
        if scenario_contract_out_path is not None and scene_result_report_scenario_contract_path:
            if Path(scene_result_report_scenario_contract_path).resolve() != scenario_contract_out_path:
                raise ValueError(
                    "runtime scene result report scenario_contract_report_path must match runtime scenario contract report"
                )
        scene_result_actor_count = _parse_int_or_default(scene_result_report_payload.get("actor_count"), default=0)
        scene_result_sensor_stream_count = _parse_int_or_default(
            scene_result_report_payload.get("sensor_stream_count"),
            default=0,
        )
        scene_result_estimated_scene_frame_count = _parse_int_or_default(
            scene_result_report_payload.get("estimated_scene_frame_count"),
            default=0,
        )
        scene_result_executed_step_count = _parse_int_or_default(
            scene_result_report_payload.get("executed_step_count"),
            default=0,
        )
        scene_result_sim_duration_sec = _parse_float_or_default(
            scene_result_report_payload.get("sim_duration_sec"),
            default=0.0,
        )
        scene_result_coverage_ratio = _parse_float_or_default(
            scene_result_report_payload.get("coverage_ratio"),
            default=0.0,
        )
        scene_result_ego_travel_distance_m = _parse_float_or_default(
            scene_result_report_payload.get("ego_travel_distance_m"),
            default=0.0,
        )
        if scene_result_actor_count <= 0:
            raise ValueError("runtime scene result report actor_count must be > 0")
        if scene_result_sensor_stream_count <= 0:
            raise ValueError("runtime scene result report sensor_stream_count must be > 0")
        if scene_result_estimated_scene_frame_count <= 0:
            raise ValueError("runtime scene result report estimated_scene_frame_count must be > 0")
        if scene_result_executed_step_count <= 0:
            raise ValueError("runtime scene result report executed_step_count must be > 0")
        if scene_result_sim_duration_sec <= 0.0:
            raise ValueError("runtime scene result report sim_duration_sec must be > 0")
        if scene_result_coverage_ratio <= 0.0:
            raise ValueError("runtime scene result report coverage_ratio must be > 0")
        if scene_result_coverage_ratio > 1.0:
            raise ValueError("runtime scene result report coverage_ratio must be <= 1")
        if scene_result_ego_travel_distance_m < 0.0:
            raise ValueError("runtime scene result report ego_travel_distance_m must be >= 0")
        report.update(
            {
                "scene_result_checked": True,
                "scene_result_out": str(scene_result_out_path),
                "scene_result_status": scene_result_status_report or scene_result_status_payload,
                "scene_result_runtime_ready": bool(scene_result_runtime_ready_report),
                "scene_result_actor_count": int(scene_result_actor_count),
                "scene_result_sensor_stream_count": int(scene_result_sensor_stream_count),
                "scene_result_estimated_scene_frame_count": int(scene_result_estimated_scene_frame_count),
                "scene_result_executed_step_count": int(scene_result_executed_step_count),
                "scene_result_sim_duration_sec": float(scene_result_sim_duration_sec),
                "scene_result_coverage_ratio": float(scene_result_coverage_ratio),
                "scene_result_ego_travel_distance_m": float(scene_result_ego_travel_distance_m),
            }
        )

    if not interop_contract_enabled:
        report["interop_contract_checked"] = False
        return report

    interop_contract_payload = phase3_hooks.get("sim_runtime_interop_contract")
    if not isinstance(interop_contract_payload, dict) or not bool(interop_contract_payload.get("enabled")):
        raise ValueError("pipeline manifest phase3_hooks.sim_runtime_interop_contract.enabled must be true")
    interop_contract_runtime = str(interop_contract_payload.get("runtime", "")).strip().lower()
    if interop_contract_runtime and interop_contract_runtime != runtime:
        raise ValueError(
            "pipeline manifest sim_runtime_interop_contract.runtime mismatch: "
            f"expected {runtime}, got {interop_contract_runtime}"
        )
    interop_contract_out_text = str(interop_contract_payload.get("out", "")).strip()
    if not interop_contract_out_text:
        raise ValueError("pipeline manifest sim_runtime_interop_contract.out must be a non-empty string")
    interop_contract_out_path = Path(interop_contract_out_text).resolve()
    interop_contract_report_payload = _load_report_object(
        interop_contract_out_path,
        subject="runtime interop contract report",
    )
    _ensure_schema_version(
        interop_contract_report_payload,
        field="sim_runtime_interop_contract_schema_version",
        expected=SIM_RUNTIME_INTEROP_CONTRACT_SCHEMA_VERSION_V0,
        subject="runtime interop contract report",
    )
    interop_report_runtime = str(interop_contract_report_payload.get("runtime", "")).strip().lower()
    if interop_report_runtime and interop_report_runtime != runtime:
        raise ValueError(
            f"runtime interop contract report runtime mismatch: expected {runtime}, got {interop_report_runtime}"
        )
    interop_runtime_ready_report = bool(interop_contract_report_payload.get("runtime_ready", False))
    interop_runtime_ready_payload = bool(interop_contract_payload.get("runtime_ready", False))
    if interop_runtime_ready_payload != interop_runtime_ready_report:
        raise ValueError(
            "pipeline manifest sim_runtime_interop_contract.runtime_ready must match runtime interop contract report"
        )
    interop_status_payload = str(interop_contract_payload.get("interop_contract_status", "")).strip()
    interop_status_report = str(interop_contract_report_payload.get("interop_contract_status", "")).strip()
    if interop_status_payload and interop_status_report and interop_status_payload != interop_status_report:
        raise ValueError(
            "pipeline manifest sim_runtime_interop_contract.interop_contract_status "
            "must match runtime interop contract report"
        )
    if interop_contract_require_runtime_ready and not interop_runtime_ready_report:
        raise ValueError("pipeline manifest sim_runtime_interop_contract.runtime_ready must be true")
    if interop_contract_require_runtime_ready and interop_status_report and interop_status_report.lower() != "pass":
        raise ValueError(
            "runtime interop contract report interop_contract_status must be pass when runtime-ready is required"
        )

    interop_export_out_text = str(interop_contract_payload.get("interop_export_out", "")).strip()
    interop_export_status_payload = str(interop_contract_payload.get("interop_export_status", "")).strip()
    interop_export_xosc_path_payload = str(interop_contract_payload.get("interop_export_xosc_path", "")).strip()
    interop_export_xodr_path_payload = str(interop_contract_payload.get("interop_export_xodr_path", "")).strip()
    interop_export_metadata_present = bool(
        interop_export_out_text
        or interop_export_status_payload
        or interop_export_xosc_path_payload
        or interop_export_xodr_path_payload
    )
    if interop_export_metadata_present and not interop_export_out_text:
        raise ValueError(
            "pipeline manifest sim_runtime_interop_contract.interop_export_out must be non-empty "
            "when interop export metadata is present"
        )
    interop_import_out_text = str(interop_contract_payload.get("interop_import_out", "")).strip()
    interop_import_status_payload = str(interop_contract_payload.get("interop_import_status", "")).strip()
    interop_import_xosc_path_payload = str(interop_contract_payload.get("interop_import_xosc_path", "")).strip()
    interop_import_xodr_path_payload = str(interop_contract_payload.get("interop_import_xodr_path", "")).strip()
    interop_import_manifest_consistent_payload_provided = "interop_import_manifest_consistent" in interop_contract_payload
    interop_import_actor_count_manifest_payload_provided = "interop_import_actor_count_manifest" in interop_contract_payload
    interop_import_xosc_entity_count_payload_provided = "interop_import_xosc_entity_count" in interop_contract_payload
    interop_import_xodr_road_count_payload_provided = "interop_import_xodr_road_count" in interop_contract_payload
    interop_import_xodr_total_road_length_m_payload_provided = (
        "interop_import_xodr_total_road_length_m" in interop_contract_payload
    )
    interop_import_manifest_consistent_payload = (
        bool(interop_contract_payload.get("interop_import_manifest_consistent", False))
        if interop_import_manifest_consistent_payload_provided
        else None
    )
    interop_import_actor_count_manifest_payload = _parse_int_or_default(
        interop_contract_payload.get("interop_import_actor_count_manifest"),
        default=0,
    )
    interop_import_xosc_entity_count_payload = _parse_int_or_default(
        interop_contract_payload.get("interop_import_xosc_entity_count"),
        default=0,
    )
    interop_import_xodr_road_count_payload = _parse_int_or_default(
        interop_contract_payload.get("interop_import_xodr_road_count"),
        default=0,
    )
    interop_import_xodr_total_road_length_m_payload = _parse_float_or_default(
        interop_contract_payload.get("interop_import_xodr_total_road_length_m"),
        default=0.0,
    )
    interop_import_metadata_present = bool(
        interop_import_out_text
        or interop_import_status_payload
        or interop_import_xosc_path_payload
        or interop_import_xodr_path_payload
        or interop_import_manifest_consistent_payload_provided
        or interop_import_actor_count_manifest_payload_provided
        or interop_import_xosc_entity_count_payload_provided
        or interop_import_xodr_road_count_payload_provided
        or interop_import_xodr_total_road_length_m_payload_provided
    )
    if interop_import_metadata_present and not interop_import_out_text:
        raise ValueError(
            "pipeline manifest sim_runtime_interop_contract.interop_import_out must be non-empty "
            "when interop import metadata is present"
        )

    interop_export_checked = False
    interop_export_out_path_text = ""
    interop_export_status = interop_export_status_payload
    interop_export_xosc_path_text = ""
    interop_export_xodr_path_text = ""
    interop_export_actor_count_manifest = 0
    interop_export_sensor_stream_count_manifest = 0
    interop_export_xosc_entity_count = 0
    interop_export_xodr_road_count = 0
    interop_export_generated_road_length_m = 0.0
    interop_import_checked = False
    interop_import_out_path_text = ""
    interop_import_status = interop_import_status_payload
    interop_import_xosc_path_text = ""
    interop_import_xodr_path_text = ""
    interop_import_manifest_consistent: Optional[bool] = None
    interop_import_actor_count_manifest = 0
    interop_import_xosc_entity_count = 0
    interop_import_xodr_road_count = 0
    interop_import_xodr_total_road_length_m = 0.0

    if interop_export_out_text:
        interop_export_out_path = Path(interop_export_out_text).resolve()
        interop_export_report_payload = _load_report_object(
            interop_export_out_path,
            subject="runtime interop export report",
        )
        _ensure_schema_version(
            interop_export_report_payload,
            field="sim_runtime_interop_export_schema_version",
            expected=SIM_RUNTIME_INTEROP_EXPORT_SCHEMA_VERSION_V0,
            subject="runtime interop export report",
        )
        interop_export_runtime = str(interop_export_report_payload.get("runtime", "")).strip().lower()
        if interop_export_runtime and interop_export_runtime != runtime:
            raise ValueError(
                f"runtime interop export report runtime mismatch: expected {runtime}, got {interop_export_runtime}"
            )
        interop_export_status_report = str(interop_export_report_payload.get("export_status", "")).strip()
        if not interop_export_status_report:
            raise ValueError("runtime interop export report export_status must be non-empty")
        if (
            interop_export_status_payload
            and interop_export_status_report
            and interop_export_status_payload != interop_export_status_report
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_interop_contract.interop_export_status "
                "must match runtime interop export report"
            )
        if interop_contract_require_runtime_ready and interop_export_status_report.lower() != "pass":
            raise ValueError(
                "runtime interop export report export_status must be pass when runtime-ready is required"
            )

        interop_export_launch_manifest_path = str(
            interop_export_report_payload.get("launch_manifest_path", "")
        ).strip()
        if interop_export_launch_manifest_path:
            if Path(interop_export_launch_manifest_path).resolve() != launch_manifest_out_path:
                raise ValueError(
                    "runtime interop export report launch_manifest_path must match runtime launch manifest"
                )

        interop_export_xosc_path_report = str(interop_export_report_payload.get("xosc_path", "")).strip()
        interop_export_xodr_path_report = str(interop_export_report_payload.get("xodr_path", "")).strip()
        if not interop_export_xosc_path_report:
            raise ValueError("runtime interop export report xosc_path must be non-empty")
        if not interop_export_xodr_path_report:
            raise ValueError("runtime interop export report xodr_path must be non-empty")
        interop_export_xosc_path_resolved = Path(interop_export_xosc_path_report).resolve()
        interop_export_xodr_path_resolved = Path(interop_export_xodr_path_report).resolve()
        if not interop_export_xosc_path_resolved.exists():
            raise ValueError(
                "runtime interop export report xosc_path must point to an existing path: "
                f"{interop_export_xosc_path_resolved}"
            )
        if not interop_export_xodr_path_resolved.exists():
            raise ValueError(
                "runtime interop export report xodr_path must point to an existing path: "
                f"{interop_export_xodr_path_resolved}"
            )
        if interop_export_xosc_path_payload:
            if Path(interop_export_xosc_path_payload).resolve() != interop_export_xosc_path_resolved:
                raise ValueError(
                    "pipeline manifest sim_runtime_interop_contract.interop_export_xosc_path "
                    "must match runtime interop export report"
                )
        if interop_export_xodr_path_payload:
            if Path(interop_export_xodr_path_payload).resolve() != interop_export_xodr_path_resolved:
                raise ValueError(
                    "pipeline manifest sim_runtime_interop_contract.interop_export_xodr_path "
                    "must match runtime interop export report"
                )

        interop_export_actor_count_manifest = _parse_int_or_default(
            interop_export_report_payload.get("actor_count_manifest"),
            default=0,
        )
        interop_export_sensor_stream_count_manifest = _parse_int_or_default(
            interop_export_report_payload.get("sensor_stream_count_manifest"),
            default=0,
        )
        interop_export_xosc_entity_count = _parse_int_or_default(
            interop_export_report_payload.get("xosc_entity_count"),
            default=0,
        )
        interop_export_xodr_road_count = _parse_int_or_default(
            interop_export_report_payload.get("xodr_road_count"),
            default=0,
        )
        interop_export_generated_road_length_m = _parse_float_or_default(
            interop_export_report_payload.get("generated_road_length_m"),
            default=0.0,
        )
        if interop_export_actor_count_manifest <= 0:
            raise ValueError("runtime interop export report actor_count_manifest must be > 0")
        if interop_export_sensor_stream_count_manifest <= 0:
            raise ValueError("runtime interop export report sensor_stream_count_manifest must be > 0")
        if interop_export_xosc_entity_count <= 0:
            raise ValueError("runtime interop export report xosc_entity_count must be > 0")
        if interop_export_xodr_road_count <= 0:
            raise ValueError("runtime interop export report xodr_road_count must be > 0")
        if interop_export_generated_road_length_m <= 0.0:
            raise ValueError("runtime interop export report generated_road_length_m must be > 0")

        interop_export_checked = True
        interop_export_out_path_text = str(interop_export_out_path)
        interop_export_status = interop_export_status_report or interop_export_status_payload
        interop_export_xosc_path_text = str(interop_export_xosc_path_resolved)
        interop_export_xodr_path_text = str(interop_export_xodr_path_resolved)
    if interop_import_out_text:
        interop_import_out_path = Path(interop_import_out_text).resolve()
        interop_import_report_payload = _load_report_object(
            interop_import_out_path,
            subject="runtime interop import report",
        )
        _ensure_schema_version(
            interop_import_report_payload,
            field="sim_runtime_interop_import_schema_version",
            expected=SIM_RUNTIME_INTEROP_IMPORT_SCHEMA_VERSION_V0,
            subject="runtime interop import report",
        )
        interop_import_runtime = str(interop_import_report_payload.get("runtime", "")).strip().lower()
        if interop_import_runtime and interop_import_runtime != runtime:
            raise ValueError(
                f"runtime interop import report runtime mismatch: expected {runtime}, got {interop_import_runtime}"
            )
        interop_import_status_report = str(interop_import_report_payload.get("import_status", "")).strip()
        if not interop_import_status_report:
            raise ValueError("runtime interop import report import_status must be non-empty")
        if (
            interop_import_status_payload
            and interop_import_status_report
            and interop_import_status_payload != interop_import_status_report
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_interop_contract.interop_import_status "
                "must match runtime interop import report"
            )
        if interop_contract_require_runtime_ready and interop_import_status_report.lower() != "pass":
            raise ValueError(
                "runtime interop import report import_status must be pass when runtime-ready is required"
            )

        interop_import_launch_manifest_path = str(
            interop_import_report_payload.get("launch_manifest_path", "")
        ).strip()
        if interop_import_launch_manifest_path:
            if Path(interop_import_launch_manifest_path).resolve() != launch_manifest_out_path:
                raise ValueError(
                    "runtime interop import report launch_manifest_path must match runtime launch manifest"
                )

        interop_import_xosc_path_report = str(interop_import_report_payload.get("xosc_path", "")).strip()
        interop_import_xodr_path_report = str(interop_import_report_payload.get("xodr_path", "")).strip()
        if not interop_import_xosc_path_report:
            raise ValueError("runtime interop import report xosc_path must be non-empty")
        if not interop_import_xodr_path_report:
            raise ValueError("runtime interop import report xodr_path must be non-empty")
        interop_import_xosc_path_resolved = Path(interop_import_xosc_path_report).resolve()
        interop_import_xodr_path_resolved = Path(interop_import_xodr_path_report).resolve()
        if not interop_import_xosc_path_resolved.exists():
            raise ValueError(
                "runtime interop import report xosc_path must point to an existing path: "
                f"{interop_import_xosc_path_resolved}"
            )
        if not interop_import_xodr_path_resolved.exists():
            raise ValueError(
                "runtime interop import report xodr_path must point to an existing path: "
                f"{interop_import_xodr_path_resolved}"
            )
        if interop_import_xosc_path_payload:
            if Path(interop_import_xosc_path_payload).resolve() != interop_import_xosc_path_resolved:
                raise ValueError(
                    "pipeline manifest sim_runtime_interop_contract.interop_import_xosc_path "
                    "must match runtime interop import report"
                )
        if interop_import_xodr_path_payload:
            if Path(interop_import_xodr_path_payload).resolve() != interop_import_xodr_path_resolved:
                raise ValueError(
                    "pipeline manifest sim_runtime_interop_contract.interop_import_xodr_path "
                    "must match runtime interop import report"
                )

        interop_import_manifest_consistent_report = bool(
            interop_import_report_payload.get("manifest_consistent", False)
        )
        if (
            interop_import_manifest_consistent_payload is not None
            and interop_import_manifest_consistent_payload != interop_import_manifest_consistent_report
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_interop_contract.interop_import_manifest_consistent "
                "must match runtime interop import report"
            )
        interop_import_actor_count_manifest = _parse_int_or_default(
            interop_import_report_payload.get("actor_count_manifest"),
            default=0,
        )
        interop_import_xosc_entity_count = _parse_int_or_default(
            interop_import_report_payload.get("xosc_entity_count"),
            default=0,
        )
        interop_import_xodr_road_count = _parse_int_or_default(
            interop_import_report_payload.get("xodr_road_count"),
            default=0,
        )
        interop_import_xodr_total_road_length_m = _parse_float_or_default(
            interop_import_report_payload.get("xodr_total_road_length_m"),
            default=0.0,
        )
        if interop_import_actor_count_manifest <= 0:
            raise ValueError("runtime interop import report actor_count_manifest must be > 0")
        if interop_import_xosc_entity_count <= 0:
            raise ValueError("runtime interop import report xosc_entity_count must be > 0")
        if interop_import_xodr_road_count <= 0:
            raise ValueError("runtime interop import report xodr_road_count must be > 0")
        if interop_import_xodr_total_road_length_m <= 0.0:
            raise ValueError("runtime interop import report xodr_total_road_length_m must be > 0")
        if (
            interop_import_actor_count_manifest_payload_provided
            and interop_import_actor_count_manifest_payload != interop_import_actor_count_manifest
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_interop_contract.interop_import_actor_count_manifest "
                "must match runtime interop import report"
            )
        if (
            interop_import_xosc_entity_count_payload_provided
            and interop_import_xosc_entity_count_payload != interop_import_xosc_entity_count
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_interop_contract.interop_import_xosc_entity_count "
                "must match runtime interop import report"
            )
        if (
            interop_import_xodr_road_count_payload_provided
            and interop_import_xodr_road_count_payload != interop_import_xodr_road_count
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_interop_contract.interop_import_xodr_road_count "
                "must match runtime interop import report"
            )
        if (
            interop_import_xodr_total_road_length_m_payload_provided
            and abs(interop_import_xodr_total_road_length_m_payload - interop_import_xodr_total_road_length_m) > 1e-6
        ):
            raise ValueError(
                "pipeline manifest sim_runtime_interop_contract.interop_import_xodr_total_road_length_m "
                "must match runtime interop import report"
            )

        interop_import_checked = True
        interop_import_out_path_text = str(interop_import_out_path)
        interop_import_status = interop_import_status_report or interop_import_status_payload
        interop_import_xosc_path_text = str(interop_import_xosc_path_resolved)
        interop_import_xodr_path_text = str(interop_import_xodr_path_resolved)
        interop_import_manifest_consistent = bool(interop_import_manifest_consistent_report)

    interop_imported_actor_count = _parse_int_or_default(
        interop_contract_report_payload.get("imported_actor_count"),
        default=0,
    )
    interop_xosc_entity_count = _parse_int_or_default(
        interop_contract_report_payload.get("xosc_entity_count"),
        default=0,
    )
    interop_xodr_road_count = _parse_int_or_default(
        interop_contract_report_payload.get("xodr_road_count"),
        default=0,
    )
    interop_executed_step_count = _parse_int_or_default(
        interop_contract_report_payload.get("executed_step_count"),
        default=0,
    )
    interop_sim_duration_sec = _parse_float_or_default(
        interop_contract_report_payload.get("sim_duration_sec"),
        default=0.0,
    )
    if interop_imported_actor_count <= 0:
        raise ValueError("runtime interop contract report imported_actor_count must be > 0")
    if interop_xosc_entity_count <= 0:
        raise ValueError("runtime interop contract report xosc_entity_count must be > 0")
    if interop_xodr_road_count <= 0:
        raise ValueError("runtime interop contract report xodr_road_count must be > 0")
    if interop_executed_step_count <= 0:
        raise ValueError("runtime interop contract report executed_step_count must be > 0")
    if interop_sim_duration_sec <= 0.0:
        raise ValueError("runtime interop contract report sim_duration_sec must be > 0")

    report["interop_export_checked"] = bool(interop_export_checked)
    if interop_export_checked:
        report.update(
            {
                "interop_export_out": interop_export_out_path_text,
                "interop_export_status": interop_export_status,
                "interop_export_xosc_path": interop_export_xosc_path_text,
                "interop_export_xodr_path": interop_export_xodr_path_text,
                "interop_export_actor_count_manifest": int(interop_export_actor_count_manifest),
                "interop_export_sensor_stream_count_manifest": int(interop_export_sensor_stream_count_manifest),
                "interop_export_xosc_entity_count": int(interop_export_xosc_entity_count),
                "interop_export_xodr_road_count": int(interop_export_xodr_road_count),
                "interop_export_generated_road_length_m": float(interop_export_generated_road_length_m),
            }
        )
    report["interop_import_checked"] = bool(interop_import_checked)
    if interop_import_checked and interop_import_manifest_consistent is not None:
        report.update(
            {
                "interop_import_out": interop_import_out_path_text,
                "interop_import_status": interop_import_status,
                "interop_import_xosc_path": interop_import_xosc_path_text,
                "interop_import_xodr_path": interop_import_xodr_path_text,
                "interop_import_manifest_consistent": bool(interop_import_manifest_consistent),
                "interop_import_actor_count_manifest": int(interop_import_actor_count_manifest),
                "interop_import_xosc_entity_count": int(interop_import_xosc_entity_count),
                "interop_import_xodr_road_count": int(interop_import_xodr_road_count),
                "interop_import_xodr_total_road_length_m": float(interop_import_xodr_total_road_length_m),
            }
        )

    report.update(
        {
            "interop_contract_checked": True,
            "interop_contract_out": str(interop_contract_out_path),
            "interop_contract_status": interop_status_report or interop_status_payload,
            "interop_runtime_ready": bool(interop_runtime_ready_report),
            "interop_imported_actor_count": int(interop_imported_actor_count),
            "interop_xosc_entity_count": int(interop_xosc_entity_count),
            "interop_xodr_road_count": int(interop_xodr_road_count),
            "interop_executed_step_count": int(interop_executed_step_count),
            "interop_sim_duration_sec": float(interop_sim_duration_sec),
        }
    )
    return report


def main() -> int:
    args = parse_args()
    max_failures = parse_non_negative_int(str(args.max_failures), default=0, field="max-failures")
    validate_trend_inputs(args)
    validate_functional_quality_gate_inputs(args)
    validate_phase3_runtime_inputs(args)

    selected_profile_ids = resolve_selected_profile_ids(args)
    profiles = load_matrix_profiles(
        python_bin=str(args.python_bin),
        profile_loader=str(args.profile_loader),
        profiles_file=str(args.profiles_file),
        selected_profile_ids=selected_profile_ids,
    )

    release_prefix = str(args.release_prefix).strip()
    if not release_prefix:
        release_prefix = f"REL_MATRIX_{utc_now_compact()}"

    results: list[dict[str, Any]] = []
    runtime_evidence_records: list[dict[str, Any]] = []
    failures = 0
    first_failure: Optional[RuntimeError] = None

    for profile in profiles:
        profile_id = profile["profile_id"]
        release_id = f"{release_prefix}_{safe_token(profile_id)}"
        cmd = build_profile_cmd(args=args, profile=profile, release_id=release_id)
        print(f"[cmd] {shell_join(cmd)}")
        proc = run_capture(cmd)
        emit_captured_output(proc)

        profile_sim_runtime = str(profile.get("sim_runtime", args.sim_runtime)).strip().lower() or "none"
        profile_sim_runtime_scene = str(profile.get("sim_runtime_scene", args.sim_runtime_scene)).strip()
        profile_sim_runtime_sensor_rig = str(
            profile.get("sim_runtime_sensor_rig", args.sim_runtime_sensor_rig)
        ).strip()
        profile_sim_runtime_mode = str(profile.get("sim_runtime_mode", args.sim_runtime_mode)).strip().lower()

        result: dict[str, Any] = {
            "profile_id": profile_id,
            "release_id": release_id,
            "default_batch_spec": profile["default_batch_spec"],
            "default_sds_versions": profile["default_sds_versions"],
            "sim_runtime": profile_sim_runtime,
            "sim_runtime_scene": profile_sim_runtime_scene,
            "sim_runtime_sensor_rig": profile_sim_runtime_sensor_rig,
            "sim_runtime_mode": profile_sim_runtime_mode,
            "returncode": int(proc.returncode),
            "status": "ok" if proc.returncode == 0 else "failed",
        }

        if proc.returncode != 0:
            failures += 1
            failure_message = compact_failure_detail(str(proc.stderr) or str(proc.stdout))
            result["error"] = failure_message
            print(
                f"[warn] matrix_profile_failed profile_id={profile_id} release_id={release_id} returncode={proc.returncode}",
                file=sys.stderr,
            )
            results.append(result)

            if not args.continue_on_error:
                first_failure = RuntimeError(
                    format_subprocess_failure(
                        cmd,
                        returncode=int(proc.returncode),
                        stdout=proc.stdout,
                        stderr=proc.stderr,
                        context=f"matrix profile {profile_id}",
                    )
                )
                break
            if max_failures > 0 and failures >= max_failures:
                print(
                    "[warn] max_failures_reached failures={failures} limit={limit}; stopping matrix run".format(
                        failures=failures,
                        limit=max_failures,
                    ),
                    file=sys.stderr,
                )
                break
            continue

        if bool(args.sim_runtime_assert_artifacts) and not bool(args.dry_run):
            manifest_path_text = _extract_ci_manifest_path(str(proc.stdout))
            try:
                if not manifest_path_text:
                    raise ValueError("missing [ok] ci_manifest_path output from run_ci_pipeline.py")
                runtime_artifact_report = validate_runtime_artifacts(
                    manifest_path=Path(manifest_path_text).resolve(),
                    runtime=str(args.sim_runtime).strip().lower(),
                    probe_enabled=bool(args.sim_runtime_probe_enable),
                    probe_execute=bool(args.sim_runtime_probe_execute),
                    probe_require_availability=bool(args.sim_runtime_probe_require_availability),
                    scenario_contract_enabled=bool(args.sim_runtime_scenario_contract_enable),
                    scenario_contract_require_runtime_ready=bool(
                        args.sim_runtime_scenario_contract_require_runtime_ready
                    ),
                    scene_result_enabled=bool(args.sim_runtime_scene_result_enable),
                    scene_result_require_runtime_ready=bool(
                        args.sim_runtime_scene_result_require_runtime_ready
                    ),
                    interop_contract_enabled=bool(args.sim_runtime_interop_contract_enable),
                    interop_contract_require_runtime_ready=bool(
                        args.sim_runtime_interop_contract_require_runtime_ready
                    ),
                )
                result["runtime_artifacts"] = runtime_artifact_report
                runtime_evidence_records.append(
                    {
                        "profile_id": profile_id,
                        "release_id": release_id,
                        "status": "validated",
                        "runtime_artifacts": runtime_artifact_report,
                    }
                )
                print(
                    "[ok] runtime_artifacts_validated profile_id={profile_id} manifest={manifest}".format(
                        profile_id=profile_id,
                        manifest=runtime_artifact_report["manifest_path"],
                    )
                )
            except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
                failures += 1
                result["status"] = "failed"
                result["returncode"] = 2
                result["error"] = compact_failure_detail(str(exc))
                runtime_evidence_records.append(
                    {
                        "profile_id": profile_id,
                        "release_id": release_id,
                        "status": "failed",
                        "error": compact_failure_detail(str(exc)),
                    }
                )
                print(
                    "[warn] matrix_profile_failed profile_id={profile_id} release_id={release_id} "
                    "returncode=2 reason=runtime_artifact_assertion".format(
                        profile_id=profile_id,
                        release_id=release_id,
                    ),
                    file=sys.stderr,
                )
                results.append(result)

                if not args.continue_on_error:
                    first_failure = RuntimeError(
                        "runtime artifact assertion failed for profile "
                        f"{profile_id}: {exc}"
                    )
                    break
                if max_failures > 0 and failures >= max_failures:
                    print(
                        "[warn] max_failures_reached failures={failures} limit={limit}; stopping matrix run".format(
                            failures=failures,
                            limit=max_failures,
                        ),
                        file=sys.stderr,
                    )
                    break
                continue

        print(f"[ok] matrix_profile={profile_id} release_id={release_id}")
        results.append(result)

    summary_out = str(args.summary_out).strip()
    if summary_out:
        summary_path = Path(summary_out).resolve()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": utc_now_iso(),
            "dry_run": bool(args.dry_run),
            "release_prefix": release_prefix,
            "sim_runtime": str(args.sim_runtime).strip().lower() or "none",
            "sim_runtime_assert_artifacts": bool(args.sim_runtime_assert_artifacts),
            "profile_count": len(results),
            "failure_count": failures,
            "max_failures": max_failures,
            "results": results,
        }
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] summary_out={summary_path}")

    runtime_evidence_out = str(args.runtime_evidence_out).strip()
    sim_runtime_probe_flag = str(args.sim_runtime_probe_flag).strip()
    sim_runtime_probe_args_shlex = str(args.sim_runtime_probe_args_shlex).strip()
    if runtime_evidence_out:
        runtime_evidence_path = Path(runtime_evidence_out).resolve()
        runtime_evidence_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_evidence_payload = {
            "generated_at": utc_now_iso(),
            "dry_run": bool(args.dry_run),
            "release_prefix": release_prefix,
            "sim_runtime": str(args.sim_runtime).strip().lower() or "none",
            "sim_runtime_assert_artifacts": bool(args.sim_runtime_assert_artifacts),
            "sim_runtime_probe_enable": bool(args.sim_runtime_probe_enable),
            "sim_runtime_probe_execute": bool(args.sim_runtime_probe_execute),
            "sim_runtime_probe_require_availability": bool(args.sim_runtime_probe_require_availability),
            "sim_runtime_probe_flag": sim_runtime_probe_flag,
            "sim_runtime_probe_args_shlex": sim_runtime_probe_args_shlex,
            "sim_runtime_scenario_contract_enable": bool(args.sim_runtime_scenario_contract_enable),
            "sim_runtime_scenario_contract_require_runtime_ready": bool(
                args.sim_runtime_scenario_contract_require_runtime_ready
            ),
            "sim_runtime_scene_result_enable": bool(args.sim_runtime_scene_result_enable),
            "sim_runtime_scene_result_require_runtime_ready": bool(
                args.sim_runtime_scene_result_require_runtime_ready
            ),
            "sim_runtime_interop_contract_enable": bool(args.sim_runtime_interop_contract_enable),
            "sim_runtime_interop_contract_require_runtime_ready": bool(
                args.sim_runtime_interop_contract_require_runtime_ready
            ),
            "profile_count": len(results),
            "failure_count": failures,
            "runtime_evidence_count": len(runtime_evidence_records),
            "runtime_evidence_records": runtime_evidence_records,
        }
        runtime_evidence_path.write_text(
            json.dumps(runtime_evidence_payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        print(f"[ok] runtime_evidence_out={runtime_evidence_path}")

    if first_failure is not None:
        raise first_failure

    if failures > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="run_ci_matrix_pipeline.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
