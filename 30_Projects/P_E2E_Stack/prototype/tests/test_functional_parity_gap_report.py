import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROTOTYPE_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROTOTYPE_DIR / "check_functional_parity_gaps.py"
PYTHON = sys.executable


def run_script(*args: str, expected_rc: int = 0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [PYTHON, str(SCRIPT_PATH), *args],
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


class FunctionalParityGapReportTests(unittest.TestCase):
    def _write_matrix(self, path: Path, *, runtime_sensor_status: str) -> None:
        path.write_text(
            "\n".join(
                [
                    "# Applied Intuition Stack Module Parity Matrix (v1.64)",
                    "",
                    "| Module | AppliedDocs Reference | Local Spec | Local Code Base | Parity Phase | Phase Gate | Contract Status | Runtime Status | First Deliverable (DoD) |",
                    "|---|---|---|---|---|---|---|---|---|",
                    "| `cloud_engine` | ref | spec | code | Phase-1 | PHASE1_DONE | CONTRACT_NATIVE | RUNTIME_NATIVE | done |",
                    f"| `sensor_sim` | ref | spec | code | Phase-2 | PHASE2_DONE | CONTRACT_NATIVE | {runtime_sensor_status} | todo |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_master_plan(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                [
                    "# E2E Stack Master Plan",
                    "",
                    "## Milestones",
                    "",
                    "| ID | Milestone | Priority | Status | Exit Criteria |",
                    "|---|---|---|---|---|",
                    "| F1 | Runtime Scenario Execution Path | P0 | IN_PROGRESS | done |",
                    "| F2 | Scenario Standards Interop | P0 | DONE | done |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_checklist(self, path: Path, *, sensor_status: str) -> None:
        path.write_text(
            "\n".join(
                [
                    "# Phase Checklist",
                    "",
                    "## cloud_engine",
                    "",
                    "| Feature | Local Evidence Path | Status | Verification Command |",
                    "|---|---|---|---|",
                    "| batch flow | file.py | NATIVE | python3 -m unittest tests.test_ci_scripts.CloudBatchRunnerTests |",
                    "",
                    "## sensor_sim",
                    "",
                    "| Feature | Local Evidence Path | Status | Verification Command |",
                    "|---|---|---|---|",
                    f"| plugin bridge | bridge.py | {sensor_status} | python3 -m unittest tests.test_ci_scripts.SensorSimBridgeTests |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_builds_gap_report_with_open_gap_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            matrix_path = tmp_path / "STACK_MODULE_PARITY_MATRIX.md"
            master_plan_path = tmp_path / "STACK_MASTER_PLAN.md"
            checklist_path = tmp_path / "PHASE2_MODULE_PARITY_CHECKLIST.md"
            out_json = tmp_path / "reports" / "functional_parity_gap_report_v0.json"
            out_md = tmp_path / "reports" / "functional_parity_gap_report_v0.md"

            self._write_matrix(matrix_path, runtime_sensor_status="RUNTIME_PARTIAL")
            self._write_master_plan(master_plan_path)
            self._write_checklist(checklist_path, sensor_status="PARTIAL")

            proc = run_script(
                "--matrix",
                str(matrix_path),
                "--master-plan",
                str(master_plan_path),
                "--checklist",
                str(checklist_path),
                "--out-json",
                str(out_json),
                "--out-markdown",
                str(out_md),
            )
            self.assertIn(
                "[summary] runtime_native=1/2 runtime_open=1 contract_open=0 milestone_open=1 checklist_non_native_rows=1 consistency_issues=0",
                proc.stdout,
            )

            payload = json.loads(out_json.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            self.assertEqual(summary.get("total_module_count"), 2)
            self.assertEqual(summary.get("runtime_native_module_count"), 1)
            self.assertEqual(summary.get("runtime_open_module_count"), 1)
            self.assertEqual(summary.get("contract_open_module_count"), 0)
            self.assertEqual(summary.get("open_milestone_count"), 1)
            self.assertEqual(summary.get("checklist_non_native_row_count"), 1)
            self.assertEqual(summary.get("consistency_issue_count"), 0)
            self.assertEqual(summary.get("open_gap_count_total"), 3)

            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("## Runtime Open Modules", markdown)
            self.assertIn("## Open Milestones", markdown)
            self.assertIn("## Checklist Non-Native Rows", markdown)

    def test_detects_matrix_checklist_runtime_inconsistency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            matrix_path = tmp_path / "STACK_MODULE_PARITY_MATRIX.md"
            master_plan_path = tmp_path / "STACK_MASTER_PLAN.md"
            checklist_path = tmp_path / "PHASE2_MODULE_PARITY_CHECKLIST.md"
            out_json = tmp_path / "reports" / "functional_parity_gap_report_v0.json"
            out_md = tmp_path / "reports" / "functional_parity_gap_report_v0.md"

            self._write_matrix(matrix_path, runtime_sensor_status="RUNTIME_PARTIAL")
            self._write_master_plan(master_plan_path)
            self._write_checklist(checklist_path, sensor_status="NATIVE")

            proc = run_script(
                "--matrix",
                str(matrix_path),
                "--master-plan",
                str(master_plan_path),
                "--checklist",
                str(checklist_path),
                "--out-json",
                str(out_json),
                "--out-markdown",
                str(out_md),
            )
            self.assertIn("consistency_issues=1", proc.stdout)

            payload = json.loads(out_json.read_text(encoding="utf-8"))
            issues = payload.get("consistency_issues", [])
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].get("type"), "runtime_not_native_but_checklist_all_native")
            self.assertEqual(issues[0].get("module"), "sensor_sim")

            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("runtime_not_native_but_checklist_all_native", markdown)

    def test_fail_on_open_gaps_returns_exit_code_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            matrix_path = tmp_path / "STACK_MODULE_PARITY_MATRIX.md"
            master_plan_path = tmp_path / "STACK_MASTER_PLAN.md"
            checklist_path = tmp_path / "PHASE2_MODULE_PARITY_CHECKLIST.md"
            out_json = tmp_path / "reports" / "functional_parity_gap_report_v0.json"
            out_md = tmp_path / "reports" / "functional_parity_gap_report_v0.md"

            self._write_matrix(matrix_path, runtime_sensor_status="RUNTIME_PARTIAL")
            self._write_master_plan(master_plan_path)
            self._write_checklist(checklist_path, sensor_status="PARTIAL")

            proc = run_script(
                "--matrix",
                str(matrix_path),
                "--master-plan",
                str(master_plan_path),
                "--checklist",
                str(checklist_path),
                "--out-json",
                str(out_json),
                "--out-markdown",
                str(out_md),
                "--fail-on-open-gaps",
                "1",
                expected_rc=3,
            )
            self.assertIn("[error] open gaps remain: 3", proc.stderr)


if __name__ == "__main__":
    unittest.main()
