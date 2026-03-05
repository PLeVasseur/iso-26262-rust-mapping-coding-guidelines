from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any


def build_reviewer_packet(*, run_dir: Path, output_zip: Path) -> dict[str, Any]:
    required_paths = [
        run_dir / "writer_host_run_summary.json",
        run_dir / "normalization_report.json",
        run_dir / "evidence_synthesizer_gate_report.json",
        run_dir / "writer_output_auditor_report.json",
        run_dir / "role_validation_report.json",
        run_dir / "drafts.jsonl",
        run_dir / "writer_subagent_outputs" / "prompt_contract_snapshot.json",
        run_dir / "writer_subagent_outputs" / "subagent_invocation_trace.json",
        run_dir / "writer_subagent_outputs" / "merge_validation_report.json",
    ]
    for path in required_paths:
        if not path.exists():
            raise RuntimeError(f"missing packet artifact: {path}")

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    with zipfile.ZipFile(output_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in required_paths:
            arcname = str(path.relative_to(run_dir))
            archive.write(path, arcname=arcname)
            archived.append(arcname)
        for path in sorted((run_dir / "writer_subagent_outputs").glob("*.jsonl")):
            arcname = str(path.relative_to(run_dir))
            if arcname not in archived:
                archive.write(path, arcname=arcname)
                archived.append(arcname)

    manifest = {
        "run_dir": str(run_dir),
        "output_zip": str(output_zip),
        "artifact_count": len(archived),
        "artifacts": archived,
    }
    manifest_path = output_zip.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return manifest
