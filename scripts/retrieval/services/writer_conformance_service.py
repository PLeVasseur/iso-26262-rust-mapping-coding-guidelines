from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.publish import namespace_from_args, run_conformance_command


def run(args: Namespace, *, root: Path) -> int:
    run_dir, mode, _, _, _ = namespace_from_args(args, root=root)
    report = run_conformance_command(root=root, run_dir=run_dir, mode=mode)
    print(report.get("report_path", ""))
    return 0 if str(report.get("status", "")) == "pass" else 2
