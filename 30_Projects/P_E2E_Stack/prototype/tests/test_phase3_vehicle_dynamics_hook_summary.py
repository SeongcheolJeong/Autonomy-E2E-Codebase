import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROTOTYPE_DIR = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run_script(script_path: Path, *args: str, expected_rc: int = 0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [PYTHON, str(script_path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != expected_rc:
        raise AssertionError(
            f"Unexpected return code: got {proc.returncode}, expected {expected_rc}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


class Phase3VehicleDynamicsHookSummaryTests(unittest.TestCase):
    def test_phase3_hooks_include_vehicle_dynamics_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch_spec.json"
            write_json(
                batch_spec,
                {
                    "batch_id": "batch_demo",
                    "output_root": str(tmp_path / "batch_runs"),
                },
            )

            cloud_runner = tmp_path / "fake_cloud_runner.py"
            cloud_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "spec_path = Path(sys.argv[sys.argv.index('--batch-spec') + 1]).resolve()\n"
                "spec = json.loads(spec_path.read_text(encoding='utf-8'))\n"
                "output_root = Path(spec.get('output_root')).resolve()\n"
                "batch_root = output_root / str(spec.get('batch_id'))\n"
                "batch_root.mkdir(parents=True, exist_ok=True)\n"
                "result_path = batch_root / 'batch_result.json'\n"
                "result_path.write_text(json.dumps({'ok': True}) + '\\n', encoding='utf-8')\n"
                "run_dir = batch_root / 'run_001'\n"
                "run_dir.mkdir(parents=True, exist_ok=True)\n"
                "(run_dir / 'summary.json').write_text(json.dumps({\n"
                "  'run_id': 'run_001',\n"
                "  'scenario_id': 'scenario.phase3.summary',\n"
                "  'sds_version': 'sds_v1',\n"
                "  'status': 'success'\n"
                "}) + '\\n', encoding='utf-8')\n"
                "print(f'[ok] result={result_path}')\n",
                encoding="utf-8",
            )

            ingest_runner = tmp_path / "fake_ingest_runner.py"
            ingest_runner.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

            report_runner = tmp_path / "fake_report_runner.py"
            report_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "release_id = args[args.index('--release-id') + 1]\n"
                "sds_version = args[args.index('--sds-version') + 1]\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "summary_path = Path(args[args.index('--summary-out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "summary_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text('# fake report\\n', encoding='utf-8')\n"
                "summary_path.write_text(json.dumps({\n"
                "  'release_id': release_id,\n"
                "  'sds_version': sds_version,\n"
                "  'final_result': 'PASS'\n"
                "}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            neural_scene_runner = tmp_path / "fake_neural_scene_runner.py"
            neural_scene_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text(json.dumps({'neural_scene_schema_version': 'neural_scene_v0'}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            neural_render_runner = tmp_path / "fake_neural_render_runner.py"
            neural_render_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text(json.dumps({'render_frame_count': 1}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            dataset_manifest_runner = tmp_path / "fake_dataset_manifest_runner.py"
            dataset_manifest_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "dataset_id = args[args.index('--dataset-id') + 1]\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text(json.dumps({'dataset_id': dataset_id}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            log_scene = tmp_path / "phase3" / "log_scene.json"
            sensor_rig = tmp_path / "phase3" / "sensor_rig.json"
            vehicle_profile = tmp_path / "phase3" / "vehicle_profile.json"
            control_sequence = tmp_path / "phase3" / "control_sequence.json"
            vehicle_dynamics_out = tmp_path / "phase3" / "vehicle_trace.json"
            dataset_manifest_out = tmp_path / "phase3" / "dataset_manifest.json"
            neural_scene_out = tmp_path / "phase3" / "neural_scene.json"
            neural_render_out = tmp_path / "phase3" / "neural_render.json"

            write_json(
                log_scene,
                {
                    "log_scene_schema_version": "log_scene_v0",
                    "log_id": "LOG_PHASE3_SUMMARY_001",
                    "map_id": "map_phase3_v0",
                    "map_version": "v0",
                    "ego_initial_speed_mps": 10.0,
                    "lead_vehicle_initial_gap_m": 40.0,
                    "lead_vehicle_speed_mps": 9.0,
                    "duration_sec": 2.0,
                    "dt_sec": 0.1,
                },
            )
            write_json(sensor_rig, {"rig_schema_version": "sensor_rig_v0", "sensors": []})
            write_json(
                vehicle_profile,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.9,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 30.0,
                    "mass_kg": 1800.0,
                    "rolling_resistance_coeff": 0.02,
                    "drag_coefficient": 0.32,
                    "frontal_area_m2": 2.3,
                    "air_density_kgpm3": 1.225,
                },
            )
            write_json(
                control_sequence,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.2,
                    "default_road_grade_percent": 1.5,
                    "default_target_speed_mps": 6.2,
                    "initial_speed_mps": 6.0,
                    "initial_position_m": 1.5,
                    "commands": [
                        {"throttle": 0.3, "brake": 0.0, "road_grade_percent": 2.5, "target_speed_mps": 6.5},
                        {"throttle": 0.4, "brake": 0.0},
                        {"throttle": 0.0, "brake": 0.1, "road_grade_percent": -1.0, "target_speed_mps": 5.8},
                    ],
                },
            )

            run_script(
                PROTOTYPE_DIR / "run_e2e_pipeline.py",
                "--batch-spec",
                str(batch_spec),
                "--release-id",
                "REL_PHASE3_SUMMARY_001",
                "--sds-version",
                "sds_v1",
                "--db",
                str(tmp_path / "scenario_lake.sqlite"),
                "--report-dir",
                str(tmp_path / "reports"),
                "--cloud-runner",
                str(cloud_runner),
                "--ingest-runner",
                str(ingest_runner),
                "--report-runner",
                str(report_runner),
                "--phase3-enable-hooks",
                "--dataset-manifest-runner",
                str(dataset_manifest_runner),
                "--log-scene",
                str(log_scene),
                "--neural-scene-runner",
                str(neural_scene_runner),
                "--neural-scene-out",
                str(neural_scene_out),
                "--neural-render-runner",
                str(neural_render_runner),
                "--neural-render-sensor-rig",
                str(sensor_rig),
                "--neural-render-out",
                str(neural_render_out),
                "--vehicle-dynamics-runner",
                str(PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py"),
                "--vehicle-profile",
                str(vehicle_profile),
                "--control-sequence",
                str(control_sequence),
                "--vehicle-dynamics-out",
                str(vehicle_dynamics_out),
                "--dataset-id",
                "DATASET_PHASE3_SUMMARY_001",
                "--dataset-manifest-out",
                str(dataset_manifest_out),
            )

            manifest = json.loads(
                (tmp_path / "batch_runs" / "batch_demo" / "pipeline_result.json").read_text(encoding="utf-8")
            )
            phase3_hooks = manifest.get("phase3_hooks", {})
            self.assertTrue(phase3_hooks.get("enabled"))
            self.assertEqual(phase3_hooks.get("vehicle_dynamics_out"), str(vehicle_dynamics_out.resolve()))
            vehicle_summary = phase3_hooks.get("vehicle_dynamics", {})
            self.assertEqual(vehicle_summary.get("vehicle_dynamics_model"), "longitudinal_force_balance_v1")
            self.assertEqual(int(vehicle_summary.get("step_count", 0)), 3)
            self.assertEqual(float(vehicle_summary.get("initial_speed_mps", -1.0)), 6.0)
            self.assertEqual(float(vehicle_summary.get("initial_position_m", -1.0)), 1.5)
            self.assertGreater(float(vehicle_summary.get("final_speed_mps", 0.0)), 0.0)
            self.assertGreater(float(vehicle_summary.get("final_position_m", 0.0)), 1.5)
            self.assertFalse(bool(vehicle_summary.get("planar_kinematics_enabled", True)))
            self.assertFalse(bool(vehicle_summary.get("dynamic_bicycle_enabled", True)))
            self.assertAlmostEqual(float(vehicle_summary.get("initial_heading_deg", 1.0)), 0.0, places=6)
            self.assertAlmostEqual(float(vehicle_summary.get("final_heading_deg", 1.0)), 0.0, places=6)
            self.assertAlmostEqual(float(vehicle_summary.get("initial_lateral_position_m", 1.0)), 0.0, places=6)
            self.assertAlmostEqual(float(vehicle_summary.get("final_lateral_position_m", 1.0)), 0.0, places=6)
            self.assertAlmostEqual(float(vehicle_summary.get("initial_lateral_velocity_mps", 1.0)), 0.0, places=6)
            self.assertAlmostEqual(float(vehicle_summary.get("final_lateral_velocity_mps", 1.0)), 0.0, places=6)
            self.assertAlmostEqual(float(vehicle_summary.get("initial_yaw_rate_rps", 1.0)), 0.0, places=6)
            self.assertAlmostEqual(float(vehicle_summary.get("final_yaw_rate_rps", 1.0)), 0.0, places=6)
            self.assertLess(float(vehicle_summary.get("min_road_grade_percent", 0.0)), 0.0)
            self.assertGreater(float(vehicle_summary.get("max_road_grade_percent", 0.0)), 0.0)
            self.assertGreater(float(vehicle_summary.get("max_abs_grade_force_n", 0.0)), 0.0)

            vehicle_payload = json.loads(vehicle_dynamics_out.read_text(encoding="utf-8"))
            self.assertEqual(vehicle_summary.get("step_count"), vehicle_payload.get("step_count"))
            self.assertEqual(
                float(vehicle_summary.get("final_speed_mps", 0.0)),
                float(vehicle_payload.get("final_speed_mps", 0.0)),
            )
            self.assertEqual(
                float(vehicle_summary.get("final_position_m", 0.0)),
                float(vehicle_payload.get("final_position_m", 0.0)),
            )
            trace = vehicle_payload.get("trace", [])
            self.assertTrue(isinstance(trace, list) and trace)
            road_grade_values = [float(row.get("road_grade_percent", 0.0)) for row in trace]
            grade_force_values = [float(row.get("grade_force_n", 0.0)) for row in trace]
            self.assertEqual(
                float(vehicle_summary.get("min_road_grade_percent", 0.0)),
                min(road_grade_values),
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("avg_road_grade_percent", 0.0)),
                sum(road_grade_values) / float(len(road_grade_values)),
                places=6,
            )
            self.assertEqual(
                float(vehicle_summary.get("max_road_grade_percent", 0.0)),
                max(road_grade_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_grade_force_n", 0.0)),
                max(abs(value) for value in grade_force_values),
            )
            heading_values = [float(row.get("heading_deg", 0.0)) for row in trace]
            lateral_values = [float(row.get("y_m", 0.0)) for row in trace]
            yaw_rate_values = [float(row.get("yaw_rate_rps", 0.0)) for row in trace]
            lateral_velocity_values = [float(row.get("lateral_velocity_mps", 0.0)) for row in trace]
            self.assertEqual(
                float(vehicle_summary.get("min_heading_deg", 0.0)),
                min(heading_values),
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("avg_heading_deg", 0.0)),
                sum(heading_values) / float(len(heading_values)),
                places=6,
            )
            self.assertEqual(
                float(vehicle_summary.get("max_heading_deg", 0.0)),
                max(heading_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("min_lateral_position_m", 0.0)),
                min(lateral_values),
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("avg_lateral_position_m", 0.0)),
                sum(lateral_values) / float(len(lateral_values)),
                places=6,
            )
            self.assertEqual(
                float(vehicle_summary.get("max_lateral_position_m", 0.0)),
                max(lateral_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_lateral_position_m", 0.0)),
                max(abs(value) for value in lateral_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_yaw_rate_rps", 0.0)),
                max(abs(value) for value in yaw_rate_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_lateral_velocity_mps", 0.0)),
                max(abs(value) for value in lateral_velocity_values),
            )
            accel_values = [float(row.get("accel_mps2", 0.0)) for row in trace]
            lateral_accel_values = [float(row.get("lateral_accel_mps2", 0.0)) for row in trace]
            yaw_accel_values = [float(row.get("yaw_accel_rps2", 0.0)) for row in trace]
            self.assertEqual(
                float(vehicle_summary.get("max_abs_accel_mps2", 0.0)),
                max(abs(value) for value in accel_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_lateral_accel_mps2", 0.0)),
                max(abs(value) for value in lateral_accel_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_yaw_accel_rps2", 0.0)),
                max(abs(value) for value in yaw_accel_values),
            )
            dt_sec = 0.2
            jerk_values = [
                (accel_values[idx] - accel_values[idx - 1]) / dt_sec for idx in range(1, len(accel_values))
            ]
            lateral_jerk_values = [
                (lateral_accel_values[idx] - lateral_accel_values[idx - 1]) / dt_sec
                for idx in range(1, len(lateral_accel_values))
            ]
            yaw_jerk_values = [
                (yaw_accel_values[idx] - yaw_accel_values[idx - 1]) / dt_sec
                for idx in range(1, len(yaw_accel_values))
            ]
            self.assertEqual(
                float(vehicle_summary.get("max_abs_jerk_mps3", 0.0)),
                max(abs(value) for value in jerk_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_lateral_jerk_mps3", 0.0)),
                max(abs(value) for value in lateral_jerk_values),
            )
            self.assertEqual(
                float(vehicle_summary.get("max_abs_yaw_jerk_rps3", 0.0)),
                max(abs(value) for value in yaw_jerk_values),
            )
            self.assertEqual(int(vehicle_summary.get("control_command_step_count", 0) or 0), 3)
            self.assertEqual(
                int(vehicle_summary.get("control_throttle_brake_overlap_step_count", 0) or 0),
                0,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("control_throttle_brake_overlap_ratio", 0.0) or 0.0),
                0.0,
                places=6,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("control_max_abs_steering_rate_degps", 0.0) or 0.0),
                0.0,
                places=6,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("control_max_abs_throttle_rate_per_sec", 0.0) or 0.0),
                2.0,
                places=6,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("control_max_abs_brake_rate_per_sec", 0.0) or 0.0),
                0.5,
                places=6,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("control_max_throttle_plus_brake", 0.0) or 0.0),
                0.4,
                places=6,
            )
            self.assertEqual(int(vehicle_summary.get("speed_tracking_target_step_count", 0) or 0), 3)
            speed_tracking_error_values = [
                float(row.get("speed_tracking_error_mps", 0.0) or 0.0)
                for row in trace
                if row.get("speed_tracking_error_mps") is not None
            ]
            self.assertEqual(len(speed_tracking_error_values), 3)
            self.assertAlmostEqual(
                float(vehicle_summary.get("speed_tracking_error_mps_min", 0.0) or 0.0),
                min(speed_tracking_error_values),
                places=6,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("speed_tracking_error_mps_avg", 0.0) or 0.0),
                sum(speed_tracking_error_values) / float(len(speed_tracking_error_values)),
                places=6,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("speed_tracking_error_mps_max", 0.0) or 0.0),
                max(speed_tracking_error_values),
                places=6,
            )
            self.assertAlmostEqual(
                float(vehicle_summary.get("speed_tracking_error_abs_mps_max", 0.0) or 0.0),
                max(abs(value) for value in speed_tracking_error_values),
                places=6,
            )


if __name__ == "__main__":
    unittest.main()
