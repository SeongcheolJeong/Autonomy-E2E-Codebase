# P_E2E_Stack Prototype - One-command Pipeline

This script orchestrates:

1. Cloud batch execution
2. Scenario summary ingest into SQLite
3. Capability-gated validation report generation

## Recommended task runner

Use the local Makefile to reduce repeated command typing:

```bash
make help
make pipeline-smoke RELEASE_ID=REL_2026Q1_AUTO1
make pipeline-smoke RELEASE_ID=REL_2026Q1_AUTO1 REQUIREMENT_MAP=../../P_Validation-Tooling-MVP/prototype/requirement_maps/h0_highway_trace_v0.json
make pipeline-smoke RELEASE_ID=REL_2026Q1_AUTO1 STRICT_GATE=1
make pipeline-smoke RELEASE_ID=REL_2026Q1_AUTO1 STRICT_GATE=1 TREND_WINDOW=10 TREND_MIN_PASS_RATE=0.8 TREND_MIN_SAMPLES=3
```

## Planning and Progress Tracking

- Master plan (milestones/status): `STACK_MASTER_PLAN.md`
- Progress log (commit evidence): `STACK_PROGRESS_LOG.md`
- Progress log updater: `python3 update_stack_progress_log.py --entry "..."` (optional: `--date YYYY-MM-DD`)
- Module parity matrix (AppliedDocs ↔ local modules): `STACK_MODULE_PARITY_MATRIX.md`
- Phase-1 execution checklist: `PHASE1_MODULE_PARITY_CHECKLIST.md`
- Phase-2 execution checklist: `PHASE2_MODULE_PARITY_CHECKLIST.md`
- Phase-3 execution checklist: `PHASE3_MODULE_PARITY_CHECKLIST.md`
- Phase-4 execution checklist: `PHASE4_MODULE_PARITY_CHECKLIST.md`
- Phase-4 external references map: `PHASE4_EXTERNAL_REFERENCE_MAP.md`
- Project boundary contract: `PROJECT_BOUNDARY_CONTRACT.md`
- Execution-path whitelist scope: `ci_profiles/execution_path_scope.json`
- Quality gate command: `make validate`

Useful targets:

- `make pipeline-smoke`
- `make pipeline-dry-run`
- `make pipeline-matrix`
- `make pipeline-matrix-dry-run`
- `make pipeline-runtime-smoke`
- `make pipeline-runtime-smoke-dry-run`
- `make pipeline-runtime-native-entry-smoke-auto`
- `make pipeline-runtime-native-entry-smoke-auto-dry-run`
- `make pipeline-runtime-native-entry-smoke-auto-both-compare`
- `make pipeline-runtime-native-entry-smoke-auto-both-compare-dry-run`
- `make pipeline-runtime-available-smoke`
- `make pipeline-runtime-available-smoke-dry-run`
- `make pipeline-runtime-available-contract-smoke`
- `make pipeline-runtime-available-contract-smoke-dry-run`
- `make pipeline-runtime-available-auto`
- `make pipeline-runtime-available-auto-dry-run`
- `make pipeline-runtime-available-auto-docker-linux`
- `make pipeline-runtime-available-auto-docker-linux-dry-run`
- `make pipeline-runtime-available-auto-docker-linux-fast-nobuild-both-compare`
- `make pipeline-runtime-available-auto-docker-linux-fast-nobuild-both-compare-dry-run`
- `make workflow-runtime-available-dispatch-both-exec`
- `make workflow-runtime-available-dispatch-both-exec-dry-run`
- `make runtime-native-summary-compare`
- `make runtime-evidence-compare`
- `make pipeline-matrix-summary`
- `make pipeline-matrix-summary-dry-run`
- `make quick-cycle`
- `make cloud-regression`
- `make regression-check`
- `make generate-sds-batches`
- `make ingest-release`
- `make release-latest`
- `make release-hold-reasons`
- `make release-trend`
- `make release-compare`
- `make release-diff`
- `make build-release-summary`
- `make build-notification-payload`
- `make send-notification-dry-run`
- `make adp-trace-smoke`
- `make copilot-contract-smoke`
- `make copilot-release-assist-smoke`
- `make phase4-linkage-smoke`
- `make phase4-reference-pattern-smoke`
- `make phase1-regression`
- `make phase4-regression`
- `make phase4-contract-smoke`
- `make unit-test`
- `make shell-lint` (shellcheck 설치 시 shell 스크립트 정적 점검)
- `make sync-pr-quick-scope`
- `make check-pr-quick-scope`
- `make sync-nightly-matrix-scope`
- `make check-nightly-matrix-scope`
- `make sync-ci-scopes`
- `make check-ci-scopes`
- `make check-project-boundary`
- `make validate`

`make pipeline-smoke`/`make pipeline-dry-run`은 `run_ci_pipeline.py`를 경유해 입력 정규화와 실행 옵션 해석을 공통 처리합니다.
`make phase1-regression`은 Phase-1 핵심 계약(cloud/data_explorer/validation/object_sim 관련 unittest 묶음)만 빠르게 회귀 확인할 때 사용합니다.
`make phase4-regression`은 Phase-4 핵심 계약(링키지/ADP/Copilot + stack integration failure paths)만 빠르게 묶어 회귀 확인할 때 사용합니다.
`phase4_reference_pattern_scan_stub.py`는 Primary reference coverage를 gate로 유지하면서, `PHASE4_EXTERNAL_REFERENCE_MAP.md`의 `Secondary Module-to-Reference Mapping` 섹션이 있으면 secondary coverage를 리포트 필드(`secondary_*`)로 함께 기록합니다.
필요하면 `PHASE4_REFERENCE_PATTERN_SECONDARY_MIN_COVERAGE_RATIO`(make) 또는 `--phase4-reference-secondary-min-coverage-ratio`(runner)를 지정해 secondary coverage를 선택적으로 gate할 수 있습니다.
`run_e2e_pipeline.py`의 `phase4_hooks.reference_pattern_scan`은 secondary coverage 요약(`reference_pattern_secondary_*`)을 함께 노출하고, `build_release_summary_artifact.py`/`render_release_summary_markdown.py` 파이프라인 개요에 해당 지표가 있으면 함께 표시합니다.
`run_e2e_pipeline.py`의 `phase3_hooks.vehicle_dynamics` 요약(`vehicle_dynamics_model`, `step_count`, `initial_*`, `final_*`, `*_road_grade_percent`, `max_abs_grade_force_n`)도 `build_release_summary_artifact.py`/`render_release_summary_markdown.py`에서 집계(`phase3_vehicle_dynamics_summary`)되며, final 지표와 함께 delta(`final-initial`) speed/position 통계, heading/lateral/yaw(최대 절대 yaw rate, lateral absolute peak 포함) 통계, accel/jerk peak 통계, road-grade/grade-force 통계, 그리고 control sequence의 `target_speed_mps` 기준 speed-tracking error envelope(`min/avg/max`, `abs_avg/abs_max`)도 개별 release 개요로 표시합니다. `vehicle_dynamics_stub.py`는 선택적으로 `enable_planar_kinematics` + `steering_angle_deg`를 받아 heading/yaw/x-y trace를 계산하는 planar bicycle 보강 모델(`planar_bicycle_force_balance_v1`)과, `enable_dynamic_bicycle` 시 질량/요관성/코너링 강성을 반영한 동적 bicycle 보강 모델(`planar_dynamic_bicycle_force_balance_v1`)을 지원합니다.
추가로 `phase3_hooks.sim_runtime_adapter`는 선택적 runtime scaffold(`--sim-runtime awsim|carla`)를 실행해 headless 렌더링 연계용 어댑터 리포트와 launch manifest 경로(`launch_manifest_out`)를 남깁니다(`sim_runtime_adapter_stub.py`).
`--sim-runtime-probe-enable`를 함께 쓰면 `phase3_hooks.sim_runtime_probe`에 runtime binary availability/probe 결과가 기록됩니다(`sim_runtime_probe_runner.py`).
`phase3_hooks.sim_runtime_interop_contract`는 launch manifest 기반 interop export(`sim_runtime_interop_export_runner.py`) 뒤에 roundtrip import(`sim_runtime_interop_import_runner.py`)를 자동 수행해 exported XOSC/XODR의 actor-count manifest 일관성(`interop_import_manifest_consistent`)을 함께 기록합니다.
`sim_runtime_probe_runner.py`는 `carla --help` probe가 에뮬레이션된 `linux/amd64` 환경에서 `returncode=132` + `stderr=Illegal instruction`으로 종료되는 케이스를 실행 가능성 증거(`probe_returncode_acceptable=true`)로 취급합니다.
`run_ci_matrix_pipeline.py`에서 `--sim-runtime-assert-artifacts-input true`를 주면 각 profile 완료 후 `ci_manifest_path`를 읽어 `phase3_hooks.sim_runtime_adapter`/`sim_runtime_probe` 아티팩트 존재성뿐 아니라 schema/runtime contract(`runtime_entrypoint`, `reference_repo`, `bridge_contract`) 및 요구조건(`probe_executed`, `runtime_available`, `runtime_bin_resolved`)까지 추가 검증합니다.
`build_release_notification_payload.py`는 `phase3_vehicle_dynamics_summary`가 있으면 notification payload/text/slack blocks에도 final+delta+heading/lateral/yaw+grade 요약을 함께 포함하고, phase3 speed/position/delta/grade warn/hold threshold가 설정된 경우 `phase3_vehicle_dynamics_violation_rows`(배치별 위반 증거)와 `phase3_vehicle_dynamics_violation_summary`(severity+metric 집계 요약)를 함께 기록합니다.
notification 경고 정책에는 primary coverage 기반(`NOTIFY_PHASE4_PRIMARY_WARN_RATIO`, `NOTIFY_PHASE4_PRIMARY_HOLD_RATIO`), secondary total coverage 기반(`NOTIFY_PHASE4_SECONDARY_WARN_RATIO`, `NOTIFY_PHASE4_SECONDARY_HOLD_RATIO`, `NOTIFY_PHASE4_SECONDARY_WARN_MIN_MODULES`), module별 secondary coverage 기반(`NOTIFY_PHASE4_SECONDARY_MODULE_WARN_THRESHOLDS`, `NOTIFY_PHASE4_SECONDARY_MODULE_HOLD_THRESHOLDS`, 예: `adp=0.8,copilot=0.7`) 옵션, 그리고 Phase-3 vehiclesim 집계 기반 속도/위치/delta/heading/lateral/yaw/accel/jerk/road-grade/grade-force 및 control-input(overlap ratio/steering rate/throttle+brake sum) warn·hold 임계치(`NOTIFY_PHASE3_VEHICLE_*`) 옵션을 함께 적용할 수 있습니다.
`make pipeline-matrix`/`make pipeline-matrix-dry-run`은 `run_ci_matrix_pipeline.py`를 통해 selected profile들을 순차 실행하고 matrix 요약 JSON을 생성합니다.
`make runtime-assets-prepare`는 CARLA/AWSIM 공식 배포 매니페스트(`P_Sim-Engine/prototype/examples/runtime_assets_manifest_v0.json`)를 기준으로 런타임 바이너리 + 맵/시나리오 리소스를 다운로드/압축해제하고, 런타임별 resolved manifest(`RUNTIME_ASSET_RESOLVED_OUT`)와 env 파일(`RUNTIME_ASSET_ENV_OUT`)을 생성합니다. 필요하면 `RUNTIME_ASSET_PREP_REQUIRE_HOST_COMPATIBLE=1`로 현재 호스트와 `target_platforms` 불일치를 준비 단계에서 즉시 차단할 수 있고, `RUNTIME_ASSET_PREP_ARCHIVE_SHA256_MODE=always|verify_only|never`로 archive sha256 계산 비용/검증 강도를 조정할 수 있습니다.
`make pipeline-runtime-smoke`/`make pipeline-runtime-smoke-dry-run`은 runtime 전용 matrix profile(`ci_profiles/runtime_matrix_profiles.json`)을 사용해 `PHASE3_ENABLE_HOOKS=1` + `SIM_RUNTIME=<carla|awsim>` headless 스모크를 수동 실행/점검할 때 사용합니다.
`make pipeline-runtime-native-entry-smoke-auto`/`make pipeline-runtime-native-entry-smoke-auto-dry-run`은 `run_ci_pipeline_entry.sh` 기반 runtime-native 경로를 호스트별(direct/docker)로 자동 라우팅하며, `RUNTIME_NATIVE_ENTRY_SIM_RUNTIME=both`일 때 `awsim/carla`를 순차 실행합니다(`RUNTIME_NATIVE_ENTRY_AUTO_CONTINUE_ON_RUNTIME_FAILURE=1|0`).
`make pipeline-runtime-native-entry-smoke-auto-both-compare`/`make pipeline-runtime-native-entry-smoke-auto-both-compare-dry-run`은 runtime-native both 실행 뒤 `awsim/carla` summary를 한 번에 비교하는 one-click 경로입니다. 비교 리포트는 기본으로 `$(REPORT_DIR)/$(RELEASE_ID)_runtime_native_both_summary_compare.{json,txt}`에 생성됩니다.
`e2e-runtime-available.yml`의 `runtime-native-docker-auto-smoke` job은 runtime contract 입력(`runtime_scenario_contract_require_runtime_ready`, `runtime_scene_result_require_runtime_ready`, `runtime_interop_contract_require_runtime_ready`)을 runtime-native make 호출(`RUNTIME_NATIVE_ENTRY_SIM_RUNTIME_*_REQUIRE_RUNTIME_READY`)로 그대로 전달합니다.
`make runtime-native-summary-compare`는 이미 생성된 runtime-native summary 쌍(`RUNTIME_NATIVE_SUMMARY_COMPARE_LEFT_RELEASE_PREFIX`, `RUNTIME_NATIVE_SUMMARY_COMPARE_RIGHT_RELEASE_PREFIX`)을 SDS 버전 목록(`RUNTIME_NATIVE_SUMMARY_COMPARE_VERSIONS`) 기준으로 비교해 `final_result/gate_result/requirement_result/run_count/collision_count/hold_reason_codes` 차이를 요약합니다.
runtime matrix profile 행에는 선택적으로 `sim_runtime`, `sim_runtime_scene`, `sim_runtime_sensor_rig`, `sim_runtime_mode`를 넣어 profile별 런타임 타깃/scene을 오버라이드할 수 있습니다(예: 한 matrix에서 `runtime_carla_smoke_v0` + `runtime_awsim_smoke_v0` 동시 실행).
`make pipeline-runtime-available-smoke`/`make pipeline-runtime-available-smoke-dry-run`은 runtime available lane 정책(`SIM_RUNTIME_PROBE_EXECUTE=1`, `SIM_RUNTIME_PROBE_REQUIRE_AVAILABILITY=1`, `SIM_RUNTIME_SCENARIO_CONTRACT_ENABLE=1`, `SIM_RUNTIME_SCENE_RESULT_ENABLE=1`, `SIM_RUNTIME_INTEROP_CONTRACT_ENABLE=1`, `SIM_RUNTIME_ASSERT_ARTIFACTS=1`)을 기본 적용해 외부 런타임 연결 환경에서 실행 계약(Block C)과 시나리오/scene result/interop 계약(Block A1~A3)을 함께 검증할 때 사용합니다. 이 lane은 기본으로 runtime asset host precheck(`RUNTIME_AVAILABLE_PRECHECK_RUNTIME_ASSETS=1`, `RUNTIME_AVAILABLE_ASSET_REQUIRE_HOST_COMPATIBLE=1`)를 수행하므로, 현재 매니페스트가 `linux_x86_64` 번들만 제공하는 경우 Darwin arm64에서는 실행 전에 조기 실패합니다. 기본으로 `RUNTIME_AVAILABLE_RUNTIME_EVIDENCE_OUT` 경로에 runtime evidence JSON도 함께 기록합니다(`--runtime-evidence-out`). runtime-available precheck에서는 기본값으로 `RUNTIME_AVAILABLE_ASSET_ARCHIVE_SHA256_MODE=verify_only`를 사용해 expected sha256가 없는 대형 아카이브의 반복 해시 계산 비용을 줄입니다.
`e2e-runtime-available.yml`의 `matrix_profile_ids` 기본값은 `runtime_carla_smoke_v0,runtime_awsim_smoke_v0`이며, 각 runtime iteration에서 `ci_profiles/runtime_matrix_profiles.json`의 `sim_runtime` override를 기준으로 해당 runtime에 맞는 profile id만 자동 선택해 중복 실행을 방지합니다.
`e2e-runtime-available.yml`의 `workflow_dispatch`에서는 runtime exec-row 임계치(`notify_runtime_lane_execution_*`) 외에도 phase3 control-input 임계치(`notify_phase3_vehicle_control_overlap_ratio_*`, `notify_phase3_vehicle_control_steering_rate_*`, `notify_phase3_vehicle_control_throttle_plus_brake_*`)와 runtime contract block on/off 입력(`runtime_scenario_contract_*`, `runtime_scene_result_*`, `runtime_interop_contract_*`)을 직접 지정해 runtime lane summary/notification 단계에 동일 정책을 적용할 수 있습니다.
GitHub Actions 실행을 Makefile에서 바로 고정 운영하려면 `make workflow-runtime-available-dispatch-both-exec`/`make workflow-runtime-available-dispatch-both-exec-dry-run`을 사용합니다(`sim_runtime=both`, `lane=exec`, `continue_on_runtime_failure=0`, `WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_REQUIRE_RUN_SUCCESS=1`).
dispatch 경로에서 runtime contract require-ready/interop 입력도 직접 지정할 수 있습니다 (`WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_RUNTIME_SCENARIO_CONTRACT_REQUIRE_RUNTIME_READY`, `WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_RUNTIME_SCENE_RESULT_REQUIRE_RUNTIME_READY`, `WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_RUNTIME_INTEROP_CONTRACT_REQUIRE_RUNTIME_READY`, `WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_RUNTIME_INTEROP_CONTRACT_XOSC`, `WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_RUNTIME_INTEROP_CONTRACT_XODR`).
같은 dispatch 경로에서 summary/notification 정책도 함께 오버라이드할 수 있습니다 (`WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_NOTIFY_ON`, `..._NOTIFY_FORMAT`, `..._NOTIFY_TIMEOUT_SEC`, `..._NOTIFY_MAX_RETRIES`, `..._NOTIFY_RUNTIME_EVIDENCE_COMPARE_*`, `..._NOTIFY_PHASE2_SENSOR_*`, `..._NOTIFY_PHASE3_VEHICLE_*`).
runtime asset/runner 토글(`WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_RUNTIME_ASSET_PROFILE`, `..._RUNTIME_ASSET_ARCHIVE_SHA256_MODE`, `..._INSTALL_RUNTIME_DEPS`, `..._USE_RUNTIME_ASSET_CACHE`, `..._ASSET_SKIP_*`, `..._ASSET_FORCE_*`)도 dispatch 변수로 바로 제어할 수 있습니다.
`run_runtime_available_workflow_dispatch.sh`는 dispatch 전에 입력을 fail-fast 검증합니다(바이너리 플래그/enum/timeout·retry 정수/interop scale 양수/phase2·phase3 threshold 타입·범위 및 phase2 warn-hold pair 관계).
`workflow-runtime-available-dispatch` 경로는 기본적으로 `GH_TOKEN`/`GITHUB_TOKEN`을 사용하고, 미설정 시 `WORKFLOW_RUNTIME_AVAILABLE_DISPATCH_ALLOW_GH_CLI_TOKEN=1`(기본값)일 때 `gh auth token`을 fallback으로 사용합니다.
`make pipeline-runtime-available-contract-smoke`/`make pipeline-runtime-available-contract-smoke-dry-run`은 로컬 비호환 호스트에서도 계약 검증 흐름을 유지하기 위한 lane입니다. contract lane은 runtime asset precheck를 host-compat 강제 없이 먼저 실행해(`RUNTIME_AVAILABLE_CONTRACT_ASSET_REQUIRE_HOST_COMPATIBLE=0`) resolved env의 `SIM_RUNTIME_PROBE_RUNTIME_BIN`을 자동 주입하고, probe 실행은 끄되(`SIM_RUNTIME_PROBE_EXECUTE=0`) 가용성 계약(`SIM_RUNTIME_PROBE_REQUIRE_AVAILABILITY`)과 artifact assert는 유지합니다. 즉, 런타임 실행 가능성은 Linux 실행 노드에서 `pipeline-runtime-available-smoke`로 검증하고, 로컬에서는 contract lane으로 배선/아티팩트 계약을 빠르게 확인하는 2-lane 운영을 권장합니다.
`make pipeline-runtime-available-auto`/`make pipeline-runtime-available-auto-dry-run`은 호스트 기준으로 lane을 자동 선택합니다(`Linux + x86_64 => exec`, 그 외 => contract). 필요하면 `RUNTIME_AVAILABLE_AUTO_FORCE_LANE=exec|contract|auto`로 강제 오버라이드할 수 있고, `RUNTIME_AVAILABLE_SIM_RUNTIME=both`일 때는 `resolve_runtime_matrix_profile_ids.py`를 통해 runtime별 profile id를 분리해 `awsim`/`carla`를 순차 실행합니다(`RUNTIME_AVAILABLE_AUTO_CONTINUE_ON_RUNTIME_FAILURE=1|0`).
`make pipeline-runtime-available-auto-docker-linux`/`make pipeline-runtime-available-auto-docker-linux-dry-run`은 로컬 macOS에서도 `linux/amd64` 컨테이너 안에서 동일 auto lane을 실행/드라이런할 때 사용합니다. Docker가 설치된 환경에서 Linux runner 재현 검증을 반복할 때 권장하며, 내부에서 Ubuntu 의존성 설치 후 `pipeline-runtime-available-auto`(또는 dry-run)를 그대로 호출합니다.
반복 실행 속도를 높이려면 `make pipeline-runtime-available-docker-image-build`로 preinstalled runner 이미지를 먼저 빌드하고, `make pipeline-runtime-available-auto-docker-linux-fast`/`make pipeline-runtime-available-auto-docker-linux-fast-dry-run`을 사용하면 컨테이너 시작 시 apt bootstrap을 생략합니다(`RUNTIME_AVAILABLE_DOCKER_SKIP_APT=1`). 이미지가 이미 준비된 상태에서 재빌드까지 건너뛰려면 `make pipeline-runtime-available-auto-docker-linux-fast-nobuild`/`make pipeline-runtime-available-auto-docker-linux-fast-nobuild-dry-run`을 사용합니다. `make pipeline-runtime-available-auto-docker-linux-fast-nobuild-both-compare`는 `both+exec`를 먼저 실행한 뒤 `awsim/carla` runtime evidence를 바로 비교 report로 생성합니다.
`make runtime-evidence-compare`는 두 runtime evidence JSON(`RUNTIME_EVIDENCE_COMPARE_LEFT`, `RUNTIME_EVIDENCE_COMPARE_RIGHT`)을 비교해 top-level 불일치, status/runtime count 차이, profile 단위 diff를 `RUNTIME_EVIDENCE_COMPARE_OUT_JSON`/`RUNTIME_EVIDENCE_COMPARE_OUT_TEXT`로 출력합니다. Linux runner에서 생성한 CARLA/AWSIM evidence를 빠르게 교차 점검할 때 사용합니다.
`build_release_summary_artifact.py`는 runtime evidence artifact(`*runtime*evidence*.json`)를 자동 스캔해 `runtime_evidence_summary`를 release summary JSON/text에 포함하고, `render_release_summary_markdown.py`/`build_release_notification_payload.py`도 동일 요약을 표시합니다. runtime evidence 실패 레코드가 있으면 notification payload는 `runtime_evidence_warning`을 함께 남기고 상태를 `WARN`으로 승격할 수 있습니다(기존 `HOLD`는 유지).
`MATRIX_CONTINUE_ON_ERROR=1`이면 실패 profile이 있어도 다음 profile을 계속 실행하고, `MATRIX_MAX_FAILURES`(기본 0, 비활성)로 실패 누적 임계치 조기 중단을 설정할 수 있습니다.
`make pipeline-matrix-summary`/`make pipeline-matrix-summary-dry-run`은 matrix 실행 이후 `run_ci_summary.py`까지 연쇄 실행해 summary/notification payload 아티팩트를 즉시 생성합니다(실행 실패가 있어도 summary는 생성 후 마지막에 상태코드 반환). 이 summary 체인은 `SUMMARY_NOTIFY_PROFILE`(`nightly` 기본, `pr_quick|nightly|runtime_available|generic`)에 맞춰 phase3 dataset traffic notify 기본 임계치를 자동 선택하며, `NOTIFY_PHASE3_DATASET_TRAFFIC_*`를 명시하면 해당 값으로 우선 오버라이드됩니다.
`make generate-sds-batches`는 `generate_sds_batches.py`를 사용해 `SDS_VERSIONS`(공백/콤마 구분) 목록 전체에 대해 배치 스펙을 일괄 생성합니다.
필요하면 `SDS_BATCH_OUTPUT_DIR`, `SDS_BATCH_ID_PREFIX`, `SDS_RUN_ID_PREFIX_BASE`, `SDS_SEED_BASE`, `SDS_SEED_VERSION_STRIDE`, `SDS_MATRIX_PROFILES_OUT`로 출력 위치/ID/seed/matrix profile 파일을 조정할 수 있습니다.
`SUMMARY_FILES_ROOT`(기본 `REPORT_DIR`)로 report summary 스캔 범위를, `PIPELINE_MANIFESTS_ROOT`로 pipeline manifest 스캔 범위를 분리해 전체 스캔 비용을 줄일 수 있습니다.
다운로드된 아티팩트 레이아웃(`downloaded_artifacts/<artifact-name>/...`)에서는 `SUMMARY_FILES_SUBPATH`, `PIPELINE_MANIFESTS_SUBPATH`를 함께 주면 하위 경로만 선별 스캔해 탐색 비용을 더 줄일 수 있습니다.

Regression suite input:

- batch spec: `../../P_Cloud-Engine/prototype/examples/batch_regression_highway_v0.json`
- catalog manifest: `../../P_Sim-Engine/prototype/scenario_catalog/highway_regression_v0/catalog_manifest.json`

## Run pipeline

```bash
python3 run_e2e_pipeline.py \
  --batch-spec ../../P_Cloud-Engine/prototype/examples/batch_smoke_v0.json \
  --release-id REL_2026Q1_AUTO1 \
  --requirement-map ../../P_Validation-Tooling-MVP/prototype/requirement_maps/h0_highway_trace_v0.json \
  --trend-window 10 \
  --trend-min-pass-rate 0.8 \
  --trend-min-samples 3 \
  --strict-gate
```

Phase-2 hook integration (optional):

```bash
python3 run_e2e_pipeline.py \
  --batch-spec ../../P_Cloud-Engine/prototype/examples/batch_smoke_v0.json \
  --release-id REL_2026Q1_AUTO1 \
  --phase2-enable-hooks
```

`--phase2-enable-hooks` is wired through `run_ci_pipeline.py` (`--phase2-enable-hooks-input true`) and recorded under `phase2_hooks` in `pipeline_result.json`.

Phase-3 synthetic dataset hook integration (optional):

```bash
python3 run_e2e_pipeline.py \
  --batch-spec ../../P_Cloud-Engine/prototype/examples/batch_smoke_v0.json \
  --release-id REL_2026Q1_AUTO1 \
  --phase3-enable-hooks \
  --dataset-id DATASET_2026Q1_AUTO1 \
  --dataset-manifest-out ../../P_Data-Lake-and-Explorer/prototype/data/dataset_manifest_2026Q1_AUTO1.json
```

`--phase3-enable-hooks` is wired through `run_ci_pipeline.py` (`--phase3-enable-hooks-input true`) and recorded under `phase3_hooks` in `pipeline_result.json`.
`phase3_hooks.vehicle_dynamics`에는 `vehicle_dynamics_stub.py` 출력에서 추출한 요약(`vehicle_dynamics_model`, `step_count`, `initial_*`, `final_*`, `min/avg/max_road_grade_percent`, `max_abs_grade_force_n`, `speed_tracking_*`)이 함께 기록됩니다.

When enabled, Phase-3 hook runs:

1. `neural_scene_bridge.py` (`--log-scene` -> `--neural-scene-out`)
2. `render_neural_sensor_stub.py` (`--neural-scene-out` + `--neural-render-sensor-rig` -> `--neural-render-out`)
3. Optional runtime adapter scaffold (`--sim-runtime awsim|carla`) via `sim_runtime_adapter_stub.py` (adapter report + runtime launch manifest)
4. Optional runtime probe scaffold (`--sim-runtime-probe-enable`) via `sim_runtime_probe_runner.py` (availability/probe report)
5. `vehicle_dynamics_stub.py` (`--vehicle-profile` + `--control-sequence` -> `--vehicle-dynamics-out`)
6. `build_dataset_manifest.py` + ingest (`--dataset-manifest-out`)

`vehicle_dynamics_stub.py` control-sequence는 선택적으로 `default_road_grade_percent`와 각 command의 `road_grade_percent`를 받아 경사(+) / 내리막(-) 중력 성분을 longitudinal force balance에 반영합니다.

Phase-4 HIL/ADP/linkage hook integration (optional):

```bash
python3 run_e2e_pipeline.py \
  --batch-spec ../../P_Cloud-Engine/prototype/examples/batch_smoke_v0.json \
  --release-id REL_2026Q1_AUTO1 \
  --phase4-enable-hooks \
  --phase4-require-done \
  --hil-interface ../../P_Autoware-Workspace-CI-MVP/prototype/examples/hil_interface_v0.json \
  --hil-sequence ../../P_Autoware-Workspace-CI-MVP/prototype/examples/hil_test_sequence_v0.json \
  --hil-max-runtime-sec 180 \
  --adp-trace-out ../../P_E2E_Stack/prototype/reports/adp_workflow_trace_v0.json \
  --phase4-linkage-out ../../P_E2E_Stack/prototype/reports/phase4_module_linkage_report_v0.json \
  --hil-schedule-out ../../P_Autoware-Workspace-CI-MVP/prototype/runs/hil_schedule_manifest_v0.json
```

`--phase4-enable-hooks` is wired through `run_ci_pipeline.py` (`--phase4-enable-hooks-input true`) and recorded under `phase4_hooks` (`adp_hooks`, `module_linkage`) in `pipeline_result.json`.
`--phase4-require-done`이 설정되면 `phase4_hooks.module_linkage.phase4_status`가 `PHASE4_DONE`이 아닐 때 파이프라인이 실패합니다.

Phase-4 Copilot hook integration (optional):

```bash
python3 run_e2e_pipeline.py \
  --batch-spec ../../P_Cloud-Engine/prototype/examples/batch_smoke_v0.json \
  --release-id REL_2026Q1_AUTO1 \
  --phase4-enable-hooks \
  --phase4-enable-copilot-hooks \
  --copilot-mode scenario \
  --copilot-prompt "Generate a safe merge scenario with one cut-in actor." \
  --copilot-contract-out ../../P_E2E_Stack/prototype/reports/copilot_prompt_contract_v0.json \
  --copilot-release-assist-out ../../P_E2E_Stack/prototype/reports/copilot_release_assist_hook_v0.json
```

`--phase4-enable-copilot-hooks` is wired through `run_ci_pipeline.py` (`--phase4-enable-copilot-hooks-input true`) and recorded under `phase4_hooks.copilot_hooks` in `pipeline_result.json`.

By default it uses:

- DB: `../P_Data-Lake-and-Explorer/prototype/data/scenario_lake_v0.sqlite`
- Gate profile: `../P_Validation-Tooling-MVP/prototype/gate_profiles/h0_highway_sanity_v0.json`
- Requirement map: `../P_Validation-Tooling-MVP/prototype/requirement_maps/h0_highway_trace_v0.json`
- Report dir: `../P_Validation-Tooling-MVP/prototype/reports`

## Dry run

```bash
python3 run_e2e_pipeline.py \
  --batch-spec ../../P_Cloud-Engine/prototype/examples/batch_smoke_v0.json \
  --release-id REL_2026Q1_AUTO1 \
  --dry-run
```

## Output

- Batch artifacts under Cloud batch output root
- Updated SQLite lake (scenario run summaries + release assessment summaries)
- Optional dataset manifest artifact + ingest record (when `phase3` hooks enabled)
- Per-version release reports (`*.md`) and report summaries (`*.summary.json`)
- Aggregated release decision template (`<release_id>_release_decision.md`)
- `pipeline_result.json` manifest inside the batch directory

## CI Workflows

GitHub Actions workflows:

- `.github/workflows/e2e-pr-quick.yml`
  - trigger: `pull_request` (path filters + changed-file precheck), `workflow_dispatch`
  - purpose: 빠른 smoke pipeline 검증 + 실행 결과 요약 아티팩트 생성
- `.github/workflows/e2e-nightly.yml`
  - trigger: weekly schedule (`0 1 * * 0`), `workflow_dispatch`
  - purpose: full/nightly 성격의 정기 실행 (기본: SDS 버전별 2개 매트릭스 프로필)
- `.github/workflows/e2e-runtime-available.yml`
  - trigger: `workflow_dispatch`
  - purpose: runtime available lane 전용 수동 실행 (AWSIM/CARLA 단일 또는 동시(`sim_runtime=both`) 실행, auto/exec/contract lane 선택, runtime asset 옵션 조정(`runtime_asset_profile`, `runtime_asset_archive_sha256_mode`), Linux runtime dependency 설치/`dry_run`/runtime asset cache 지원, resolved lane/runner platform 요약 + exec lane host guard + 다중 런타임 실패 후 계속 실행 옵션(`continue_on_runtime_failure`) + 입력값 fail-fast 검증 + 런타임별 실행 결과 JSON 요약 출력(각 row에 runtime evidence 경로/존재 여부 + runtime별 missing evidence count(`runtime_evidence_missing_runtime_counts`) 포함) + Job Summary evidence 존재율(`present/exists/missing/unknown`) 게시 + evidence missing runtime csv(`runtime_evidence_missing_runtimes_csv`) 및 runtime별 count string(`runtime_execution_evidence_missing_runtime_counts`) 노출 + `sim_runtime=both`일 때 AWSIM↔CARLA runtime evidence compare report(`runtime_evidence_compare_json_path`, `runtime_evidence_compare_text_path`, `runtime_evidence_compare_summary`) 자동 생성/노출)

`workflow_dispatch` 입력으로 PR Quick/Nightly 모두 기본 batch/profile/gate/trend/notification 옵션과 함께 `notify_phase3_vehicle_*`(speed/position/delta/heading/lateral/yaw/accel/jerk/road-grade/grade-force warn·hold) 임계치를 커스터마이즈할 수 있습니다.
`strict_gate=true`일 때 `overall_result=HOLD`면 workflow가 실패합니다 (`PR Quick` 기본값: false, `Nightly` 기본값: true).
PR Quick/Nightly 파이프라인 실행 단계는 `PHASE4_ENABLE_HOOKS_INPUT=true`, `PHASE4_REQUIRE_DONE_INPUT=true`를 고정 적용해 Phase-4 linkage 상태가 `PHASE4_DONE`이 아닌 경우 실패합니다.
PR Quick은 실행 후 `e2e-pr-quick-summary-<run_id>-<run_attempt>` 아티팩트(`pr_quick_release_diff.txt`, `pr_quick_release_summary.json`, `pr_quick_notification_payload.json`, `pr_quick_release_summary.sqlite`)를 업로드합니다.
PR Quick/Nightly 요약은 GitHub Actions Job Summary에도 텍스트로 게시됩니다.
PR Quick에서 `batch_spec`이 비어 있고 `quick_profile_id` 또는 `quick_profile_file`을 지정하면 프로필 기반 배치를 사용합니다.
PR Quick/Nightly는 요약 JSON 기반 알림 payload(`*_notification_payload.json`)도 생성합니다.
PR Quick/Nightly는 파이프라인 실행 전에 `make phase1-regression`과 `make phase4-regression`을 순서대로 실행해 핵심 계약 회귀를 빠르게 확인하고, 이후 `make validate`를 수행합니다.
저장소 시크릿 `E2E_NOTIFICATION_WEBHOOK_URL`이 설정되어 있으면 payload를 웹훅으로 POST합니다(미설정 시 자동 skip).
웹훅 전송은 `notify_on`(always/hold/warn/hold_warn/pass/never), `notify_format`(slack/raw), `notify_timeout_sec`(기본 10), `notify_max_retries`(기본 2), `notify_retry_backoff_sec`(기본 2) 정책을 따릅니다.
웹훅 전송 재시도는 `send_release_notification.py`에서 처리하며, 일시적 실패(HTTP `408`/`429`/`5xx`, URL 오류)에 대해 `notify_max_retries`/`notify_retry_backoff_sec` 정책을 적용합니다.
필요하면 `notify_timing_total_warn_ms`를 지정해 summary `timing_ms.total`이 임계치를 넘을 때 알림 상태를 `WARN`으로 승격할 수 있습니다(`HOLD`는 유지).
workflow 기본값은 PR Quick `120000ms`, Nightly `300000ms`이며, 로컬 `run_ci_summary.py` 기본값은 `0`(비활성)입니다.
`notify_timing_regression_baseline_ms`와 `notify_timing_regression_warn_ratio`를 함께 지정하면 `(timing_ms.total-baseline)/baseline` 회귀율이 임계치 이상일 때도 `WARN`으로 승격할 수 있습니다(`HOLD`는 유지).
두 회귀 옵션의 기본값은 `0`으로 비활성화되어 있고, `notify_timing_total_warn_ms`와 동시에 사용하면 두 조건이 모두 `timing_warning`에 기록됩니다.
`notify_timing_regression_history_window`를 `0`보다 크게 지정하면(기본 `0`) 최근 `*_release_summary.json`의 `timing_ms.total` median을 baseline으로 자동 계산할 수 있습니다.
history baseline 자동 계산은 `release_prefix` 계열(예: `REL_PR`, `REL_CI`)이 같은 이전 run들만 사용하고, run sequence가 있으면 현재 run 이전 시퀀스만 샘플로 사용합니다.
필요하면 `notify_timing_regression_history_dir`로 history 스캔 디렉터리를 직접 지정할 수 있고, `notify_timing_regression_history_outlier_method=iqr`, `notify_timing_regression_history_trim_ratio`로 이상치/극단값 제거를 적용할 수 있습니다.
payload에는 `timing_regression_baseline_source`, `timing_regression_history_filter`, `timing_regression_history_samples_raw_ms`, `timing_regression_history_samples_ms`가 함께 기록됩니다.
timing 경고가 있으면 payload에 `timing_warning_severity`(`WARN`/`HIGH`)와 `timing_warning_reasons`(`total_threshold`, `regression_ratio`)가 함께 기록됩니다.
임계치 경고가 발생하면 notification payload는 `slowest_stages_ms`(상위 느린 단계)도 함께 포함합니다.
HTTP `429`/`408`/`5xx` 응답에 `Retry-After` 헤더가 있으면 고정 backoff 대신 해당 값을 우선 사용합니다.
재시도가 수행되면 `send_release_notification.py`는 `[warn] retrying notification ... attempts=<n>/<max> delay_sec=<x>` 로그를 출력합니다.
`send_release_notification.py --dry-run` 로그는 webhook URL을 `url=***`로 마스킹해 출력합니다.

Nightly 기본 매트릭스 프로필:

<!-- BEGIN AUTO-GENERATED NIGHTLY MATRIX PROFILES -->
- `sds_v0_1_0` -> `30_Projects/P_Cloud-Engine/prototype/examples/batch_regression_highway_sds_v0.1.0.json`
- `sds_v0_2_0` -> `30_Projects/P_Cloud-Engine/prototype/examples/batch_regression_highway_sds_v0.2.0.json`
<!-- END AUTO-GENERATED NIGHTLY MATRIX PROFILES -->
- profiles file: `30_Projects/P_E2E_Stack/prototype/ci_profiles/nightly_matrix_profiles.json`
- 특정 프로필만 실행하려면 `matrix_profile_ids`에 `sds_v0_1_0,sds_v0_2_0` 형식으로 지정

`workflow_dispatch`에서 `batch_spec`을 지정하면 두 매트릭스 job 모두 해당 스펙을 사용합니다.
두 매트릭스 job은 동일한 `release_id` 베이스를 공유하며, 저장 레코드는 `<release_id>_<sds_version>` 형식으로 생성됩니다.
`release_id`를 입력하지 않으면 Nightly는 `REL_CI_<run_id>_<run_attempt>` 베이스를 자동 사용합니다.
Nightly는 매트릭스 완료 후 `e2e-full-matrix-summary` 아티팩트(`matrix_release_diff.txt`, `matrix_release_summary.json`, `matrix_notification_payload.json`, `matrix_release_summary.sqlite`)를 추가 업로드합니다.
요약 diff는 `sds_versions`의 앞 2개 버전(미지정 시 `sds_v0.1.0`, `sds_v0.2.0`) 기준으로 생성됩니다.

요약 아티팩트 생성은 `build_release_summary_artifact.py`를 공통으로 사용합니다.
알림 payload 생성은 `build_release_notification_payload.py`를 사용합니다.
웹훅 전송은 `send_release_notification.py`를 사용합니다.
Nightly 매트릭스 로딩은 `load_ci_matrix.py`를 사용합니다.
PR Quick precheck include 패턴 로딩은 `load_ci_precheck_patterns.py`를 사용합니다.
Job Summary 섹션 출력은 `write_ci_summary_section.py`를 사용합니다(`GITHUB_STEP_SUMMARY` 기본 사용).
Job Summary shell entry 공통 writer는 `run_ci_summary_section_entry.sh`를 사용합니다.
PR Quick trigger/precheck 스코프 동기화는 `sync_pr_quick_scope.py`를 사용합니다.
Nightly matrix 스코프 동기화는 `sync_nightly_matrix_scope.py`를 사용합니다.
CI 입력 정규화/파이프라인 실행 래퍼는 `run_ci_pipeline.py`를 사용합니다(quick profile 해석/기본 batch fallback 포함).
GitHub workflow run step은 `run_ci_pipeline_entry.sh`를 통해 `run_ci_pipeline.py` 호출 인자를 공통 조립합니다.
GitHub workflow summary step은 `run_ci_summary_entry.sh`를 통해 `run_ci_summary.py` 호출 인자를 공통 조립합니다.
`run_ci_summary_entry.sh`는 `CI_SUMMARY_PROFILE=pr_quick|nightly`를 지원하며 프로필별 기본 출력 파일명/타이틀/workflow 이름/타이밍 경고 기본값을 자동 적용합니다.
GitHub workflow preflight step은 `run_ci_preflight_entry.sh`를 통해 `phase1-regression`/`phase4-regression`/`validate`를 공통 실행하고 stage outcome을 출력합니다.
GitHub workflow preflight summary step은 `run_ci_preflight_summary_entry.sh`를 통해 Job Summary 섹션을 공통 출력합니다.
필요하면 summary step env(`SUMMARY_FILES_ROOT`, `PIPELINE_MANIFESTS_ROOT`, `SUMMARY_FILES_SUBPATH`, `PIPELINE_MANIFESTS_SUBPATH`)로 summary/manifest 스캔 루트와 하위 경로 힌트를 별도 지정할 수 있습니다.
PR Quick precheck 단계는 `run_ci_precheck_entry.sh`, Nightly matrix 준비 단계는 `run_ci_matrix_entry.sh`로 공통 처리합니다.
Nightly matrix selection summary 단계는 `run_ci_matrix_selection_summary_entry.sh`로 공통 처리합니다.
PR Quick skip summary 단계는 `run_ci_precheck_skip_summary_entry.sh`로 공통 처리합니다.
PR Quick 스코프 소스 파일은 `ci_profiles/pr_quick_scope.json`이며, 여기서 workflow `pull_request.paths`와 `ci_profiles/pr_quick_precheck_rules.json`을 함께 생성합니다.
`make sync-pr-quick-scope`로 재생성하고, `make check-pr-quick-scope`로 동기화 상태를 검증합니다.
Nightly 스코프 소스 파일은 `ci_profiles/nightly_matrix_scope.json`이며, 여기서 `ci_profiles/nightly_matrix_profiles.json`, README의 기본 매트릭스 프로필 섹션, Nightly workflow의 `matrix_profile_file` 기본값을 함께 생성합니다.
`make sync-nightly-matrix-scope`로 재생성하고, `make check-nightly-matrix-scope`로 동기화 상태를 검증합니다.
스코프 파일 스키마 검증은 `ci_scope_schema.py`로 수행되며, 규칙 위반 시 `profiles[0].profile_id` 같은 필드 경로를 포함한 오류와 함께 sync/check 단계에서 즉시 실패합니다.
두 스코프를 한 번에 처리하려면 `make sync-ci-scopes`/`make check-ci-scopes`를 사용합니다.
실행 경로(project boundary) 검증은 `check_execution_path_whitelist.py` + `ci_profiles/execution_path_scope.json`으로 수행되며, 허용되지 않은 `30_Projects/<project>/prototype` 참조가 생기면 `make check-project-boundary`에서 즉시 실패합니다.
경계 규칙/책임 매트릭스는 `PROJECT_BOUNDARY_CONTRACT.md`를 기준으로 관리합니다.
PR Quick precheck 기본 규칙 파일(`ci_profiles/pr_quick_precheck_rules.json`)은 `PRECHECK_RULES_FILE` env로 오버라이드할 수 있습니다.
PR Quick precheck는 `should_run`/`reason` 외에 `matched_file`/`matched_pattern` 출력도 함께 남겨 디버깅에 사용합니다.
Nightly matrix 준비 단계는 `matrix` 외에 `profile_ids`/`profile_count` 출력도 남겨 선택된 매트릭스 범위를 확인할 수 있습니다.
PR Quick/Nightly 모두 위 메타데이터를 Job Summary에도 게시합니다.
PR Quick이 skip될 때도 reason/matched metadata를 Job Summary에 게시합니다.
PR Quick/Nightly의 validate job은 preflight 단계(`phase1-regression`/`phase4-regression`/`validate`) outcome과 `first_failed_stage`/`first_failed_command`를 Job Summary에 남겨 실패 원인 추적 시간을 줄입니다.
두 entry 스크립트는 raw command echo를 하지 않고, 실제 명령 로깅/마스킹은 Python 래퍼(`run_ci_pipeline.py`, `run_ci_summary.py`)가 담당합니다.
두 entry 스크립트는 호출 위치와 무관하게 스크립트 자신의 경로 기준으로 Python 래퍼를 찾습니다.
shell entry 스크립트 오류 요약(`E2E CI Error`) 출력은 `ci_shell_reporting.sh`를 공통 사용합니다.
CI 요약/알림/Job Summary 게시 래퍼는 `run_ci_summary.py`를 사용합니다.
release ID/prefix 기본값(`REL_PR_*`, `REL_CI_*`) 계산은 `ci_release.py`로 공통 처리합니다.
명령 출력/마스킹 렌더링은 `ci_commands.py`에서 공통 처리합니다.
오류 상세 단계(`phase`) 값 상수는 `ci_phases.py`(python)와 `ci_phases_shell.sh`(shell)에서 동기화 관리합니다.
오류 상세 메타데이터 키/순서(`phase`, `command`, `exit_code`, `log_path`, `manifest_path`)는 `ci_reporting.py`에서 고정 관리합니다.
알림 정책 기본값(`notify_on=hold_warn`, `notify_format=slack`, timing/phase4 primary total+module/secondary 옵션 기본값 + `notify_phase3_vehicle_*` warn·hold 기본값 `0`)도 `run_ci_summary.py`에서 공통 처리합니다.
입력 검증 실패나 실행 실패 시 `run_ci_pipeline.py`는 `--step-summary-file`로 오류 요약을 남깁니다.
`run_ci_pipeline.py`/`run_ci_summary.py`의 오류 요약은 `ci_reporting.py`를 통해 동일한 `E2E CI Error` 포맷으로 게시됩니다.
`run_ci_pipeline.py`도 실패 시 `phase`/`command` 메타데이터를 포함하고, 실행 단계 실패인 경우 `exit_code`/`log_path`/`manifest_path`를 함께 남깁니다.
`run_ci_summary.py`는 실패 시 `phase`/`command` 메타데이터를 함께 남기고, `--webhook-url` 값은 로그/오류 메시지에서 마스킹(`***`) 처리합니다.
`run_ci_summary.py --dry-run`은 요약 생성/알림 payload 생성/알림 전송 명령(마스킹 적용)을 모두 출력합니다.
`send_release_notification.py`는 실패 시 `attempts=<현재>/<최대>` 정보를 함께 출력해 재시도 소진 여부를 바로 확인할 수 있습니다.
요약 텍스트/JSON에는 `pipeline manifest(overall/trend)`, `release-latest`, `release-diff`, `hold-reason code/raw 집계`가 포함됩니다.
요약 JSON(`timing_ms`)과 텍스트(`timing_ms=...`)에는 스캔/조회 단계 소요시간(ms)이 포함되어 CI 성능 추이를 비교할 수 있습니다.
Job Summary용 압축 Markdown 렌더링은 `render_release_summary_markdown.py`를 사용합니다.

로컬에서 동일 요약을 재생성하려면:

```bash
make build-release-summary RELEASE_ID=REL_TREND_STRICT_001 VERSION_A=sds_v0.1.0 VERSION_B=sds_v0.2.0
make build-notification-payload RELEASE_ID=REL_TREND_STRICT_001 SUMMARY_JSON_OUT=/private/tmp/make_release_summary3.json
make send-notification-dry-run RELEASE_ID=REL_TREND_STRICT_001 NOTIFY_PAYLOAD_OUT=/private/tmp/release_notification_test.json NOTIFY_ON=hold_warn NOTIFY_FORMAT=slack WEBHOOK_URL=https://example.invalid/webhook NOTIFY_TIMEOUT_SEC=5 NOTIFY_MAX_RETRIES=3 NOTIFY_RETRY_BACKOFF_SEC=1
```
