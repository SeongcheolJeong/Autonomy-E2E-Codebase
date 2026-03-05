#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./ci_shell_reporting.sh
source "${SCRIPT_DIR}/ci_shell_reporting.sh"
# shellcheck source=./ci_phases_shell.sh
source "${SCRIPT_DIR}/ci_phases_shell.sh"
# shellcheck source=./ci_shell_entry.sh
source "${SCRIPT_DIR}/ci_shell_entry.sh"
ci_entry_bootstrap "$(basename "$0")" "${PHASE_RESOLVE_INPUTS}"
OUTPUT_PATH="$(ci_resolve_output_path)"
STEP_SUMMARY_PATH="$(ci_resolve_step_summary_path)"

RUN_CMD=(
  python3 "${SCRIPT_DIR}/run_ci_pipeline.py"
  --batch-spec "${BATCH_SPEC:-}"
  --fallback-batch-spec "${FALLBACK_BATCH_SPEC:-}"
  --profile-file "${PROFILE_FILE:-}"
  --profile-id "${PROFILE_ID:-}"
  --profile-default-file "${PROFILE_DEFAULT_FILE:-}"
  --profile-loader "${PROFILE_LOADER:-${SCRIPT_DIR}/load_ci_matrix.py}"
  --release-id "${RELEASE_ID:-}"
  --release-id-input "${RELEASE_ID_INPUT:-}"
  --release-id-fallback-prefix "${RELEASE_ID_FALLBACK_PREFIX:-}"
  --release-id-fallback-run-id "${RELEASE_ID_FALLBACK_RUN_ID:-}"
  --release-id-fallback-run-attempt "${RELEASE_ID_FALLBACK_RUN_ATTEMPT:-}"
  --batch-out "${BATCH_OUT:-}"
  --db "${DB_PATH:-}"
  --report-dir "${REPORT_DIR:-}"
  --cloud-runner "${CLOUD_RUNNER:-}"
  --ingest-runner "${INGEST_RUNNER:-}"
  --report-runner "${REPORT_RUNNER:-}"
  --gate-profile "${GATE_PROFILE:-}"
  --requirement-map "${REQUIREMENT_MAP:-}"
  --strict-gate-input "${STRICT_GATE_INPUT:-}"
  --strict-gate-default "${STRICT_GATE_DEFAULT:-false}"
  --phase2-enable-hooks-input "${PHASE2_ENABLE_HOOKS_INPUT:-}"
  --phase2-enable-hooks-default "${PHASE2_ENABLE_HOOKS_DEFAULT:-false}"
  --phase3-enable-hooks-input "${PHASE3_ENABLE_HOOKS_INPUT:-}"
  --phase3-enable-hooks-default "${PHASE3_ENABLE_HOOKS_DEFAULT:-false}"
  --sim-runtime-probe-enable-input "${SIM_RUNTIME_PROBE_ENABLE_INPUT:-}"
  --sim-runtime-probe-enable-default "${SIM_RUNTIME_PROBE_ENABLE_DEFAULT:-false}"
  --sim-runtime-probe-execute-input "${SIM_RUNTIME_PROBE_EXECUTE_INPUT:-}"
  --sim-runtime-probe-execute-default "${SIM_RUNTIME_PROBE_EXECUTE_DEFAULT:-false}"
  --sim-runtime-probe-require-availability-input "${SIM_RUNTIME_PROBE_REQUIRE_AVAILABILITY_INPUT:-}"
  --sim-runtime-probe-require-availability-default "${SIM_RUNTIME_PROBE_REQUIRE_AVAILABILITY_DEFAULT:-false}"
  --sim-runtime-scenario-contract-enable-input "${SIM_RUNTIME_SCENARIO_CONTRACT_ENABLE_INPUT:-}"
  --sim-runtime-scenario-contract-enable-default "${SIM_RUNTIME_SCENARIO_CONTRACT_ENABLE_DEFAULT:-false}"
  --sim-runtime-scenario-contract-require-runtime-ready-input "${SIM_RUNTIME_SCENARIO_CONTRACT_REQUIRE_RUNTIME_READY_INPUT:-}"
  --sim-runtime-scenario-contract-require-runtime-ready-default "${SIM_RUNTIME_SCENARIO_CONTRACT_REQUIRE_RUNTIME_READY_DEFAULT:-false}"
  --sim-runtime-scene-result-enable-input "${SIM_RUNTIME_SCENE_RESULT_ENABLE_INPUT:-}"
  --sim-runtime-scene-result-enable-default "${SIM_RUNTIME_SCENE_RESULT_ENABLE_DEFAULT:-false}"
  --sim-runtime-scene-result-require-runtime-ready-input "${SIM_RUNTIME_SCENE_RESULT_REQUIRE_RUNTIME_READY_INPUT:-}"
  --sim-runtime-scene-result-require-runtime-ready-default "${SIM_RUNTIME_SCENE_RESULT_REQUIRE_RUNTIME_READY_DEFAULT:-false}"
  --sim-runtime-interop-contract-enable-input "${SIM_RUNTIME_INTEROP_CONTRACT_ENABLE_INPUT:-}"
  --sim-runtime-interop-contract-enable-default "${SIM_RUNTIME_INTEROP_CONTRACT_ENABLE_DEFAULT:-false}"
  --sim-runtime-interop-contract-require-runtime-ready-input "${SIM_RUNTIME_INTEROP_CONTRACT_REQUIRE_RUNTIME_READY_INPUT:-}"
  --sim-runtime-interop-contract-require-runtime-ready-default "${SIM_RUNTIME_INTEROP_CONTRACT_REQUIRE_RUNTIME_READY_DEFAULT:-false}"
  --phase4-enable-hooks-input "${PHASE4_ENABLE_HOOKS_INPUT:-}"
  --phase4-enable-hooks-default "${PHASE4_ENABLE_HOOKS_DEFAULT:-false}"
  --phase4-enable-copilot-hooks-input "${PHASE4_ENABLE_COPILOT_HOOKS_INPUT:-}"
  --phase4-enable-copilot-hooks-default "${PHASE4_ENABLE_COPILOT_HOOKS_DEFAULT:-false}"
  --phase4-require-done-input "${PHASE4_REQUIRE_DONE_INPUT:-}"
  --phase4-require-done-default "${PHASE4_REQUIRE_DONE_DEFAULT:-false}"
  --hil-sequence-runner "${HIL_SEQUENCE_RUNNER:-}"
  --hil-interface "${HIL_INTERFACE:-}"
  --hil-sequence "${HIL_SEQUENCE:-}"
  --hil-max-runtime-sec "${HIL_MAX_RUNTIME_SEC:-}"
  --hil-schedule-out "${HIL_SCHEDULE_OUT:-}"
  --adp-trace-runner "${ADP_TRACE_RUNNER:-}"
  --adp-trace-out "${ADP_TRACE_OUT:-}"
  --sim-runtime-adapter-runner "${SIM_RUNTIME_ADAPTER_RUNNER:-}"
  --sim-runtime "${SIM_RUNTIME:-}"
  --sim-runtime-scene "${SIM_RUNTIME_SCENE:-}"
  --sim-runtime-sensor-rig "${SIM_RUNTIME_SENSOR_RIG:-}"
  --sim-runtime-mode "${SIM_RUNTIME_MODE:-}"
  --sim-runtime-out "${SIM_RUNTIME_OUT:-}"
  --sim-runtime-probe-runner "${SIM_RUNTIME_PROBE_RUNNER:-}"
  --sim-runtime-probe-runtime-bin "${SIM_RUNTIME_PROBE_RUNTIME_BIN:-}"
  --sim-runtime-probe-out "${SIM_RUNTIME_PROBE_OUT:-}"
  --sim-runtime-scenario-contract-runner "${SIM_RUNTIME_SCENARIO_CONTRACT_RUNNER:-}"
  --sim-runtime-scenario-contract-out "${SIM_RUNTIME_SCENARIO_CONTRACT_OUT:-}"
  --sim-runtime-scene-result-runner "${SIM_RUNTIME_SCENE_RESULT_RUNNER:-}"
  --sim-runtime-scene-result-out "${SIM_RUNTIME_SCENE_RESULT_OUT:-}"
  --sim-runtime-interop-contract-runner "${SIM_RUNTIME_INTEROP_CONTRACT_RUNNER:-}"
  --sim-runtime-interop-export-runner "${SIM_RUNTIME_INTEROP_EXPORT_RUNNER:-}"
  --sim-runtime-interop-export-road-length-scale "${SIM_RUNTIME_INTEROP_EXPORT_ROAD_LENGTH_SCALE:-}"
  --sim-runtime-interop-export-xosc-out "${SIM_RUNTIME_INTEROP_EXPORT_XOSC_OUT:-}"
  --sim-runtime-interop-export-xodr-out "${SIM_RUNTIME_INTEROP_EXPORT_XODR_OUT:-}"
  --sim-runtime-interop-export-out "${SIM_RUNTIME_INTEROP_EXPORT_OUT:-}"
  --sim-runtime-interop-import-runner "${SIM_RUNTIME_INTEROP_IMPORT_RUNNER:-}"
  --sim-runtime-interop-import-out "${SIM_RUNTIME_INTEROP_IMPORT_OUT:-}"
  --sim-runtime-interop-import-manifest-consistency-mode "${SIM_RUNTIME_INTEROP_IMPORT_MANIFEST_CONSISTENCY_MODE:-}"
  --sim-runtime-interop-import-export-consistency-mode "${SIM_RUNTIME_INTEROP_IMPORT_EXPORT_CONSISTENCY_MODE:-}"
  --sim-runtime-interop-contract-xosc "${SIM_RUNTIME_INTEROP_CONTRACT_XOSC:-}"
  --sim-runtime-interop-contract-xodr "${SIM_RUNTIME_INTEROP_CONTRACT_XODR:-}"
  --sim-runtime-interop-contract-out "${SIM_RUNTIME_INTEROP_CONTRACT_OUT:-}"
  --phase4-linkage-runner "${PHASE4_LINKAGE_RUNNER:-}"
  --phase4-linkage-matrix "${PHASE4_LINKAGE_MATRIX:-}"
  --phase4-linkage-checklist "${PHASE4_LINKAGE_CHECKLIST:-}"
  --phase4-linkage-reference-map "${PHASE4_LINKAGE_REFERENCE_MAP:-}"
  --phase4-linkage-out "${PHASE4_LINKAGE_OUT:-}"
  --phase4-reference-pattern-runner "${PHASE4_REFERENCE_PATTERN_RUNNER:-}"
  --phase4-reference-index "${PHASE4_REFERENCE_INDEX:-}"
  --phase4-reference-repo-root "${PHASE4_REFERENCE_REPO_ROOT:-}"
  --phase4-reference-max-scan-files-per-repo "${PHASE4_REFERENCE_MAX_SCAN_FILES_PER_REPO:-}"
  --phase4-reference-min-coverage-ratio "${PHASE4_REFERENCE_PATTERN_MIN_COVERAGE_RATIO:-}"
  --phase4-reference-secondary-min-coverage-ratio "${PHASE4_REFERENCE_SECONDARY_MIN_COVERAGE_RATIO:-}"
  --phase4-reference-pattern-out "${PHASE4_REFERENCE_PATTERN_OUT:-}"
  --copilot-contract-runner "${COPILOT_CONTRACT_RUNNER:-}"
  --copilot-release-assist-runner "${COPILOT_RELEASE_ASSIST_RUNNER:-}"
  --copilot-mode "${COPILOT_MODE:-}"
  --copilot-prompt "${COPILOT_PROMPT:-}"
  --copilot-context-json "${COPILOT_CONTEXT_JSON:-}"
  --copilot-guard-hold-threshold "${COPILOT_GUARD_HOLD_THRESHOLD:-}"
  --copilot-contract-out "${COPILOT_CONTRACT_OUT:-}"
  --copilot-audit-log "${COPILOT_AUDIT_LOG:-}"
  --copilot-release-assist-out "${COPILOT_RELEASE_ASSIST_OUT:-}"
  --trend-window "${TREND_WINDOW:-}"
  --trend-min-pass-rate "${TREND_MIN_PASS_RATE:-}"
  --trend-min-samples "${TREND_MIN_SAMPLES:-}"
  --sds-versions-csv "${SDS_VERSIONS_CSV:-}"
  --default-sds-versions "${DEFAULT_SDS_VERSIONS:-}"
  --runner "${RUNNER_PATH:-${SCRIPT_DIR}/run_e2e_pipeline.py}"
  --log-path "${PIPELINE_LOG_PATH:-e2e_pipeline.log}"
  --github-output "${OUTPUT_PATH}"
  --step-summary-file "${STEP_SUMMARY_PATH}"
)

if ! ci_entry_capture_phase4_hook_effective_flags_for_phase \
  "${PHASE_RESOLVE_INPUTS}" \
  PHASE4_ENABLE_HOOKS_EFFECTIVE \
  PHASE4_ENABLE_COPILOT_HOOKS_EFFECTIVE \
  PHASE4_REQUIRE_DONE_EFFECTIVE \
  "${PHASE4_ENABLE_HOOKS_INPUT:-}" \
  "${PHASE4_ENABLE_HOOKS_DEFAULT:-false}" \
  "${PHASE4_ENABLE_COPILOT_HOOKS_INPUT:-}" \
  "${PHASE4_ENABLE_COPILOT_HOOKS_DEFAULT:-false}" \
  "${PHASE4_REQUIRE_DONE_INPUT:-}" \
  "${PHASE4_REQUIRE_DONE_DEFAULT:-false}"; then
  exit 1
fi

PHASE4_LINKAGE_MODULES_CSV=""
if ! ci_entry_capture_phase4_linkage_modules_csv_for_phase \
  "${PHASE_RESOLVE_INPUTS}" \
  PHASE4_LINKAGE_MODULES_CSV \
  "${PHASE4_LINKAGE_MODULES:-}"; then
  exit 1
fi
ci_entry_append_run_cmd_flag_from_csv_values \
  "--phase4-linkage-module" \
  "${PHASE4_LINKAGE_MODULES_CSV}"
PHASE4_REFERENCE_PATTERN_MODULES_CSV=""
if ! ci_entry_capture_phase4_reference_pattern_modules_csv_for_phase \
  "${PHASE_RESOLVE_INPUTS}" \
  PHASE4_REFERENCE_PATTERN_MODULES_CSV \
  "${PHASE4_REFERENCE_PATTERN_MODULES:-}"; then
  exit 1
fi
ci_entry_append_run_cmd_flag_from_csv_values \
  "--phase4-reference-pattern-module" \
  "${PHASE4_REFERENCE_PATTERN_MODULES_CSV}"
ci_entry_append_run_cmd_flag_from_csv_values \
  "--phase4-reference-repo-path" \
  "${PHASE4_REFERENCE_REPO_PATHS:-}"

SIM_RUNTIME_PROBE_FLAG_VALUE="$(ci_trim "${SIM_RUNTIME_PROBE_FLAG:-}")"
if [[ -n "${SIM_RUNTIME_PROBE_FLAG_VALUE}" ]]; then
  RUN_CMD+=("--sim-runtime-probe-flag=${SIM_RUNTIME_PROBE_FLAG_VALUE}")
fi
SIM_RUNTIME_PROBE_ARGS_SHLEX_VALUE="$(ci_trim "${SIM_RUNTIME_PROBE_ARGS_SHLEX:-}")"
if [[ -n "${SIM_RUNTIME_PROBE_ARGS_SHLEX_VALUE}" ]]; then
  RUN_CMD+=("--sim-runtime-probe-args-shlex=${SIM_RUNTIME_PROBE_ARGS_SHLEX_VALUE}")
fi

if ! ci_entry_append_run_cmd_flag_from_bool_input \
  "--dry-run" \
  "${CI_PIPELINE_DRY_RUN:-}" \
  "false" \
  "CI_PIPELINE_DRY_RUN" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

ci_entry_exec_delegated_for_phase "${PIPELINE_PHASE_RUN_PIPELINE}" "${RUN_CMD[@]}"
