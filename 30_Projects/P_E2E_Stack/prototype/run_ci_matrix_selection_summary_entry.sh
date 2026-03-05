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
MATRIX_SELECTION_SUMMARY_TITLE="$(ci_trim "${MATRIX_SELECTION_SUMMARY_TITLE:-Nightly Matrix Selection}")"
MATRIX_PROFILE_IDS_INPUT="${MATRIX_PROFILE_IDS:-}"
MATRIX_PROFILE_COUNT_INPUT="$(ci_trim "${MATRIX_PROFILE_COUNT:-0}")"

if ! ci_entry_require_named_nonempty \
  "MATRIX_SELECTION_SUMMARY_TITLE" \
  "${MATRIX_SELECTION_SUMMARY_TITLE}" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

if ! ci_entry_require_named_nonempty \
  "MATRIX_PROFILE_IDS" \
  "$(ci_trim "${MATRIX_PROFILE_IDS_INPUT}")" \
  "${SHELL_PHASE_PARSE_PROFILE_IDS}"; then
  exit 1
fi

if ! ci_entry_capture_matrix_profile_ids_csv_for_phase \
  "${SHELL_PHASE_PARSE_PROFILE_IDS}" \
  MATRIX_PROFILE_IDS_VALUE \
  "${MATRIX_PROFILE_IDS_INPUT}"; then
  exit 1
fi

MATRIX_PROFILE_COUNT_VALUE=""
if ! ci_entry_capture_matrix_profile_count_for_phase \
  MATRIX_PROFILE_COUNT_VALUE \
  "${SHELL_PHASE_PARSE_PROFILE_IDS}" \
  "${MATRIX_PROFILE_COUNT_INPUT}" \
  "${CI_MATRIX_PROFILE_IDS_COUNT}"; then
  exit 1
fi

if ! ci_entry_publish_summary_section_for_phase \
  "${SCRIPT_DIR}" \
  "${STEP_SUMMARY_PATH}" \
  "${SHELL_PHASE_PUBLISH_MATRIX_SELECTION_SUMMARY}" \
  "${MATRIX_SELECTION_SUMMARY_TITLE}" \
  "profile_ids" "${MATRIX_PROFILE_IDS_VALUE}" \
  "profile_count" "${MATRIX_PROFILE_COUNT_VALUE}"; then
  exit 1
fi
