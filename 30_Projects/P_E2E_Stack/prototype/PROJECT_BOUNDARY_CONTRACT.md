# Project Boundary Contract (Execution Path)

Last updated: 2026-03-02

This contract defines how `P_E2E_Stack` is allowed to reference sibling projects, so implementation work stays module-complete without scattered ownership drift.

## 1) Boundary Rules

1. Runtime execution paths may reference only whitelisted project roots in `ci_profiles/execution_path_scope.json`.
2. Accepted path shapes:
   - canonical: `30_Projects/<project>/prototype/...`
   - sibling-relative: `../P_<project>/prototype/...` or `../../P_<project>/prototype/...`
3. Any new project dependency must update both:
   - this contract
   - `ci_profiles/execution_path_scope.json`
4. Verification command:
   - `make check-project-boundary`
5. CI gate:
   - `make validate` always runs `check-project-boundary`.

## 2) Folder Responsibility Matrix

| Project Root | Primary Responsibility | Owned Runtime Artifacts | Allowed From E2E Stack |
|---|---|---|---|
| `P_E2E_Stack/prototype` | Orchestration and cross-module release gating | CI wrappers, summary/notification contracts, phase integration | Yes |
| `P_Cloud-Engine/prototype` | Batch expansion and cloud run orchestration | batch specs, cloud run manifests | Yes |
| `P_Sim-Engine/prototype` | Scenario/object/sensor/runtime simulation contracts | sim traces, runtime probe/scene/interop artifacts | Yes |
| `P_Data-Lake-and-Explorer/prototype` | Run/release dataset ingest and query | sqlite ingest/query and dataset manifests | Yes |
| `P_Validation-Tooling-MVP/prototype` | Requirement mapping and release report generation | release report outputs and policy profiles | Yes |
| `P_Map-Toolset-MVP/prototype` | Map conversion and semantic validation | canonical map conversion/validation reports | Yes |
| `P_Autoware-Workspace-CI-MVP/prototype` | HIL interface/sequence contract | HIL schedule and runtime checks | Yes |

## 3) Change Protocol

1. If a new feature needs a new module root, add it to the whitelist first.
2. Add/extend tests in `tests/test_ci_scripts.py` for new boundary behavior.
3. Merge only after `make validate` passes with boundary check included.
