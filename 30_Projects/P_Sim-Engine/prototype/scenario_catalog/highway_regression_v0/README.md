# Highway Regression Scenario Catalog v0

Seed catalog for baseline regression runs.

## Contents

- 20 scenario JSON files (`SC_HWY_REG_001` ~ `SC_HWY_REG_020`)
- `catalog_manifest.json` with expected outcome per scenario

## Notes

- All scenarios use `scenario_schema_version=\"scenario_definition_v0\"`.
- `SC_HWY_REG_010_timeout_guard` and `SC_HWY_REG_020_timeout_guard_b` are explicit timeout observer checks.
- This catalog is consumed by Cloud batch spec:
  - `30_Projects/P_Cloud-Engine/prototype/examples/batch_regression_highway_v0.json`
