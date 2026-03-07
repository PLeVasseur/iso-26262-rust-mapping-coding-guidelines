from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.publish import (
    default_publish_report_path,
    namespace_from_args,
    run_publish_from_run,
    write_publish_report,
)
from retrieval.writer_host.packet import build_publish_reviewer_packet


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
    status = str(report.get("status", ""))
    if status in {"pass", "dry_run", "no_changes", "review_export_pass", "publishability_pass"}:
        return 0
    return 2
