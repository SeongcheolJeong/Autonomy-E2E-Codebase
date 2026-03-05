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


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


class VehicleDynamicsEnhancedTests(unittest.TestCase):
    def test_control_sequence_initial_conditions_are_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 25.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 1.0,
                    "initial_speed_mps": 10.0,
                    "initial_position_m": 5.0,
                    "commands": [{"throttle": 0.0, "brake": 0.0}],
                },
            )

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(float(payload.get("initial_speed_mps", -1.0)), 10.0)
            self.assertEqual(float(payload.get("initial_position_m", -1.0)), 5.0)
            self.assertAlmostEqual(float(payload.get("final_position_m", 0.0)), 15.0, places=6)
            row = payload.get("trace", [])[0]
            self.assertIn("drag_force_n", row)
            self.assertIn("net_force_n", row)

    def test_speed_tracking_error_metrics_are_emitted_when_target_speed_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 40.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.5,
                    "initial_speed_mps": 4.0,
                    "default_target_speed_mps": 6.0,
                    "commands": [
                        {"throttle": 0.3, "brake": 0.0},
                        {"throttle": 0.3, "brake": 0.0},
                        {"throttle": 0.0, "brake": 0.2, "target_speed_mps": 5.0},
                    ],
                },
            )

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            trace = payload.get("trace", [])
            self.assertEqual(len(trace), 3)
            self.assertEqual(int(payload.get("speed_tracking_target_step_count", 0) or 0), 3)
            tracking_errors = []
            for row in trace:
                self.assertIn("target_speed_mps", row)
                self.assertIn("speed_tracking_error_mps", row)
                target_speed = float(row.get("target_speed_mps", 0.0) or 0.0)
                speed = float(row.get("speed_mps", 0.0) or 0.0)
                tracking_error = float(row.get("speed_tracking_error_mps", 0.0) or 0.0)
                self.assertAlmostEqual(tracking_error, speed - target_speed, places=6)
                tracking_errors.append(tracking_error)
            self.assertAlmostEqual(
                float(payload.get("speed_tracking_error_mps_min", 0.0) or 0.0),
                min(tracking_errors),
                places=6,
            )
            self.assertAlmostEqual(
                float(payload.get("speed_tracking_error_mps_avg", 0.0) or 0.0),
                sum(tracking_errors) / float(len(tracking_errors)),
                places=6,
            )
            self.assertAlmostEqual(
                float(payload.get("speed_tracking_error_mps_max", 0.0) or 0.0),
                max(tracking_errors),
                places=6,
            )
            self.assertAlmostEqual(
                float(payload.get("speed_tracking_error_abs_mps_max", 0.0) or 0.0),
                max(abs(value) for value in tracking_errors),
                places=6,
            )

    def test_heavier_vehicle_loses_less_speed_under_same_drag(self) -> None:
        def _run_case(mass_kg: float) -> float:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                profile_path = tmp_path / "vehicle_profile.json"
                sequence_path = tmp_path / "control_sequence.json"
                out_path = tmp_path / "vehicle_trace.json"
                _write_json(
                    profile_path,
                    {
                        "profile_schema_version": "vehicle_profile_v0",
                        "wheelbase_m": 2.8,
                        "max_accel_mps2": 3.0,
                        "max_decel_mps2": 5.0,
                        "max_speed_mps": 60.0,
                        "mass_kg": mass_kg,
                        "rolling_resistance_coeff": 0.0,
                        "drag_coefficient": 0.5,
                        "frontal_area_m2": 2.2,
                        "air_density_kgpm3": 1.225,
                    },
                )
                _write_json(
                    sequence_path,
                    {
                        "sequence_schema_version": "control_sequence_v0",
                        "dt_sec": 1.0,
                        "initial_speed_mps": 20.0,
                        "commands": [{"throttle": 0.0, "brake": 0.0}] * 10,
                    },
                )
                run_script(
                    PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                    "--vehicle-profile",
                    str(profile_path),
                    "--control-sequence",
                    str(sequence_path),
                    "--out",
                    str(out_path),
                )
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                return float(payload.get("final_speed_mps", 0.0))

        light_mass_final_speed = _run_case(1000.0)
        heavy_mass_final_speed = _run_case(3000.0)
        self.assertLess(light_mass_final_speed, 20.0)
        self.assertLess(heavy_mass_final_speed, 20.0)
        self.assertGreater(heavy_mass_final_speed, light_mass_final_speed)

    def test_road_grade_changes_longitudinal_dynamics(self) -> None:
        def _run_case(grade_percent: float) -> tuple[float, dict[str, object]]:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                profile_path = tmp_path / "vehicle_profile.json"
                sequence_path = tmp_path / "control_sequence.json"
                out_path = tmp_path / "vehicle_trace.json"
                _write_json(
                    profile_path,
                    {
                        "profile_schema_version": "vehicle_profile_v0",
                        "wheelbase_m": 2.8,
                        "max_accel_mps2": 3.0,
                        "max_decel_mps2": 5.0,
                        "max_speed_mps": 60.0,
                        "mass_kg": 1500.0,
                        "rolling_resistance_coeff": 0.0,
                        "drag_coefficient": 0.0,
                        "frontal_area_m2": 2.2,
                        "air_density_kgpm3": 1.225,
                    },
                )
                _write_json(
                    sequence_path,
                    {
                        "sequence_schema_version": "control_sequence_v0",
                        "dt_sec": 1.0,
                        "initial_speed_mps": 15.0,
                        "default_road_grade_percent": grade_percent,
                        "commands": [{"throttle": 0.0, "brake": 0.0}] * 10,
                    },
                )
                run_script(
                    PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                    "--vehicle-profile",
                    str(profile_path),
                    "--control-sequence",
                    str(sequence_path),
                    "--out",
                    str(out_path),
                )
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                trace = payload.get("trace", [])
                first_row = trace[0] if isinstance(trace, list) and trace else {}
                return float(payload.get("final_speed_mps", 0.0)), first_row if isinstance(first_row, dict) else {}

        uphill_speed, uphill_row = _run_case(8.0)
        flat_speed, flat_row = _run_case(0.0)
        downhill_speed, downhill_row = _run_case(-8.0)

        self.assertLess(uphill_speed, flat_speed)
        self.assertGreater(downhill_speed, flat_speed)
        self.assertGreater(float(uphill_row.get("grade_force_n", 0.0)), 0.0)
        self.assertAlmostEqual(float(flat_row.get("grade_force_n", 999.0)), 0.0, places=6)
        self.assertLess(float(downhill_row.get("grade_force_n", 0.0)), 0.0)

    def test_command_grade_overrides_sequence_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 60.0,
                    "mass_kg": 1500.0,
                    "rolling_resistance_coeff": 0.0,
                    "drag_coefficient": 0.0,
                    "frontal_area_m2": 2.2,
                    "air_density_kgpm3": 1.225,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 1.0,
                    "initial_speed_mps": 15.0,
                    "default_road_grade_percent": 5.0,
                    "commands": [
                        {"throttle": 0.0, "brake": 0.0, "road_grade_percent": -3.0},
                        {"throttle": 0.0, "brake": 0.0},
                    ],
                },
            )
            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            trace = payload.get("trace", [])
            self.assertEqual(len(trace), 2)
            first = trace[0]
            second = trace[1]
            self.assertAlmostEqual(float(first.get("road_grade_percent", 0.0)), -3.0, places=6)
            self.assertAlmostEqual(float(second.get("road_grade_percent", 0.0)), 5.0, places=6)
            self.assertLess(float(first.get("grade_force_n", 0.0)), 0.0)
            self.assertGreater(float(second.get("grade_force_n", 0.0)), 0.0)

    def test_invalid_default_road_grade_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 25.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "default_road_grade_percent": "steep",
                    "commands": [{"throttle": 0.0, "brake": 0.0}],
                },
            )

            proc = run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
                expected_rc=2,
            )
            self.assertIn("default_road_grade_percent must be a number", proc.stderr)

    def test_planar_kinematics_accumulates_lateral_motion_with_steering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 40.0,
                    "mass_kg": 1500.0,
                    "rolling_resistance_coeff": 0.0,
                    "drag_coefficient": 0.0,
                    "frontal_area_m2": 2.2,
                    "air_density_kgpm3": 1.225,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "initial_speed_mps": 8.0,
                    "initial_heading_deg": 0.0,
                    "initial_lateral_position_m": 0.0,
                    "enable_planar_kinematics": True,
                    "commands": [
                        {"throttle": 0.0, "brake": 0.0, "steering_angle_deg": 8.0}
                    ]
                    * 20,
                },
            )

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("vehicle_dynamics_model"), "planar_bicycle_force_balance_v1")
            self.assertTrue(bool(payload.get("planar_kinematics_enabled")))
            self.assertGreater(float(payload.get("final_heading_deg", 0.0)), 0.0)
            self.assertGreater(float(payload.get("final_lateral_position_m", 0.0)), 0.0)
            first_row = payload.get("trace", [])[0]
            self.assertAlmostEqual(float(first_row.get("steering_angle_deg", 0.0)), 8.0, places=6)
            self.assertGreater(float(first_row.get("yaw_rate_rps", 0.0)), 0.0)
            self.assertIn("x_m", first_row)
            self.assertIn("y_m", first_row)

    def test_steering_without_planar_mode_does_not_change_lateral_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 40.0,
                    "mass_kg": 1500.0,
                    "rolling_resistance_coeff": 0.0,
                    "drag_coefficient": 0.0,
                    "frontal_area_m2": 2.2,
                    "air_density_kgpm3": 1.225,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "initial_speed_mps": 6.0,
                    "initial_heading_deg": 3.0,
                    "initial_lateral_position_m": 2.5,
                    "commands": [
                        {"throttle": 0.0, "brake": 0.0, "steering_angle_deg": 10.0}
                    ]
                    * 10,
                },
            )

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("vehicle_dynamics_model"), "longitudinal_force_balance_v1")
            self.assertFalse(bool(payload.get("planar_kinematics_enabled")))
            self.assertAlmostEqual(float(payload.get("final_heading_deg", 0.0)), 3.0, places=6)
            self.assertAlmostEqual(float(payload.get("final_lateral_position_m", 0.0)), 2.5, places=6)
            first_row = payload.get("trace", [])[0]
            self.assertAlmostEqual(float(first_row.get("yaw_rate_rps", 1.0)), 0.0, places=6)

    def test_invalid_steering_angle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 25.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "commands": [{"throttle": 0.0, "brake": 0.0, "steering_angle_deg": 90.0}],
                },
            )

            proc = run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
                expected_rc=2,
            )
            self.assertIn("commands[0].steering_angle_deg magnitude must be < 89.9", proc.stderr)

    def test_dynamic_bicycle_mode_generates_lateral_velocity_and_yaw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 40.0,
                    "mass_kg": 1500.0,
                    "rolling_resistance_coeff": 0.0,
                    "drag_coefficient": 0.0,
                    "frontal_area_m2": 2.2,
                    "air_density_kgpm3": 1.225,
                    "front_axle_to_cg_m": 1.3,
                    "rear_axle_to_cg_m": 1.5,
                    "yaw_inertia_kgm2": 2500.0,
                    "cornering_stiffness_front_nprad": 85000.0,
                    "cornering_stiffness_rear_nprad": 80000.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "initial_speed_mps": 12.0,
                    "enable_planar_kinematics": True,
                    "enable_dynamic_bicycle": True,
                    "commands": [
                        {"throttle": 0.0, "brake": 0.0, "steering_angle_deg": 6.0}
                    ]
                    * 30,
                },
            )

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("vehicle_dynamics_model"), "planar_dynamic_bicycle_force_balance_v1")
            self.assertTrue(bool(payload.get("planar_kinematics_enabled")))
            self.assertTrue(bool(payload.get("dynamic_bicycle_enabled")))
            self.assertGreater(float(payload.get("final_heading_deg", 0.0)), 0.0)
            self.assertGreater(float(payload.get("final_lateral_position_m", 0.0)), 0.0)
            self.assertGreater(abs(float(payload.get("final_lateral_velocity_mps", 0.0))), 0.0)
            self.assertGreater(abs(float(payload.get("final_yaw_rate_rps", 0.0))), 0.0)
            first_row = payload.get("trace", [])[0]
            self.assertIn("lateral_velocity_mps", first_row)
            self.assertIn("lateral_accel_mps2", first_row)
            self.assertIn("yaw_accel_rps2", first_row)

    def test_dynamic_bicycle_requires_planar_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 25.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "enable_dynamic_bicycle": True,
                    "commands": [{"throttle": 0.0, "brake": 0.0}],
                },
            )

            proc = run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
                expected_rc=2,
            )
            self.assertIn("enable_dynamic_bicycle requires enable_planar_kinematics=true", proc.stderr)

    def test_invalid_mass_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 25.0,
                    "mass_kg": 0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "commands": [{"throttle": 0.0, "brake": 0.0}],
                },
            )

            proc = run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
                expected_rc=2,
            )
            self.assertIn("mass_kg must be > 0", proc.stderr)

    def test_invalid_cornering_stiffness_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 3.0,
                    "max_decel_mps2": 5.0,
                    "max_speed_mps": 25.0,
                    "cornering_stiffness_front_nprad": 0.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.1,
                    "commands": [{"throttle": 0.0, "brake": 0.0}],
                },
            )

            proc = run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
                expected_rc=2,
            )
            self.assertIn("cornering_stiffness_front_nprad must be > 0", proc.stderr)

    def test_low_tire_friction_limits_longitudinal_force(self) -> None:
        def _run_case(tire_friction_coeff: float) -> tuple[float, dict[str, object]]:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                profile_path = tmp_path / "vehicle_profile.json"
                sequence_path = tmp_path / "control_sequence.json"
                out_path = tmp_path / "vehicle_trace.json"
                _write_json(
                    profile_path,
                    {
                        "profile_schema_version": "vehicle_profile_v0",
                        "wheelbase_m": 2.8,
                        "max_accel_mps2": 8.0,
                        "max_decel_mps2": 8.0,
                        "max_speed_mps": 50.0,
                        "mass_kg": 1500.0,
                        "rolling_resistance_coeff": 0.0,
                        "drag_coefficient": 0.0,
                        "frontal_area_m2": 2.2,
                        "air_density_kgpm3": 1.225,
                        "tire_friction_coeff": tire_friction_coeff,
                    },
                )
                _write_json(
                    sequence_path,
                    {
                        "sequence_schema_version": "control_sequence_v0",
                        "dt_sec": 0.2,
                        "initial_speed_mps": 0.0,
                        "commands": [{"throttle": 1.0, "brake": 0.0}] * 10,
                    },
                )
                run_script(
                    PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                    "--vehicle-profile",
                    str(profile_path),
                    "--control-sequence",
                    str(sequence_path),
                    "--out",
                    str(out_path),
                )
                payload = json.loads(out_path.read_text(encoding="utf-8"))
                trace = payload.get("trace", [])
                first_row = trace[0] if isinstance(trace, list) and trace else {}
                return float(payload.get("final_speed_mps", 0.0)), first_row if isinstance(first_row, dict) else {}

        high_mu_final_speed, high_mu_first_row = _run_case(1.0)
        low_mu_final_speed, low_mu_first_row = _run_case(0.2)

        self.assertGreater(high_mu_final_speed, low_mu_final_speed)
        self.assertFalse(bool(high_mu_first_row.get("longitudinal_force_limited", True)))
        self.assertTrue(bool(low_mu_first_row.get("longitudinal_force_limited", False)))
        self.assertGreater(
            float(high_mu_first_row.get("tire_force_limit_n", 0.0)),
            float(low_mu_first_row.get("tire_force_limit_n", 0.0)),
        )
        self.assertAlmostEqual(float(low_mu_first_row.get("effective_friction_coeff", 0.0)), 0.2, places=6)

    def test_command_surface_friction_scale_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "vehicle_profile.json"
            sequence_path = tmp_path / "control_sequence.json"
            out_path = tmp_path / "vehicle_trace.json"
            _write_json(
                profile_path,
                {
                    "profile_schema_version": "vehicle_profile_v0",
                    "wheelbase_m": 2.8,
                    "max_accel_mps2": 8.0,
                    "max_decel_mps2": 6.0,
                    "max_speed_mps": 50.0,
                    "mass_kg": 1500.0,
                    "rolling_resistance_coeff": 0.0,
                    "drag_coefficient": 0.0,
                    "frontal_area_m2": 2.2,
                    "air_density_kgpm3": 1.225,
                    "tire_friction_coeff": 1.0,
                },
            )
            _write_json(
                sequence_path,
                {
                    "sequence_schema_version": "control_sequence_v0",
                    "dt_sec": 0.2,
                    "default_surface_friction_scale": 0.2,
                    "commands": [
                        {"throttle": 1.0, "brake": 0.0, "surface_friction_scale": 1.0},
                        {"throttle": 1.0, "brake": 0.0},
                    ],
                },
            )

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/vehicle_dynamics_stub.py",
                "--vehicle-profile",
                str(profile_path),
                "--control-sequence",
                str(sequence_path),
                "--out",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            trace = payload.get("trace", [])
            self.assertEqual(len(trace), 2)
            first = trace[0]
            second = trace[1]

            self.assertAlmostEqual(float(first.get("surface_friction_scale", 0.0)), 1.0, places=6)
            self.assertAlmostEqual(float(second.get("surface_friction_scale", 0.0)), 0.2, places=6)
            self.assertAlmostEqual(float(first.get("effective_friction_coeff", 0.0)), 1.0, places=6)
            self.assertAlmostEqual(float(second.get("effective_friction_coeff", 0.0)), 0.2, places=6)
            self.assertGreater(float(first.get("tire_force_limit_n", 0.0)), float(second.get("tire_force_limit_n", 0.0)))
            self.assertFalse(bool(first.get("longitudinal_force_limited", True)))
            self.assertTrue(bool(second.get("longitudinal_force_limited", False)))


if __name__ == "__main__":
    unittest.main()
