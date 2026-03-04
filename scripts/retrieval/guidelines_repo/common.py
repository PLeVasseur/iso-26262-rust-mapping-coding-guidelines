from __future__ import annotations

import hashlib
import json
import subprocess
from argparse import Namespace
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

EXIT_PRECONDITION_FAIL = 2
EXIT_SUCCESS = 0


@dataclass(frozen=True)
class RunContext:
    operation: str
    run_id: str
    report_dir: Path
    command: str
    profile: str
    mode: str
    non_publishable: bool
    flags_used: list[str]


def utc_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def parse_sources(root: Path) -> dict[str, Any]:
    config = read_yaml(root / "config" / "corpora" / "guidelines_repo.yaml")
    sources_raw = config.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    exemplar_ids = sources.get("known_good_exemplar_ids")
    return {
        "guidelines_repo_root": str(sources.get("guidelines_repo_root", "")).strip(),
        "guidelines_repo_revision": str(sources.get("guidelines_repo_revision", "")).strip(),
        "known_good_exemplar_ids": [
            str(value).strip() for value in (exemplar_ids if isinstance(exemplar_ids, list) else [])
        ],
    }


def resolve_repo_root(root: Path, repo_root_raw: str) -> Path:
    repo_root = Path(repo_root_raw)
    if not repo_root.is_absolute():
        repo_root = (root / repo_root).resolve()
    return repo_root


def run_idempotency_key(*, revision: str, root: Path) -> str:
    config_digest = hashlib.sha256(
        (root / "config" / "corpora" / "guidelines_repo.yaml")
        .read_text(encoding="utf-8")
        .encode("utf-8")
    ).hexdigest()[:12]
    schema_manifest = hashlib.sha256(
        (root / "config" / "sqlite_migrations" / "manifest.yaml")
        .read_text(encoding="utf-8")
        .encode("utf-8")
    ).hexdigest()[:12]
    return f"{revision}:{schema_manifest}:{config_digest}"


def base_summary(
    ctx: RunContext, *, did_work: bool, skipped_reason: str, status: str
) -> dict[str, Any]:
    return {
        "operation": ctx.operation,
        "corpus": "guidelines_repo",
        "did_work": did_work,
        "skipped_reason": skipped_reason,
        "idempotency_key": "unresolved_precondition",
        "selected_profile": ctx.profile,
        "selected_mode": ctx.mode,
        "non_publishable": ctx.non_publishable,
        "status": status,
        "run_id": ctx.run_id,
    }


def emit_failure(
    *,
    ctx: RunContext,
    failure_code: str,
    skipped_reason: str,
    owner_hint: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    failing_path_or_key: str,
    repo_root: Path,
    db_path: Path,
    fix_commands: list[str],
    stdout: str = "",
    stderr: str = "",
) -> int:
    summary = base_summary(
        ctx, did_work=False, skipped_reason=skipped_reason, status="failed_precondition"
    )
    remediation = {
        "failure_code": failure_code,
        "operation": ctx.operation,
        "expected": expected,
        "observed": observed,
        "failing_path_or_key": failing_path_or_key,
        "repo_root": str(repo_root),
        "db_path": str(db_path),
        "flags_used": list(ctx.flags_used),
        "rerun_command": ctx.command,
        "fix_commands": fix_commands,
        "owner_hint": owner_hint,
    }
    write_json(ctx.report_dir / "summary.json", summary)
    write_text(ctx.report_dir / "stdout.log", stdout if stdout else f"Command: {ctx.command}\n")
    write_text(
        ctx.report_dir / "stderr.log",
        stderr if stderr else json.dumps(observed, sort_keys=True) + "\n",
    )
    write_json(ctx.report_dir / "remediation.json", remediation)
    return EXIT_PRECONDITION_FAIL


def emit_success(
    ctx: RunContext, payload: dict[str, Any], *, stdout: str = "", stderr: str = ""
) -> int:
    summary = base_summary(ctx, did_work=True, skipped_reason="", status="ok")
    summary.update(payload)
    write_json(ctx.report_dir / "summary.json", summary)
    write_text(ctx.report_dir / "stdout.log", stdout if stdout else f"Command: {ctx.command}\n")
    write_text(ctx.report_dir / "stderr.log", stderr)
    return EXIT_SUCCESS


def new_context(*, root: Path, operation: str, command: str, args: Namespace) -> RunContext:
    run_id = utc_run_id()
    report_dir = root / ".cache" / "sqlite_kb" / "reports" / "guidelines_repo" / operation / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    profile = str(getattr(args, "profile", "fast"))
    mode = str(getattr(args, "mode", "publishable"))
    flags_used: list[str] = []
    if getattr(args, "profile", None) is not None:
        flags_used.extend(["--profile", profile])
    if getattr(args, "mode", None) is not None:
        flags_used.extend(["--mode", mode])
    if bool(getattr(args, "allow_main", False)):
        flags_used.append("--allow-main")
    return RunContext(
        operation=operation,
        run_id=run_id,
        report_dir=report_dir,
        command=command,
        profile=profile,
        mode=mode,
        non_publishable=(mode == "exploratory"),
        flags_used=flags_used,
    )


def compute_tree_hash(root: Path) -> str:
    entries: list[str] = []
    for path in sorted(root.glob("**/*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        rel_str = rel.as_posix()
        if (
            rel_str.startswith(".git/")
            or rel_str.startswith("build/")
            or rel_str.startswith(".venv/")
        ):
            continue
        entries.append(rel_str + "::" + hashlib.sha256(path.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def to_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        token = value.strip()
        if token:
            try:
                return int(token)
            except ValueError:
                return default
    return default


def git_clean(repo_root: Path) -> tuple[bool, str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "git_status_failed").strip()
    return (completed.stdout.strip() == ""), completed.stdout.strip()


def ensure_checkout(repo_root: Path, revision: str, *, allow_main: bool) -> tuple[bool, str]:
    if not (repo_root / ".git").exists():
        return False, "missing_git_metadata"
    clean, details = git_clean(repo_root)
    if not clean:
        dirty_lines = [line.strip() for line in str(details).splitlines() if line.strip()]
        if dirty_lines and all(line.endswith("uv.lock") for line in dirty_lines):
            restore = subprocess.run(
                ["git", "restore", "uv.lock"],
                cwd=str(repo_root),
                text=True,
                capture_output=True,
                check=False,
            )
            if restore.returncode == 0:
                clean, details = git_clean(repo_root)
        if not clean:
            return False, details

    if revision:
        fetch = subprocess.run(
            ["git", "fetch", "--tags", "--prune"],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if fetch.returncode != 0:
            return False, (fetch.stderr or fetch.stdout).strip()
        checkout = subprocess.run(
            ["git", "checkout", "--detach", revision],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            check=False,
        )
        if checkout.returncode != 0:
            return False, (checkout.stderr or checkout.stdout).strip()
    elif not allow_main:
        return False, "missing_revision_for_publishable"
    return True, ""
