from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.publish import (
    default_publish_report_path,
    namespace_from_args,
    run_publish_from_run,
    write_publish_report,
)


def run(args: Namespace, *, root: Path) -> int:
    run_dir, mode, dry_run, keep_worktree, audit_only = namespace_from_args(args, root=root)
    report = run_publish_from_run(
        root=root,
        run_dir=run_dir,
        mode=mode,
        dry_run=dry_run,
        keep_worktree=keep_worktree,
        audit_only=audit_only,
    )
    output_raw = str(getattr(args, "output", "") or "").strip()
    default_output_path = default_publish_report_path(root=root, run_dir=run_dir)
    if output_raw:
        output_path = Path(output_raw).resolve()
    else:
        output_path = default_output_path
    write_publish_report(default_output_path, report)
    if output_path != default_output_path:
        write_publish_report(output_path, report)
    print(output_path)
    status = str(report.get("status", ""))
    if status in {"pass", "dry_run", "no_changes", "review_export_pass", "publishability_pass"}:
        return 0
    return 2
