#!/usr/bin/env bash
set -euo pipefail

# Keep this file synchronized with ci_phases.py.
PHASE_RESOLVE_INPUTS="resolve_inputs"
PIPELINE_PHASE_RUN_PIPELINE="run_pipeline"
SUMMARY_PHASE_BUILD_SUMMARY="build_summary"
SUMMARY_PHASE_BUILD_NOTIFICATION_PAYLOAD="build_notification_payload"
SUMMARY_PHASE_SEND_NOTIFICATION="send_notification"
SUMMARY_PHASE_PUBLISH_SUMMARY="publish_summary"
SHELL_PHASE_DIFF_CHANGED_FILES="diff_changed_files"
SHELL_PHASE_LOAD_PRECHECK_RULES="load_precheck_rules"
SHELL_PHASE_EVALUATE_CHANGES="evaluate_changes"
SHELL_PHASE_PARSE_PROFILE_IDS="parse_profile_ids"
SHELL_PHASE_LOAD_MATRIX="load_matrix"
SHELL_PHASE_RUN_PREFLIGHT_PHASE1="run_preflight_phase1"
SHELL_PHASE_RUN_PREFLIGHT_PHASE4="run_preflight_phase4"
SHELL_PHASE_RUN_PREFLIGHT_VALIDATE="run_preflight_validate"
SHELL_PHASE_PUBLISH_PREFLIGHT_SUMMARY="publish_preflight_summary"
SHELL_PHASE_PUBLISH_SKIP_SUMMARY="publish_skip_summary"
SHELL_PHASE_PUBLISH_MATRIX_SELECTION_SUMMARY="publish_matrix_selection_summary"
SHELL_PHASE_WRITE_OUTPUTS="write_outputs"

PHASE4_LINKAGE_ALLOWED_MODULES_CSV="adp, copilot"
PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_CSV="hil_sim, adp, copilot"

ci_trim_text() {
  local value="${1:-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

ci_trim_lower_text() {
  local trimmed
  trimmed="$(ci_trim_text "${1:-}")"
  printf '%s' "$(printf '%s' "${trimmed}" | tr '[:upper:]' '[:lower:]')"
}

ci_is_true_flag() {
  local normalized
  normalized="$(ci_trim_lower_text "${1:-}")"
  [[ "${normalized}" == "true" ]]
}

ci_bool_compat_error() {
  local field="${1:-}"
  local raw="${2:-}"
  printf '%s must be true/false compatible, got: %s' "${field}" "${raw}"
}

ci_normalize_bool_flag() {
  local normalized
  normalized="$(ci_trim_lower_text "${1:-}")"
  case "${normalized}" in
    "")
      printf ''
      return 0
      ;;
    1|true|yes|y|on)
      printf 'true'
      return 0
      ;;
    0|false|no|n|off)
      printf 'false'
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ci_resolve_effective_bool_flag() {
  local raw_input="${1:-}"
  local raw_default="${2:-false}"
  local field="${3:-flag}"
  local normalized_input=""
  local normalized_default=""
  CI_EFFECTIVE_BOOL_FLAG=""
  CI_BOOL_PARSE_ERROR=""

  if ! normalized_input="$(ci_normalize_bool_flag "${raw_input}")"; then
    CI_BOOL_PARSE_ERROR="$(ci_bool_compat_error "${field}" "${raw_input}")"
    return 1
  fi

  if [[ -n "${normalized_input}" ]]; then
    CI_EFFECTIVE_BOOL_FLAG="${normalized_input}"
    return 0
  fi

  if ! normalized_default="$(ci_normalize_bool_flag "${raw_default}")"; then
    CI_BOOL_PARSE_ERROR="$(ci_bool_compat_error "${field}" "${raw_default}")"
    return 1
  fi

  if [[ -n "${normalized_default}" ]]; then
    CI_EFFECTIVE_BOOL_FLAG="${normalized_default}"
  else
    CI_EFFECTIVE_BOOL_FLAG="false"
  fi
  return 0
}

ci_array_contains_exact() {
  local needle="${1:-}"
  shift || true
  local item=""
  for item in "$@"; do
    if [[ "${item}" == "${needle}" ]]; then
      return 0
    fi
  done
  return 1
}

ci_phase4_linkage_is_allowed_module() {
  local module="${1:-}"
  case "${module}" in
    adp|copilot)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ci_phase4_linkage_error_unknown_modules() {
  local modules="$1"
  printf 'phase4-linkage-module must be one of: %s; got: %s' \
    "${PHASE4_LINKAGE_ALLOWED_MODULES_CSV}" \
    "${modules}"
}

ci_phase4_linkage_error_duplicate_modules() {
  local modules="$1"
  printf 'phase4-linkage-module contains duplicate entries: %s' "${modules}"
}

ci_phase4_linkage_error_empty_modules() {
  printf 'PHASE4_LINKAGE_MODULES must include at least one non-empty module when provided'
}

ci_phase4_reference_pattern_is_allowed_module() {
  local module="${1:-}"
  case "${module}" in
    hil_sim|adp|copilot)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ci_phase4_reference_pattern_error_unknown_modules() {
  local modules="$1"
  printf 'phase4-reference-pattern-module must be one of: %s; got: %s' \
    "${PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_CSV}" \
    "${modules}"
}

ci_phase4_reference_pattern_error_duplicate_modules() {
  local modules="$1"
  printf 'phase4-reference-pattern-module contains duplicate entries: %s' "${modules}"
}

ci_phase4_reference_pattern_error_empty_modules() {
  printf 'PHASE4_REFERENCE_PATTERN_MODULES must include at least one non-empty module when provided'
}

ci_phase4_dependency_error_copilot_hooks_require_phase4_hooks() {
  printf 'phase4-enable-copilot-hooks-input requires phase4-enable-hooks-input=true'
}

ci_phase4_dependency_error_require_done_requires_phase4_hooks() {
  printf 'phase4-require-done-input requires phase4-enable-hooks-input=true'
}

ci_phase4_validate_hook_dependencies() {
  local phase4_enable_hooks="${1:-}"
  local phase4_enable_copilot_hooks="${2:-}"
  local phase4_require_done="${3:-}"
  CI_PHASE4_DEPENDENCY_ERROR=""

  if ci_is_true_flag "${phase4_enable_copilot_hooks}" && ! ci_is_true_flag "${phase4_enable_hooks}"; then
    CI_PHASE4_DEPENDENCY_ERROR="$(ci_phase4_dependency_error_copilot_hooks_require_phase4_hooks)"
    return 1
  fi

  if ci_is_true_flag "${phase4_require_done}" && ! ci_is_true_flag "${phase4_enable_hooks}"; then
    CI_PHASE4_DEPENDENCY_ERROR="$(ci_phase4_dependency_error_require_done_requires_phase4_hooks)"
    return 1
  fi

  return 0
}

ci_collect_modules_csv_with_allow_fn() {
  local raw_input="${1:-}"
  local allow_fn="${2:-}"
  local normalized_input="${raw_input//,/ }"
  local module=""
  local normalized=""
  local is_duplicate="false"
  local has_nonempty="false"
  local -a duplicate_modules=()
  local -a duplicate_seen_modules=()
  local -a seen_modules=()
  local -a unknown_modules=()
  local -a unknown_seen_modules=()
  local -a normalized_modules=()

  for module in ${normalized_input}; do
    normalized="$(ci_trim_lower_text "${module}")"
    if [[ -z "${normalized}" ]]; then
      continue
    fi
    has_nonempty="true"
    is_duplicate="false"
    if ci_array_contains_exact "${normalized}" "${seen_modules[@]-}"; then
      is_duplicate="true"
      if ! ci_array_contains_exact "${normalized}" "${duplicate_seen_modules[@]-}"; then
        duplicate_modules+=("${normalized}")
        duplicate_seen_modules+=("${normalized}")
      fi
    else
      seen_modules+=("${normalized}")
    fi
    if ! "${allow_fn}" "${normalized}"; then
      if ! ci_array_contains_exact "${normalized}" "${unknown_seen_modules[@]-}"; then
        unknown_modules+=("${normalized}")
        unknown_seen_modules+=("${normalized}")
      fi
      continue
    fi
    if [[ "${is_duplicate}" != "true" ]]; then
      normalized_modules+=("${normalized}")
    fi
  done

  CI_COLLECTED_MODULES_HAS_NONEMPTY="${has_nonempty}"
  CI_COLLECTED_MODULES_DUPLICATE_MODULES="${duplicate_modules[*]-}"
  CI_COLLECTED_MODULES_UNKNOWN_MODULES="${unknown_modules[*]-}"
  CI_COLLECTED_MODULES_NORMALIZED_CSV="$(
    IFS=','
    printf '%s' "${normalized_modules[*]-}"
  )"
}

ci_phase4_linkage_collect_modules() {
  local raw_input="${1:-}"
  ci_collect_modules_csv_with_allow_fn "${raw_input}" ci_phase4_linkage_is_allowed_module
  CI_PHASE4_LINKAGE_HAS_NONEMPTY="${CI_COLLECTED_MODULES_HAS_NONEMPTY}"
  CI_PHASE4_LINKAGE_DUPLICATE_MODULES="${CI_COLLECTED_MODULES_DUPLICATE_MODULES}"
  CI_PHASE4_LINKAGE_UNKNOWN_MODULES="${CI_COLLECTED_MODULES_UNKNOWN_MODULES}"
  CI_PHASE4_LINKAGE_NORMALIZED_MODULES_CSV="${CI_COLLECTED_MODULES_NORMALIZED_CSV}"
}

ci_phase4_reference_pattern_collect_modules() {
  local raw_input="${1:-}"
  ci_collect_modules_csv_with_allow_fn "${raw_input}" ci_phase4_reference_pattern_is_allowed_module
  CI_PHASE4_REFERENCE_PATTERN_HAS_NONEMPTY="${CI_COLLECTED_MODULES_HAS_NONEMPTY}"
  CI_PHASE4_REFERENCE_PATTERN_DUPLICATE_MODULES="${CI_COLLECTED_MODULES_DUPLICATE_MODULES}"
  CI_PHASE4_REFERENCE_PATTERN_UNKNOWN_MODULES="${CI_COLLECTED_MODULES_UNKNOWN_MODULES}"
  CI_PHASE4_REFERENCE_PATTERN_NORMALIZED_MODULES_CSV="${CI_COLLECTED_MODULES_NORMALIZED_CSV}"
}

ci_matrix_profile_ids_error_empty() {
  printf 'MATRIX_PROFILE_IDS must include at least one non-empty profile id when provided'
}

ci_matrix_profile_ids_error_duplicates() {
  local profile_ids="$1"
  printf 'MATRIX_PROFILE_IDS contains duplicate entries: %s' "${profile_ids}"
}

ci_matrix_profile_count_error_non_negative_integer() {
  printf 'MATRIX_PROFILE_COUNT must be a non-negative integer'
}

ci_matrix_profile_count_error_mismatch() {
  local expected_count="$1"
  local actual_count="$2"
  printf 'MATRIX_PROFILE_COUNT must match MATRIX_PROFILE_IDS count: expected %s, got %s' \
    "${expected_count}" \
    "${actual_count}"
}

ci_matrix_profile_ids_collect_unique() {
  local raw_csv="${1:-}"
  local profile_id=""
  local trimmed=""
  local has_nonempty="false"
  local -a duplicate_profile_ids=()
  local -a seen_profile_ids=()
  local -a normalized_profile_ids=()
  local -a profile_id_items=()
  IFS=',' read -ra profile_id_items <<< "${raw_csv}"
  for profile_id in "${profile_id_items[@]}"; do
    trimmed="$(ci_trim_text "${profile_id}")"
    if [[ -z "${trimmed}" ]]; then
      continue
    fi
    has_nonempty="true"
    if ci_array_contains_exact "${trimmed}" "${seen_profile_ids[@]-}"; then
      if ! ci_array_contains_exact "${trimmed}" "${duplicate_profile_ids[@]-}"; then
        duplicate_profile_ids+=("${trimmed}")
      fi
      continue
    fi
    seen_profile_ids+=("${trimmed}")
    normalized_profile_ids+=("${trimmed}")
  done

  CI_MATRIX_PROFILE_IDS_HAS_NONEMPTY="${has_nonempty}"
  CI_MATRIX_PROFILE_IDS_COUNT="${#normalized_profile_ids[@]}"
  CI_MATRIX_PROFILE_IDS_DUPLICATES="${duplicate_profile_ids[*]-}"
  CI_MATRIX_PROFILE_IDS_NORMALIZED_CSV="$(
    IFS=','
    printf '%s' "${normalized_profile_ids[*]-}"
  )"
}
