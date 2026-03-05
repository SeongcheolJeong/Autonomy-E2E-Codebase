#!/usr/bin/env python3
"""Probe runtime availability for AWSIM/CARLA launch-manifest integration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from ci_error_summary import write_ci_error_summary


SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0 = "sim_runtime_probe_v0"
SIM_RUNTIME_LAUNCH_MANIFEST_SCHEMA_VERSION_V0 = "sim_runtime_launch_manifest_v0"
ALLOWED_RUNTIMES = {"awsim", "carla"}
ERROR_SOURCE = "sim_runtime_probe_runner.py"
ERROR_PHASE = "resolve_inputs"
DEFAULT_TIMEOUT_SEC = 5.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe runtime availability using launch-manifest contract")
    parser.add_argument("--runtime", required=True, help="Runtime target: awsim|carla")
    parser.add_argument("--launch-manifest", required=True, help="Runtime launch manifest JSON path")
    parser.add_argument(
        "--runtime-bin",
        default="",
        help=(
            "Optional runtime executable path/name override. "
            "Can be a binary path, a runtime root directory, or a PATH command name."
        ),
    )
    parser.add_argument("--probe-flag", default="", help="Optional single probe argument (legacy compatibility)")
    parser.add_argument(
        "--probe-arg",
        action="append",
        default=[],
        help="Probe command argument (repeatable). Overrides --probe-flag when set.",
    )
    parser.add_argument("--timeout-sec", default="5", help="Probe command timeout (seconds, >0)")
    parser.add_argument(
        "--execute-probe",
        action="store_true",
        help="Run runtime probe command when runtime binary is available",
    )
    parser.add_argument(
        "--require-availability",
        action="store_true",
        help="Fail when runtime binary is unavailable or probe command fails",
    )
    parser.add_argument("--out", required=True, help="Output probe report JSON path")
    return parser.parse_args()


def _normalize_runtime(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in ALLOWED_RUNTIMES:
        allowed = ", ".join(sorted(ALLOWED_RUNTIMES))
        raise ValueError(f"runtime must be one of: {allowed}; got: {value}")
    return normalized


def _parse_positive_float(value: str, *, field: str) -> float:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} must be a positive number")
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a positive number, got: {value}") from exc
    if parsed <= 0.0:
        raise ValueError(f"{field} must be > 0, got: {parsed}")
    return parsed


def _load_json_object(path: Path, *, subject: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return payload


def _default_runtime_bin(runtime: str) -> str:
    if runtime == "awsim":
        return "awsim"
    return "CarlaUE4.sh"


def _runtime_entrypoint_globs(runtime: str) -> list[str]:
    if runtime == "awsim":
        return [
            "AWSIM*.x86_64",
            "**/AWSIM*.x86_64",
            "AWSIM*.exe",
            "**/AWSIM*.exe",
            "AWSIM.app/Contents/MacOS/*",
            "**/AWSIM.app/Contents/MacOS/*",
            "AWSIM",
            "**/AWSIM",
        ]
    return ["CarlaUE4.sh", "**/CarlaUE4.sh"]


def _ensure_executable(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    current_mode = path.stat().st_mode
    executable_mode = current_mode
    if current_mode & stat.S_IRUSR:
        executable_mode |= stat.S_IXUSR
    if current_mode & stat.S_IRGRP:
        executable_mode |= stat.S_IXGRP
    if current_mode & stat.S_IROTH:
        executable_mode |= stat.S_IXOTH
    if executable_mode == current_mode:
        return False
    os.chmod(path, executable_mode)
    return True


def _resolve_runtime_bin_from_dir(directory: Path, *, runtime: str) -> tuple[str, bool, list[str]]:
    checked_candidates: list[str] = []
    seen_candidates: set[str] = set()
    for pattern in _runtime_entrypoint_globs(runtime):
        for candidate in sorted(directory.glob(pattern)):
            if not candidate.exists() or not candidate.is_file():
                continue
            resolved = str(candidate.resolve())
            if resolved in seen_candidates:
                continue
            seen_candidates.add(resolved)
            checked_candidates.append(resolved)

    for candidate_text in checked_candidates:
        candidate = Path(candidate_text)
        if os.access(candidate, os.X_OK):
            return candidate_text, False, checked_candidates
        try:
            made_executable = _ensure_executable(candidate)
        except OSError:
            made_executable = False
        if made_executable and os.access(candidate, os.X_OK):
            return candidate_text, True, checked_candidates
    return "", False, checked_candidates


def _resolve_runtime_bin(
    candidate: str,
    *,
    runtime: str,
) -> tuple[bool, str, bool, list[str], str]:
    text = str(candidate).strip()
    if not text:
        return False, "", False, [], "empty"

    # Prioritize local paths first, even when no separator is present.
    candidate_path = Path(text).expanduser()
    if candidate_path.exists():
        resolved_path = candidate_path.resolve()
        if resolved_path.is_dir():
            resolved_bin, made_executable, checked_candidates = _resolve_runtime_bin_from_dir(
                resolved_path,
                runtime=runtime,
            )
            if resolved_bin:
                return True, resolved_bin, made_executable, checked_candidates, "directory_glob"
            return False, "", False, checked_candidates, "directory_glob_missing"
        if resolved_path.is_file():
            made_executable = False
            if not os.access(resolved_path, os.X_OK):
                try:
                    made_executable = _ensure_executable(resolved_path)
                except OSError:
                    made_executable = False
            if os.access(resolved_path, os.X_OK):
                return True, str(resolved_path), made_executable, [str(resolved_path)], "path_file"
            return False, "", made_executable, [str(resolved_path)], "path_not_executable"
        return False, "", False, [str(resolved_path)], "path_not_file"

    if os.path.sep in text or (os.path.altsep and os.path.altsep in text) or candidate_path.is_absolute():
        return False, "", False, [str(candidate_path.resolve())], "path_missing"

    resolved = shutil.which(text)
    if not resolved:
        return False, "", False, [], "which_missing"
    resolved_path = Path(resolved).resolve()
    return True, str(resolved_path), False, [str(resolved_path)], "which"


def _resolve_probe_args(
    *,
    args: argparse.Namespace,
    launch_manifest_payload: dict[str, Any],
) -> tuple[list[str], str]:
    explicit_args = [str(item).strip() for item in list(args.probe_arg or []) if str(item).strip()]
    if explicit_args:
        return explicit_args, "probe-arg"

    probe_flag = str(args.probe_flag).strip()
    if probe_flag:
        return [probe_flag], "probe-flag"

    runtime_contract_raw = launch_manifest_payload.get("runtime_contract", {})
    runtime_contract = runtime_contract_raw if isinstance(runtime_contract_raw, dict) else {}
    contract_probe_args_raw = runtime_contract.get("probe_args", [])
    if isinstance(contract_probe_args_raw, list):
        contract_probe_args = [str(item).strip() for item in contract_probe_args_raw if str(item).strip()]
        if contract_probe_args:
            return contract_probe_args, "launch_manifest.runtime_contract.probe_args"
    contract_probe_flag = str(runtime_contract.get("probe_flag", "")).strip()
    if contract_probe_flag:
        return [contract_probe_flag], "launch_manifest.runtime_contract.probe_flag"

    return ["--help"], "default"


def _excerpt(text: str, *, limit: int = 400) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _run_probe(*, runtime_bin: str, probe_args: list[str], timeout_sec: float) -> tuple[int, str, str]:
    cmd = [runtime_bin]
    cmd.extend([str(item).strip() for item in probe_args if str(item).strip()])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        return int(proc.returncode), str(proc.stdout), str(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")
        timeout_note = f"probe command timed out after {timeout_sec:.3f}s"
        if stderr:
            stderr = stderr + "\n" + timeout_note
        else:
            stderr = timeout_note
        return 124, stdout, stderr
    except OSError as exc:
        return 126, "", f"{type(exc).__name__}: {exc}"


def _is_acceptable_probe_result(
    *,
    runtime: str,
    probe_args: list[str],
    returncode: int,
    stderr: str,
) -> bool:
    if int(returncode) == 0:
        return True
    # AWSIM help probes can return code 1 with no stderr while still proving binary executability.
    normalized_probe_args = [str(item).strip() for item in probe_args if str(item).strip()]
    is_help_probe = len(normalized_probe_args) == 1 and normalized_probe_args[0] == "--help"
    if runtime == "awsim" and is_help_probe:
        return int(returncode) == 1 and not str(stderr).strip()
    # CARLA help probes can raise SIGILL(132) on emulated linux/amd64 runners (for example, Apple Silicon
    # Docker translation) while still proving that the runtime entrypoint is resolvable and executable.
    if runtime == "carla" and is_help_probe:
        return int(returncode) == 132 and "illegal instruction" in str(stderr).strip().lower()
    return False


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _shell_join(parts: list[str]) -> str:
    return " ".join(str(part) for part in parts if str(part))


def main() -> int:
    try:
        args = parse_args()
        runtime = _normalize_runtime(args.runtime)
        timeout_sec = _parse_positive_float(args.timeout_sec, field="timeout-sec")
        launch_manifest_path = Path(args.launch_manifest).resolve()
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        launch_manifest_payload = _load_json_object(
            launch_manifest_path,
            subject="launch manifest",
        )
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

        requested_runtime_bin = str(args.runtime_bin).strip() or _default_runtime_bin(runtime)
        (
            runtime_available,
            resolved_runtime_bin,
            runtime_bin_made_executable,
            runtime_bin_candidates_checked,
            runtime_bin_resolution_strategy,
        ) = _resolve_runtime_bin(
            requested_runtime_bin,
            runtime=runtime,
        )
        runtime_bin_size_bytes = 0
        runtime_bin_mtime_utc = ""
        runtime_bin_sha256 = ""
        if runtime_available and resolved_runtime_bin:
            resolved_runtime_path = Path(resolved_runtime_bin)
            if resolved_runtime_path.exists() and resolved_runtime_path.is_file():
                try:
                    runtime_bin_size_bytes = int(resolved_runtime_path.stat().st_size)
                except OSError:
                    runtime_bin_size_bytes = 0
                try:
                    runtime_bin_mtime_utc = datetime.fromtimestamp(
                        float(resolved_runtime_path.stat().st_mtime),
                        tz=timezone.utc,
                    ).isoformat()
                except OSError:
                    runtime_bin_mtime_utc = ""
                try:
                    runtime_bin_sha256 = _sha256_file(resolved_runtime_path)
                except OSError:
                    runtime_bin_sha256 = ""

        probe_executed = False
        probe_returncode: int | None = None
        probe_stdout = ""
        probe_stderr = ""
        probe_command = ""
        probe_args: list[str] = []
        probe_args_source = ""
        probe_duration_ms = 0
        probe_returncode_acceptable = False
        if args.execute_probe and runtime_available and resolved_runtime_bin:
            probe_executed = True
            probe_args, probe_args_source = _resolve_probe_args(
                args=args,
                launch_manifest_payload=launch_manifest_payload,
            )
            probe_command = _shell_join([resolved_runtime_bin, *probe_args]) if probe_args else resolved_runtime_bin
            probe_started_at = perf_counter()
            probe_returncode, probe_stdout, probe_stderr = _run_probe(
                runtime_bin=resolved_runtime_bin,
                probe_args=probe_args,
                timeout_sec=timeout_sec,
            )
            probe_duration_ms = max(0, int(round((perf_counter() - probe_started_at) * 1000)))
            probe_returncode_acceptable = _is_acceptable_probe_result(
                runtime=runtime,
                probe_args=probe_args,
                returncode=int(probe_returncode or 0),
                stderr=probe_stderr,
            )

        if args.require_availability and not runtime_available:
            raise ValueError(
                f"runtime binary unavailable for runtime={runtime}: {requested_runtime_bin}"
            )
        if args.require_availability and probe_executed and not probe_returncode_acceptable:
            raise ValueError(
                "runtime probe command failed for runtime="
                f"{runtime} returncode={int(probe_returncode or 0)}"
            )

        output_payload = {
            "sim_runtime_probe_schema_version": SIM_RUNTIME_PROBE_SCHEMA_VERSION_V0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime": runtime,
            "launch_manifest_path": str(launch_manifest_path),
            "launch_manifest_schema_version": launch_schema,
            "launch_mode": str(launch_manifest_payload.get("mode", "")).strip(),
            "runtime_bin": requested_runtime_bin,
            "runtime_bin_resolved": resolved_runtime_bin,
            "runtime_bin_made_executable": bool(runtime_bin_made_executable),
            "runtime_bin_resolution_strategy": runtime_bin_resolution_strategy,
            "runtime_bin_candidates_checked": runtime_bin_candidates_checked,
            "runtime_bin_size_bytes": int(runtime_bin_size_bytes),
            "runtime_bin_mtime_utc": runtime_bin_mtime_utc,
            "runtime_bin_sha256": runtime_bin_sha256,
            "runtime_available": bool(runtime_available),
            "require_availability": bool(args.require_availability),
            "execute_probe": bool(args.execute_probe),
            "probe_flag": probe_args[0] if len(probe_args) == 1 else "",
            "probe_args": probe_args,
            "probe_args_source": probe_args_source,
            "probe_timeout_sec": float(timeout_sec),
            "probe_executed": bool(probe_executed),
            "probe_command": probe_command,
            "probe_duration_ms": int(probe_duration_ms),
            "probe_returncode": probe_returncode,
            "probe_returncode_acceptable": bool(probe_returncode_acceptable),
            "probe_stdout_excerpt": _excerpt(probe_stdout),
            "probe_stderr_excerpt": _excerpt(probe_stderr),
            "runner_host": str(platform.node()).strip(),
            "runner_platform": str(platform.platform()).strip(),
            "runner_python": str(sys.version.split()[0]).strip(),
        }
        out_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"[ok] runtime={runtime}")
        print(f"[ok] runtime_available={str(runtime_available).lower()}")
        if probe_executed:
            print(f"[ok] probe_returncode={int(probe_returncode or 0)}")
        print(f"[ok] out={out_path}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        print(f"[error] sim_runtime_probe_runner.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
