from __future__ import annotations

import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any

import yaml

from retrieval.guidelines_repo.common import (
    compute_tree_hash as _compute_tree_hash,
    emit_failure as _emit_failure,
    emit_success as _emit_success,
    ensure_checkout as _ensure_checkout,
    new_context as _new_context,
    parse_sources as _parse_sources,
    read_yaml as _read_yaml,
    resolve_repo_root as _resolve_repo_root,
    run_idempotency_key as _run_idempotency_key,
)

EXIT_SUCCESS = 0
EXIT_PRECONDITION_FAIL = 2
EXIT_RUNTIME_FAIL = 3


def run_doctor(args: Namespace, *, root: Path) -> int:
    command = "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo doctor"
    ctx = _new_context(root=root, operation="doctor", command=command, args=args)
    sources = _parse_sources(root)
    db_path = (root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite").resolve()
    repo_root_raw = str(sources["guidelines_repo_root"])
    revision = str(sources["guidelines_repo_revision"])
    mode = str(getattr(args, "mode", "publishable"))

    if not repo_root_raw:
        return _emit_failure(
            ctx=ctx,
            failure_code="MISSING_GUIDELINES_REPO_ROOT",
            skipped_reason="precondition_failed_missing_repo_root",
            owner_hint="env",
            expected={"sources.guidelines_repo_root": "required"},
            observed={"sources.guidelines_repo_root": repo_root_raw},
            failing_path_or_key="config/corpora/guidelines_repo.yaml:sources.guidelines_repo_root",
            repo_root=root,
            db_path=db_path,
            fix_commands=[
                "Set sources.guidelines_repo_root in config/corpora/guidelines_repo.yaml"
            ],
        )

    if mode == "publishable" and not revision:
        return _emit_failure(
            ctx=ctx,
            failure_code="PIN_REQUIRED_PUBLISHABLE",
            skipped_reason="precondition_failed_missing_pin",
            owner_hint="policy",
            expected={"sources.guidelines_repo_revision": "non-empty pinned revision"},
            observed={"sources.guidelines_repo_revision": revision},
            failing_path_or_key="config/corpora/guidelines_repo.yaml:sources.guidelines_repo_revision",
            repo_root=root,
            db_path=db_path,
            fix_commands=[
                "Set sources.guidelines_repo_revision (commit SHA) in config/corpora/guidelines_repo.yaml"
            ],
        )

    repo_root = _resolve_repo_root(root, repo_root_raw)
    required_contracts = [
        root / "contracts" / "rf_needs_json.contract.json",
        root / "contracts" / "rf_guidelines_ids.contract.json",
    ]
    missing_contracts = [str(path) for path in required_contracts if not path.exists()]
    if missing_contracts:
        return _emit_failure(
            ctx=ctx,
            failure_code="MISSING_ARTIFACT_CONTRACTS",
            skipped_reason="precondition_failed_missing_contracts",
            owner_hint="contract",
            expected={"required_contracts": [str(path) for path in required_contracts]},
            observed={"missing_contracts": missing_contracts},
            failing_path_or_key="contracts/rf_*.contract.json",
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[
                "Create contracts/rf_needs_json.contract.json",
                "Create contracts/rf_guidelines_ids.contract.json",
            ],
        )

    return _emit_success(
        ctx,
        {
            "idempotency_key": _run_idempotency_key(revision=revision or "unpinned", root=root),
            "repo_root": str(repo_root),
            "revision": revision,
        },
    )


def run_ensure_repo(args: Namespace, *, root: Path) -> int:
    command = "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo ensure-repo"
    ctx = _new_context(root=root, operation="ensure_repo", command=command, args=args)
    sources = _parse_sources(root)
    repo_root_raw = str(sources["guidelines_repo_root"])
    revision = str(sources["guidelines_repo_revision"])
    mode = str(getattr(args, "mode", "publishable"))
    allow_main = bool(getattr(args, "allow_main", False))
    db_path = (root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite").resolve()

    if not repo_root_raw:
        return _emit_failure(
            ctx=ctx,
            failure_code="MISSING_GUIDELINES_REPO_ROOT",
            skipped_reason="precondition_failed_missing_repo_root",
            owner_hint="env",
            expected={"sources.guidelines_repo_root": "required"},
            observed={"sources.guidelines_repo_root": repo_root_raw},
            failing_path_or_key="config/corpora/guidelines_repo.yaml:sources.guidelines_repo_root",
            repo_root=root,
            db_path=db_path,
            fix_commands=[
                "Set sources.guidelines_repo_root in config/corpora/guidelines_repo.yaml"
            ],
        )
    repo_root = _resolve_repo_root(root, repo_root_raw)
    if not repo_root.exists():
        clone = subprocess.run(
            [
                "git",
                "clone",
                "https://github.com/rustfoundation/safety-critical-rust-coding-guidelines",
                str(repo_root),
            ],
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
        )
        if clone.returncode != 0:
            return _emit_failure(
                ctx=ctx,
                failure_code="REPO_CLONE_FAILED",
                skipped_reason="precondition_failed_clone",
                owner_hint="env",
                expected={"clone": 0},
                observed={"returncode": int(clone.returncode)},
                failing_path_or_key=str(repo_root),
                repo_root=repo_root,
                db_path=db_path,
                fix_commands=[
                    f'git clone https://github.com/rustfoundation/safety-critical-rust-coding-guidelines "{repo_root}"'
                ],
                stdout=clone.stdout,
                stderr=clone.stderr,
            )

    if mode == "publishable" and not revision:
        return _emit_failure(
            ctx=ctx,
            failure_code="PIN_REQUIRED_PUBLISHABLE",
            skipped_reason="precondition_failed_missing_pin",
            owner_hint="policy",
            expected={"sources.guidelines_repo_revision": "non-empty pinned revision"},
            observed={"sources.guidelines_repo_revision": revision},
            failing_path_or_key="config/corpora/guidelines_repo.yaml:sources.guidelines_repo_revision",
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[
                "Set sources.guidelines_repo_revision (commit SHA) in config/corpora/guidelines_repo.yaml"
            ],
        )

    if (repo_root / ".git").exists():
        ok, detail = _ensure_checkout(
            repo_root, revision, allow_main=allow_main or mode == "exploratory"
        )
        if not ok:
            return _emit_failure(
                ctx=ctx,
                failure_code="REPO_CHECKOUT_FAILED",
                skipped_reason="precondition_failed_checkout",
                owner_hint="env",
                expected={"git_checkout": "clean detached at pinned revision"},
                observed={"detail": detail},
                failing_path_or_key=str(repo_root),
                repo_root=repo_root,
                db_path=db_path,
                fix_commands=[f'cd "{repo_root}" && git status --short'],
            )
        revision_value = (
            revision
            or subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo_root),
                text=True,
                capture_output=True,
                check=False,
            ).stdout.strip()
        )
    else:
        revision_value = _compute_tree_hash(repo_root)

    return _emit_success(
        ctx,
        {
            "idempotency_key": _run_idempotency_key(revision=revision_value, root=root),
            "repo_root": str(repo_root),
            "revision": revision_value,
            "repo_clean": True,
        },
    )


def run_bootstrap_guidelines_repo(args: Namespace, *, root: Path) -> int:
    command = "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo bootstrap-guidelines-repo --verify"
    ctx = _new_context(root=root, operation="bootstrap", command=command, args=args)
    return _emit_success(
        ctx,
        {
            "idempotency_key": "bootstrap-deprecated",
            "status_note": "bootstrap-guidelines-repo is deprecated under chapter-sidecar export mode",
        },
    )


def run_bump_pin(args: Namespace, *, root: Path) -> int:
    command = "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo bump-pin --revision <sha>"
    ctx = _new_context(root=root, operation="bump_pin", command=command, args=args)
    revision = str(getattr(args, "revision", "")).strip()
    db_path = (root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite").resolve()
    if not revision:
        return _emit_failure(
            ctx=ctx,
            failure_code="MISSING_PIN_REVISION",
            skipped_reason="precondition_failed_missing_pin_revision",
            owner_hint="policy",
            expected={"--revision": "required"},
            observed={"revision": revision},
            failing_path_or_key="args.revision",
            repo_root=root,
            db_path=db_path,
            fix_commands=[command],
        )
    cfg_path = root / "config" / "corpora" / "guidelines_repo.yaml"
    payload = _read_yaml(cfg_path)
    sources_raw = payload.get("sources")
    sources: dict[str, Any] = sources_raw if isinstance(sources_raw, dict) else {}
    sources["guidelines_repo_revision"] = revision
    payload["sources"] = sources
    cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return _emit_success(
        ctx,
        {
            "idempotency_key": _run_idempotency_key(revision=revision, root=root),
            "new_revision": revision,
            "config_path": str(cfg_path),
        },
    )


def run_reorg_path_mapping(args: Namespace, *, root: Path) -> int:
    command = "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo reorg-path-mapping"
    ctx = _new_context(root=root, operation="reorg_path_mapping", command=command, args=args)
    return _emit_failure(
        ctx=ctx,
        failure_code="REORG_REQUIRES_EXPLICIT_MAPPING",
        skipped_reason="precondition_failed_missing_reorg_map",
        owner_hint="policy",
        expected={"mapping_file": "required"},
        observed={"mapping_file": ""},
        failing_path_or_key="args.mapping_file",
        repo_root=root,
        db_path=(root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite"),
        fix_commands=[
            "Provide a mapping file then rerun with --mapping-file <path> (operation intentionally guarded)"
        ],
    )
