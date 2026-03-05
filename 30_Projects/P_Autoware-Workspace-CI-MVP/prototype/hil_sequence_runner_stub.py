#!/usr/bin/env python3
"""Generate a minimal HIL test schedule manifest from interface + sequence inputs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import resolve_step_summary_file_from_env, write_ci_error_summary


HIL_INTERFACE_SCHEMA_VERSION_V0 = "hil_interface_v0"
HIL_TEST_SEQUENCE_SCHEMA_VERSION_V0 = "hil_test_sequence_v0"
HIL_SCHEDULE_SCHEMA_VERSION_V0 = "hil_schedule_manifest_v0"
ALLOWED_CHANNEL_DIRECTIONS = {"sim_to_hil", "hil_to_sim"}
ALLOWED_TRIGGER_TYPES = {"time_offset_sec", "event"}
ERROR_PHASE = "resolve_inputs"


def _emit_ci_error(*, source: str, message: str, step_summary_file: str, phase: str) -> None:
    normalized = str(message).strip() or "unknown_error"
    print(f"[error] {source}: {normalized}", file=sys.stderr)
    if not str(step_summary_file).strip():
        return
    write_ci_error_summary(
        source=source,
        phase=phase,
        message=normalized,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build HIL schedule scaffold from interface and sequence JSON")
    parser.add_argument("--interface", required=True, help="HIL interface JSON path")
    parser.add_argument("--sequence", required=True, help="HIL test sequence JSON path")
    parser.add_argument(
        "--max-runtime-sec",
        type=float,
        default=0.0,
        help="Optional runtime upper bound in seconds (0 to disable)",
    )
    parser.add_argument("--out", required=True, help="Output schedule manifest JSON path")
    return parser.parse_args()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _as_nonempty_text(value: Any, *, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _as_nonnegative_float(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if number < 0.0:
        raise ValueError(f"{field} must be >= 0")
    return number


def _validate_interface(payload: dict[str, Any]) -> tuple[str, list[str]]:
    schema_version = _as_nonempty_text(payload.get("interface_schema_version"), field="interface_schema_version")
    if schema_version != HIL_INTERFACE_SCHEMA_VERSION_V0:
        raise ValueError(
            f"interface_schema_version must be {HIL_INTERFACE_SCHEMA_VERSION_V0}"
        )

    channels = payload.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ValueError("channels must be a non-empty list")

    channel_ids: list[str] = []
    seen_channel_ids: set[str] = set()
    for index, channel in enumerate(channels):
        if not isinstance(channel, dict):
            raise ValueError(f"channels[{index}] must be an object")
        channel_id = _as_nonempty_text(channel.get("channel_id"), field=f"channels[{index}].channel_id")
        if channel_id in seen_channel_ids:
            raise ValueError(f"duplicate channel_id: {channel_id}")
        seen_channel_ids.add(channel_id)
        direction = _as_nonempty_text(channel.get("direction"), field=f"channels[{index}].direction")
        if direction not in ALLOWED_CHANNEL_DIRECTIONS:
            raise ValueError(
                f"channels[{index}].direction must be one of {sorted(ALLOWED_CHANNEL_DIRECTIONS)}"
            )
        _as_nonempty_text(channel.get("signal_type"), field=f"channels[{index}].signal_type")
        channel_ids.append(channel_id)
    return schema_version, channel_ids


def _validate_sequence(payload: dict[str, Any], *, channel_ids: set[str]) -> tuple[str, str, list[dict[str, Any]]]:
    schema_version = _as_nonempty_text(payload.get("sequence_schema_version"), field="sequence_schema_version")
    if schema_version != HIL_TEST_SEQUENCE_SCHEMA_VERSION_V0:
        raise ValueError(
            f"sequence_schema_version must be {HIL_TEST_SEQUENCE_SCHEMA_VERSION_V0}"
        )
    scenario_id = _as_nonempty_text(payload.get("scenario_id"), field="scenario_id")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")

    normalized_steps: list[dict[str, Any]] = []
    seen_step_ids: set[str] = set()
    last_time_offset_sec: float | None = None
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{index}] must be an object")
        step_id = _as_nonempty_text(step.get("step_id"), field=f"steps[{index}].step_id")
        if step_id in seen_step_ids:
            raise ValueError(f"duplicate step_id: {step_id}")
        seen_step_ids.add(step_id)

        trigger = step.get("trigger")
        if not isinstance(trigger, dict):
            raise ValueError(f"steps[{index}].trigger must be an object")
        trigger_type = _as_nonempty_text(trigger.get("trigger_type"), field=f"steps[{index}].trigger.trigger_type")
        if trigger_type not in ALLOWED_TRIGGER_TYPES:
            raise ValueError(
                f"steps[{index}].trigger.trigger_type must be one of {sorted(ALLOWED_TRIGGER_TYPES)}"
            )
        trigger_value = trigger.get("trigger_value")
        if trigger_type == "time_offset_sec":
            trigger_value = _as_nonnegative_float(
                trigger_value,
                field=f"steps[{index}].trigger.trigger_value",
            )
            if last_time_offset_sec is not None and trigger_value < last_time_offset_sec:
                raise ValueError(
                    "steps time_offset_sec trigger_value must be non-decreasing: "
                    f"steps[{index}].trigger.trigger_value={trigger_value} "
                    f"< previous={last_time_offset_sec}"
                )
            last_time_offset_sec = trigger_value
        else:
            trigger_value = _as_nonempty_text(
                trigger_value,
                field=f"steps[{index}].trigger.trigger_value",
            )

        action = step.get("action")
        if not isinstance(action, dict):
            raise ValueError(f"steps[{index}].action must be an object")
        target_channel_id = _as_nonempty_text(
            action.get("target_channel_id"),
            field=f"steps[{index}].action.target_channel_id",
        )
        if target_channel_id not in channel_ids:
            raise ValueError(
                f"steps[{index}].action.target_channel_id is not defined in interface: {target_channel_id}"
            )
        if "value" not in action:
            raise ValueError(f"steps[{index}].action.value is required")

        duration_sec = _as_nonnegative_float(step.get("duration_sec", 0.0), field=f"steps[{index}].duration_sec")
        normalized_steps.append(
            {
                "step_id": step_id,
                "trigger_type": trigger_type,
                "trigger_value": trigger_value,
                "target_channel_id": target_channel_id,
                "duration_sec": duration_sec,
            }
        )
    return schema_version, scenario_id, normalized_steps


def main() -> int:
    try:
        args = parse_args()
        max_runtime_sec = _as_nonnegative_float(args.max_runtime_sec, field="max-runtime-sec")
        interface_path = Path(args.interface).resolve()
        sequence_path = Path(args.sequence).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        interface_payload = _load_json_object(interface_path, "interface")
        sequence_payload = _load_json_object(sequence_path, "sequence")
        interface_schema_version, channel_ids = _validate_interface(interface_payload)
        sequence_schema_version, scenario_id, steps = _validate_sequence(
            sequence_payload,
            channel_ids=set(channel_ids),
        )
        estimated_runtime_sec = round(sum(float(step["duration_sec"]) for step in steps), 6)
        runtime_within_limit = True
        if max_runtime_sec > 0.0 and estimated_runtime_sec > max_runtime_sec:
            runtime_within_limit = False
            raise ValueError(
                "estimated runtime exceeds max-runtime-sec: "
                f"{estimated_runtime_sec} > {max_runtime_sec}"
            )

        manifest = {
            "hil_schedule_schema_version": HIL_SCHEDULE_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scenario_id": scenario_id,
            "interface_schema_version": interface_schema_version,
            "sequence_schema_version": sequence_schema_version,
            "channel_count": len(channel_ids),
            "step_count": len(steps),
            "estimated_runtime_sec": estimated_runtime_sec,
            "max_runtime_sec": max_runtime_sec,
            "runtime_within_limit": runtime_within_limit,
            "steps": steps,
        }
        out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] step_count={len(steps)}")
        print(f"[ok] channel_count={len(channel_ids)}")
        print(f"[ok] estimated_runtime_sec={estimated_runtime_sec}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        _emit_ci_error(
            source="hil_sequence_runner_stub.py",
            message=str(exc),
            step_summary_file=resolve_step_summary_file_from_env(),
            phase=ERROR_PHASE,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
