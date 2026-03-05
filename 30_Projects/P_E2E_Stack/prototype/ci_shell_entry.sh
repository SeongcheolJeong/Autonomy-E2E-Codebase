#!/usr/bin/env bash
set -euo pipefail

ci_entry_bootstrap() {
  local script_name="$1"
  local initial_phase="${2:-resolve_inputs}"

  SCRIPT_NAME="${script_name}"
  CURRENT_PHASE="${initial_phase}"
  ERROR_REPORTED="false"

  ci_entry_report_error() {
    local message="$1"
    local phase="${2:-${CURRENT_PHASE}}"
    ERROR_REPORTED="true"
    ci_emit_error "${SCRIPT_NAME}" "${message}" "${phase}"
  }

  ci_entry_set_phase() {
    local phase="$1"
    CURRENT_PHASE="${phase}"
  }

  ci_entry_require_nonempty() {
    local value="$1"
    local message="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local trimmed
    trimmed="$(ci_trim "${value}")"
    if [[ -n "${trimmed}" ]]; then
      return 0
    fi
    ci_entry_report_error "${message}" "${phase}"
    return 1
  }

  ci_entry_require_named_nonempty() {
    local field_name="$1"
    local value="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    ci_entry_require_nonempty "${value}" "${field_name} must be non-empty" "${phase}"
  }

  ci_entry_require_named_nonempty_fields() {
    local phase="${CURRENT_PHASE}"
    local field_name=""
    local value=""
    if [[ $# -eq 0 || $(( $# % 2 )) -ne 0 ]]; then
      ci_entry_report_error \
        "named non-empty validator requires field/value pairs" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      field_name="$1"
      value="$2"
      shift 2
      if ! ci_entry_require_named_nonempty "${field_name}" "${value}" "${phase}"; then
        return 1
      fi
    done
  }

  ci_entry_validate_enum_value() {
    local field_name="$1"
    local value="$2"
    local allowed_values="$3"
    local phase="${4:-${CURRENT_PHASE}}"
    local allowed=""
    for allowed in ${allowed_values}; do
      if [[ "${allowed}" == "${value}" ]]; then
        return 0
      fi
    done
    ci_entry_report_error "${field_name} must be one of: ${allowed_values}" "${phase}"
    return 1
  }

  ci_entry_validate_enum_fields() {
    local phase="${CURRENT_PHASE}"
    local field_name=""
    local value=""
    local allowed_values=""
    if [[ $# -eq 0 || $(( $# % 3 )) -ne 0 ]]; then
      ci_entry_report_error \
        "enum field validator requires field/value/allowed triplets" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      field_name="$1"
      value="$2"
      allowed_values="$3"
      shift 3
      if ! ci_entry_validate_enum_value "${field_name}" "${value}" "${allowed_values}" "${phase}"; then
        return 1
      fi
    done
  }

  ci_entry_validate_non_negative_int_value() {
    local field_name="$1"
    local value="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local trimmed=""
    trimmed="$(ci_trim "${value}")"
    if [[ ! "${trimmed}" =~ ^[0-9]+$ ]]; then
      ci_entry_report_error "${field_name} must be a non-negative integer" "${phase}"
      return 1
    fi
    printf '%s' "${trimmed}"
  }

  ci_entry_validate_non_negative_float_value() {
    local field_name="$1"
    local value="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local trimmed=""
    local parse_rc=0
    trimmed="$(ci_trim "${value}")"
    if python3 - "${trimmed}" <<'PY'
import sys

raw = sys.argv[1]
try:
    parsed = float(raw)
except ValueError:
    raise SystemExit(1)
if parsed < 0:
    raise SystemExit(2)
PY
    then
      printf '%s' "${trimmed}"
      return 0
    else
      parse_rc=$?
    fi
    if [[ "${parse_rc}" -eq 1 ]]; then
      ci_entry_report_error "${field_name} must be a number" "${phase}"
      return 1
    fi
    if [[ "${parse_rc}" -eq 2 ]]; then
      ci_entry_report_error "${field_name} must be >= 0" "${phase}"
      return 1
    fi
    ci_entry_report_error "${field_name} numeric validation failed" "${phase}"
    return 1
  }

  ci_entry_validate_positive_float_value() {
    local field_name="$1"
    local value="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local trimmed=""
    local parse_rc=0
    trimmed="$(ci_trim "${value}")"
    if python3 - "${trimmed}" <<'PY'
import sys

raw = sys.argv[1]
try:
    parsed = float(raw)
except ValueError:
    raise SystemExit(1)
if parsed <= 0:
    raise SystemExit(2)
PY
    then
      printf '%s' "${trimmed}"
      return 0
    else
      parse_rc=$?
    fi
    if [[ "${parse_rc}" -eq 1 ]]; then
      ci_entry_report_error "${field_name} must be a number" "${phase}"
      return 1
    fi
    if [[ "${parse_rc}" -eq 2 ]]; then
      ci_entry_report_error "${field_name} must be > 0" "${phase}"
      return 1
    fi
    ci_entry_report_error "${field_name} numeric validation failed" "${phase}"
    return 1
  }

  ci_entry_validate_float_at_most_value() {
    local field_name="$1"
    local value="$2"
    local upper_bound="$3"
    local phase="${4:-${CURRENT_PHASE}}"
    local trimmed=""
    local parse_rc=0
    trimmed="$(ci_trim "${value}")"
    if python3 - "${trimmed}" "${upper_bound}" <<'PY'
import sys

raw = sys.argv[1]
upper_raw = sys.argv[2]
try:
    parsed = float(raw)
except ValueError:
    raise SystemExit(1)
try:
    upper = float(upper_raw)
except ValueError:
    raise SystemExit(3)
if parsed > upper:
    raise SystemExit(2)
PY
    then
      printf '%s' "${trimmed}"
      return 0
    else
      parse_rc=$?
    fi
    if [[ "${parse_rc}" -eq 1 ]]; then
      ci_entry_report_error "${field_name} must be a number" "${phase}"
      return 1
    fi
    if [[ "${parse_rc}" -eq 2 ]]; then
      ci_entry_report_error "${field_name} must be <= ${upper_bound}" "${phase}"
      return 1
    fi
    ci_entry_report_error "${field_name} numeric upper-bound validation failed" "${phase}"
    return 1
  }

  ci_entry_validate_phase4_module_thresholds_csv() {
    local field_name="$1"
    local raw_value="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local value=""
    local item_raw=""
    local item=""
    local module_raw=""
    local ratio_raw=""
    local module=""
    local validated_ratio=""
    local normalized_csv=""
    local has_nonempty="false"
    local -a parsed_items=()
    local -a seen_modules=()
    local -a normalized_items=()

    value="$(ci_trim "${raw_value}")"
    if [[ -z "${value}" ]]; then
      printf '%s' ""
      return 0
    fi

    IFS=',' read -ra parsed_items <<< "${value}"
    for item_raw in "${parsed_items[@]}"; do
      item="$(ci_trim "${item_raw}")"
      if [[ -z "${item}" ]]; then
        continue
      fi
      has_nonempty="true"
      if [[ "${item}" != *=* ]]; then
        ci_entry_report_error \
          "${field_name} items must use module=ratio format; got: ${item_raw}" \
          "${phase}"
        return 1
      fi
      module_raw="${item%%=*}"
      ratio_raw="${item#*=}"
      module="$(ci_trim_lower_text "${module_raw}")"
      if [[ -z "${module}" ]]; then
        ci_entry_report_error "${field_name} module must be non-empty" "${phase}"
        return 1
      fi
      if ! ci_phase4_reference_pattern_is_allowed_module "${module}"; then
        ci_entry_report_error \
          "${field_name} module must be one of: ${PHASE4_REFERENCE_PATTERN_ALLOWED_MODULES_CSV}; got: ${module}" \
          "${phase}"
        return 1
      fi
      if ci_array_contains_exact "${module}" "${seen_modules[@]-}"; then
        ci_entry_report_error \
          "${field_name} contains duplicate module entry: ${module}" \
          "${phase}"
        return 1
      fi
      seen_modules+=("${module}")
      if ! validated_ratio="$(
        ci_entry_validate_non_negative_float_value \
          "${field_name}[${module}]" \
          "${ratio_raw}" \
          "${phase}"
      )"; then
        return 1
      fi
      if ! validated_ratio="$(
        ci_entry_validate_float_at_most_value \
          "${field_name}[${module}]" \
          "${validated_ratio}" \
          "1" \
          "${phase}"
      )"; then
        return 1
      fi
      normalized_items+=("${module}=${validated_ratio}")
    done

    if [[ "${has_nonempty}" != "true" ]]; then
      printf '%s' ""
      return 0
    fi

    normalized_csv="$(
      IFS=','
      printf '%s' "${normalized_items[*]-}"
    )"
    printf '%s' "${normalized_csv}"
  }

  ci_entry_validate_non_negative_float_fields() {
    local phase="${CURRENT_PHASE}"
    local field_name=""
    local value_var_name=""
    local value=""
    local validated_value=""
    if [[ $# -eq 0 || $(( $# % 2 )) -ne 0 ]]; then
      ci_entry_report_error \
        "non-negative float field validator requires field/value-variable pairs" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      field_name="$1"
      value_var_name="$2"
      shift 2
      if [[ ! "${value_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        ci_entry_report_error \
          "non-negative float field validator requires valid value variable names: ${value_var_name}" \
          "${phase}"
        return 1
      fi
      value="${!value_var_name-}"
      if [[ -z "${value}" ]]; then
        continue
      fi
      if ! validated_value="$(
        ci_entry_validate_non_negative_float_value \
          "${field_name}" \
          "${value}" \
          "${phase}"
      )"; then
        return 1
      fi
      printf -v "${value_var_name}" '%s' "${validated_value}"
    done
  }

  ci_entry_validate_non_negative_int_fields() {
    local phase="${CURRENT_PHASE}"
    local field_name=""
    local value_var_name=""
    local value=""
    local validated_value=""
    if [[ $# -eq 0 || $(( $# % 2 )) -ne 0 ]]; then
      ci_entry_report_error \
        "non-negative int field validator requires field/value-variable pairs" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      field_name="$1"
      value_var_name="$2"
      shift 2
      if [[ ! "${value_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        ci_entry_report_error \
          "non-negative int field validator requires valid value variable names: ${value_var_name}" \
          "${phase}"
        return 1
      fi
      value="${!value_var_name-}"
      if [[ -z "${value}" ]]; then
        continue
      fi
      if ! validated_value="$(
        ci_entry_validate_non_negative_int_value \
          "${field_name}" \
          "${value}" \
          "${phase}"
      )"; then
        return 1
      fi
      printf -v "${value_var_name}" '%s' "${validated_value}"
    done
  }

  ci_entry_validate_float_at_most_fields() {
    local phase="${CURRENT_PHASE}"
    local field_name=""
    local value_var_name=""
    local upper_bound=""
    local value=""
    local validated_value=""
    if [[ $# -eq 0 || $(( $# % 3 )) -ne 0 ]]; then
      ci_entry_report_error \
        "float upper-bound validator requires field/value-variable/upper-bound triplets" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      field_name="$1"
      value_var_name="$2"
      upper_bound="$3"
      shift 3
      if [[ ! "${value_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        ci_entry_report_error \
          "float upper-bound validator requires valid value variable names: ${value_var_name}" \
          "${phase}"
        return 1
      fi
      value="${!value_var_name-}"
      if [[ -z "${value}" ]]; then
        continue
      fi
      if ! validated_value="$(
        ci_entry_validate_float_at_most_value \
          "${field_name}" \
          "${value}" \
          "${upper_bound}" \
          "${phase}"
      )"; then
        return 1
      fi
      printf -v "${value_var_name}" '%s' "${validated_value}"
    done
  }

  ci_entry_validate_threshold_pair_relation() {
    local warn_field="$1"
    local warn_value="$2"
    local hold_field="$3"
    local hold_value="$4"
    local relation="$5"
    local phase="${6:-${CURRENT_PHASE}}"
    local relation_rc=0

    if python3 - "${warn_value}" "${hold_value}" "${relation}" <<'PY'
import sys

warn = float(sys.argv[1])
hold = float(sys.argv[2])
relation = sys.argv[3]

# A zero threshold means "disabled" in notify inputs; skip pair-order checks.
if warn <= 0 or hold <= 0:
    raise SystemExit(0)

if relation == "hold_gte_warn":
    if hold < warn:
        raise SystemExit(2)
    raise SystemExit(0)

if relation == "hold_lte_warn":
    if hold > warn:
        raise SystemExit(2)
    raise SystemExit(0)

raise SystemExit(3)
PY
    then
      return 0
    else
      relation_rc=$?
    fi

    if [[ "${relation_rc}" -eq 2 ]]; then
      if [[ "${relation}" == "hold_gte_warn" ]]; then
        ci_entry_report_error \
          "${hold_field} must be >= ${warn_field} when both thresholds are > 0" \
          "${phase}"
        return 1
      fi
      if [[ "${relation}" == "hold_lte_warn" ]]; then
        ci_entry_report_error \
          "${hold_field} must be <= ${warn_field} when both thresholds are > 0" \
          "${phase}"
        return 1
      fi
    fi

    ci_entry_report_error \
      "threshold pair relation validation failed for ${warn_field}/${hold_field}" \
      "${phase}"
    return 1
  }

  ci_entry_validate_hold_not_less_than_warn_fields() {
    local phase="${CURRENT_PHASE}"
    local warn_field=""
    local warn_var_name=""
    local hold_field=""
    local hold_var_name=""
    local warn_value=""
    local hold_value=""
    if [[ $# -eq 0 || $(( $# % 4 )) -ne 0 ]]; then
      ci_entry_report_error \
        "hold>=warn validator requires warn-field/warn-value-var/hold-field/hold-value-var quartets" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      warn_field="$1"
      warn_var_name="$2"
      hold_field="$3"
      hold_var_name="$4"
      shift 4
      if [[ ! "${warn_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ || ! "${hold_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        ci_entry_report_error \
          "hold>=warn validator requires valid value variable names: ${warn_var_name}/${hold_var_name}" \
          "${phase}"
        return 1
      fi
      warn_value="${!warn_var_name-}"
      hold_value="${!hold_var_name-}"
      if [[ -z "${warn_value}" || -z "${hold_value}" ]]; then
        continue
      fi
      if ! ci_entry_validate_threshold_pair_relation \
        "${warn_field}" \
        "${warn_value}" \
        "${hold_field}" \
        "${hold_value}" \
        "hold_gte_warn" \
        "${phase}"; then
        return 1
      fi
    done
  }

  ci_entry_validate_hold_not_greater_than_warn_fields() {
    local phase="${CURRENT_PHASE}"
    local warn_field=""
    local warn_var_name=""
    local hold_field=""
    local hold_var_name=""
    local warn_value=""
    local hold_value=""
    if [[ $# -eq 0 || $(( $# % 4 )) -ne 0 ]]; then
      ci_entry_report_error \
        "hold<=warn validator requires warn-field/warn-value-var/hold-field/hold-value-var quartets" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      warn_field="$1"
      warn_var_name="$2"
      hold_field="$3"
      hold_var_name="$4"
      shift 4
      if [[ ! "${warn_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ || ! "${hold_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        ci_entry_report_error \
          "hold<=warn validator requires valid value variable names: ${warn_var_name}/${hold_var_name}" \
          "${phase}"
        return 1
      fi
      warn_value="${!warn_var_name-}"
      hold_value="${!hold_var_name-}"
      if [[ -z "${warn_value}" || -z "${hold_value}" ]]; then
        continue
      fi
      if ! ci_entry_validate_threshold_pair_relation \
        "${warn_field}" \
        "${warn_value}" \
        "${hold_field}" \
        "${hold_value}" \
        "hold_lte_warn" \
        "${phase}"; then
        return 1
      fi
    done
  }

  ci_entry_resolve_with_default() {
    local value="$1"
    local fallback="$2"
    local trimmed
    trimmed="$(ci_trim "${value}")"
    if [[ -n "${trimmed}" ]]; then
      printf '%s' "${trimmed}"
      return 0
    fi
    ci_trim "${fallback}"
  }

  ci_entry_capture_resolved_with_defaults() {
    local phase="${CURRENT_PHASE}"
    local output_var_name=""
    local value=""
    local fallback=""
    local resolved_value=""
    if [[ $# -eq 0 || $(( $# % 3 )) -ne 0 ]]; then
      ci_entry_report_error \
        "default resolver capture helper requires output/value/fallback triplets" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      output_var_name="$1"
      value="$2"
      fallback="$3"
      shift 3
      if [[ ! "${output_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
        ci_entry_report_error \
          "default resolver capture helper output variable name is invalid: ${output_var_name}" \
          "${phase}"
        return 1
      fi
      resolved_value="$(ci_entry_resolve_with_default "${value}" "${fallback}")"
      printf -v "${output_var_name}" '%s' "${resolved_value}"
    done
  }

  ci_entry_resolve_effective_bool() {
    local raw_input="$1"
    local raw_default="$2"
    local field_name="$3"
    local phase="${4:-${CURRENT_PHASE}}"
    if ! ci_resolve_effective_bool_flag "${raw_input}" "${raw_default}" "${field_name}"; then
      ci_entry_report_error "${CI_BOOL_PARSE_ERROR}" "${phase}"
      return 1
    fi
    printf '%s' "${CI_EFFECTIVE_BOOL_FLAG}"
  }

  ci_entry_capture_effective_bool() {
    local output_var_name="$1"
    local raw_input="$2"
    local raw_default="$3"
    local field_name="$4"
    local phase="${5:-${CURRENT_PHASE}}"
    local resolved_bool=""
    if ! resolved_bool="$(
      ci_entry_resolve_effective_bool \
        "${raw_input}" \
        "${raw_default}" \
        "${field_name}" \
        "${phase}"
    )"; then
      return 1
    fi
    printf -v "${output_var_name}" '%s' "${resolved_bool}"
  }

  ci_entry_append_optional_run_cmd_flag_value() {
    local flag="$1"
    local value="$2"
    if [[ -n "${value}" ]]; then
      RUN_CMD+=("${flag}" "${value}")
    fi
  }

  ci_entry_append_optional_run_cmd_flag_pairs() {
    local phase="${CURRENT_PHASE}"
    local flag=""
    local value=""
    if [[ $(( $# % 2 )) -ne 0 ]]; then
      ci_entry_report_error \
        "optional run command flag pair helper requires flag/value pairs" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      flag="$1"
      value="$2"
      shift 2
      ci_entry_append_optional_run_cmd_flag_value "${flag}" "${value}"
    done
  }

  ci_entry_append_run_cmd_flag_if_true() {
    local flag="$1"
    local bool_value="$2"
    if ci_is_true_flag "${bool_value}"; then
      RUN_CMD+=("${flag}")
    fi
  }

  ci_entry_append_run_cmd_flag_from_csv_values() {
    local flag="$1"
    local csv_values="$2"
    local item=""
    local trimmed_item=""
    local -a parsed_values=()
    if [[ -z "${csv_values}" ]]; then
      return 0
    fi
    IFS=',' read -ra parsed_values <<< "${csv_values}"
    for item in "${parsed_values[@]}"; do
      trimmed_item="$(ci_trim "${item}")"
      if [[ -n "${trimmed_item}" ]]; then
        RUN_CMD+=("${flag}" "${trimmed_item}")
      fi
    done
  }

  ci_entry_append_run_cmd_flag_from_bool_input() {
    local flag="$1"
    local raw_input="$2"
    local raw_default="$3"
    local field_name="$4"
    local phase="${5:-${CURRENT_PHASE}}"
    local effective_bool=""
    if ! ci_entry_capture_effective_bool \
      effective_bool \
      "${raw_input}" \
      "${raw_default}" \
      "${field_name}" \
      "${phase}"; then
      return 1
    fi
    ci_entry_append_run_cmd_flag_if_true "${flag}" "${effective_bool}"
  }

  ci_entry_append_summary_section_items() {
    local raw_items="$1"
    local phase="${2:-${CURRENT_PHASE}}"
    local item_count=0
    local raw_line=""
    local line=""
    local key_part=""
    while IFS= read -r raw_line; do
      line="$(ci_trim "${raw_line}")"
      if [[ -z "${line}" ]]; then
        continue
      fi
      if [[ "${line}" != *=* ]]; then
        ci_entry_report_error \
          "SUMMARY_SECTION_ITEMS lines must use key=value format: ${line}" \
          "${phase}"
        return 1
      fi
      key_part="$(ci_trim "${line%%=*}")"
      if [[ -z "${key_part}" ]]; then
        ci_entry_report_error \
          "SUMMARY_SECTION_ITEMS lines must include a non-empty key before '=': ${line}" \
          "${phase}"
        return 1
      fi
      ci_entry_append_optional_run_cmd_flag_value "--item" "${line}"
      item_count=$((item_count + 1))
    done <<< "${raw_items}"

    if [[ ${item_count} -eq 0 ]]; then
      ci_entry_report_error "SUMMARY_SECTION_ITEMS must include at least one key=value line" "${phase}"
      return 1
    fi
  }

  ci_entry_build_summary_section_items() {
    local output_var_name="$1"
    shift
    local key=""
    local value=""
    local rendered_items=""
    if [[ $# -eq 0 || $(( $# % 2 )) -ne 0 ]]; then
      ci_entry_report_error \
        "summary section item builder requires key/value pairs" \
        "${CURRENT_PHASE}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      key="$1"
      value="$2"
      shift 2
      if [[ -n "${rendered_items}" ]]; then
        rendered_items+=$'\n'
      fi
      rendered_items+="${key}=${value}"
    done
    printf -v "${output_var_name}" '%s' "${rendered_items}"
  }

  ci_entry_validate_preflight_result_metadata() {
    local preflight_result="$1"
    local first_failed_stage="$2"
    local first_failed_command="$3"
    local phase="${4:-${CURRENT_PHASE}}"

    if [[ "${preflight_result}" == "PASS" ]]; then
      if [[ "${first_failed_stage}" != "none" || "${first_failed_command}" != "none" ]]; then
        ci_entry_report_error \
          "FIRST_FAILED_STAGE and FIRST_FAILED_COMMAND must be none when PREFLIGHT_RESULT=PASS" \
          "${phase}"
        return 1
      fi
      return 0
    fi

    if [[ "${preflight_result}" == "FAIL" ]]; then
      if [[ "${first_failed_stage}" == "none" || "${first_failed_stage}" == "unknown" || "${first_failed_command}" == "none" || "${first_failed_command}" == "unknown" ]]; then
        ci_entry_report_error \
          "FIRST_FAILED_STAGE and FIRST_FAILED_COMMAND must be concrete values when PREFLIGHT_RESULT=FAIL" \
          "${phase}"
        return 1
      fi
      return 0
    fi

    if [[ "${preflight_result}" == "unknown" ]]; then
      if [[ "${first_failed_stage}" != "unknown" || "${first_failed_command}" != "unknown" ]]; then
        ci_entry_report_error \
          "FIRST_FAILED_STAGE and FIRST_FAILED_COMMAND must be unknown when PREFLIGHT_RESULT=unknown" \
          "${phase}"
        return 1
      fi
    fi
  }

  ci_entry_capture_phase4_hook_effective_flags() {
    local output_enable_hooks_var="$1"
    local output_enable_copilot_hooks_var="$2"
    local output_require_done_var="$3"
    local enable_hooks_input="$4"
    local enable_hooks_default="$5"
    local enable_copilot_hooks_input="$6"
    local enable_copilot_hooks_default="$7"
    local require_done_input="$8"
    local require_done_default="$9"
    local phase="${10:-${CURRENT_PHASE}}"
    local effective_enable_hooks=""
    local effective_enable_copilot_hooks=""
    local effective_require_done=""

    if ! ci_entry_capture_effective_bool \
      effective_enable_hooks \
      "${enable_hooks_input}" \
      "${enable_hooks_default}" \
      "phase4-enable-hooks-input" \
      "${phase}"; then
      return 1
    fi

    if ! ci_entry_capture_effective_bool \
      effective_enable_copilot_hooks \
      "${enable_copilot_hooks_input}" \
      "${enable_copilot_hooks_default}" \
      "phase4-enable-copilot-hooks-input" \
      "${phase}"; then
      return 1
    fi

    if ! ci_entry_capture_effective_bool \
      effective_require_done \
      "${require_done_input}" \
      "${require_done_default}" \
      "phase4-require-done-input" \
      "${phase}"; then
      return 1
    fi

    if ! ci_phase4_validate_hook_dependencies \
      "${effective_enable_hooks}" \
      "${effective_enable_copilot_hooks}" \
      "${effective_require_done}"; then
      ci_entry_report_error \
        "${CI_PHASE4_DEPENDENCY_ERROR}" \
        "${phase}"
      return 1
    fi

    printf -v "${output_enable_hooks_var}" '%s' "${effective_enable_hooks}"
    printf -v "${output_enable_copilot_hooks_var}" '%s' "${effective_enable_copilot_hooks}"
    printf -v "${output_require_done_var}" '%s' "${effective_require_done}"
  }

  ci_entry_capture_phase4_hook_effective_flags_for_phase() {
    local phase="$1"
    local output_enable_hooks_var="$2"
    local output_enable_copilot_hooks_var="$3"
    local output_require_done_var="$4"
    local enable_hooks_input="$5"
    local enable_hooks_default="$6"
    local enable_copilot_hooks_input="$7"
    local enable_copilot_hooks_default="$8"
    local require_done_input="$9"
    local require_done_default="${10}"
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_capture_phase4_hook_effective_flags \
      "${output_enable_hooks_var}" \
      "${output_enable_copilot_hooks_var}" \
      "${output_require_done_var}" \
      "${enable_hooks_input}" \
      "${enable_hooks_default}" \
      "${enable_copilot_hooks_input}" \
      "${enable_copilot_hooks_default}" \
      "${require_done_input}" \
      "${require_done_default}" \
      "${phase}"
  }

  ci_entry_capture_phase4_modules_csv_with_collector() {
    local output_var_name="$1"
    local raw_modules="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local collect_fn="$4"
    local has_nonempty_var="$5"
    local unknown_modules_var="$6"
    local duplicate_modules_var="$7"
    local normalized_modules_csv_var="$8"
    local error_empty_fn="$9"
    local error_unknown_fn="${10}"
    local error_duplicate_fn="${11}"
    local modules_value=""
    local has_nonempty_value=""
    local unknown_modules=""
    local duplicate_modules=""
    local normalized_modules_csv=""

    modules_value="$(ci_trim "${raw_modules}")"
    if [[ -z "${modules_value}" ]]; then
      printf -v "${output_var_name}" '%s' ""
      return 0
    fi

    "${collect_fn}" "${modules_value}"
    has_nonempty_value="${!has_nonempty_var-}"
    unknown_modules="${!unknown_modules_var-}"
    duplicate_modules="${!duplicate_modules_var-}"
    normalized_modules_csv="${!normalized_modules_csv_var-}"

    if [[ "${has_nonempty_value}" != "true" ]]; then
      ci_entry_report_error "$("${error_empty_fn}")" "${phase}"
      return 1
    fi
    if [[ -n "${unknown_modules}" ]]; then
      ci_entry_report_error \
        "$("${error_unknown_fn}" "${unknown_modules}")" \
        "${phase}"
      return 1
    fi
    if [[ -n "${duplicate_modules}" ]]; then
      ci_entry_report_error \
        "$("${error_duplicate_fn}" "${duplicate_modules}")" \
        "${phase}"
      return 1
    fi
    printf -v "${output_var_name}" '%s' "${normalized_modules_csv}"
  }

  ci_entry_capture_phase4_linkage_modules_csv() {
    local output_var_name="$1"
    local raw_modules="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    ci_entry_capture_phase4_modules_csv_with_collector \
      "${output_var_name}" \
      "${raw_modules}" \
      "${phase}" \
      "ci_phase4_linkage_collect_modules" \
      "CI_PHASE4_LINKAGE_HAS_NONEMPTY" \
      "CI_PHASE4_LINKAGE_UNKNOWN_MODULES" \
      "CI_PHASE4_LINKAGE_DUPLICATE_MODULES" \
      "CI_PHASE4_LINKAGE_NORMALIZED_MODULES_CSV" \
      "ci_phase4_linkage_error_empty_modules" \
      "ci_phase4_linkage_error_unknown_modules" \
      "ci_phase4_linkage_error_duplicate_modules"
  }

  ci_entry_capture_phase4_modules_csv_for_phase() {
    local phase="$1"
    local capture_fn="$2"
    local output_var_name="$3"
    local raw_modules="$4"
    ci_entry_run_for_phase \
      "${phase}" \
      "${capture_fn}" \
      "${output_var_name}" \
      "${raw_modules}" \
      "${phase}"
  }

  ci_entry_capture_phase4_linkage_modules_csv_for_phase() {
    local phase="$1"
    local output_var_name="$2"
    local raw_modules="$3"
    ci_entry_capture_phase4_modules_csv_for_phase \
      "${phase}" \
      "ci_entry_capture_phase4_linkage_modules_csv" \
      "${output_var_name}" \
      "${raw_modules}"
  }

  ci_entry_capture_phase4_reference_pattern_modules_csv() {
    local output_var_name="$1"
    local raw_modules="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    ci_entry_capture_phase4_modules_csv_with_collector \
      "${output_var_name}" \
      "${raw_modules}" \
      "${phase}" \
      "ci_phase4_reference_pattern_collect_modules" \
      "CI_PHASE4_REFERENCE_PATTERN_HAS_NONEMPTY" \
      "CI_PHASE4_REFERENCE_PATTERN_UNKNOWN_MODULES" \
      "CI_PHASE4_REFERENCE_PATTERN_DUPLICATE_MODULES" \
      "CI_PHASE4_REFERENCE_PATTERN_NORMALIZED_MODULES_CSV" \
      "ci_phase4_reference_pattern_error_empty_modules" \
      "ci_phase4_reference_pattern_error_unknown_modules" \
      "ci_phase4_reference_pattern_error_duplicate_modules"
  }

  ci_entry_capture_phase4_reference_pattern_modules_csv_for_phase() {
    local phase="$1"
    local output_var_name="$2"
    local raw_modules="$3"
    ci_entry_capture_phase4_modules_csv_for_phase \
      "${phase}" \
      "ci_entry_capture_phase4_reference_pattern_modules_csv" \
      "${output_var_name}" \
      "${raw_modules}"
  }

  ci_entry_capture_matrix_profile_ids_csv() {
    local output_var_name="$1"
    local raw_profile_ids="$2"
    local phase="${3:-${CURRENT_PHASE}}"

    ci_matrix_profile_ids_collect_unique "${raw_profile_ids}"
    if [[ "${CI_MATRIX_PROFILE_IDS_HAS_NONEMPTY}" != "true" ]]; then
      ci_entry_report_error \
        "$(ci_matrix_profile_ids_error_empty)" \
        "${phase}"
      return 1
    fi
    if [[ -n "${CI_MATRIX_PROFILE_IDS_DUPLICATES}" ]]; then
      ci_entry_report_error \
        "$(ci_matrix_profile_ids_error_duplicates "${CI_MATRIX_PROFILE_IDS_DUPLICATES}")" \
        "${phase}"
      return 1
    fi
    printf -v "${output_var_name}" '%s' "${CI_MATRIX_PROFILE_IDS_NORMALIZED_CSV}"
  }

  ci_entry_capture_matrix_profile_ids_csv_for_phase() {
    local phase="$1"
    local output_var_name="$2"
    local raw_profile_ids="$3"
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_capture_matrix_profile_ids_csv \
      "${output_var_name}" \
      "${raw_profile_ids}" \
      "${phase}"
  }

  ci_entry_validate_matrix_profile_count() {
    local raw_count_input="$1"
    local expected_count="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local count_value=""
    count_value="$(ci_trim "${raw_count_input}")"
    if [[ ! "${count_value}" =~ ^[0-9]+$ ]]; then
      ci_entry_report_error \
        "$(ci_matrix_profile_count_error_non_negative_integer)" \
        "${phase}"
      return 1
    fi
    if [[ "${expected_count}" -ne "${count_value}" ]]; then
      ci_entry_report_error \
        "$(ci_matrix_profile_count_error_mismatch "${expected_count}" "${count_value}")" \
        "${phase}"
      return 1
    fi
    printf '%s' "${count_value}"
  }

  ci_entry_validate_matrix_profile_count_for_phase() {
    local phase="$1"
    local raw_count_input="$2"
    local expected_count="$3"
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_validate_matrix_profile_count \
      "${raw_count_input}" \
      "${expected_count}" \
      "${phase}"
  }

  ci_entry_capture_stdout_var() {
    local output_var_name="$1"
    shift
    local captured_output=""
    if [[ ! "${output_var_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
      ci_entry_report_error \
        "stdout capture helper requires a valid output variable name: ${output_var_name}" \
        "${CURRENT_PHASE}"
      return 1
    fi
    if ! captured_output="$("$@")"; then
      return 1
    fi
    printf -v "${output_var_name}" '%s' "${captured_output}"
  }

  ci_entry_capture_stdout_var_for_phase() {
    local phase="$1"
    local output_var_name="$2"
    shift 2
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_capture_stdout_var \
      "${output_var_name}" \
      "$@"
  }

  ci_entry_capture_matrix_profile_count_for_phase() {
    local output_var_name="$1"
    local phase="$2"
    local raw_count_input="$3"
    local expected_count="$4"
    ci_entry_capture_stdout_var_for_phase \
      "${phase}" \
      "${output_var_name}" \
      ci_entry_validate_matrix_profile_count \
      "${raw_count_input}" \
      "${expected_count}"
  }

  ci_entry_count_nonempty_csv_items() {
    local raw_csv="$1"
    local count=0
    local item=""
    local trimmed_item=""
    local -a parsed_values=()

    IFS=',' read -ra parsed_values <<< "${raw_csv}"
    for item in "${parsed_values[@]}"; do
      trimmed_item="$(ci_trim "${item}")"
      if [[ -n "${trimmed_item}" ]]; then
        count=$((count + 1))
      fi
    done
    printf '%s' "${count}"
  }

  ci_entry_collect_nonempty_lines() {
    local raw_lines="$1"
    local raw_line=""
    local trimmed_line=""
    CI_ENTRY_COLLECTED_LINES=()

    while IFS= read -r raw_line; do
      trimmed_line="$(ci_trim "${raw_line}")"
      if [[ -n "${trimmed_line}" ]]; then
        CI_ENTRY_COLLECTED_LINES+=("${trimmed_line}")
      fi
    done <<< "${raw_lines}"
  }

  ci_entry_capture_nonempty_lines_array() {
    local output_array_name="$1"
    local raw_lines="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local had_nounset="false"
    local collected_count=0
    if [[ ! "${output_array_name}" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
      ci_entry_report_error \
        "nonempty lines capture helper requires a valid output array variable name: ${output_array_name}" \
        "${phase}"
      return 1
    fi
    ci_entry_collect_nonempty_lines "${raw_lines}"
    eval "${output_array_name}=()"
    case "$-" in
      *u*)
        had_nounset="true"
        set +u
        ;;
      *)
        ;;
    esac
    collected_count="${#CI_ENTRY_COLLECTED_LINES[@]}"
    if [[ "${collected_count}" -gt 0 ]]; then
      # shellcheck disable=SC2294
      eval "${output_array_name}=(\"\${CI_ENTRY_COLLECTED_LINES[@]}\")"
    fi
    if [[ "${had_nounset}" == "true" ]]; then
      set -u
    fi
  }

  ci_entry_capture_nonempty_lines_array_for_phase() {
    local phase="$1"
    local output_array_name="$2"
    local raw_lines="$3"
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_capture_nonempty_lines_array \
      "${output_array_name}" \
      "${raw_lines}" \
      "${phase}"
  }

  ci_entry_require_directory() {
    local dir_path="$1"
    local message="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    if [[ -d "${dir_path}" ]]; then
      return 0
    fi
    ci_entry_report_error "${message}" "${phase}"
    return 1
  }

  ci_entry_require_resolved_path() {
    local resolve_fn="$1"
    local missing_message="$2"
    local phase="${3:-${CURRENT_PHASE}}"
    local resolved_path=""
    if ! resolved_path="$("${resolve_fn}")"; then
      ci_entry_report_error "failed to resolve required path via ${resolve_fn}" "${phase}"
      return 1
    fi
    if ! ci_entry_require_nonempty "${resolved_path}" "${missing_message}" "${phase}"; then
      return 1
    fi
    printf '%s' "${resolved_path}"
  }

  ci_entry_require_output_path() {
    local phase="${1:-${CURRENT_PHASE}}"
    ci_entry_require_resolved_path \
      ci_resolve_output_path \
      "GITHUB_OUTPUT (or GITHUB_OUTPUT_PATH) is required" \
      "${phase}"
  }

  ci_entry_require_step_summary_path() {
    local phase="${1:-${CURRENT_PHASE}}"
    ci_entry_require_resolved_path \
      ci_resolve_step_summary_path \
      "STEP_SUMMARY_FILE (or GITHUB_STEP_SUMMARY) is required" \
      "${phase}"
  }

  ci_entry_write_output_pairs() {
    local output_path="$1"
    shift
    local phase="${CURRENT_PHASE}"
    local key=""
    local value=""
    if [[ $# -eq 0 || $(( $# % 2 )) -ne 0 ]]; then
      ci_entry_report_error \
        "output writer helper requires key/value pairs" \
        "${phase}"
      return 1
    fi
    while [[ $# -gt 0 ]]; do
      key="$1"
      value="$2"
      shift 2
      ci_write_output "${output_path}" "${key}" "${value}"
    done
  }

  ci_entry_write_output_pairs_for_phase() {
    local phase="$1"
    local output_path="$2"
    shift 2
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_write_output_pairs \
      "${output_path}" \
      "$@"
  }

  ci_entry_publish_summary_section() {
    local script_dir="$1"
    local step_summary_path="$2"
    local summary_title="$3"
    local summary_items="$4"
    STEP_SUMMARY_FILE="${step_summary_path}" \
    SUMMARY_SECTION_TITLE="${summary_title}" \
    SUMMARY_SECTION_ITEMS="${summary_items}" \
    ci_entry_exec_delegated bash "${script_dir}/run_ci_summary_section_entry.sh"
  }

  ci_entry_publish_summary_section_from_pairs() {
    local script_dir="$1"
    local step_summary_path="$2"
    local summary_title="$3"
    shift 3
    local summary_items=""
    if ! ci_entry_build_summary_section_items summary_items "$@"; then
      return 1
    fi
    ci_entry_publish_summary_section \
      "${script_dir}" \
      "${step_summary_path}" \
      "${summary_title}" \
      "${summary_items}"
  }

  ci_entry_publish_summary_section_for_phase() {
    local script_dir="$1"
    local step_summary_path="$2"
    local phase="$3"
    local summary_title="$4"
    shift 4
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_publish_summary_section_from_pairs \
      "${script_dir}" \
      "${step_summary_path}" \
      "${summary_title}" \
      "$@"
  }

  ci_entry_run_for_phase() {
    local phase="$1"
    shift
    ci_entry_set_phase "${phase}"
    "$@"
  }

  ci_entry_exec_delegated() {
    if "$@"; then
      return 0
    else
      local rc=$?
      exit "${rc}"
    fi
  }

  ci_entry_exec_delegated_for_phase() {
    local phase="$1"
    shift
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_exec_delegated \
      "$@"
  }

  ci_entry_capture_delegated() {
    local output_var_name="$1"
    shift
    local captured_output=""
    if captured_output="$("$@")"; then
      printf -v "${output_var_name}" '%s' "${captured_output}"
      return 0
    else
      local rc=$?
      exit "${rc}"
    fi
  }

  ci_entry_capture_delegated_for_phase() {
    local phase="$1"
    local output_var_name="$2"
    shift 2
    ci_entry_run_for_phase \
      "${phase}" \
      ci_entry_capture_delegated \
      "${output_var_name}" \
      "$@"
  }

  ci_entry_on_err() {
    local failed_cmd="${1:-unknown}"
    local rc="${2:-1}"
    if [[ "${ERROR_REPORTED}" != "true" ]]; then
      ci_entry_report_error "command failed with exit code ${rc}: ${failed_cmd}" "${CURRENT_PHASE}"
    fi
    exit "${rc}"
  }

  trap 'ci_entry_on_err "${BASH_COMMAND:-unknown}" "$?"' ERR
}
