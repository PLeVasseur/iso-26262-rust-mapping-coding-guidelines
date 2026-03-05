from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from retrieval.writer_host.packet import build_reviewer_packet


def run(args: Namespace, *, root: Path) -> int:
    run_dir = Path(str(getattr(args, "run_dir", "") or "")).resolve()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir not found: {run_dir}")
    output_raw = str(getattr(args, "output", "") or "").strip()
    if output_raw:
        output_path = Path(output_raw).resolve()
    else:
        output_path = run_dir / "writer_review_packet.zip"
    manifest = build_reviewer_packet(run_dir=run_dir, output_zip=output_path)
    print(output_path)
    print(output_path.with_suffix(".manifest.json"))
    return 0 if int(manifest.get("artifact_count", 0)) > 0 else 2
