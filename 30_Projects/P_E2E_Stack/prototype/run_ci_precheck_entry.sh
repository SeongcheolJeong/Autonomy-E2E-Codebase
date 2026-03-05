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

EVENT_NAME="$(ci_trim_lower_text "${EVENT_NAME:-}")"
BASE_SHA="$(ci_trim "${BASE_SHA:-}")"
HEAD_SHA="$(ci_trim "${HEAD_SHA:-}")"
PRECHECK_RULES_FILE="$(
  ci_entry_resolve_with_default \
    "${PRECHECK_RULES_FILE:-}" \
    "${SCRIPT_DIR}/ci_profiles/pr_quick_precheck_rules.json"
)"
OUTPUT_PATH="$(ci_entry_require_output_path "${PHASE_RESOLVE_INPUTS}")" || exit 1
MATCHED_FILE=""
MATCHED_PATTERN=""

write_decision() {
  local should_run="$1"
  local reason="$2"
  local phase="${3:-}"
  if [[ -n "${phase}" ]]; then
    ci_entry_write_output_pairs_for_phase \
      "${phase}" \
      "${OUTPUT_PATH}" \
      "should_run" "${should_run}" \
      "reason" "${reason}" \
      "matched_file" "${MATCHED_FILE}" \
      "matched_pattern" "${MATCHED_PATTERN}"
  else
    ci_entry_write_output_pairs \
      "${OUTPUT_PATH}" \
      "should_run" "${should_run}" \
      "reason" "${reason}" \
      "matched_file" "${MATCHED_FILE}" \
      "matched_pattern" "${MATCHED_PATTERN}"
  fi
}

if [[ "${EVENT_NAME}" == "workflow_dispatch" ]]; then
  write_decision "true" "manual_dispatch"
  exit 0
fi

if [[ -z "${BASE_SHA}" || -z "${HEAD_SHA}" ]]; then
  write_decision "true" "missing_pr_sha"
  exit 0
fi

CHANGED_FILES_LINES="$(git diff --name-only "${BASE_SHA}" "${HEAD_SHA}")"
CHANGED_FILES=()
if ! ci_entry_capture_nonempty_lines_array_for_phase \
  "${SHELL_PHASE_DIFF_CHANGED_FILES}" \
  CHANGED_FILES \
  "${CHANGED_FILES_LINES}"; then
  exit 1
fi
printf 'Changed files (%s)\n' "${#CHANGED_FILES[@]}"
if [[ ${#CHANGED_FILES[@]} -gt 0 ]]; then
  printf '%s\n' "${CHANGED_FILES[@]}"
fi

ci_entry_capture_delegated_for_phase \
  "${SHELL_PHASE_LOAD_PRECHECK_RULES}" \
  PATTERN_LINES \
  python3 "${SCRIPT_DIR}/load_ci_precheck_patterns.py" \
  --rules-file "${PRECHECK_RULES_FILE}"
INCLUDE_PATTERNS=()
if ! ci_entry_capture_nonempty_lines_array_for_phase \
  "${SHELL_PHASE_LOAD_PRECHECK_RULES}" \
  INCLUDE_PATTERNS \
  "${PATTERN_LINES}"; then
  exit 1
fi

ci_entry_set_phase "${SHELL_PHASE_EVALUATE_CHANGES}"
SHOULD_RUN="false"
if [[ ${#CHANGED_FILES[@]} -gt 0 && ${#INCLUDE_PATTERNS[@]} -gt 0 ]]; then
  for file in "${CHANGED_FILES[@]}"; do
    for pattern in "${INCLUDE_PATTERNS[@]}"; do
      if [[ "${file}" =~ ${pattern} ]]; then
        SHOULD_RUN="true"
        MATCHED_FILE="${file}"
        MATCHED_PATTERN="${pattern}"
        break 2
      fi
    done
  done
fi

if [[ "${SHOULD_RUN}" == "true" ]]; then
  write_decision \
    "${SHOULD_RUN}" \
    "runtime_inputs_changed" \
    "${SHELL_PHASE_WRITE_OUTPUTS}"
else
  write_decision \
    "${SHOULD_RUN}" \
    "no_runtime_inputs_changed" \
    "${SHELL_PHASE_WRITE_OUTPUTS}"
fi
