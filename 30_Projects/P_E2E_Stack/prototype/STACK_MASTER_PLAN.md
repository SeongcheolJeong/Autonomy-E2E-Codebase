# E2E Stack Master Plan (Functional Coverage Priority)

Last replanned: 2026-03-03

This master plan is rebuilt to prioritize **feature implementation coverage** first.

## Source of Truth

1. Functional master plan (detailed): `APPLIED_FUNCTIONAL_PARITY_MASTER_PLAN.md`
2. Module matrix/status: `STACK_MODULE_PARITY_MATRIX.md`
3. Phase checklists: `PHASE1_MODULE_PARITY_CHECKLIST.md` ... `PHASE4_MODULE_PARITY_CHECKLIST.md`
4. Weak block mapping: `REFERENCE_MIGRATION_MAP.md`
5. Execution history: `STACK_PROGRESS_LOG.md`

## Ground Rules

1. Always record meaningful code changes with git commits.
2. Use references first (AppliedDocs + mapped repos), then implement.
3. Prioritize feature completion (`L2_FUNCTIONAL`) before detail polish (`L3_FIDELITY`).
4. Run one efficiency review after every 5 module-scoped commits.
5. If a plan block is stalled for 2 consecutive batches, replan immediately.
6. Stop repetitive low-value work and replace with shared utilities/contracts.

## Feature-First Enforcement (Effective: 2026-03-02)

1. Commit-mix rule:
   - For any rolling 3 merged commits, at least 2 must be `feature-path` commits tied to `F1/F2/F3/F4/F5`.
2. Governance/gate cap:
   - `summary/notification/workflow/gate-only` work is allowed only if it unblocks an active feature batch and must reference that batch in commit/progress log text.
3. Anti-drift rule:
   - If 2 consecutive commits are governance/gate only, the next commit must be a user-visible feature-path change.
4. Scope lock:
   - `P0` backlog (`F1/F2/F3`) cannot be preempted by polish/stability-only work unless there is a blocking failure.
5. Status transparency:
   - Module status must be reported with separate `Contract` and `Runtime` maturity values (see `STACK_MODULE_PARITY_MATRIX.md`).

## Priority Order (Strict)

1. P0: user-visible functional completeness (can run, can output, can gate).
2. P1: scale/diversity expansion (more scenarios, more actors, more datasets).
3. P2: fidelity/performance upgrades (physics/render realism and runtime quality).

No polish-only block may preempt P0/P1 functional backlog.

## Runtime-Native Sprint Mode (Effective: 2026-03-03)

1. Runtime-native promotion is now the highest execution priority.
2. Each feature batch must close at least one runtime-native blocker and update `STACK_MODULE_PARITY_MATRIX.md`.
3. No more than one governance/gate-only commit is allowed within any rolling four commits.
4. Fidelity-only improvements are deferred unless they unlock runtime-native execution evidence.

## Milestones

| ID | Milestone | Priority | Status | Exit Criteria |
|---|---|---|---|---|
| F0 | Functional-Coverage Replan | P0 | DONE | coverage-first master plan and per-module hurdles/solutions defined |
| F1 | Runtime Scenario Execution Path | P0 | IN_PROGRESS | runtime matrix lane validates scenario contract evidence (`carla`, then `awsim`) |
| F2 | Scenario Standards Interop | P0 | TODO | OpenSCENARIO/OpenDRIVE import-export contract in executable path |
| F3 | Routing + Control Quality Gates | P0 | IN_PROGRESS | map routing semantic checks + vehicle control quality metrics wired into gating |
| F4 | Traffic/Data Scale-Up | P1 | IN_PROGRESS | SUMO actor profile injection + dataset diversity/coverage counters |
| F5 | Sensor/HIL Fidelity Upgrade | P2 | TODO | sensor fidelity tiers + runtime/HIL quality evidence thresholds |

## Active Execution Sequence

1. Batch RN1: align matrix runtime status with checklist-backed L2 execution evidence.
2. Batch RN2: harden runtime scenario evidence lane (`object_sim`, `log_sim`, `map_toolset`) with executable smoke contracts.
3. Batch RN3: harden runtime lane coverage for `neural_sim`, `vehiclesim`, `synthetic_datasets`.
4. Batch RN4: close runtime runner portability path for `sensor_sim` and `hil_sim` (Linux canonical lane evidence).
5. Batch RN5: finalize operator-visible runtime workflows for `adp` and `copilot` with traceable run artifacts.

## Major Hurdles and How We Resolve Them

| Hurdle | Why it blocks feature completion | Resolution |
|---|---|---|
| Runtime portability (`Exec format error`, host mismatch) | runtime lane blocked before feature verification | Linux runtime lane as canonical; host-aware asset selection and preflight checks |
| Contract drift between scripts | same feature passes one stage and fails downstream | shared parser/contract helpers + end-to-end propagation tests |
| Stability-only iteration loop | visible feature coverage stagnates | enforce batch policy: each cycle must land one user-visible feature block |
| External reference coupling risk | maintenance/license burden | pattern extraction only; keep local adapter ownership; track source in inventory |
| Slow runtime feedback | feature iteration speed collapses | dual-lane model: fast contract lane + runtime lane, both mandatory |

## Success Metrics

1. Functional coverage metric:
   - `%L2 = executable feature elements / total feature elements`
2. Release usability metric:
   - every new feature element appears in summary + notification outputs
3. Reliability metric:
   - success + failure path tests for each newly added feature element
4. Execution-balance metric:
   - rolling 3-commit window satisfies `feature-path >= 2`
5. Runtime parity metric:
   - `%RuntimeNative(L2) = Runtime-Native modules / total modules` (tracked in matrix)
6. Runtime execution evidence metric:
   - `%RuntimeEvidenceNative = modules with Linux runtime-lane evidence / total modules`

## Update Protocol

1. Update `APPLIED_FUNCTIONAL_PARITY_MASTER_PLAN.md` when priorities or hurdles change.
2. Update this file only for milestone and execution-order changes.
3. Append each merged block to `STACK_PROGRESS_LOG.md` with validation evidence.
4. Treat a milestone as complete only when related tests are green and artifacts are visible.
