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


class CoreSimAvoidanceFrictionTests(unittest.TestCase):
    def _base_scenario(self) -> dict[str, object]:
        return {
            "scenario_schema_version": "scenario_definition_v0",
            "scenario_id": "SC_CORE_AVOIDANCE_FRICTION_001",
            "duration_sec": 4.0,
            "dt_sec": 0.1,
            "ego": {"actor_id": "ego", "position_m": 0.0, "speed_mps": 22.0},
            "npcs": [{"actor_id": "npc_1", "position_m": 24.0, "speed_mps": 10.0}],
        }

    def test_high_friction_ego_avoidance_prevents_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scenario_path = tmp_path / "scenario.json"
            runs_root = tmp_path / "runs"
            run_id = "RUN_CORE_AVOID_HIGH_FRICTION_001"
            _write_json(scenario_path, self._base_scenario())

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/core_sim_runner.py",
                "--scenario",
                str(scenario_path),
                "--run-id",
                run_id,
                "--out",
                str(runs_root),
                "--enable-ego-collision-avoidance",
                "true",
                "--avoidance-ttc-threshold-sec",
                "2.5",
                "--ego-max-brake-mps2",
                "9.0",
                "--tire-friction-coeff",
                "1.0",
                "--surface-friction-scale",
                "1.0",
            )
            summary = json.loads((runs_root / run_id / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(str(summary.get("status", "")), "success")
            self.assertFalse(bool(summary.get("collision", True)))
            self.assertTrue(bool(summary.get("enable_ego_collision_avoidance", False)))
            self.assertGreater(int(summary.get("ego_avoidance_brake_event_count", 0) or 0), 0)
            self.assertAlmostEqual(float(summary.get("ego_avoidance_applied_brake_mps2_max", 0.0) or 0.0), 9.0, places=6)

    def test_low_friction_limits_braking_and_causes_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scenario_path = tmp_path / "scenario.json"
            runs_root = tmp_path / "runs"
            run_id = "RUN_CORE_AVOID_LOW_FRICTION_001"
            _write_json(scenario_path, self._base_scenario())

            run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/core_sim_runner.py",
                "--scenario",
                str(scenario_path),
                "--run-id",
                run_id,
                "--out",
                str(runs_root),
                "--enable-ego-collision-avoidance",
                "true",
                "--avoidance-ttc-threshold-sec",
                "2.5",
                "--ego-max-brake-mps2",
                "9.0",
                "--tire-friction-coeff",
                "1.0",
                "--surface-friction-scale",
                "0.2",
            )
            summary = json.loads((runs_root / run_id / "summary.json").read_text(encoding="utf-8"))
            lane_risk_summary = summary.get("lane_risk_summary", {})
            self.assertEqual(str(summary.get("status", "")), "failed")
            self.assertTrue(bool(summary.get("collision", False)))
            self.assertGreater(int(summary.get("ego_avoidance_brake_event_count", 0) or 0), 0)
            self.assertAlmostEqual(
                float(summary.get("ego_avoidance_applied_brake_mps2_max", 0.0) or 0.0),
                1.96133,
                places=4,
            )
            self.assertGreater(int(lane_risk_summary.get("step_rows_total", 0) or 0), 0)
            trace_csv = (runs_root / run_id / "trace.csv").read_text(encoding="utf-8")
            self.assertIn("ego_avoidance_brake_applied", trace_csv)
            self.assertIn("ego_avoidance_effective_brake_limit_mps2", trace_csv)

    def test_invalid_boolean_cli_for_avoidance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scenario_path = tmp_path / "scenario.json"
            _write_json(scenario_path, self._base_scenario())

            proc = run_script(
                PROTOTYPE_DIR / "../../P_Sim-Engine/prototype/core_sim_runner.py",
                "--scenario",
                str(scenario_path),
                "--run-id",
                "RUN_CORE_AVOID_BAD_BOOL_001",
                "--out",
                str(tmp_path / "runs"),
                "--enable-ego-collision-avoidance",
                "maybe",
                expected_rc=2,
            )
            self.assertIn("[error] enable-ego-collision-avoidance must be a boolean, got: maybe", proc.stdout)
            self.assertNotIn("Traceback", proc.stdout)


if __name__ == "__main__":
    unittest.main()
