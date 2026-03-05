# Applied Intuition Parity Acceleration Execution Plan

This file is the execution guide for rapidly increasing practical feature parity with Applied Intuition while
keeping contracts/testability stable.

## 1. Goal

Build an operator-usable stack that can do real scenario authoring, simulation execution, evidence generation,
and release gating with behavior-level parity against AppliedDocs v1.64 references.

Scope priority is:

1. User-visible functionality parity
2. Contract reliability and reproducibility
3. Runtime/performance fidelity expansion

## 2. Canonical Tracking Files

1. `STACK_MASTER_PLAN.md`: milestone status and execution focus
2. `STACK_MODULE_PARITY_MATRIX.md`: module-level parity status
3. `PHASE*_MODULE_PARITY_CHECKLIST.md`: per-module feature rows (`NATIVE`/`PARTIAL`/`MISSING`)
4. `REFERENCE_MIGRATION_MAP.md`: weak-block -> reference-repo mapping
5. `STACK_PROGRESS_LOG.md`: commit-level evidence + validation logs

## 3. Reference Usage Strategy

## 3.1 Priority order

1. Product behavior contract:
   `references/applieddocs_v1.64/manual/v1.64/docs/*`
2. Architecture/pattern references:
   `autoware`, `openpilot`, `AWSIM`, `CARLA`, `Apollo`, `scenario_runner`, `lanelet2`, `SUMO`, `esmini`, `scenariogeneration`
3. Local implementation targets:
   `30_Projects/P_*/prototype/*`

## 3.2 Usage rule

1. Do not copy large external code paths directly.
2. Extract patterns into local contracts/adapters first.
3. Add schema + integration + failure-path tests for each extracted pattern.
4. Record source and rationale in checklist/progress log.

## 4. Workstreams (Problem -> Solution -> Deliverable)

| Workstream | Current Problem | Reference Sources | Execution Plan | Exit Criteria |
|---|---|---|---|---|
| Runtime Rendering & Scenario Execution | Runtime path is strong on availability/probe contracts but still weak on full scenario-run result loops | `carla`, `scenario_runner`, `AWSIM` | (1) add scenario-run contract runner for CARLA/AWSIM lane; (2) publish runtime execution report schema (`runtime_scene_result_v0`); (3) integrate into matrix runtime lane | At least one runtime lane executes scenario and emits result artifacts consumed by summary/notification |
| Scenario Standards (OpenSCENARIO/OpenDRIVE) | Scenario IO compatibility is limited; conversion/interop depth is weak | `esmini`, `scenariogeneration`, `scenario_runner`, `libopendrive` | (1) add OpenSCENARIO import/export contract checker; (2) connect converted scenario to cloud batch profile generation | One end-to-end case: import -> execute -> evidence with schema validation |
| Vehicle Dynamics & Control Realism | Current vehicle dynamics path is deterministic but still lightweight vs production-level control behavior | `openpilot`, `autoware`, `apollo` | (1) add controller response quality metrics (tracking error envelope, jerk/lat accel bounds); (2) extend phase3 summary/notify thresholds | Phase3 output includes control quality metrics with warn/hold policy and tests |
| Map Topology & Routing Semantics | Map conversion/validation exists but lane-level routing semantics need depth | `lanelet2`, `libopendrive`, `autoware` | (1) add canonical map routing graph checks; (2) add route continuity/conflict rules; (3) surface violations in release summary | Routing/topology validation failures are visible as gated reasons in report + notification |
| Traffic Scale & Multi-Actor Profiles | Actor generation diversity/scale profile is limited | `SUMO`, `scenario_runner`, `apollo` | (1) add SUMO-derived actor profile generator; (2) feed profile into batch generation and simulation scene expansion | Batch profile supports multi-actor templates and coverage counters in summary |
| Sensor Physics Fidelity | Non-physics sensor pathways are available, but high-fidelity physics behavior remains weak | `CARLA`, `AWSIM`, `autoware` sensor assumptions | (1) define phased sensor-fidelity contract tiers; (2) add camera/lidar/radar evidence metrics per tier | Sensor tier contract appears in artifacts and can be used in gate policy |

## 5. Main Risks and Mitigations

| Risk | Why it blocks parity | Mitigation |
|---|---|---|
| Runtime binary portability (`Exec format error`, host mismatch) | Runtime-available lane can fail before feature validation | Keep Linux runner lane as canonical runtime path; maintain host-aware asset selection and preflight checks |
| Contract drift across scripts | Different stages can interpret same field differently | Keep shared parsers/helpers; add end-to-end propagation tests for every new field |
| Over-focusing on stability only | Feature expansion speed drops and parity gap remains | Enforce parallel cadence: every stabilization block must include one feature-visible expansion block |
| External repo over-dependence | Hard to maintain and license-risky | Extract interfaces/patterns only; preserve local implementation ownership |
| Cost/time explosion in full-fidelity sim | Slow feedback loop harms iteration speed | Two-lane strategy: contract lane (fast) + runtime lane (real execution), both mandatory in CI policy |

## 6. Execution Cadence (Speed + Control)

## 6.1 Batch unit

1. One feature batch = 3 to 5 commits:
   - contract/schema
   - integration wiring
   - summary/notification surfacing
   - regression tests
   - progress log
2. Efficiency review every 5 module-scoped commits (not every task).

## 6.2 Near-term waves

1. Wave A (now): Runtime scenario-run result contract (CARLA first, AWSIM second)
2. Wave B: OpenSCENARIO/OpenDRIVE interop pipeline
3. Wave C: Vehicle control quality metrics + gate policy
4. Wave D: Lane topology/routing validation + traffic profile expansion

## 7. Definition of Done (Parity-Oriented)

A feature block is considered complete only when all are true:

1. AppliedDocs feature intent is mapped and implemented in local contract
2. Summary/notification/operator surfaces include the feature evidence
3. Success and failure paths are both test-covered
4. Matrix lane or e2e lane has runnable evidence
5. `STACK_PROGRESS_LOG.md` records commit and validation evidence

## 8. Ground-Rule Enforcement

1. Always commit meaningful changes (`code` and `log` commits separated).
2. Reference-first design for every block (`AppliedDocs` + external mapping).
3. Re-plan immediately when milestone feasibility drops.
4. Stop repetitive low-value work by utility extraction and workflow automation.
