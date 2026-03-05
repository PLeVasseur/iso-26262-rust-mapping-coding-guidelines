from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.publish import run_publish, write_publish_report


def run(args: Namespace, *, root: Path) -> int:
    mode = str(getattr(args, "mode", "publishable") or "publishable")
    profile = str(getattr(args, "profile", "fast") or "fast")
    dry_run = bool(getattr(args, "dry_run", False))
    report = run_publish(root=root, mode=mode, profile=profile, dry_run=dry_run)
    output_raw = str(getattr(args, "output", "") or "").strip()
    if output_raw:
        output_path = Path(output_raw).resolve()
    else:
        output_path = root / ".cache" / "sqlite_kb" / "reports" / "writer_publish_report.json"
    write_publish_report(output_path, report)
    print(output_path)
    status = str(report.get("status", ""))
    if status == "pass" or status == "dry_run":
        return 0
    return 2
