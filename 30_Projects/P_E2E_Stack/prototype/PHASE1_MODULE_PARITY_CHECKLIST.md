# Phase-1 Module Parity Checklist

This file tracks executable parity work for Phase-1 modules:

1. `cloud_engine`
2. `data_explorer`
3. `validation_toolset`
4. `object_sim`

Reference matrix: `STACK_MODULE_PARITY_MATRIX.md`.

## Checklist Rule

1. Start each feature as `TO_AUDIT` unless code evidence is confirmed.
2. Move to `PARTIAL` or `NATIVE` only with file + test evidence.
3. Every status change must include a commit in `STACK_PROGRESS_LOG.md`.

## cloud_engine

Applied reference: `references/applieddocs_v1.64/manual/v1.64/docs/cloud_engine/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| CI-triggered batch simulation flow | `30_Projects/P_E2E_Stack/prototype/run_ci_pipeline.py`, `.github/workflows/e2e-*.yml` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunCiPipelineTests` |
| Large scenario/profile matrix execution | `30_Projects/P_E2E_Stack/prototype/run_ci_matrix_pipeline.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunCiMatrixPipelineTests` |
| Reproducible per-run artifacts | `30_Projects/P_Cloud-Engine/prototype/cloud_batch_runner.py`, `batch_runs/*` | NATIVE | `python3 -m unittest tests.test_ci_scripts.CloudBatchRunnerTests` |
| Detailed failure/root-cause summary | `30_Projects/P_E2E_Stack/prototype/ci_reporting.py`, `30_Projects/P_E2E_Stack/prototype/build_release_summary_artifact.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunCiSummaryTests tests.test_ci_scripts.BuildReleaseSummaryArtifactTests` |

## data_explorer

Applied reference: `references/applieddocs_v1.64/manual/v1.64/docs/data_explorer/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Scalable ingest pipeline to queryable storage | `30_Projects/P_Data-Lake-and-Explorer/prototype/ingest_scenario_runs.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.DataExplorerIngestTests` |
| Query interface for events/metrics | `30_Projects/P_Data-Lake-and-Explorer/prototype/query_scenario_runs.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.DataExplorerIngestTests` |
| Searchable release/history summaries | `30_Projects/P_E2E_Stack/prototype/build_release_summary_artifact.py`, `30_Projects/P_Data-Lake-and-Explorer/prototype/query_scenario_runs.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.BuildReleaseSummaryArtifactTests tests.test_ci_scripts.DataExplorerIngestTests` |
| Dataset export/link hooks | `30_Projects/P_Validation-Tooling-MVP/prototype/reports/*.summary.json`, `30_Projects/P_Data-Lake-and-Explorer/prototype/build_dataset_manifest.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.DataExplorerIngestTests tests.test_ci_scripts.DatasetManifestTests` |

## validation_toolset

Applied reference: `references/applieddocs_v1.64/manual/v1.64/docs/validation_toolset/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Requirement-oriented release report generation | `30_Projects/P_Validation-Tooling-MVP/prototype/generate_release_report.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.GenerateReleaseReportScriptTests` |
| Coverage/performance summary in CI artifacts | `30_Projects/P_E2E_Stack/prototype/render_release_summary_markdown.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RenderReleaseSummaryMarkdownTests` |
| Failure case aggregation and trend gating | `30_Projects/P_E2E_Stack/prototype/run_ci_pipeline.py`, `run_ci_summary.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunCiPipelineTests tests.test_ci_scripts.RunCiSummaryTests` |
| Scenario language / auto-generation parity | `30_Projects/P_Validation-Tooling-MVP/prototype/generate_scenario_variants.py`, `30_Projects/P_Validation-Tooling-MVP/prototype/scenario_languages/*.json` | NATIVE | `python3 -m unittest tests.test_ci_scripts.ScenarioVariantGenerationTests` |

## object_sim

Applied reference: `references/applieddocs_v1.64/manual/v1.64/docs/object_sim/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Scenario execution for planning/prediction/control loops | `30_Projects/P_Sim-Engine/prototype/core_sim_runner.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests` |
| Parameter sweep capability | `30_Projects/P_Cloud-Engine/prototype/examples/*.json`, `30_Projects/P_E2E_Stack/prototype/generate_sds_batches.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.GenerateSdsBatchesTests` |
| Pass/fail observer-like gating | `30_Projects/P_Validation-Tooling-MVP/prototype/gate_profiles/*.json`, `30_Projects/P_E2E_Stack/prototype/run_ci_pipeline.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunCiPipelineTests` |
| Integration interface contract (stack-in-the-loop) | `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.RunE2EPipelineTests` |

## Current Sprint Target (M8-C)

1. Keep Phase-1 module checklist executable with all feature rows at `NATIVE`.
2. Keep `make phase1-regression` and `make -C 30_Projects/P_E2E_Stack/prototype validate` green.
3. Prevent regressions while Phase-4 contract hardening continues.
