# P_Validation-Tooling-MVP Prototype (v0)

This prototype generates a release-level markdown report from the scenario run SQLite lake.

It also includes a minimal logical-to-concrete scenario variant generator for parameter sweeps.

## Generate scenario variants

```bash
python3 generate_scenario_variants.py \
  --logical-scenarios scenario_languages/highway_cut_in_v0.json \
  --sampling full \
  --out reports/highway_cut_in_v0.variants.json
```

Input shape:

- top-level `logical_scenarios` list
- each entry has `scenario_id` and `parameters`
- each `parameters.<name>` is a list of candidate values

Output includes deterministic `variants[]` with:

- `scenario_id`
- `logical_scenario_id`
- `parameters`

## Generate report

```bash
python3 generate_release_report.py \
  --db ../../P_Data-Lake-and-Explorer/prototype/data/scenario_lake_v0.sqlite \
  --release-id REL_2026Q1_RC1 \
  --sds-version sds_v0.1.0 \
  --gate-profile gate_profiles/h0_highway_sanity_v0.json \
  --requirement-map requirement_maps/h0_highway_trace_v0.json \
  --summary-out reports/REL_2026Q1_RC1.summary.json \
  --out reports/REL_2026Q1_RC1.md
```

## Output

- run/fail/collision summary
- minTTC percentile snapshot
- failed run list with traceable summary paths
- requirement-to-metric traceability section (PASS/HOLD per requirement)
- capability gate result (`PASS`/`HOLD`) with rule-check reasons
- final decision section (`FINAL_RESULT`)
- optional machine-readable summary JSON (`--summary-out`)
- summary JSON includes `hold_reasons` and normalized `hold_reason_codes` for downstream aggregation

## Gate Profiles

- `gate_profiles/h0_highway_sanity_v0.json`
- `gate_profiles/h1_highway_core_v0.json` (placeholder)

Gate rules can include `min_run_count`, `max_fail_count`, `max_timeout_count`, `max_collision_count`, `max_collision_rate`, `min_ttc_p5_sec`.

## Requirement Maps

- `requirement_maps/h0_highway_trace_v0.json`
