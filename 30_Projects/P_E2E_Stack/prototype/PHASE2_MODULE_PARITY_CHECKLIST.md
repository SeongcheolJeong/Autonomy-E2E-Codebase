# Phase-2 Module Parity Checklist

This file tracks executable parity work for Phase-2 modules:

1. `sensor_sim`
2. `log_sim`
3. `map_toolset`

Reference matrix: `STACK_MODULE_PARITY_MATRIX.md`.

## Checklist Rule

1. Start each feature as `TO_AUDIT` unless code evidence is confirmed.
2. Move to `PARTIAL` or `NATIVE` only with file + test evidence.
3. Every status change must include a commit in `STACK_PROGRESS_LOG.md`.

## sensor_sim

Applied reference: `20_Knowledge/Sim/AppliedDocs_v1.64/manual/v1.64/docs/sensor_sim/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Core plugin interface scaffold (camera/lidar/radar) | `30_Projects/P_Sim-Engine/prototype/sensor_sim_bridge.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.SensorSimBridgeTests` |
| Sensor rig configuration + deterministic frame generation | `30_Projects/P_Sim-Engine/prototype/examples/sensor_rig_v0.json` | NATIVE | `python3 -m unittest tests.test_ci_scripts.SensorSimBridgeTests tests.test_ci_scripts.RunE2EPipelineTests` |
| Sensor placement/sweep analysis hooks | `30_Projects/P_Sim-Engine/prototype/sensor_rig_sweep.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.SensorRigSweepTests` |

## log_sim

Applied reference: `20_Knowledge/Sim/AppliedDocs_v1.64/manual/v1.64/docs/log_sim/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Log replay mode scaffold (`log_scene` -> simulation run) | `30_Projects/P_Sim-Engine/prototype/log_replay_runner.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.LogReplayRunnerTests` |
| Closed-loop re-simulation summary artifact | `30_Projects/P_Sim-Engine/prototype/log_replay_runner.py`, `runs/*/log_replay_manifest.json` | NATIVE | `python3 -m unittest tests.test_ci_scripts.LogReplayRunnerTests tests.test_ci_scripts.RunE2EPipelineTests` |
| Log augmentation / fault injection hooks | `30_Projects/P_Sim-Engine/prototype/augment_log_scene.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.LogAugmentationTests` |

## map_toolset

Applied reference: `20_Knowledge/Sim/AppliedDocs_v1.64/manual/v1.64/docs/map_toolset/index.clean.md`

| Feature | Local Evidence Path | Status | Verification Command |
|---|---|---|---|
| Canonical lane graph format converter | `30_Projects/P_Map-Toolset-MVP/prototype/convert_map_format.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.MapToolsetConverterTests` |
| Round-trip conversion smoke test | `30_Projects/P_Map-Toolset-MVP/prototype/examples/simple_highway_segment_v0.json` | NATIVE | `python3 -m unittest tests.test_ci_scripts.MapToolsetConverterTests tests.test_ci_scripts.RunE2EPipelineTests` |
| Map query/validation algorithmic checks | `30_Projects/P_Map-Toolset-MVP/prototype/validate_canonical_map.py` | NATIVE | `python3 -m unittest tests.test_ci_scripts.MapToolsetValidationTests tests.test_ci_scripts.RunE2EPipelineTests` |

## Current Sprint Target (M12-A)

1. Keep all Phase-2 rows `NATIVE` while maintaining integration/failure contracts.
2. Prepare Phase-3 kickoff scaffold (`neural_sim`, `vehiclesim`, `synthetic_datasets`).
3. Keep `make -C 30_Projects/P_E2E_Stack/prototype validate` green.
