from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.publish import (
    namespace_from_args,
    run_publish_from_run,
    write_publish_report,
)


def run(args: Namespace, *, root: Path) -> int:
    run_dir, mode, dry_run = namespace_from_args(args, root=root)
    report = run_publish_from_run(root=root, run_dir=run_dir, mode=mode, dry_run=dry_run)
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
