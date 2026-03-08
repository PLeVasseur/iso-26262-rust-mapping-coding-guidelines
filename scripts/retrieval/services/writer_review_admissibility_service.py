from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.reviewer_admissibility import (
    evaluate_review_admissibility,
    write_review_admissibility_artifacts,
)


def run(args: Namespace, *, root: Path) -> int:
    run_dir = Path(str(getattr(args, "run_dir", "") or "")).resolve()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir not found: {run_dir}")
    report = evaluate_review_admissibility(root=root, run_dir=run_dir)
    paths = write_review_admissibility_artifacts(run_dir=run_dir, report=report)
    print(paths["admissibility"])
    return 0 if str(report.get("status", "")) == "pass" else 2
