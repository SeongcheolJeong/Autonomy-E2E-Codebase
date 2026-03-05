from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from build_release_summary_artifact import summarize_phase3_dataset_traffic


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


class Phase3DatasetTrafficAggregateTests(unittest.TestCase):
    def test_phase3_dataset_traffic_summary_aggregates_manifests(self) -> None:
        summary = summarize_phase3_dataset_traffic(
            [
                {
                    "batch_id": "BATCH_TRAFFIC_002",
                    "phase3_dataset_traffic_gate_result": "hold",
                    "phase3_dataset_traffic_gate_reason_count": 2,
                    "phase3_dataset_traffic_gate_min_run_summary_count": 3,
                    "phase3_dataset_traffic_gate_min_traffic_profile_count": 2,
                    "phase3_dataset_traffic_gate_min_actor_pattern_count": 1,
                    "phase3_dataset_traffic_gate_min_avg_npc_count": 2.5,
                    "phase3_dataset_traffic_run_summary_count": 3,
                    "phase3_dataset_traffic_run_status_counts": {"pass": 2, "hold": 1},
                    "phase3_dataset_traffic_profile_count": 2,
                    "phase3_dataset_traffic_profile_ids": ["dense_city", "night_merge"],
                    "phase3_dataset_traffic_profile_source_count": 2,
                    "phase3_dataset_traffic_profile_source_ids": ["sumo_stub_builtin_v0", "sumo_custom_v1"],
                    "phase3_dataset_traffic_actor_pattern_count": 1,
                    "phase3_dataset_traffic_actor_pattern_ids": ["baseline"],
                    "phase3_dataset_traffic_lane_profile_signature_count": 2,
                    "phase3_dataset_traffic_lane_profile_signatures": ["0,1,-1", "0,1"],
                    "phase3_dataset_traffic_npc_count_sample_count": 3,
                    "phase3_dataset_traffic_npc_count_min": 5,
                    "phase3_dataset_traffic_npc_count_avg": 7.5,
                    "phase3_dataset_traffic_npc_count_max": 10,
                    "phase3_dataset_traffic_npc_initial_gap_m_sample_count": 3,
                    "phase3_dataset_traffic_npc_initial_gap_m_avg": 30.0,
                    "phase3_dataset_traffic_npc_gap_step_m_sample_count": 3,
                    "phase3_dataset_traffic_npc_gap_step_m_avg": 12.0,
                    "phase3_dataset_traffic_npc_speed_scale_sample_count": 3,
                    "phase3_dataset_traffic_npc_speed_scale_avg": 1.1,
                    "phase3_dataset_traffic_npc_speed_jitter_mps_sample_count": 3,
                    "phase3_dataset_traffic_npc_speed_jitter_mps_avg": 0.3,
                    "phase3_dataset_traffic_lane_index_unique_count": 2,
                    "phase3_dataset_traffic_lane_indices": [1, 2],
                    "phase3_dataset_manifest_counts_rows": 3,
                    "phase3_dataset_manifest_run_summary_count": 3,
                    "phase3_dataset_manifest_release_summary_count": 1,
                    "phase3_dataset_manifest_versions": ["sds_v1"],
                },
                {
                    "batch_id": "BATCH_TRAFFIC_001",
                    "phase3_dataset_traffic_gate_result": "pass",
                    "phase3_dataset_traffic_gate_reason_count": 0,
                    "phase3_dataset_traffic_gate_min_run_summary_count": 2,
                    "phase3_dataset_traffic_gate_min_traffic_profile_count": 1,
                    "phase3_dataset_traffic_gate_min_actor_pattern_count": 1,
                    "phase3_dataset_traffic_gate_min_avg_npc_count": 2.0,
                    "phase3_dataset_traffic_run_summary_count": 2,
                    "phase3_dataset_traffic_run_status_counts": {"pass": 2},
                    "phase3_dataset_traffic_profile_count": 1,
                    "phase3_dataset_traffic_profile_ids": ["dense_city"],
                    "phase3_dataset_traffic_profile_source_count": 1,
                    "phase3_dataset_traffic_profile_source_ids": ["sumo_stub_builtin_v0"],
                    "phase3_dataset_traffic_actor_pattern_count": 2,
                    "phase3_dataset_traffic_actor_pattern_ids": ["baseline", "aggressive"],
                    "phase3_dataset_traffic_lane_profile_signature_count": 1,
                    "phase3_dataset_traffic_lane_profile_signatures": ["0,1"],
                    "phase3_dataset_traffic_npc_count_sample_count": 2,
                    "phase3_dataset_traffic_npc_count_min": 3,
                    "phase3_dataset_traffic_npc_count_avg": 5.0,
                    "phase3_dataset_traffic_npc_count_max": 8,
                    "phase3_dataset_traffic_npc_initial_gap_m_sample_count": 2,
                    "phase3_dataset_traffic_npc_initial_gap_m_avg": 42.0,
                    "phase3_dataset_traffic_npc_gap_step_m_sample_count": 2,
                    "phase3_dataset_traffic_npc_gap_step_m_avg": 18.0,
                    "phase3_dataset_traffic_npc_speed_scale_sample_count": 2,
                    "phase3_dataset_traffic_npc_speed_scale_avg": 0.9,
                    "phase3_dataset_traffic_npc_speed_jitter_mps_sample_count": 2,
                    "phase3_dataset_traffic_npc_speed_jitter_mps_avg": 0.1,
                    "phase3_dataset_traffic_lane_index_unique_count": 2,
                    "phase3_dataset_traffic_lane_indices": [2, 3],
                    "phase3_dataset_manifest_counts_rows": 2,
                    "phase3_dataset_manifest_run_summary_count": 2,
                    "phase3_dataset_manifest_release_summary_count": 1,
                    "phase3_dataset_manifest_versions": ["sds_v1", "sds_v2"],
                },
                {
                    "batch_id": "BATCH_TRAFFIC_003",
                    "phase3_dataset_traffic_gate_result": "n/a",
                    "phase3_dataset_traffic_gate_reason_count": 0,
                    "phase3_dataset_traffic_run_summary_count": 0,
                    "phase3_dataset_manifest_run_summary_count": 0,
                    "phase3_dataset_traffic_profile_count": 10,
                },
            ]
        )
        self.assertEqual(int(summary.get("pipeline_manifest_count", 0) or 0), 3)
        self.assertEqual(int(summary.get("evaluated_manifest_count", 0) or 0), 2)
        self.assertEqual(summary.get("gate_result_counts"), {"hold": 1, "pass": 1})
        self.assertEqual(int(summary.get("gate_reason_count_total", 0) or 0), 2)
        self.assertEqual(summary.get("gate_min_run_summary_count_counts"), {"2": 1, "3": 1})
        self.assertEqual(summary.get("gate_min_traffic_profile_count_counts"), {"1": 1, "2": 1})
        self.assertEqual(summary.get("gate_min_actor_pattern_count_counts"), {"1": 2})
        self.assertEqual(summary.get("gate_min_avg_npc_count_counts"), {"2": 1, "2.5": 1})
        self.assertEqual(int(summary.get("run_summary_count_total", 0) or 0), 5)
        self.assertEqual(summary.get("run_status_counts"), {"hold": 1, "pass": 4})
        self.assertEqual(int(summary.get("traffic_profile_unique_count", 0) or 0), 2)
        self.assertEqual(summary.get("traffic_profile_ids"), ["dense_city", "night_merge"])
        self.assertAlmostEqual(float(summary.get("traffic_profile_count_avg", 0.0) or 0.0), 1.5, places=6)
        self.assertEqual(int(summary.get("max_traffic_profile_count", 0) or 0), 2)
        self.assertEqual(str(summary.get("highest_traffic_profile_batch_id", "")), "BATCH_TRAFFIC_002")
        self.assertEqual(int(summary.get("traffic_profile_source_unique_count", 0) or 0), 2)
        self.assertEqual(summary.get("traffic_profile_source_ids"), ["sumo_custom_v1", "sumo_stub_builtin_v0"])
        self.assertAlmostEqual(float(summary.get("traffic_profile_source_count_avg", 0.0) or 0.0), 1.5, places=6)
        self.assertEqual(int(summary.get("max_traffic_profile_source_count", 0) or 0), 2)
        self.assertEqual(str(summary.get("highest_traffic_profile_source_batch_id", "")), "BATCH_TRAFFIC_002")
        self.assertEqual(int(summary.get("traffic_actor_pattern_unique_count", 0) or 0), 2)
        self.assertEqual(summary.get("traffic_actor_pattern_ids"), ["aggressive", "baseline"])
        self.assertAlmostEqual(float(summary.get("traffic_actor_pattern_count_avg", 0.0) or 0.0), 1.5, places=6)
        self.assertEqual(int(summary.get("max_traffic_actor_pattern_count", 0) or 0), 2)
        self.assertEqual(str(summary.get("highest_traffic_actor_pattern_batch_id", "")), "BATCH_TRAFFIC_001")
        self.assertEqual(int(summary.get("traffic_lane_profile_signature_unique_count", 0) or 0), 2)
        self.assertEqual(summary.get("traffic_lane_profile_signatures"), ["0,1", "0,1,-1"])
        self.assertAlmostEqual(
            float(summary.get("traffic_lane_profile_signature_count_avg", 0.0) or 0.0),
            1.5,
            places=6,
        )
        self.assertEqual(int(summary.get("max_traffic_lane_profile_signature_count", 0) or 0), 2)
        self.assertEqual(
            str(summary.get("highest_traffic_lane_profile_signature_batch_id", "")),
            "BATCH_TRAFFIC_002",
        )
        self.assertAlmostEqual(float(summary.get("traffic_npc_count_avg_avg", 0.0) or 0.0), 6.25, places=6)
        self.assertAlmostEqual(float(summary.get("traffic_npc_count_avg_max", 0.0) or 0.0), 7.5, places=6)
        self.assertEqual(str(summary.get("highest_traffic_npc_avg_batch_id", "")), "BATCH_TRAFFIC_002")
        self.assertEqual(int(summary.get("traffic_npc_count_max_max", 0) or 0), 10)
        self.assertEqual(str(summary.get("highest_traffic_npc_max_batch_id", "")), "BATCH_TRAFFIC_002")
        self.assertEqual(int(summary.get("traffic_npc_initial_gap_m_sample_count_total", 0) or 0), 5)
        self.assertAlmostEqual(float(summary.get("traffic_npc_initial_gap_m_avg_avg", 0.0) or 0.0), 34.8, places=6)
        self.assertAlmostEqual(float(summary.get("traffic_npc_initial_gap_m_avg_max", 0.0) or 0.0), 42.0, places=6)
        self.assertEqual(
            str(summary.get("highest_traffic_npc_initial_gap_m_avg_batch_id", "")),
            "BATCH_TRAFFIC_001",
        )
        self.assertEqual(int(summary.get("traffic_npc_gap_step_m_sample_count_total", 0) or 0), 5)
        self.assertAlmostEqual(float(summary.get("traffic_npc_gap_step_m_avg_avg", 0.0) or 0.0), 14.4, places=6)
        self.assertAlmostEqual(float(summary.get("traffic_npc_gap_step_m_avg_max", 0.0) or 0.0), 18.0, places=6)
        self.assertEqual(
            str(summary.get("highest_traffic_npc_gap_step_m_avg_batch_id", "")),
            "BATCH_TRAFFIC_001",
        )
        self.assertEqual(int(summary.get("traffic_npc_speed_scale_sample_count_total", 0) or 0), 5)
        self.assertAlmostEqual(float(summary.get("traffic_npc_speed_scale_avg_avg", 0.0) or 0.0), 1.02, places=6)
        self.assertAlmostEqual(float(summary.get("traffic_npc_speed_scale_avg_max", 0.0) or 0.0), 1.1, places=6)
        self.assertEqual(
            str(summary.get("highest_traffic_npc_speed_scale_avg_batch_id", "")),
            "BATCH_TRAFFIC_002",
        )
        self.assertEqual(int(summary.get("traffic_npc_speed_jitter_mps_sample_count_total", 0) or 0), 5)
        self.assertAlmostEqual(
            float(summary.get("traffic_npc_speed_jitter_mps_avg_avg", 0.0) or 0.0),
            0.22,
            places=6,
        )
        self.assertAlmostEqual(
            float(summary.get("traffic_npc_speed_jitter_mps_avg_max", 0.0) or 0.0),
            0.3,
            places=6,
        )
        self.assertEqual(
            str(summary.get("highest_traffic_npc_speed_jitter_mps_avg_batch_id", "")),
            "BATCH_TRAFFIC_002",
        )
        self.assertEqual(int(summary.get("traffic_lane_indices_unique_count", 0) or 0), 3)
        self.assertEqual(summary.get("traffic_lane_indices"), [1, 2, 3])
        self.assertAlmostEqual(float(summary.get("traffic_lane_index_unique_count_avg", 0.0) or 0.0), 2.0, places=6)
        self.assertEqual(int(summary.get("dataset_manifest_run_summary_count_total", 0) or 0), 5)
        self.assertEqual(int(summary.get("dataset_manifest_release_summary_count_total", 0) or 0), 2)
        self.assertEqual(summary.get("dataset_manifest_versions"), ["sds_v1", "sds_v2"])

    def test_build_summary_artifact_collects_phase3_dataset_traffic_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifacts_root = tmp_path / "artifacts"
            reports_root = artifacts_root / "reports"
            batch_root = artifacts_root / "batch_traffic"
            reports_root.mkdir(parents=True, exist_ok=True)
            batch_root.mkdir(parents=True, exist_ok=True)

            summary_path = reports_root / "REL_PHASE3_TRAFFIC_001_sds_v1.summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_TRAFFIC_001_sds_v1",
                        "sds_version": "sds_v1",
                        "final_result": "PASS",
                        "generated_at": "2026-03-02T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            pipeline_manifest_path = batch_root / "pipeline_result.json"
            pipeline_manifest_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE3_TRAFFIC_001_sds_v1",
                        "batch_id": "BATCH_TRAFFIC_001",
                        "overall_result": "HOLD",
                        "strict_gate": True,
                        "trend_gate": {"result": "PASS"},
                        "reports": [{"sds_version": "sds_v1"}],
                        "phase3_hooks": {
                            "enabled": True,
                            "dataset_traffic_diversity": {
                                "run_summary_count": 3,
                                "run_status_counts": {"pass": 2, "hold": 1},
                                "traffic_profile_count": 2,
                                "traffic_profile_ids": ["dense_city", "night_merge"],
                                "traffic_profile_source_count": 2,
                                "traffic_profile_source_ids": ["sumo_stub_builtin_v0", "sumo_custom_v1"],
                                "traffic_actor_pattern_count": 1,
                                "traffic_actor_pattern_ids": ["baseline"],
                                "traffic_lane_profile_signature_count": 2,
                                "traffic_lane_profile_signatures": ["0,1,-1", "0,1"],
                                "traffic_npc_count_sample_count": 3,
                                "traffic_npc_count_min": 5,
                                "traffic_npc_count_avg": 7.5,
                                "traffic_npc_count_max": 10,
                                "traffic_npc_initial_gap_m_sample_count": 3,
                                "traffic_npc_initial_gap_m_avg": 30.0,
                                "traffic_npc_gap_step_m_sample_count": 3,
                                "traffic_npc_gap_step_m_avg": 12.0,
                                "traffic_npc_speed_scale_sample_count": 3,
                                "traffic_npc_speed_scale_avg": 1.1,
                                "traffic_npc_speed_jitter_mps_sample_count": 3,
                                "traffic_npc_speed_jitter_mps_avg": 0.3,
                                "traffic_lane_index_unique_count": 2,
                                "traffic_lane_indices": [1, 2],
                                "dataset_manifest_counts_rows": 3,
                                "dataset_manifest_run_summary_count": 3,
                                "dataset_manifest_release_summary_count": 1,
                                "dataset_manifest_versions": ["sds_v1"],
                            },
                        },
                        "functional_quality_gates": {
                            "phase3_dataset_traffic_gate": {
                                "result": "HOLD",
                                "reasons": [
                                    "phase3_dataset_traffic_profile_count 1 < min_traffic_profile_count 2",
                                ],
                                "details": {
                                    "min_run_summary_count": 3,
                                    "min_traffic_profile_count": 2,
                                    "min_actor_pattern_count": 1,
                                    "min_avg_npc_count": 2.5,
                                },
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            out_text = tmp_path / "summary.txt"
            out_json = tmp_path / "summary.json"
            out_db = tmp_path / "summary.db"
            run_script(
                PROTOTYPE_DIR / "build_release_summary_artifact.py",
                "--release-prefix",
                "REL_PHASE3_TRAFFIC_001",
                "--artifacts-root",
                str(artifacts_root),
                "--out-text",
                str(out_text),
                "--out-json",
                str(out_json),
                "--out-db",
                str(out_db),
                "--python-bin",
                PYTHON,
            )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
            manifests = payload.get("pipeline_manifests", [])
            self.assertEqual(len(manifests), 1)
            row = manifests[0]
            self.assertEqual(str(row.get("phase3_dataset_traffic_gate_result", "")), "hold")
            self.assertEqual(int(row.get("phase3_dataset_traffic_gate_reason_count", 0) or 0), 1)
            self.assertEqual(int(row.get("phase3_dataset_traffic_gate_min_run_summary_count", 0) or 0), 3)
            self.assertEqual(int(row.get("phase3_dataset_traffic_gate_min_traffic_profile_count", 0) or 0), 2)
            self.assertEqual(int(row.get("phase3_dataset_traffic_gate_min_actor_pattern_count", 0) or 0), 1)
            self.assertAlmostEqual(float(row.get("phase3_dataset_traffic_gate_min_avg_npc_count", 0.0) or 0.0), 2.5, places=6)
            self.assertEqual(int(row.get("phase3_dataset_traffic_run_summary_count", 0) or 0), 3)
            self.assertEqual(int(row.get("phase3_dataset_traffic_profile_count", 0) or 0), 2)
            self.assertEqual(int(row.get("phase3_dataset_traffic_actor_pattern_count", 0) or 0), 1)
            self.assertEqual(int(row.get("phase3_dataset_traffic_profile_source_count", 0) or 0), 2)
            self.assertEqual(
                row.get("phase3_dataset_traffic_profile_source_ids"),
                ["sumo_stub_builtin_v0", "sumo_custom_v1"],
            )
            self.assertEqual(int(row.get("phase3_dataset_traffic_lane_profile_signature_count", 0) or 0), 2)
            self.assertEqual(row.get("phase3_dataset_traffic_lane_profile_signatures"), ["0,1,-1", "0,1"])
            self.assertAlmostEqual(float(row.get("phase3_dataset_traffic_npc_count_avg", 0.0) or 0.0), 7.5, places=6)
            self.assertEqual(int(row.get("phase3_dataset_traffic_npc_initial_gap_m_sample_count", 0) or 0), 3)
            self.assertAlmostEqual(float(row.get("phase3_dataset_traffic_npc_initial_gap_m_avg", 0.0) or 0.0), 30.0, places=6)
            self.assertEqual(int(row.get("phase3_dataset_traffic_npc_gap_step_m_sample_count", 0) or 0), 3)
            self.assertAlmostEqual(float(row.get("phase3_dataset_traffic_npc_gap_step_m_avg", 0.0) or 0.0), 12.0, places=6)
            self.assertEqual(int(row.get("phase3_dataset_traffic_npc_speed_scale_sample_count", 0) or 0), 3)
            self.assertAlmostEqual(float(row.get("phase3_dataset_traffic_npc_speed_scale_avg", 0.0) or 0.0), 1.1, places=6)
            self.assertEqual(int(row.get("phase3_dataset_traffic_npc_speed_jitter_mps_sample_count", 0) or 0), 3)
            self.assertAlmostEqual(
                float(row.get("phase3_dataset_traffic_npc_speed_jitter_mps_avg", 0.0) or 0.0),
                0.3,
                places=6,
            )
            self.assertEqual(row.get("phase3_dataset_traffic_lane_indices"), [1, 2])
            self.assertEqual(row.get("phase3_dataset_manifest_versions"), ["sds_v1"])
            phase3_dataset_summary = payload.get("phase3_dataset_traffic_summary", {})
            self.assertEqual(int(phase3_dataset_summary.get("evaluated_manifest_count", 0) or 0), 1)
            self.assertEqual(phase3_dataset_summary.get("gate_result_counts"), {"hold": 1})
            self.assertEqual(phase3_dataset_summary.get("gate_min_run_summary_count_counts"), {"3": 1})
            self.assertEqual(phase3_dataset_summary.get("gate_min_traffic_profile_count_counts"), {"2": 1})
            self.assertEqual(phase3_dataset_summary.get("gate_min_actor_pattern_count_counts"), {"1": 1})
            self.assertEqual(phase3_dataset_summary.get("gate_min_avg_npc_count_counts"), {"2.5": 1})
            self.assertEqual(int(phase3_dataset_summary.get("run_summary_count_total", 0) or 0), 3)
            self.assertEqual(int(phase3_dataset_summary.get("traffic_profile_unique_count", 0) or 0), 2)
            self.assertEqual(phase3_dataset_summary.get("traffic_profile_ids"), ["dense_city", "night_merge"])
            self.assertEqual(int(phase3_dataset_summary.get("traffic_profile_source_unique_count", 0) or 0), 2)
            self.assertEqual(
                phase3_dataset_summary.get("traffic_profile_source_ids"),
                ["sumo_custom_v1", "sumo_stub_builtin_v0"],
            )
            self.assertEqual(
                int(phase3_dataset_summary.get("traffic_lane_profile_signature_unique_count", 0) or 0),
                2,
            )
            self.assertEqual(
                phase3_dataset_summary.get("traffic_lane_profile_signatures"),
                ["0,1", "0,1,-1"],
            )
            self.assertIn("phase3_dataset_traffic=evaluated:1", out_text.read_text(encoding="utf-8"))

    def test_markdown_renderer_renders_phase3_dataset_traffic_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_json = tmp_path / "summary.json"
            summary_json.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_TRAFFIC_MD_001",
                        "summary_count": 1,
                        "sds_versions": ["sds_v1"],
                        "final_result_counts": {"PASS": 1},
                        "pipeline_manifest_count": 1,
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "pipeline_manifests": [],
                        "timing_ms": {"total": 100},
                        "phase3_dataset_traffic_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "gate_result_counts": {"hold": 1},
                            "gate_reason_count_total": 1,
                            "run_summary_count_total": 3,
                            "run_status_counts": {"hold": 1, "pass": 2},
                            "traffic_profile_unique_count": 2,
                            "traffic_profile_ids": ["dense_city", "night_merge"],
                            "traffic_profile_count_avg": 2.0,
                            "max_traffic_profile_count": 2,
                            "highest_traffic_profile_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_actor_pattern_unique_count": 1,
                            "traffic_actor_pattern_ids": ["baseline"],
                            "traffic_actor_pattern_count_avg": 1.0,
                            "max_traffic_actor_pattern_count": 1,
                            "highest_traffic_actor_pattern_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_npc_count_avg_avg": 7.5,
                            "traffic_npc_count_avg_max": 7.5,
                            "highest_traffic_npc_avg_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_npc_count_max_max": 10,
                            "highest_traffic_npc_max_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_lane_indices_unique_count": 2,
                            "traffic_lane_index_unique_count_avg": 2.0,
                            "traffic_lane_indices": [1, 2],
                            "dataset_manifest_counts_rows_total": 3,
                            "dataset_manifest_run_summary_count_total": 3,
                            "dataset_manifest_release_summary_count_total": 1,
                            "dataset_manifest_versions": ["sds_v1"],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            proc = run_script(
                PROTOTYPE_DIR / "render_release_summary_markdown.py",
                "--summary-json",
                str(summary_json),
                "--title",
                "Summary",
            )
            self.assertIn("- phase3_dataset_traffic: `evaluated=1, gate_results=hold:1,", proc.stdout)
            self.assertIn("profiles=unique:2,ids:dense_city, night_merge", proc.stdout)

    def test_notification_includes_phase3_dataset_traffic_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_TRAFFIC_NOTIFY_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_dataset_traffic_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "gate_result_counts": {"hold": 1},
                            "gate_reason_count_total": 1,
                            "run_summary_count_total": 3,
                            "run_status_counts": {"hold": 1, "pass": 2},
                            "traffic_profile_unique_count": 2,
                            "traffic_profile_ids": ["dense_city", "night_merge"],
                            "traffic_profile_count_avg": 2.0,
                            "max_traffic_profile_count": 2,
                            "highest_traffic_profile_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_actor_pattern_unique_count": 1,
                            "traffic_actor_pattern_ids": ["baseline"],
                            "traffic_actor_pattern_count_avg": 1.0,
                            "max_traffic_actor_pattern_count": 1,
                            "highest_traffic_actor_pattern_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_npc_count_avg_avg": 7.5,
                            "traffic_npc_count_avg_max": 7.5,
                            "highest_traffic_npc_avg_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_npc_count_max_max": 10,
                            "highest_traffic_npc_max_batch_id": "BATCH_TRAFFIC_001",
                            "traffic_lane_indices_unique_count": 2,
                            "traffic_lane_index_unique_count_avg": 2.0,
                            "traffic_lane_indices": [1, 2],
                            "dataset_manifest_counts_rows_total": 3,
                            "dataset_manifest_run_summary_count_total": 3,
                            "dataset_manifest_release_summary_count_total": 1,
                            "dataset_manifest_versions": ["sds_v1"],
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
            self.assertIn("phase3_dataset_traffic_summary=evaluated=1,gate_results=hold:1", payload.get("message_text", ""))
            self.assertIn("*phase3 dataset traffic*", json.dumps(payload.get("slack", {})))
            self.assertIn("phase3_dataset_traffic_summary_text", payload)
            self.assertIsInstance(payload.get("phase3_dataset_traffic_summary"), dict)

    def test_notification_warns_on_phase3_dataset_traffic_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_TRAFFIC_NOTIFY_WARN_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_dataset_traffic_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "run_summary_count_total": 3,
                            "traffic_profile_unique_count": 2,
                            "traffic_actor_pattern_unique_count": 2,
                            "traffic_npc_count_avg_avg": 7.5,
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
                "--phase3-dataset-traffic-profile-count-warn-min",
                "3",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "WARN")
            self.assertIn(
                "phase3_dataset_traffic_warning=phase3_dataset_traffic_profile_unique_count=2 below warn_min=3",
                payload.get("message_text", ""),
            )
            self.assertIn(
                "phase3_dataset_traffic_profile_unique_count_below_warn_min",
                payload.get("phase3_dataset_traffic_warning_reasons", []),
            )

    def test_notification_holds_on_phase3_dataset_traffic_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_TRAFFIC_NOTIFY_HOLD_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_dataset_traffic_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "run_summary_count_total": 3,
                            "traffic_profile_unique_count": 2,
                            "traffic_actor_pattern_unique_count": 2,
                            "traffic_npc_count_avg_avg": 7.5,
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
                "--phase3-dataset-traffic-run-summary-hold-min",
                "4",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "HOLD")
            self.assertIn(
                "phase3_dataset_traffic_warning=phase3_dataset_traffic_run_summary_count_total=3 below hold_min=4",
                payload.get("message_text", ""),
            )
            self.assertIn(
                "phase3_dataset_traffic_run_summary_count_below_hold_min",
                payload.get("phase3_dataset_traffic_warning_reasons", []),
            )

    def test_notification_warns_on_phase3_dataset_traffic_threshold_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_TRAFFIC_NOTIFY_MISMATCH_WARN_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_dataset_traffic_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "run_summary_count_total": 5,
                            "traffic_profile_unique_count": 4,
                            "traffic_actor_pattern_unique_count": 2,
                            "traffic_npc_count_avg_avg": 7.5,
                            "gate_min_traffic_profile_count_counts": {"2": 1},
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
                "--phase3-dataset-traffic-profile-count-warn-min",
                "3",
                "--phase3-dataset-traffic-profile-count-hold-min",
                "2",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "WARN")
            self.assertEqual(str(payload.get("phase3_dataset_traffic_threshold_drift_severity", "")), "WARN")
            self.assertIn(
                "phase3_dataset_traffic_profile_count_warn_min_mismatch",
                payload.get("phase3_dataset_traffic_warning_reasons", []),
            )
            self.assertIn(
                "profile_count_warn_min=expected:3,observed:2:1",
                str(payload.get("phase3_dataset_traffic_threshold_drift_summary_text", "")),
            )

    def test_notification_holds_on_phase3_dataset_traffic_threshold_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            out_path = tmp_path / "notification.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_prefix": "REL_PHASE3_TRAFFIC_NOTIFY_MISMATCH_HOLD_001",
                        "summary_count": 1,
                        "final_result_counts": {"PASS": 1},
                        "pipeline_overall_counts": {"PASS": 1},
                        "pipeline_trend_counts": {"PASS": 1},
                        "phase3_dataset_traffic_summary": {
                            "evaluated_manifest_count": 1,
                            "pipeline_manifest_count": 1,
                            "run_summary_count_total": 5,
                            "traffic_profile_unique_count": 3,
                            "traffic_actor_pattern_unique_count": 2,
                            "traffic_npc_count_avg_avg": 3.5,
                            "gate_min_avg_npc_count_counts": {"2.5": 1},
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
                "--phase3-dataset-traffic-avg-npc-count-warn-min",
                "2.5",
                "--phase3-dataset-traffic-avg-npc-count-hold-min",
                "3.0",
            )
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("status", "")), "HOLD")
            self.assertEqual(str(payload.get("phase3_dataset_traffic_threshold_drift_severity", "")), "HOLD")
            self.assertIn(
                "phase3_dataset_traffic_avg_npc_count_hold_min_mismatch",
                payload.get("phase3_dataset_traffic_warning_reasons", []),
            )
            self.assertIn(
                "avg_npc_count_hold_min=expected:3.000,observed:2.5:1",
                str(payload.get("phase3_dataset_traffic_threshold_drift_summary_text", "")),
            )


if __name__ == "__main__":
    unittest.main()
