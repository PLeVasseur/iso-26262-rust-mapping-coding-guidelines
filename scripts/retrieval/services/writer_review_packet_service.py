from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.packet import build_publish_reviewer_packet
from retrieval.writer_host.publish import publish_root_for_run
from retrieval.writer_host.reviewer_admissibility import (
    evaluate_review_admissibility,
    write_review_admissibility_artifacts,
)


def run(args: Namespace, *, root: Path) -> int:
    run_dir = Path(str(getattr(args, "run_dir", "") or "")).resolve()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir not found: {run_dir}")
    report = evaluate_review_admissibility(root=root, run_dir=run_dir)
    write_review_admissibility_artifacts(run_dir=run_dir, report=report)
    if str(report.get("status", "")) != "pass":
        print(run_dir / "writer_review_admissibility_report.json")
        return 2
    publish_root = publish_root_for_run(root=root, run_dir=run_dir)
    if not publish_root.exists():
        raise RuntimeError(f"publish_root not found: {publish_root}")
    output_raw = str(getattr(args, "output", "") or "").strip()
    if output_raw:
        output_path = Path(output_raw).resolve()
    else:
        output_path = publish_root / "writer_publish_review_packet.zip"
    manifest = build_publish_reviewer_packet(
        publish_root=publish_root,
        output_zip=output_path,
        source_run_dir=run_dir,
    )
    print(output_path)
    print(output_path.with_suffix(".manifest.json"))
    return 0 if int(manifest.get("artifact_count", 0)) > 0 else 2
