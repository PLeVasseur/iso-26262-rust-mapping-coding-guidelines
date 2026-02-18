from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

EXIT_SUCCESS = 0
EXIT_POLICY_FAIL = 2
EXIT_RUNTIME_FAIL = 3


@dataclass(frozen=True)
class ExtractorPaths:
    repo_root: Path
    cache_root: Path
    manifest_dir: Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def generate_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}"


def read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_yaml(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def run_command(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )


def resolve_path(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (root / candidate).resolve()


def resolve_extractor_paths(root: Path | None = None) -> ExtractorPaths:
    root = root or repo_root()
    config_path = root / "config" / "extractor_paths.yaml"
    config_payload: dict[str, Any] = {}
    if config_path.exists():
        config_payload = read_yaml(config_path) or {}

    default_repo_root = "../iso-26262-coding-standard-extraction"
    repo_root_raw = (
        os.environ.get("EXTRACTOR_REPO_ROOT")
        or config_payload.get("repo_root")
        or default_repo_root
    )
    cache_root_raw = (
        os.environ.get("EXTRACTOR_CACHE_ROOT")
        or config_payload.get("cache_root")
        or f"{repo_root_raw}/.cache/iso26262"
    )
    manifest_dir_raw = (
        os.environ.get("EXTRACTOR_MANIFEST_DIR")
        or config_payload.get("manifest_dir")
        or f"{cache_root_raw}/manifests"
    )

    resolved_repo_root = resolve_path(repo_root_raw, root)
    resolved_cache_root = resolve_path(cache_root_raw, root)
    resolved_manifest_dir = resolve_path(manifest_dir_raw, root)

    return ExtractorPaths(
        repo_root=resolved_repo_root,
        cache_root=resolved_cache_root,
        manifest_dir=resolved_manifest_dir,
    )


def ensure_extractor_paths(paths: ExtractorPaths) -> list[str]:
    issues: list[str] = []
    if not paths.repo_root.exists():
        issues.append(f"Extractor repo_root missing: {paths.repo_root}")
    if not paths.cache_root.exists():
        issues.append(f"Extractor cache_root missing: {paths.cache_root}")
    if not paths.manifest_dir.exists():
        issues.append(f"Extractor manifest_dir missing: {paths.manifest_dir}")
    return issues


def extract_json_blob(raw: str) -> Any:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in command output")
    return json.loads(raw[start : end + 1])


def stable_scope_fingerprint(target_ids: list[str]) -> str:
    normalized = sorted({value.strip() for value in target_ids if value.strip()})
    digest = hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()
    return digest


def find_registry_baseline(
    run_registry: dict[str, Any], corpus_pack_id: str, mode: str
) -> dict[str, Any] | None:
    for entry in run_registry.get("accepted_runs", []):
        if entry.get("corpus_pack_id") == corpus_pack_id and entry.get("mode") == mode:
            return entry
    return None
