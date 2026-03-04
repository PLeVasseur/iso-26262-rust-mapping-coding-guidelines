from __future__ import annotations

import sqlite3
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any

import yaml

from retrieval.builders.guidelines_repo_builder import run_guidelines_repo_build
from retrieval.guidelines_repo.common import (
    RunContext,
    compute_tree_hash as _compute_tree_hash,
    emit_failure as _emit_failure,
    emit_success as _emit_success,
    ensure_checkout as _ensure_checkout,
    new_context as _new_context,
    parse_sources as _parse_sources,
    read_yaml as _read_yaml,
    resolve_repo_root as _resolve_repo_root,
    run_idempotency_key as _run_idempotency_key,
    to_int as _to_int,
    utc_now as _utc_now,
)
from retrieval.guidelines.build_runner import run_guidelines_build
from retrieval.operations.export_rst import export_guidelines
from retrieval.services.guidelines_projection import export_projection_summary, run_m15_projection

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


def run_autopilot(args: Namespace, *, root: Path) -> int:
    command = (
        "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py "
        "guidelines-repo autopilot "
        f"--profile {args.profile} --mode {args.mode}"
        + (" --allow-main" if bool(getattr(args, "allow_main", False)) else "")
    )
    ctx = _new_context(root=root, operation="autopilot", command=command, args=args)
    mode = str(args.mode)
    if bool(getattr(args, "allow_main", False)) and mode != "exploratory":
        return _emit_failure(
            ctx=ctx,
            failure_code="POLICY_ALLOW_MAIN_REQUIRES_EXPLORATORY",
            skipped_reason="invalid_flag_combination",
            owner_hint="policy",
            expected={"mode": "exploratory when --allow-main is used"},
            observed={"mode": mode, "allow_main": True},
            failing_path_or_key="args.mode",
            repo_root=root,
            db_path=(root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite"),
            fix_commands=[
                "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo autopilot --profile fast --mode exploratory --allow-main"
            ],
        )

    doctor_status = run_doctor(args, root=root)
    if doctor_status != EXIT_SUCCESS:
        return _emit_failure(
            ctx=ctx,
            failure_code="AUTOPILOT_DOCTOR_FAILED",
            skipped_reason="precondition_failed_doctor",
            owner_hint="policy",
            expected={"doctor": EXIT_SUCCESS},
            observed={"doctor_exit_code": int(doctor_status)},
            failing_path_or_key="guidelines-repo doctor",
            repo_root=root,
            db_path=(
                root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite"
            ).resolve(),
            fix_commands=[
                "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo doctor --mode publishable"
            ],
        )
    ensure_status = run_ensure_repo(args, root=root)
    if ensure_status != EXIT_SUCCESS:
        return _emit_failure(
            ctx=ctx,
            failure_code="AUTOPILOT_ENSURE_REPO_FAILED",
            skipped_reason="precondition_failed_ensure_repo",
            owner_hint="env",
            expected={"ensure_repo": EXIT_SUCCESS},
            observed={"ensure_repo_exit_code": int(ensure_status)},
            failing_path_or_key="guidelines-repo ensure-repo",
            repo_root=root,
            db_path=(
                root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite"
            ).resolve(),
            fix_commands=[
                "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py guidelines-repo ensure-repo --mode publishable"
            ],
        )

    sources = _parse_sources(root)
    repo_root = _resolve_repo_root(root, str(sources["guidelines_repo_root"]))
    revision = str(sources["guidelines_repo_revision"]).strip()
    if (repo_root / ".git").exists() and not revision:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip()
    db_path = (root / ".cache" / "sqlite_kb" / "current" / "guidelines_repo.sqlite").resolve()

    preflight_ctx = _new_context(
        root=root,
        operation="preflight",
        command=f'cd "{repo_root}" && ./make.py --offline',
        args=args,
    )
    preflight_code, preflight_stdout, preflight_stderr, versions = run_guidelines_build(
        repo_root=repo_root,
        offline=True,
    )
    if preflight_code != 0:
        return _emit_failure(
            ctx=preflight_ctx,
            failure_code="M0_PREFLIGHT_BUILD_FAILED",
            skipped_reason="precondition_failed_m0_build",
            owner_hint="env",
            expected={"command": "./make.py --offline", "returncode": 0},
            observed={"returncode": preflight_code, "versions": versions},
            failing_path_or_key=str(repo_root / "make.py"),
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[f'cd "{repo_root}" && ./make.py --offline'],
            stdout=preflight_stdout,
            stderr=preflight_stderr,
        )
    _emit_success(
        preflight_ctx,
        {
            "idempotency_key": _run_idempotency_key(revision=revision, root=root),
            "versions": versions,
        },
        stdout=preflight_stdout,
        stderr=preflight_stderr,
    )

    profile = str(args.profile)
    exemplar_ids = list(sources["known_good_exemplar_ids"])
    if profile == "fast" and exemplar_ids:
        exemplar_ids = exemplar_ids[: max(2, min(5, len(exemplar_ids)))]

    build_args = Namespace(
        corpus="guidelines_repo",
        db_path=str(db_path),
        report_root=str(root / ".cache" / "sqlite_kb" / "reports" / "guidelines_repo"),
        ingest_strategy="guidelines_artifacts_v1",
        guidelines_repo_root=str(repo_root),
        guidelines_repo_revision=str(revision),
        guidelines_exemplar_ids=exemplar_ids,
        assume_built=False,
    )
    before_projection = None
    if db_path.exists():
        before_projection = export_projection_summary(db_path)

    try:
        build_summary = run_guidelines_repo_build(args=build_args, root=root)
    except Exception as exc:
        return _emit_failure(
            ctx=ctx,
            failure_code="M1_INGEST_FAILED",
            skipped_reason="m1_failed",
            owner_hint="contract",
            expected={"build": "successful artifact ingest"},
            observed={"error": str(exc)},
            failing_path_or_key="guidelines_repo_builder",
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[command],
        )

    m15_code, m15_stdout, m15_stderr = run_m15_projection(repo_root, ctx.report_dir)
    if m15_code != 0:
        return _emit_failure(
            ctx=ctx,
            failure_code="M1_5_GATE_FAILED",
            skipped_reason="m1_5_gate_failed",
            owner_hint="data",
            expected={"extract_rust_examples": 0},
            observed={"returncode": m15_code},
            failing_path_or_key=str(repo_root / "scripts" / "extract_rust_examples.py"),
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[
                "uv run python scripts/extract_rust_examples.py --test --src-dir src/coding-guidelines --prelude src/examples_prelude.rs --json build/examples/m1_5_results.json --fail-on-error --verbose"
            ],
            stdout=m15_stdout,
            stderr=m15_stderr,
        )

    output_root = repo_root / "src" / "coding-guidelines"
    post_export_ctx = _new_context(
        root=root,
        operation="post_export",
        command=f"export-rst->{output_root}",
        args=args,
    )
    try:
        export_summary = export_guidelines(db_path=db_path, output_root=output_root)
    except Exception as exc:
        return _emit_failure(
            ctx=post_export_ctx,
            failure_code="M2_EXPORT_FAILED",
            skipped_reason="m2_export_failed",
            owner_hint="contract",
            expected={"export": "deterministic generated lane"},
            observed={"error": str(exc)},
            failing_path_or_key=str(output_root),
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[
                "uv run --extra guidelines-integration --extra guidelines-export python scripts/sqlite_kb.py export-rst --corpus guidelines_repo"
            ],
        )

    gate_code, gate_stdout, gate_stderr, _ = run_guidelines_build(repo_root=repo_root, offline=True)
    if gate_code != 0:
        return _emit_failure(
            ctx=post_export_ctx,
            failure_code="POST_EXPORT_BUILD_FAILED",
            skipped_reason="m2_build_gate_failed",
            owner_hint="env",
            expected={"command": "./make.py --offline", "returncode": 0},
            observed={"returncode": gate_code},
            failing_path_or_key=str(repo_root / "make.py"),
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[f'cd "{repo_root}" && ./make.py --offline'],
            stdout=gate_stdout,
            stderr=gate_stderr,
        )

    exported_file_count = _to_int(export_summary.get("file_count", 0), default=0)
    output_digest = str(export_summary.get("output_digest", ""))
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO guideline_export_runs(run_id, corpus, source_revision, output_root, file_count, output_digest, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                f"export::{ctx.run_id}",
                "guidelines_repo",
                revision,
                str(output_root),
                exported_file_count,
                output_digest,
                _utc_now(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    _emit_success(
        post_export_ctx,
        {
            "idempotency_key": _run_idempotency_key(revision=revision, root=root),
            "file_count": exported_file_count,
            "output_digest": output_digest,
        },
        stdout=gate_stdout,
        stderr=gate_stderr,
    )

    reingest_args = Namespace(**dict(vars(build_args)))
    reingest_args.assume_built = True
    try:
        run_guidelines_repo_build(args=reingest_args, root=root)
    except Exception as exc:
        return _emit_failure(
            ctx=ctx,
            failure_code="ROUNDTRIP_REINGEST_FAILED",
            skipped_reason="m2_roundtrip_failed",
            owner_hint="contract",
            expected={"reingest": "successful"},
            observed={"error": str(exc)},
            failing_path_or_key="guidelines_repo_builder",
            repo_root=repo_root,
            db_path=db_path,
            fix_commands=[command],
        )
    after_projection = export_projection_summary(db_path)
    if before_projection is not None:
        before_counts_raw = before_projection.get("counts", {})
        before_counts = before_counts_raw if isinstance(before_counts_raw, dict) else {}
        if _to_int(before_counts.get("guidelines", 0), default=0) > 0:
            before_hash = str(before_projection.get("hash", ""))
            after_hash = str(after_projection.get("hash", ""))
            if before_hash != after_hash:
                return _emit_failure(
                    ctx=ctx,
                    failure_code="ROUNDTRIP_SEMANTIC_MISMATCH",
                    skipped_reason="m2_roundtrip_failed",
                    owner_hint="data",
                    expected={"projection_hash": before_hash},
                    observed={"projection_hash": after_hash},
                    failing_path_or_key="guidelines semantic projection",
                    repo_root=repo_root,
                    db_path=db_path,
                    fix_commands=[command],
                )

    m15_report = root / "docs" / "reports" / "guidelines_repo_m1_5_assessment.md"
    m15_report.parent.mkdir(parents=True, exist_ok=True)
    m15_report.write_text(
        "# M1.5 Assessment\n\n"
        "- Decision: go\n"
        "- Mechanical gates: pass\n"
        "- RF example test: pass\n"
        "- Notes: export/build lane passed for current snapshot\n",
        encoding="utf-8",
    )

    return _emit_success(
        ctx,
        {
            "idempotency_key": _run_idempotency_key(revision=revision, root=root),
            "build_summary": build_summary,
            "m1_5_go": True,
            "m1_5_report": str(m15_report),
            "roundtrip_projection": after_projection,
            "completed_at": _utc_now(),
        },
    )
