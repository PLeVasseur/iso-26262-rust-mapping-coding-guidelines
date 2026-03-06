from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any


def _write_manifest(*, payload: dict[str, Any], output_zip: Path) -> dict[str, Any]:
    manifest_path = output_zip.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return payload


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
    return _write_manifest(payload=manifest, output_zip=output_zip)


def build_publish_reviewer_packet(
    *,
    publish_root: Path,
    output_zip: Path,
    source_run_dir: Path | None = None,
) -> dict[str, Any]:
    required_paths = [publish_root / "writer_publish_report.json"]
    manifest_path = output_zip.with_suffix(".manifest.json")
    for path in required_paths:
        if not path.exists():
            raise RuntimeError(f"missing packet artifact: {path}")

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    with zipfile.ZipFile(output_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root_file in sorted(path for path in publish_root.glob("*") if path.is_file()):
            if root_file in {output_zip, manifest_path}:
                continue
            arcname = str(root_file.relative_to(publish_root))
            archive.write(root_file, arcname=arcname)
            archived.append(arcname)
        for nested in sorted(path for path in publish_root.rglob("*") if path.is_file()):
            if nested in {output_zip, manifest_path}:
                continue
            arcname = str(nested.relative_to(publish_root))
            if arcname not in archived:
                archive.write(nested, arcname=arcname)
                archived.append(arcname)
        if source_run_dir is not None:
            writer_packet = source_run_dir / "writer_review_packet.zip"
            if writer_packet.exists():
                arcname = f"source_run/{writer_packet.name}"
                archive.write(writer_packet, arcname=arcname)
                archived.append(arcname)
            writer_manifest = source_run_dir / "writer_review_packet.manifest.json"
            if writer_manifest.exists():
                arcname = f"source_run/{writer_manifest.name}"
                archive.write(writer_manifest, arcname=arcname)
                archived.append(arcname)

    manifest = {
        "publish_root": str(publish_root),
        "source_run_dir": str(source_run_dir) if source_run_dir is not None else "",
        "output_zip": str(output_zip),
        "artifact_count": len(archived),
        "artifacts": archived,
    }
    return _write_manifest(payload=manifest, output_zip=output_zip)
