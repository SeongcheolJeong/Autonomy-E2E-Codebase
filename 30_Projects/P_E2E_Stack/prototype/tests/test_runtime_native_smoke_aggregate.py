from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_release_summary_artifact import summarize_phase2_log_replay, summarize_runtime_native_smoke


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


class RuntimeNativeSmokeAggregateTests(unittest.TestCase):
    def test_phase2_log_replay_summary_aggregates_manifests(self) -> None:
        summary = summarize_phase2_log_replay(
            [
                {
                    "batch_id": "BATCH_001",
                    "phase2_log_replay_checked": True,
                    "phase2_log_replay_status": "pass",
                    "phase2_log_replay_manifest_present": True,
                    "phase2_log_replay_summary_present": True,
                    "phase2_log_replay_run_source": "summary",
                    "phase2_log_replay_run_status": "success",
                    "phase2_log_replay_log_id": "LOG_001",
                    "phase2_log_replay_map_id": "MAP_001",
                },
                {
                    "batch_id": "BATCH_002",
                    "phase2_log_replay_checked": True,
                    "phase2_log_replay_status": "fail",
                    "phase2_log_replay_manifest_present": False,
                    "phase2_log_replay_summary_present": False,
                    "phase2_log_replay_run_source": "manifest",
                    "phase2_log_replay_run_status": "error",
                    "phase2_log_replay_log_id": "",
                    "phase2_log_replay_map_id": "",
                },
                {
                    "batch_id": "BATCH_003",
                    "phase2_log_replay_checked": False,
                },
            ]
        )
        self.assertEqual(int(summary.get("pipeline_manifest_count", 0) or 0), 3)
        self.assertEqual(int(summary.get("evaluated_manifest_count", 0) or 0), 2)
        self.assertEqual(summary.get("status_counts"), {"fail": 1, "pass": 1})
        self.assertEqual(summary.get("run_status_counts"), {"fail": 1, "pass": 1})
        self.assertEqual(summary.get("run_source_counts"), {"manifest": 1, "summary": 1})
        self.assertEqual(int(summary.get("manifest_present_count", 0) or 0), 1)
        self.assertEqual(int(summary.get("summary_present_count", 0) or 0), 1)
        self.assertEqual(int(summary.get("missing_manifest_count", 0) or 0), 1)
        self.assertEqual(int(summary.get("missing_summary_count", 0) or 0), 1)
        self.assertEqual(int(summary.get("log_id_present_count", 0) or 0), 1)
        self.assertEqual(int(summary.get("map_id_present_count", 0) or 0), 1)

    def test_runtime_native_smoke_summary_aggregates_module_statuses(self) -> None:
        summary = summarize_runtime_native_smoke(
            [
                {
                    "batch_id": "BATCH_A",
                    "phase3_object_sim_checked": True,
                    "phase3_object_sim_status": "pass",
                    "phase2_log_replay_checked": True,
                    "phase2_log_replay_status": "pass",
                    "phase2_map_routing_checked": True,
                    "phase2_map_routing_status": "pass",
                    "phase2_map_route_checked": True,
                    "phase2_map_route_status": "pass",
                },
                {
                    "batch_id": "BATCH_B",
                    "phase3_object_sim_checked": True,
                    "phase3_object_sim_status": "partial",
                    "phase2_log_replay_checked": True,
                    "phase2_log_replay_status": "fail",
                    "phase2_map_routing_checked": True,
                    "phase2_map_routing_status": "warn",
                    "phase2_map_route_checked": True,
                    "phase2_map_route_status": "pass",
                },
                {
                    "batch_id": "BATCH_C",
                    "phase2_map_routing_checked": True,
                    "phase2_map_routing_status": "pass",
                    "phase2_map_route_checked": False,
                },
            ]
        )
        self.assertEqual(int(summary.get("pipeline_manifest_count", 0) or 0), 3)
        self.assertEqual(int(summary.get("evaluated_manifest_count", 0) or 0), 3)
        module_summaries = summary.get("module_summaries", {})
        self.assertEqual(
            module_summaries.get("object_sim", {}),
            {"evaluated_manifest_count": 2, "status_counts": {"partial": 1, "pass": 1}},
        )
        self.assertEqual(
            module_summaries.get("log_sim", {}),
            {"evaluated_manifest_count": 2, "status_counts": {"fail": 1, "pass": 1}},
        )
        self.assertEqual(
            module_summaries.get("map_toolset", {}),
            {"evaluated_manifest_count": 3, "status_counts": {"partial": 1, "pass": 2}},
        )
        self.assertEqual(summary.get("all_modules_status_counts"), {"fail": 1, "partial": 1, "pass": 1})
        self.assertEqual(int(summary.get("all_modules_pass_manifest_count", 0) or 0), 1)

    def test_notification_payload_surfaces_runtime_native_smoke_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_SMOKE_NOTIFY_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase2_log_replay_summary": {
                            "evaluated_manifest_count": 1,
                            "status_counts": {"pass": 1},
                            "run_status_counts": {"pass": 1},
                            "run_source_counts": {"summary": 1},
                            "manifest_present_count": 1,
                            "summary_present_count": 1,
                            "missing_manifest_count": 0,
                            "missing_summary_count": 0,
                            "log_id_present_count": 1,
                            "map_id_present_count": 1,
                        },
                        "runtime_native_smoke_summary": {
                            "evaluated_manifest_count": 1,
                            "module_summaries": {
                                "object_sim": {"evaluated_manifest_count": 1, "status_counts": {"pass": 1}},
                                "log_sim": {"evaluated_manifest_count": 1, "status_counts": {"pass": 1}},
                                "map_toolset": {"evaluated_manifest_count": 1, "status_counts": {"pass": 1}},
                            },
                            "all_modules_status_counts": {"pass": 1},
                            "all_modules_pass_manifest_count": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_path),
                "--out-json",
                str(out_path),
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            message_text = str(payload.get("message_text", ""))
            self.assertIn("phase2_log_replay_summary=evaluated=1,statuses=pass:1", message_text)
            self.assertIn("runtime_native_smoke_summary=evaluated=1,all_statuses=pass:1", message_text)
            slack_text = json.dumps(payload.get("slack", {}))
            self.assertIn("*phase2 log replay*", slack_text)
            self.assertIn("*runtime native smoke*", slack_text)

    def test_phase2_log_replay_warn_threshold_promotes_pass_to_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_RN2_WARN_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase2_log_replay_summary": {
                            "evaluated_manifest_count": 2,
                            "status_counts": {"fail": 2},
                            "run_status_counts": {"fail": 2},
                            "run_source_counts": {"manifest": 2},
                            "manifest_present_count": 2,
                            "summary_present_count": 0,
                            "missing_manifest_count": 0,
                            "missing_summary_count": 2,
                            "log_id_present_count": 2,
                            "map_id_present_count": 2,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_path),
                "--out-json",
                str(out_path),
                "--phase2-log-replay-fail-warn-max",
                "1",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertIn(
                "phase2_log_replay_fail_count=2 exceeded warn_max=1",
                payload.get("phase2_log_replay_warning", ""),
            )
            self.assertIn(
                "phase2_log_replay_fail_count_above_warn_max",
                payload.get("phase2_log_replay_warning_reasons", []),
            )
            self.assertIn("phase2_log_replay_warning=", payload.get("message_text", ""))

    def test_runtime_native_smoke_hold_threshold_promotes_pass_to_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_RN2_HOLD_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "runtime_native_smoke_summary": {
                            "evaluated_manifest_count": 2,
                            "module_summaries": {
                                "object_sim": {"evaluated_manifest_count": 2, "status_counts": {"fail": 2}},
                                "log_sim": {"evaluated_manifest_count": 2, "status_counts": {"fail": 2}},
                                "map_toolset": {"evaluated_manifest_count": 2, "status_counts": {"fail": 2}},
                            },
                            "all_modules_status_counts": {"fail": 2},
                            "all_modules_pass_manifest_count": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_path),
                "--out-json",
                str(out_path),
                "--runtime-native-smoke-fail-hold-max",
                "1",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertIn(
                "runtime_native_smoke_fail_count=2 exceeded hold_max=1",
                payload.get("runtime_native_smoke_warning", ""),
            )
            self.assertIn(
                "runtime_native_smoke_fail_count_above_hold_max",
                payload.get("runtime_native_smoke_warning_reasons", []),
            )
            self.assertIn("runtime_native_smoke_warning=", payload.get("message_text", ""))


if __name__ == "__main__":
    unittest.main()
