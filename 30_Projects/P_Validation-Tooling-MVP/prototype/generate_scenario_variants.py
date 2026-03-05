#!/usr/bin/env python3
"""Generate concrete scenario variants from logical scenario parameter sets."""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary

ERROR_SOURCE = "generate_scenario_variants.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate concrete scenario variants from logical scenario definitions"
    )
    parser.add_argument(
        "--logical-scenarios",
        default="",
        help="Path to logical scenario JSON file",
    )
    parser.add_argument(
        "--scenario-language-profile",
        default="",
        help="Scenario language profile ID under scenario language directory (without .json)",
    )
    parser.add_argument(
        "--scenario-language-dir",
        default=str(Path(__file__).resolve().with_name("scenario_languages")),
        help="Scenario language profile directory",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output JSON path for generated concrete variants",
    )
    parser.add_argument(
        "--sampling",
        choices=["full", "random"],
        default="full",
        help="Generation mode: full cartesian or deterministic random sample",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=0,
        help="Variant sample size per logical scenario when sampling=random",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed used when sampling=random",
    )
    parser.add_argument(
        "--max-variants-per-scenario",
        type=int,
        default=1000,
        help="Upper bound of generated variants per logical scenario",
    )
    return parser.parse_args()


def _must_be_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _must_be_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _build_combinations(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    if not parameters:
        return [{}]
    names: list[str] = []
    values_by_name: list[list[Any]] = []
    for key, value in parameters.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("parameter names must be non-empty strings")
        choices = _must_be_list(value, f"parameter '{key}'")
        if not choices:
            raise ValueError(f"parameter '{key}' choices must not be empty")
        names.append(key)
        values_by_name.append(choices)

    rows: list[dict[str, Any]] = []
    for combo in itertools.product(*values_by_name):
        rows.append({name: combo[idx] for idx, name in enumerate(names)})
    return rows


def load_source_payload(args: argparse.Namespace) -> tuple[dict[str, Any], Path, str]:
    logical_scenarios = str(args.logical_scenarios).strip()
    scenario_language_profile = str(args.scenario_language_profile).strip()

    if bool(logical_scenarios) == bool(scenario_language_profile):
        raise ValueError(
            "provide exactly one of --logical-scenarios or --scenario-language-profile"
        )

    if logical_scenarios:
        source_path = Path(logical_scenarios).resolve()
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        payload_obj = _must_be_dict(payload, "logical scenario file")
        return payload_obj, source_path, "logical_scenarios"

    scenario_language_dir = Path(str(args.scenario_language_dir).strip()).resolve()
    profile_path = scenario_language_dir / f"{scenario_language_profile}.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    payload_obj = _must_be_dict(payload, "scenario language profile")
    profile_id = str(payload_obj.get("profile_id", "")).strip()
    if profile_id and profile_id != scenario_language_profile:
        raise ValueError(
            "scenario language profile_id mismatch: "
            f"expected={scenario_language_profile} actual={profile_id}"
        )
    return payload_obj, profile_path, "scenario_language_profile"


def _sample_rows(
    rows: list[dict[str, Any]],
    *,
    sampling: str,
    sample_size: int,
    max_variants_per_scenario: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    capped = rows[:max_variants_per_scenario]
    if sampling == "full":
        return capped
    if sample_size <= 0:
        raise ValueError("--sample-size must be positive when --sampling=random")
    target = min(sample_size, len(capped))
    indices = sorted(rng.sample(range(len(capped)), target))
    return [capped[idx] for idx in indices]


def generate_variants(
    payload: dict[str, Any],
    *,
    sampling: str,
    sample_size: int,
    max_variants_per_scenario: int,
    seed: int,
) -> list[dict[str, Any]]:
    logical_scenarios = _must_be_list(payload.get("logical_scenarios"), "logical_scenarios")
    rng = random.Random(seed)

    variants: list[dict[str, Any]] = []
    for logical in logical_scenarios:
        logical_obj = _must_be_dict(logical, "logical scenario entry")
        logical_id = str(logical_obj.get("scenario_id", "")).strip()
        if not logical_id:
            raise ValueError("logical scenario entry missing scenario_id")
        parameters = _must_be_dict(logical_obj.get("parameters"), "logical scenario parameters")
        base_rows = _build_combinations(parameters)
        selected_rows = _sample_rows(
            base_rows,
            sampling=sampling,
            sample_size=sample_size,
            max_variants_per_scenario=max_variants_per_scenario,
            rng=rng,
        )
        for idx, row in enumerate(selected_rows, start=1):
            variants.append(
                {
                    "scenario_id": f"{logical_id}_{idx:04d}",
                    "logical_scenario_id": logical_id,
                    "parameters": row,
                }
            )
    return variants


def main() -> int:
    try:
        args = parse_args()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        payload_obj, source_path, source_kind = load_source_payload(args)

        variants = generate_variants(
            payload_obj,
            sampling=args.sampling,
            sample_size=args.sample_size,
            max_variants_per_scenario=args.max_variants_per_scenario,
            seed=args.seed,
        )
        logical_scenarios = _must_be_list(payload_obj.get("logical_scenarios"), "logical_scenarios")
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_path": str(source_path),
            "source_kind": source_kind,
            "scenario_count": len(logical_scenarios),
            "variant_count": len(variants),
            "variants": variants,
        }
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        if source_kind == "scenario_language_profile":
            print(f"[ok] scenario_language_profile={args.scenario_language_profile}")
        print(f"[ok] logical_scenario_count={len(logical_scenarios)}")
        print(f"[ok] generated_variant_count={len(variants)}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] generate_scenario_variants.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
