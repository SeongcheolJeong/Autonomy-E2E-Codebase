# Phase-4 Module Parity Checklist

This file tracks executable parity work for Phase-4 modules:

1. `hil_sim`
2. `adp`
3. `copilot`

Reference matrix: `STACK_MODULE_PARITY_MATRIX.md`.
External reference map: `PHASE4_EXTERNAL_REFERENCE_MAP.md`.

## Checklist Rule

1. Start each feature as `TO_AUDIT` unless code evidence is confirmed.
2. Move to `PARTIAL` or `NATIVE` only with file + test evidence.
3. Every status change must include a commit in `STACK_PROGRESS_LOG.md`.

## hil_sim

Applied reference: `references/applieddocs_v1.64/manual/v1.64/docs/hil_sim/index.clean.md`
External reference baseline: `autowarefoundation/autoware`, `tier4/AWSIM`
Secondary reference pool: `tier4/scenario_simulator_v2`, `eclipse-sumo/sumo`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| HIL interface contract scaffold | `30_Projects/P_Autoware-Workspace-CI-MVP/prototype/hil_sequence_runner_stub.py` + `examples/hil_interface_v0.json` | NATIVE | `python3 -m unittest tests.test_ci_scripts.HilSequenceRunnerStubTests` |
| Trigger-based test sequence scaffold | `30_Projects/P_Autoware-Workspace-CI-MVP/prototype/hil_sequence_runner_stub.py` + `examples/hil_test_sequence_v0.json` | NATIVE | `python3 -m unittest tests.test_ci_scripts.HilSequenceRunnerStubTests` |
| Remote scheduling hook via stack runner | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunCiPipelineTests.test_dry_run_forwards_phase4_hook_options tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_hil_schedule_manifest tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_hil_runtime_limit_exceeded` |

## adp

Applied reference: `references/applieddocs_v1.64/manual/v1.64/docs/adp/index.clean.md`
External reference baseline: `autowarefoundation/autoware`, `carla-simulator/carla`
Secondary reference pool: `ApolloAuto/apollo`, `CommonRoad/commonroad-io`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Unified workflow surface mapping (module linkage) | `30_Projects/P_E2E_Stack/prototype/phase4_module_linkage_check_stub.py` + `run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.Phase4ModuleLinkageCheckStubTests tests.test_ci_scripts.RunCiPipelineTests.test_dry_run_forwards_phase4_hook_options tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_hil_schedule_manifest tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_module_linkage_matrix_status_is_backlog tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_phase4_require_done_and_linkage_in_progress` |
| ADP use-case traceability scaffold | `30_Projects/P_E2E_Stack/prototype/adp_workflow_trace_stub.py` + `run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.AdpWorkflowTraceStubTests tests.test_ci_scripts.RunCiPipelineTests.test_dry_run_forwards_phase4_hook_options tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_hil_schedule_manifest tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_adp_notice_missing_for_hold` |
| User-responsibility notice propagation contract | `30_Projects/P_E2E_Stack/prototype/adp_workflow_trace_stub.py` + `run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.AdpWorkflowTraceStubTests tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_adp_notice_missing_for_hold` |

## copilot

Applied reference: `references/applieddocs_v1.64/manual/v1.64/docs/copilot/index.clean.md`
External reference baseline: `commaai/openpilot`, `carla-simulator/carla`
Secondary reference pool: `CommonRoad/commonroad-scenario-designer`, `ApolloAuto/apollo`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Prompt-to-scenario contract scaffold | `30_Projects/P_E2E_Stack/prototype/copilot_prompt_contract_stub.py` + `run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.CopilotPromptContractStubTests tests.test_ci_scripts.RunCiPipelineTests.test_dry_run_forwards_phase4_hook_options tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_hil_schedule_manifest tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_copilot_prompt_is_empty` |
| Prompt-to-query contract scaffold | `30_Projects/P_E2E_Stack/prototype/copilot_prompt_contract_stub.py` + `run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.CopilotPromptContractStubTests tests.test_ci_scripts.RunCiPipelineTests.test_dry_run_forwards_phase4_hook_options tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_copilot_query_hold_artifacts` |
| Prompt audit logging + trace hook | `30_Projects/P_E2E_Stack/prototype/copilot_prompt_contract_stub.py` + `run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.CopilotPromptContractStubTests tests.test_ci_scripts.RunCiPipelineTests.test_dry_run_forwards_phase4_hook_options tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_hil_schedule_manifest tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_copilot_query_hold_artifacts tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_copilot_prompt_is_empty` |
| Release-assist hook contract | `30_Projects/P_E2E_Stack/prototype/copilot_release_assist_hook_stub.py` + `run_e2e_pipeline.py` + `run_ci_pipeline.py` + `Makefile` | NATIVE | `python3 -m unittest tests.test_ci_scripts.CopilotReleaseAssistHookStubTests tests.test_ci_scripts.RunCiPipelineTests.test_dry_run_forwards_phase4_hook_options tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_hil_schedule_manifest tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_generate_copilot_query_hold_artifacts tests.test_ci_scripts.RunE2EPipelineTests.test_phase4_hooks_fail_when_copilot_prompt_is_empty` |

## Current Sprint Target (M15-A)

1. `hil_sim`, `adp`, `copilot` rows are now all-`NATIVE`.
2. Keep Phase-4 hook contracts stable through stack-runner integration tests.
3. Keep `make -C 30_Projects/P_E2E_Stack/prototype validate` green.

## Reference-Driven Priority

1. `P0`: `hil_sim` using `autowarefoundation/autoware` + `tier4/AWSIM`.
2. `P1`: `adp` using `autowarefoundation/autoware` + `carla-simulator/carla`.
3. `P1`: `copilot` using `commaai/openpilot` + `carla-simulator/carla`.
4. Secondary expansion candidates: `ApolloAuto/apollo`, `tier4/scenario_simulator_v2`, `eclipse-sumo/sumo`, `CommonRoad/commonroad-io`, `CommonRoad/commonroad-scenario-designer`.
