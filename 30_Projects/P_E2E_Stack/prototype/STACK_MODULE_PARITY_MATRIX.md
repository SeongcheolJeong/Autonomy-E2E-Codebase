# Applied Intuition Stack Module Parity Matrix (v1.64)

This file defines module-level parity targets between Applied Intuition docs and local implementation projects.
This is the execution matrix for M7.

## Reference Rule

1. Product behavior reference: `references/applieddocs_v1.64/manual/v1.64/docs/<module>/index.md`
2. Local scope/requirement reference: `30_Projects/P_*.md`
3. Local implementation reference: `30_Projects/<project>/prototype/*`
4. Completion gate: tests + `make -C 30_Projects/P_E2E_Stack/prototype validate`

## Module Matrix

Status vocabulary:

1. `CONTRACT_NATIVE`: schema/contract path implemented and regression-tested.
2. `RUNTIME_NATIVE`: executable runtime/operator path is proven in stack-run lanes with regression tests (`L2_FUNCTIONAL` complete).
3. `RUNTIME_PARTIAL`: executable path exists, but one or more checklist rows remain `PARTIAL` or `TO_AUDIT`.
4. `RUNTIME_MISSING`: no usable runtime path yet.

| Module | AppliedDocs Reference | Local Spec | Local Code Base | Parity Phase | Phase Gate | Contract Status | Runtime Status | First Deliverable (DoD) |
|---|---|---|---|---|---|---|---|---|
| `cloud_engine` | `references/applieddocs_v1.64/manual/v1.64/docs/cloud_engine/index.md` | `30_Projects/P_Cloud-Engine.md` | `30_Projects/P_Cloud-Engine/prototype` | Phase-1 | PHASE1_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Batch/profile execution flow parity checklist + CLI contract tests |
| `data_explorer` | `references/applieddocs_v1.64/manual/v1.64/docs/data_explorer/index.md` | `30_Projects/P_Data-Lake-and-Explorer.md` | `30_Projects/P_Data-Lake-and-Explorer/prototype` | Phase-1 | PHASE1_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Query/ingest parity map + release-summary query coverage |
| `validation_toolset` | `references/applieddocs_v1.64/manual/v1.64/docs/validation_toolset/index.md` | `30_Projects/P_Validation-Tooling-MVP.md` | `30_Projects/P_Validation-Tooling-MVP/prototype` | Phase-1 | PHASE1_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Requirement/metric/report parity checklist + regression tests |
| `object_sim` | `references/applieddocs_v1.64/manual/v1.64/docs/object_sim/index.md` | `30_Projects/P_Sim-Engine.md` | `30_Projects/P_Sim-Engine/prototype` | Phase-1 | PHASE1_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Scenario I/O contract parity + deterministic run summary validation |
| `log_sim` | `references/applieddocs_v1.64/manual/v1.64/docs/log_sim/index.md` | `30_Projects/P_LogSim-NeuralSim.md` | `30_Projects/P_Sim-Engine/prototype` (bridge) | Phase-2 | PHASE2_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Log replay mode contract draft + minimal runner scaffold |
| `neural_sim` | `references/applieddocs_v1.64/manual/v1.64/docs/neural_sim/index.md` | `30_Projects/P_LogSim-NeuralSim.md` | `30_Projects/P_Sim-Engine/prototype` (bridge) | Phase-3 | PHASE3_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Neural scene representation stub + replay handoff + sensor rendering hook in stack runner |
| `sensor_sim` | `references/applieddocs_v1.64/manual/v1.64/docs/sensor_sim/index.md` | `30_Projects/P_SensorSim-Core.md`, `30_Projects/P_SensorSim-Camera-MVP.md`, `30_Projects/P_SensorSim-Lidar-MVP.md` | `30_Projects/P_Sim-Engine/prototype` (temporary host) | Phase-2 | PHASE2_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Core plugin interface scaffold + camera/lidar stub adapters |
| `vehiclesim` | `references/applieddocs_v1.64/manual/v1.64/docs/vehiclesim/index.md` | `30_Projects/P_VehicleOS-Minimal-Runtime.md`, `30_Projects/P_SDS-Highway-L2Plus-MVP.md` | `30_Projects/P_Sim-Engine/prototype` (temporary host) | Phase-3 | PHASE3_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Ego vehicle dynamics/control contract + stack hook passthrough |
| `map_toolset` | `references/applieddocs_v1.64/manual/v1.64/docs/map_toolset/index.md` | `30_Projects/P_Map-Toolset-MVP.md` | `30_Projects/P_Map-Toolset-MVP/prototype` | Phase-2 | PHASE2_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Canonical map format + one round-trip converter smoke test |
| `synthetic_datasets` | `references/applieddocs_v1.64/manual/v1.64/docs/synthetic_datasets/index.md` | `30_Projects/P_SensorSim-Validation-MVP.md` | `30_Projects/P_Data-Lake-and-Explorer/prototype` (dataset metadata) | Phase-3 | PHASE3_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | Dataset manifest schema + generation metadata ingestion/query + release linkage hooks |
| `hil_sim` | `references/applieddocs_v1.64/manual/v1.64/docs/hil_sim/index.md` | `30_Projects/P_Autoware-Workspace-CI-MVP.md` | `30_Projects/P_Autoware-Workspace-CI-MVP/prototype` | Phase-4 | PHASE4_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | HIL interface assumptions + trigger-sequence scheduler hook scaffold |
| `adp` | `references/applieddocs_v1.64/manual/v1.64/docs/adp/index.md` | `30_Projects/P_E2E_Stack/prototype/PHASE4_MODULE_PARITY_CHECKLIST.md` | `30_Projects/P_E2E_Stack/prototype` | Phase-4 | PHASE4_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | workflow traceability + user responsibility + module-linkage contracts in stack runner |
| `copilot` | `references/applieddocs_v1.64/manual/v1.64/docs/copilot/index.md` | `30_Projects/P_E2E_Stack/prototype/PHASE4_MODULE_PARITY_CHECKLIST.md` | `30_Projects/P_E2E_Stack/prototype` | Phase-4 | PHASE4_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | prompt scenario/query + audit trace + release-assist contracts in stack runner |

Current parity snapshot:

1. Contract-native: `13/13`.
2. Runtime-native: `13/13`.
3. Runtime-partial: `0/13`.
4. Fidelity parity is tracked separately as `L3_FIDELITY` work in `APPLIED_FUNCTIONAL_PARITY_MASTER_PLAN.md`.

## Execution Sequence

1. Phase-1 (now): `cloud_engine`, `data_explorer`, `validation_toolset`, `object_sim`
2. Phase-2: `sensor_sim`, `log_sim`, `map_toolset`
3. Phase-3: `neural_sim`, `vehiclesim`, `synthetic_datasets`
4. Phase-4: `hil_sim`, `adp`, `copilot`

## Module Implementation Template

1. Extract feature list from AppliedDocs module index.
2. Map each feature to local spec requirement and implementation file.
3. Mark one of: `native`, `partial`, `missing`.
4. Implement highest-priority `missing` features first.
5. Add/extend tests.
6. Run `make -C 30_Projects/P_E2E_Stack/prototype validate`.
7. Commit with module-scoped message.

## Efficiency Review Cadence

Run one efficiency review after every 5 module-scoped commits:

1. identify repeated manual steps,
2. convert to shared utility/script,
3. update this matrix and `STACK_PROGRESS_LOG.md`.
