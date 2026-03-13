from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from retrieval.writer_host.packet import build_publish_reviewer_packet  # noqa: E402


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_build_publish_reviewer_packet_filters_to_admitted_changed_files(tmp_path: Path) -> None:
    publish_root = tmp_path / "writer_publish" / "demo"
    run_dir = tmp_path / "run"
    exported = publish_root / "exported_guidelines"
    (exported / "attributes").mkdir(parents=True, exist_ok=True)
    (exported / "expressions").mkdir(parents=True, exist_ok=True)
    (exported / "attributes" / "gui_keep.rst").write_text("keep\n", encoding="utf-8")
    (exported / "expressions" / "gui_drop.rst").write_text("drop\n", encoding="utf-8")
    (exported / "THIS_RUN_CHANGES.md").write_text("# Changes\n", encoding="utf-8")
    _write(publish_root / "writer_publish_report.json", {"status": "review_export_pass"})
    _write(
        publish_root / "exported_guidelines_changes.json",
        {
            "created_files": ["attributes/gui_keep.rst", "expressions/gui_drop.rst"],
            "modified_files": [],
        },
    )
    _write(
        publish_root / "internal_rendered_candidate_manifest.json",
        {
            "rendered_candidates": [
                {"draft_id": "a", "rendered_path": "attributes/gui_keep.rst"},
                {"draft_id": "b", "rendered_path": "expressions/gui_drop.rst"},
            ]
        },
    )
    _write(
        run_dir / "writer_review_admissibility_report.json",
        {
            "status": "pass",
            "entries": [
                {
                    "draft_id": "a",
                    "rendered_candidate_path": "attributes/gui_keep.rst",
                    "recommended_external_export": "yes",
                },
                {
                    "draft_id": "b",
                    "rendered_candidate_path": "expressions/gui_drop.rst",
                    "recommended_external_export": "no",
                },
            ],
        },
    )
    _write(run_dir / "metadata_adjudication_report.json", {"status": "pass"})
    _write(run_dir / "taxonomy_integration_report.json", {"status": "pass"})
    _write(run_dir / "family_resolution_report.json", {"status": "pass"})

    output_zip = publish_root / "packet.zip"
    manifest = build_publish_reviewer_packet(
        publish_root=publish_root,
        output_zip=output_zip,
        source_run_dir=run_dir,
    )

    assert manifest["included_guideline_files"] == ["attributes/gui_keep.rst"]
    assert manifest["excluded_generated_files"] == ["expressions/gui_drop.rst"]
    with zipfile.ZipFile(output_zip) as archive:
        names = set(archive.namelist())
    assert "guidelines/attributes/gui_keep.rst" in names
    assert "guidelines/expressions/gui_drop.rst" not in names
    assert "reports/writer_review_admissibility_report.json" in names
    assert "internal_rendered_candidate_manifest.json" in names
