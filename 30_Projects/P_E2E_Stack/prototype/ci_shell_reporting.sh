#!/usr/bin/env bash
set -euo pipefail

CI_ERROR_TITLE="E2E CI Error"

ci_trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

ci_resolve_output_path() {
  local output_path
  output_path="$(ci_trim "${GITHUB_OUTPUT_PATH:-}")"
  if [[ -n "${output_path}" ]]; then
    printf '%s' "${output_path}"
    return 0
  fi
  ci_trim "${GITHUB_OUTPUT:-}"
}

ci_resolve_step_summary_path() {
  local summary_path
  summary_path="$(ci_trim "${STEP_SUMMARY_FILE:-}")"
  if [[ -n "${summary_path}" ]]; then
    printf '%s' "${summary_path}"
    return 0
  fi
  ci_trim "${GITHUB_STEP_SUMMARY:-}"
}

ci_write_output() {
  local output_path="$1"
  local key="$2"
  local value="${3:-}"
  local normalized_output_path
  normalized_output_path="$(ci_trim "${output_path}")"

  if [[ -z "${normalized_output_path}" ]]; then
    return 1
  fi
  mkdir -p "$(dirname "${normalized_output_path}")"
  if [[ "${value}" == *$'\n'* ]]; then
    local marker="CI_EOF_$$_${RANDOM}"
    while [[ "${value}" == *"${marker}"* ]]; do
      marker="${marker}_X"
    done
    {
      printf '%s<<%s\n' "${key}" "${marker}"
      printf '%s' "${value}"
      if [[ "${value}" != *$'\n' ]]; then
        printf '\n'
      fi
      printf '%s\n' "${marker}"
    } >> "${normalized_output_path}"
    return 0
  fi
  printf '%s=%s\n' "${key}" "${value}" >> "${normalized_output_path}"
}

ci_emit_error() {
  local source="$1"
  local message="$2"
  local phase="${3:-}"
  local summary_path
  summary_path="$(ci_resolve_step_summary_path)"

  local normalized
  normalized="$(ci_trim "${message}")"
  if [[ -z "${normalized}" ]]; then
    normalized="unknown_error"
  fi

  printf '[error] %s: %s\n' "${source}" "${normalized}" >&2

  if [[ -z "${summary_path}" ]]; then
    return 0
  fi

  mkdir -p "$(dirname "${summary_path}")"
  {
    printf '## %s\n' "${CI_ERROR_TITLE}"
    printf '\n'
    printf '%s\n' "- source: \`${source}\`"
    if [[ -n "${phase}" ]]; then
      printf '%s\n' "- phase: \`${phase}\`"
    fi
    printf '\n'
    printf '```text\n'
    printf '%s\n' "${normalized}"
    printf '```\n'
  } >> "${summary_path}"
}
