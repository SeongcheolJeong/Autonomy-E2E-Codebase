# Applied Functional Parity Master Plan (Coverage-First)

Last replanned: 2026-03-03

This plan prioritizes **functional implementation coverage** over micro-level polish.

## 1. Priority Policy

1. First priority: make each Applied module feature executable end-to-end.
2. Second priority: keep schema/contracts stable enough to prevent regressions.
3. Third priority: increase fidelity/performance after functional paths are complete.
4. Active mode (now): runtime-native acceleration (`L2_FUNCTIONAL` runtime completion first).

Non-goal for now: polishing-only improvements without functional coverage increase.

## 2. Coverage Model

Use these maturity levels per feature element:

1. `L1_CONTRACT`: schema/contract exists, basic unit tests pass.
2. `L2_FUNCTIONAL`: executable path exists in stack run, output artifacts are produced.
3. `L3_FIDELITY`: behavior/physics/performance realism upgraded with quality thresholds.

Completion policy:

1. Phase gate is based on `L2_FUNCTIONAL` coverage first.
2. `L3_FIDELITY` is scheduled only after `L2` path exists for the same feature.

## 3. Module-by-Module Execution Plan (Feature Elements + Hurdles + Solutions)

| Module | Applied Feature Elements to Implement | Current Focus Level | Main Hurdles | Resolution Plan | Exit Gate |
|---|---|---|---|---|---|
| `cloud_engine` | CI batch orchestration, large profile matrix, reproducible per-run artifacts, failure root-cause surface | L2 | profile explosion, artifact size growth | tiered profile presets (`quick/nightly/runtime`), artifact compaction, fail-fast matrix filtering | selected profile set runs and emits complete summary/notification evidence |
| `data_explorer` | scalable ingest, query APIs, release/history search, dataset linkage | L2 | schema drift, large history scans | schema-versioned ingest, history window constraints, query contracts per report version | ingest/query against latest + historical summaries without schema break |
| `validation_toolset` | requirement-based reports, CI coverage/performance views, trend gating, scenario variant generation | L2 | too many metrics with low operator actionability | tiered gate model (core/extended), warning reason normalization | reports produce direct action reasons and map to gate decisions |
| `object_sim` | scenario execution loop, parameter sweep, observer/gating contract, stack-in-the-loop interface | L2 | runtime fidelity mismatch between stub and runtime lane | bind object-sim outputs to runtime evidence lane and compare contract fields | one scenario executes in stack and runtime lane with aligned evidence |
| `sensor_sim` | camera/lidar/radar plugin path, rig config + deterministic frames, placement sweep hooks | L2 | physics realism gap | define sensor fidelity tiers (`contract/basic/high`) and expose tier in artifacts | sensor outputs include fidelity tier + per-tier validation counters |
| `log_sim` | replay flow, closed-loop re-sim summary, augmentation/fault injection | L2 | replay determinism and reproducibility | replay contract hash + deterministic seed control | identical replay inputs produce stable run manifests |
| `map_toolset` | canonical map conversion, round-trip checks, map query/validation semantics | L2 (toward L3) | routing/topology semantics currently shallow | add routing graph validation rules (continuity/conflict/reachability) | routing rule violations appear in release summary + notification reasons |
| `neural_sim` | neural scene representation, sensor rendering hook, replay-to-neural handoff | L2 | backend realism and coupling to runtime scene | add backend mode contract and runtime-backed render evidence path | neural path produces usable artifacts in e2e run and is queryable |
| `vehiclesim` | control+dynamics interface, vehicle profile support, controller-in-loop path | L2 (toward L3) | control quality metrics not fully surfaced | add control quality metrics (tracking error envelope, jerk/lat accel bounds) + thresholds | phase3 summaries include control quality gate metrics |
| `synthetic_datasets` | dataset manifest schema, generation metadata ingest, release linkage | L2 | scenario diversity and coverage observability | add diversity/coverage counters per dataset and link to release gates | dataset quality counters visible in summary and trend history |
| `hil_sim` | interface contract, trigger sequence execution, remote scheduling | L1/L2 | real runner dependency and remote environment variance | maintain contract lane + optional hardware lane; enforce interface schema at runner boundary | at least one remote/HIL execution evidence artifact is generated from stack |
| `adp` | unified scenario->execution->analysis workflow mapping, use-case traceability, responsibility notice | L1/L2 | cross-module trace continuity | enforce trace IDs across pipeline phases and artifacts | one trace chain links request -> run -> report -> decision |
| `copilot` | scenario/query prompt contracts, audit logging, release-assist hook | L1/L2 | safety guard consistency and hallucination risk | strict guard thresholds + audit schema + explicit hold reasons | copilot outputs always include guard result and traceable decision reason |

## 4. Functional-Coverage-First Wave Plan

## Wave A (P0): User-visible functional gaps first

1. Runtime scenario execution contract in matrix lane (`carla` first, `awsim` second).
2. OpenSCENARIO/OpenDRIVE import-export contract path.
3. Map routing/topology semantic validation.
4. Vehicle control quality metrics in phase3 and release gates.

Expected outcome: core user-visible "can run and can evaluate" path completed.

## Wave B (P1): Coverage scale-up

1. SUMO-based actor profile injection for large traffic diversity.
2. Synthetic dataset coverage/diversity metrics.
3. Replay determinism and reproducibility hardening.

Expected outcome: broader scenario space and stronger dataset usefulness.

## Wave C (P2): Fidelity/performance upgrades

1. Sensor fidelity tier expansion (camera/lidar/radar physics depth).
2. Runtime rendering quality/performance evidence thresholds.
3. HIL execution reliability and operator workflow tightening.

Expected outcome: quality and realism improvements after coverage baseline.

## 5. Hurdle Register (Cross-Cutting) and Mitigation

| Hurdle | Impact | Mitigation |
|---|---|---|
| Runtime portability (`Exec format error`, host mismatch) | runtime lane blocked before feature validation | keep Linux runtime lane canonical; host-aware asset selection + preflight checks |
| Contract drift between scripts | same feature interpreted differently by stage | shared parsers/contracts + end-to-end propagation tests required per field |
| Repeated low-value refinements | slows feature coverage growth | block polish-only tasks unless they unlock a Wave A/B feature |
| External reference over-coupling | maintenance and license risk | pattern extraction only, local adapter ownership, license trace in inventory |
| Slow feedback from full-fidelity runs | low iteration speed | dual lane model: fast contract lane + runtime lane, both required for merge |

## 6. Execution Discipline

1. One feature batch must include:
   - contract/schema update
   - stack wiring
   - summary/notification surface
   - success/failure tests
2. Commit policy:
   - code commit
   - progress-log commit
3. Efficiency review cadence:
   - once per 5 module-scoped commits
4. Replan trigger:
   - if Wave scope slips or runtime blocker persists for 2 consecutive batches.

## 6.1 Feature-First Guardrail (Enforced)

1. Rolling commit mix:
   - in every 3 merged commits, at least 2 must be feature-path commits that move `Wave A/B/C` scope.
2. Governance/gate-only work cap:
   - `summary/notification/workflow/gate-only` commits are allowed only when they unblock an active feature batch.
3. Mandatory linkage:
   - every governance/gate commit must cite its linked feature batch (`A1..A5` or `F1..F5`) in `STACK_PROGRESS_LOG.md`.
4. Stop condition:
   - if two consecutive commits do not increase feature coverage, next batch is forced to feature-path only.
5. Status reporting:
   - module parity must be reported as `Contract` and `Runtime` separately (no single `NATIVE` label for both).

## 7. Immediate Next Batches (Coverage Priority)

1. Batch RN1: align matrix runtime status with checklist-backed L2 execution evidence.
2. Batch RN2: harden runtime scenario evidence lane (`object_sim`, `log_sim`, `map_toolset`).
3. Batch RN3: harden runtime lane coverage for `neural_sim`, `vehiclesim`, `synthetic_datasets`.
4. Batch RN4: close runtime portability path for `sensor_sim` and `hil_sim` on Linux canonical lane.
5. Batch RN5: finalize runtime operator workflow evidence for `adp` and `copilot`.

These five batches are the current master execution sequence.
