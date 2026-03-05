#!/usr/bin/env python3
"""Prepare runtime binary/map/scenario assets for AWSIM/CARLA integration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import shlex
import stat
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ci_error_summary import write_ci_error_summary


RUNTIME_ASSETS_SCHEMA_VERSION_V0 = "sim_runtime_assets_manifest_v0"
RUNTIME_ASSETS_RESOLVED_SCHEMA_VERSION_V0 = "sim_runtime_assets_resolved_v0"
ALLOWED_RUNTIMES = {"all", "carla", "awsim"}
ALLOWED_ARCHIVE_FORMATS = {"tar.gz", "zip", "directory"}
ALLOWED_ARCHIVE_SHA256_MODES = {"always", "verify_only", "never"}
ERROR_SOURCE = "prepare_runtime_assets.py"
ERROR_PHASE = "resolve_inputs"
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Prepare AWSIM/CARLA runtime asset bundles")
    parser.add_argument(
        "--manifest",
        default=str(root / "examples/runtime_assets_manifest_v0.json"),
        help="Runtime asset manifest JSON path",
    )
    parser.add_argument(
        "--runtime",
        choices=sorted(ALLOWED_RUNTIMES),
        default="all",
        help="Target runtime scope",
    )
    parser.add_argument(
        "--profile",
        default="lightweight",
        help="Asset profile selector (for example: lightweight|full)",
    )
    parser.add_argument(
        "--archives-root",
        default=str(root / "runtime_assets/_archives"),
        help="Archive cache directory",
    )
    parser.add_argument(
        "--assets-root",
        default=str(root / "runtime_assets"),
        help="Extracted asset root directory",
    )
    parser.add_argument(
        "--resolved-out",
        default=str(root / "runs/runtime_assets_resolved_v0.json"),
        help="Output path for resolved runtime asset manifest",
    )
    parser.add_argument(
        "--env-out",
        default="",
        help=(
            "Optional shell env file output (runtime single mode only). "
            "Includes SIM_RUNTIME_SCENE/SIM_RUNTIME_SENSOR_RIG/SIM_RUNTIME_PROBE_RUNTIME_BIN."
        ),
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download step and reuse existing archives/directories",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip archive extraction step",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Force re-download archives even when cache exists",
    )
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Force re-extract archive payloads even when extraction directory exists",
    )
    parser.add_argument(
        "--archive-sha256-mode",
        choices=sorted(ALLOWED_ARCHIVE_SHA256_MODES),
        default="always",
        help=(
            "Archive sha256 strategy: always=compute for all archive files, "
            "verify_only=compute only when expected sha256 is declared in manifest, "
            "never=skip routine archive sha256 computation (but still computes when expected sha256 is declared)."
        ),
    )
    parser.add_argument(
        "--require-runtime-bin",
        action="store_true",
        help="Fail if selected runtime has no resolved runtime binary candidate",
    )
    parser.add_argument(
        "--require-host-compatible",
        action="store_true",
        help="Fail if selected runtime binary target_platforms are incompatible with current host",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json_object(path: Path, *, subject: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return payload


def _ensure_nonempty_str(value: Any, *, field_path: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_path} must be a non-empty string")
    return text


def _ensure_str_list(value: Any, *, field_path: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_path} must be a list of strings")
    output: list[str] = []
    for idx, item in enumerate(value):
        output.append(_ensure_nonempty_str(item, field_path=f"{field_path}[{idx}]"))
    return output


def _host_platform_token() -> str:
    system = str(platform.system()).strip().lower()
    machine = str(platform.machine()).strip().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        arch = machine or "unknown"
    system_norm = system or "unknown"
    return f"{system_norm}_{arch}"


def _safe_archive_name(url: str, fallback: str) -> str:
    parsed = urllib.parse.urlparse(url)
    basename = Path(parsed.path).name
    candidate = basename.strip() or fallback
    return candidate.replace("/", "_")


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _extract_tarball(*, archive_path: Path, out_dir: Path) -> None:
    with tarfile.open(archive_path, "r:*") as archive:
        out_dir_resolved = out_dir.resolve()
        for member in archive.getmembers():
            target = (out_dir / member.name).resolve()
            if not _is_relative_to(target, out_dir_resolved):
                raise ValueError(f"archive path traversal blocked: {member.name}")
        archive.extractall(path=out_dir)


def _extract_zip(*, archive_path: Path, out_dir: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as archive:
        out_dir_resolved = out_dir.resolve()
        for member in archive.infolist():
            target = (out_dir / member.filename).resolve()
            if not _is_relative_to(target, out_dir_resolved):
                raise ValueError(f"archive path traversal blocked: {member.filename}")
        archive.extractall(path=out_dir)


def _download_to_path(*, url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, out_path.open("wb") as output:
        while True:
            chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            output.write(chunk)


def _resolve_local_source(url: str) -> Path | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path)).expanduser().resolve()
    if parsed.scheme == "":
        return Path(url).expanduser().resolve()
    return None


def _prepare_archive(
    *,
    asset_id: str,
    url: str,
    archive_path: Path,
    archive_format: str,
    skip_download: bool,
    force_download: bool,
) -> tuple[str, bool]:
    local_source = _resolve_local_source(url)
    if archive_format == "directory":
        if local_source is None:
            raise ValueError(f"{asset_id}: archive_format=directory requires local path or file:// URL")
        if not local_source.exists() or not local_source.is_dir():
            raise ValueError(f"{asset_id}: directory source not found: {local_source}")
        return str(local_source), False

    if skip_download and archive_path.exists():
        return str(archive_path), False
    if archive_path.exists() and not force_download:
        return str(archive_path), False

    if local_source is not None:
        if not local_source.exists() or not local_source.is_file():
            raise ValueError(f"{asset_id}: archive source not found: {local_source}")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_source, archive_path)
        return str(archive_path), True

    _download_to_path(url=url, out_path=archive_path)
    return str(archive_path), True


def _extract_asset(
    *,
    asset_id: str,
    archive_format: str,
    archive_path_text: str,
    extract_dir: Path,
    skip_extract: bool,
    force_extract: bool,
) -> bool:
    if skip_extract:
        return False
    if archive_format == "directory":
        source_dir = Path(archive_path_text).resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            raise ValueError(f"{asset_id}: directory source missing: {source_dir}")
        if extract_dir.exists() and force_extract:
            shutil.rmtree(extract_dir)
        if extract_dir.exists():
            return False
        shutil.copytree(source_dir, extract_dir)
        return True

    archive_path = Path(archive_path_text).resolve()
    if not archive_path.exists():
        raise ValueError(f"{asset_id}: archive not found: {archive_path}")
    if extract_dir.exists() and force_extract:
        shutil.rmtree(extract_dir)
    if extract_dir.exists():
        return False
    extract_dir.mkdir(parents=True, exist_ok=True)
    if archive_format == "tar.gz":
        _extract_tarball(archive_path=archive_path, out_dir=extract_dir)
        return True
    if archive_format == "zip":
        _extract_zip(archive_path=archive_path, out_dir=extract_dir)
        return True
    raise ValueError(f"{asset_id}: unsupported archive_format: {archive_format}")


def _collect_hint_paths(root: Path, hints: list[str]) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        pattern = str(hint).strip()
        if not pattern:
            continue
        matches = sorted(root.glob(pattern))
        if matches:
            for path in matches:
                resolved = str(path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                collected.append(resolved)
            continue
        candidate = (root / pattern).resolve()
        if candidate.exists():
            resolved = str(candidate)
            if resolved not in seen:
                seen.add(resolved)
                collected.append(resolved)
    return collected


def _host_os(host_platform: str) -> str:
    token = str(host_platform).strip().lower()
    if token.startswith("linux_"):
        return "linux"
    if token.startswith("darwin_"):
        return "darwin"
    if token.startswith("windows_"):
        return "windows"
    return "unknown"


def _candidate_platform_hints(path_text: str) -> set[str]:
    lower = str(path_text).strip().lower()
    name = Path(lower).name
    hints: set[str] = set()
    if ".app/contents/macos/" in lower:
        hints.add("darwin")
    if name.endswith(".exe"):
        hints.add("windows")
    if name.endswith(".x86_64") or name.endswith(".sh"):
        hints.add("linux")
    return hints


def _entrypoint_runtime_bonus(path_text: str, *, runtime: str) -> int:
    lower = str(path_text).strip().lower()
    name = Path(lower).name
    if runtime == "carla":
        if name == "carlaue4.sh":
            return 30
        if "carlaue4" in name:
            return 15
        return 0
    if runtime == "awsim":
        if name.startswith("awsim"):
            return 30
        if "awsim" in lower:
            return 15
        return 0
    return 0


def _score_entrypoint_candidate(
    path_text: str,
    *,
    runtime: str,
    host_platform: str,
) -> tuple[int, set[str], bool]:
    hints = _candidate_platform_hints(path_text)
    host_os = _host_os(host_platform)
    host_match = host_os in hints if hints and host_os != "unknown" else False

    score = 0
    if host_match:
        score += 100
    elif hints and host_os != "unknown":
        score -= 60
    score += _entrypoint_runtime_bonus(path_text, runtime=runtime)
    return score, hints, host_match


def _select_runtime_entrypoint(
    *,
    candidates: list[str],
    runtime: str,
    host_platform: str,
) -> tuple[str, list[dict[str, Any]], str]:
    existing: list[str] = []
    seen: set[str] = set()
    for path_text in candidates:
        path = Path(path_text).resolve()
        resolved = str(path)
        if resolved in seen:
            continue
        if not path.exists() or not path.is_file():
            continue
        seen.add(resolved)
        existing.append(resolved)
    if not existing:
        return "", [], "no_candidates"

    scored_rows: list[dict[str, Any]] = []
    for path_text in existing:
        score, hints, host_match = _score_entrypoint_candidate(
            path_text,
            runtime=runtime,
            host_platform=host_platform,
        )
        scored_rows.append(
            {
                "path": path_text,
                "score": int(score),
                "platform_hints": sorted(hints),
                "host_match": bool(host_match),
            }
        )
    scored_rows.sort(key=lambda row: (-int(row.get("score", 0) or 0), str(row.get("path", ""))))
    selected = str(scored_rows[0].get("path", "")).strip()
    strategy = "scored_preference"
    if len(scored_rows) >= 2 and int(scored_rows[0].get("score", 0) or 0) == int(scored_rows[1].get("score", 0) or 0):
        strategy = "score_tie_lexicographic"
    return selected, scored_rows, strategy


def _ensure_executable(path_text: str) -> bool:
    path = Path(path_text).resolve()
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


def _resolve_default_path(manifest_path: Path, relative_path: str) -> str:
    path_text = str(relative_path).strip()
    if not path_text:
        return ""
    path = Path(path_text)
    if path.is_absolute():
        return str(path.resolve())
    return str((manifest_path.parent / path).resolve())


def _runtime_probe_args_default(runtime: str) -> list[str]:
    runtime_text = str(runtime).strip().lower()
    if runtime_text in {"carla", "awsim"}:
        return ["--help"]
    return []


def _write_env_file(
    *,
    path: Path,
    runtime: str,
    scene: str,
    sensor_rig: str,
    runtime_bin: str,
    runtime_probe_args_shlex: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/usr/bin/env bash",
        f"export SIM_RUNTIME={shlex.quote(runtime)}",
        f"export SIM_RUNTIME_SCENE={shlex.quote(scene)}",
        f"export SIM_RUNTIME_SENSOR_RIG={shlex.quote(sensor_rig)}",
        f"export SIM_RUNTIME_PROBE_RUNTIME_BIN={shlex.quote(runtime_bin)}",
        f"export SIM_RUNTIME_PROBE_ARGS_SHLEX={shlex.quote(runtime_probe_args_shlex)}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    try:
        args = parse_args()
        runtime = _ensure_nonempty_str(args.runtime, field_path="runtime").lower()
        profile = _ensure_nonempty_str(args.profile, field_path="profile")
        manifest_path = Path(args.manifest).resolve()
        archives_root = Path(args.archives_root).resolve()
        assets_root = Path(args.assets_root).resolve()
        resolved_out = Path(args.resolved_out).resolve()
        archive_sha256_mode = _ensure_nonempty_str(
            args.archive_sha256_mode,
            field_path="archive-sha256-mode",
        ).lower()
        if archive_sha256_mode not in ALLOWED_ARCHIVE_SHA256_MODES:
            allowed = ", ".join(sorted(ALLOWED_ARCHIVE_SHA256_MODES))
            raise ValueError(f"archive-sha256-mode must be one of: {allowed}")
        host_platform = _host_platform_token()

        payload = _load_json_object(manifest_path, subject="runtime asset manifest")
        manifest_schema = _ensure_nonempty_str(
            payload.get("runtime_assets_schema_version"),
            field_path="runtime_assets_schema_version",
        )
        if manifest_schema != RUNTIME_ASSETS_SCHEMA_VERSION_V0:
            raise ValueError(
                "runtime_assets_schema_version must be "
                f"{RUNTIME_ASSETS_SCHEMA_VERSION_V0}, got: {manifest_schema}"
            )

        assets_raw = payload.get("assets", [])
        if not isinstance(assets_raw, list) or not assets_raw:
            raise ValueError("assets must be a non-empty list")

        selected_assets: list[dict[str, Any]] = []
        for idx, item in enumerate(assets_raw):
            if not isinstance(item, dict):
                raise ValueError(f"assets[{idx}] must be an object")
            asset_id = _ensure_nonempty_str(item.get("asset_id"), field_path=f"assets[{idx}].asset_id")
            asset_runtime = _ensure_nonempty_str(item.get("runtime"), field_path=f"assets[{idx}].runtime").lower()
            if asset_runtime not in {"carla", "awsim"}:
                raise ValueError(f"assets[{idx}].runtime must be carla|awsim, got: {asset_runtime}")
            asset_profiles = _ensure_str_list(item.get("profiles", []), field_path=f"assets[{idx}].profiles")
            if profile not in asset_profiles:
                continue
            if runtime != "all" and asset_runtime != runtime:
                continue
            archive_format = _ensure_nonempty_str(
                item.get("archive_format"),
                field_path=f"assets[{idx}].archive_format",
            ).lower()
            if archive_format not in ALLOWED_ARCHIVE_FORMATS:
                raise ValueError(
                    f"assets[{idx}].archive_format must be one of: {', '.join(sorted(ALLOWED_ARCHIVE_FORMATS))}"
                )

            url = _ensure_nonempty_str(item.get("url"), field_path=f"assets[{idx}].url")
            archive_name = str(item.get("archive_name", "")).strip() or _safe_archive_name(url, f"{asset_id}.archive")
            extract_subdir = _ensure_nonempty_str(item.get("extract_subdir"), field_path=f"assets[{idx}].extract_subdir")

            selected_assets.append(
                {
                    "asset_id": asset_id,
                    "runtime": asset_runtime,
                    "kind": str(item.get("kind", "")).strip(),
                    "version": str(item.get("version", "")).strip(),
                    "url": url,
                    "profiles": asset_profiles,
                    "archive_format": archive_format,
                    "archive_name": archive_name,
                    "extract_subdir": extract_subdir,
                    "entrypoint_relpath": str(item.get("entrypoint_relpath", "")).strip(),
                    "entrypoint_glob": _ensure_str_list(
                        item.get("entrypoint_glob", []),
                        field_path=f"assets[{idx}].entrypoint_glob",
                    )
                    if isinstance(item.get("entrypoint_glob", []), list)
                    else [],
                    "map_hints": _ensure_str_list(item.get("map_hints", []), field_path=f"assets[{idx}].map_hints")
                    if isinstance(item.get("map_hints", []), list)
                    else [],
                    "scenario_hints": _ensure_str_list(
                        item.get("scenario_hints", []),
                        field_path=f"assets[{idx}].scenario_hints",
                    )
                    if isinstance(item.get("scenario_hints", []), list)
                    else [],
                    "target_platforms": _ensure_str_list(
                        item.get("target_platforms", []),
                        field_path=f"assets[{idx}].target_platforms",
                    )
                    if isinstance(item.get("target_platforms", []), list)
                    else [],
                    "sha256": str(item.get("sha256", "")).strip().lower(),
                }
            )

        if not selected_assets:
            raise ValueError(f"no assets selected for runtime={runtime} profile={profile}")

        runtime_defaults_raw = payload.get("runtime_defaults", {})
        runtime_defaults = runtime_defaults_raw if isinstance(runtime_defaults_raw, dict) else {}

        runtime_rows: dict[str, dict[str, Any]] = {}
        asset_rows: list[dict[str, Any]] = []

        archives_root.mkdir(parents=True, exist_ok=True)
        assets_root.mkdir(parents=True, exist_ok=True)

        for row in selected_assets:
            asset_id = str(row["asset_id"])
            asset_runtime = str(row["runtime"])
            archive_format = str(row["archive_format"])
            archive_path = (archives_root / str(row["archive_name"])).resolve()
            extract_dir = (assets_root / str(row["extract_subdir"])).resolve()

            archive_path_text, downloaded = _prepare_archive(
                asset_id=asset_id,
                url=str(row["url"]),
                archive_path=archive_path,
                archive_format=archive_format,
                skip_download=bool(args.skip_download),
                force_download=bool(args.force_download),
            )

            archive_sha256 = ""
            expected_sha256 = str(row.get("sha256", "")).strip().lower()
            archive_sha256_computed = False
            if archive_format != "directory":
                compute_archive_sha256 = bool(expected_sha256) or archive_sha256_mode == "always"
                if archive_sha256_mode == "never" and not expected_sha256:
                    compute_archive_sha256 = False
                if compute_archive_sha256:
                    archive_sha256 = _sha256_file(Path(archive_path_text).resolve())
                    archive_sha256_computed = True
                if expected_sha256 and archive_sha256 != expected_sha256:
                    raise ValueError(
                        f"{asset_id}: sha256 mismatch expected={expected_sha256} actual={archive_sha256}"
                    )

            extracted = _extract_asset(
                asset_id=asset_id,
                archive_format=archive_format,
                archive_path_text=archive_path_text,
                extract_dir=extract_dir,
                skip_extract=bool(args.skip_extract),
                force_extract=bool(args.force_extract),
            )

            entrypoint_candidates: list[str] = []
            entrypoint_relpath = str(row.get("entrypoint_relpath", "")).strip()
            if entrypoint_relpath:
                path = (extract_dir / entrypoint_relpath).resolve()
                if path.exists():
                    entrypoint_candidates.append(str(path))
            entrypoint_candidates.extend(_collect_hint_paths(extract_dir, list(row.get("entrypoint_glob", []))))
            entrypoint_resolved, entrypoint_selection_scores, entrypoint_selection_strategy = _select_runtime_entrypoint(
                candidates=entrypoint_candidates,
                runtime=asset_runtime,
                host_platform=host_platform,
            )
            entrypoint_made_executable = False
            if entrypoint_resolved:
                try:
                    entrypoint_made_executable = _ensure_executable(entrypoint_resolved)
                except OSError:
                    entrypoint_made_executable = False

            map_paths = _collect_hint_paths(extract_dir, list(row.get("map_hints", [])))
            scenario_paths = _collect_hint_paths(extract_dir, list(row.get("scenario_hints", [])))

            runtime_row = runtime_rows.setdefault(
                asset_runtime,
                {
                    "runtime_bin_candidates": [],
                    "runtime_bin_resolved": "",
                    "runtime_bin_source_asset_id": "",
                    "runtime_bin_selection_strategy": "",
                    "runtime_bin_selection_scores": [],
                    "map_resource_paths": [],
                    "scenario_resource_paths": [],
                    "target_platforms": [],
                },
            )
            for path_text in entrypoint_candidates:
                if path_text not in runtime_row["runtime_bin_candidates"]:
                    runtime_row["runtime_bin_candidates"].append(path_text)
            if not runtime_row["runtime_bin_resolved"] and entrypoint_resolved:
                runtime_row["runtime_bin_resolved"] = entrypoint_resolved
                runtime_row["runtime_bin_source_asset_id"] = asset_id
                runtime_row["runtime_bin_selection_strategy"] = entrypoint_selection_strategy
                runtime_row["runtime_bin_selection_scores"] = entrypoint_selection_scores
            if str(row.get("kind", "")).strip() in {"runtime_binary", "runtime_demo_pack"}:
                for token in list(row.get("target_platforms", [])):
                    if token not in runtime_row["target_platforms"]:
                        runtime_row["target_platforms"].append(token)
            for path_text in map_paths:
                if path_text not in runtime_row["map_resource_paths"]:
                    runtime_row["map_resource_paths"].append(path_text)
            for path_text in scenario_paths:
                if path_text not in runtime_row["scenario_resource_paths"]:
                    runtime_row["scenario_resource_paths"].append(path_text)

            asset_rows.append(
                {
                    "asset_id": asset_id,
                    "runtime": asset_runtime,
                    "kind": str(row.get("kind", "")).strip(),
                    "version": str(row.get("version", "")).strip(),
                    "url": str(row.get("url", "")).strip(),
                    "archive_format": archive_format,
                    "archive_path": archive_path_text if archive_format != "directory" else "",
                    "archive_source_path": archive_path_text if archive_format == "directory" else "",
                    "archive_sha256": archive_sha256,
                    "expected_sha256": expected_sha256,
                    "archive_sha256_computed": bool(archive_sha256_computed),
                    "downloaded": bool(downloaded),
                    "extract_path": str(extract_dir),
                    "extracted": bool(extracted),
                    "entrypoint_candidates": entrypoint_candidates,
                    "entrypoint_resolved": entrypoint_resolved,
                    "entrypoint_selection_strategy": entrypoint_selection_strategy,
                    "entrypoint_selection_scores": entrypoint_selection_scores,
                    "entrypoint_made_executable": bool(entrypoint_made_executable),
                    "map_resource_paths": map_paths,
                    "scenario_resource_paths": scenario_paths,
                    "target_platforms": list(row.get("target_platforms", [])),
                }
            )

        for runtime_key, runtime_row in runtime_rows.items():
            defaults = runtime_defaults.get(runtime_key, {})
            defaults_obj = defaults if isinstance(defaults, dict) else {}
            runtime_row["scene_path"] = _resolve_default_path(
                manifest_path,
                str(defaults_obj.get("scene", "")).strip(),
            )
            runtime_row["sensor_rig_path"] = _resolve_default_path(
                manifest_path,
                str(defaults_obj.get("sensor_rig", "")).strip(),
            )
            runtime_row["asset_count"] = sum(1 for asset in asset_rows if asset.get("runtime") == runtime_key)
            target_platforms = [str(token).strip().lower() for token in runtime_row.get("target_platforms", []) if str(token).strip()]
            runtime_row["target_platforms"] = target_platforms
            runtime_row["host_platform"] = host_platform
            runtime_row["host_compatibility_known"] = bool(target_platforms)
            runtime_row["host_compatible"] = (host_platform in target_platforms) if target_platforms else True
            runtime_probe_args_default = _runtime_probe_args_default(runtime_key)
            runtime_row["runtime_probe_args_default"] = runtime_probe_args_default
            runtime_row["runtime_probe_args_shlex"] = shlex.join(runtime_probe_args_default)

        if bool(args.require_runtime_bin) and runtime != "all":
            runtime_row = runtime_rows.get(runtime, {})
            runtime_bin = str(runtime_row.get("runtime_bin_resolved", "")).strip()
            if not runtime_bin:
                raise ValueError(f"runtime binary not resolved for runtime={runtime}")
        if bool(args.require_host_compatible):
            if runtime == "all":
                raise ValueError("--require-host-compatible requires --runtime carla|awsim")
            runtime_row = runtime_rows.get(runtime, {})
            runtime_bin = str(runtime_row.get("runtime_bin_resolved", "")).strip()
            if not runtime_bin:
                raise ValueError(f"runtime binary not resolved for runtime={runtime}")
            if not bool(runtime_row.get("host_compatible", False)):
                targets = list(runtime_row.get("target_platforms", []))
                targets_text = ",".join(str(token) for token in targets) if targets else "<unspecified>"
                raise ValueError(
                    "runtime binary host compatibility check failed for runtime="
                    f"{runtime}: host={host_platform} target_platforms={targets_text}"
                )

        resolved_payload = {
            "runtime_assets_resolved_schema_version": RUNTIME_ASSETS_RESOLVED_SCHEMA_VERSION_V0,
            "generated_at": _utc_now_iso(),
            "manifest_path": str(manifest_path),
            "runtime": runtime,
            "profile": profile,
            "archive_sha256_mode": archive_sha256_mode,
            "archives_root": str(archives_root),
            "assets_root": str(assets_root),
            "assets": asset_rows,
            "runtimes": runtime_rows,
        }
        resolved_out.parent.mkdir(parents=True, exist_ok=True)
        resolved_out.write_text(json.dumps(resolved_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

        env_out_text = str(args.env_out).strip()
        if env_out_text:
            if runtime == "all":
                raise ValueError("--env-out requires --runtime carla|awsim")
            runtime_row = runtime_rows.get(runtime, {})
            _write_env_file(
                path=Path(env_out_text).resolve(),
                runtime=runtime,
                scene=str(runtime_row.get("scene_path", "")).strip(),
                sensor_rig=str(runtime_row.get("sensor_rig_path", "")).strip(),
                runtime_bin=str(runtime_row.get("runtime_bin_resolved", "")).strip(),
                runtime_probe_args_shlex=str(runtime_row.get("runtime_probe_args_shlex", "")).strip(),
            )

        print(f"[ok] runtime={runtime}")
        print(f"[ok] profile={profile}")
        print(f"[ok] selected_assets={len(asset_rows)}")
        for runtime_key in sorted(runtime_rows):
            runtime_row = runtime_rows[runtime_key]
            print(
                "[ok] runtime_row="
                f"{runtime_key},runtime_bin={str(runtime_row.get('runtime_bin_resolved', '')).strip() or '<none>'},"
                f"maps={len(list(runtime_row.get('map_resource_paths', [])))},"
                f"scenarios={len(list(runtime_row.get('scenario_resource_paths', [])))},"
                f"host_compatible={str(bool(runtime_row.get('host_compatible', False))).lower()}"
            )
        print(f"[ok] resolved_out={resolved_out}")
        return 0
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        message = str(exc)
        print(f"[error] prepare_runtime_assets.py: {message}", file=sys.stderr)
        write_ci_error_summary(source=ERROR_SOURCE, phase=ERROR_PHASE, message=message)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
