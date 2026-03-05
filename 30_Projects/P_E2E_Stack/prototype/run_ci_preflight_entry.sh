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

OUTPUT_PATH="$(ci_entry_require_output_path "${PHASE_RESOLVE_INPUTS}")" || exit 1
PREFLIGHT_MAKE_BIN="$(ci_trim "${PREFLIGHT_MAKE_BIN:-make}")"
PREFLIGHT_MAKE_DIR="$(ci_trim "${PREFLIGHT_MAKE_DIR:-${SCRIPT_DIR}}")"
PREFLIGHT_PHASE1_TARGET="$(ci_trim "${PREFLIGHT_PHASE1_TARGET:-phase1-regression}")"
PREFLIGHT_PHASE4_TARGET="$(ci_trim "${PREFLIGHT_PHASE4_TARGET:-phase4-regression}")"
PREFLIGHT_VALIDATE_TARGET="$(ci_trim "${PREFLIGHT_VALIDATE_TARGET:-validate}")"

if ! ci_entry_require_named_nonempty_fields \
  "PREFLIGHT_MAKE_BIN" \
  "${PREFLIGHT_MAKE_BIN}" \
  "PREFLIGHT_MAKE_DIR" \
  "${PREFLIGHT_MAKE_DIR}" \
  "PREFLIGHT_PHASE1_TARGET" \
  "${PREFLIGHT_PHASE1_TARGET}" \
  "PREFLIGHT_PHASE4_TARGET" \
  "${PREFLIGHT_PHASE4_TARGET}" \
  "PREFLIGHT_VALIDATE_TARGET" \
  "${PREFLIGHT_VALIDATE_TARGET}"; then
  exit 1
fi
if ! ci_entry_require_directory \
  "${PREFLIGHT_MAKE_DIR}" \
  "PREFLIGHT_MAKE_DIR does not exist: ${PREFLIGHT_MAKE_DIR}" \
  "${PHASE_RESOLVE_INPUTS}"; then
  exit 1
fi

phase1_outcome="skipped"
phase4_outcome="skipped"
validate_outcome="skipped"
result="PASS"
first_failed_stage="none"
first_failed_command="none"

run_stage() {
  local stage_key="$1"
  local phase="$2"
  local target="$3"
  ci_entry_set_phase "${phase}"
  local cmd=("${PREFLIGHT_MAKE_BIN}" -C "${PREFLIGHT_MAKE_DIR}" "${target}")
  local cmd_text="${PREFLIGHT_MAKE_BIN} -C ${PREFLIGHT_MAKE_DIR} ${target}"

  set +e
  "${cmd[@]}"
  local rc=$?
  set -e

  if [[ ${rc} -eq 0 ]]; then
    case "${stage_key}" in
      phase1) phase1_outcome="success" ;;
      phase4) phase4_outcome="success" ;;
      validate) validate_outcome="success" ;;
      *)
        ci_entry_report_error "unknown preflight stage key: ${stage_key}" "${phase}"
        return 1
        ;;
    esac
    return 0
  fi

  result="FAIL"
  first_failed_stage="${target}"
  first_failed_command="${cmd_text}"
  case "${stage_key}" in
    phase1) phase1_outcome="failure" ;;
    phase4) phase4_outcome="failure" ;;
    validate) validate_outcome="failure" ;;
    *)
      ci_entry_report_error "unknown preflight stage key: ${stage_key}" "${phase}"
      return 1
      ;;
  esac
  return "${rc}"
}

rc=0
if run_stage "phase1" "${SHELL_PHASE_RUN_PREFLIGHT_PHASE1}" "${PREFLIGHT_PHASE1_TARGET}"; then
  if run_stage "phase4" "${SHELL_PHASE_RUN_PREFLIGHT_PHASE4}" "${PREFLIGHT_PHASE4_TARGET}"; then
    if ! run_stage "validate" "${SHELL_PHASE_RUN_PREFLIGHT_VALIDATE}" "${PREFLIGHT_VALIDATE_TARGET}"; then
      rc=$?
    fi
  else
    rc=$?
  fi
else
  rc=$?
fi

if ! ci_entry_write_output_pairs_for_phase \
  "${SHELL_PHASE_WRITE_OUTPUTS}" \
  "${OUTPUT_PATH}" \
  "phase1_outcome" "${phase1_outcome}" \
  "phase4_outcome" "${phase4_outcome}" \
  "validate_outcome" "${validate_outcome}" \
  "result" "${result}" \
  "first_failed_stage" "${first_failed_stage}" \
  "first_failed_command" "${first_failed_command}"; then
  exit 1
fi

if [[ ${rc} -ne 0 ]]; then
  exit "${rc}"
fi
