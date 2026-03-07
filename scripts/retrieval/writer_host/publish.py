from __future__ import annotations

import json
import shutil
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
    status_porcelain,
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
        "fls_resolution": dict(mapping.get("fls_resolution") or {}),
        "fls_resolution_report": str(mapping.get("fls_resolution_report") or ""),
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


def publish_root_for_run(*, root: Path, run_dir: Path) -> Path:
    return root / ".cache" / "sqlite_kb" / "reports" / "writer_publish" / run_dir.name


def default_publish_report_path(*, root: Path, run_dir: Path) -> Path:
    return publish_root_for_run(root=root, run_dir=run_dir) / "writer_publish_report.json"


def _copy_export_snapshot(*, source_root: Path, snapshot_root: Path) -> dict[str, Any]:
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    shutil.copytree(source_root, snapshot_root)
    files = sorted(path for path in snapshot_root.rglob("*") if path.is_file())
    return {
        "path": str(snapshot_root),
        "file_count": len(files),
        "files": [str(path) for path in files],
    }


def _relative_export_paths(*, generated_files: list[str], source_root: Path) -> list[str]:
    out: list[str] = []
    for raw in generated_files:
        candidate = Path(str(raw)).resolve()
        try:
            rel = candidate.relative_to(source_root)
        except ValueError:
            continue
        out.append(rel.as_posix())
    return sorted(dict.fromkeys(out))


def _classify_export_delta(
    *, worktree_root: Path, source_root: Path, generated_files: list[str]
) -> dict[str, Any]:
    relative_generated = _relative_export_paths(
        generated_files=generated_files, source_root=source_root
    )
    if not relative_generated:
        return {
            "snapshot_root": "",
            "source_worktree": str(worktree_root),
            "generated_files": [],
            "created_files": [],
            "modified_files": [],
            "deleted_files": [],
            "unchanged_generated_files": [],
            "counts": {
                "generated": 0,
                "created": 0,
                "modified": 0,
                "deleted": 0,
                "unchanged_generated": 0,
            },
        }

    rows = status_porcelain(
        worktree_root=worktree_root,
        pathspecs=[f"src/coding-guidelines/{path}" for path in relative_generated],
    )
    status_by_path = {
        Path(str(row.get("path", ""))).relative_to("src/coding-guidelines").as_posix(): row
        for row in rows
        if str(row.get("path", "")).startswith("src/coding-guidelines/")
    }

    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    unchanged: list[str] = []
    for path in relative_generated:
        row = status_by_path.get(path)
        if row is None:
            unchanged.append(path)
            continue
        code = str(row.get("code", "  "))
        if "?" in code or "A" in code:
            created.append(path)
        elif "D" in code:
            deleted.append(path)
        elif "M" in code or "R" in code or "C" in code:
            modified.append(path)
        else:
            unchanged.append(path)

    return {
        "snapshot_root": "",
        "source_worktree": str(worktree_root),
        "generated_files": relative_generated,
        "created_files": created,
        "modified_files": modified,
        "deleted_files": deleted,
        "unchanged_generated_files": unchanged,
        "counts": {
            "generated": len(relative_generated),
            "created": len(created),
            "modified": len(modified),
            "deleted": len(deleted),
            "unchanged_generated": len(unchanged),
        },
    }


def _write_export_delta_manifest(*, publish_root: Path, payload: dict[str, Any]) -> Path:
    path = publish_root / "exported_guidelines_changes.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _render_export_delta_note(*, payload: dict[str, Any], manifest_path: Path) -> str:
    counts = dict(payload.get("counts") or {})
    created = list(payload.get("created_files") or [])
    modified = list(payload.get("modified_files") or [])
    deleted = list(payload.get("deleted_files") or [])
    unchanged = list(payload.get("unchanged_generated_files") or [])

    def _section(title: str, paths: list[str]) -> list[str]:
        lines = [f"## {title}", ""]
        if not paths:
            lines.append("- none")
        else:
            lines.extend(f"- `{path}`" for path in paths)
        lines.append("")
        return lines

    lines = [
        "# This Run Changes",
        "",
        "This snapshot contains the full exported `src/coding-guidelines` tree for durability.",
        "The lists below identify the files changed by this publish run.",
        "",
        f"- Manifest: `{manifest_path.name}`",
        f"- Generated files: {int(counts.get('generated', 0))}",
        f"- Created files: {int(counts.get('created', 0))}",
        f"- Modified files: {int(counts.get('modified', 0))}",
        f"- Deleted files: {int(counts.get('deleted', 0))}",
        f"- Unchanged generated files: {int(counts.get('unchanged_generated', 0))}",
        "",
    ]
    lines.extend(_section("Created Files", created))
    lines.extend(_section("Modified Files", modified))
    if deleted:
        lines.extend(_section("Deleted Files", deleted))
    else:
        lines.extend(["## Deleted Files", "", "- none", ""])
    lines.extend(["## Unchanged Generated Files", ""])
    if unchanged:
        lines.append(f"- count: {len(unchanged)}")
    else:
        lines.append("- count: 0")
    lines.append("")
    return "\n".join(lines)


def _write_export_delta_note(
    *, snapshot_root: Path, payload: dict[str, Any], manifest_path: Path
) -> Path:
    note_path = snapshot_root / "THIS_RUN_CHANGES.md"
    note_path.write_text(
        _render_export_delta_note(payload=payload, manifest_path=manifest_path),
        encoding="utf-8",
    )
    return note_path


def _cleanup_report(*, requested: bool, performed: bool, reason: str) -> dict[str, Any]:
    return {
        "requested": requested,
        "performed": performed,
        "reason": reason,
    }


def _base_publish_report(
    *,
    root: Path,
    run_dir: Path,
    mode: str,
    dry_run: bool,
    keep_worktree: bool,
) -> dict[str, Any]:
    publish_root = publish_root_for_run(root=root, run_dir=run_dir)
    return {
        "status": "fail",
        "mode": mode,
        "run_dir": str(run_dir),
        "repo_root": "",
        "publish_root": str(publish_root),
        "db_path": str(publish_root / "writer_publish.sqlite"),
        "dry_run": dry_run,
        "keep_worktree": keep_worktree,
        "worktree": "",
        "branch": "",
        "failure_code": "",
        "failure_message": "",
        "ingest": {},
        "export": {},
        "export_snapshot": {},
        "export_delta": {},
        "conformance": {},
        "commit": {"committed": False},
        "push": {"pushed": False},
        "cleanup": _cleanup_report(
            requested=not keep_worktree, performed=False, reason="not_started"
        ),
    }


def run_ingest_from_run(
    *,
    root: Path,
    run_dir: Path,
    mode: str,
    output_db: Path,
    resolution_report_root: Path | None = None,
) -> dict[str, Any]:
    payload = load_publish_payload(run_dir=run_dir, publishable=(mode == "publishable"))
    mapped_rows: list[dict[str, Any]] = []
    for row in payload["draft_rows"]:
        mapping = map_publish_record(row, resolution_report_root=resolution_report_root)
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


def run_publish_from_run(
    *,
    root: Path,
    run_dir: Path,
    mode: str,
    dry_run: bool,
    keep_worktree: bool = False,
) -> dict[str, Any]:
    repo_root = _load_guidelines_repo_root(root)
    publish_root = publish_root_for_run(root=root, run_dir=run_dir)
    publish_root.mkdir(parents=True, exist_ok=True)
    db_path = publish_root / "writer_publish.sqlite"
    report = _base_publish_report(
        root=root,
        run_dir=run_dir,
        mode=mode,
        dry_run=dry_run,
        keep_worktree=keep_worktree,
    )
    report["repo_root"] = str(repo_root)

    if dry_run:
        report["status"] = "dry_run"
        report["cleanup"] = _cleanup_report(
            requested=not keep_worktree,
            performed=False,
            reason="dry_run_no_worktree",
        )
        return report

    worktree_info = create_worktree(repo_root=repo_root, cache_root=publish_root)
    worktree_root = Path(str(worktree_info["worktree"])).resolve()
    branch = str(worktree_info["branch"])
    report["worktree"] = str(worktree_root)
    report["branch"] = branch
    cleanup_performed = False
    cleanup_reason = "preserved_for_review"
    try:
        try:
            ingest = run_ingest_from_run(
                root=root,
                run_dir=run_dir,
                mode=mode,
                output_db=db_path,
                resolution_report_root=publish_root / "fls_resolution",
            )
            report["ingest"] = ingest
            (publish_root / "annotation_policy_metrics.json").write_text(
                json.dumps(ingest.get("annotation_policy_metrics", {}), indent=2, sort_keys=False)
                + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            report["failure_code"] = "INGEST_FAILED"
            report["failure_message"] = str(exc)
            return report

        try:
            export = run_export_rst(root=root, db_path=db_path, guidelines_repo_root=worktree_root)
            report["export"] = export
            source_root = worktree_root / "src" / "coding-guidelines"
            delta_payload = _classify_export_delta(
                worktree_root=worktree_root,
                source_root=source_root,
                generated_files=list(((export.get("export") or {}).get("generated_files") or [])),
            )
            snapshot = _copy_export_snapshot(
                source_root=source_root,
                snapshot_root=publish_root / "exported_guidelines",
            )
            delta_payload["snapshot_root"] = str(snapshot["path"])
            manifest_path = _write_export_delta_manifest(
                publish_root=publish_root, payload=delta_payload
            )
            note_path = _write_export_delta_note(
                snapshot_root=Path(str(snapshot["path"])),
                payload=delta_payload,
                manifest_path=manifest_path,
            )
            snapshot_files = list(snapshot.get("files") or [])
            snapshot_files.append(str(note_path))
            snapshot["files"] = sorted(dict.fromkeys(snapshot_files))
            snapshot["file_count"] = len(snapshot["files"])
            report["export_snapshot"] = snapshot
            report["export_delta"] = {
                **delta_payload,
                "manifest_path": str(manifest_path),
                "note_path": str(note_path),
            }
        except Exception as exc:
            report["failure_code"] = "EXPORT_FAILED"
            report["failure_message"] = str(exc)
            return report

        conformance = run_conformance(
            repo_root=worktree_root,
            report_dir=publish_root,
        )
        report["conformance"] = conformance
        if mode == "publishable" and str(conformance.get("status", "")) != "pass":
            report["failure_code"] = "CONFORMANCE_FAILED"
            report["failure_message"] = "publishable mode requires passing conformance"
            return report

        commit_message = f"feat(guidelines): publish writer run {run_dir.name}"
        try:
            commit = finalize_commit(worktree_root=worktree_root, message=commit_message)
        except Exception as exc:
            report["failure_code"] = "COMMIT_FAILED"
            report["failure_message"] = str(exc)
            return report
        report["commit"] = commit
        if not bool(commit.get("committed", False)):
            report["status"] = "no_changes"
            report["failure_code"] = "NO_CHANGES"
            report["failure_message"] = "export completed but produced no git diff"
            return report

        try:
            push = push_branch(worktree_root=worktree_root, branch=branch)
        except Exception as exc:
            report["failure_code"] = "PUSH_FAILED"
            report["failure_message"] = str(exc)
            return report
        report["push"] = push
        report["status"] = "pass"
        report["failure_code"] = ""
        report["failure_message"] = ""
        if not keep_worktree:
            remove_worktree(repo_root=repo_root, worktree_root=worktree_root)
            cleanup_performed = True
            cleanup_reason = "success_cleanup"
        else:
            cleanup_reason = "kept_by_request"
        return report
    finally:
        if not cleanup_performed:
            if keep_worktree:
                cleanup_reason = "kept_by_request"
            elif report.get("status") != "pass":
                cleanup_reason = "preserved_after_non_pass"
            else:
                cleanup_reason = "already_removed"
        report["cleanup"] = _cleanup_report(
            requested=not keep_worktree,
            performed=cleanup_performed,
            reason=cleanup_reason,
        )


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


def namespace_from_args(args: Namespace, *, root: Path) -> tuple[Path, str, bool, bool]:
    run_dir_raw = str(getattr(args, "run_dir", "") or "").strip()
    if not run_dir_raw:
        raise RuntimeError("--run-dir is required")
    run_dir = Path(run_dir_raw).resolve()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir does not exist: {run_dir}")
    mode = str(getattr(args, "mode", "publishable") or "publishable")
    dry_run = bool(getattr(args, "dry_run", False))
    keep_worktree = bool(getattr(args, "keep_worktree", False))
    _ = root
    return run_dir, mode, dry_run, keep_worktree
