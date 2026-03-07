from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.services import guidelines_repo_service
from retrieval.writer_host.packet import build_publish_reviewer_packet
from retrieval.writer_host.publish import (
    _load_guidelines_repo_root,
    default_publish_report_path,
    namespace_from_args,
    run_conformance_command,
    run_export_rst as run_export_rst_stage,
    run_ingest_from_run as run_ingest_stage,
    run_publish_from_run as run_publish_stage,
    write_publish_report,
)


def run_doctor(args: Namespace, *, root: Path) -> int:
    return guidelines_repo_service.run_doctor(args, root=root)


def run_ensure_repo(args: Namespace, *, root: Path) -> int:
    return guidelines_repo_service.run_ensure_repo(args, root=root)


def run_bump_pin(args: Namespace, *, root: Path) -> int:
    return guidelines_repo_service.run_bump_pin(args, root=root)


def run_reorg_path_mapping(args: Namespace, *, root: Path) -> int:
    return guidelines_repo_service.run_reorg_path_mapping(args, root=root)


def run_ingest_from_run(args: Namespace, *, root: Path) -> int:
    run_dir, mode, _, _, _ = namespace_from_args(args, root=root)
    output_db_raw = str(getattr(args, "output_db", "") or "").strip()
    if output_db_raw:
        output_db = Path(output_db_raw).resolve()
    else:
        output_db = (
            root
            / ".cache"
            / "sqlite_kb"
            / "reports"
            / "writer_publish"
            / run_dir.name
            / "writer_publish.sqlite"
        )
    summary = run_ingest_stage(root=root, run_dir=run_dir, mode=mode, output_db=output_db)
    print(summary["db"]["db_path"])
    return 0


def run_export_rst(args: Namespace, *, root: Path) -> int:
    db_path_raw = str(getattr(args, "db_path", "") or "").strip()
    if not db_path_raw:
        raise RuntimeError("--db-path is required")
    db_path = Path(db_path_raw).resolve()
    repo_root = _load_guidelines_repo_root(root)
    summary = run_export_rst_stage(root=root, db_path=db_path, guidelines_repo_root=repo_root)
    print(summary["output_root"])
    return 0


def run_conformance(args: Namespace, *, root: Path) -> int:
    run_dir, mode, _, _, _ = namespace_from_args(args, root=root)
    report = run_conformance_command(root=root, run_dir=run_dir, mode=mode)
    print(report["report_path"])
    return 0 if str(report.get("status", "")) == "pass" else 2


def run_publish_from_run(args: Namespace, *, root: Path) -> int:
    run_dir, mode, dry_run, keep_worktree, audit_only = namespace_from_args(args, root=root)
    report = run_publish_stage(
        root=root,
        run_dir=run_dir,
        mode=mode,
        dry_run=dry_run,
        keep_worktree=keep_worktree,
        audit_only=audit_only,
    )
    output_raw = str(getattr(args, "output", "") or "").strip()
    output_path = (
        Path(output_raw).resolve()
        if output_raw
        else default_publish_report_path(root=root, run_dir=run_dir)
    )
    default_output_path = default_publish_report_path(root=root, run_dir=run_dir)
    write_publish_report(default_output_path, report)
    if output_path != default_output_path:
        write_publish_report(output_path, report)
    if str(report.get("publish_root", "")).strip():
        packet_path = Path(str(report.get("publish_root"))) / "writer_publish_review_packet.zip"
        manifest = build_publish_reviewer_packet(
            publish_root=Path(str(report.get("publish_root"))),
            output_zip=packet_path,
            source_run_dir=run_dir,
        )
        report["review_packet"] = {
            "path": str(packet_path),
            "manifest_path": str(packet_path.with_suffix(".manifest.json")),
            "artifact_count": int(manifest.get("artifact_count", 0)),
        }
        write_publish_report(default_output_path, report)
        if output_path != default_output_path:
            write_publish_report(output_path, report)
    print(output_path)
    return (
        0
        if str(report.get("status", ""))
        in {"pass", "dry_run", "no_changes", "review_export_pass", "publishability_pass"}
        else 2
    )
