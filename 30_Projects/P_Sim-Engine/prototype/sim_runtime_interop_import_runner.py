#!/usr/bin/env python3
"""Import OpenSCENARIO/OpenDRIVE artifacts and validate manifest-aligned interop roundtrip."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


SIM_RUNTIME_INTEROP_IMPORT_SCHEMA_VERSION_V0 = "sim_runtime_interop_import_v0"
SIM_RUNTIME_INTEROP_EXPORT_SCHEMA_VERSION_V0 = "sim_runtime_interop_export_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
ALLOWED_RUNTIMES = {"awsim", "carla"}
ERROR_SOURCE = "sim_runtime_interop_import_runner.py"
ERROR_PHASE = "resolve_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import OpenSCENARIO/OpenDRIVE runtime interop artifacts")
    parser.add_argument("--runtime", required=True, help="Runtime target: awsim|carla")
    parser.add_argument("--launch-manifest", required=True, help="Runtime launch-manifest JSON path")
    parser.add_argument("--xosc", required=True, help="OpenSCENARIO (.xosc) path to import")
    parser.add_argument("--xodr", required=True, help="OpenDRIVE (.xodr) path to import")
    parser.add_argument(
        "--export-report",
        default="",
        help="Optional runtime interop export report JSON path for roundtrip consistency checks",
    )
    parser.add_argument(
        "--require-manifest-consistency",
        action="store_true",
        help="Fail when imported actor count mismatches launch manifest actor count",
    )
    parser.add_argument(
        "--require-export-consistency",
        action="store_true",
        help="Fail when imported OpenSCENARIO/OpenDRIVE does not match export report expectations",
    )
    parser.add_argument("--out", required=True, help="Output import report JSON path")
    return parser.parse_args()


def _normalize_runtime(value: str) -> str:
    runtime = str(value).strip().lower()
    if runtime not in ALLOWED_RUNTIMES:
        allowed = ", ".join(sorted(ALLOWED_RUNTIMES))
        raise ValueError(f"runtime must be one of: {allowed}; got: {value}")
    return runtime


def _load_json_object(path: Path, *, subject: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return payload


def _xml_local_name(tag: str) -> str:
    raw = str(tag).strip()
    if "}" in raw:
        return raw.rsplit("}", 1)[1].strip()
    return raw


def _parse_xml_root(path: Path, *, subject: str, expected_root: str) -> ET.Element:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"{subject} is not valid XML: {path}") from exc
    root = tree.getroot()
    root_name = _xml_local_name(root.tag)
    if root_name != expected_root:
        raise ValueError(f"{subject} root tag must be {expected_root}, got: {root_name or '<empty>'}")
    return root


def _parse_float_or_default(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_int_or_default(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _extract_xosc_actor_names(xosc_root: ET.Element) -> list[str]:
    names: list[str] = []
    for idx, node in enumerate(xosc_root.iter(), start=1):
        if _xml_local_name(node.tag) != "ScenarioObject":
            continue
        name = str(node.attrib.get("name", "")).strip()
        names.append(name if name else f"scenario_object_{idx:03d}")
    return names


def _count_xml_nodes(root: ET.Element, *, local_name: str) -> int:
    target = str(local_name).strip()
    if not target:
        return 0
    count = 0
    for node in root.iter():
        if _xml_local_name(node.tag) == target:
            count += 1
    return count


def _sum_xodr_road_lengths(xodr_root: ET.Element) -> float:
    total = 0.0
    for node in xodr_root.iter():
        if _xml_local_name(node.tag) != "road":
            continue
        total += max(0.0, _parse_float_or_default(node.attrib.get("length"), default=0.0))
    return float(total)


def main() -> int:
    try:
        args = parse_args()
        runtime = _normalize_runtime(args.runtime)
        launch_manifest_path = Path(args.launch_manifest).resolve()
        xosc_path = Path(args.xosc).resolve()
        xodr_path = Path(args.xodr).resolve()
        export_report_raw = str(args.export_report).strip()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if bool(args.require_export_consistency) and not export_report_raw:
            raise ValueError("--require-export-consistency requires --export-report")

        launch_manifest_payload = _load_json_object(launch_manifest_path, subject="launch manifest")
        launch_schema = str(
            launch_manifest_payload.get("sim_runtime_launch_manifest_schema_version", "")
        ).strip()
        if launch_schema and launch_schema != SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0:
            raise ValueError(
                "launch manifest schema must be "
                f"{SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0}, got: {launch_schema}"
            )
        launch_runtime = str(launch_manifest_payload.get("runtime", "")).strip().lower()
        if launch_runtime and launch_runtime != runtime:
            raise ValueError(
                f"runtime mismatch between --runtime ({runtime}) and launch manifest ({launch_runtime})"
            )

        actors_raw = launch_manifest_payload.get("actors", [])
        if not isinstance(actors_raw, list) or not actors_raw:
            raise ValueError("launch manifest actors must be a non-empty list")
        actors = [row for row in actors_raw if isinstance(row, dict)]
        if not actors:
            raise ValueError("launch manifest actors must contain object rows")
        actor_count_manifest = int(len(actors))

        sensor_streams_raw = launch_manifest_payload.get("sensor_streams", [])
        if not isinstance(sensor_streams_raw, list) or not sensor_streams_raw:
            raise ValueError("launch manifest sensor_streams must be a non-empty list")
        sensor_streams = [row for row in sensor_streams_raw if isinstance(row, dict)]
        if not sensor_streams:
            raise ValueError("launch manifest sensor_streams must contain object rows")
        sensor_stream_count_manifest = int(len(sensor_streams))

        xosc_root = _parse_xml_root(xosc_path, subject="OpenSCENARIO", expected_root="OpenSCENARIO")
        xodr_root = _parse_xml_root(xodr_path, subject="OpenDRIVE", expected_root="OpenDRIVE")
        xosc_actor_names = _extract_xosc_actor_names(xosc_root)
        xosc_entity_count = int(len(xosc_actor_names))
        xosc_story_count = int(_count_xml_nodes(xosc_root, local_name="Story"))
        xodr_road_count = int(_count_xml_nodes(xodr_root, local_name="road"))
        xodr_junction_count = int(_count_xml_nodes(xodr_root, local_name="junction"))
        xodr_total_road_length_m = float(round(_sum_xodr_road_lengths(xodr_root), 6))

        if xosc_entity_count <= 0:
            raise ValueError("OpenSCENARIO import must contain at least one ScenarioObject")
        if xodr_road_count <= 0:
            raise ValueError("OpenDRIVE import must contain at least one road")

        manifest_consistent = bool(actor_count_manifest == xosc_entity_count)
        if bool(args.require_manifest_consistency) and not manifest_consistent:
            raise ValueError(
                "interop import actor count mismatch: "
                f"manifest={actor_count_manifest}, imported={xosc_entity_count}"
            )

        imported_actor_count = min(actor_count_manifest, xosc_entity_count)
        imported_actor_count = max(1, imported_actor_count)

        export_report_path = Path(export_report_raw).resolve() if export_report_raw else None
        export_report_checked = bool(export_report_path is not None)
        export_consistency_mismatch_reasons: list[str] = []
        export_actor_count_manifest = 0
        export_xosc_entity_count = 0
        export_xodr_road_count = 0
        export_generated_road_length_m = 0.0
        export_consistent = False
        if export_report_path is not None:
            export_report_payload = _load_json_object(export_report_path, subject="interop export report")
            export_schema = str(
                export_report_payload.get("sim_runtime_interop_export_schema_version", "")
            ).strip()
            if export_schema and export_schema != SIM_RUNTIME_INTEROP_EXPORT_SCHEMA_VERSION_V0:
                raise ValueError(
                    "interop export report schema must be "
                    f"{SIM_RUNTIME_INTEROP_EXPORT_SCHEMA_VERSION_V0}, got: {export_schema}"
                )
            export_runtime = str(export_report_payload.get("runtime", "")).strip().lower()
            if export_runtime and export_runtime != runtime:
                raise ValueError(
                    "runtime mismatch between --runtime and export report: "
                    f"runtime={runtime}, export_runtime={export_runtime}"
                )

            export_xosc_path_text = str(export_report_payload.get("xosc_path", "")).strip()
            export_xodr_path_text = str(export_report_payload.get("xodr_path", "")).strip()
            if export_xosc_path_text and Path(export_xosc_path_text).resolve() != xosc_path:
                export_consistency_mismatch_reasons.append(
                    "xosc_path_mismatch:"
                    f"expected={Path(export_xosc_path_text).resolve()},observed={xosc_path}"
                )
            if export_xodr_path_text and Path(export_xodr_path_text).resolve() != xodr_path:
                export_consistency_mismatch_reasons.append(
                    "xodr_path_mismatch:"
                    f"expected={Path(export_xodr_path_text).resolve()},observed={xodr_path}"
                )

            export_actor_count_manifest = _parse_int_or_default(
                export_report_payload.get("actor_count_manifest"),
                default=0,
            )
            export_xosc_entity_count = _parse_int_or_default(
                export_report_payload.get("xosc_entity_count"),
                default=0,
            )
            export_xodr_road_count = _parse_int_or_default(
                export_report_payload.get("xodr_road_count"),
                default=0,
            )
            export_generated_road_length_m = float(
                round(
                    _parse_float_or_default(
                        export_report_payload.get("generated_road_length_m"),
                        default=0.0,
                    ),
                    6,
                )
            )
            if export_actor_count_manifest > 0 and actor_count_manifest != export_actor_count_manifest:
                export_consistency_mismatch_reasons.append(
                    "actor_count_manifest_mismatch:"
                    f"expected={export_actor_count_manifest},observed={actor_count_manifest}"
                )
            if export_xosc_entity_count > 0 and xosc_entity_count != export_xosc_entity_count:
                export_consistency_mismatch_reasons.append(
                    "xosc_entity_count_mismatch:"
                    f"expected={export_xosc_entity_count},observed={xosc_entity_count}"
                )
            if export_xodr_road_count > 0 and xodr_road_count != export_xodr_road_count:
                export_consistency_mismatch_reasons.append(
                    "xodr_road_count_mismatch:"
                    f"expected={export_xodr_road_count},observed={xodr_road_count}"
                )
            if export_generated_road_length_m > 0.0 and abs(
                xodr_total_road_length_m - export_generated_road_length_m
            ) > 1e-6:
                export_consistency_mismatch_reasons.append(
                    "xodr_total_road_length_m_mismatch:"
                    f"expected={export_generated_road_length_m:.6f},observed={xodr_total_road_length_m:.6f}"
                )
            export_consistent = not export_consistency_mismatch_reasons
            if bool(args.require_export_consistency) and not export_consistent:
                raise ValueError(
                    "interop import/export consistency mismatch: "
                    + "; ".join(export_consistency_mismatch_reasons)
                )

        output_payload = {
            "sim_runtime_interop_import_schema_version": SIM_RUNTIME_INTEROP_IMPORT_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
            "launch_manifest_path": str(launch_manifest_path),
            "launch_manifest_schema_version": launch_schema,
            "xosc_path": str(xosc_path),
            "xodr_path": str(xodr_path),
            "actor_count_manifest": int(actor_count_manifest),
            "sensor_stream_count_manifest": int(sensor_stream_count_manifest),
            "xosc_entity_count": int(xosc_entity_count),
            "xosc_story_count": int(xosc_story_count),
            "xosc_actor_names": xosc_actor_names,
            "xodr_road_count": int(xodr_road_count),
            "xodr_junction_count": int(xodr_junction_count),
            "xodr_total_road_length_m": float(xodr_total_road_length_m),
            "imported_actor_count": int(imported_actor_count),
            "manifest_consistent": bool(manifest_consistent),
            "require_manifest_consistency": bool(args.require_manifest_consistency),
            "export_report_path": str(export_report_path) if export_report_path is not None else "",
            "export_report_checked": bool(export_report_checked),
            "export_actor_count_manifest": int(export_actor_count_manifest),
            "export_xosc_entity_count": int(export_xosc_entity_count),
            "export_xodr_road_count": int(export_xodr_road_count),
            "export_generated_road_length_m": float(export_generated_road_length_m),
            "export_consistent": bool(export_consistent),
            "export_consistency_mismatch_reasons": export_consistency_mismatch_reasons,
            "require_export_consistency": bool(args.require_export_consistency),
            "import_status": "pass",
            "runner_host": str(platform.node()).strip(),
            "runner_platform": str(platform.platform()).strip(),
            "runner_python": str(sys.version.split()[0]).strip(),
        }
        out_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        print(f"[ok] runtime={runtime}")
        print(f"[ok] manifest_consistent={str(manifest_consistent).lower()}")
        if export_report_checked:
            print(f"[ok] export_consistent={str(export_consistent).lower()}")
        print("[ok] import_status=pass")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] sim_runtime_interop_import_runner.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
