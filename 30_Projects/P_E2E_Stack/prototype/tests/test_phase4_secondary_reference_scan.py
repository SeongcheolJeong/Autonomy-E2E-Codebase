import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROTOTYPE_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROTOTYPE_DIR / "phase4_reference_pattern_scan_stub.py"
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


class Phase4SecondaryReferenceScanTests(unittest.TestCase):
    def _write_reference_map(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                [
                    "# Phase-4 External Reference Map",
                    "",
                    "| Phase-4 Module | Priority | Primary References | Pattern to Extract | Local First Target |",
                    "|---|---|---|---|---|",
                    "| `adp` | P1 | `autowarefoundation/autoware` | primary workflow pattern | target_adp |",
                    "",
                    "## Secondary Module-to-Reference Mapping",
                    "",
                    "| Phase-4 Module | Secondary References | Candidate Patterns to Extract |",
                    "|---|---|---|",
                    "| `adp` | `ApolloAuto/apollo` | moduleized planning control handoff contracts |",
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_reference_index(self, path: Path, *, include_secondary_repo: bool) -> None:
        repositories: list[dict[str, object]] = [
            {
                "repository": "autowarefoundation/autoware",
                "observed_patterns": ["primary workflow pattern"],
            }
        ]
        if include_secondary_repo:
            repositories.append(
                {
                    "repository": "ApolloAuto/apollo",
                    "observed_patterns": ["moduleized planning control handoff contracts"],
                }
            )
        path.write_text(
            json.dumps(
                {
                    "phase4_reference_index_schema_version": "phase4_reference_index_v0",
                    "generated_at": "2026-02-28T00:00:00+00:00",
                    "repositories": repositories,
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_reports_secondary_reference_coverage_when_index_contains_secondary_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference_map = tmp_path / "PHASE4_EXTERNAL_REFERENCE_MAP.md"
            reference_index = tmp_path / "PHASE4_REFERENCE_SCAN_INDEX_STUB.json"
            out_path = tmp_path / "phase4_reference_pattern_scan_report.json"
            self._write_reference_map(reference_map)
            self._write_reference_index(reference_index, include_secondary_repo=True)

            proc = run_script(
                "--reference-map",
                str(reference_map),
                "--reference-index",
                str(reference_index),
                "--module",
                "adp",
                "--out",
                str(out_path),
            )
            self.assertIn("[ok] module=adp matched=1/1 coverage=1.000", proc.stdout)

            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("module_count"), 1)
            self.assertEqual(payload.get("total_expected_pattern_count"), 1)
            self.assertEqual(payload.get("total_matched_pattern_count"), 1)
            self.assertEqual(payload.get("secondary_module_count"), 1)
            self.assertEqual(payload.get("secondary_total_expected_pattern_count"), 1)
            self.assertEqual(payload.get("secondary_total_matched_pattern_count"), 1)
            self.assertAlmostEqual(float(payload.get("secondary_total_coverage_ratio", 0.0)), 1.0)

            module_row = payload.get("modules", [])[0]
            self.assertTrue(module_row.get("secondary_reference_enabled"))
            self.assertEqual(module_row.get("secondary_references"), ["ApolloAuto/apollo"])
            self.assertEqual(module_row.get("secondary_expected_pattern_count"), 1)
            self.assertEqual(module_row.get("secondary_matched_pattern_count"), 1)
            self.assertEqual(module_row.get("secondary_missing_repositories"), [])
            self.assertAlmostEqual(float(module_row.get("secondary_coverage_ratio", 0.0)), 1.0)

    def test_missing_secondary_repo_in_index_does_not_fail_primary_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference_map = tmp_path / "PHASE4_EXTERNAL_REFERENCE_MAP.md"
            reference_index = tmp_path / "PHASE4_REFERENCE_SCAN_INDEX_STUB.json"
            out_path = tmp_path / "phase4_reference_pattern_scan_report.json"
            self._write_reference_map(reference_map)
            self._write_reference_index(reference_index, include_secondary_repo=False)

            proc = run_script(
                "--reference-map",
                str(reference_map),
                "--reference-index",
                str(reference_index),
                "--module",
                "adp",
                "--out",
                str(out_path),
            )
            self.assertIn("[ok] module=adp matched=1/1 coverage=1.000", proc.stdout)

            payload = json.loads(out_path.read_text(encoding="utf-8"))
            module_row = payload.get("modules", [])[0]
            self.assertEqual(module_row.get("secondary_expected_pattern_count"), 1)
            self.assertEqual(module_row.get("secondary_matched_pattern_count"), 0)
            self.assertEqual(module_row.get("secondary_missing_repositories"), ["ApolloAuto/apollo"])
            self.assertEqual(
                module_row.get("secondary_unmatched_patterns"),
                ["moduleized planning control handoff contracts"],
            )
            self.assertAlmostEqual(float(module_row.get("secondary_coverage_ratio", 0.0)), 0.0)
            self.assertEqual(payload.get("secondary_total_expected_pattern_count"), 1)
            self.assertEqual(payload.get("secondary_total_matched_pattern_count"), 0)
            self.assertAlmostEqual(float(payload.get("secondary_total_coverage_ratio", 0.0)), 0.0)

    def test_recovers_secondary_repo_patterns_from_local_repo_root_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reference_map = tmp_path / "PHASE4_EXTERNAL_REFERENCE_MAP.md"
            reference_index = tmp_path / "PHASE4_REFERENCE_SCAN_INDEX_STUB.json"
            out_path = tmp_path / "phase4_reference_pattern_scan_report.json"
            repo_root = tmp_path / "reference_repos"
            apollo_repo = repo_root / "ApolloAuto__apollo"

            self._write_reference_map(reference_map)
            self._write_reference_index(reference_index, include_secondary_repo=False)
            apollo_repo.mkdir(parents=True, exist_ok=True)
            (apollo_repo / "README.md").write_text(
                "This repository contains moduleized planning control handoff contracts.",
                encoding="utf-8",
            )

            proc = run_script(
                "--reference-map",
                str(reference_map),
                "--reference-index",
                str(reference_index),
                "--reference-repo-root",
                str(repo_root),
                "--module",
                "adp",
                "--out",
                str(out_path),
            )
            self.assertIn("[ok] module=adp matched=1/1 coverage=1.000", proc.stdout)

            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("secondary_reference_repo_paths", {}).get("ApolloAuto/apollo"), str(apollo_repo.resolve()))
            self.assertIn("ApolloAuto/apollo", payload.get("secondary_reference_repo_scanned", []))
            module_row = payload.get("modules", [])[0]
            self.assertEqual(module_row.get("secondary_missing_repositories"), [])
            self.assertEqual(module_row.get("secondary_matched_pattern_count"), 1)
            self.assertAlmostEqual(float(module_row.get("secondary_coverage_ratio", 0.0)), 1.0)


if __name__ == "__main__":
    unittest.main()
