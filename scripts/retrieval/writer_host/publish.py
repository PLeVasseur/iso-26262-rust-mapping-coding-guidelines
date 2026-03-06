from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import yaml

from retrieval.operations.export_rst import export_guidelines
from retrieval.writer_host.conformance import run_conformance
from retrieval.writer_host.publish_git import (
    create_worktree,
    finalize_commit,
    push_branch,
    remove_worktree,
)
from retrieval.writer_host.publish_ingest import ingest_records
from retrieval.writer_host.publish_loader import load_publish_payload
from retrieval.writer_host.publish_mapping import map_publish_record


def _load_guidelines_repo_root(root: Path) -> Path:
    cfg_path = root / "config" / "corpora" / "guidelines_repo.yaml"
    payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    sources = payload.get("sources") if isinstance(payload, dict) else {}
    repo_raw = str((sources or {}).get("guidelines_repo_root", "")).strip()
    if not repo_raw:
        raise RuntimeError("sources.guidelines_repo_root is required")
    return (root / repo_raw).resolve()


def _build_record(row: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    amplification = row["amplification"]
    rationale = row["rationale"]
    examples = row["examples"]
    metadata = row["metadata"]
    return {
        "target_id": mapping["target_id"],
        "guideline_id": mapping["guideline_id"],
        "filename": mapping["filename"],
        "chapter": mapping["chapter"],
        "title": mapping["title"],
        "category": mapping["category"],
        "status": mapping["status"],
        "release": mapping["release"],
        "fls_id": mapping["fls_id"],
        "decidability": mapping["decidability"],
        "scope": mapping["scope"],
        "tags": list(mapping["tags"]),
        "non_compliant_miri_intent": str(examples.get("non_compliant_miri_intent", "")).strip(),
        "compliant_miri_intent": str(examples.get("compliant_miri_intent", "")).strip(),
        "non_compliant_miri_skip_justification": str(
            examples.get("non_compliant_miri_skip_justification", "")
        ).strip(),
        "compliant_miri_skip_justification": str(
            examples.get("compliant_miri_skip_justification", "")
        ).strip(),
        "blocks": [
            {
                "block_type": "body",
                "order_index": 1,
                "content": str(amplification.get("guideline_amplification_text", "")).strip(),
            },
            {
                "block_type": "rationale",
                "order_index": 2,
                "content": str(rationale.get("rationale_text", "")).strip(),
            },
            {
                "block_type": "non_compliant_narrative",
                "order_index": 3,
                "content": str(examples.get("non_compliant_narrative", "")).strip(),
            },
            {
                "block_type": "non_compliant_code",
                "order_index": 4,
                "content": str(examples.get("non_compliant_code", "")).strip(),
            },
            {
                "block_type": "compliant_narrative",
                "order_index": 5,
                "content": str(examples.get("compliant_narrative", "")).strip(),
            },
            {
                "block_type": "compliant_code",
                "order_index": 6,
                "content": str(examples.get("compliant_code", "")).strip(),
            },
        ],
        "bibliography_rows": list(metadata.get("bibliography_rows") or []),
    }


def run_ingest_from_run(*, root: Path, run_dir: Path, mode: str, output_db: Path) -> dict[str, Any]:
    payload = load_publish_payload(run_dir=run_dir, publishable=(mode == "publishable"))
    mapped_rows: list[dict[str, Any]] = []
    for row in payload["draft_rows"]:
        mapping = map_publish_record(row)
        mapped_rows.append(_build_record(row, mapping))
    summary = ingest_records(
        db_path=output_db,
        records=mapped_rows,
        source_run_id=run_dir.name,
    )
    metrics = {
        "unsafe_examples_total": 0,
        "miri_check_count": 0,
        "miri_expect_ub_count": 0,
        "miri_skip_count": 0,
        "miri_skip_without_justification_count": 0,
    }
    for row in mapped_rows:
        for side in ("non_compliant", "compliant"):
            code = str(
                next(
                    (
                        block.get("content", "")
                        for block in list(row.get("blocks") or [])
                        if str(block.get("block_type", "")) == f"{side}_code"
                    ),
                    "",
                )
            )
            intent = str(row.get(f"{side}_miri_intent", "")).strip().lower()
            justification = str(row.get(f"{side}_miri_skip_justification", "")).strip()
            if "unsafe" in code:
                metrics["unsafe_examples_total"] += 1
            if intent == "check":
                metrics["miri_check_count"] += 1
            elif intent == "expect_ub":
                metrics["miri_expect_ub_count"] += 1
            elif intent == "skip":
                metrics["miri_skip_count"] += 1
                if not justification:
                    metrics["miri_skip_without_justification_count"] += 1

    return {
        "status": "pass",
        "run_dir": str(run_dir),
        "mode": mode,
        "db": summary,
        "record_count": len(mapped_rows),
        "annotation_policy_metrics": metrics,
    }


def run_export_rst(*, root: Path, db_path: Path, guidelines_repo_root: Path) -> dict[str, Any]:
    output_root = guidelines_repo_root / "src" / "coding-guidelines"
    summary = export_guidelines(db_path=db_path, output_root=output_root)
    return {
        "status": "pass",
        "db_path": str(db_path),
        "output_root": str(output_root),
        "export": summary,
    }


def run_publish_from_run(*, root: Path, run_dir: Path, mode: str, dry_run: bool) -> dict[str, Any]:
    repo_root = _load_guidelines_repo_root(root)
    publish_root = root / ".cache" / "sqlite_kb" / "reports" / "writer_publish" / run_dir.name
    publish_root.mkdir(parents=True, exist_ok=True)
    db_path = publish_root / "writer_publish.sqlite"

    if dry_run:
        return {
            "status": "dry_run",
            "mode": mode,
            "run_dir": str(run_dir),
            "repo_root": str(repo_root),
            "db_path": str(db_path),
        }

    worktree_info = create_worktree(repo_root=repo_root, cache_root=publish_root)
    worktree_root = Path(str(worktree_info["worktree"])).resolve()
    branch = str(worktree_info["branch"])
    try:
        ingest = run_ingest_from_run(root=root, run_dir=run_dir, mode=mode, output_db=db_path)
        (publish_root / "annotation_policy_metrics.json").write_text(
            json.dumps(ingest.get("annotation_policy_metrics", {}), indent=2, sort_keys=False)
            + "\n",
            encoding="utf-8",
        )
        export = run_export_rst(root=root, db_path=db_path, guidelines_repo_root=worktree_root)
        conformance = run_conformance(
            repo_root=worktree_root,
            report_dir=publish_root,
        )
        if mode == "publishable" and str(conformance.get("status", "")) != "pass":
            return {
                "status": "fail",
                "mode": mode,
                "run_dir": str(run_dir),
                "repo_root": str(repo_root),
                "worktree": str(worktree_root),
                "branch": branch,
                "ingest": ingest,
                "export": export,
                "conformance": conformance,
                "failure_code": "CONFORMANCE_FAILED",
                "commit": {"committed": False},
            }

        commit_message = f"feat(guidelines): publish writer run {run_dir.name}"
        commit = finalize_commit(worktree_root=worktree_root, message=commit_message)
        push = {"pushed": False, "branch": branch}
        if bool(commit.get("committed", False)):
            push = push_branch(worktree_root=worktree_root, branch=branch)

        return {
            "status": "pass",
            "mode": mode,
            "run_dir": str(run_dir),
            "repo_root": str(repo_root),
            "worktree": str(worktree_root),
            "branch": branch,
            "ingest": ingest,
            "export": export,
            "conformance": conformance,
            "commit": commit,
            "push": push,
        }
    finally:
        remove_worktree(repo_root=repo_root, worktree_root=worktree_root)


def write_publish_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def run_conformance_command(*, root: Path, run_dir: Path, mode: str) -> dict[str, Any]:
    _ = run_dir  # reserved for future report linkage
    repo_root = _load_guidelines_repo_root(root)
    report_dir = root / ".cache" / "sqlite_kb" / "reports" / "writer_conformance" / run_dir.name
    report = run_conformance(repo_root=repo_root, report_dir=report_dir)
    return {
        "status": report.get("status", "fail"),
        "mode": mode,
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "report_path": str(report_dir / "writer_conformance_report.json"),
        "report": report,
    }


def namespace_from_args(args: Namespace, *, root: Path) -> tuple[Path, str, bool]:
    run_dir_raw = str(getattr(args, "run_dir", "") or "").strip()
    if not run_dir_raw:
        raise RuntimeError("--run-dir is required")
    run_dir = Path(run_dir_raw).resolve()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir does not exist: {run_dir}")
    mode = str(getattr(args, "mode", "publishable") or "publishable")
    dry_run = bool(getattr(args, "dry_run", False))
    _ = root
    return run_dir, mode, dry_run
