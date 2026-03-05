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

STEP_SUMMARY_PATH="$(ci_entry_require_step_summary_path "${PHASE_RESOLVE_INPUTS}")" || exit 1
PREFLIGHT_SUMMARY_TITLE="$(ci_trim "${PREFLIGHT_SUMMARY_TITLE:-CI Preflight Stages}")"
PHASE1_OUTCOME_VALUE="$(ci_trim "${PHASE1_OUTCOME:-unknown}")"
PHASE4_OUTCOME_VALUE="$(ci_trim "${PHASE4_OUTCOME:-unknown}")"
VALIDATE_OUTCOME_VALUE="$(ci_trim "${VALIDATE_OUTCOME:-unknown}")"
PREFLIGHT_RESULT_VALUE="$(ci_trim "${PREFLIGHT_RESULT:-unknown}")"
FIRST_FAILED_STAGE_VALUE="$(ci_trim "${FIRST_FAILED_STAGE:-unknown}")"
FIRST_FAILED_COMMAND_VALUE="$(ci_trim "${FIRST_FAILED_COMMAND:-unknown}")"

if ! ci_entry_require_named_nonempty \
  "PREFLIGHT_SUMMARY_TITLE" \
  "${PREFLIGHT_SUMMARY_TITLE}" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

if ! ci_entry_validate_enum_fields \
  "PHASE1_OUTCOME" \
  "${PHASE1_OUTCOME_VALUE}" \
  "success failure skipped unknown" \
  "PHASE4_OUTCOME" \
  "${PHASE4_OUTCOME_VALUE}" \
  "success failure skipped unknown" \
  "VALIDATE_OUTCOME" \
  "${VALIDATE_OUTCOME_VALUE}" \
  "success failure skipped unknown" \
  "PREFLIGHT_RESULT" \
  "${PREFLIGHT_RESULT_VALUE}" \
  "PASS FAIL unknown"; then
  exit 1
fi

if ! ci_entry_validate_preflight_result_metadata \
  "${PREFLIGHT_RESULT_VALUE}" \
  "${FIRST_FAILED_STAGE_VALUE}" \
  "${FIRST_FAILED_COMMAND_VALUE}" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

if ! ci_entry_publish_summary_section_for_phase \
  "${SCRIPT_DIR}" \
  "${STEP_SUMMARY_PATH}" \
  "${SHELL_PHASE_PUBLISH_PREFLIGHT_SUMMARY}" \
  "${PREFLIGHT_SUMMARY_TITLE}" \
  "phase1_regression" "${PHASE1_OUTCOME_VALUE}" \
  "phase4_regression" "${PHASE4_OUTCOME_VALUE}" \
  "validate" "${VALIDATE_OUTCOME_VALUE}" \
  "result" "${PREFLIGHT_RESULT_VALUE}" \
  "first_failed_stage" "${FIRST_FAILED_STAGE_VALUE}" \
  "first_failed_command" "${FIRST_FAILED_COMMAND_VALUE}"; then
  exit 1
fi
