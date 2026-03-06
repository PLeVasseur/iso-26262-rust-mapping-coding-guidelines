from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.services import guidelines_repo_service
from retrieval.writer_host.publish import (
    _load_guidelines_repo_root,
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
    run_dir, mode, _ = namespace_from_args(args, root=root)
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
    run_dir, mode, _ = namespace_from_args(args, root=root)
    report = run_conformance_command(root=root, run_dir=run_dir, mode=mode)
    print(report["report_path"])
    return 0 if str(report.get("status", "")) == "pass" else 2


def run_publish_from_run(args: Namespace, *, root: Path) -> int:
    run_dir, mode, dry_run = namespace_from_args(args, root=root)
    report = run_publish_stage(root=root, run_dir=run_dir, mode=mode, dry_run=dry_run)
    output_raw = str(getattr(args, "output", "") or "").strip()
    output_path = (
        Path(output_raw).resolve()
        if output_raw
        else root / ".cache" / "sqlite_kb" / "reports" / "writer_publish_report.json"
    )
    write_publish_report(output_path, report)
    print(output_path)
    return 0 if str(report.get("status", "")) in {"pass", "dry_run"} else 2
