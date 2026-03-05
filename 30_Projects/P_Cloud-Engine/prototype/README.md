# P_Cloud-Engine Prototype (v0)

This prototype executes multiple scenario runs on a single machine and collects batch-level results.

## What it does

- reads `batch_spec` JSON/YAML
- executes runs in parallel (`max_concurrency`)
- calls the `P_Sim-Engine` runner subprocess
- collects per-run status, logs, and summary refs
- writes `batch_result.json`

## Quick Start

```bash
python3 cloud_batch_runner.py \
  --batch-spec examples/batch_smoke_v0.json
```

## Baseline regression suite (20 scenarios)

```bash
python3 cloud_batch_runner.py \
  --batch-spec examples/batch_regression_highway_v0.json
```

## Generate per-SDS regression specs

Build SDS-version-isolated batch specs from the scenario catalog:

```bash
python3 generate_batch_from_catalog.py \
  --catalog-manifest ../../P_Sim-Engine/prototype/scenario_catalog/highway_regression_v0/catalog_manifest.json \
  --batch-id BATCH_REG_HWY_SDS_V0_1_0_0001 \
  --sds-version sds_v0.1.0 \
  --sumo-actor-profile-id sumo_highway_balanced_v0 \
  --run-id-prefix RUN_RGA \
  --seed-base 1101 \
  --out examples/batch_regression_highway_sds_v0.1.0.json

python3 generate_batch_from_catalog.py \
  --catalog-manifest ../../P_Sim-Engine/prototype/scenario_catalog/highway_regression_v0/catalog_manifest.json \
  --batch-id BATCH_REG_HWY_SDS_V0_2_0_0001 \
  --sds-version sds_v0.2.0 \
  --sumo-actor-profile-id sumo_highway_aggressive_v0 \
  --run-id-prefix RUN_RGB \
  --seed-base 2101 \
  --out examples/batch_regression_highway_sds_v0.2.0.json
```

Built-in SUMO actor profiles:
- `sumo_highway_calm_v0`
- `sumo_highway_balanced_v0`
- `sumo_highway_aggressive_v0`

Optional traffic overrides for generated specs:
- `--sumo-actor-pattern-id`
- `--sumo-npc-count`
- `--sumo-npc-initial-gap-m`
- `--sumo-npc-gap-step-m`
- `--sumo-npc-speed-offset-mps`
- `--sumo-npc-lane-profile`
- `--sumo-npc-speed-scale`
- `--sumo-npc-speed-jitter-mps`

These generated specs are used as default profiles in nightly CI matrix execution.

Validate against expected catalog outcomes:

```bash
python3 check_batch_against_catalog.py \
  --batch-result batch_runs/BATCH_REG_HWY_V0_0001/batch_result.json \
  --catalog-manifest ../../P_Sim-Engine/prototype/scenario_catalog/highway_regression_v0/catalog_manifest.json
```

## Dry run

```bash
python3 cloud_batch_runner.py \
  --batch-spec examples/batch_smoke_v0.json \
  --dry-run
```

## Output

- `batch_runs/<batch_id>/batch_result.json`
- `batch_runs/<batch_id>/<run_id>/runner_stdout.log`
- `batch_runs/<batch_id>/<run_id>/runner_stderr.log`
- `batch_runs/<batch_id>/<run_id>/summary.json` (from sim runner)
- `batch_runs/<batch_id>/<run_id>/trace.csv` (from sim runner)

`batch_result.json` includes `lane_risk_batch_summary` when run summaries provide
`lane_risk_summary` telemetry.

This v0 implementation is intentionally minimal and designed as a local execution backend for follow-up integration with CI and Data Explorer ingestion.
