# Phase-4 External Reference Map

This file defines how external repositories are used as implementation references for
Phase-4 modules (`hil_sim`, `adp`, `copilot`).

## Reference Repositories

### Primary (active in Phase-4 contract scan)

1. `https://github.com/autowarefoundation/autoware`
2. `https://github.com/tier4/AWSIM`
3. `https://github.com/carla-simulator/carla`
4. `https://github.com/commaai/openpilot`

### Secondary (adopt in next iteration without expanding current parser contract)

1. `https://github.com/ApolloAuto/apollo`
2. `https://github.com/tier4/scenario_simulator_v2`
3. `https://github.com/eclipse-sumo/sumo`
4. `https://github.com/CommonRoad/commonroad-io`
5. `https://github.com/CommonRoad/commonroad-scenario-designer`

## Usage Rule

1. Use external repositories for architecture and workflow patterns, not copy-paste.
2. Convert useful patterns into local adapters/contracts first.
3. Record every imported pattern in module checklist + test evidence.
4. Review license and asset constraints before introducing third-party code or content.
5. Keep Phase-4 parser-scanned primary references stable; stage secondary references via explicit spikes.

## Module-to-Reference Mapping

| Phase-4 Module | Priority | Primary References | Pattern to Extract | Local First Target |
|---|---|---|---|---|
| `hil_sim` | P0 | `autowarefoundation/autoware`, `tier4/AWSIM` | ROS2 interface contracts, simulation bridge flow, test harness launch patterns | promote HIL interface/sequence rows from `PARTIAL` to `NATIVE` |
| `adp` | P1 | `autowarefoundation/autoware`, `carla-simulator/carla` | unified workflow surface (scenario->execution->analysis), use-case traceability structure | keep traceability + responsibility + module-linkage rows at `NATIVE` |
| `copilot` | P1 | `commaai/openpilot`, `carla-simulator/carla` | prompt-driven safety guardrails, replay/query assist loop patterns | keep scenario/query + audit/trace + release-assist contracts stable at `NATIVE` |

## Secondary Reference Expansion (M16 Candidate Set)

- `hil_sim`: `tier4/scenario_simulator_v2`, `eclipse-sumo/sumo`
  - scenario test-runner orchestration and traffic actor synthesis patterns.
- `adp`: `ApolloAuto/apollo`, `CommonRoad/commonroad-io`
  - integrated workflow decomposition and scenario IO/validation interoperability patterns.
- `copilot`: `CommonRoad/commonroad-scenario-designer`, `ApolloAuto/apollo`
  - scenario authoring/conversion assistance and safety-oriented operator workflow patterns.

## Secondary Module-to-Reference Mapping

This section is machine-readable for `phase4_reference_pattern_scan_stub.py` secondary coverage fields.

| Phase-4 Module | Secondary References | Candidate Patterns to Extract |
|---|---|---|
| `hil_sim` | `tier4/scenario_simulator_v2`, `eclipse-sumo/sumo` | simulation bridge flow, test harness launch patterns, traffic actor behavior configuration |
| `adp` | `ApolloAuto/apollo`, `CommonRoad/commonroad-io` | moduleized planning control handoff contracts, scenario io validation and visualization workflow |
| `copilot` | `CommonRoad/commonroad-scenario-designer`, `ApolloAuto/apollo` | map conversion and scenario authoring workflow, scenario format conversion adapters |

## Repo-by-Repo Practical Use

### autowarefoundation/autoware

- Best fit: `hil_sim`, `adp`
- Why: mature ROS2 integration, launch/test conventions, CI-ready interface boundaries.
- Do not: import full stack components directly into this prototype.

### tier4/AWSIM

- Best fit: `hil_sim`
- Why: simulation bridge and Autoware-adjacent runtime integration patterns.
- Do not: depend on AWSIM runtime availability for unit-level contract tests.

### carla-simulator/carla

- Best fit: `adp`, `copilot`
- Why: scenario generation/parameterization and simulation execution loop patterns.
- Do not: tie Phase-4 scaffold exit criteria to CARLA runtime performance.

### commaai/openpilot

- Best fit: `copilot`
- Why: replay-centered validation loops, practical safety checks around model-assisted flows.
- Do not: mirror project-specific architecture decisions without local requirement mapping.

### ApolloAuto/apollo

- Best fit: `adp`, `copilot`
- Why: large-scale workflow segmentation and planning/control integration references.
- Do not: replicate full-runtime assumptions (hardware/deployment stack) into this prototype.

### tier4/scenario_simulator_v2

- Best fit: `hil_sim`, `adp`
- Why: scenario runner architecture and simulator-agnostic orchestration around Autoware.
- Do not: couple Phase-4 unit contracts to scenario_simulator_v2 runtime availability.

### eclipse-sumo/sumo

- Best fit: `hil_sim`, `adp`
- Why: traffic scenario generation and large-network actor behavior parameterization patterns.
- Do not: force SUMO-specific network artifacts into baseline Phase-4 contracts.

### CommonRoad/commonroad-io

- Best fit: `adp`, `copilot`
- Why: scenario read/write/validation and reproducible scenario exchange workflows.
- Do not: bind local schema evolution directly to CommonRoad release cadence.

### CommonRoad/commonroad-scenario-designer

- Best fit: `copilot`, `adp`
- Why: map/scenario conversion and scenario editing workflows for authoring assistants.
- Do not: import GUI/toolchain dependencies into CI contract-level tests.

## Immediate Execution Plan (M15-B)

1. `hil_sim`: completed all rows to `NATIVE` (runtime guard + failure-path contracts added).
2. `adp`: first executable `PARTIAL` row completed (`adp_workflow_trace_stub.py`).
3. `copilot`: first executable `PARTIAL` rows completed (`copilot_prompt_contract_stub.py` + audit log).
4. `adp`: responsibility notice propagation contract completed (enforced for `WARN`/`HOLD` in `adp_workflow_trace_stub.py`).
5. `copilot`: guard-policy threshold + pipeline-manifest trace linkage contracts completed.
6. `adp`: module-linkage mapping evidence completed (`phase4_module_linkage_check_stub.py` + contract tests).
7. `copilot`: release-assist hook contract completed (`copilot_release_assist_hook_stub.py` + contract tests).
8. `copilot`: release-assist hook row promoted to `NATIVE` via stack-runner integration (`run_e2e_pipeline.py`/`run_ci_pipeline.py`/`Makefile`).
9. `copilot`: prompt audit/trace hook row promoted to `NATIVE` via stack-runner integration evidence.
10. `copilot`: scenario/query contract rows promoted to `NATIVE` with integrated query-hold evidence.
11. `adp`: use-case traceability/responsibility rows promoted to `NATIVE` with stack-runner integration and HOLD failure-path evidence.
12. `adp`: module-linkage row promoted to `NATIVE` via stack-runner integration evidence (`run_e2e_pipeline.py`/`run_ci_pipeline.py`/`Makefile`) with success/failure tests.
13. Next: keep Phase-4 (`hil_sim`/`adp`/`copilot`) contracts stable while applying periodic efficiency reviews.

## Runtime Integration Bridge (M17)

1. Add runtime adapter scaffold (`sim_runtime_adapter_stub.py`) for `awsim`/`carla` and keep default `sim-runtime=none`.
2. Wire adapter options through stack runners (`run_ci_pipeline.py` -> `run_e2e_pipeline.py`) under `phase3_hooks.sim_runtime_adapter`.
3. Keep runtime integration optional/headless-first; do not require AWSIM/CARLA runtime in baseline unit tests.
4. Promote from scaffold to executable runtime runners in staged blocks:
   - Block A: adapter contract + manifest/report plumbing (done: `sim_runtime_adapter_v0` + `sim_runtime_launch_manifest_v0`)
   - Block B: headless smoke with external runtime runners (local/manual profile path added: `run_ci_matrix_pipeline.py` + `ci_profiles/runtime_matrix_profiles.json` + `make pipeline-runtime-smoke`; runtime availability probe hook: `sim_runtime_probe_runner.py`)
   - Block C: optional CI lane with runtime availability checks and artifact assertions (`make pipeline-runtime-available-smoke`, `--sim-runtime-assert-artifacts-input`) including adapter/probe/launch-manifest schema + runtime contract field validation and runtime evidence artifact output (`--runtime-evidence-out`)
   - Block D: summary/notification bridge (done): `build_release_summary_artifact.py` scans `*runtime*evidence*.json` and emits `runtime_evidence_summary`; markdown/notification builders render the same summary and surface failed runtime evidence rows as warning context.
