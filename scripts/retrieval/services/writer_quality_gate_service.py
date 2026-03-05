from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.quality_gate import evaluate_run, write_quality_gate_report


def run(args: Namespace, *, root: Path) -> int:
    run_dir = Path(str(getattr(args, "run_dir", "") or "")).resolve()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir not found: {run_dir}")
    report = evaluate_run(run_dir)
    output_path_raw = str(getattr(args, "output", "") or "").strip()
    output_path = (
        Path(output_path_raw).resolve()
        if output_path_raw
        else run_dir / "writer_quality_gate_report.json"
    )
    write_quality_gate_report(output_path, report)
    print(output_path)
    return 0 if str(report.get("status", "")) == "pass" else 2
