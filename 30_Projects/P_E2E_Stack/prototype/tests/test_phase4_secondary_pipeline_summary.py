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


class Phase4SecondaryPipelineSummaryTests(unittest.TestCase):
    def test_run_e2e_manifest_exposes_secondary_reference_pattern_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            batch_spec = tmp_path / "batch_spec.json"
            batch_spec.write_text(
                json.dumps(
                    {
                        "batch_id": "batch_demo",
                        "output_root": str(tmp_path / "batch_runs"),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            cloud_runner = tmp_path / "fake_cloud_runner.py"
            cloud_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "spec_path = Path(sys.argv[sys.argv.index('--batch-spec') + 1]).resolve()\n"
                "spec = json.loads(spec_path.read_text(encoding='utf-8'))\n"
                "output_root = Path(spec['output_root']).resolve()\n"
                "batch_root = output_root / str(spec['batch_id'])\n"
                "batch_root.mkdir(parents=True, exist_ok=True)\n"
                "(batch_root / 'batch_result.json').write_text(json.dumps({'ok': True}) + '\\n', encoding='utf-8')\n"
                "run_dir = batch_root / 'run_001'\n"
                "run_dir.mkdir(parents=True, exist_ok=True)\n"
                "(run_dir / 'summary.json').write_text(json.dumps({'run_id':'run_001','scenario_id':'scenario.secondary','sds_version':'sds_v1','status':'success'}) + '\\n', encoding='utf-8')\n"
                "print('[ok] cloud')\n",
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
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "summary_path = Path(args[args.index('--summary-out') + 1]).resolve()\n"
                "release_id = args[args.index('--release-id') + 1]\n"
                "sds_version = args[args.index('--sds-version') + 1]\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "summary_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text('# fake report\\n', encoding='utf-8')\n"
                "summary_path.write_text(json.dumps({'release_id': release_id, 'sds_version': sds_version, 'final_result': 'PASS'}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            hil_runner = tmp_path / "fake_hil_runner.py"
            hil_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text(json.dumps({'hil_schedule_schema_version': 'hil_schedule_manifest_v0'}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            adp_runner = tmp_path / "fake_adp_runner.py"
            adp_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text(json.dumps({'user_responsibility': {'requires_ack': True, 'notice_count': 2}}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            linkage_runner = tmp_path / "fake_linkage_runner.py"
            linkage_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "modules = []\n"
                "for i, token in enumerate(args):\n"
                "    if token == '--module' and i + 1 < len(args):\n"
                "        modules.append(args[i + 1])\n"
                "if not modules:\n"
                "    modules = ['adp']\n"
                "rows = []\n"
                "for module in modules:\n"
                "    rows.append({\n"
                "        'module': module,\n"
                "        'matrix_status': 'PHASE4_DONE',\n"
                "        'ready_row_count': 1,\n"
                "        'reference_priority': 'P1',\n"
                "        'reference_repositories': ['autowarefoundation/autoware'],\n"
                "        'reference_pattern_to_extract': 'pattern',\n"
                "        'reference_local_first_target': 'target',\n"
                "    })\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text(json.dumps({'modules': rows}) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            reference_pattern_runner = tmp_path / "fake_reference_pattern_runner.py"
            reference_pattern_runner.write_text(
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "args = sys.argv\n"
                "modules = []\n"
                "for i, token in enumerate(args):\n"
                "    if token == '--module' and i + 1 < len(args):\n"
                "        modules.append(args[i + 1])\n"
                "if not modules:\n"
                "    modules = ['adp']\n"
                "rows = []\n"
                "for module in modules:\n"
                "    rows.append({\n"
                "        'module': module,\n"
                "        'coverage_ratio': 1.0,\n"
                "        'unmatched_patterns': [],\n"
                "        'secondary_coverage_ratio': 0.5,\n"
                "        'secondary_unmatched_patterns': ['missing_secondary_pattern'],\n"
                "    })\n"
                "payload = {\n"
                "    'phase4_reference_pattern_scan_report_schema_version': 'phase4_reference_pattern_scan_report_v0',\n"
                "    'total_coverage_ratio': 1.0,\n"
                "    'secondary_module_count': 1,\n"
                "    'secondary_total_expected_pattern_count': 2,\n"
                "    'secondary_total_matched_pattern_count': 1,\n"
                "    'secondary_total_coverage_ratio': 0.5,\n"
                "    'reference_repo_scanned': ['autowarefoundation/autoware'],\n"
                "    'secondary_reference_repo_scanned': ['ApolloAuto/apollo'],\n"
                "    'modules': rows,\n"
                "}\n"
                "out_path = Path(args[args.index('--out') + 1]).resolve()\n"
                "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "out_path.write_text(json.dumps(payload) + '\\n', encoding='utf-8')\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )

            hil_interface = tmp_path / "hil_interface.json"
            hil_sequence = tmp_path / "hil_sequence.json"
            hil_interface.write_text("{}\n", encoding="utf-8")
            hil_sequence.write_text("{}\n", encoding="utf-8")

            run_script(
                PROTOTYPE_DIR / "run_e2e_pipeline.py",
                "--batch-spec",
                str(batch_spec),
                "--release-id",
                "REL_PHASE4_SECONDARY_001",
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
                "--phase4-enable-hooks",
                "--hil-sequence-runner",
                str(hil_runner),
                "--hil-interface",
                str(hil_interface),
                "--hil-sequence",
                str(hil_sequence),
                "--hil-schedule-out",
                str(tmp_path / "phase4" / "hil_schedule_manifest.json"),
                "--adp-trace-runner",
                str(adp_runner),
                "--adp-trace-out",
                str(tmp_path / "phase4" / "adp_trace.json"),
                "--phase4-linkage-runner",
                str(linkage_runner),
                "--phase4-linkage-module",
                "adp",
                "--phase4-linkage-out",
                str(tmp_path / "phase4" / "phase4_linkage_report.json"),
                "--phase4-reference-pattern-runner",
                str(reference_pattern_runner),
                "--phase4-reference-pattern-module",
                "adp",
                "--phase4-reference-pattern-out",
                str(tmp_path / "phase4" / "phase4_reference_pattern_scan_report.json"),
            )

            manifest_path = tmp_path / "batch_runs" / "batch_demo" / "pipeline_result.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            reference_pattern_scan = manifest.get("phase4_hooks", {}).get("reference_pattern_scan", {})
            self.assertEqual(
                float(reference_pattern_scan.get("reference_pattern_secondary_total_coverage_ratio", 0.0)),
                0.5,
            )
            self.assertEqual(int(reference_pattern_scan.get("reference_pattern_secondary_module_count", 0)), 1)
            self.assertEqual(
                float(reference_pattern_scan.get("reference_pattern_secondary_module_coverage", {}).get("adp", 0.0)),
                0.5,
            )
            self.assertEqual(
                int(
                    reference_pattern_scan.get("reference_pattern_secondary_module_unmatched_counts", {}).get("adp", -1)
                ),
                1,
            )
            self.assertIn(
                "ApolloAuto/apollo",
                reference_pattern_scan.get("secondary_reference_repo_scanned", []),
            )

    def test_summary_artifact_and_markdown_include_secondary_phase4_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifacts_root = tmp_path / "artifacts"
            reports_root = artifacts_root / "reports"
            batch_root = artifacts_root / "batch_x"
            reports_root.mkdir(parents=True, exist_ok=True)
            batch_root.mkdir(parents=True, exist_ok=True)

            summary_path = reports_root / "REL_PHASE4_SECONDARY_SUMMARY_001_sds_v1.summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE4_SECONDARY_SUMMARY_001_sds_v1",
                        "sds_version": "sds_v1",
                        "final_result": "PASS",
                        "generated_at": "2026-02-28T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            pipeline_manifest_path = batch_root / "pipeline_result.json"
            pipeline_manifest_path.write_text(
                json.dumps(
                    {
                        "release_id": "REL_PHASE4_SECONDARY_SUMMARY_001_sds_v1",
                        "batch_id": "BATCH_SECONDARY_001",
                        "overall_result": "PASS",
                        "strict_gate": True,
                        "trend_gate": {"result": "PASS"},
                        "reports": [{"sds_version": "sds_v1"}],
                        "phase4_hooks": {
                            "reference_pattern_scan": {
                                "reference_pattern_total_coverage_ratio": 1.0,
                                "reference_pattern_module_coverage": {
                                    "adp": 1.0,
                                },
                                "reference_pattern_secondary_total_coverage_ratio": 0.5,
                                "reference_pattern_secondary_module_count": 1,
                                "reference_pattern_secondary_module_coverage": {
                                    "adp": 0.5,
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
            run_script(
                PROTOTYPE_DIR / "build_release_summary_artifact.py",
                "--artifacts-root",
                str(artifacts_root),
                "--release-prefix",
                "REL_PHASE4_SECONDARY_SUMMARY_001",
                "--out-text",
                str(out_text),
                "--out-json",
                str(out_json),
                "--out-db",
                str(tmp_path / "summary.sqlite"),
            )

            summary_payload = json.loads(out_json.read_text(encoding="utf-8"))
            pipeline_manifest = summary_payload.get("pipeline_manifests", [])[0]
            self.assertEqual(
                float(pipeline_manifest.get("phase4_reference_primary_total_coverage_ratio", 0.0)),
                1.0,
            )
            self.assertEqual(
                float(pipeline_manifest.get("phase4_reference_secondary_total_coverage_ratio", 0.0)),
                0.5,
            )
            self.assertEqual(int(pipeline_manifest.get("phase4_reference_secondary_module_count", 0)), 1)
            self.assertEqual(
                float(
                    pipeline_manifest.get("phase4_reference_secondary_module_coverage", {}).get("adp", 0.0)
                ),
                0.5,
            )
            self.assertEqual(
                float(
                    pipeline_manifest.get("phase4_reference_primary_module_coverage", {}).get("adp", 0.0)
                ),
                1.0,
            )
            self.assertEqual(
                int(summary_payload.get("phase4_primary_coverage_summary", {}).get("evaluated_manifest_count", 0)),
                1,
            )
            self.assertEqual(
                float(
                    summary_payload.get("phase4_primary_coverage_summary", {})
                    .get("module_coverage_summary", {})
                    .get("adp", {})
                    .get("min_coverage_ratio", 0.0)
                ),
                1.0,
            )

            markdown_proc = run_script(
                PROTOTYPE_DIR / "render_release_summary_markdown.py",
                "--summary-json",
                str(out_json),
                "--title",
                "Secondary Coverage Summary",
            )
            self.assertIn(
                "BATCH_SECONDARY_001:overall=PASS,trend=PASS,strict=True,phase4_primary_cov=1.000,phase4_secondary_cov=0.500(modules=1)",
                markdown_proc.stdout,
            )
            self.assertIn(
                "- phase4_primary_coverage: `evaluated=1, min=1.000 (BATCH_SECONDARY_001), avg=1.000, max=1.000 (BATCH_SECONDARY_001)`",
                markdown_proc.stdout,
            )
            self.assertIn(
                "- phase4_primary_module_coverage: `adp:min=1.000 (BATCH_SECONDARY_001), avg=1.000, max=1.000 (BATCH_SECONDARY_001)`",
                markdown_proc.stdout,
            )


if __name__ == "__main__":
    unittest.main()
