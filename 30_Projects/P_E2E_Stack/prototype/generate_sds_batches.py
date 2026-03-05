#!/usr/bin/env python3
"""Generate per-SDS regression batch specs from one catalog manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ci_commands import shell_join
from ci_input_parsing import parse_int
from ci_phases import PHASE_RESOLVE_INPUTS
from ci_script_entry import resolve_step_summary_file_from_env, run_with_error_handling
from ci_subprocess import run_capture_stdout_or_raise
from ci_sync_utils import resolve_repo_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate per-SDS batch specs from catalog manifest")
    parser.add_argument("--catalog-manifest", required=True, help="Path to catalog_manifest.json")
    parser.add_argument("--versions", default="", help="SDS versions list (comma or whitespace separated)")
    parser.add_argument("--out-dir", default="../../P_Cloud-Engine/prototype/examples", help="Output directory")
    parser.add_argument("--batch-id-prefix", default="BATCH_REG_HWY", help="Batch ID prefix")
    parser.add_argument("--batch-id-suffix", default="_0001", help="Batch ID suffix")
    parser.add_argument("--batch-file-prefix", default="batch_regression_highway", help="Batch file prefix")
    parser.add_argument("--run-id-prefix-base", default="RUN_RG", help="Run ID prefix base")
    parser.add_argument("--seed-base", default="", help="Seed base for first SDS version")
    parser.add_argument("--seed-version-stride", default="", help="Seed base stride between versions")
    parser.add_argument("--seed-step", default="", help="Seed step per scenario")
    parser.add_argument("--profiles-out", default="", help="Optional CI matrix profiles JSON output path")
    parser.add_argument(
        "--generator",
        default=str(Path(__file__).resolve().parents[2] / "P_Cloud-Engine/prototype/generate_batch_from_catalog.py"),
        help="Path to generate_batch_from_catalog.py",
    )
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--max-concurrency", default="")
    parser.add_argument("--timeout-sec-per-run", default="")
    parser.add_argument("--output-root", default="../batch_runs")
    parser.add_argument("--sim-runner-script", default="../../../P_Sim-Engine/prototype/core_sim_runner.py")
    parser.add_argument("--run-source", default="sim_closed_loop")
    parser.add_argument("--sim-version", default="sim_engine_v0_prototype")
    parser.add_argument("--fidelity-profile", default="dev-fast")
    parser.add_argument("--map-id", default="map_demo_highway")
    parser.add_argument("--map-version", default="v0")
    parser.add_argument("--odd-tags", default="highway,regression,v0")
    return parser.parse_args()


def parse_versions(raw: str) -> list[str]:
    tokens = [token.strip() for token in str(raw).replace(",", " ").split() if token.strip()]
    ordered_unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        ordered_unique.append(token)
    return ordered_unique


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return slug or "unknown"

def as_repo_relative_or_abs(path: Path) -> str:
    root = resolve_repo_root(__file__)
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


def build_generator_cmd(
    *,
    args: argparse.Namespace,
    catalog_manifest: Path,
    version: str,
    version_slug: str,
    out_path: Path,
    seed_base: int,
) -> list[str]:
    version_token = version_slug.upper()
    batch_id = f"{args.batch_id_prefix}_{version_token}{args.batch_id_suffix}"
    run_id_prefix = f"{args.run_id_prefix_base}_{version_token}"

    return [
        str(args.python_bin),
        str(Path(args.generator).resolve()),
        "--catalog-manifest",
        str(catalog_manifest),
        "--batch-id",
        batch_id,
        "--sds-version",
        version,
        "--out",
        str(out_path),
        "--run-id-prefix",
        run_id_prefix,
        "--seed-base",
        str(seed_base),
        "--seed-step",
        str(args.seed_step),
        "--max-concurrency",
        str(args.max_concurrency),
        "--timeout-sec-per-run",
        str(args.timeout_sec_per_run),
        "--output-root",
        str(args.output_root),
        "--python-bin",
        str(args.python_bin),
        "--sim-runner-script",
        str(args.sim_runner_script),
        "--run-source",
        str(args.run_source),
        "--sim-version",
        str(args.sim_version),
        "--fidelity-profile",
        str(args.fidelity_profile),
        "--map-id",
        str(args.map_id),
        "--map-version",
        str(args.map_version),
        "--odd-tags",
        str(args.odd_tags),
    ]


def main() -> int:
    args = parse_args()
    args.seed_base = parse_int(str(args.seed_base), default=1101, field="seed-base")
    args.seed_version_stride = parse_int(
        str(args.seed_version_stride),
        default=1000,
        field="seed-version-stride",
    )
    args.seed_step = parse_int(str(args.seed_step), default=1, field="seed-step")
    args.max_concurrency = parse_int(
        str(args.max_concurrency),
        default=4,
        field="max-concurrency",
    )
    args.timeout_sec_per_run = parse_int(
        str(args.timeout_sec_per_run),
        default=30,
        field="timeout-sec-per-run",
    )
    versions = parse_versions(args.versions)
    if not versions:
        raise ValueError("at least one sds version is required (--versions)")

    catalog_manifest = Path(args.catalog_manifest).resolve()
    if not catalog_manifest.exists():
        raise FileNotFoundError(f"catalog manifest not found: {catalog_manifest}")

    generator_path = Path(args.generator).resolve()
    if not generator_path.exists():
        raise FileNotFoundError(f"batch generator not found: {generator_path}")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    generated: list[dict[str, str]] = []
    profiles: list[dict[str, str]] = []
    slug_to_version: dict[str, str] = {}
    for index, version in enumerate(versions):
        version_slug = slugify(version)
        previous_version = slug_to_version.get(version_slug)
        if previous_version is not None and previous_version != version:
            raise ValueError(
                "versions produce duplicate profile slug '{slug}' "
                "for distinct versions: {first} vs {second}".format(
                    slug=version_slug,
                    first=previous_version,
                    second=version,
                )
            )
        slug_to_version[version_slug] = version
        out_path = out_dir / f"{args.batch_file_prefix}_{version}.json"
        seed_base = args.seed_base + (index * args.seed_version_stride)

        cmd = build_generator_cmd(
            args=args,
            catalog_manifest=catalog_manifest,
            version=version,
            version_slug=version_slug,
            out_path=out_path,
            seed_base=seed_base,
        )
        print(f"[cmd] {shell_join(cmd)}")
        run_capture_stdout_or_raise(cmd, context=f"generate batch spec ({version})")

        batch_id = f"{args.batch_id_prefix}_{version_slug.upper()}{args.batch_id_suffix}"
        generated.append(
            {
                "sds_version": version,
                "batch_id": batch_id,
                "batch_spec": str(out_path),
            }
        )
        profiles.append(
            {
                "profile_id": version_slug,
                "default_batch_spec": as_repo_relative_or_abs(out_path),
                "default_sds_versions": version,
            }
        )

    profiles_out = str(args.profiles_out).strip()
    if profiles_out:
        profiles_path = Path(profiles_out).resolve()
        profiles_path.parent.mkdir(parents=True, exist_ok=True)
        profiles_path.write_text(json.dumps({"profiles": profiles}, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] profiles_out={profiles_path}")
        print(f"[ok] profile_count={len(profiles)}")

    print(f"[ok] generated_specs={len(generated)}")
    for item in generated:
        print(
            "[ok] sds_version={sds_version} batch_id={batch_id} batch_spec={batch_spec}".format(**item)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_with_error_handling(
            main,
            source="generate_sds_batches.py",
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=PHASE_RESOLVE_INPUTS,
        )
    )
