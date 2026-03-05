from __future__ import annotations

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


class Phase4SecondaryNotificationWarningTests(unittest.TestCase):
    def _write_summary_json(
        self,
        out_path: Path,
        *,
        manifests: list[dict[str, object]],
        final_counts: dict[str, int] | None = None,
        phase3_vehicle_dynamics_summary: dict[str, object] | None = None,
    ) -> None:
        payload = {
            "release_prefix": "REL_PHASE4_NOTIFY_2026_0001",
            "sds_versions": ["sds_v1"],
            "summary_count": 1,
            "pipeline_manifest_count": len(manifests),
            "final_result_counts": final_counts or {"PASS": 1},
            "pipeline_overall_counts": {"PASS": 1},
            "pipeline_trend_counts": {"PASS": 1},
            "timing_ms": {"total": 100},
            "pipeline_manifests": manifests,
        }
        if phase3_vehicle_dynamics_summary is not None:
            payload["phase3_vehicle_dynamics_summary"] = phase3_vehicle_dynamics_summary
        out_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def test_notification_warns_when_secondary_coverage_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_LOW",
                        "phase4_reference_secondary_total_coverage_ratio": 0.4,
                        "phase4_reference_secondary_module_count": 1,
                        "phase4_reference_secondary_module_coverage": {"adp": 0.4},
                    },
                    {
                        "batch_id": "BATCH_HIGH",
                        "phase4_reference_secondary_total_coverage_ratio": 0.9,
                        "phase4_reference_secondary_module_count": 3,
                        "phase4_reference_secondary_module_coverage": {"adp": 0.9, "copilot": 0.9},
                    },
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-warn-ratio",
                "0.5",
                "--phase4-secondary-warn-min-modules",
                "1",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertEqual(payload.get("phase4_secondary_warn_ratio"), 0.5)
            self.assertEqual(payload.get("phase4_secondary_warn_min_modules"), 1)
            self.assertIn("phase4_secondary_coverage=0.400", str(payload.get("phase4_secondary_warning", "")))
            self.assertEqual(
                payload.get("phase4_secondary_warning_reasons"),
                ["phase4_secondary_coverage_below_threshold"],
            )
            self.assertEqual(len(payload.get("phase4_secondary_coverage_rows", [])), 2)
            self.assertIn("phase4_secondary_warning=", str(payload.get("message_text", "")))
            blocks = payload.get("slack", {}).get("blocks", [])
            self.assertTrue(
                any("phase4 secondary warning" in str(block) for block in blocks),
                msg=f"Expected phase4 warning block, got blocks={blocks}",
            )

    def test_notification_warns_when_primary_coverage_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PRIMARY_LOW",
                        "phase4_reference_primary_total_coverage_ratio": 0.55,
                    },
                    {
                        "batch_id": "BATCH_PRIMARY_HIGH",
                        "phase4_reference_primary_total_coverage_ratio": 0.95,
                    },
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-warn-ratio",
                "0.6",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertEqual(payload.get("phase4_primary_warn_ratio"), 0.6)
            self.assertIn("phase4_primary_coverage=0.550", str(payload.get("phase4_primary_warning", "")))
            self.assertEqual(
                payload.get("phase4_primary_warning_reasons"),
                ["phase4_primary_coverage_below_threshold"],
            )
            self.assertEqual(len(payload.get("phase4_primary_coverage_rows", [])), 2)
            self.assertIn("phase4_primary_warning=", str(payload.get("message_text", "")))
            blocks = payload.get("slack", {}).get("blocks", [])
            self.assertTrue(
                any("phase4 primary warning" in str(block) for block in blocks),
                msg=f"Expected phase4 primary warning block, got blocks={blocks}",
            )

    def test_notification_holds_when_primary_coverage_below_hold_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PRIMARY_HOLD",
                        "phase4_reference_primary_total_coverage_ratio": 0.5,
                    }
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-warn-ratio",
                "0.7",
                "--phase4-primary-hold-ratio",
                "0.55",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(payload.get("phase4_primary_hold_ratio"), 0.55)
            self.assertIn("below hold_threshold=0.550", str(payload.get("phase4_primary_warning", "")))
            self.assertIn(
                "phase4_primary_coverage_below_hold_threshold",
                payload.get("phase4_primary_warning_reasons", []),
            )

    def test_notification_warns_when_primary_module_coverage_below_module_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_ADP_LOW",
                        "phase4_reference_primary_total_coverage_ratio": 0.9,
                        "phase4_reference_primary_module_coverage": {"adp": 0.6, "copilot": 0.9},
                    },
                    {
                        "batch_id": "BATCH_COPILOT_LOW",
                        "phase4_reference_primary_total_coverage_ratio": 0.95,
                        "phase4_reference_primary_module_coverage": {"adp": 0.95, "copilot": 0.65},
                    },
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-module-warn-thresholds",
                "adp=0.8,copilot=0.7",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertEqual(
                payload.get("phase4_primary_module_warn_thresholds"),
                {"adp": 0.8, "copilot": 0.7},
            )
            self.assertEqual(
                payload.get("phase4_primary_warning_reasons"),
                ["phase4_primary_module_coverage_below_threshold"],
            )
            self.assertEqual(len(payload.get("phase4_primary_module_warning_rows", [])), 2)
            self.assertEqual(
                payload.get("phase4_primary_module_warning_summary"),
                {
                    "adp": {
                        "violation_count": 1,
                        "threshold": 0.8,
                        "min_coverage_ratio": 0.6,
                        "min_batch_id": "BATCH_ADP_LOW",
                    },
                    "copilot": {
                        "violation_count": 1,
                        "threshold": 0.7,
                        "min_coverage_ratio": 0.65,
                        "min_batch_id": "BATCH_COPILOT_LOW",
                    },
                },
            )
            self.assertIn("module=adp", str(payload.get("phase4_primary_warning", "")))
            self.assertIn(
                "phase4_primary_module_warning_summary=adp:count=1,min_cov=0.600,threshold=0.800,batch=BATCH_ADP_LOW;",
                str(payload.get("message_text", "")),
            )
            blocks = payload.get("slack", {}).get("blocks", [])
            self.assertTrue(
                any("module_warning_summary" in str(block) for block in blocks),
                msg=f"Expected primary module warning summary in block, got blocks={blocks}",
            )

    def test_notification_holds_when_primary_module_coverage_below_module_hold_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_ADP_LOW",
                        "phase4_reference_primary_total_coverage_ratio": 0.95,
                        "phase4_reference_primary_module_coverage": {"adp": 0.45, "copilot": 0.9},
                    }
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-module-hold-thresholds",
                "adp=0.5",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(payload.get("phase4_primary_module_hold_thresholds"), {"adp": 0.5})
            self.assertEqual(
                payload.get("phase4_primary_warning_reasons"),
                ["phase4_primary_module_coverage_below_hold_threshold"],
            )
            self.assertEqual(len(payload.get("phase4_primary_module_hold_rows", [])), 1)
            self.assertEqual(
                payload.get("phase4_primary_module_hold_summary"),
                {
                    "adp": {
                        "violation_count": 1,
                        "threshold": 0.5,
                        "min_coverage_ratio": 0.45,
                        "min_batch_id": "BATCH_ADP_LOW",
                    }
                },
            )
            self.assertIn(
                "phase4_primary_module_hold_summary=adp:count=1,min_cov=0.450,threshold=0.500,batch=BATCH_ADP_LOW",
                str(payload.get("message_text", "")),
            )

    def test_notification_warns_when_primary_coverage_summary_below_threshold_without_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            summary_payload = {
                "release_prefix": "REL_PHASE4_NOTIFY_2026_0001",
                "sds_versions": ["sds_v1"],
                "summary_count": 1,
                "pipeline_manifest_count": 0,
                "final_result_counts": {"PASS": 1},
                "pipeline_overall_counts": {"PASS": 1},
                "pipeline_trend_counts": {"PASS": 1},
                "timing_ms": {"total": 100},
                "pipeline_manifests": [],
                "phase4_primary_coverage_summary": {
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "min_coverage_ratio": 0.55,
                    "avg_coverage_ratio": 0.55,
                    "max_coverage_ratio": 0.55,
                    "lowest_batch_id": "BATCH_PRIMARY_SUMMARY",
                    "highest_batch_id": "BATCH_PRIMARY_SUMMARY",
                    "module_coverage_summary": {},
                },
            }
            summary_json.write_text(json.dumps(summary_payload) + "\n", encoding="utf-8")

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-warn-ratio",
                "0.6",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertIn("batch=BATCH_PRIMARY_SUMMARY", str(payload.get("phase4_primary_warning", "")))
            self.assertIn("phase4_primary_coverage=0.550", str(payload.get("phase4_primary_warning", "")))
            self.assertEqual(
                payload.get("phase4_primary_warning_reasons"),
                ["phase4_primary_coverage_below_threshold"],
            )
            self.assertEqual(len(payload.get("phase4_primary_coverage_rows", [])), 1)

    def test_notification_holds_when_primary_module_summary_below_hold_threshold_without_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            summary_payload = {
                "release_prefix": "REL_PHASE4_NOTIFY_2026_0001",
                "sds_versions": ["sds_v1"],
                "summary_count": 1,
                "pipeline_manifest_count": 0,
                "final_result_counts": {"PASS": 1},
                "pipeline_overall_counts": {"PASS": 1},
                "pipeline_trend_counts": {"PASS": 1},
                "timing_ms": {"total": 100},
                "pipeline_manifests": [],
                "phase4_primary_coverage_summary": {
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "min_coverage_ratio": 0.9,
                    "avg_coverage_ratio": 0.9,
                    "max_coverage_ratio": 0.9,
                    "lowest_batch_id": "BATCH_PRIMARY_SUMMARY",
                    "highest_batch_id": "BATCH_PRIMARY_SUMMARY",
                    "module_coverage_summary": {
                        "adp": {
                            "sample_count": 1,
                            "min_coverage_ratio": 0.45,
                            "avg_coverage_ratio": 0.45,
                            "max_coverage_ratio": 0.45,
                            "lowest_batch_id": "BATCH_ADP_SUMMARY",
                            "highest_batch_id": "BATCH_ADP_SUMMARY",
                        }
                    },
                },
            }
            summary_json.write_text(json.dumps(summary_payload) + "\n", encoding="utf-8")

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-module-hold-thresholds",
                "adp=0.5",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertIn(
                "phase4_primary_module_coverage=0.450 below hold_threshold=0.500",
                str(payload.get("phase4_primary_warning", "")),
            )
            self.assertIn("batch=BATCH_ADP_SUMMARY", str(payload.get("phase4_primary_warning", "")))
            self.assertEqual(
                payload.get("phase4_primary_warning_reasons"),
                ["phase4_primary_module_coverage_below_hold_threshold"],
            )
            self.assertEqual(
                payload.get("phase4_primary_module_hold_summary"),
                {
                    "adp": {
                        "violation_count": 1,
                        "threshold": 0.5,
                        "min_coverage_ratio": 0.45,
                        "min_batch_id": "BATCH_ADP_SUMMARY",
                    }
                },
            )

    def test_notification_warns_when_secondary_coverage_summary_below_threshold_without_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            summary_payload = {
                "release_prefix": "REL_PHASE4_NOTIFY_2026_0001",
                "sds_versions": ["sds_v1"],
                "summary_count": 1,
                "pipeline_manifest_count": 0,
                "final_result_counts": {"PASS": 1},
                "pipeline_overall_counts": {"PASS": 1},
                "pipeline_trend_counts": {"PASS": 1},
                "timing_ms": {"total": 100},
                "pipeline_manifests": [],
                "phase4_secondary_coverage_summary": {
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "min_coverage_ratio": 0.4,
                    "avg_coverage_ratio": 0.4,
                    "max_coverage_ratio": 0.4,
                    "lowest_batch_id": "BATCH_SECONDARY_SUMMARY",
                    "highest_batch_id": "BATCH_SECONDARY_SUMMARY",
                    "module_coverage_summary": {},
                },
            }
            summary_json.write_text(json.dumps(summary_payload) + "\n", encoding="utf-8")

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-warn-ratio",
                "0.5",
                "--phase4-secondary-warn-min-modules",
                "1",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertIn("phase4_secondary_coverage=0.400", str(payload.get("phase4_secondary_warning", "")))
            self.assertIn("batch=BATCH_SECONDARY_SUMMARY", str(payload.get("phase4_secondary_warning", "")))
            self.assertEqual(
                payload.get("phase4_secondary_warning_reasons"),
                ["phase4_secondary_coverage_below_threshold"],
            )
            self.assertEqual(len(payload.get("phase4_secondary_coverage_rows", [])), 1)

    def test_notification_warns_for_secondary_summary_by_min_modules_without_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            summary_payload = {
                "release_prefix": "REL_PHASE4_NOTIFY_2026_0001",
                "sds_versions": ["sds_v1"],
                "summary_count": 1,
                "pipeline_manifest_count": 0,
                "final_result_counts": {"PASS": 1},
                "pipeline_overall_counts": {"PASS": 1},
                "pipeline_trend_counts": {"PASS": 1},
                "timing_ms": {"total": 100},
                "pipeline_manifests": [],
                "phase4_secondary_coverage_summary": {
                    "evaluated_manifest_count": 3,
                    "pipeline_manifest_count": 3,
                    "min_coverage_ratio": 0.2,
                    "avg_coverage_ratio": 0.5,
                    "max_coverage_ratio": 0.9,
                    "lowest_batch_id": "BATCH_MIN1",
                    "highest_batch_id": "BATCH_HIGH",
                    "lowest_batch_secondary_module_count": 1,
                    "secondary_coverage_by_min_modules": {
                        "1": {
                            "evaluated_manifest_count": 3,
                            "min_coverage_ratio": 0.2,
                            "lowest_batch_id": "BATCH_MIN1",
                            "lowest_batch_secondary_module_count": 1,
                        },
                        "2": {
                            "evaluated_manifest_count": 2,
                            "min_coverage_ratio": 0.45,
                            "lowest_batch_id": "BATCH_MIN2",
                            "lowest_batch_secondary_module_count": 2,
                        },
                    },
                    "module_coverage_summary": {},
                },
            }
            summary_json.write_text(json.dumps(summary_payload) + "\n", encoding="utf-8")

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-warn-ratio",
                "0.5",
                "--phase4-secondary-warn-min-modules",
                "2",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertIn("phase4_secondary_coverage=0.450", str(payload.get("phase4_secondary_warning", "")))
            self.assertIn("batch=BATCH_MIN2", str(payload.get("phase4_secondary_warning", "")))
            self.assertEqual(
                payload.get("phase4_secondary_warning_reasons"),
                ["phase4_secondary_coverage_below_threshold"],
            )
            rows = payload.get("phase4_secondary_coverage_rows", [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(int(rows[0].get("secondary_module_count", 0)), 2)
            self.assertEqual(rows[0].get("batch_id"), "BATCH_MIN2")

    def test_notification_skips_secondary_summary_warn_when_min_modules_unmet_without_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            summary_payload = {
                "release_prefix": "REL_PHASE4_NOTIFY_2026_0001",
                "sds_versions": ["sds_v1"],
                "summary_count": 1,
                "pipeline_manifest_count": 0,
                "final_result_counts": {"PASS": 1},
                "pipeline_overall_counts": {"PASS": 1},
                "pipeline_trend_counts": {"PASS": 1},
                "timing_ms": {"total": 100},
                "pipeline_manifests": [],
                "phase4_secondary_coverage_summary": {
                    "evaluated_manifest_count": 2,
                    "pipeline_manifest_count": 2,
                    "min_coverage_ratio": 0.2,
                    "avg_coverage_ratio": 0.6,
                    "max_coverage_ratio": 1.0,
                    "lowest_batch_id": "BATCH_ONLY_MIN1",
                    "highest_batch_id": "BATCH_HIGH",
                    "lowest_batch_secondary_module_count": 1,
                    "secondary_coverage_by_min_modules": {
                        "1": {
                            "evaluated_manifest_count": 2,
                            "min_coverage_ratio": 0.2,
                            "lowest_batch_id": "BATCH_ONLY_MIN1",
                            "lowest_batch_secondary_module_count": 1,
                        }
                    },
                    "module_coverage_summary": {},
                },
            }
            summary_json.write_text(json.dumps(summary_payload) + "\n", encoding="utf-8")

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-warn-ratio",
                "0.5",
                "--phase4-secondary-warn-min-modules",
                "2",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "PASS")
            self.assertEqual(payload.get("phase4_secondary_warning"), "")
            self.assertEqual(payload.get("phase4_secondary_warning_reasons"), [])
            rows = payload.get("phase4_secondary_coverage_rows", [])
            self.assertEqual(len(rows), 1)
            self.assertEqual(int(rows[0].get("secondary_module_count", 0)), 1)
            self.assertEqual(rows[0].get("batch_id"), "BATCH_ONLY_MIN1")

    def test_notification_holds_when_secondary_module_summary_below_hold_threshold_without_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            summary_payload = {
                "release_prefix": "REL_PHASE4_NOTIFY_2026_0001",
                "sds_versions": ["sds_v1"],
                "summary_count": 1,
                "pipeline_manifest_count": 0,
                "final_result_counts": {"PASS": 1},
                "pipeline_overall_counts": {"PASS": 1},
                "pipeline_trend_counts": {"PASS": 1},
                "timing_ms": {"total": 100},
                "pipeline_manifests": [],
                "phase4_secondary_coverage_summary": {
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "min_coverage_ratio": 0.9,
                    "avg_coverage_ratio": 0.9,
                    "max_coverage_ratio": 0.9,
                    "lowest_batch_id": "BATCH_SECONDARY_SUMMARY",
                    "highest_batch_id": "BATCH_SECONDARY_SUMMARY",
                    "module_coverage_summary": {
                        "adp": {
                            "sample_count": 1,
                            "min_coverage_ratio": 0.45,
                            "avg_coverage_ratio": 0.45,
                            "max_coverage_ratio": 0.45,
                            "lowest_batch_id": "BATCH_ADP_SUMMARY",
                            "highest_batch_id": "BATCH_ADP_SUMMARY",
                        }
                    },
                },
            }
            summary_json.write_text(json.dumps(summary_payload) + "\n", encoding="utf-8")

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-module-hold-thresholds",
                "adp=0.5",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertIn(
                "phase4_secondary_module_coverage=0.450 below hold_threshold=0.500",
                str(payload.get("phase4_secondary_warning", "")),
            )
            self.assertIn("batch=BATCH_ADP_SUMMARY", str(payload.get("phase4_secondary_warning", "")))
            self.assertEqual(
                payload.get("phase4_secondary_warning_reasons"),
                ["phase4_secondary_module_coverage_below_hold_threshold"],
            )
            self.assertEqual(
                payload.get("phase4_secondary_module_hold_summary"),
                {
                    "adp": {
                        "violation_count": 1,
                        "threshold": 0.5,
                        "min_coverage_ratio": 0.45,
                        "min_batch_id": "BATCH_ADP_SUMMARY",
                    }
                },
            )

    def test_notification_holds_when_secondary_coverage_below_hold_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_HOLD",
                        "phase4_reference_secondary_total_coverage_ratio": 0.4,
                        "phase4_reference_secondary_module_count": 2,
                        "phase4_reference_secondary_module_coverage": {"adp": 0.4, "copilot": 0.7},
                    }
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-warn-ratio",
                "0.5",
                "--phase4-secondary-hold-ratio",
                "0.45",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(payload.get("phase4_secondary_hold_ratio"), 0.45)
            self.assertIn("below hold_threshold=0.450", str(payload.get("phase4_secondary_warning", "")))
            self.assertIn(
                "phase4_secondary_coverage_below_hold_threshold",
                payload.get("phase4_secondary_warning_reasons", []),
            )

    def test_notification_warns_when_secondary_module_coverage_below_module_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_ADP_LOW",
                        "phase4_reference_secondary_total_coverage_ratio": 0.8,
                        "phase4_reference_secondary_module_count": 2,
                        "phase4_reference_secondary_module_coverage": {"adp": 0.6, "copilot": 0.9},
                    },
                    {
                        "batch_id": "BATCH_COPILOT_LOW",
                        "phase4_reference_secondary_total_coverage_ratio": 0.85,
                        "phase4_reference_secondary_module_count": 2,
                        "phase4_reference_secondary_module_coverage": {"adp": 0.95, "copilot": 0.65},
                    },
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-module-warn-thresholds",
                "adp=0.8,copilot=0.7",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertEqual(
                payload.get("phase4_secondary_module_warn_thresholds"),
                {"adp": 0.8, "copilot": 0.7},
            )
            self.assertEqual(
                payload.get("phase4_secondary_warning_reasons"),
                ["phase4_secondary_module_coverage_below_threshold"],
            )
            warning_rows = payload.get("phase4_secondary_module_warning_rows", [])
            self.assertEqual(len(warning_rows), 2)
            self.assertEqual(
                payload.get("phase4_secondary_module_warning_summary"),
                {
                    "adp": {
                        "violation_count": 1,
                        "threshold": 0.8,
                        "min_coverage_ratio": 0.6,
                        "min_batch_id": "BATCH_ADP_LOW",
                    },
                    "copilot": {
                        "violation_count": 1,
                        "threshold": 0.7,
                        "min_coverage_ratio": 0.65,
                        "min_batch_id": "BATCH_COPILOT_LOW",
                    },
                },
            )
            self.assertIn("module=adp", str(payload.get("phase4_secondary_warning", "")))
            self.assertIn("phase4_secondary_module_coverage=", str(payload.get("phase4_secondary_warning", "")))
            self.assertIn(
                "phase4_secondary_module_warning_summary=adp:count=1,min_cov=0.600,threshold=0.800,batch=BATCH_ADP_LOW;",
                str(payload.get("message_text", "")),
            )

    def test_notification_holds_when_secondary_module_coverage_below_module_hold_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_ADP_LOW",
                        "phase4_reference_secondary_total_coverage_ratio": 0.8,
                        "phase4_reference_secondary_module_count": 2,
                        "phase4_reference_secondary_module_coverage": {"adp": 0.45, "copilot": 0.9},
                    }
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-module-hold-thresholds",
                "adp=0.5",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(payload.get("phase4_secondary_module_hold_thresholds"), {"adp": 0.5})
            self.assertEqual(
                payload.get("phase4_secondary_warning_reasons"),
                ["phase4_secondary_module_coverage_below_hold_threshold"],
            )
            hold_rows = payload.get("phase4_secondary_module_hold_rows", [])
            self.assertEqual(len(hold_rows), 1)
            self.assertEqual(
                payload.get("phase4_secondary_module_hold_summary"),
                {
                    "adp": {
                        "violation_count": 1,
                        "threshold": 0.5,
                        "min_coverage_ratio": 0.45,
                        "min_batch_id": "BATCH_ADP_LOW",
                    }
                },
            )
            self.assertIn("below hold_threshold=0.500", str(payload.get("phase4_secondary_warning", "")))
            self.assertIn(
                "phase4_secondary_module_hold_summary=adp:count=1,min_cov=0.450,threshold=0.500,batch=BATCH_ADP_LOW",
                str(payload.get("message_text", "")),
            )

    def test_notification_includes_phase3_vehicle_dynamics_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["longitudinal_force_balance_v1"],
                    "dynamic_enabled_manifest_count": 1,
                    "min_final_speed_mps": 6.5,
                    "avg_final_speed_mps": 6.5,
                    "max_final_speed_mps": 6.5,
                    "lowest_speed_batch_id": "BATCH_PHASE3_001",
                    "highest_speed_batch_id": "BATCH_PHASE3_001",
                    "min_final_position_m": 5.4,
                    "avg_final_position_m": 5.4,
                    "max_final_position_m": 5.4,
                    "lowest_position_batch_id": "BATCH_PHASE3_001",
                    "highest_position_batch_id": "BATCH_PHASE3_001",
                    "min_delta_speed_mps": 0.5,
                    "avg_delta_speed_mps": 0.5,
                    "max_delta_speed_mps": 0.5,
                    "lowest_delta_speed_batch_id": "BATCH_PHASE3_001",
                    "highest_delta_speed_batch_id": "BATCH_PHASE3_001",
                    "min_delta_position_m": 3.9,
                    "avg_delta_position_m": 3.9,
                    "max_delta_position_m": 3.9,
                    "lowest_delta_position_batch_id": "BATCH_PHASE3_001",
                    "highest_delta_position_batch_id": "BATCH_PHASE3_001",
                    "min_final_heading_deg": -2.5,
                    "avg_final_heading_deg": 1.2,
                    "max_final_heading_deg": 4.0,
                    "lowest_heading_batch_id": "BATCH_PHASE3_001",
                    "highest_heading_batch_id": "BATCH_PHASE3_001",
                    "min_final_lateral_position_m": -0.6,
                    "avg_final_lateral_position_m": 0.2,
                    "max_final_lateral_position_m": 0.9,
                    "lowest_lateral_position_batch_id": "BATCH_PHASE3_001",
                    "highest_lateral_position_batch_id": "BATCH_PHASE3_001",
                    "min_final_lateral_velocity_mps": -0.4,
                    "avg_final_lateral_velocity_mps": 0.1,
                    "max_final_lateral_velocity_mps": 0.5,
                    "lowest_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                    "highest_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                    "min_final_yaw_rate_rps": -0.3,
                    "avg_final_yaw_rate_rps": 0.05,
                    "max_final_yaw_rate_rps": 0.4,
                    "lowest_yaw_rate_batch_id": "BATCH_PHASE3_001",
                    "highest_yaw_rate_batch_id": "BATCH_PHASE3_001",
                    "min_delta_heading_deg": -1.8,
                    "avg_delta_heading_deg": 0.7,
                    "max_delta_heading_deg": 2.3,
                    "lowest_delta_heading_batch_id": "BATCH_PHASE3_001",
                    "highest_delta_heading_batch_id": "BATCH_PHASE3_001",
                    "min_delta_lateral_position_m": -0.5,
                    "avg_delta_lateral_position_m": 0.1,
                    "max_delta_lateral_position_m": 0.8,
                    "lowest_delta_lateral_position_batch_id": "BATCH_PHASE3_001",
                    "highest_delta_lateral_position_batch_id": "BATCH_PHASE3_001",
                    "min_delta_lateral_velocity_mps": -0.2,
                    "avg_delta_lateral_velocity_mps": 0.15,
                    "max_delta_lateral_velocity_mps": 0.5,
                    "lowest_delta_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                    "highest_delta_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                    "min_delta_yaw_rate_rps": -0.1,
                    "avg_delta_yaw_rate_rps": 0.2,
                    "max_delta_yaw_rate_rps": 0.45,
                    "lowest_delta_yaw_rate_batch_id": "BATCH_PHASE3_001",
                    "highest_delta_yaw_rate_batch_id": "BATCH_PHASE3_001",
                    "max_abs_yaw_rate_rps": 0.42,
                    "highest_abs_yaw_rate_batch_id": "BATCH_PHASE3_001",
                    "max_abs_lateral_velocity_mps": 0.55,
                    "highest_abs_lateral_velocity_batch_id": "BATCH_PHASE3_001",
                    "max_abs_accel_mps2": 2.3,
                    "highest_abs_accel_batch_id": "BATCH_PHASE3_001",
                    "max_abs_lateral_accel_mps2": 1.4,
                    "highest_abs_lateral_accel_batch_id": "BATCH_PHASE3_001",
                    "max_abs_yaw_accel_rps2": 0.8,
                    "highest_abs_yaw_accel_batch_id": "BATCH_PHASE3_001",
                    "max_abs_jerk_mps3": 3.1,
                    "highest_abs_jerk_batch_id": "BATCH_PHASE3_001",
                    "max_abs_lateral_jerk_mps3": 2.0,
                    "highest_abs_lateral_jerk_batch_id": "BATCH_PHASE3_001",
                    "max_abs_yaw_jerk_rps3": 1.1,
                    "highest_abs_yaw_jerk_batch_id": "BATCH_PHASE3_001",
                    "max_abs_lateral_position_m": 1.1,
                    "highest_abs_lateral_position_batch_id": "BATCH_PHASE3_001",
                    "min_road_grade_percent": -1.5,
                    "avg_road_grade_percent": 0.2,
                    "max_road_grade_percent": 2.1,
                    "lowest_road_grade_batch_id": "BATCH_PHASE3_001",
                    "highest_road_grade_batch_id": "BATCH_PHASE3_001",
                    "max_abs_grade_force_n": 148.0,
                    "highest_abs_grade_force_batch_id": "BATCH_PHASE3_001",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(
                payload.get("phase3_vehicle_dynamics_summary", {}).get("evaluated_manifest_count"),
                1,
            )
            summary_text = str(payload.get("phase3_vehicle_dynamics_summary_text", ""))
            self.assertIn("evaluated=1", summary_text)
            self.assertIn("dynamic_enabled=1", summary_text)
            self.assertIn("models=longitudinal_force_balance_v1", summary_text)
            self.assertIn("delta_speed:min=0.500(BATCH_PHASE3_001)", summary_text)
            self.assertIn("delta_position:min=3.900(BATCH_PHASE3_001)", summary_text)
            self.assertIn("heading:min=-2.500(BATCH_PHASE3_001)", summary_text)
            self.assertIn("lateral_position:min=-0.600(BATCH_PHASE3_001)", summary_text)
            self.assertIn("lateral_velocity:min=-0.400(BATCH_PHASE3_001)", summary_text)
            self.assertIn("yaw_rate_final:min=-0.300(BATCH_PHASE3_001)", summary_text)
            self.assertIn("delta_lateral_velocity:min=-0.200(BATCH_PHASE3_001)", summary_text)
            self.assertIn("delta_yaw_rate:min=-0.100(BATCH_PHASE3_001)", summary_text)
            self.assertIn("yaw_rate:max_abs=0.420(BATCH_PHASE3_001)", summary_text)
            self.assertIn("lateral_velocity:max_abs=0.550(BATCH_PHASE3_001)", summary_text)
            self.assertIn("accel:max_abs=2.300(BATCH_PHASE3_001)", summary_text)
            self.assertIn("lateral_accel:max_abs=1.400(BATCH_PHASE3_001)", summary_text)
            self.assertIn("yaw_accel:max_abs=0.800(BATCH_PHASE3_001)", summary_text)
            self.assertIn("jerk:max_abs=3.100(BATCH_PHASE3_001)", summary_text)
            self.assertIn("lateral_jerk:max_abs=2.000(BATCH_PHASE3_001)", summary_text)
            self.assertIn("yaw_jerk:max_abs=1.100(BATCH_PHASE3_001)", summary_text)
            self.assertIn("lateral_abs:max=1.100(BATCH_PHASE3_001)", summary_text)
            self.assertIn("road_grade:min=-1.500(BATCH_PHASE3_001)", summary_text)
            self.assertIn("grade_force:max_abs=148.000(BATCH_PHASE3_001)", summary_text)
            self.assertIn("phase3_vehicle_dynamics_summary=evaluated=1", str(payload.get("message_text", "")))
            blocks = payload.get("slack", {}).get("blocks", [])
            self.assertTrue(
                any("phase3 vehicle dynamics" in str(block) for block in blocks),
                msg=f"Expected phase3 vehicle dynamics block, got blocks={blocks}",
            )

    def test_notification_warns_when_phase3_vehicle_final_speed_exceeds_warn_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_001",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_final_speed_mps": 7.2,
                        "phase3_vehicle_dynamics_final_position_m": 5.0,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["longitudinal_force_balance_v1"],
                    "max_final_speed_mps": 7.2,
                    "highest_speed_batch_id": "BATCH_PHASE3_001",
                    "max_final_position_m": 5.0,
                    "highest_position_batch_id": "BATCH_PHASE3_001",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-final-speed-warn-max",
                "7.0",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertEqual(float(payload.get("phase3_vehicle_final_speed_warn_max", 0.0)), 7.0)
            self.assertIn(
                "phase3_vehicle_final_speed_above_warn_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_final_speed_mps=7.200 exceeded warn_max=7.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            violation_rows = payload.get("phase3_vehicle_dynamics_violation_rows", [])
            self.assertEqual(len(violation_rows), 1)
            self.assertEqual(violation_rows[0].get("severity"), "WARN")
            self.assertEqual(violation_rows[0].get("metric"), "final_speed_mps")
            self.assertEqual(violation_rows[0].get("batch_id"), "BATCH_PHASE3_001")
            self.assertIn(
                "phase3_vehicle_dynamics_violation_rows=WARN:final_speed_mps=7.200>7.000(BATCH_PHASE3_001)",
                str(payload.get("message_text", "")),
            )

    def test_notification_holds_when_phase3_vehicle_final_position_exceeds_hold_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_001",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_final_speed_mps": 6.0,
                        "phase3_vehicle_dynamics_final_position_m": 12.5,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["longitudinal_force_balance_v1"],
                    "max_final_speed_mps": 6.0,
                    "highest_speed_batch_id": "BATCH_PHASE3_001",
                    "max_final_position_m": 12.5,
                    "highest_position_batch_id": "BATCH_PHASE3_001",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-final-position-hold-max",
                "12.0",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(float(payload.get("phase3_vehicle_final_position_hold_max", 0.0)), 12.0)
            self.assertIn(
                "phase3_vehicle_final_position_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_final_position_m=12.500 exceeded hold_max=12.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            violation_rows = payload.get("phase3_vehicle_dynamics_violation_rows", [])
            self.assertEqual(len(violation_rows), 1)
            self.assertEqual(violation_rows[0].get("severity"), "HOLD")
            self.assertEqual(violation_rows[0].get("metric"), "final_position_m")
            self.assertEqual(violation_rows[0].get("batch_id"), "BATCH_PHASE3_001")

    def test_notification_warns_when_phase3_vehicle_delta_speed_exceeds_warn_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_001",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_initial_speed_mps": 4.0,
                        "phase3_vehicle_dynamics_final_speed_mps": 5.5,
                        "phase3_vehicle_dynamics_initial_position_m": 0.0,
                        "phase3_vehicle_dynamics_final_position_m": 1.0,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["longitudinal_force_balance_v1"],
                    "max_final_speed_mps": 5.5,
                    "highest_speed_batch_id": "BATCH_PHASE3_001",
                    "max_final_position_m": 1.0,
                    "highest_position_batch_id": "BATCH_PHASE3_001",
                    "max_delta_speed_mps": 1.5,
                    "highest_delta_speed_batch_id": "BATCH_PHASE3_001",
                    "max_delta_position_m": 1.0,
                    "highest_delta_position_batch_id": "BATCH_PHASE3_001",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-delta-speed-warn-max",
                "1.4",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertEqual(float(payload.get("phase3_vehicle_delta_speed_warn_max", 0.0)), 1.4)
            self.assertIn(
                "phase3_vehicle_delta_speed_above_warn_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_delta_speed_mps=1.500 exceeded warn_max=1.400",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "delta_speed_mps"
                    and row.get("severity") == "WARN"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_holds_when_phase3_vehicle_delta_position_exceeds_hold_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_001",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_initial_speed_mps": 1.0,
                        "phase3_vehicle_dynamics_final_speed_mps": 1.2,
                        "phase3_vehicle_dynamics_initial_position_m": 5.0,
                        "phase3_vehicle_dynamics_final_position_m": 7.6,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["longitudinal_force_balance_v1"],
                    "max_final_speed_mps": 1.2,
                    "highest_speed_batch_id": "BATCH_PHASE3_001",
                    "max_final_position_m": 7.6,
                    "highest_position_batch_id": "BATCH_PHASE3_001",
                    "max_delta_speed_mps": 0.2,
                    "highest_delta_speed_batch_id": "BATCH_PHASE3_001",
                    "max_delta_position_m": 2.6,
                    "highest_delta_position_batch_id": "BATCH_PHASE3_001",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-delta-position-hold-max",
                "2.5",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(float(payload.get("phase3_vehicle_delta_position_hold_max", 0.0)), 2.5)
            self.assertIn(
                "phase3_vehicle_delta_position_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_delta_position_m=2.600 exceeded hold_max=2.500",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "delta_position_m"
                    and row.get("severity") == "HOLD"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_warns_when_phase3_vehicle_road_grade_abs_exceeds_warn_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_GRADE_WARN",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_min_road_grade_percent": -2.5,
                        "phase3_vehicle_dynamics_max_road_grade_percent": 1.5,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["longitudinal_force_balance_v1"],
                    "min_road_grade_percent": -2.5,
                    "avg_road_grade_percent": -0.2,
                    "max_road_grade_percent": 1.5,
                    "lowest_road_grade_batch_id": "BATCH_PHASE3_GRADE_WARN",
                    "highest_road_grade_batch_id": "BATCH_PHASE3_GRADE_WARN",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-road-grade-abs-warn-max",
                "2.0",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertEqual(float(payload.get("phase3_vehicle_road_grade_abs_warn_max", 0.0)), 2.0)
            self.assertIn(
                "phase3_vehicle_road_grade_abs_above_warn_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_road_grade_abs_percent=2.500 exceeded warn_max=2.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "road_grade_abs_percent"
                    and row.get("severity") == "WARN"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_holds_when_phase3_vehicle_grade_force_exceeds_hold_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_GRADE_FORCE_HOLD",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_max_abs_grade_force_n": 150.0,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["longitudinal_force_balance_v1"],
                    "max_abs_grade_force_n": 150.0,
                    "highest_abs_grade_force_batch_id": "BATCH_PHASE3_GRADE_FORCE_HOLD",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-grade-force-hold-max",
                "120",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(float(payload.get("phase3_vehicle_grade_force_hold_max", 0.0)), 120.0)
            self.assertIn(
                "phase3_vehicle_grade_force_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_grade_force_n=150.000 exceeded hold_max=120.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "grade_force_n"
                    and row.get("severity") == "HOLD"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_holds_when_phase3_vehicle_yaw_rate_abs_exceeds_hold_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_YAW_HOLD",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_max_abs_yaw_rate_rps": 0.78,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["planar_bicycle_force_balance_v1"],
                    "max_abs_yaw_rate_rps": 0.78,
                    "highest_abs_yaw_rate_batch_id": "BATCH_PHASE3_YAW_HOLD",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-yaw-rate-abs-hold-max",
                "0.70",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(float(payload.get("phase3_vehicle_yaw_rate_abs_hold_max", 0.0)), 0.7)
            self.assertIn(
                "phase3_vehicle_yaw_rate_abs_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_yaw_rate_abs_rps=0.780 exceeded hold_max=0.700",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "yaw_rate_abs_rps"
                    and row.get("severity") == "HOLD"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_holds_when_phase3_vehicle_lateral_velocity_abs_exceeds_hold_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_LV_HOLD",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_max_abs_lateral_velocity_mps": 0.66,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["planar_dynamic_bicycle_force_balance_v1"],
                    "max_abs_lateral_velocity_mps": 0.66,
                    "highest_abs_lateral_velocity_batch_id": "BATCH_PHASE3_LV_HOLD",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-lateral-velocity-abs-hold-max",
                "0.60",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(float(payload.get("phase3_vehicle_lateral_velocity_abs_hold_max", 0.0)), 0.6)
            self.assertIn(
                "phase3_vehicle_lateral_velocity_abs_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_lateral_velocity_abs_mps=0.660 exceeded hold_max=0.600",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "lateral_velocity_abs_mps"
                    and row.get("severity") == "HOLD"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_holds_when_phase3_vehicle_accel_abs_exceeds_hold_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_ACCEL_HOLD",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_max_abs_accel_mps2": 2.35,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["planar_dynamic_bicycle_force_balance_v1"],
                    "max_abs_accel_mps2": 2.35,
                    "highest_abs_accel_batch_id": "BATCH_PHASE3_ACCEL_HOLD",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-accel-abs-hold-max",
                "2.00",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(float(payload.get("phase3_vehicle_accel_abs_hold_max", 0.0)), 2.0)
            self.assertIn(
                "phase3_vehicle_accel_abs_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_accel_abs_mps2=2.350 exceeded hold_max=2.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "accel_abs_mps2" and row.get("severity") == "HOLD"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_holds_when_phase3_vehicle_jerk_abs_exceeds_hold_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_JERK_HOLD",
                        "phase3_vehicle_dynamics_step_count": 4,
                        "phase3_vehicle_dynamics_max_abs_jerk_mps3": 5.25,
                    }
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 1,
                    "pipeline_manifest_count": 1,
                    "models": ["planar_dynamic_bicycle_force_balance_v1"],
                    "max_abs_jerk_mps3": 5.25,
                    "highest_abs_jerk_batch_id": "BATCH_PHASE3_JERK_HOLD",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-jerk-abs-hold-max",
                "5.00",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertEqual(float(payload.get("phase3_vehicle_jerk_abs_hold_max", 0.0)), 5.0)
            self.assertIn(
                "phase3_vehicle_jerk_abs_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertIn(
                "phase3_vehicle_jerk_abs_mps3=5.250 exceeded hold_max=5.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertTrue(
                any(
                    row.get("metric") == "jerk_abs_mps3" and row.get("severity") == "HOLD"
                    for row in payload.get("phase3_vehicle_dynamics_violation_rows", [])
                )
            )

    def test_notification_warns_from_phase3_rows_when_summary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_001",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_final_speed_mps": 7.2,
                        "phase3_vehicle_dynamics_final_position_m": 2.0,
                    }
                ],
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-final-speed-warn-max",
                "7.0",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "WARN")
            self.assertIn(
                "phase3_vehicle_final_speed_mps=7.200 exceeded warn_max=7.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertIn(
                "phase3_vehicle_final_speed_above_warn_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertEqual(str(payload.get("phase3_vehicle_dynamics_summary_text", "")), "n/a")
            self.assertEqual(len(payload.get("phase3_vehicle_dynamics_violation_rows", [])), 1)

    def test_notification_holds_from_phase3_delta_rows_when_summary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_001",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_initial_speed_mps": 1.0,
                        "phase3_vehicle_dynamics_final_speed_mps": 1.3,
                        "phase3_vehicle_dynamics_initial_position_m": 2.0,
                        "phase3_vehicle_dynamics_final_position_m": 5.4,
                    }
                ],
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-delta-position-hold-max",
                "3.0",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            self.assertIn(
                "phase3_vehicle_delta_position_m=3.400 exceeded hold_max=3.000",
                str(payload.get("phase3_vehicle_dynamics_warning", "")),
            )
            self.assertIn(
                "phase3_vehicle_delta_position_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertEqual(str(payload.get("phase3_vehicle_dynamics_summary_text", "")), "n/a")
            self.assertEqual(len(payload.get("phase3_vehicle_dynamics_violation_rows", [])), 1)

    def test_notification_fallback_prefers_hold_over_warn_for_same_phase3_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_001",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_final_speed_mps": 7.2,
                        "phase3_vehicle_dynamics_final_position_m": 2.0,
                    },
                    {
                        "batch_id": "BATCH_PHASE3_002",
                        "phase3_vehicle_dynamics_step_count": 3,
                        "phase3_vehicle_dynamics_final_speed_mps": 10.5,
                        "phase3_vehicle_dynamics_final_position_m": 2.1,
                    },
                ],
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-final-speed-warn-max",
                "7.0",
                "--phase3-vehicle-final-speed-hold-max",
                "10.0",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            warning = str(payload.get("phase3_vehicle_dynamics_warning", ""))
            self.assertIn(
                "phase3_vehicle_final_speed_mps=10.500 exceeded hold_max=10.000 (batch=BATCH_PHASE3_002)",
                warning,
            )
            self.assertNotIn(
                "phase3_vehicle_final_speed_mps=7.200 exceeded warn_max=7.000 (batch=BATCH_PHASE3_001)",
                warning,
            )
            warning_messages = payload.get("phase3_vehicle_dynamics_warning_messages", [])
            self.assertEqual(len(warning_messages), 1)
            self.assertIn(
                "phase3_vehicle_final_speed_above_hold_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertNotIn(
                "phase3_vehicle_final_speed_above_warn_max",
                payload.get("phase3_vehicle_dynamics_warning_reasons", []),
            )
            self.assertEqual(len(payload.get("phase3_vehicle_dynamics_violation_rows", [])), 2)

    def test_notification_collects_phase3_vehicle_violation_rows_across_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_PHASE3_HOLD",
                        "phase3_vehicle_dynamics_step_count": 4,
                        "phase3_vehicle_dynamics_final_speed_mps": 8.1,
                        "phase3_vehicle_dynamics_final_position_m": 13.0,
                    },
                    {
                        "batch_id": "BATCH_PHASE3_WARN",
                        "phase3_vehicle_dynamics_step_count": 5,
                        "phase3_vehicle_dynamics_final_speed_mps": 7.2,
                        "phase3_vehicle_dynamics_final_position_m": 11.0,
                    },
                ],
                phase3_vehicle_dynamics_summary={
                    "evaluated_manifest_count": 2,
                    "pipeline_manifest_count": 2,
                    "models": ["longitudinal_force_balance_v1"],
                    "max_final_speed_mps": 8.1,
                    "highest_speed_batch_id": "BATCH_PHASE3_HOLD",
                    "max_final_position_m": 13.0,
                    "highest_position_batch_id": "BATCH_PHASE3_HOLD",
                },
            )
            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase3-vehicle-final-speed-warn-max",
                "7.0",
                "--phase3-vehicle-final-speed-hold-max",
                "8.0",
                "--phase3-vehicle-final-position-warn-max",
                "10.0",
                "--phase3-vehicle-final-position-hold-max",
                "12.0",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "HOLD")
            violation_rows = payload.get("phase3_vehicle_dynamics_violation_rows", [])
            self.assertEqual(len(violation_rows), 4)
            self.assertTrue(
                any(
                    row.get("batch_id") == "BATCH_PHASE3_WARN"
                    and row.get("metric") == "final_speed_mps"
                    and row.get("severity") == "WARN"
                    for row in violation_rows
                )
            )
            self.assertTrue(
                any(
                    row.get("batch_id") == "BATCH_PHASE3_HOLD"
                    and row.get("metric") == "final_position_m"
                    and row.get("severity") == "HOLD"
                    for row in violation_rows
                )
            )
            summary = payload.get("phase3_vehicle_dynamics_violation_summary", {})
            self.assertEqual(
                sorted(summary.keys()),
                [
                    "HOLD:final_position_m",
                    "HOLD:final_speed_mps",
                    "WARN:final_position_m",
                    "WARN:final_speed_mps",
                ],
            )
            hold_position = summary.get("HOLD:final_position_m", {})
            self.assertEqual(int(hold_position.get("violation_count", 0)), 1)
            self.assertEqual(str(hold_position.get("max_batch_id", "")), "BATCH_PHASE3_HOLD")
            self.assertAlmostEqual(float(hold_position.get("threshold", 0.0)), 12.0)
            self.assertAlmostEqual(float(hold_position.get("max_value", 0.0)), 13.0)
            self.assertAlmostEqual(float(hold_position.get("max_exceedance", 0.0)), 1.0)
            warn_speed = summary.get("WARN:final_speed_mps", {})
            self.assertEqual(int(warn_speed.get("violation_count", 0)), 1)
            self.assertEqual(str(warn_speed.get("max_batch_id", "")), "BATCH_PHASE3_WARN")
            self.assertAlmostEqual(float(warn_speed.get("threshold", 0.0)), 7.0)
            self.assertAlmostEqual(float(warn_speed.get("max_value", 0.0)), 7.2)
            self.assertAlmostEqual(float(warn_speed.get("max_exceedance", 0.0)), 0.2)
            self.assertIn(
                "phase3_vehicle_dynamics_violation_rows=",
                str(payload.get("message_text", "")),
            )
            self.assertIn(
                "phase3_vehicle_dynamics_violation_summary=",
                str(payload.get("message_text", "")),
            )
            blocks = payload.get("slack", {}).get("blocks", [])
            self.assertTrue(
                any("violations:" in str(block) for block in blocks),
                msg=f"Expected phase3 violation block details, got blocks={blocks}",
            )
            self.assertTrue(
                any("violation_summary:" in str(block) for block in blocks),
                msg=f"Expected phase3 violation summary block details, got blocks={blocks}",
            )

    def test_notification_keeps_pass_when_no_eligible_secondary_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(
                summary_json,
                manifests=[
                    {
                        "batch_id": "BATCH_SMALL",
                        "phase4_reference_secondary_total_coverage_ratio": 0.1,
                        "phase4_reference_secondary_module_count": 1,
                    }
                ],
            )

            run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-warn-ratio",
                "0.5",
                "--phase4-secondary-warn-min-modules",
                "2",
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("status"), "PASS")
            self.assertEqual(payload.get("phase4_secondary_warning"), "")
            self.assertEqual(payload.get("phase4_secondary_warning_reasons"), [])
            self.assertEqual(len(payload.get("phase4_secondary_coverage_rows", [])), 1)

    def test_notification_rejects_invalid_module_warn_threshold_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(summary_json, manifests=[])

            proc = run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-secondary-module-warn-thresholds",
                "unknown=0.5",
                expected_rc=1,
            )
            self.assertIn("[error] build_release_notification_payload.py:", proc.stderr)
            self.assertIn(
                "phase4-secondary-module-warn-thresholds module must be one of:",
                proc.stderr,
            )

    def test_notification_rejects_invalid_primary_module_warn_threshold_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(summary_json, manifests=[])

            proc = run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-module-warn-thresholds",
                "unknown=0.5",
                expected_rc=1,
            )
            self.assertIn("[error] build_release_notification_payload.py:", proc.stderr)
            self.assertIn(
                "phase4-primary-module-warn-thresholds module must be one of:",
                proc.stderr,
            )

    def test_notification_rejects_invalid_primary_hold_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            out_json = tmp_path / "notification.json"
            self._write_summary_json(summary_json, manifests=[])

            proc = run_script(
                PROTOTYPE_DIR / "build_release_notification_payload.py",
                "--summary-json",
                str(summary_json),
                "--out-json",
                str(out_json),
                "--workflow-name",
                "wf",
                "--phase4-primary-hold-ratio",
                "1.1",
                expected_rc=1,
            )
            self.assertIn("[error] build_release_notification_payload.py:", proc.stderr)
            self.assertIn("phase4-primary-hold-ratio must be between 0 and 1", proc.stderr)

    def test_run_ci_summary_dry_run_forwards_phase4_secondary_notify_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase4-primary-warn-ratio",
                "0.62",
                "--notify-phase4-primary-hold-ratio",
                "0.48",
                "--notify-phase4-primary-module-warn-thresholds",
                "adp=0.8,copilot=0.7",
                "--notify-phase4-primary-module-hold-thresholds",
                "adp=0.5",
                "--notify-phase4-secondary-warn-ratio",
                "0.5",
                "--notify-phase4-secondary-hold-ratio",
                "0.45",
                "--notify-phase4-secondary-warn-min-modules",
                "3",
                "--notify-phase4-secondary-module-warn-thresholds",
                "adp=0.8,copilot=0.7",
                "--notify-phase4-secondary-module-hold-thresholds",
                "adp=0.5",
                "--notify-phase3-vehicle-final-speed-warn-max",
                "7.1",
                "--notify-phase3-vehicle-final-speed-hold-max",
                "7.9",
                "--notify-phase3-vehicle-final-position-warn-max",
                "11.2",
                "--notify-phase3-vehicle-final-position-hold-max",
                "12.4",
                "--notify-phase3-vehicle-delta-speed-warn-max",
                "1.3",
                "--notify-phase3-vehicle-delta-speed-hold-max",
                "1.9",
                "--notify-phase3-vehicle-delta-position-warn-max",
                "2.1",
                "--notify-phase3-vehicle-delta-position-hold-max",
                "2.7",
                "--notify-phase3-vehicle-final-heading-abs-warn-max",
                "4.0",
                "--notify-phase3-vehicle-final-heading-abs-hold-max",
                "5.5",
                "--notify-phase3-vehicle-final-lateral-position-abs-warn-max",
                "0.8",
                "--notify-phase3-vehicle-final-lateral-position-abs-hold-max",
                "1.2",
                "--notify-phase3-vehicle-delta-heading-abs-warn-max",
                "2.1",
                "--notify-phase3-vehicle-delta-heading-abs-hold-max",
                "3.0",
                "--notify-phase3-vehicle-delta-lateral-position-abs-warn-max",
                "0.6",
                "--notify-phase3-vehicle-delta-lateral-position-abs-hold-max",
                "1.0",
                "--notify-phase3-vehicle-yaw-rate-abs-warn-max",
                "0.4",
                "--notify-phase3-vehicle-yaw-rate-abs-hold-max",
                "0.7",
                "--notify-phase3-vehicle-lateral-position-abs-warn-max",
                "1.0",
                "--notify-phase3-vehicle-lateral-position-abs-hold-max",
                "1.5",
                "--notify-phase3-vehicle-road-grade-abs-warn-max",
                "2.2",
                "--notify-phase3-vehicle-road-grade-abs-hold-max",
                "3.0",
                "--notify-phase3-vehicle-grade-force-warn-max",
                "120",
                "--notify-phase3-vehicle-grade-force-hold-max",
                "150",
                "--dry-run",
            )
            self.assertIn("--phase4-primary-warn-ratio 0.62", proc.stdout)
            self.assertIn("--phase4-primary-hold-ratio 0.48", proc.stdout)
            self.assertIn("--phase4-primary-module-warn-thresholds adp=0.8,copilot=0.7", proc.stdout)
            self.assertIn("--phase4-primary-module-hold-thresholds adp=0.5", proc.stdout)
            self.assertIn("--phase4-secondary-warn-ratio 0.5", proc.stdout)
            self.assertIn("--phase4-secondary-hold-ratio 0.45", proc.stdout)
            self.assertIn("--phase4-secondary-warn-min-modules 3", proc.stdout)
            self.assertIn("--phase4-secondary-module-warn-thresholds adp=0.8,copilot=0.7", proc.stdout)
            self.assertIn("--phase4-secondary-module-hold-thresholds adp=0.5", proc.stdout)
            self.assertIn("--phase3-vehicle-final-speed-warn-max 7.1", proc.stdout)
            self.assertIn("--phase3-vehicle-final-speed-hold-max 7.9", proc.stdout)
            self.assertIn("--phase3-vehicle-final-position-warn-max 11.2", proc.stdout)
            self.assertIn("--phase3-vehicle-final-position-hold-max 12.4", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-speed-warn-max 1.3", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-speed-hold-max 1.9", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-position-warn-max 2.1", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-position-hold-max 2.7", proc.stdout)
            self.assertIn("--phase3-vehicle-final-heading-abs-warn-max 4.0", proc.stdout)
            self.assertIn("--phase3-vehicle-final-heading-abs-hold-max 5.5", proc.stdout)
            self.assertIn("--phase3-vehicle-final-lateral-position-abs-warn-max 0.8", proc.stdout)
            self.assertIn("--phase3-vehicle-final-lateral-position-abs-hold-max 1.2", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-heading-abs-warn-max 2.1", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-heading-abs-hold-max 3.0", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-lateral-position-abs-warn-max 0.6", proc.stdout)
            self.assertIn("--phase3-vehicle-delta-lateral-position-abs-hold-max 1.0", proc.stdout)
            self.assertIn("--phase3-vehicle-yaw-rate-abs-warn-max 0.4", proc.stdout)
            self.assertIn("--phase3-vehicle-yaw-rate-abs-hold-max 0.7", proc.stdout)
            self.assertIn("--phase3-vehicle-lateral-position-abs-warn-max 1.0", proc.stdout)
            self.assertIn("--phase3-vehicle-lateral-position-abs-hold-max 1.5", proc.stdout)
            self.assertIn("--phase3-vehicle-road-grade-abs-warn-max 2.2", proc.stdout)
            self.assertIn("--phase3-vehicle-road-grade-abs-hold-max 3.0", proc.stdout)
            self.assertIn("--phase3-vehicle-grade-force-warn-max 120.0", proc.stdout)
            self.assertIn("--phase3-vehicle-grade-force-hold-max 150.0", proc.stdout)

    def test_run_ci_summary_rejects_invalid_secondary_hold_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase4-secondary-hold-ratio",
                "1.2",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase4-secondary-hold-ratio must be <= 1", proc.stderr)

    def test_run_ci_summary_rejects_invalid_primary_hold_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase4-primary-hold-ratio",
                "1.3",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase4-primary-hold-ratio must be <= 1", proc.stderr)

    def test_run_ci_summary_rejects_invalid_module_warn_threshold_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase4-secondary-module-warn-thresholds",
                "unknown=0.5",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn(
                "notify-phase4-secondary-module-warn-thresholds module must be one of:",
                proc.stderr,
            )

    def test_run_ci_summary_rejects_invalid_primary_module_warn_threshold_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase4-primary-module-warn-thresholds",
                "unknown=0.5",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn(
                "notify-phase4-primary-module-warn-thresholds module must be one of:",
                proc.stderr,
            )

    def test_run_ci_summary_rejects_invalid_module_hold_threshold_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase4-secondary-module-hold-thresholds",
                "unknown=0.5",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn(
                "notify-phase4-secondary-module-hold-thresholds module must be one of:",
                proc.stderr,
            )

    def test_run_ci_summary_rejects_invalid_primary_module_hold_threshold_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase4-primary-module-hold-thresholds",
                "unknown=0.5",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn(
                "notify-phase4-primary-module-hold-thresholds module must be one of:",
                proc.stderr,
            )

    def test_run_ci_summary_rejects_invalid_phase3_vehicle_warn_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase3-vehicle-final-speed-warn-max",
                "-1",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase3-vehicle-final-speed-warn-max must be >= 0", proc.stderr)

    def test_run_ci_summary_rejects_invalid_phase3_vehicle_delta_warn_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase3-vehicle-delta-speed-warn-max",
                "-1",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase3-vehicle-delta-speed-warn-max must be >= 0", proc.stderr)

    def test_run_ci_summary_rejects_invalid_phase3_vehicle_road_grade_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase3-vehicle-road-grade-abs-warn-max",
                "-1",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase3-vehicle-road-grade-abs-warn-max must be >= 0", proc.stderr)

    def test_run_ci_summary_rejects_invalid_phase3_vehicle_yaw_rate_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase3-vehicle-yaw-rate-abs-warn-max",
                "-1",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase3-vehicle-yaw-rate-abs-warn-max must be >= 0", proc.stderr)

    def test_run_ci_summary_rejects_invalid_phase3_vehicle_accel_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase3-vehicle-accel-abs-warn-max",
                "-1",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase3-vehicle-accel-abs-warn-max must be >= 0", proc.stderr)

    def test_run_ci_summary_rejects_invalid_phase3_vehicle_jerk_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase3-vehicle-jerk-abs-warn-max",
                "-1",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase3-vehicle-jerk-abs-warn-max must be >= 0", proc.stderr)

    def test_run_ci_summary_rejects_invalid_phase3_vehicle_delta_yaw_rate_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            proc = run_script(
                PROTOTYPE_DIR / "run_ci_summary.py",
                "--artifacts-root",
                str(tmp_path / "artifacts"),
                "--release-prefix",
                "REL_PHASE4_NOTIFY_2026_0001",
                "--out-text",
                str(tmp_path / "summary.txt"),
                "--out-json",
                str(tmp_path / "summary.json"),
                "--out-db",
                str(tmp_path / "summary.db"),
                "--summary-title",
                "CI Summary",
                "--workflow-name",
                "wf",
                "--notification-out-json",
                str(tmp_path / "notification.json"),
                "--notify-phase3-vehicle-delta-yaw-rate-abs-warn-max",
                "-1",
                "--dry-run",
                expected_rc=1,
            )
            self.assertIn("[error] run_ci_summary.py:", proc.stderr)
            self.assertIn("notify-phase3-vehicle-delta-yaw-rate-abs-warn-max must be >= 0", proc.stderr)


if __name__ == "__main__":
    unittest.main()
