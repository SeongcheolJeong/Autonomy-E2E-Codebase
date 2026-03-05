# P_Sim-Engine Prototype (v0)

This prototype provides a minimal deterministic Object-Sim runner with:

- fixed time-step core loop
- ego/NPC kinematic updates
- collision/minTTC/timeout observers
- run artifact writer (`summary.json`, `trace.csv`)

It also includes Phase-2 bridge scaffolds:

- `log_replay_runner.py`: `log_scene_v0` -> generated scenario -> `core_sim_runner.py`
- `sensor_sim_bridge.py`: world state + sensor rig -> camera/lidar/radar stub sensor frames
- `sensor_rig_sweep.py`: evaluate rig candidates with heuristic stub metrics
- `augment_log_scene.py`: produce adjacent/fault variants of `log_scene_v0`

Phase-3 kickoff scaffolds:

- `neural_scene_bridge.py`: `log_scene_v0` -> `neural_scene_v0` representation scaffold
- `render_neural_sensor_stub.py`: `neural_scene_v0` + `sensor_rig_v0` -> rendered sensor frame scaffold
- `vehicle_dynamics_stub.py`: ego dynamics/control interface contract simulation
- `sim_runtime_adapter_stub.py`: `log_scene_v0` + `sensor_rig_v0` -> runtime adapter report + launch manifest scaffold (`awsim|carla`)
- `sim_runtime_probe_runner.py`: runtime launch manifest -> runtime availability/probe report scaffold

## Quick Start

```bash
python3 core_sim_runner.py \
  --scenario examples/highway_following_v0.json \
  --run-id RUN_0001 \
  --seed 42 \
  --out runs
```

## Output

- `runs/<run_id>/summary.json`
- `runs/<run_id>/trace.csv`
- `runs/<run_id>/lane_risk_summary.json`

`summary.json` includes lane-aware TTC telemetry:

- `min_ttc_same_lane_sec`
- `min_ttc_adjacent_lane_sec`
- `min_ttc_any_lane_sec`

The output schema is aligned to the `P_Sim-Engine` MVP spec and is intended to be consumed by Cloud/Data/Validation follow-up work.

## Scenario schema v0

Input scenario must include:

- `scenario_schema_version`: must be `scenario_definition_v0`
- `scenario_id`, `duration_sec`, `dt_sec`, `ego`, `npcs`

Optional:

- `npc_speed_jitter_mps`
- `wall_timeout_sec` (wall-clock timeout for observer)

Runner-side traffic actor controls:

- `--traffic-actor-pattern-id` (`sumo_platoon_sparse_v0`, `sumo_platoon_balanced_v0`, `sumo_dense_aggressive_v0`)
- `--traffic-npc-count`
- `--traffic-npc-initial-gap-m`
- `--traffic-npc-gap-step-m`
- `--traffic-npc-speed-offset-mps`
- `--traffic-npc-lane-profile`
- `--traffic-npc-speed-scale`
- `--traffic-npc-speed-jitter-mps`

## Regression catalog

- `scenario_catalog/highway_regression_v0/`
  - 10 scenario seeds + expected outcome manifest
  - used by `P_Cloud-Engine/prototype/examples/batch_regression_highway_v0.json`

## Reproduce a failed run

Use `scenario_path` and `seed` from `summary.json`:

```bash
python3 core_sim_runner.py \
  --scenario "<scenario_path_from_summary>" \
  --run-id "<new_run_id>" \
  --seed <seed_from_summary> \
  --out runs
```

## Log replay scaffold

```bash
python3 log_replay_runner.py \
  --log-scene examples/log_scene_v0.json \
  --run-id LOG_REPLAY_0001 \
  --out runs
```

## Sensor Sim bridge scaffold

```bash
python3 sensor_sim_bridge.py \
  --world-state examples/world_state_v0.json \
  --sensor-rig examples/sensor_rig_v0.json \
  --out runs/sensor_frames_v0.json
```

Optional world-state environment inputs (for weather/light-aware sensor degradation):

- `environment.precipitation_intensity` (`0.0` to `1.0`)
- `environment.fog_density` (`0.0` to `1.0`)
- `environment.ambient_light_lux` (for low-light camera behavior)

Adverse weather example:

```bash
python3 sensor_sim_bridge.py \
  --world-state examples/world_state_adverse_weather_v0.json \
  --sensor-rig examples/sensor_rig_v0.json \
  --fidelity-tier high \
  --out runs/sensor_frames_adverse_weather_v0.json
```

Camera basic physics inputs (Applied docs aligned subset):

- `f_number`, `iso`, `shutter_speed_hz`
- `quantum_efficiency`, `full_well_capacity`, `readout_noise`
- `fixed_pattern_noise.dsnu`, `fixed_pattern_noise.prnu`
- `rolling_shutter.row_delay`, `rolling_shutter.col_delay`, `rolling_shutter.num_time_steps`, `rolling_shutter.num_exposure_samples_per_pixel`

Camera physics example:

```bash
python3 sensor_sim_bridge.py \
  --world-state examples/world_state_adverse_weather_v0.json \
  --sensor-rig examples/sensor_rig_camera_physics_v0.json \
  --fidelity-tier high \
  --out runs/sensor_frames_camera_physics_v0.json
```

Camera geometry/distortion inputs (Applied docs aligned subset):

- `lens_params.projection` (`RECTILINEAR|EQUIDISTANT|ORTHOGRAPHIC`)
- `lens_params.camera_intrinsic_params` (`fx`, `fy`, `cx`, `cy`)
- `lens_params.opencv_distortion_params` (`k1..k6`, `p1`, `p2`)
- `lens_params.radial_distortion_params.units` (`NORMALIZED|PIXELS|RADIANS`)
- `lens_params.radial_distortion_params.coefficients` (`a_0..a_14`)
- `lens_params.cropping`, `standard_params.rendered_field_of_view`

Camera geometry/distortion example:

```bash
python3 sensor_sim_bridge.py \
  --world-state examples/world_state_v0.json \
  --sensor-rig examples/sensor_rig_camera_geometry_distortion_v0.json \
  --fidelity-tier high \
  --out runs/sensor_frames_camera_geometry_distortion_v0.json
```

Camera postprocess/system-lens effects inputs (Applied docs aligned subset):

- `lens_params.chromatic_aberration`, `lens_params.lens_flare`
- `lens_params.vignetting.intensity|alpha|radius`
- `sensor_params.bloom`
- `system_params.gain`, `system_params.gamma`, `system_params.white_balance`
- `system_params.auto_black_level_offset.stddev_to_subtract` (`0..6`)
- `system_params.black_level_offset` (`r|g|b|a`, normalized)
- `system_params.saturation` (`r|g|b|a`, normalized)
- `fidelity.bloom.disable|level`, `fidelity.disable_tonemapper`

Camera postprocess example:

```bash
python3 sensor_sim_bridge.py \
  --world-state examples/world_state_adverse_weather_v0.json \
  --sensor-rig examples/sensor_rig_camera_postprocess_v0.json \
  --fidelity-tier high \
  --out runs/sensor_frames_camera_postprocess_v0.json
```

Camera tonemapping/black-level example:

```bash
python3 sensor_sim_bridge.py \
  --world-state examples/world_state_adverse_weather_v0.json \
  --sensor-rig examples/sensor_rig_camera_tonemapping_v0.json \
  --fidelity-tier high \
  --out runs/sensor_frames_camera_tonemapping_v0.json
```

Camera depth/optical-flow inputs (Applied docs aligned subset):

- `system_params.depth_params.min|max|log_base|type|bit_depth`
- `system_params.data_type` (`UINT|FLOAT`)
- `sensor_params.optical_flow_2d_settings.velocity_direction|y_axis_direction`

Depth + optical flow example:

```bash
python3 sensor_sim_bridge.py \
  --world-state examples/world_state_adverse_weather_v0.json \
  --sensor-rig examples/sensor_rig_camera_depth_optical_flow_v0.json \
  --fidelity-tier high \
  --out runs/sensor_frames_camera_depth_optical_flow_v0.json
```

## Sensor rig sweep scaffold

```bash
python3 sensor_rig_sweep.py \
  --world-state examples/world_state_v0.json \
  --rig-candidates examples/rig_sweep_v0.json \
  --out runs/rig_sweep_report_v0.json
```

## Log augmentation scaffold

```bash
python3 augment_log_scene.py \
  --input examples/log_scene_v0.json \
  --ego-speed-scale 1.1 \
  --lead-gap-offset-m -5 \
  --out runs/log_scene_aug_v0.json
```

## Neural scene scaffold

```bash
python3 neural_scene_bridge.py \
  --log-scene examples/log_scene_v0.json \
  --out runs/neural_scene_v0.json
```

## Vehicle dynamics scaffold

```bash
python3 vehicle_dynamics_stub.py \
  --vehicle-profile examples/vehicle_profile_v0.json \
  --control-sequence examples/control_sequence_v0.json \
  --out runs/vehicle_dynamics_trace_v0.json
```

## Neural sensor rendering scaffold

```bash
python3 render_neural_sensor_stub.py \
  --neural-scene runs/neural_scene_v0.json \
  --sensor-rig examples/sensor_rig_v0.json \
  --out runs/neural_sensor_frames_v0.json
```

## Runtime adapter scaffold (AWSIM/CARLA)

```bash
python3 sim_runtime_adapter_stub.py \
  --runtime carla \
  --scene examples/log_scene_v0.json \
  --sensor-rig examples/sensor_rig_v0.json \
  --mode headless \
  --frame-count 30 \
  --out runs/sim_runtime_adapter_report_v0.json
```

Output:

- adapter report (`sim_runtime_adapter_v0`)
- runtime launch manifest (`sim_runtime_launch_manifest_v0`, default: next to adapter report)

## Runtime probe scaffold (AWSIM/CARLA)

```bash
python3 sim_runtime_probe_runner.py \
  --runtime carla \
  --launch-manifest runs/sim_runtime_adapter_report_v0.carla.launch_manifest.json \
  --runtime-bin CarlaUE4.sh \
  --out runs/sim_runtime_probe_report_v0.json
```

Optional:

- `--execute-probe`: run `<runtime-bin> --help` probe command when binary is available
- `--require-availability`: fail if runtime binary is unavailable (manual/runtime-available lane)
- Host compatibility note: `--execute-probe` requires runtime binary architecture compatible with current host (for example Linux x86_64 bundle cannot execute on macOS arm64; probe return code becomes `126`).

## Runtime asset preparation (AWSIM/CARLA)

Use `prepare_runtime_assets.py` to stage runtime binaries + map/scenario resource bundles from the manifest:

```bash
python3 prepare_runtime_assets.py \
  --manifest examples/runtime_assets_manifest_v0.json \
  --runtime carla \
  --profile lightweight \
  --archive-sha256-mode verify_only \
  --require-runtime-bin \
  --require-host-compatible \
  --resolved-out runs/runtime_assets_resolved_v0.json \
  --env-out runs/runtime_assets_carla.env
```

Output:

- resolved runtime asset manifest (`sim_runtime_assets_resolved_v0`)
- optional shell env file (`SIM_RUNTIME`, `SIM_RUNTIME_SCENE`, `SIM_RUNTIME_SENSOR_RIG`, `SIM_RUNTIME_PROBE_RUNTIME_BIN`)

Notes:

- Default manifest (`examples/runtime_assets_manifest_v0.json`) is pinned to official CARLA/AWSIM release endpoints.
- Profile guideline: `lightweight` excludes CARLA additional map pack (~14.8GB) for faster bring-up, while `full` includes it.
- Manifest `target_platforms` and `--require-host-compatible` can block unsupported host/runtime combinations early (for example Linux x86_64 runtime on macOS arm64 host).
- Large runtime archives are downloaded to `runtime_assets/_archives/` and extracted under `runtime_assets/`.
- `--archive-sha256-mode` controls archive hash cost/strictness:
  - `always` (default): compute `archive_sha256` for all archive files.
  - `verify_only`: compute archive sha256 only when manifest `sha256` is declared.
  - `never`: skip routine archive sha256 computation (expected `sha256` verification still computes hash).
- Runtime-specific log-scene defaults are provided in:
  - `examples/log_scene_runtime_carla_v0.json`
  - `examples/log_scene_runtime_awsim_v0.json`
