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
SKIP_SUMMARY_TITLE="$(ci_trim "${SKIP_SUMMARY_TITLE:-PR Quick Skipped}")"
SKIP_REASON_VALUE="$(ci_trim "${SKIP_REASON:-unknown}")"
SKIP_MATCHED_FILE_VALUE="$(ci_trim "${SKIP_MATCHED_FILE:-}")"
SKIP_MATCHED_PATTERN_VALUE="$(ci_trim "${SKIP_MATCHED_PATTERN:-}")"

if ! ci_entry_require_named_nonempty \
  "SKIP_SUMMARY_TITLE" \
  "${SKIP_SUMMARY_TITLE}" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

if ! ci_entry_validate_enum_value \
  "SKIP_REASON" \
  "${SKIP_REASON_VALUE}" \
  "no_runtime_inputs_changed" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

if [[ -n "${SKIP_MATCHED_FILE_VALUE}" || -n "${SKIP_MATCHED_PATTERN_VALUE}" ]]; then
  ci_entry_report_error \
    "SKIP_MATCHED_FILE and SKIP_MATCHED_PATTERN must be empty when SKIP_REASON=no_runtime_inputs_changed" \
    "${PHASE_RESOLVE_INPUTS}"
  exit 1
fi

if ! ci_entry_publish_summary_section_for_phase \
  "${SCRIPT_DIR}" \
  "${STEP_SUMMARY_PATH}" \
  "${SHELL_PHASE_PUBLISH_SKIP_SUMMARY}" \
  "${SKIP_SUMMARY_TITLE}" \
  "reason" "${SKIP_REASON_VALUE}" \
  "matched_file" "${SKIP_MATCHED_FILE_VALUE}" \
  "matched_pattern" "${SKIP_MATCHED_PATTERN_VALUE}"; then
  exit 1
fi
