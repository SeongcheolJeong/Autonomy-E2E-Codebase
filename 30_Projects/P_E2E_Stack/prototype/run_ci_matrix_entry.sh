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

MATRIX_PROFILE_FILE="$(
  ci_entry_resolve_with_default \
    "${MATRIX_PROFILE_FILE:-}" \
    "${SCRIPT_DIR}/ci_profiles/nightly_matrix_profiles.json"
)"
MATRIX_PROFILE_IDS_INPUT="${MATRIX_PROFILE_IDS:-}"
OUTPUT_PATH="$(ci_entry_require_output_path "${PHASE_RESOLVE_INPUTS}")" || exit 1

LOAD_MATRIX_CMD=(
  python3 "${SCRIPT_DIR}/load_ci_matrix.py"
  --profiles-file "${MATRIX_PROFILE_FILE}"
)
LOAD_PROFILE_IDS_CMD=(
  python3 "${SCRIPT_DIR}/load_ci_matrix.py"
  --profiles-file "${MATRIX_PROFILE_FILE}"
  --output profile-ids
)

if [[ -n "${MATRIX_PROFILE_IDS_INPUT}" ]]; then
  MATRIX_PROFILE_IDS_CSV=""
  if ! ci_entry_capture_matrix_profile_ids_csv_for_phase \
    "${SHELL_PHASE_PARSE_PROFILE_IDS}" \
    MATRIX_PROFILE_IDS_CSV \
    "${MATRIX_PROFILE_IDS_INPUT}"; then
    exit 1
  fi
  if [[ -n "${MATRIX_PROFILE_IDS_CSV}" ]]; then
    IFS=',' read -ra NORMALIZED_PROFILE_IDS <<< "${MATRIX_PROFILE_IDS_CSV}"
    for profile_id in "${NORMALIZED_PROFILE_IDS[@]}"; do
      LOAD_MATRIX_CMD+=(--profile-id "${profile_id}")
      LOAD_PROFILE_IDS_CMD+=(--profile-id "${profile_id}")
    done
  fi
fi

ci_entry_capture_delegated_for_phase \
  "${SHELL_PHASE_LOAD_MATRIX}" \
  MATRIX_JSON \
  "${LOAD_MATRIX_CMD[@]}"
ci_entry_capture_delegated_for_phase \
  "${SHELL_PHASE_LOAD_MATRIX}" \
  PROFILE_IDS_CSV \
  "${LOAD_PROFILE_IDS_CMD[@]}"
PROFILE_COUNT="$(ci_entry_count_nonempty_csv_items "${PROFILE_IDS_CSV}")"

if ! ci_entry_write_output_pairs_for_phase \
  "${SHELL_PHASE_WRITE_OUTPUTS}" \
  "${OUTPUT_PATH}" \
  "matrix" "${MATRIX_JSON}" \
  "profile_ids" "${PROFILE_IDS_CSV}" \
  "profile_count" "${PROFILE_COUNT}"; then
  exit 1
fi
