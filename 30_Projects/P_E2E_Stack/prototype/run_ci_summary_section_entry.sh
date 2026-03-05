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
SUMMARY_SECTION_TITLE_VALUE="$(ci_trim "${SUMMARY_SECTION_TITLE:-}")"
SUMMARY_SECTION_ITEMS_RAW="${SUMMARY_SECTION_ITEMS:-}"

if ! ci_entry_require_named_nonempty \
  "SUMMARY_SECTION_TITLE" \
  "${SUMMARY_SECTION_TITLE_VALUE}" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

RUN_CMD=(
  python3 "${SCRIPT_DIR}/write_ci_summary_section.py"
  --summary-file "${STEP_SUMMARY_PATH}"
  --title "${SUMMARY_SECTION_TITLE_VALUE}"
)

if ! ci_entry_append_summary_section_items \
  "${SUMMARY_SECTION_ITEMS_RAW}" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

ci_entry_exec_delegated_for_phase "${SUMMARY_PHASE_PUBLISH_SUMMARY}" "${RUN_CMD[@]}"
