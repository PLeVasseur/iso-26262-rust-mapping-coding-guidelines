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
    if source_run_dir is None:
        raise RuntimeError("source_run_dir is required for publish reviewer packets")
    admissibility_path = source_run_dir / "writer_review_admissibility_report.json"
    metadata_path = source_run_dir / "metadata_adjudication_report.json"
    taxonomy_path = source_run_dir / "taxonomy_integration_report.json"
    family_path = source_run_dir / "family_resolution_report.json"
    export_delta_path = publish_root / "exported_guidelines_changes.json"
    changes_note_path = publish_root / "exported_guidelines" / "THIS_RUN_CHANGES.md"
    internal_manifest_path = publish_root / "internal_rendered_candidate_manifest.json"
    required_paths.extend(
        [
            admissibility_path,
            metadata_path,
            taxonomy_path,
            family_path,
            export_delta_path,
            changes_note_path,
            internal_manifest_path,
        ]
    )
    manifest_path = output_zip.with_suffix(".manifest.json")
    for path in required_paths:
        if not path.exists():
            raise RuntimeError(f"missing packet artifact: {path}")

    admissibility = json.loads(admissibility_path.read_text(encoding="utf-8"))
    if str(admissibility.get("status", "")) != "pass":
        raise RuntimeError(
            "writer_review_admissibility_report.json must pass before packet creation"
        )
    export_delta = json.loads(export_delta_path.read_text(encoding="utf-8"))
    admitted_relative = {
        str(Path(str(entry.get("rendered_candidate_path", ""))).as_posix())
        for entry in list(admissibility.get("entries") or [])
        if isinstance(entry, dict)
        and str(entry.get("recommended_external_export", "")).strip() == "yes"
        and str(entry.get("rendered_candidate_path", "")).strip()
    }
    changed_files = set(
        str(value) for value in list(export_delta.get("created_files") or [])
    ) | set(str(value) for value in list(export_delta.get("modified_files") or []))
    guideline_files = sorted(admitted_relative & changed_files)
    if not guideline_files:
        raise RuntimeError(
            "no admitted changed guideline files available for external reviewer packet"
        )

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    with zipfile.ZipFile(output_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in guideline_files:
            path = publish_root / "exported_guidelines" / relative
            arcname = f"guidelines/{relative}"
            archive.write(path, arcname=arcname)
            archived.append(arcname)

        support_files = [
            (publish_root / "writer_publish_report.json", "writer_publish_report.json"),
            (export_delta_path, "exported_guidelines_changes.json"),
            (changes_note_path, "THIS_RUN_CHANGES.md"),
            (internal_manifest_path, "internal_rendered_candidate_manifest.json"),
            (admissibility_path, "reports/writer_review_admissibility_report.json"),
            (metadata_path, "reports/metadata_adjudication_report.json"),
            (taxonomy_path, "reports/taxonomy_integration_report.json"),
            (family_path, "reports/family_resolution_report.json"),
        ]
        for path, arcname in support_files:
            archive.write(path, arcname=arcname)
            archived.append(arcname)

    manifest = {
        "publish_root": str(publish_root),
        "source_run_dir": str(source_run_dir) if source_run_dir is not None else "",
        "output_zip": str(output_zip),
        "artifact_count": len(archived),
        "artifacts": archived,
        "included_guideline_files": guideline_files,
        "included_supporting_files": [
            path for path in archived if not path.startswith("guidelines/")
        ],
        "excluded_generated_files": sorted(changed_files - set(guideline_files)),
        "exclusion_reasons": {
            relative: "not_admitted_by_reviewer_admissibility"
            for relative in sorted(changed_files - set(guideline_files))
        },
    }
    return _write_manifest(payload=manifest, output_zip=output_zip)
