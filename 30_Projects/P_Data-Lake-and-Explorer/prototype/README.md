# P_Data-Lake-and-Explorer Prototype (v0)

This prototype ingests `summary.json` artifacts into SQLite and supports core query patterns.

## Files

- `ingest_scenario_runs.py`: ingest summary files into DB
- `query_scenario_runs.py`: run simple queries
- `build_dataset_manifest.py`: build dataset manifest from run/release summaries

## Ingest

```bash
python3 ingest_scenario_runs.py \
  --summary-root ../P_Cloud-Engine/prototype/batch_runs \
  --db data/scenario_lake_v0.sqlite
```

Ingest release-level assessment summaries:

```bash
python3 ingest_scenario_runs.py \
  --report-summary-root ../P_Validation-Tooling-MVP/prototype/reports \
  --db data/scenario_lake_v0.sqlite
```

Ingest only specific summary files (faster incremental update):

```bash
python3 ingest_scenario_runs.py \
  --report-summary-file ../P_Validation-Tooling-MVP/prototype/reports/REL_X_sds_v0.1.0.summary.json \
  --report-summary-file ../P_Validation-Tooling-MVP/prototype/reports/REL_X_sds_v0.2.0.summary.json \
  --db data/scenario_lake_v0.sqlite
```

Ingest dataset manifest artifacts (for synthetic dataset tracking):

```bash
python3 ingest_scenario_runs.py \
  --dataset-manifest-file data/dataset_manifest_demo.json \
  --db data/scenario_lake_v0.sqlite
```

## Query examples

```bash
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite failures --limit 20
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite near-miss --ttc-threshold 2.0 --limit 20
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite compare \
  --metric-id collision_flag --version-a sds_v0.1.0 --version-b sds_v0.2.0
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite release-latest --limit 20
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite release-holds --limit 20
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite release-hold-reasons --limit 20
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite release-hold-reasons --mode raw --limit 20
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite release-trend --window 10
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite release-compare \
  --version-a sds_v0.1.0 --version-b sds_v0.2.0 --window 10
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite release-diff \
  --release-prefix REL_TREND_FULL_001 --version-a sds_v0.1.0 --version-b sds_v0.2.0
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite dataset-latest --limit 20
python3 query_scenario_runs.py --db data/scenario_lake_v0.sqlite dataset-release-links \
  --release-id REL_TREND_FULL_001_sds_v0.1.0 --limit 20
```

## Dataset manifest

```bash
python3 build_dataset_manifest.py \
  --summary-root ../P_Cloud-Engine/prototype/batch_runs \
  --release-summary-root ../P_Validation-Tooling-MVP/prototype/reports \
  --dataset-id DATASET_DEMO_001 \
  --out data/dataset_manifest_demo.json
```

For deterministic release-scoped generation, pass explicit files:

```bash
python3 build_dataset_manifest.py \
  --summary-file ../P_Cloud-Engine/prototype/batch_runs/BATCH_X/run_001/summary.json \
  --release-summary-file ../P_Validation-Tooling-MVP/prototype/reports/REL_X_sds_v0.1.0.summary.json \
  --dataset-id DATASET_DEMO_001 \
  --out data/dataset_manifest_demo.json
```

Manifest schema (`dataset_manifest_v0`) now includes:

- `run_ids`: unique run IDs from `run_summaries`
- `release_ids`: unique release IDs from `release_summaries`

When explicit files are passed (`--summary-file`, `--release-summary-file`), missing
`run_id`/`release_id` is treated as an error to prevent silent schema drift.

## Scope

- v0 schema: `scenario_run`, `metric_value`, `run_tag`, `release_assessment`, `dataset_manifest`
- focus query patterns: failure search, near-miss search, version comparison
