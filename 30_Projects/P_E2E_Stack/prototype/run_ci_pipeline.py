#!/usr/bin/env python3
"""Normalize CI inputs and invoke run_e2e_pipeline.py with consistent behavior."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from ci_commands import shell_join
from ci_input_parsing import (
    parse_bool,
    parse_csv_items_with_fallback,
    parse_float,
    parse_int,
    parse_non_negative_float,
    parse_positive_int,
    resolve_phase4_copilot_mode,
)
from ci_phases import PHASE_RESOLVE_INPUTS, PIPELINE_PHASE_RUN_PIPELINE
from ci_reporting import emit_ci_error, normalize_exception_message
from ci_release import resolve_release_value
from ci_script_entry import resolve_github_output_file_from_env, resolve_step_summary_file_from_env
from ci_subprocess import compact_failure_detail, run_capture_stdout_or_raise
from ci_sync_utils import resolve_repo_root
from phase4_linkage_contract import (
    PHASE4_LINKAGE_ALLOWED_MODULES_TEXT,
    PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT,
    resolve_phase4_linkage_modules as resolve_phase4_linkage_modules_contract,
    resolve_phase4_reference_pattern_modules as resolve_phase4_reference_pattern_modules_contract,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run E2E pipeline with normalized CI inputs")
    parser.add_argument("--batch-spec", default="", help="Batch spec path (optional)")
    parser.add_argument("--fallback-batch-spec", default="", help="Fallback batch spec path when batch-spec is empty")
    parser.add_argument("--profile-file", default="", help="Profile JSON path for optional batch/spec defaults")
    parser.add_argument("--profile-id", default="", help="Profile ID filter when profile-file is used")
    parser.add_argument(
        "--profile-default-file",
        default="",
        help="Default profile JSON path when profile-id is given without profile-file",
    )
    parser.add_argument(
        "--profile-loader",
        default=str(Path(__file__).resolve().with_name("load_ci_matrix.py")),
        help="load_ci_matrix.py path for profile resolution",
    )
    parser.add_argument("--release-id", default="", help="Release ID")
    parser.add_argument("--release-id-input", default="", help="Release ID input override")
    parser.add_argument("--release-id-fallback-prefix", default="", help="Fallback release ID prefix")
    parser.add_argument("--release-id-fallback-run-id", default="", help="Fallback run ID token")
    parser.add_argument("--release-id-fallback-run-attempt", default="", help="Fallback run attempt token")
    parser.add_argument("--batch-out", default="", help="Optional Cloud batch output root override")
    parser.add_argument("--db", default="", help="Optional SQLite DB path")
    parser.add_argument("--report-dir", default="", help="Optional report output directory")
    parser.add_argument("--cloud-runner", default="", help="Optional cloud_batch_runner.py path")
    parser.add_argument("--ingest-runner", default="", help="Optional ingest_scenario_runs.py path")
    parser.add_argument("--report-runner", default="", help="Optional generate_release_report.py path")
    parser.add_argument("--gate-profile", default="", help="Gate profile path (optional)")
    parser.add_argument("--requirement-map", default="", help="Requirement map path (optional)")
    parser.add_argument("--strict-gate-input", default="", help="Strict gate input (true/false, optional)")
    parser.add_argument(
        "--strict-gate-default",
        choices=["true", "false"],
        default="false",
        help="Strict gate default when strict-gate-input is empty",
    )
    parser.add_argument(
        "--phase2-enable-hooks-input",
        default="",
        help="Phase-2 hooks enable input (true/false, optional)",
    )
    parser.add_argument(
        "--phase2-enable-hooks-default",
        choices=["true", "false"],
        default="false",
        help="Phase-2 hooks default when input is empty",
    )
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
        help="Runtime OpenSCENARIO/OpenDRIVE interop-contract hook enable input (true/false, optional)",
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
        "--phase4-enable-hooks-input",
        default="",
        help="Phase-4 hooks enable input (true/false, optional)",
    )
    parser.add_argument(
        "--phase4-enable-hooks-default",
        choices=["true", "false"],
        default="false",
        help="Phase-4 hooks default when input is empty",
    )
    parser.add_argument(
        "--phase4-enable-copilot-hooks-input",
        default="",
        help="Phase-4 Copilot hooks enable input (true/false, optional)",
    )
    parser.add_argument(
        "--phase4-enable-copilot-hooks-default",
        choices=["true", "false"],
        default="false",
        help="Phase-4 Copilot hooks default when input is empty",
    )
    parser.add_argument(
        "--phase4-require-done-input",
        default="",
        help="Require Phase-4 done-state input (true/false, optional)",
    )
    parser.add_argument(
        "--phase4-require-done-default",
        choices=["true", "false"],
        default="false",
        help="Require Phase-4 done-state default when input is empty",
    )
    parser.add_argument("--sensor-bridge-runner", default="", help="Optional sensor_sim_bridge.py path")
    parser.add_argument("--sensor-bridge-world-state", default="", help="Optional world-state path for sensor bridge")
    parser.add_argument("--sensor-bridge-rig", default="", help="Optional sensor rig path for sensor bridge")
    parser.add_argument("--sensor-bridge-out", default="", help="Optional sensor frame output path")
    parser.add_argument(
        "--sensor-bridge-fidelity-tier",
        default="",
        help="Optional sensor bridge fidelity tier override (contract|basic|high)",
    )
    parser.add_argument("--sensor-sweep-runner", default="", help="Optional sensor_rig_sweep.py path")
    parser.add_argument("--sensor-sweep-candidates", default="", help="Optional rig sweep candidates path")
    parser.add_argument("--sensor-sweep-out", default="", help="Optional rig sweep output path")
    parser.add_argument("--log-replay-runner", default="", help="Optional log_replay_runner.py path")
    parser.add_argument("--log-scene", default="", help="Optional log scene path for replay hook")
    parser.add_argument("--log-replay-out-root", default="", help="Optional log replay output root")
    parser.add_argument("--map-convert-runner", default="", help="Optional convert_map_format.py path")
    parser.add_argument("--map-validate-runner", default="", help="Optional validate_canonical_map.py path")
    parser.add_argument("--map-simple", default="", help="Optional simple map path for conversion hook")
    parser.add_argument("--map-canonical-out", default="", help="Optional canonical map output path")
    parser.add_argument(
        "--map-validate-report-out",
        default="",
        help="Optional map validation report output path",
    )
    parser.add_argument("--map-route-runner", default="", help="Optional compute_canonical_route.py path")
    parser.add_argument(
        "--map-route-report-out",
        default="",
        help="Optional map route report output path",
    )
    parser.add_argument(
        "--map-route-cost-mode",
        default="",
        help="Optional map route cost mode override (hops|length)",
    )
    parser.add_argument(
        "--map-route-entry-lane-id",
        default="",
        help="Optional map route entry lane id override",
    )
    parser.add_argument(
        "--map-route-exit-lane-id",
        default="",
        help="Optional map route exit lane id override",
    )
    parser.add_argument(
        "--map-route-via-lane-id",
        action="append",
        default=[],
        help="Optional map route via lane id override (repeatable)",
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
        "--dataset-manifest-runner",
        default="",
        help="Optional build_dataset_manifest.py path for Phase-3 hook",
    )
    parser.add_argument("--neural-scene-runner", default="", help="Optional neural_scene_bridge.py path")
    parser.add_argument("--neural-scene-out", default="", help="Optional neural scene output path")
    parser.add_argument("--neural-render-runner", default="", help="Optional render_neural_sensor_stub.py path")
    parser.add_argument("--neural-render-sensor-rig", default="", help="Optional sensor rig path for neural render")
    parser.add_argument("--neural-render-out", default="", help="Optional neural render output path")
    parser.add_argument(
        "--sim-runtime-adapter-runner",
        default="",
        help="Optional sim_runtime_adapter_stub.py path for runtime rendering scaffold",
    )
    parser.add_argument("--sim-runtime", default="", help="Optional runtime adapter target (none|awsim|carla)")
    parser.add_argument("--sim-runtime-scene", default="", help="Optional scene path for runtime adapter hook")
    parser.add_argument("--sim-runtime-sensor-rig", default="", help="Optional sensor rig path for runtime adapter hook")
    parser.add_argument("--sim-runtime-mode", default="", help="Optional runtime adapter mode (headless|interactive)")
    parser.add_argument("--sim-runtime-out", default="", help="Optional runtime adapter report output path")
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
    parser.add_argument("--vehicle-dynamics-runner", default="", help="Optional vehicle_dynamics_stub.py path")
    parser.add_argument("--vehicle-profile", default="", help="Optional vehicle profile path for Phase-3 hook")
    parser.add_argument("--control-sequence", default="", help="Optional control sequence path for Phase-3 hook")
    parser.add_argument("--vehicle-dynamics-out", default="", help="Optional vehicle dynamics output path")
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
    parser.add_argument("--dataset-id", default="", help="Optional dataset id override for Phase-3 hook")
    parser.add_argument("--dataset-manifest-out", default="", help="Optional dataset manifest output path")
    parser.add_argument("--hil-sequence-runner", default="", help="Optional hil_sequence_runner_stub.py path")
    parser.add_argument("--hil-interface", default="", help="Optional HIL interface JSON path")
    parser.add_argument("--hil-sequence", default="", help="Optional HIL test sequence JSON path")
    parser.add_argument("--hil-max-runtime-sec", default="", help="Optional HIL runtime upper bound in seconds")
    parser.add_argument("--hil-schedule-out", default="", help="Optional HIL schedule manifest output path")
    parser.add_argument("--adp-trace-runner", default="", help="Optional adp_workflow_trace_stub.py path")
    parser.add_argument("--adp-trace-out", default="", help="Optional ADP workflow trace output path")
    parser.add_argument(
        "--phase4-linkage-runner",
        default="",
        help="Optional phase4_module_linkage_check_stub.py path",
    )
    parser.add_argument("--phase4-linkage-matrix", default="", help="Optional Phase-4 linkage matrix file path")
    parser.add_argument(
        "--phase4-linkage-checklist",
        default="",
        help="Optional Phase-4 linkage checklist file path",
    )
    parser.add_argument(
        "--phase4-linkage-reference-map",
        default="",
        help="Optional Phase-4 external reference map file path",
    )
    parser.add_argument(
        "--phase4-linkage-module",
        action="append",
        default=[],
        help=(
            "Optional Phase-4 linkage module "
            f"(repeatable; allowed: {PHASE4_LINKAGE_ALLOWED_MODULES_TEXT})"
        ),
    )
    parser.add_argument("--phase4-linkage-out", default="", help="Optional Phase-4 linkage report output path")
    parser.add_argument(
        "--phase4-reference-pattern-runner",
        default="",
        help="Optional phase4_reference_pattern_scan_stub.py path",
    )
    parser.add_argument("--phase4-reference-index", default="", help="Optional Phase-4 reference index JSON path")
    parser.add_argument(
        "--phase4-reference-repo-root",
        default="",
        help="Optional local repository root for Phase-4 reference pattern repo scan fallback",
    )
    parser.add_argument(
        "--phase4-reference-repo-path",
        action="append",
        default=[],
        help="Optional explicit repo_id=path mapping for Phase-4 reference pattern repo scan fallback (repeatable)",
    )
    parser.add_argument(
        "--phase4-reference-max-scan-files-per-repo",
        default="",
        help="Optional max text files scanned per repo for Phase-4 reference pattern repo scan fallback",
    )
    parser.add_argument(
        "--phase4-reference-pattern-module",
        action="append",
        default=[],
        help=(
            "Optional Phase-4 reference pattern module "
            f"(repeatable; allowed: {PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_TEXT})"
        ),
    )
    parser.add_argument(
        "--phase4-reference-min-coverage-ratio",
        default="",
        help="Optional Phase-4 reference pattern minimum coverage ratio",
    )
    parser.add_argument(
        "--phase4-reference-secondary-min-coverage-ratio",
        default="",
        help="Optional Phase-4 secondary reference pattern minimum coverage ratio",
    )
    parser.add_argument(
        "--phase4-reference-pattern-out",
        default="",
        help="Optional Phase-4 reference pattern scan report output path",
    )
    parser.add_argument("--copilot-contract-runner", default="", help="Optional copilot_prompt_contract_stub.py path")
    parser.add_argument(
        "--copilot-release-assist-runner",
        default="",
        help="Optional copilot_release_assist_hook_stub.py path",
    )
    parser.add_argument("--copilot-mode", default="", help="Optional Copilot mode for Phase-4 hook")
    parser.add_argument("--copilot-prompt", default="", help="Optional Copilot prompt text for Phase-4 hook")
    parser.add_argument("--copilot-context-json", default="", help="Optional Copilot context JSON path")
    parser.add_argument(
        "--copilot-guard-hold-threshold",
        default="",
        help="Optional Copilot guard HOLD threshold for Phase-4 hook",
    )
    parser.add_argument("--copilot-contract-out", default="", help="Optional Copilot contract output path")
    parser.add_argument("--copilot-audit-log", default="", help="Optional Copilot audit log output path")
    parser.add_argument(
        "--copilot-release-assist-out",
        default="",
        help="Optional Copilot release-assist output path",
    )
    parser.add_argument("--trend-window", default="", help="Trend window input (optional)")
    parser.add_argument("--trend-min-pass-rate", default="", help="Trend pass rate input (optional)")
    parser.add_argument("--trend-min-samples", default="", help="Trend sample count input (optional)")
    parser.add_argument("--default-trend-window", default="", help="Default trend window")
    parser.add_argument(
        "--default-trend-min-pass-rate",
        default="",
        help="Default trend minimum pass rate",
    )
    parser.add_argument(
        "--default-trend-min-samples",
        default="",
        help="Default trend minimum samples",
    )
    parser.add_argument("--sds-versions-csv", default="", help="SDS versions CSV input")
    parser.add_argument("--default-sds-versions", default="", help="Fallback SDS versions CSV")
    parser.add_argument("--python-bin", default="python3", help="Python executable")
    parser.add_argument(
        "--runner",
        default=str(Path(__file__).resolve().with_name("run_e2e_pipeline.py")),
        help="run_e2e_pipeline.py path",
    )
    parser.add_argument("--log-path", default="e2e_pipeline.log", help="Log file path")
    parser.add_argument(
        "--github-output",
        default="",
        help="Path to GitHub output file (defaults to GITHUB_OUTPUT_PATH or GITHUB_OUTPUT env)",
    )
    parser.add_argument(
        "--step-summary-file",
        default="",
        help="Path to GitHub step summary file (defaults to STEP_SUMMARY_FILE or GITHUB_STEP_SUMMARY env)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print normalized command without running")
    return parser.parse_args()


def resolve_profile_defaults(
    *,
    python_bin: str,
    loader_path: str,
    profile_file: str,
    profile_id: str,
) -> tuple[str, str]:
    cmd = [
        python_bin,
        str(Path(loader_path).resolve()),
        "--profiles-file",
        profile_file,
        "--output",
        "field",
        "--field",
        "default_batch_spec",
        "--field",
        "default_sds_versions",
    ]
    selected_profile_id = str(profile_id).strip()
    if selected_profile_id:
        cmd.extend(["--profile-id", selected_profile_id])

    stdout_text = run_capture_stdout_or_raise(
        cmd,
        context="profile resolution",
        emit_output_on_success=False,
        emit_output_on_failure=False,
    )
    fields = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    if len(fields) < 2:
        raise RuntimeError("profile resolution failed: expected batch spec and sds versions output")
    return fields[0], fields[1]


def normalize_batch_spec_path(
    *,
    batch_spec: str,
    profile_file: str,
) -> str:
    batch_spec_text = str(batch_spec).strip()
    if not batch_spec_text:
        return ""
    candidate = Path(batch_spec_text)
    if candidate.is_absolute():
        return str(candidate)
    if candidate.exists():
        return str(candidate.resolve())

    profile_file_text = str(profile_file).strip()
    if profile_file_text:
        profile_parent = Path(profile_file_text).resolve().parent
        profile_relative = (profile_parent / candidate).resolve()
        if profile_relative.exists():
            return str(profile_relative)

    repo_root = resolve_repo_root(__file__)
    repo_relative = (repo_root / candidate).resolve()
    if repo_relative.exists():
        return str(repo_relative)

    return batch_spec_text


def normalize_optional_path(
    *,
    value: str,
    profile_file: str,
) -> str:
    value_text = str(value).strip()
    if not value_text:
        return ""
    candidate = Path(value_text)
    if candidate.is_absolute():
        return str(candidate)
    if candidate.exists():
        return str(candidate.resolve())

    profile_file_text = str(profile_file).strip()
    if profile_file_text:
        profile_parent = Path(profile_file_text).resolve().parent
        profile_relative = (profile_parent / candidate).resolve()
        if profile_relative.exists():
            return str(profile_relative)

    repo_root = resolve_repo_root(__file__)
    repo_relative = (repo_root / candidate).resolve()
    if repo_relative.exists():
        return str(repo_relative)

    return value_text


def resolve_batch_and_versions(
    *,
    args: argparse.Namespace,
) -> tuple[str, str]:
    batch_spec = str(args.batch_spec).strip()
    sds_versions_csv = str(args.sds_versions_csv).strip()

    profile_file = str(args.profile_file).strip()
    profile_id = str(args.profile_id).strip()
    profile_default_file = str(args.profile_default_file).strip()
    if not profile_file and profile_id and profile_default_file:
        profile_file = profile_default_file

    if not batch_spec and profile_file:
        profile_path = Path(profile_file).resolve()
        if profile_path.exists():
            try:
                profile_batch_spec, profile_sds_versions = resolve_profile_defaults(
                    python_bin=str(args.python_bin),
                    loader_path=str(args.profile_loader),
                    profile_file=str(profile_path),
                    profile_id=profile_id,
                )
                if profile_batch_spec:
                    batch_spec = profile_batch_spec
                if not sds_versions_csv and profile_sds_versions:
                    sds_versions_csv = profile_sds_versions
            except Exception as exc:  # pragma: no cover - exercised via CLI tests
                print(f"[warn] {exc}", file=sys.stderr)
        else:
            print(f"[warn] profile file not found: {profile_path}", file=sys.stderr)

    fallback_batch_spec = str(args.fallback_batch_spec).strip()
    if not batch_spec and fallback_batch_spec:
        batch_spec = fallback_batch_spec

    if not batch_spec:
        raise ValueError("batch-spec is required (provide batch-spec, profile, or fallback-batch-spec)")

    batch_spec = normalize_batch_spec_path(batch_spec=batch_spec, profile_file=profile_file)
    return batch_spec, sds_versions_csv


def resolve_phase4_linkage_modules(values: list[str]) -> list[str]:
    return resolve_phase4_linkage_modules_contract(values, default_to_allowed_when_empty=False)


def resolve_phase4_reference_pattern_modules(values: list[str]) -> list[str]:
    return resolve_phase4_reference_pattern_modules_contract(values, default_to_allowed_when_empty=False)


def build_cmd(
    *,
    args: argparse.Namespace,
    release_id: str,
    batch_spec: str,
    strict_gate: bool,
    phase2_enable_hooks: bool,
    phase3_enable_hooks: bool,
    sim_runtime_probe_enable: bool,
    sim_runtime_probe_execute: bool,
    sim_runtime_probe_require_availability: bool,
    sim_runtime_scenario_contract_enable: bool,
    sim_runtime_scenario_contract_require_runtime_ready: bool,
    sim_runtime_scene_result_enable: bool,
    sim_runtime_scene_result_require_runtime_ready: bool,
    sim_runtime_interop_contract_enable: bool,
    sim_runtime_interop_contract_require_runtime_ready: bool,
    phase4_enable_hooks: bool,
    phase4_enable_copilot_hooks: bool,
    phase4_require_done: bool,
    hil_max_runtime_sec: float,
    copilot_guard_hold_threshold: int,
    trend_window: int,
    trend_min_pass_rate: float,
    trend_min_samples: int,
    phase2_route_gate_require_status_pass: bool,
    phase2_route_gate_require_routing_semantic_pass: bool,
    phase2_route_gate_min_lane_count: int,
    phase2_route_gate_min_total_length_m: float,
    phase2_route_gate_max_routing_semantic_warning_count: int,
    phase2_route_gate_max_unreachable_lane_count: int,
    phase2_route_gate_max_non_reciprocal_link_warning_count: int,
    phase2_route_gate_max_continuity_gap_warning_count: int,
    phase3_control_gate_max_overlap_ratio: float,
    phase3_control_gate_max_steering_rate_degps: float,
    phase3_control_gate_max_throttle_plus_brake: float,
    phase3_control_gate_max_speed_tracking_error_abs_mps: float,
    phase3_dataset_gate_min_run_summary_count: int,
    phase3_dataset_gate_min_traffic_profile_count: int,
    phase3_dataset_gate_min_actor_pattern_count: int,
    phase3_dataset_gate_min_avg_npc_count: float,
    phase3_lane_risk_gate_min_ttc_same_lane_sec: float,
    phase3_lane_risk_gate_min_ttc_adjacent_lane_sec: float,
    phase3_lane_risk_gate_min_ttc_any_lane_sec: float,
    phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total: int,
    phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total: int,
    phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total: int,
    phase3_enable_ego_collision_avoidance: bool,
    phase3_avoidance_ttc_threshold_sec: float,
    phase3_ego_max_brake_mps2: float,
    phase3_tire_friction_coeff: float,
    phase3_surface_friction_scale: float,
    phase3_core_sim_gate_require_success: bool,
    phase3_core_sim_gate_min_ttc_same_lane_sec: float,
    phase3_core_sim_gate_min_ttc_any_lane_sec: float,
    phase3_core_sim_matrix_gate_require_all_cases_success: bool,
    phase3_core_sim_matrix_gate_min_ttc_same_lane_sec: float,
    phase3_core_sim_matrix_gate_min_ttc_any_lane_sec: float,
    phase3_core_sim_matrix_gate_max_failed_cases: int,
    phase3_core_sim_matrix_gate_max_collision_cases: int,
    phase3_core_sim_matrix_gate_max_timeout_cases: int,
    versions: list[str],
) -> list[str]:
    profile_file_text = str(args.profile_file).strip()
    if not profile_file_text:
        profile_file_text = str(args.profile_default_file).strip()
    normalized_sim_runtime_scene = normalize_optional_path(
        value=str(args.sim_runtime_scene),
        profile_file=profile_file_text,
    )

    cmd = [
        args.python_bin,
        str(Path(args.runner).resolve()),
        "--batch-spec",
        batch_spec,
        "--release-id",
        release_id,
    ]
    core_optional_pairs = [
        ("--batch-out", args.batch_out),
        ("--db", args.db),
        ("--report-dir", args.report_dir),
        ("--cloud-runner", args.cloud_runner),
        ("--ingest-runner", args.ingest_runner),
        ("--report-runner", args.report_runner),
    ]
    for flag, value in core_optional_pairs:
        value_text = str(value).strip()
        if value_text:
            cmd.extend([flag, value_text])

    gate_profile = str(args.gate_profile).strip()
    if gate_profile:
        cmd.extend(["--gate-profile", gate_profile])

    requirement_map = str(args.requirement_map).strip()
    if requirement_map:
        cmd.extend(["--requirement-map", requirement_map])

    if strict_gate:
        cmd.append("--strict-gate")

    if phase2_enable_hooks:
        cmd.append("--phase2-enable-hooks")
        phase2_optional_pairs = [
            ("--sensor-bridge-runner", args.sensor_bridge_runner),
            ("--sensor-bridge-world-state", args.sensor_bridge_world_state),
            ("--sensor-bridge-rig", args.sensor_bridge_rig),
            ("--sensor-bridge-out", args.sensor_bridge_out),
            ("--sensor-bridge-fidelity-tier", args.sensor_bridge_fidelity_tier),
            ("--sensor-sweep-runner", args.sensor_sweep_runner),
            ("--sensor-sweep-candidates", args.sensor_sweep_candidates),
            ("--sensor-sweep-out", args.sensor_sweep_out),
            ("--log-replay-runner", args.log_replay_runner),
            ("--log-scene", args.log_scene),
            ("--log-replay-out-root", args.log_replay_out_root),
            ("--map-convert-runner", args.map_convert_runner),
            ("--map-validate-runner", args.map_validate_runner),
            ("--map-simple", args.map_simple),
            ("--map-canonical-out", args.map_canonical_out),
            ("--map-validate-report-out", args.map_validate_report_out),
            ("--map-route-runner", args.map_route_runner),
            ("--map-route-report-out", args.map_route_report_out),
            ("--map-route-cost-mode", args.map_route_cost_mode),
            ("--map-route-entry-lane-id", args.map_route_entry_lane_id),
            ("--map-route-exit-lane-id", args.map_route_exit_lane_id),
        ]
        for flag, value in phase2_optional_pairs:
            value_text = str(value).strip()
            if value_text:
                cmd.extend([flag, value_text])
        for raw_via_lane_id in args.map_route_via_lane_id:
            via_lane_id = str(raw_via_lane_id).strip()
            if via_lane_id:
                cmd.extend(["--map-route-via-lane-id", via_lane_id])
        if phase2_route_gate_require_status_pass:
            cmd.append("--phase2-route-gate-require-status-pass")
        if phase2_route_gate_require_routing_semantic_pass:
            cmd.append("--phase2-route-gate-require-routing-semantic-pass")
        if phase2_route_gate_min_lane_count > 0:
            cmd.extend(["--phase2-route-gate-min-lane-count", str(phase2_route_gate_min_lane_count)])
        if phase2_route_gate_min_total_length_m > 0.0:
            cmd.extend(
                ["--phase2-route-gate-min-total-length-m", str(phase2_route_gate_min_total_length_m)]
            )
        if phase2_route_gate_max_routing_semantic_warning_count > 0:
            cmd.extend(
                [
                    "--phase2-route-gate-max-routing-semantic-warning-count",
                    str(phase2_route_gate_max_routing_semantic_warning_count),
                ]
            )
        if phase2_route_gate_max_unreachable_lane_count > 0:
            cmd.extend(
                [
                    "--phase2-route-gate-max-unreachable-lane-count",
                    str(phase2_route_gate_max_unreachable_lane_count),
                ]
            )
        if phase2_route_gate_max_non_reciprocal_link_warning_count > 0:
            cmd.extend(
                [
                    "--phase2-route-gate-max-non-reciprocal-link-warning-count",
                    str(phase2_route_gate_max_non_reciprocal_link_warning_count),
                ]
            )
        if phase2_route_gate_max_continuity_gap_warning_count > 0:
            cmd.extend(
                [
                    "--phase2-route-gate-max-continuity-gap-warning-count",
                    str(phase2_route_gate_max_continuity_gap_warning_count),
                ]
            )

    if phase3_enable_hooks:
        cmd.append("--phase3-enable-hooks")
        if phase3_enable_ego_collision_avoidance:
            cmd.append("--phase3-enable-ego-collision-avoidance")
        if phase3_core_sim_gate_require_success:
            cmd.append("--phase3-core-sim-gate-require-success")
        if sim_runtime_probe_enable:
            cmd.append("--sim-runtime-probe-enable")
        if sim_runtime_probe_execute:
            cmd.append("--sim-runtime-probe-execute")
        if sim_runtime_probe_require_availability:
            cmd.append("--sim-runtime-probe-require-availability")
        if sim_runtime_scenario_contract_enable:
            cmd.append("--sim-runtime-scenario-contract-enable")
        if sim_runtime_scenario_contract_require_runtime_ready:
            cmd.append("--sim-runtime-scenario-contract-require-runtime-ready")
        if sim_runtime_scene_result_enable:
            cmd.append("--sim-runtime-scene-result-enable")
        if sim_runtime_scene_result_require_runtime_ready:
            cmd.append("--sim-runtime-scene-result-require-runtime-ready")
        if sim_runtime_interop_contract_enable:
            cmd.append("--sim-runtime-interop-contract-enable")
        if sim_runtime_interop_contract_require_runtime_ready:
            cmd.append("--sim-runtime-interop-contract-require-runtime-ready")
        phase3_optional_pairs = [
            ("--dataset-manifest-runner", args.dataset_manifest_runner),
            ("--neural-scene-runner", args.neural_scene_runner),
            ("--neural-scene-out", args.neural_scene_out),
            ("--neural-render-runner", args.neural_render_runner),
            ("--neural-render-sensor-rig", args.neural_render_sensor_rig),
            ("--neural-render-out", args.neural_render_out),
            ("--sim-runtime-adapter-runner", args.sim_runtime_adapter_runner),
            ("--sim-runtime", args.sim_runtime),
            ("--sim-runtime-scene", normalized_sim_runtime_scene),
            ("--sim-runtime-sensor-rig", args.sim_runtime_sensor_rig),
            ("--sim-runtime-mode", args.sim_runtime_mode),
            ("--sim-runtime-out", args.sim_runtime_out),
            ("--sim-runtime-probe-runner", args.sim_runtime_probe_runner),
            ("--sim-runtime-probe-runtime-bin", args.sim_runtime_probe_runtime_bin),
            ("--sim-runtime-probe-flag", args.sim_runtime_probe_flag),
            ("--sim-runtime-probe-args-shlex", args.sim_runtime_probe_args_shlex),
            ("--sim-runtime-probe-out", args.sim_runtime_probe_out),
            ("--sim-runtime-scenario-contract-runner", args.sim_runtime_scenario_contract_runner),
            ("--sim-runtime-scenario-contract-out", args.sim_runtime_scenario_contract_out),
            ("--sim-runtime-scene-result-runner", args.sim_runtime_scene_result_runner),
            ("--sim-runtime-scene-result-out", args.sim_runtime_scene_result_out),
            ("--sim-runtime-interop-contract-runner", args.sim_runtime_interop_contract_runner),
            ("--sim-runtime-interop-export-runner", args.sim_runtime_interop_export_runner),
            (
                "--sim-runtime-interop-export-road-length-scale",
                args.sim_runtime_interop_export_road_length_scale,
            ),
            ("--sim-runtime-interop-export-xosc-out", args.sim_runtime_interop_export_xosc_out),
            ("--sim-runtime-interop-export-xodr-out", args.sim_runtime_interop_export_xodr_out),
            ("--sim-runtime-interop-export-out", args.sim_runtime_interop_export_out),
            ("--sim-runtime-interop-import-runner", args.sim_runtime_interop_import_runner),
            ("--sim-runtime-interop-import-out", args.sim_runtime_interop_import_out),
            (
                "--sim-runtime-interop-import-manifest-consistency-mode",
                args.sim_runtime_interop_import_manifest_consistency_mode,
            ),
            (
                "--sim-runtime-interop-import-export-consistency-mode",
                args.sim_runtime_interop_import_export_consistency_mode,
            ),
            ("--sim-runtime-interop-contract-xosc", args.sim_runtime_interop_contract_xosc),
            ("--sim-runtime-interop-contract-xodr", args.sim_runtime_interop_contract_xodr),
            ("--sim-runtime-interop-contract-out", args.sim_runtime_interop_contract_out),
            ("--vehicle-dynamics-runner", args.vehicle_dynamics_runner),
            ("--vehicle-profile", args.vehicle_profile),
            ("--control-sequence", args.control_sequence),
            ("--vehicle-dynamics-out", args.vehicle_dynamics_out),
            ("--phase3-core-sim-runner", args.phase3_core_sim_runner),
            ("--phase3-core-sim-scenario", args.phase3_core_sim_scenario),
            ("--phase3-core-sim-run-id", args.phase3_core_sim_run_id),
            ("--phase3-core-sim-out-root", args.phase3_core_sim_out_root),
            ("--dataset-id", args.dataset_id),
            ("--dataset-manifest-out", args.dataset_manifest_out),
        ]
        phase3_inline_assign_flags = {
            "--sim-runtime-probe-flag",
            "--sim-runtime-probe-args-shlex",
        }
        for flag, value in phase3_optional_pairs:
            value_text = str(value).strip()
            if value_text:
                if flag in phase3_inline_assign_flags:
                    cmd.append(f"{flag}={value_text}")
                else:
                    cmd.extend([flag, value_text])
        if phase3_control_gate_max_overlap_ratio > 0.0:
            cmd.extend(
                ["--phase3-control-gate-max-overlap-ratio", str(phase3_control_gate_max_overlap_ratio)]
            )
        if phase3_control_gate_max_steering_rate_degps > 0.0:
            cmd.extend(
                [
                    "--phase3-control-gate-max-steering-rate-degps",
                    str(phase3_control_gate_max_steering_rate_degps),
                ]
            )
        if phase3_control_gate_max_throttle_plus_brake > 0.0:
            cmd.extend(
                [
                    "--phase3-control-gate-max-throttle-plus-brake",
                    str(phase3_control_gate_max_throttle_plus_brake),
                ]
            )
        if phase3_control_gate_max_speed_tracking_error_abs_mps > 0.0:
            cmd.extend(
                [
                    "--phase3-control-gate-max-speed-tracking-error-abs-mps",
                    str(phase3_control_gate_max_speed_tracking_error_abs_mps),
                ]
            )
        if phase3_dataset_gate_min_run_summary_count > 0:
            cmd.extend(
                [
                    "--phase3-dataset-gate-min-run-summary-count",
                    str(phase3_dataset_gate_min_run_summary_count),
                ]
            )
        if phase3_dataset_gate_min_traffic_profile_count > 0:
            cmd.extend(
                [
                    "--phase3-dataset-gate-min-traffic-profile-count",
                    str(phase3_dataset_gate_min_traffic_profile_count),
                ]
            )
        if phase3_dataset_gate_min_actor_pattern_count > 0:
            cmd.extend(
                [
                    "--phase3-dataset-gate-min-actor-pattern-count",
                    str(phase3_dataset_gate_min_actor_pattern_count),
                ]
            )
        if phase3_dataset_gate_min_avg_npc_count > 0.0:
            cmd.extend(
                [
                    "--phase3-dataset-gate-min-avg-npc-count",
                    str(phase3_dataset_gate_min_avg_npc_count),
                ]
            )
        if phase3_lane_risk_gate_min_ttc_same_lane_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-lane-risk-gate-min-ttc-same-lane-sec",
                    str(phase3_lane_risk_gate_min_ttc_same_lane_sec),
                ]
            )
        if phase3_lane_risk_gate_min_ttc_adjacent_lane_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
                    str(phase3_lane_risk_gate_min_ttc_adjacent_lane_sec),
                ]
            )
        if phase3_lane_risk_gate_min_ttc_any_lane_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-lane-risk-gate-min-ttc-any-lane-sec",
                    str(phase3_lane_risk_gate_min_ttc_any_lane_sec),
                ]
            )
        if phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total > 0:
            cmd.extend(
                [
                    "--phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
                    str(phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total),
                ]
            )
        if phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total > 0:
            cmd.extend(
                [
                    "--phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
                    str(phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total),
                ]
            )
        if phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total > 0:
            cmd.extend(
                [
                    "--phase3-lane-risk-gate-max-ttc-under-3s-any-lane-total",
                    str(phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total),
                ]
            )
        if phase3_core_sim_gate_min_ttc_same_lane_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-core-sim-gate-min-ttc-same-lane-sec",
                    str(phase3_core_sim_gate_min_ttc_same_lane_sec),
                ]
            )
        if phase3_core_sim_gate_min_ttc_any_lane_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-core-sim-gate-min-ttc-any-lane-sec",
                    str(phase3_core_sim_gate_min_ttc_any_lane_sec),
                ]
            )
        if phase3_core_sim_matrix_gate_require_all_cases_success:
            cmd.append("--phase3-core-sim-matrix-gate-require-all-cases-success")
        if phase3_core_sim_matrix_gate_min_ttc_same_lane_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
                    str(phase3_core_sim_matrix_gate_min_ttc_same_lane_sec),
                ]
            )
        if phase3_core_sim_matrix_gate_min_ttc_any_lane_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
                    str(phase3_core_sim_matrix_gate_min_ttc_any_lane_sec),
                ]
            )
        if phase3_core_sim_matrix_gate_max_failed_cases > 0:
            cmd.extend(
                [
                    "--phase3-core-sim-matrix-gate-max-failed-cases",
                    str(phase3_core_sim_matrix_gate_max_failed_cases),
                ]
            )
        if phase3_core_sim_matrix_gate_max_collision_cases > 0:
            cmd.extend(
                [
                    "--phase3-core-sim-matrix-gate-max-collision-cases",
                    str(phase3_core_sim_matrix_gate_max_collision_cases),
                ]
            )
        if phase3_core_sim_matrix_gate_max_timeout_cases > 0:
            cmd.extend(
                [
                    "--phase3-core-sim-matrix-gate-max-timeout-cases",
                    str(phase3_core_sim_matrix_gate_max_timeout_cases),
                ]
            )
        if phase3_avoidance_ttc_threshold_sec > 0.0:
            cmd.extend(
                [
                    "--phase3-avoidance-ttc-threshold-sec",
                    str(phase3_avoidance_ttc_threshold_sec),
                ]
            )
        if phase3_ego_max_brake_mps2 > 0.0:
            cmd.extend(
                [
                    "--phase3-ego-max-brake-mps2",
                    str(phase3_ego_max_brake_mps2),
                ]
            )
        if phase3_tire_friction_coeff > 0.0:
            cmd.extend(
                [
                    "--phase3-tire-friction-coeff",
                    str(phase3_tire_friction_coeff),
                ]
            )
        if phase3_surface_friction_scale > 0.0:
            cmd.extend(
                [
                    "--phase3-surface-friction-scale",
                    str(phase3_surface_friction_scale),
                ]
            )

    if phase4_enable_hooks:
        cmd.append("--phase4-enable-hooks")
        if phase4_require_done:
            cmd.append("--phase4-require-done")
        hil_max_runtime_text = str(args.hil_max_runtime_sec).strip()
        phase4_optional_pairs = [
            ("--hil-sequence-runner", args.hil_sequence_runner),
            ("--hil-interface", args.hil_interface),
            ("--hil-sequence", args.hil_sequence),
            ("--hil-schedule-out", args.hil_schedule_out),
            ("--adp-trace-runner", args.adp_trace_runner),
            ("--adp-trace-out", args.adp_trace_out),
            ("--phase4-linkage-runner", args.phase4_linkage_runner),
            ("--phase4-linkage-matrix", args.phase4_linkage_matrix),
            ("--phase4-linkage-checklist", args.phase4_linkage_checklist),
            ("--phase4-linkage-reference-map", args.phase4_linkage_reference_map),
            ("--phase4-linkage-out", args.phase4_linkage_out),
            ("--phase4-reference-pattern-runner", args.phase4_reference_pattern_runner),
            ("--phase4-reference-index", args.phase4_reference_index),
            ("--phase4-reference-repo-root", args.phase4_reference_repo_root),
            (
                "--phase4-reference-max-scan-files-per-repo",
                args.phase4_reference_max_scan_files_per_repo,
            ),
            ("--phase4-reference-min-coverage-ratio", args.phase4_reference_min_coverage_ratio),
            (
                "--phase4-reference-secondary-min-coverage-ratio",
                args.phase4_reference_secondary_min_coverage_ratio,
            ),
            ("--phase4-reference-pattern-out", args.phase4_reference_pattern_out),
        ]
        for flag, value in phase4_optional_pairs:
            value_text = str(value).strip()
            if value_text:
                cmd.extend([flag, value_text])
        if hil_max_runtime_text:
            cmd.extend(["--hil-max-runtime-sec", str(hil_max_runtime_sec)])
        for module in resolve_phase4_linkage_modules(args.phase4_linkage_module):
            cmd.extend(["--phase4-linkage-module", module])
        for mapping in args.phase4_reference_repo_path:
            cmd.extend(["--phase4-reference-repo-path", mapping])
        for module in args.phase4_reference_pattern_module:
            cmd.extend(["--phase4-reference-pattern-module", module])

        if phase4_enable_copilot_hooks:
            cmd.append("--phase4-enable-copilot-hooks")
            copilot_guard_threshold_text = str(args.copilot_guard_hold_threshold).strip()
            phase4_copilot_optional_pairs = [
                ("--copilot-contract-runner", args.copilot_contract_runner),
                ("--copilot-release-assist-runner", args.copilot_release_assist_runner),
                ("--copilot-mode", args.copilot_mode),
                ("--copilot-prompt", args.copilot_prompt),
                ("--copilot-context-json", args.copilot_context_json),
                ("--copilot-contract-out", args.copilot_contract_out),
                ("--copilot-audit-log", args.copilot_audit_log),
                ("--copilot-release-assist-out", args.copilot_release_assist_out),
            ]
            for flag, value in phase4_copilot_optional_pairs:
                value_text = str(value).strip()
                if value_text:
                    cmd.extend([flag, value_text])
            if copilot_guard_threshold_text:
                cmd.extend(["--copilot-guard-hold-threshold", str(copilot_guard_hold_threshold)])

    if trend_window != 0:
        cmd.extend(
            [
                "--trend-window",
                str(trend_window),
                "--trend-min-pass-rate",
                str(trend_min_pass_rate),
                "--trend-min-samples",
                str(trend_min_samples),
            ]
        )

    for version in versions:
        cmd.extend(["--sds-version", version])

    return cmd


def append_github_output(path: str, *, key: str, value: str) -> None:
    output_path = str(path).strip()
    if not output_path:
        return
    resolved = Path(output_path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("a", encoding="utf-8") as handle:
        if "\n" in value:
            marker = f"CI_EOF_{os.getpid()}_{int(time.time() * 1000)}"
            while marker in value:
                marker += "_X"
            handle.write(f"{key}<<{marker}\n")
            handle.write(value)
            if not value.endswith("\n"):
                handle.write("\n")
            handle.write(f"{marker}\n")
            return
        handle.write(f"{key}={value}\n")


def run_with_streaming(cmd: list[str], *, log_path: Path) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = ""
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    ) as proc, log_path.open("w", encoding="utf-8") as log_file:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log_file.write(line)
            stripped = line.strip()
            if stripped.startswith("[ok] pipeline_manifest="):
                manifest_path = stripped.split("=", 1)[1].strip()
        rc = int(proc.wait())
    return rc, manifest_path


def extract_failure_detail(log_path: Path) -> str:
    if not log_path.exists():
        return ""
    last_nonempty = ""
    last_error = ""
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            last_nonempty = line
            if line.startswith("[error]"):
                last_error = line
    detail = last_error or last_nonempty
    if not detail:
        return ""
    return compact_failure_detail(detail)


def main() -> int:
    args = parse_args()
    failure_phase = PHASE_RESOLVE_INPUTS
    failure_command = ""
    pipeline_started = False
    pipeline_exit_code = ""
    pipeline_log_path = ""
    pipeline_manifest_path = ""
    github_output_file = str(args.github_output).strip() or resolve_github_output_file_from_env()
    step_summary_file = str(args.step_summary_file).strip() or resolve_step_summary_file_from_env()
    try:
        strict_gate = parse_bool(
            args.strict_gate_input,
            default=(args.strict_gate_default == "true"),
            field="strict-gate-input",
        )
        phase2_enable_hooks = parse_bool(
            args.phase2_enable_hooks_input,
            default=(args.phase2_enable_hooks_default == "true"),
            field="phase2-enable-hooks-input",
        )
        phase2_route_gate_require_status_pass = parse_bool(
            args.phase2_route_gate_require_status_pass_input,
            default=(args.phase2_route_gate_require_status_pass_default == "true"),
            field="phase2-route-gate-require-status-pass-input",
        )
        phase2_route_gate_require_routing_semantic_pass = parse_bool(
            args.phase2_route_gate_require_routing_semantic_pass_input,
            default=(args.phase2_route_gate_require_routing_semantic_pass_default == "true"),
            field="phase2-route-gate-require-routing-semantic-pass-input",
        )
        phase2_route_gate_min_lane_count = parse_int(
            args.phase2_route_gate_min_lane_count,
            default=0,
            field="phase2-route-gate-min-lane-count",
            minimum=0,
        )
        phase2_route_gate_min_total_length_m = parse_non_negative_float(
            args.phase2_route_gate_min_total_length_m,
            default=0.0,
            field="phase2-route-gate-min-total-length-m",
        )
        phase2_route_gate_max_routing_semantic_warning_count = parse_int(
            args.phase2_route_gate_max_routing_semantic_warning_count,
            default=0,
            field="phase2-route-gate-max-routing-semantic-warning-count",
            minimum=0,
        )
        phase2_route_gate_max_unreachable_lane_count = parse_int(
            args.phase2_route_gate_max_unreachable_lane_count,
            default=0,
            field="phase2-route-gate-max-unreachable-lane-count",
            minimum=0,
        )
        phase2_route_gate_max_non_reciprocal_link_warning_count = parse_int(
            args.phase2_route_gate_max_non_reciprocal_link_warning_count,
            default=0,
            field="phase2-route-gate-max-non-reciprocal-link-warning-count",
            minimum=0,
        )
        phase2_route_gate_max_continuity_gap_warning_count = parse_int(
            args.phase2_route_gate_max_continuity_gap_warning_count,
            default=0,
            field="phase2-route-gate-max-continuity-gap-warning-count",
            minimum=0,
        )
        phase3_enable_hooks = parse_bool(
            args.phase3_enable_hooks_input,
            default=(args.phase3_enable_hooks_default == "true"),
            field="phase3-enable-hooks-input",
        )
        phase3_control_gate_max_overlap_ratio = parse_float(
            args.phase3_control_gate_max_overlap_ratio,
            default=0.0,
            field="phase3-control-gate-max-overlap-ratio",
        )
        phase3_control_gate_max_steering_rate_degps = parse_non_negative_float(
            args.phase3_control_gate_max_steering_rate_degps,
            default=0.0,
            field="phase3-control-gate-max-steering-rate-degps",
        )
        phase3_control_gate_max_throttle_plus_brake = parse_non_negative_float(
            args.phase3_control_gate_max_throttle_plus_brake,
            default=0.0,
            field="phase3-control-gate-max-throttle-plus-brake",
        )
        phase3_control_gate_max_speed_tracking_error_abs_mps = parse_non_negative_float(
            args.phase3_control_gate_max_speed_tracking_error_abs_mps,
            default=0.0,
            field="phase3-control-gate-max-speed-tracking-error-abs-mps",
        )
        phase3_dataset_gate_min_run_summary_count = parse_int(
            args.phase3_dataset_gate_min_run_summary_count,
            default=0,
            field="phase3-dataset-gate-min-run-summary-count",
            minimum=0,
        )
        phase3_dataset_gate_min_traffic_profile_count = parse_int(
            args.phase3_dataset_gate_min_traffic_profile_count,
            default=0,
            field="phase3-dataset-gate-min-traffic-profile-count",
            minimum=0,
        )
        phase3_dataset_gate_min_actor_pattern_count = parse_int(
            args.phase3_dataset_gate_min_actor_pattern_count,
            default=0,
            field="phase3-dataset-gate-min-actor-pattern-count",
            minimum=0,
        )
        phase3_dataset_gate_min_avg_npc_count = parse_non_negative_float(
            args.phase3_dataset_gate_min_avg_npc_count,
            default=0.0,
            field="phase3-dataset-gate-min-avg-npc-count",
        )
        phase3_lane_risk_gate_min_ttc_same_lane_sec = parse_non_negative_float(
            args.phase3_lane_risk_gate_min_ttc_same_lane_sec,
            default=0.0,
            field="phase3-lane-risk-gate-min-ttc-same-lane-sec",
        )
        phase3_lane_risk_gate_min_ttc_adjacent_lane_sec = parse_non_negative_float(
            args.phase3_lane_risk_gate_min_ttc_adjacent_lane_sec,
            default=0.0,
            field="phase3-lane-risk-gate-min-ttc-adjacent-lane-sec",
        )
        phase3_lane_risk_gate_min_ttc_any_lane_sec = parse_non_negative_float(
            args.phase3_lane_risk_gate_min_ttc_any_lane_sec,
            default=0.0,
            field="phase3-lane-risk-gate-min-ttc-any-lane-sec",
        )
        phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total = parse_int(
            args.phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total,
            default=0,
            field="phase3-lane-risk-gate-max-ttc-under-3s-same-lane-total",
            minimum=0,
        )
        phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total = parse_int(
            args.phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total,
            default=0,
            field="phase3-lane-risk-gate-max-ttc-under-3s-adjacent-lane-total",
            minimum=0,
        )
        phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total = parse_int(
            args.phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total,
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
            args.phase3_enable_ego_collision_avoidance_input,
            default=(args.phase3_enable_ego_collision_avoidance_default == "true"),
            field="phase3-enable-ego-collision-avoidance-input",
        )
        phase3_avoidance_ttc_threshold_sec = parse_non_negative_float(
            args.phase3_avoidance_ttc_threshold_sec,
            default=0.0,
            field="phase3-avoidance-ttc-threshold-sec",
        )
        phase3_ego_max_brake_mps2 = parse_non_negative_float(
            args.phase3_ego_max_brake_mps2,
            default=0.0,
            field="phase3-ego-max-brake-mps2",
        )
        phase3_tire_friction_coeff = parse_non_negative_float(
            args.phase3_tire_friction_coeff,
            default=0.0,
            field="phase3-tire-friction-coeff",
        )
        phase3_surface_friction_scale = parse_non_negative_float(
            args.phase3_surface_friction_scale,
            default=0.0,
            field="phase3-surface-friction-scale",
        )
        phase3_core_sim_gate_require_success = parse_bool(
            args.phase3_core_sim_gate_require_success_input,
            default=(args.phase3_core_sim_gate_require_success_default == "true"),
            field="phase3-core-sim-gate-require-success-input",
        )
        phase3_core_sim_gate_min_ttc_same_lane_sec = parse_non_negative_float(
            args.phase3_core_sim_gate_min_ttc_same_lane_sec,
            default=0.0,
            field="phase3-core-sim-gate-min-ttc-same-lane-sec",
        )
        phase3_core_sim_gate_min_ttc_any_lane_sec = parse_non_negative_float(
            args.phase3_core_sim_gate_min_ttc_any_lane_sec,
            default=0.0,
            field="phase3-core-sim-gate-min-ttc-any-lane-sec",
        )
        phase3_core_sim_matrix_gate_require_all_cases_success = parse_bool(
            args.phase3_core_sim_matrix_gate_require_all_cases_success_input,
            default=(args.phase3_core_sim_matrix_gate_require_all_cases_success_default == "true"),
            field="phase3-core-sim-matrix-gate-require-all-cases-success-input",
        )
        phase3_core_sim_matrix_gate_min_ttc_same_lane_sec = parse_non_negative_float(
            args.phase3_core_sim_matrix_gate_min_ttc_same_lane_sec,
            default=0.0,
            field="phase3-core-sim-matrix-gate-min-ttc-same-lane-sec",
        )
        phase3_core_sim_matrix_gate_min_ttc_any_lane_sec = parse_non_negative_float(
            args.phase3_core_sim_matrix_gate_min_ttc_any_lane_sec,
            default=0.0,
            field="phase3-core-sim-matrix-gate-min-ttc-any-lane-sec",
        )
        phase3_core_sim_matrix_gate_max_failed_cases = parse_int(
            args.phase3_core_sim_matrix_gate_max_failed_cases,
            default=0,
            field="phase3-core-sim-matrix-gate-max-failed-cases",
            minimum=0,
        )
        phase3_core_sim_matrix_gate_max_collision_cases = parse_int(
            args.phase3_core_sim_matrix_gate_max_collision_cases,
            default=0,
            field="phase3-core-sim-matrix-gate-max-collision-cases",
            minimum=0,
        )
        phase3_core_sim_matrix_gate_max_timeout_cases = parse_int(
            args.phase3_core_sim_matrix_gate_max_timeout_cases,
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
        if str(args.phase3_core_sim_gate_min_ttc_any_lane_sec).strip() and phase3_core_sim_gate_min_ttc_any_lane_sec <= 0.0:
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
            phase2_route_gate_require_status_pass
            or phase2_route_gate_require_routing_semantic_pass
            or phase2_route_gate_min_lane_count > 0
            or phase2_route_gate_min_total_length_m > 0.0
            or phase2_route_gate_max_routing_semantic_warning_count > 0
            or phase2_route_gate_max_unreachable_lane_count > 0
            or phase2_route_gate_max_non_reciprocal_link_warning_count > 0
            or phase2_route_gate_max_continuity_gap_warning_count > 0
        ) and not phase2_enable_hooks:
            raise ValueError(
                "phase2-route-gate-* inputs require phase2-enable-hooks-input=true"
            )
        if (
            phase3_control_gate_max_overlap_ratio > 0.0
            or phase3_control_gate_max_steering_rate_degps > 0.0
            or phase3_control_gate_max_throttle_plus_brake > 0.0
            or phase3_control_gate_max_speed_tracking_error_abs_mps > 0.0
            or phase3_dataset_gate_min_run_summary_count > 0
            or phase3_dataset_gate_min_traffic_profile_count > 0
            or phase3_dataset_gate_min_actor_pattern_count > 0
            or phase3_dataset_gate_min_avg_npc_count > 0.0
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
        ) and not phase3_enable_hooks:
            raise ValueError(
                "phase3-control-gate-*/phase3-dataset-gate-* inputs require phase3-enable-hooks-input=true"
            )
        sim_runtime = str(args.sim_runtime).strip().lower()
        if not sim_runtime:
            sim_runtime = "none"
        if sim_runtime not in {"none", "awsim", "carla"}:
            raise ValueError(f"sim-runtime must be one of: none, awsim, carla; got: {args.sim_runtime}")
        args.sim_runtime = sim_runtime
        sim_runtime_mode = str(args.sim_runtime_mode).strip().lower()
        if not sim_runtime_mode:
            sim_runtime_mode = "headless"
        if sim_runtime_mode not in {"headless", "interactive"}:
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
        if (
            sim_runtime_scenario_contract_require_runtime_ready
            and not sim_runtime_scenario_contract_enable
        ):
            raise ValueError(
                "sim-runtime-scenario-contract-require-runtime-ready-input "
                "requires sim-runtime-scenario-contract-enable-input=true"
            )
        if (
            sim_runtime_interop_contract_require_runtime_ready
            and not sim_runtime_interop_contract_enable
        ):
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
        if sim_runtime == "none" and (
            sim_runtime_probe_enable
            or sim_runtime_probe_execute
            or sim_runtime_probe_require_availability
            or bool(sim_runtime_probe_flag)
            or bool(sim_runtime_probe_args_shlex)
        ):
            raise ValueError(
                "sim-runtime-probe-* inputs require sim-runtime to be one of: awsim, carla"
            )
        if sim_runtime == "none" and (
            sim_runtime_scenario_contract_enable
            or sim_runtime_scenario_contract_require_runtime_ready
        ):
            raise ValueError(
                "sim-runtime-scenario-contract-* inputs require sim-runtime to be one of: awsim, carla"
            )
        if sim_runtime_scenario_contract_require_runtime_ready and not sim_runtime_probe_enable:
            raise ValueError(
                "sim-runtime-scenario-contract-require-runtime-ready-input "
                "requires sim-runtime-probe-enable-input=true"
            )
        if sim_runtime == "none" and (
            sim_runtime_scene_result_enable
            or sim_runtime_scene_result_require_runtime_ready
        ):
            raise ValueError(
                "sim-runtime-scene-result-* inputs require sim-runtime to be one of: awsim, carla"
            )
        if sim_runtime_scene_result_require_runtime_ready and not sim_runtime_probe_enable:
            raise ValueError(
                "sim-runtime-scene-result-require-runtime-ready-input "
                "requires sim-runtime-probe-enable-input=true"
            )
        if sim_runtime == "none" and (
            sim_runtime_interop_contract_enable
            or sim_runtime_interop_contract_require_runtime_ready
            or bool(sim_runtime_interop_import_manifest_consistency_mode)
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
        phase4_enable_hooks = parse_bool(
            args.phase4_enable_hooks_input,
            default=(args.phase4_enable_hooks_default == "true"),
            field="phase4-enable-hooks-input",
        )
        phase4_enable_copilot_hooks = parse_bool(
            args.phase4_enable_copilot_hooks_input,
            default=(args.phase4_enable_copilot_hooks_default == "true"),
            field="phase4-enable-copilot-hooks-input",
        )
        phase4_require_done = parse_bool(
            args.phase4_require_done_input,
            default=(args.phase4_require_done_default == "true"),
            field="phase4-require-done-input",
        )
        args.copilot_mode = resolve_phase4_copilot_mode(
            phase4_enable_hooks=phase4_enable_hooks,
            phase4_enable_copilot_hooks=phase4_enable_copilot_hooks,
            phase4_require_done=phase4_require_done,
            raw_copilot_mode=str(args.copilot_mode),
            copilot_hooks_dependency_error=(
                "phase4-enable-copilot-hooks-input requires phase4-enable-hooks-input=true"
            ),
            require_done_dependency_error=(
                "phase4-require-done-input requires phase4-enable-hooks-input=true"
            ),
        )
        default_trend_window = parse_int(
            args.default_trend_window,
            default=0,
            field="default-trend-window",
            minimum=0,
        )
        default_trend_min_pass_rate = parse_float(
            args.default_trend_min_pass_rate,
            default=0.8,
            field="default-trend-min-pass-rate",
        )
        default_trend_min_samples = parse_int(
            args.default_trend_min_samples,
            default=3,
            field="default-trend-min-samples",
            minimum=1,
        )
        hil_max_runtime_sec = parse_non_negative_float(
            args.hil_max_runtime_sec,
            default=0.0,
            field="hil-max-runtime-sec",
        )
        copilot_guard_hold_threshold = parse_positive_int(
            args.copilot_guard_hold_threshold,
            default=1,
            field="copilot-guard-hold-threshold",
        )
        trend_window = parse_int(
            args.trend_window,
            default=default_trend_window,
            field="trend-window",
            minimum=0,
        )
        trend_min_pass_rate = parse_float(
            args.trend_min_pass_rate,
            default=default_trend_min_pass_rate,
            field="trend-min-pass-rate",
        )
        trend_min_samples = parse_int(
            args.trend_min_samples,
            default=default_trend_min_samples,
            field="trend-min-samples",
            minimum=1,
        )
        phase4_reference_min_coverage_ratio_input = str(args.phase4_reference_min_coverage_ratio).strip()
        phase4_reference_min_coverage_ratio = parse_non_negative_float(
            phase4_reference_min_coverage_ratio_input,
            default=1.0,
            field="phase4-reference-min-coverage-ratio",
        )
        if phase4_reference_min_coverage_ratio > 1.0:
            raise ValueError(
                "phase4-reference-min-coverage-ratio must be between 0 and 1, got: "
                f"{phase4_reference_min_coverage_ratio_input or phase4_reference_min_coverage_ratio}"
            )
        phase4_reference_secondary_min_coverage_ratio_input = str(
            args.phase4_reference_secondary_min_coverage_ratio
        ).strip()
        phase4_reference_secondary_min_coverage_ratio = parse_non_negative_float(
            phase4_reference_secondary_min_coverage_ratio_input,
            default=0.0,
            field="phase4-reference-secondary-min-coverage-ratio",
        )
        if phase4_reference_secondary_min_coverage_ratio > 1.0:
            raise ValueError(
                "phase4-reference-secondary-min-coverage-ratio must be between 0 and 1, got: "
                f"{phase4_reference_secondary_min_coverage_ratio_input or phase4_reference_secondary_min_coverage_ratio}"
            )
        phase4_reference_max_scan_files_per_repo_input = str(args.phase4_reference_max_scan_files_per_repo).strip()
        phase4_reference_max_scan_files_per_repo = parse_positive_int(
            phase4_reference_max_scan_files_per_repo_input,
            default=2000,
            field="phase4-reference-max-scan-files-per-repo",
        )
        args.phase4_reference_min_coverage_ratio = (
            str(phase4_reference_min_coverage_ratio) if phase4_reference_min_coverage_ratio_input else ""
        )
        args.phase4_reference_secondary_min_coverage_ratio = (
            str(phase4_reference_secondary_min_coverage_ratio)
            if phase4_reference_secondary_min_coverage_ratio_input
            else ""
        )
        args.phase4_reference_max_scan_files_per_repo = (
            str(phase4_reference_max_scan_files_per_repo)
            if phase4_reference_max_scan_files_per_repo_input
            else ""
        )
        args.phase4_reference_repo_path = [
            value for value in (str(item).strip() for item in args.phase4_reference_repo_path) if value
        ]
        args.phase4_reference_pattern_module = resolve_phase4_reference_pattern_modules(
            args.phase4_reference_pattern_module
        )
        batch_spec, sds_versions_csv = resolve_batch_and_versions(args=args)
        release_id = resolve_release_value(
            explicit_value=str(args.release_id),
            release_id_input=str(args.release_id_input),
            fallback_prefix=str(args.release_id_fallback_prefix),
            fallback_run_id=str(args.release_id_fallback_run_id),
            fallback_run_attempt=str(args.release_id_fallback_run_attempt),
            required_field="release-id",
        )
        versions = parse_csv_items_with_fallback(sds_versions_csv, args.default_sds_versions)

        cmd = build_cmd(
            args=args,
            release_id=release_id,
            batch_spec=batch_spec,
            strict_gate=strict_gate,
            phase2_enable_hooks=phase2_enable_hooks,
            phase3_enable_hooks=phase3_enable_hooks,
            sim_runtime_probe_enable=sim_runtime_probe_enable,
            sim_runtime_probe_execute=sim_runtime_probe_execute,
            sim_runtime_probe_require_availability=sim_runtime_probe_require_availability,
            sim_runtime_scenario_contract_enable=sim_runtime_scenario_contract_enable,
            sim_runtime_scenario_contract_require_runtime_ready=(
                sim_runtime_scenario_contract_require_runtime_ready
            ),
            sim_runtime_scene_result_enable=sim_runtime_scene_result_enable,
            sim_runtime_scene_result_require_runtime_ready=(
                sim_runtime_scene_result_require_runtime_ready
            ),
            sim_runtime_interop_contract_enable=sim_runtime_interop_contract_enable,
            sim_runtime_interop_contract_require_runtime_ready=(
                sim_runtime_interop_contract_require_runtime_ready
            ),
            phase4_enable_hooks=phase4_enable_hooks,
            phase4_enable_copilot_hooks=phase4_enable_copilot_hooks,
            phase4_require_done=phase4_require_done,
            hil_max_runtime_sec=hil_max_runtime_sec,
            copilot_guard_hold_threshold=copilot_guard_hold_threshold,
            trend_window=trend_window,
            trend_min_pass_rate=trend_min_pass_rate,
            trend_min_samples=trend_min_samples,
            phase2_route_gate_require_status_pass=phase2_route_gate_require_status_pass,
            phase2_route_gate_require_routing_semantic_pass=(
                phase2_route_gate_require_routing_semantic_pass
            ),
            phase2_route_gate_min_lane_count=phase2_route_gate_min_lane_count,
            phase2_route_gate_min_total_length_m=phase2_route_gate_min_total_length_m,
            phase2_route_gate_max_routing_semantic_warning_count=(
                phase2_route_gate_max_routing_semantic_warning_count
            ),
            phase2_route_gate_max_unreachable_lane_count=phase2_route_gate_max_unreachable_lane_count,
            phase2_route_gate_max_non_reciprocal_link_warning_count=(
                phase2_route_gate_max_non_reciprocal_link_warning_count
            ),
            phase2_route_gate_max_continuity_gap_warning_count=(
                phase2_route_gate_max_continuity_gap_warning_count
            ),
            phase3_control_gate_max_overlap_ratio=phase3_control_gate_max_overlap_ratio,
            phase3_control_gate_max_steering_rate_degps=phase3_control_gate_max_steering_rate_degps,
            phase3_control_gate_max_throttle_plus_brake=phase3_control_gate_max_throttle_plus_brake,
            phase3_control_gate_max_speed_tracking_error_abs_mps=(
                phase3_control_gate_max_speed_tracking_error_abs_mps
            ),
            phase3_dataset_gate_min_run_summary_count=phase3_dataset_gate_min_run_summary_count,
            phase3_dataset_gate_min_traffic_profile_count=phase3_dataset_gate_min_traffic_profile_count,
            phase3_dataset_gate_min_actor_pattern_count=phase3_dataset_gate_min_actor_pattern_count,
            phase3_dataset_gate_min_avg_npc_count=phase3_dataset_gate_min_avg_npc_count,
            phase3_lane_risk_gate_min_ttc_same_lane_sec=phase3_lane_risk_gate_min_ttc_same_lane_sec,
            phase3_lane_risk_gate_min_ttc_adjacent_lane_sec=phase3_lane_risk_gate_min_ttc_adjacent_lane_sec,
            phase3_lane_risk_gate_min_ttc_any_lane_sec=phase3_lane_risk_gate_min_ttc_any_lane_sec,
            phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total=(
                phase3_lane_risk_gate_max_ttc_under_3s_same_lane_total
            ),
            phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total=(
                phase3_lane_risk_gate_max_ttc_under_3s_adjacent_lane_total
            ),
            phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total=(
                phase3_lane_risk_gate_max_ttc_under_3s_any_lane_total
            ),
            phase3_enable_ego_collision_avoidance=phase3_enable_ego_collision_avoidance,
            phase3_avoidance_ttc_threshold_sec=phase3_avoidance_ttc_threshold_sec,
            phase3_ego_max_brake_mps2=phase3_ego_max_brake_mps2,
            phase3_tire_friction_coeff=phase3_tire_friction_coeff,
            phase3_surface_friction_scale=phase3_surface_friction_scale,
            phase3_core_sim_gate_require_success=phase3_core_sim_gate_require_success,
            phase3_core_sim_gate_min_ttc_same_lane_sec=phase3_core_sim_gate_min_ttc_same_lane_sec,
            phase3_core_sim_gate_min_ttc_any_lane_sec=phase3_core_sim_gate_min_ttc_any_lane_sec,
            phase3_core_sim_matrix_gate_require_all_cases_success=(
                phase3_core_sim_matrix_gate_require_all_cases_success
            ),
            phase3_core_sim_matrix_gate_min_ttc_same_lane_sec=(
                phase3_core_sim_matrix_gate_min_ttc_same_lane_sec
            ),
            phase3_core_sim_matrix_gate_min_ttc_any_lane_sec=(
                phase3_core_sim_matrix_gate_min_ttc_any_lane_sec
            ),
            phase3_core_sim_matrix_gate_max_failed_cases=phase3_core_sim_matrix_gate_max_failed_cases,
            phase3_core_sim_matrix_gate_max_collision_cases=(
                phase3_core_sim_matrix_gate_max_collision_cases
            ),
            phase3_core_sim_matrix_gate_max_timeout_cases=phase3_core_sim_matrix_gate_max_timeout_cases,
            versions=versions,
        )
        failure_command = shell_join(cmd)
        print(f"[cmd] {failure_command}")

        append_github_output(github_output_file, key="release_id", value=release_id)

        manifest_path = ""
        if args.dry_run:
            print("[ok] dry-run=true")
        else:
            failure_phase = PIPELINE_PHASE_RUN_PIPELINE
            log_path = Path(args.log_path).resolve()
            pipeline_started = True
            pipeline_log_path = str(log_path)
            rc, manifest_path = run_with_streaming(cmd, log_path=log_path)
            pipeline_manifest_path = manifest_path
            if rc != 0:
                pipeline_exit_code = str(rc)
                detail = extract_failure_detail(log_path)
                if detail:
                    raise RuntimeError(f"pipeline command failed with exit code {rc}: {detail}")
                raise RuntimeError(f"pipeline command failed with exit code {rc}")

        append_github_output(github_output_file, key="manifest_path", value=manifest_path)
        print(f"[ok] ci_release_id={release_id}")
        print(f"[ok] ci_manifest_path={manifest_path}")
        return 0
    except Exception as exc:
        message = normalize_exception_message(exc)
        details: dict[str, str] = {"phase": failure_phase}
        if failure_command:
            details["command"] = failure_command
        if pipeline_started:
            details["exit_code"] = pipeline_exit_code
            details["log_path"] = pipeline_log_path
            details["manifest_path"] = pipeline_manifest_path
        emit_ci_error(
            step_summary_file=step_summary_file,
            source="run_ci_pipeline.py",
            message=message,
            details=details,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
