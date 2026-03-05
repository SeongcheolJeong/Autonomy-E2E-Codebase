# Phase-3 Module Parity Checklist

This file tracks executable parity work for Phase-3 modules:

1. `neural_sim`
2. `vehiclesim`
3. `synthetic_datasets`

Reference matrix: `STACK_MODULE_PARITY_MATRIX.md`.

## Checklist Rule

1. Start each feature as `TO_AUDIT` unless code evidence is confirmed.
2. Move to `PARTIAL` or `NATIVE` only with file + test evidence.
3. Every status change must include a commit in `STACK_PROGRESS_LOG.md`.

## neural_sim

Applied reference: `20_Knowledge/Sim/AppliedDocs_v1.64/manual/v1.64/docs/neural_sim/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Neural scene representation scaffold | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` + `30_Projects/P_Sim-Engine/prototype/neural_scene_bridge.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |
| Sensor rendering integration hook | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` + `30_Projects/P_Sim-Engine/prototype/render_neural_sensor_stub.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |
| Replay-to-neural handoff contract | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` + `30_Projects/P_Sim-Engine/prototype/neural_scene_bridge.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |

## vehiclesim

Applied reference: `20_Knowledge/Sim/AppliedDocs_v1.64/manual/v1.64/docs/vehiclesim/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Ego dynamics/control interface contract | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` + `30_Projects/P_Sim-Engine/prototype/vehicle_dynamics_stub.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |
| Mass/drag/grade-aware longitudinal dynamics + optional planar steering/yaw (kinematic + dynamic bicycle) + initial state contract | `30_Projects/P_Sim-Engine/prototype/vehicle_dynamics_stub.py` + `30_Projects/P_E2E_Stack/prototype/tests/test_vehicle_dynamics_enhanced.py` | NATIVE | `python3 -m unittest tests/test_vehicle_dynamics_enhanced.py` |
| Vehicle parameter profile support | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` (`--vehicle-profile`) | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |
| Controller-in-the-loop hook | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` (`--control-sequence`) | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |

## synthetic_datasets

Applied reference: `20_Knowledge/Sim/AppliedDocs_v1.64/manual/v1.64/docs/synthetic_datasets/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Dataset manifest schema scaffold | `30_Projects/P_Data-Lake-and-Explorer/prototype/build_dataset_manifest.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.DatasetManifestTests` |
| Generation metadata ingestion contract | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` + `30_Projects/P_Data-Lake-and-Explorer/prototype/ingest_scenario_runs.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |
| Dataset release linkage hook | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` + `30_Projects/P_Data-Lake-and-Explorer/prototype/query_scenario_runs.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests.test_phase3_hooks_generate_and_ingest_dataset_manifest` |

## Current Sprint Target (M13-B)

1. Phase-3 module rows are now all `NATIVE` (`neural_sim`, `vehiclesim`, `synthetic_datasets`).
2. Keep `make -C 30_Projects/P_E2E_Stack/prototype validate` green while preparing Phase-4 kickoff.
