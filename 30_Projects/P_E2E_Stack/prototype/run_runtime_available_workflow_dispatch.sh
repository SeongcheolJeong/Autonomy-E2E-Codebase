#!/usr/bin/env bash
set -euo pipefail

trim_text() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

validate_binary_flag() {
  local value="$1"
  local name="$2"
  case "$value" in
    0|1) ;;
    *)
      echo "[error] ${name} must be 0 or 1, got: ${value}" >&2
      exit 2
      ;;
  esac
}

validate_positive_int() {
  local value="$1"
  local name="$2"
  case "$value" in
    ''|*[!0-9]*)
      echo "[error] ${name} must be a positive integer, got: ${value}" >&2
      exit 2
      ;;
    *)
      if [ "$value" -le 0 ]; then
        echo "[error] ${name} must be a positive integer, got: ${value}" >&2
        exit 2
      fi
      ;;
  esac
}

validate_non_negative_int() {
  local value="$1"
  local name="$2"
  case "$value" in
    ''|*[!0-9]*)
      echo "[error] ${name} must be a non-negative integer, got: ${value}" >&2
      exit 2
      ;;
    *)
      ;;
  esac
}

validate_optional_non_negative_int() {
  local value="$1"
  local name="$2"
  if [ -z "$value" ]; then
    return 0
  fi
  validate_non_negative_int "$value" "$name"
}

validate_positive_float() {
  local value="$1"
  local name="$2"
  python3 - "$value" "$name" <<'PY'
import sys

value = str(sys.argv[1]).strip()
name = str(sys.argv[2]).strip()
try:
    parsed = float(value)
except ValueError:
    print(f"[error] {name} must be a positive float, got: {value}", file=sys.stderr)
    raise SystemExit(2)
if parsed <= 0:
    print(f"[error] {name} must be a positive float, got: {value}", file=sys.stderr)
    raise SystemExit(2)
PY
}

validate_non_negative_float() {
  local value="$1"
  local name="$2"
  python3 - "$value" "$name" <<'PY'
import math
import sys

value = str(sys.argv[1]).strip()
name = str(sys.argv[2]).strip()
try:
    parsed = float(value)
except ValueError:
    print(f"[error] {name} must be a non-negative float, got: {value}", file=sys.stderr)
    raise SystemExit(2)
if not math.isfinite(parsed) or parsed < 0:
    print(f"[error] {name} must be a non-negative float, got: {value}", file=sys.stderr)
    raise SystemExit(2)
PY
}

validate_optional_non_negative_float() {
  local value="$1"
  local name="$2"
  if [ -z "$value" ]; then
    return 0
  fi
  validate_non_negative_float "$value" "$name"
}

validate_optional_ratio_0_to_1() {
  local value="$1"
  local name="$2"
  if [ -z "$value" ]; then
    return 0
  fi
  python3 - "$value" "$name" <<'PY'
import math
import sys

value = str(sys.argv[1]).strip()
name = str(sys.argv[2]).strip()
try:
    parsed = float(value)
except ValueError:
    print(f"[error] {name} must be within [0,1], got: {value}", file=sys.stderr)
    raise SystemExit(2)
if not math.isfinite(parsed) or parsed < 0 or parsed > 1:
    print(f"[error] {name} must be within [0,1], got: {value}", file=sys.stderr)
    raise SystemExit(2)
PY
}

validate_hold_not_greater_than_warn_optional() {
  local warn_value="$1"
  local hold_value="$2"
  local metric_name="$3"
  if [ -z "$warn_value" ] || [ -z "$hold_value" ]; then
    return 0
  fi
  if ! python3 - "$warn_value" "$hold_value" <<'PY'
import math
import sys

try:
    warn_value = float(sys.argv[1])
    hold_value = float(sys.argv[2])
except (TypeError, ValueError):
    sys.exit(1)
if not math.isfinite(warn_value) or not math.isfinite(hold_value):
    sys.exit(1)
if hold_value > warn_value:
    sys.exit(1)
PY
  then
    echo "[error] ${metric_name}: hold threshold must be <= warn threshold (warn=${warn_value}, hold=${hold_value})" >&2
    exit 2
  fi
}

validate_warn_not_greater_than_hold_optional() {
  local warn_value="$1"
  local hold_value="$2"
  local metric_name="$3"
  if [ -z "$warn_value" ] || [ -z "$hold_value" ]; then
    return 0
  fi
  if ! python3 - "$warn_value" "$hold_value" <<'PY'
import math
import sys

try:
    warn_value = float(sys.argv[1])
    hold_value = float(sys.argv[2])
except (TypeError, ValueError):
    sys.exit(1)
if not math.isfinite(warn_value) or not math.isfinite(hold_value):
    sys.exit(1)
if warn_value > hold_value:
    sys.exit(1)
PY
  then
    echo "[error] ${metric_name}: warn threshold must be <= hold threshold (warn=${warn_value}, hold=${hold_value})" >&2
    exit 2
  fi
}

validate_choice() {
  local value="$1"
  local name="$2"
  shift 2
  local allowed
  for allowed in "$@"; do
    if [ "$value" = "$allowed" ]; then
      return 0
    fi
  done
  echo "[error] ${name} must be one of: $*; got: ${value}" >&2
  exit 2
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "${script_dir}/../../.." && pwd -P)"
cd "${repo_root}"

resolve_repository_from_origin() {
  local remote_url
  remote_url="$(git -C "${repo_root}" remote get-url origin 2>/dev/null || true)"
  if [ -z "${remote_url}" ]; then
    return 0
  fi
  python3 - "${remote_url}" <<'PY'
import re
import sys

url = str(sys.argv[1]).strip()
patterns = [
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$",
    r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
]
for pattern in patterns:
    match = re.match(pattern, url)
    if match:
        owner = str(match.group(1)).strip()
        repo = str(match.group(2)).strip()
        if owner and repo:
            print(f"{owner}/{repo}")
        break
PY
}

workflow_file="$(trim_text "${WORKFLOW_DISPATCH_WORKFLOW_FILE:-e2e-runtime-available.yml}")"
dispatch_repository="$(trim_text "${WORKFLOW_DISPATCH_REPOSITORY:-}")"
dispatch_ref="$(trim_text "${WORKFLOW_DISPATCH_REF:-}")"
dispatch_wait="$(trim_text "${WORKFLOW_DISPATCH_WAIT:-1}")"
dispatch_timeout_sec="$(trim_text "${WORKFLOW_DISPATCH_TIMEOUT_SEC:-3600}")"
dispatch_poll_sec="$(trim_text "${WORKFLOW_DISPATCH_POLL_SEC:-10}")"
dispatch_discover_timeout_sec="$(trim_text "${WORKFLOW_DISPATCH_DISCOVER_TIMEOUT_SEC:-180}")"
dispatch_require_run_success="$(trim_text "${WORKFLOW_DISPATCH_REQUIRE_RUN_SUCCESS:-0}")"
dispatch_dry_run="$(trim_text "${WORKFLOW_DISPATCH_DRY_RUN:-0}")"
dispatch_allow_gh_cli_token="$(trim_text "${WORKFLOW_DISPATCH_ALLOW_GH_CLI_TOKEN:-1}")"

if [ -z "${dispatch_ref}" ]; then
  dispatch_ref="$(trim_text "$(git -C "${repo_root}" branch --show-current 2>/dev/null || true)")"
fi
if [ -z "${dispatch_ref}" ]; then
  dispatch_ref="main"
fi

if [ -z "${dispatch_repository}" ]; then
  dispatch_repository="$(trim_text "$(resolve_repository_from_origin)")"
fi

validate_binary_flag "${dispatch_wait}" "WORKFLOW_DISPATCH_WAIT"
validate_binary_flag "${dispatch_require_run_success}" "WORKFLOW_DISPATCH_REQUIRE_RUN_SUCCESS"
validate_binary_flag "${dispatch_dry_run}" "WORKFLOW_DISPATCH_DRY_RUN"
validate_binary_flag "${dispatch_allow_gh_cli_token}" "WORKFLOW_DISPATCH_ALLOW_GH_CLI_TOKEN"
validate_positive_int "${dispatch_timeout_sec}" "WORKFLOW_DISPATCH_TIMEOUT_SEC"
validate_positive_int "${dispatch_poll_sec}" "WORKFLOW_DISPATCH_POLL_SEC"
validate_positive_int "${dispatch_discover_timeout_sec}" "WORKFLOW_DISPATCH_DISCOVER_TIMEOUT_SEC"

input_sim_runtime="$(trim_text "${INPUT_SIM_RUNTIME:-awsim}")"
input_lane="$(trim_text "${INPUT_LANE:-auto}")"
input_dry_run="$(trim_text "${INPUT_DRY_RUN:-0}")"
input_continue_on_runtime_failure="$(trim_text "${INPUT_CONTINUE_ON_RUNTIME_FAILURE:-1}")"
input_runtime_asset_profile="$(trim_text "${INPUT_RUNTIME_ASSET_PROFILE:-lightweight}")"
input_runtime_asset_archive_sha256_mode="$(trim_text "${INPUT_RUNTIME_ASSET_ARCHIVE_SHA256_MODE:-verify_only}")"
input_install_runtime_deps="$(trim_text "${INPUT_INSTALL_RUNTIME_DEPS:-1}")"
input_use_runtime_asset_cache="$(trim_text "${INPUT_USE_RUNTIME_ASSET_CACHE:-1}")"
input_asset_skip_download="$(trim_text "${INPUT_ASSET_SKIP_DOWNLOAD:-1}")"
input_asset_skip_extract="$(trim_text "${INPUT_ASSET_SKIP_EXTRACT:-0}")"
input_asset_force_download="$(trim_text "${INPUT_ASSET_FORCE_DOWNLOAD:-0}")"
input_asset_force_extract="$(trim_text "${INPUT_ASSET_FORCE_EXTRACT:-0}")"
input_runtime_native_auto_enable="$(trim_text "${INPUT_RUNTIME_NATIVE_DOCKER_AUTO_ENABLE:-1}")"
input_runtime_native_auto_dry_run="$(trim_text "${INPUT_RUNTIME_NATIVE_DOCKER_AUTO_DRY_RUN:-0}")"
input_runtime_native_sim_runtime="$(trim_text "${INPUT_RUNTIME_NATIVE_SIM_RUNTIME:-carla}")"
input_runtime_native_summary_compare_fail_on_missing="$(trim_text "${INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_MISSING:-0}")"
input_runtime_native_summary_compare_fail_on_diffs="$(trim_text "${INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_DIFFS:-0}")"
input_runtime_threshold_drift_fail_on_hold="$(trim_text "${INPUT_RUNTIME_THRESHOLD_DRIFT_FAIL_ON_HOLD:-0}")"
input_phase2_log_replay_threshold_fail_on_hold="$(trim_text "${INPUT_PHASE2_LOG_REPLAY_THRESHOLD_FAIL_ON_HOLD:-0}")"
input_runtime_native_smoke_threshold_fail_on_hold="$(trim_text "${INPUT_RUNTIME_NATIVE_SMOKE_THRESHOLD_FAIL_ON_HOLD:-0}")"
input_runtime_native_evidence_compare_threshold_fail_on_hold="$(trim_text "${INPUT_RUNTIME_NATIVE_EVIDENCE_COMPARE_THRESHOLD_FAIL_ON_HOLD:-0}")"
input_runtime_scenario_contract_enable="$(trim_text "${INPUT_RUNTIME_SCENARIO_CONTRACT_ENABLE:-1}")"
input_runtime_scenario_contract_require_runtime_ready="$(trim_text "${INPUT_RUNTIME_SCENARIO_CONTRACT_REQUIRE_RUNTIME_READY:-1}")"
input_runtime_scene_result_enable="$(trim_text "${INPUT_RUNTIME_SCENE_RESULT_ENABLE:-1}")"
input_runtime_scene_result_require_runtime_ready="$(trim_text "${INPUT_RUNTIME_SCENE_RESULT_REQUIRE_RUNTIME_READY:-1}")"
input_runtime_interop_contract_enable="$(trim_text "${INPUT_RUNTIME_INTEROP_CONTRACT_ENABLE:-1}")"
input_runtime_interop_contract_require_runtime_ready="$(trim_text "${INPUT_RUNTIME_INTEROP_CONTRACT_REQUIRE_RUNTIME_READY:-1}")"
input_runtime_interop_export_road_length_scale="$(trim_text "${INPUT_RUNTIME_INTEROP_EXPORT_ROAD_LENGTH_SCALE:-1.0}")"
input_runtime_interop_contract_xosc="$(trim_text "${INPUT_RUNTIME_INTEROP_CONTRACT_XOSC:-}")"
input_runtime_interop_contract_xodr="$(trim_text "${INPUT_RUNTIME_INTEROP_CONTRACT_XODR:-}")"
input_runtime_interop_import_manifest_consistency_mode="$(trim_text "${INPUT_RUNTIME_INTEROP_IMPORT_MANIFEST_CONSISTENCY_MODE:-allow}")"
input_runtime_interop_import_export_consistency_mode="$(trim_text "${INPUT_RUNTIME_INTEROP_IMPORT_EXPORT_CONSISTENCY_MODE:-allow}")"
input_notify_on="$(trim_text "${INPUT_NOTIFY_ON:-hold_warn}")"
input_notify_format="$(trim_text "${INPUT_NOTIFY_FORMAT:-slack}")"
input_notify_timeout_sec="$(trim_text "${INPUT_NOTIFY_TIMEOUT_SEC:-10}")"
input_notify_max_retries="$(trim_text "${INPUT_NOTIFY_MAX_RETRIES:-2}")"
input_notify_retry_backoff_sec="$(trim_text "${INPUT_NOTIFY_RETRY_BACKOFF_SEC:-2}")"
input_notify_runtime_lane_execution_warn_min_exec_rows="$(trim_text "${INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_WARN_MIN_EXEC_ROWS:-}")"
input_notify_runtime_lane_execution_hold_min_exec_rows="$(trim_text "${INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_HOLD_MIN_EXEC_ROWS:-}")"
input_notify_runtime_evidence_compare_warn_min_artifacts_with_diffs="$(trim_text "${INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_ARTIFACTS_WITH_DIFFS:-}")"
input_notify_runtime_evidence_compare_hold_min_artifacts_with_diffs="$(trim_text "${INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_ARTIFACTS_WITH_DIFFS:-}")"
input_notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count="$(trim_text "${INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT:-}")"
input_notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count="$(trim_text "${INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT:-}")"
input_notify_phase2_sensor_fidelity_score_avg_warn_min="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_WARN_MIN:-}")"
input_notify_phase2_sensor_fidelity_score_avg_hold_min="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_HOLD_MIN:-}")"
input_notify_phase2_sensor_frame_count_avg_warn_min="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_WARN_MIN:-}")"
input_notify_phase2_sensor_frame_count_avg_hold_min="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_HOLD_MIN:-}")"
input_notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_WARN_MAX:-}")"
input_notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_HOLD_MAX:-}")"
input_notify_phase2_sensor_lidar_point_count_avg_warn_min="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_WARN_MIN:-}")"
input_notify_phase2_sensor_lidar_point_count_avg_hold_min="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_HOLD_MIN:-}")"
input_notify_phase2_sensor_radar_false_positive_rate_avg_warn_max="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_WARN_MAX:-}")"
input_notify_phase2_sensor_radar_false_positive_rate_avg_hold_max="$(trim_text "${INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_HOLD_MAX:-}")"
input_notify_phase3_vehicle_control_overlap_ratio_warn_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_WARN_MAX:-}")"
input_notify_phase3_vehicle_control_overlap_ratio_hold_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_HOLD_MAX:-}")"
input_notify_phase3_vehicle_control_steering_rate_warn_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_WARN_MAX:-}")"
input_notify_phase3_vehicle_control_steering_rate_hold_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_HOLD_MAX:-}")"
input_notify_phase3_vehicle_control_throttle_plus_brake_warn_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_WARN_MAX:-}")"
input_notify_phase3_vehicle_control_throttle_plus_brake_hold_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_HOLD_MAX:-}")"
input_notify_phase3_vehicle_speed_tracking_error_warn_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_WARN_MAX:-}")"
input_notify_phase3_vehicle_speed_tracking_error_hold_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_HOLD_MAX:-}")"
input_notify_phase3_vehicle_speed_tracking_error_abs_warn_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_WARN_MAX:-}")"
input_notify_phase3_vehicle_speed_tracking_error_abs_hold_max="$(trim_text "${INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_HOLD_MAX:-}")"
input_matrix_profile_ids="$(trim_text "${INPUT_MATRIX_PROFILE_IDS:-runtime_carla_smoke_v0,runtime_awsim_smoke_v0}")"
input_release_id="$(trim_text "${INPUT_RELEASE_ID:-}")"

validate_binary_flag "${input_dry_run}" "INPUT_DRY_RUN"
validate_binary_flag "${input_continue_on_runtime_failure}" "INPUT_CONTINUE_ON_RUNTIME_FAILURE"
validate_binary_flag "${input_install_runtime_deps}" "INPUT_INSTALL_RUNTIME_DEPS"
validate_binary_flag "${input_use_runtime_asset_cache}" "INPUT_USE_RUNTIME_ASSET_CACHE"
validate_binary_flag "${input_asset_skip_download}" "INPUT_ASSET_SKIP_DOWNLOAD"
validate_binary_flag "${input_asset_skip_extract}" "INPUT_ASSET_SKIP_EXTRACT"
validate_binary_flag "${input_asset_force_download}" "INPUT_ASSET_FORCE_DOWNLOAD"
validate_binary_flag "${input_asset_force_extract}" "INPUT_ASSET_FORCE_EXTRACT"
validate_binary_flag "${input_runtime_native_auto_enable}" "INPUT_RUNTIME_NATIVE_DOCKER_AUTO_ENABLE"
validate_binary_flag "${input_runtime_native_auto_dry_run}" "INPUT_RUNTIME_NATIVE_DOCKER_AUTO_DRY_RUN"
validate_binary_flag "${input_runtime_native_summary_compare_fail_on_missing}" "INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_MISSING"
validate_binary_flag "${input_runtime_native_summary_compare_fail_on_diffs}" "INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_DIFFS"
validate_binary_flag "${input_runtime_threshold_drift_fail_on_hold}" "INPUT_RUNTIME_THRESHOLD_DRIFT_FAIL_ON_HOLD"
validate_binary_flag "${input_phase2_log_replay_threshold_fail_on_hold}" "INPUT_PHASE2_LOG_REPLAY_THRESHOLD_FAIL_ON_HOLD"
validate_binary_flag "${input_runtime_native_smoke_threshold_fail_on_hold}" "INPUT_RUNTIME_NATIVE_SMOKE_THRESHOLD_FAIL_ON_HOLD"
validate_binary_flag "${input_runtime_native_evidence_compare_threshold_fail_on_hold}" "INPUT_RUNTIME_NATIVE_EVIDENCE_COMPARE_THRESHOLD_FAIL_ON_HOLD"
validate_binary_flag "${input_runtime_scenario_contract_enable}" "INPUT_RUNTIME_SCENARIO_CONTRACT_ENABLE"
validate_binary_flag "${input_runtime_scenario_contract_require_runtime_ready}" "INPUT_RUNTIME_SCENARIO_CONTRACT_REQUIRE_RUNTIME_READY"
validate_binary_flag "${input_runtime_scene_result_enable}" "INPUT_RUNTIME_SCENE_RESULT_ENABLE"
validate_binary_flag "${input_runtime_scene_result_require_runtime_ready}" "INPUT_RUNTIME_SCENE_RESULT_REQUIRE_RUNTIME_READY"
validate_binary_flag "${input_runtime_interop_contract_enable}" "INPUT_RUNTIME_INTEROP_CONTRACT_ENABLE"
validate_binary_flag "${input_runtime_interop_contract_require_runtime_ready}" "INPUT_RUNTIME_INTEROP_CONTRACT_REQUIRE_RUNTIME_READY"
validate_choice "${input_runtime_interop_import_manifest_consistency_mode}" "INPUT_RUNTIME_INTEROP_IMPORT_MANIFEST_CONSISTENCY_MODE" require allow
validate_choice "${input_runtime_interop_import_export_consistency_mode}" "INPUT_RUNTIME_INTEROP_IMPORT_EXPORT_CONSISTENCY_MODE" require allow
validate_positive_float "${input_runtime_interop_export_road_length_scale}" "INPUT_RUNTIME_INTEROP_EXPORT_ROAD_LENGTH_SCALE"
validate_choice "${input_notify_on}" "INPUT_NOTIFY_ON" always hold warn hold_warn pass never
validate_choice "${input_notify_format}" "INPUT_NOTIFY_FORMAT" slack raw
validate_positive_int "${input_notify_timeout_sec}" "INPUT_NOTIFY_TIMEOUT_SEC"
validate_non_negative_int "${input_notify_max_retries}" "INPUT_NOTIFY_MAX_RETRIES"
validate_non_negative_int "${input_notify_retry_backoff_sec}" "INPUT_NOTIFY_RETRY_BACKOFF_SEC"
validate_optional_non_negative_int "${input_notify_runtime_lane_execution_warn_min_exec_rows}" "INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_WARN_MIN_EXEC_ROWS"
validate_optional_non_negative_int "${input_notify_runtime_lane_execution_hold_min_exec_rows}" "INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_HOLD_MIN_EXEC_ROWS"
validate_optional_non_negative_int "${input_notify_runtime_evidence_compare_warn_min_artifacts_with_diffs}" "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_ARTIFACTS_WITH_DIFFS"
validate_optional_non_negative_int "${input_notify_runtime_evidence_compare_hold_min_artifacts_with_diffs}" "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_ARTIFACTS_WITH_DIFFS"
validate_optional_non_negative_int "${input_notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count}" "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT"
validate_optional_non_negative_int "${input_notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count}" "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT"
validate_optional_non_negative_float "${input_notify_phase2_sensor_fidelity_score_avg_warn_min}" "INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_WARN_MIN"
validate_optional_non_negative_float "${input_notify_phase2_sensor_fidelity_score_avg_hold_min}" "INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_HOLD_MIN"
validate_optional_non_negative_float "${input_notify_phase2_sensor_frame_count_avg_warn_min}" "INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_WARN_MIN"
validate_optional_non_negative_float "${input_notify_phase2_sensor_frame_count_avg_hold_min}" "INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_HOLD_MIN"
validate_optional_non_negative_float "${input_notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max}" "INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_WARN_MAX"
validate_optional_non_negative_float "${input_notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max}" "INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_HOLD_MAX"
validate_optional_non_negative_float "${input_notify_phase2_sensor_lidar_point_count_avg_warn_min}" "INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_WARN_MIN"
validate_optional_non_negative_float "${input_notify_phase2_sensor_lidar_point_count_avg_hold_min}" "INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_HOLD_MIN"
validate_optional_non_negative_float "${input_notify_phase2_sensor_radar_false_positive_rate_avg_warn_max}" "INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_WARN_MAX"
validate_optional_non_negative_float "${input_notify_phase2_sensor_radar_false_positive_rate_avg_hold_max}" "INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_HOLD_MAX"
validate_optional_ratio_0_to_1 "${input_notify_phase3_vehicle_control_overlap_ratio_warn_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_WARN_MAX"
validate_optional_ratio_0_to_1 "${input_notify_phase3_vehicle_control_overlap_ratio_hold_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_HOLD_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_control_steering_rate_warn_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_WARN_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_control_steering_rate_hold_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_HOLD_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_control_throttle_plus_brake_warn_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_WARN_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_control_throttle_plus_brake_hold_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_HOLD_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_speed_tracking_error_warn_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_WARN_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_speed_tracking_error_hold_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_HOLD_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_speed_tracking_error_abs_warn_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_WARN_MAX"
validate_optional_non_negative_float "${input_notify_phase3_vehicle_speed_tracking_error_abs_hold_max}" "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_HOLD_MAX"
validate_hold_not_greater_than_warn_optional \
  "${input_notify_phase2_sensor_fidelity_score_avg_warn_min}" \
  "${input_notify_phase2_sensor_fidelity_score_avg_hold_min}" \
  "notify_phase2_sensor_fidelity_score_avg"
validate_hold_not_greater_than_warn_optional \
  "${input_notify_phase2_sensor_frame_count_avg_warn_min}" \
  "${input_notify_phase2_sensor_frame_count_avg_hold_min}" \
  "notify_phase2_sensor_frame_count_avg"
validate_warn_not_greater_than_hold_optional \
  "${input_notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max}" \
  "${input_notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max}" \
  "notify_phase2_sensor_camera_noise_stddev_px_avg"
validate_hold_not_greater_than_warn_optional \
  "${input_notify_phase2_sensor_lidar_point_count_avg_warn_min}" \
  "${input_notify_phase2_sensor_lidar_point_count_avg_hold_min}" \
  "notify_phase2_sensor_lidar_point_count_avg"
validate_warn_not_greater_than_hold_optional \
  "${input_notify_phase2_sensor_radar_false_positive_rate_avg_warn_max}" \
  "${input_notify_phase2_sensor_radar_false_positive_rate_avg_hold_max}" \
  "notify_phase2_sensor_radar_false_positive_rate_avg"
validate_choice "${input_runtime_asset_profile}" "INPUT_RUNTIME_ASSET_PROFILE" lightweight full
validate_choice "${input_runtime_asset_archive_sha256_mode}" "INPUT_RUNTIME_ASSET_ARCHIVE_SHA256_MODE" verify_only always never
validate_choice "${input_sim_runtime}" "INPUT_SIM_RUNTIME" awsim carla both
validate_choice "${input_lane}" "INPUT_LANE" auto exec contract
validate_choice "${input_runtime_native_sim_runtime}" "INPUT_RUNTIME_NATIVE_SIM_RUNTIME" awsim carla both

payload="$(
  INPUT_SIM_RUNTIME="${input_sim_runtime}" \
  INPUT_LANE="${input_lane}" \
  INPUT_DRY_RUN="${input_dry_run}" \
  INPUT_CONTINUE_ON_RUNTIME_FAILURE="${input_continue_on_runtime_failure}" \
  INPUT_RUNTIME_ASSET_PROFILE="${input_runtime_asset_profile}" \
  INPUT_RUNTIME_ASSET_ARCHIVE_SHA256_MODE="${input_runtime_asset_archive_sha256_mode}" \
  INPUT_INSTALL_RUNTIME_DEPS="${input_install_runtime_deps}" \
  INPUT_USE_RUNTIME_ASSET_CACHE="${input_use_runtime_asset_cache}" \
  INPUT_ASSET_SKIP_DOWNLOAD="${input_asset_skip_download}" \
  INPUT_ASSET_SKIP_EXTRACT="${input_asset_skip_extract}" \
  INPUT_ASSET_FORCE_DOWNLOAD="${input_asset_force_download}" \
  INPUT_ASSET_FORCE_EXTRACT="${input_asset_force_extract}" \
  INPUT_RUNTIME_NATIVE_DOCKER_AUTO_ENABLE="${input_runtime_native_auto_enable}" \
  INPUT_RUNTIME_NATIVE_DOCKER_AUTO_DRY_RUN="${input_runtime_native_auto_dry_run}" \
  INPUT_RUNTIME_NATIVE_SIM_RUNTIME="${input_runtime_native_sim_runtime}" \
  INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_MISSING="${input_runtime_native_summary_compare_fail_on_missing}" \
  INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_DIFFS="${input_runtime_native_summary_compare_fail_on_diffs}" \
  INPUT_RUNTIME_THRESHOLD_DRIFT_FAIL_ON_HOLD="${input_runtime_threshold_drift_fail_on_hold}" \
  INPUT_PHASE2_LOG_REPLAY_THRESHOLD_FAIL_ON_HOLD="${input_phase2_log_replay_threshold_fail_on_hold}" \
  INPUT_RUNTIME_NATIVE_SMOKE_THRESHOLD_FAIL_ON_HOLD="${input_runtime_native_smoke_threshold_fail_on_hold}" \
  INPUT_RUNTIME_NATIVE_EVIDENCE_COMPARE_THRESHOLD_FAIL_ON_HOLD="${input_runtime_native_evidence_compare_threshold_fail_on_hold}" \
  INPUT_RUNTIME_SCENARIO_CONTRACT_ENABLE="${input_runtime_scenario_contract_enable}" \
  INPUT_RUNTIME_SCENARIO_CONTRACT_REQUIRE_RUNTIME_READY="${input_runtime_scenario_contract_require_runtime_ready}" \
  INPUT_RUNTIME_SCENE_RESULT_ENABLE="${input_runtime_scene_result_enable}" \
  INPUT_RUNTIME_SCENE_RESULT_REQUIRE_RUNTIME_READY="${input_runtime_scene_result_require_runtime_ready}" \
  INPUT_RUNTIME_INTEROP_CONTRACT_ENABLE="${input_runtime_interop_contract_enable}" \
  INPUT_RUNTIME_INTEROP_CONTRACT_REQUIRE_RUNTIME_READY="${input_runtime_interop_contract_require_runtime_ready}" \
  INPUT_RUNTIME_INTEROP_EXPORT_ROAD_LENGTH_SCALE="${input_runtime_interop_export_road_length_scale}" \
  INPUT_RUNTIME_INTEROP_CONTRACT_XOSC="${input_runtime_interop_contract_xosc}" \
  INPUT_RUNTIME_INTEROP_CONTRACT_XODR="${input_runtime_interop_contract_xodr}" \
  INPUT_RUNTIME_INTEROP_IMPORT_MANIFEST_CONSISTENCY_MODE="${input_runtime_interop_import_manifest_consistency_mode}" \
  INPUT_RUNTIME_INTEROP_IMPORT_EXPORT_CONSISTENCY_MODE="${input_runtime_interop_import_export_consistency_mode}" \
  INPUT_NOTIFY_ON="${input_notify_on}" \
  INPUT_NOTIFY_FORMAT="${input_notify_format}" \
  INPUT_NOTIFY_TIMEOUT_SEC="${input_notify_timeout_sec}" \
  INPUT_NOTIFY_MAX_RETRIES="${input_notify_max_retries}" \
  INPUT_NOTIFY_RETRY_BACKOFF_SEC="${input_notify_retry_backoff_sec}" \
  INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_WARN_MIN_EXEC_ROWS="${input_notify_runtime_lane_execution_warn_min_exec_rows}" \
  INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_HOLD_MIN_EXEC_ROWS="${input_notify_runtime_lane_execution_hold_min_exec_rows}" \
  INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_ARTIFACTS_WITH_DIFFS="${input_notify_runtime_evidence_compare_warn_min_artifacts_with_diffs}" \
  INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_ARTIFACTS_WITH_DIFFS="${input_notify_runtime_evidence_compare_hold_min_artifacts_with_diffs}" \
  INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT="${input_notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count}" \
  INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT="${input_notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count}" \
  INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_WARN_MIN="${input_notify_phase2_sensor_fidelity_score_avg_warn_min}" \
  INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_HOLD_MIN="${input_notify_phase2_sensor_fidelity_score_avg_hold_min}" \
  INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_WARN_MIN="${input_notify_phase2_sensor_frame_count_avg_warn_min}" \
  INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_HOLD_MIN="${input_notify_phase2_sensor_frame_count_avg_hold_min}" \
  INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_WARN_MAX="${input_notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max}" \
  INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_HOLD_MAX="${input_notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max}" \
  INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_WARN_MIN="${input_notify_phase2_sensor_lidar_point_count_avg_warn_min}" \
  INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_HOLD_MIN="${input_notify_phase2_sensor_lidar_point_count_avg_hold_min}" \
  INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_WARN_MAX="${input_notify_phase2_sensor_radar_false_positive_rate_avg_warn_max}" \
  INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_HOLD_MAX="${input_notify_phase2_sensor_radar_false_positive_rate_avg_hold_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_WARN_MAX="${input_notify_phase3_vehicle_control_overlap_ratio_warn_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_HOLD_MAX="${input_notify_phase3_vehicle_control_overlap_ratio_hold_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_WARN_MAX="${input_notify_phase3_vehicle_control_steering_rate_warn_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_HOLD_MAX="${input_notify_phase3_vehicle_control_steering_rate_hold_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_WARN_MAX="${input_notify_phase3_vehicle_control_throttle_plus_brake_warn_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_HOLD_MAX="${input_notify_phase3_vehicle_control_throttle_plus_brake_hold_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_WARN_MAX="${input_notify_phase3_vehicle_speed_tracking_error_warn_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_HOLD_MAX="${input_notify_phase3_vehicle_speed_tracking_error_hold_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_WARN_MAX="${input_notify_phase3_vehicle_speed_tracking_error_abs_warn_max}" \
  INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_HOLD_MAX="${input_notify_phase3_vehicle_speed_tracking_error_abs_hold_max}" \
  INPUT_MATRIX_PROFILE_IDS="${input_matrix_profile_ids}" \
  INPUT_RELEASE_ID="${input_release_id}" \
  python3 - "${dispatch_ref}" <<'PY'
import json
import os
import sys

ref = str(sys.argv[1]).strip()
inputs = {}
mapping = [
    ("sim_runtime", "INPUT_SIM_RUNTIME"),
    ("lane", "INPUT_LANE"),
    ("dry_run", "INPUT_DRY_RUN"),
    ("continue_on_runtime_failure", "INPUT_CONTINUE_ON_RUNTIME_FAILURE"),
    ("runtime_asset_profile", "INPUT_RUNTIME_ASSET_PROFILE"),
    ("runtime_asset_archive_sha256_mode", "INPUT_RUNTIME_ASSET_ARCHIVE_SHA256_MODE"),
    ("install_runtime_deps", "INPUT_INSTALL_RUNTIME_DEPS"),
    ("use_runtime_asset_cache", "INPUT_USE_RUNTIME_ASSET_CACHE"),
    ("asset_skip_download", "INPUT_ASSET_SKIP_DOWNLOAD"),
    ("asset_skip_extract", "INPUT_ASSET_SKIP_EXTRACT"),
    ("asset_force_download", "INPUT_ASSET_FORCE_DOWNLOAD"),
    ("asset_force_extract", "INPUT_ASSET_FORCE_EXTRACT"),
    ("runtime_native_docker_auto_enable", "INPUT_RUNTIME_NATIVE_DOCKER_AUTO_ENABLE"),
    ("runtime_native_docker_auto_dry_run", "INPUT_RUNTIME_NATIVE_DOCKER_AUTO_DRY_RUN"),
    ("runtime_native_sim_runtime", "INPUT_RUNTIME_NATIVE_SIM_RUNTIME"),
    ("runtime_native_summary_compare_fail_on_missing", "INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_MISSING"),
    ("runtime_native_summary_compare_fail_on_diffs", "INPUT_RUNTIME_NATIVE_SUMMARY_COMPARE_FAIL_ON_DIFFS"),
    ("runtime_threshold_drift_fail_on_hold", "INPUT_RUNTIME_THRESHOLD_DRIFT_FAIL_ON_HOLD"),
    ("phase2_log_replay_threshold_fail_on_hold", "INPUT_PHASE2_LOG_REPLAY_THRESHOLD_FAIL_ON_HOLD"),
    ("runtime_native_smoke_threshold_fail_on_hold", "INPUT_RUNTIME_NATIVE_SMOKE_THRESHOLD_FAIL_ON_HOLD"),
    ("runtime_native_evidence_compare_threshold_fail_on_hold", "INPUT_RUNTIME_NATIVE_EVIDENCE_COMPARE_THRESHOLD_FAIL_ON_HOLD"),
    ("runtime_scenario_contract_enable", "INPUT_RUNTIME_SCENARIO_CONTRACT_ENABLE"),
    ("runtime_scenario_contract_require_runtime_ready", "INPUT_RUNTIME_SCENARIO_CONTRACT_REQUIRE_RUNTIME_READY"),
    ("runtime_scene_result_enable", "INPUT_RUNTIME_SCENE_RESULT_ENABLE"),
    ("runtime_scene_result_require_runtime_ready", "INPUT_RUNTIME_SCENE_RESULT_REQUIRE_RUNTIME_READY"),
    ("runtime_interop_contract_enable", "INPUT_RUNTIME_INTEROP_CONTRACT_ENABLE"),
    ("runtime_interop_contract_require_runtime_ready", "INPUT_RUNTIME_INTEROP_CONTRACT_REQUIRE_RUNTIME_READY"),
    ("runtime_interop_export_road_length_scale", "INPUT_RUNTIME_INTEROP_EXPORT_ROAD_LENGTH_SCALE"),
    ("runtime_interop_contract_xosc", "INPUT_RUNTIME_INTEROP_CONTRACT_XOSC"),
    ("runtime_interop_contract_xodr", "INPUT_RUNTIME_INTEROP_CONTRACT_XODR"),
    ("runtime_interop_import_manifest_consistency_mode", "INPUT_RUNTIME_INTEROP_IMPORT_MANIFEST_CONSISTENCY_MODE"),
    ("runtime_interop_import_export_consistency_mode", "INPUT_RUNTIME_INTEROP_IMPORT_EXPORT_CONSISTENCY_MODE"),
    ("notify_on", "INPUT_NOTIFY_ON"),
    ("notify_format", "INPUT_NOTIFY_FORMAT"),
    ("notify_timeout_sec", "INPUT_NOTIFY_TIMEOUT_SEC"),
    ("notify_max_retries", "INPUT_NOTIFY_MAX_RETRIES"),
    ("notify_retry_backoff_sec", "INPUT_NOTIFY_RETRY_BACKOFF_SEC"),
    ("notify_runtime_lane_execution_warn_min_exec_rows", "INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_WARN_MIN_EXEC_ROWS"),
    ("notify_runtime_lane_execution_hold_min_exec_rows", "INPUT_NOTIFY_RUNTIME_LANE_EXECUTION_HOLD_MIN_EXEC_ROWS"),
    ("notify_runtime_evidence_compare_warn_min_artifacts_with_diffs", "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_ARTIFACTS_WITH_DIFFS"),
    ("notify_runtime_evidence_compare_hold_min_artifacts_with_diffs", "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_ARTIFACTS_WITH_DIFFS"),
    ("notify_runtime_evidence_compare_warn_min_interop_import_mode_diff_count", "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_WARN_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT"),
    ("notify_runtime_evidence_compare_hold_min_interop_import_mode_diff_count", "INPUT_NOTIFY_RUNTIME_EVIDENCE_COMPARE_HOLD_MIN_INTEROP_IMPORT_MODE_DIFF_COUNT"),
    ("notify_phase2_sensor_fidelity_score_avg_warn_min", "INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_WARN_MIN"),
    ("notify_phase2_sensor_fidelity_score_avg_hold_min", "INPUT_NOTIFY_PHASE2_SENSOR_FIDELITY_SCORE_AVG_HOLD_MIN"),
    ("notify_phase2_sensor_frame_count_avg_warn_min", "INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_WARN_MIN"),
    ("notify_phase2_sensor_frame_count_avg_hold_min", "INPUT_NOTIFY_PHASE2_SENSOR_FRAME_COUNT_AVG_HOLD_MIN"),
    ("notify_phase2_sensor_camera_noise_stddev_px_avg_warn_max", "INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_WARN_MAX"),
    ("notify_phase2_sensor_camera_noise_stddev_px_avg_hold_max", "INPUT_NOTIFY_PHASE2_SENSOR_CAMERA_NOISE_STDDEV_PX_AVG_HOLD_MAX"),
    ("notify_phase2_sensor_lidar_point_count_avg_warn_min", "INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_WARN_MIN"),
    ("notify_phase2_sensor_lidar_point_count_avg_hold_min", "INPUT_NOTIFY_PHASE2_SENSOR_LIDAR_POINT_COUNT_AVG_HOLD_MIN"),
    ("notify_phase2_sensor_radar_false_positive_rate_avg_warn_max", "INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_WARN_MAX"),
    ("notify_phase2_sensor_radar_false_positive_rate_avg_hold_max", "INPUT_NOTIFY_PHASE2_SENSOR_RADAR_FALSE_POSITIVE_RATE_AVG_HOLD_MAX"),
    ("notify_phase3_vehicle_control_overlap_ratio_warn_max", "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_WARN_MAX"),
    ("notify_phase3_vehicle_control_overlap_ratio_hold_max", "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_OVERLAP_RATIO_HOLD_MAX"),
    ("notify_phase3_vehicle_control_steering_rate_warn_max", "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_WARN_MAX"),
    ("notify_phase3_vehicle_control_steering_rate_hold_max", "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_STEERING_RATE_HOLD_MAX"),
    ("notify_phase3_vehicle_control_throttle_plus_brake_warn_max", "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_WARN_MAX"),
    ("notify_phase3_vehicle_control_throttle_plus_brake_hold_max", "INPUT_NOTIFY_PHASE3_VEHICLE_CONTROL_THROTTLE_PLUS_BRAKE_HOLD_MAX"),
    ("notify_phase3_vehicle_speed_tracking_error_warn_max", "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_WARN_MAX"),
    ("notify_phase3_vehicle_speed_tracking_error_hold_max", "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_HOLD_MAX"),
    ("notify_phase3_vehicle_speed_tracking_error_abs_warn_max", "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_WARN_MAX"),
    ("notify_phase3_vehicle_speed_tracking_error_abs_hold_max", "INPUT_NOTIFY_PHASE3_VEHICLE_SPEED_TRACKING_ERROR_ABS_HOLD_MAX"),
    ("matrix_profile_ids", "INPUT_MATRIX_PROFILE_IDS"),
    ("release_id", "INPUT_RELEASE_ID"),
]
for key, env_name in mapping:
    value = str(os.environ.get(env_name, "")).strip()
    if value:
        inputs[key] = value
payload = {"ref": ref}
if inputs:
    payload["inputs"] = inputs
print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
PY
)"

if [ "${dispatch_dry_run}" = "1" ]; then
  echo "[ok] dispatch_dry_run=1"
  echo "[ok] repository=${dispatch_repository:-n/a}"
  echo "[ok] workflow_file=${workflow_file}"
  echo "[ok] ref=${dispatch_ref}"
  echo "[ok] payload=${payload}"
  exit 0
fi

if [ -z "${dispatch_repository}" ]; then
  echo "[error] WORKFLOW_DISPATCH_REPOSITORY is required (or configure git remote origin to a GitHub repo)" >&2
  exit 2
fi

token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -z "${token}" ] && [ "${dispatch_allow_gh_cli_token}" = "1" ] && command -v gh >/dev/null 2>&1; then
  token="$(trim_text "$(gh auth token 2>/dev/null || true)")"
  if [ -n "${token}" ]; then
    echo "[info] dispatch_token_source=gh_auth"
  fi
fi
if [ -z "${token}" ]; then
  echo "[error] GH_TOKEN or GITHUB_TOKEN is required for workflow dispatch" >&2
  exit 2
fi

api_root="https://api.github.com/repos/${dispatch_repository}/actions/workflows/${workflow_file}"
headers=(
  -H "Accept: application/vnd.github+json"
  -H "Authorization: Bearer ${token}"
  -H "X-GitHub-Api-Version: 2022-11-28"
)

dispatch_started_epoch="$(date -u +%s)"
dispatch_body_file="$(mktemp)"
dispatch_status="$(
  curl -sS -o "${dispatch_body_file}" -w "%{http_code}" \
    -X POST "${headers[@]}" \
    "${api_root}/dispatches" \
    -d "${payload}"
)"
if [ "${dispatch_status}" != "204" ]; then
  echo "[error] workflow dispatch failed: http_status=${dispatch_status}" >&2
  cat "${dispatch_body_file}" >&2 || true
  rm -f "${dispatch_body_file}"
  exit 2
fi
rm -f "${dispatch_body_file}"
echo "[ok] workflow_dispatched repository=${dispatch_repository} workflow=${workflow_file} ref=${dispatch_ref}"

discover_deadline=$((dispatch_started_epoch + dispatch_discover_timeout_sec))
run_id=""
run_status="queued"
run_url=""
run_created_at=""
while [ "$(date -u +%s)" -le "${discover_deadline}" ]; do
  run_discovery_body_file="$(mktemp)"
  curl -fsS --get "${headers[@]}" "${api_root}/runs" \
    --data-urlencode "event=workflow_dispatch" \
    --data-urlencode "branch=${dispatch_ref}" \
    --data-urlencode "per_page=20" \
    > "${run_discovery_body_file}"
  run_line="$(
    python3 - "${dispatch_started_epoch}" "${run_discovery_body_file}" <<'PY'
import datetime
import json
import sys

started_epoch = int(sys.argv[1])
body_path = str(sys.argv[2]).strip()
try:
    with open(body_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    payload = {}
best = None
for run in payload.get("workflow_runs", []):
    created_at = str(run.get("created_at", "")).strip()
    try:
        created_epoch = int(
            datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
        )
    except Exception:
        continue
    if created_epoch < started_epoch - 180:
        continue
    run_id = run.get("id")
    if not isinstance(run_id, int):
        continue
    item = (
        created_epoch,
        run_id,
        str(run.get("status", "")).strip(),
        str(run.get("html_url", "")).strip(),
        created_at,
    )
    if best is None or item[0] > best[0]:
        best = item
if best is not None:
    print(f"{best[1]}|{best[2]}|{best[3]}|{best[4]}")
PY
  )"
  rm -f "${run_discovery_body_file}"
  if [ -n "${run_line}" ]; then
    IFS='|' read -r run_id run_status run_url run_created_at <<<"${run_line}"
    break
  fi
  sleep "${dispatch_poll_sec}"
done

if [ -z "${run_id}" ]; then
  echo "[error] workflow run discovery timed out after ${dispatch_discover_timeout_sec}s" >&2
  exit 2
fi
echo "[ok] run_discovered id=${run_id} status=${run_status} created_at=${run_created_at}"
echo "[ok] run_url=${run_url}"

if [ "${dispatch_wait}" != "1" ]; then
  exit 0
fi

run_conclusion=""
run_api="https://api.github.com/repos/${dispatch_repository}/actions/runs/${run_id}"
wait_deadline=$(( $(date -u +%s) + dispatch_timeout_sec ))
while :; do
  run_state_body_file="$(mktemp)"
  curl -fsS "${headers[@]}" "${run_api}" > "${run_state_body_file}"
  run_state_line="$(
    python3 - "${run_state_body_file}" <<'PY'
import json
import sys

body_path = str(sys.argv[1]).strip()
try:
    with open(body_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    payload = {}
status = str(payload.get("status", "")).strip()
conclusion = str(payload.get("conclusion", "")).strip()
url = str(payload.get("html_url", "")).strip()
print(f"{status}|{conclusion}|{url}")
PY
  )"
  rm -f "${run_state_body_file}"
  IFS='|' read -r run_status run_conclusion run_url <<<"${run_state_line}"
  if [ "${run_status}" = "completed" ]; then
    break
  fi
  if [ "$(date -u +%s)" -gt "${wait_deadline}" ]; then
    echo "[error] workflow run wait timed out after ${dispatch_timeout_sec}s (run_id=${run_id}, status=${run_status})" >&2
    exit 2
  fi
  sleep "${dispatch_poll_sec}"
done
echo "[ok] run_completed id=${run_id} conclusion=${run_conclusion:-n/a}"
echo "[ok] run_url=${run_url}"

jobs_body_file="$(mktemp)"
curl -fsS --get "${headers[@]}" "${run_api}/jobs" --data-urlencode "per_page=100" > "${jobs_body_file}"
jobs_line="$(
  python3 - "${jobs_body_file}" <<'PY'
import json
import sys

body_path = str(sys.argv[1]).strip()
try:
    with open(body_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    payload = {}
jobs = payload.get("jobs", [])
name_to_conclusion = {}
for job in jobs:
    if not isinstance(job, dict):
        continue
    name = str(job.get("name", "")).strip().lower()
    if not name:
        continue
    conclusion = str(job.get("conclusion", "")).strip().lower()
    name_to_conclusion[name] = conclusion
def _find(job_name: str) -> str:
    return name_to_conclusion.get(job_name.lower(), "")
print(
    "combined={combined}|runtime_available={runtime_available}|runtime_native={runtime_native}|jobs_count={count}".format(
        combined=_find("runtime-lanes-combined-summary"),
        runtime_available=_find("runtime-available"),
        runtime_native=_find("runtime-native-docker-auto-smoke"),
        count=len(jobs),
    )
)
PY
)"
rm -f "${jobs_body_file}"
combined_job_conclusion=""
runtime_available_job_conclusion=""
runtime_native_job_conclusion=""
jobs_count="0"
IFS='|' read -r combined_part runtime_available_part runtime_native_part jobs_count_part <<<"${jobs_line}"
combined_job_conclusion="${combined_part#combined=}"
runtime_available_job_conclusion="${runtime_available_part#runtime_available=}"
runtime_native_job_conclusion="${runtime_native_part#runtime_native=}"
jobs_count="${jobs_count_part#jobs_count=}"

echo "[ok] jobs_count=${jobs_count}"
echo "[ok] job_runtime_lanes_combined_summary=${combined_job_conclusion:-missing}"
echo "[ok] job_runtime_available=${runtime_available_job_conclusion:-missing}"
echo "[ok] job_runtime_native_docker_auto_smoke=${runtime_native_job_conclusion:-missing}"

if [ -z "${combined_job_conclusion}" ]; then
  echo "[error] combined summary job (runtime-lanes-combined-summary) not found in workflow run jobs" >&2
  exit 2
fi
if [ "${combined_job_conclusion}" != "success" ]; then
  echo "[error] combined summary job did not succeed: ${combined_job_conclusion}" >&2
  exit 2
fi
if [ "${dispatch_require_run_success}" = "1" ] && [ "${run_conclusion}" != "success" ]; then
  echo "[error] workflow run conclusion is not success: ${run_conclusion}" >&2
  exit 2
fi
